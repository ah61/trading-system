"""Walk-forward aware backtest engine with strict no-lookahead slicing.

5.7 contract boundary (engine refactor, option A hybrid):
    Engine consumes ``Dict[catalogue_variable_name, pd.Series]`` from upstream
    (the same shape signals consume). The portfolio layer below — ``PositionSizer``
    and ``PortfolioConstructor`` — still consumes a wide price panel DataFrame
    (columns = instruments). The engine translates between the two at the
    sizing handoff: it assembles a panel from a subset of the Series dict
    indicated by ``portfolio_config['instruments']``.

    The portfolio panel contract is preserved deliberately. Cross-sectional
    vol math and risk parity are naturally expressed on wide panels; rewriting
    them to operate on Series-dicts would be churn for no benefit. A future
    milestone (5.16, placeholder) may unify on Series throughout if downstream
    use cases warrant it.

    See PROGRESS.md §"Backtest engine 5.7 refactor" for the design rationale.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Sequence

import numpy as np
import pandas as pd
from loguru import logger

from src.backtest.results import BacktestResult, build_backtest_result
from src.portfolio.constructor import PortfolioConstructor
from src.portfolio.costs import CostModel
from src.portfolio.sizing import PositionSizer
from src.signals.base import Signal


def _as_utc_index(idx: pd.Index) -> pd.DatetimeIndex:
    """Normalise an index to a sorted UTC ``DatetimeIndex``."""
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def _utc_timestamp(d: date) -> pd.Timestamp:
    return pd.Timestamp(d, tz="UTC")


def _normalise_test_timestamps(
    test_dates: Sequence[date | pd.Timestamp],
    calendar: pd.DatetimeIndex,
) -> pd.DatetimeIndex:
    """Convert user ``test_dates`` to a sorted UTC ``DatetimeIndex`` contained in ``calendar``."""
    if len(test_dates) == 0:
        raise ValueError("test_dates must be non-empty when provided.")
    cal_set = set(pd.DatetimeIndex(calendar))
    out: List[pd.Timestamp] = []
    for x in test_dates:
        if isinstance(x, pd.Timestamp):
            ts = x
        elif isinstance(x, date):
            ts = _utc_timestamp(x)
        else:
            ts = pd.Timestamp(x)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        if ts not in cal_set:
            raise ValueError(f"test_dates entry {ts!r} is not present in the backtest calendar.")
        out.append(ts)
    return pd.DatetimeIndex(sorted(set(out)))


def _build_calendar(
    data: Dict[str, pd.Series],
    start_date: date,
    end_date: date,
) -> pd.DatetimeIndex:
    """Union of all variable timestamps, filtered to ``[start_date, end_date]`` (inclusive).

    Accepts ``Dict[str, pd.Series]`` (5.7 contract). Each Series' index contributes
    to the calendar union.
    """
    start_ts = _utc_timestamp(start_date)
    end_ts = _utc_timestamp(end_date)
    if end_ts < start_ts:
        raise ValueError("end_date must be on or after start_date.")

    union: pd.DatetimeIndex | None = None
    for series in data.values():
        ix = _as_utc_index(series.index)
        union = ix if union is None else union.union(ix)

    if union is None or len(union) == 0:
        raise ValueError("data must contain at least one non-empty Series with a datetime index.")

    union = union.sort_values()
    mask = (union >= start_ts) & (union <= end_ts)
    out = union[mask]
    if len(out) == 0:
        raise ValueError("No overlapping calendar dates between data and [start_date, end_date].")
    return pd.DatetimeIndex(out)


def _slice_data_no_lookahead(
    data: Dict[str, pd.Series],
    as_of: pd.Timestamp,
    method: str,
    train_window: int,
) -> Dict[str, pd.Series]:
    """Slice every Series to ``index <= as_of``, optionally capping history for rolling mode.

    Returns a new dict; input data is not mutated.
    """
    out: Dict[str, pd.Series] = {}
    for name, series in data.items():
        s = series.copy()
        s.index = _as_utc_index(s.index)
        sub = s.loc[s.index <= as_of]
        if method == "rolling" and train_window > 0 and len(sub) > train_window:
            sub = sub.iloc[-train_window:]
        out[name] = sub
    return out


def _signal_value_to_instrument_row(
    raw: pd.Series,
    as_of: pd.Timestamp,
    instruments: List[str],
) -> pd.Series:
    """Map a signal ``compute`` output to a single cross-section at ``as_of``.

    A signal's output asset level may be a catalogue variable name (e.g.
    ``"AAPL_CLOSE"``) rather than a raw instrument identifier. The engine
    treats the asset-level labels of the signal output as opaque: the
    portfolio panel is built from ``instruments`` (the configured list),
    and signal values are aligned to those instrument labels via direct
    ``reindex``. Callers responsible for keeping signal asset-level labels
    and engine ``instruments`` in sync — typically by passing the catalogue
    variable names through to both.
    """
    if isinstance(raw.index, pd.MultiIndex):
        if raw.index.nlevels < 2:
            raise ValueError("Signal MultiIndex must have at least two levels (date, instrument).")
        dates = raw.index.get_level_values(0)
        mask = dates <= as_of
        sub = raw.loc[mask]
        if sub.empty:
            return pd.Series(0.0, index=instruments)
        last_dt = pd.Timestamp(dates[mask].max())
        if last_dt.tzinfo is None:
            last_dt = last_dt.tz_localize("UTC")
        else:
            last_dt = last_dt.tz_convert("UTC")
        sl = sub.loc[sub.index.get_level_values(0) == last_dt]
        if sl.empty:
            return pd.Series(0.0, index=instruments)
        if isinstance(sl.index, pd.MultiIndex):
            row = sl.groupby(level=-1, sort=False).last()
        else:
            row = sl
        return row.reindex(instruments).fillna(0.0).astype(float)

    s = raw.sort_index()
    s = s.loc[:as_of]
    if s.empty:
        return pd.Series(0.0, index=instruments)
    val = float(s.iloc[-1])
    return pd.Series({c: val for c in instruments}, dtype=float)


def _assemble_price_panel(
    data: Dict[str, pd.Series],
    instruments: List[str],
    history: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Build a wide price panel from a subset of the Series dict.

    Boundary translation point between the 5.7 Series contract and the
    portfolio layer's panel contract. Each instrument in ``instruments``
    must have a corresponding entry in ``data``. Missing entries are a
    configuration error and raise.
    """
    missing = [name for name in instruments if name not in data]
    if missing:
        raise KeyError(
            f"portfolio_config['instruments'] references variables not present in data: {missing}"
        )
    cols: Dict[str, pd.Series] = {}
    for name in instruments:
        s = data[name].copy()
        s.index = _as_utc_index(s.index)
        cols[name] = s.sort_index()
    panel = pd.DataFrame(cols)
    return panel.reindex(history).ffill().replace(0, np.nan)


