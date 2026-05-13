"""Walk-forward aware backtest engine with strict no-lookahead slicing."""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import pandas as pd
from loguru import logger

from src.backtest.results import BacktestResult, build_backtest_result
from src.portfolio.constructor import PortfolioConstructor
from src.portfolio.costs import CostModel
from src.portfolio.sizing import PositionSizer
from src.signals.base import Signal


def _as_utc_index(idx: pd.Index) -> pd.DatetimeIndex:
    """Normalise an index to a sorted UTC `DatetimeIndex`."""
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        return idx.tz_localize("UTC")
    return idx.tz_convert("UTC")


def _utc_timestamp(d: date) -> pd.Timestamp:
    return pd.Timestamp(d, tz="UTC")


def _build_calendar(
    data: Dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
) -> pd.DatetimeIndex:
    """Union of all dataset timestamps, filtered to ``[start_date, end_date]`` (inclusive)."""
    start_ts = _utc_timestamp(start_date)
    end_ts = _utc_timestamp(end_date)
    if end_ts < start_ts:
        raise ValueError("end_date must be on or after start_date.")

    union: pd.DatetimeIndex | None = None
    for df in data.values():
        ix = _as_utc_index(df.index)
        union = ix if union is None else union.union(ix)

    if union is None or len(union) == 0:
        raise ValueError("data must contain at least one non-empty DataFrame with a datetime index.")

    union = union.sort_values()
    mask = (union >= start_ts) & (union <= end_ts)
    out = union[mask]
    if len(out) == 0:
        raise ValueError("No overlapping calendar dates between data and [start_date, end_date].")
    return pd.DatetimeIndex(out)


def _slice_data_no_lookahead(
    data: Dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    method: str,
    train_window: int,
) -> Dict[str, pd.DataFrame]:
    """Slice every frame to ``index <= as_of``, optionally capping history for rolling mode."""
    out: Dict[str, pd.DataFrame] = {}
    for name, df in data.items():
        idx = _as_utc_index(df.index)
        frame = df.copy()
        frame.index = idx
        sub = frame.loc[frame.index <= as_of]
        if method == "rolling" and train_window > 0 and len(sub) > train_window:
            sub = sub.iloc[-train_window:]
        out[name] = sub
    return out


def _signal_value_to_instrument_row(
    raw: pd.Series,
    as_of: pd.Timestamp,
    instruments: List[str],
) -> pd.Series:
    """Map a signal ``compute`` output to a single cross-section at ``as_of``."""
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


class BacktestEngine:
    """Run a single train/test style backtest with strict causal data feeds."""

    def run(
        self,
        signals: List[Signal],
        data: Dict[str, pd.DataFrame],
        portfolio_config: dict,
        cost_model: CostModel,
        start_date: date,
        end_date: date,
        method: str = "expanding",
        train_window: int = 252 * 5,
        test_window: int = 252,
    ) -> BacktestResult:
        """Execute one backtest over the last ``test_window`` dates in the requested range.

        For every calendar date ``d`` (including training history), each ``Signal.compute`` call
        receives only data causally available at ``d``:

        - ``expanding``: all history up to and including ``d``.
        - ``rolling``: at most the last ``train_window`` observations ending at ``d``.

        Portfolio weights for a test date ``t`` use ``PortfolioConstructor`` on signals and
        prices indexed ``<= t`` only. Gross returns use weights at ``t`` against one-step-ahead
        simple returns; ``CostModel.apply_costs`` yields net returns.

        Args:
            signals: Concrete ``Signal`` instances to combine with equal weights (Phase 1).
            data: Named datasets (e.g. ``{"prices": ...}``) required by signals and sizing.
            portfolio_config: Keys include ``prices_key`` (default ``"prices"``), ``asset_classes``,
                and optional ``sizing_method``, ``target_vol``, ``gross_limit``, ``net_limit``.
            cost_model: Transaction cost model applied to gross returns and rebalance trades.
            start_date: Inclusive range start (calendar date).
            end_date: Inclusive range end (calendar date).
            method: ``"expanding"`` or ``"rolling"`` history window for signal inputs.
            train_window: Maximum in-sample length for rolling mode (trading days / bars).
            test_window: Number of most recent dates in-range used as the test return series.

        Returns:
            ``BacktestResult`` with per-test-date returns and summary statistics.

        Raises:
            ValueError: If configuration is inconsistent or the calendar is too short.
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

        prices_key = str(portfolio_config.get("prices_key", "prices"))
        if prices_key not in data:
            raise KeyError(f"portfolio_config['prices_key']={prices_key!r} not found in data.")

        prices_full = data[prices_key].copy()
        prices_full.index = _as_utc_index(prices_full.index)
        instruments = list(prices_full.columns)

        calendar = _build_calendar(data, start_date, end_date)
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

        signal_frames: List[pd.DataFrame] = []
        for sig in signals:
            rows: List[pd.Series] = []
            for d in history:
                sliced = _slice_data_no_lookahead(data, d, method_l, train_window)
                raw = sig.compute(sliced)
                if not isinstance(raw, pd.Series):
                    raise TypeError(f"{sig.name}: compute must return a pandas Series.")
                rows.append(_signal_value_to_instrument_row(raw, d, instruments))
            sig_df = pd.DataFrame(rows, index=history, columns=instruments).astype(float)
            signal_frames.append(sig_df)

        combined_signals = signal_frames[0].astype(float).copy()
        for sf in signal_frames[1:]:
            combined_signals = combined_signals.add(sf.astype(float), fill_value=0.0)
        combined_signals = (combined_signals / float(len(signal_frames))).reindex(
            columns=instruments
        )

        prices_aligned = prices_full.reindex(history).ffill()

        sizing_method = str(portfolio_config.get("sizing_method", "vol_target"))
        target_vol = float(portfolio_config.get("target_vol", 0.10))
        gross_limit = float(portfolio_config.get("gross_limit", 2.0))
        net_limit = float(portfolio_config.get("net_limit", 0.20))
        asset_classes: Dict[str, str] = dict(portfolio_config.get("asset_classes", {}))

        vol_window = int(portfolio_config.get("vol_window", 60))
        constructor = PortfolioConstructor(position_sizer=PositionSizer(vol_window=vol_window))

        gross_list: List[float] = []
        trade_rows: List[pd.Series] = []

        fwd_ret = prices_aligned.pct_change().shift(-1)

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
            fr = fwd_ret.loc[t] if t in fwd_ret.index else pd.Series(0.0, index=instruments)
            gross_t = float((w_t.astype(float) * fr.fillna(0.0).astype(float)).sum())
            gross_list.append(gross_t)
            trade_rows.append(trades.loc[t].astype(float))

        gross_returns = pd.Series(gross_list, index=test_dates, dtype=float)
        trades_out = pd.DataFrame(trade_rows, index=test_dates).astype(float)
        net_returns = cost_model.apply_costs(gross_returns, trades_out)

        return build_backtest_result(gross_returns, net_returns, trades_out)