class BacktestEngine:
    """Run a single train/test style backtest with strict causal data feeds."""

    def run(
        self,
        signals: List[Signal],
        data: Dict[str, pd.Series],
        portfolio_config: dict,
        cost_model: CostModel,
        start_date: date,
        end_date: date,
        method: str = "expanding",
        train_window: int = 252 * 5,
        test_window: int = 252,
        test_dates: Sequence[date | pd.Timestamp] | None = None,
    ) -> BacktestResult:
        """Execute one backtest over ``test_window`` trailing dates, or over explicit ``test_dates``.

        For every calendar date ``d``, each ``Signal.compute`` call receives only
        data causally available at ``d``:

        - ``expanding``: all history up to and including ``d``.
        - ``rolling``: at most the last ``train_window`` observations ending at ``d``.

        Args:
            signals: Concrete ``Signal`` instances to combine with equal weights (Phase 1).
            data: ``Dict[catalogue_variable_name, pd.Series]`` (5.7 contract). Each Series is
                a 1-D time series indexed by UTC ``DatetimeIndex``. Must contain entries for
                every variable in ``portfolio_config['instruments']`` and every variable
                referenced by ``signals[i].required_variables``.
            portfolio_config: Required keys:
                  - ``instruments``: list of catalogue variable names representing tradeable
                    instruments. The price panel handed to the portfolio sizer is assembled
                    from these (this list replaces the pre-5.7 ``prices_key``).
                  - ``asset_classes``: mapping of instrument -> asset class string.
                Optional keys: ``sizing_method``, ``target_vol``, ``gross_limit``,
                ``net_limit``, ``vol_window``.
            cost_model: Transaction cost model applied to gross returns and rebalance trades.
            start_date: Inclusive range start (calendar date).
            end_date: Inclusive range end (calendar date).
            method: ``"expanding"`` or ``"rolling"`` history window for signal inputs.
            train_window: Maximum in-sample length for rolling mode (trading days / bars).
            test_window: Number of most recent dates in-range used as the test return series
                (ignored when ``test_dates`` is provided).
            test_dates: Optional explicit evaluation dates (each must appear in the built calendar).

        Returns:
            ``BacktestResult`` with per-test-date returns and summary statistics.

        Raises:
            ValueError: If configuration is inconsistent or the calendar is too short.
            KeyError: If ``portfolio_config['instruments']`` references variables missing from ``data``.
        """
        method_l = str(method).lower().strip()
        if method_l not in {"expanding", "rolling"}:
            raise ValueError("method must be 'expanding' or 'rolling'.")
        if train_window < 1:
            raise ValueError("train_window must be >= 1.")
        if test_window < 1:
            raise ValueError("test_window must be >= 1.")
        if not signals:
            raise ValueError("signals must be a non-empty list.")

        instruments = list(portfolio_config.get("instruments", []) or [])
        if not instruments:
            raise KeyError(
                "portfolio_config['instruments'] is required (list of catalogue variable names "
                "representing tradeable instruments). Pre-5.7 'prices_key' is no longer supported."
            )

        calendar = _build_calendar(data, start_date, end_date)
        if test_dates is not None:
            test_ix = _normalise_test_timestamps(test_dates, calendar)
            prior_before_first = int((calendar < test_ix[0]).sum())
            if method_l == "rolling" and prior_before_first < train_window:
                raise ValueError(
                    f"rolling mode needs at least train_window ({train_window}) strictly in-sample "
                    f"bars before the first custom test date; got {prior_before_first}."
                )
            if prior_before_first < 1:
                raise ValueError("Need at least one calendar row before the first custom test date.")
            test_dates = test_ix
            history = calendar
        else:
            if len(calendar) < train_window + test_window:
                raise ValueError(
                    f"Need at least train_window + test_window ({train_window + test_window}) "
                    f"calendar rows; got {len(calendar)}."
                )
            test_dates = calendar[-test_window:]
            pre_end = test_dates[-1]
            history = calendar[calendar <= pre_end]

        logger.info(
            "BacktestEngine: calendar={n_cal} test={n_test} method={m}",
            n_cal=len(calendar),
            n_test=len(test_dates),
            m=method_l,
        )

        # Price panel for the portfolio layer. This is the 5.7 contract translation
        # boundary: data is Series-per-variable above, panel from here downward.
        prices_aligned = _assemble_price_panel(data, instruments, history)

        # Causal signals: compute(data[:T]) at any t<=T equals compute(data[:t]).iloc[-1].
        # We compute once on data truncated to history[-1] and sample per-date via
        # _signal_value_to_instrument_row, which does an as-of lookup. This is
        # mathematically equivalent to the per-date compute loop for any causal signal.
        signal_frames: List[pd.DataFrame] = []
        for sig in signals:
            sliced_full = _slice_data_no_lookahead(data, history[-1], method_l, train_window)
            raw = sig.compute(sliced_full)
            if not isinstance(raw, pd.Series):
                raise TypeError(f"{sig.name}: compute must return a pandas Series.")
            rows = [_signal_value_to_instrument_row(raw, d, instruments) for d in history]
            sig_df = pd.DataFrame(rows, index=history, columns=instruments).astype(float)
            signal_frames.append(sig_df)

        combined_signals = signal_frames[0].astype(float).copy()
        for sf in signal_frames[1:]:
            combined_signals = combined_signals.add(sf.astype(float), fill_value=0.0)
        combined_signals = (combined_signals / float(len(signal_frames))).reindex(
            columns=instruments
        )

        sizing_method = str(portfolio_config.get("sizing_method", "vol_target"))
        target_vol = float(portfolio_config.get("target_vol", 0.10))
        gross_limit = float(portfolio_config.get("gross_limit", 2.0))
        net_limit = float(portfolio_config.get("net_limit", 0.20))
        asset_classes: Dict[str, str] = dict(portfolio_config.get("asset_classes", {}))

        vol_window = int(portfolio_config.get("vol_window", 60))
        constructor = PortfolioConstructor(position_sizer=PositionSizer(vol_window=vol_window))

        gross_list: List[float] = []
        trade_rows: List[pd.Series] = []

        # Forward-return reference series. The pre-5.7 engine read this from a
        # "close" column if present, else fell back to the first column. Under
        # 5.7 the panel columns ARE the per-instrument close prices (each
        # column is a `{ticker}_CLOSE`-style Series), so we use the first
        # instrument as the reference for the portfolio return. NOTE: this
        # preserves pre-5.7 semantics exactly — the previous code priced the
        # whole portfolio off a single column. Multi-instrument forward
        # returns are a known limitation flagged in PROGRESS.md.
        close_prices = prices_aligned.iloc[:, 0]
        fwd_ret = np.log(close_prices / close_prices.shift(1)).shift(-1)

        for t in test_dates:
            sub_sig = combined_signals.loc[combined_signals.index <= t]
            sub_px = prices_aligned.loc[prices_aligned.index <= t]
            weights, trades = constructor.construct(
                sub_sig,
                sub_px,
                asset_classes=asset_classes,
                sizing_method=sizing_method,
                target_vol=target_vol,
                gross_limit=gross_limit,
                net_limit=net_limit,
            )
            w_t = weights.loc[t]
            fr_val = float(fwd_ret.loc[t]) if t in fwd_ret.index else 0.0
            gross_t = float(w_t.astype(float).sum() * fr_val)
            gross_list.append(gross_t)
            trade_rows.append(trades.loc[t].astype(float))

        gross_returns = pd.Series(gross_list, index=test_dates, dtype=float)
        trades_out = pd.DataFrame(trade_rows, index=test_dates).astype(float)
        net_returns = cost_model.apply_costs(gross_returns, trades_out)

        return build_backtest_result(gross_returns, net_returns, trades_out)
