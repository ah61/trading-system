"""Signal evaluation metrics and helpers.

This module evaluates a signal against forward returns. As of Milestone 5.2
(see ROADMAP.md), evaluation can be run at daily, weekly, or monthly frequency.
The caller specifies the natural frequency of the signal and ``evaluate()``
resamples both signal and returns internally — no manual resampling required.

Frequency conventions
---------------------
- ``frequency='daily'``    : no resampling. ``horizon`` is in trading days.
                            Annualisation factor: ``sqrt(252)``.
- ``frequency='weekly'``   : resample to W-FRI. ``horizon`` is in weeks.
                            Annualisation factor: ``sqrt(52)``.
- ``frequency='monthly'``  : resample to MS (month-start). ``horizon`` is in months.
                            Annualisation factor: ``sqrt(12)``.

Resampling rules:
- Signal:   first non-zero value in the period; if the period contains no
            non-zero values, the previous period's value is carried forward.
            Rationale: zero = "no position", not "no signal".
- Returns:  log returns are summed over the period (compounding under the
            log convention, per CONVENTIONS.md §3.2).

Forward-return shift is applied in periods at the resampled frequency:
``fwd_t = returns.shift(-(horizon + 1))``. The ``+1`` is the execution lag,
expressed in the same unit as ``horizon``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


Frequency = Literal["daily", "weekly", "monthly"]


# Periods per year and resample rule per supported frequency.
_FREQUENCY_TABLE: dict[str, tuple[int, str]] = {
    "daily": (252, "B"),       # business day; primarily a no-op pass-through
    "weekly": (52, "W-FRI"),   # week ending Friday
    "monthly": (12, "MS"),     # month start
}

# Time-equivalent rolling IC window (~one quarter) at each frequency.
# 63 trading days ≈ 13 weeks ≈ 3 months.
_ROLLING_IC_WINDOW: dict[str, int] = {
    "daily": 63,
    "weekly": 13,
    "monthly": 3,
}


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    """Container for signal evaluation metrics.

    Attributes:
        ic_mean: Mean information coefficient.
        ic_std: Standard deviation of rolling IC.
        icir: ic_mean / ic_std.
        ic_positive_pct: Fraction of rolling-IC windows with IC > 0.
        hit_rate: Fraction of non-zero signal observations with correct sign.
        signal_sharpe: Annualised Sharpe of signal-weighted returns.
        turnover: Mean absolute change in signal per period.
        decay_halflife: Lags until rolling-IC autocorrelation drops to 0.5.
        n_observations: Number of paired (signal, forward-return) observations.
        forward_return_horizon: Horizon in periods (at ``frequency``).
        frequency: Evaluation frequency. One of {'daily', 'weekly', 'monthly'}.
    """

    ic_mean: float
    ic_std: float
    icir: float
    ic_positive_pct: float
    hit_rate: float
    signal_sharpe: float
    turnover: float
    decay_halflife: float
    n_observations: int
    forward_return_horizon: int
    frequency: str


def _validate_frequency(frequency: str) -> None:
    if frequency not in _FREQUENCY_TABLE:
        raise ValueError(
            f"Unknown frequency: {frequency!r}. Must be one of "
            f"{sorted(_FREQUENCY_TABLE)}."
        )


def _resample_signal(signal: pd.Series, frequency: str) -> pd.Series:
    """Resample signal to ``frequency``.

    Per period: take the first non-zero value; if all values in the period are
    zero or missing, carry forward the previous period's value.

    Works for both single-index (date) and multi-index (date, asset) series.
    """
    if frequency == "daily":
        return signal

    rule = _FREQUENCY_TABLE[frequency][1]

    def _first_nonzero_or_nan(x: pd.Series) -> float:
        # Drop NaN before searching for non-zero; if everything is NaN/zero,
        # return NaN so the carry-forward step picks it up.
        clean = x.dropna()
        if clean.empty:
            return float("nan")
        nz = clean[clean != 0.0]
        if not nz.empty:
            return float(nz.iloc[0])
        return float("nan")

    if isinstance(signal.index, pd.MultiIndex) and signal.index.nlevels >= 2:
        # Resample within each asset, then carry forward zeros/missing.
        out_parts: list[pd.Series] = []
        # Assume level 0 = date, level 1 = asset (CONVENTIONS).
        asset_level = signal.index.names[1] if signal.index.names[1] is not None else 1
        for asset, group in signal.groupby(level=asset_level, sort=False):
            s = group.droplevel(asset_level).sort_index()
            resampled = s.resample(rule).apply(_first_nonzero_or_nan)
            resampled = resampled.ffill()
            # Re-attach asset level.
            resampled.index = pd.MultiIndex.from_product(
                [resampled.index, [asset]], names=signal.index.names
            )
            out_parts.append(resampled)
        result = pd.concat(out_parts).sort_index()
        return result.astype(float)

    s = signal.sort_index()
    resampled = s.resample(rule).apply(_first_nonzero_or_nan)
    return resampled.ffill().astype(float)


def _resample_log_returns(returns: pd.Series, frequency: str) -> pd.Series:
    """Resample log returns to ``frequency`` by summing within each period.

    Summing log returns is the correct compounding rule under the log
    convention (CONVENTIONS.md §3.2).
    """
    if frequency == "daily":
        return returns

    rule = _FREQUENCY_TABLE[frequency][1]

    if isinstance(returns.index, pd.MultiIndex) and returns.index.nlevels >= 2:
        asset_level = returns.index.names[1] if returns.index.names[1] is not None else 1
        out_parts: list[pd.Series] = []
        for asset, group in returns.groupby(level=asset_level, sort=False):
            s = group.droplevel(asset_level).sort_index().astype(float)
            resampled = s.resample(rule).sum(min_count=1)
            resampled.index = pd.MultiIndex.from_product(
                [resampled.index, [asset]], names=returns.index.names
            )
            out_parts.append(resampled)
        return pd.concat(out_parts).sort_index().astype(float)

    return returns.sort_index().astype(float).resample(rule).sum(min_count=1)


class SignalEvaluator:
    """Evaluate a signal against forward returns at a chosen frequency."""

    @staticmethod
    def _apply_forward_return_convention(
        returns: pd.Series, horizon: int
    ) -> pd.Series:
        r"""Apply forward-return shift in *periods* at the resampled frequency.

        Convention:
            fwd_t(H) = returns.shift(-(H + 1))

        ``+1`` is the execution lag. After resampling, this shift is in
        periods of the chosen frequency (e.g. months for monthly evaluation).
        Applied per asset for MultiIndex returns.
        """
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer.")

        if isinstance(returns.index, pd.MultiIndex) and returns.index.nlevels >= 2:
            asset_level = returns.index.names[1] if returns.index.names[1] is not None else 1
            return returns.groupby(level=asset_level, sort=False).shift(-(horizon + 1))

        return returns.shift(-(horizon + 1))

    @staticmethod
    def _ic_by_date(signal: pd.Series, fwd: pd.Series) -> pd.Series:
        if not isinstance(signal.index, pd.MultiIndex) or signal.index.nlevels < 2:
            raise ValueError("signal must be a MultiIndex Series indexed by (date, asset).")
        if not isinstance(fwd.index, pd.MultiIndex) or fwd.index.nlevels < 2:
            raise ValueError("forward_returns must be a MultiIndex Series indexed by (date, asset).")

        signal = signal.astype(float)
        fwd = fwd.astype(float)
        aligned = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        if aligned.empty:
            return pd.Series(dtype=float)

        def _spearman(group: pd.DataFrame) -> float:
            x = group["signal"].to_numpy(dtype=float, copy=False)
            y = group["fwd"].to_numpy(dtype=float, copy=False)
            if x.size < 2:
                return np.nan
            # A single-valued vector produces a ConstantInputWarning and NaN.
            # Suppress by short-circuiting.
            if np.all(x == x[0]) or np.all(y == y[0]):
                return np.nan
            r = spearmanr(x, y, nan_policy="omit").correlation
            return float(r) if r is not None else np.nan

        ic = aligned.groupby(level=0, sort=True).apply(_spearman)
        ic.index = pd.to_datetime(ic.index, utc=True)
        return ic.sort_index()

    @staticmethod
    def _hit_rate_by_date(signal: pd.Series, fwd: pd.Series) -> float:
        aligned = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        if aligned.empty:
            return float("nan")
        hit = (np.sign(aligned["signal"]) == np.sign(aligned["fwd"])).astype(float)
        return float(hit.groupby(level=0, sort=True).mean().mean())

    @staticmethod
    def _signal_sharpe(
        signal: pd.Series, fwd: pd.Series, annualisation_factor: float
    ) -> float:
        aligned = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        if aligned.empty:
            return float("nan")
        pnl = (aligned["signal"] * aligned["fwd"]).groupby(level=0, sort=True).mean()
        mu = float(pnl.mean())
        sd = float(pnl.std(ddof=1))
        if not np.isfinite(sd) or sd == 0.0:
            return float("nan")
        return float((mu / sd) * np.sqrt(annualisation_factor))

    @staticmethod
    def _turnover(signal: pd.Series) -> float:
        if not isinstance(signal.index, pd.MultiIndex) or signal.index.nlevels < 2:
            return float("nan")
        df = signal.unstack().sort_index()
        return float(df.diff().abs().stack().mean())

    @staticmethod
    def _rolling_spearman(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
        """Rolling Spearman IC over a fixed-size window."""
        n = int(len(x))
        out = pd.Series(np.nan, index=x.index, dtype=float)
        if window < 2 or n < window:
            return out
        arr_x = x.to_numpy(dtype=float)
        arr_y = y.to_numpy(dtype=float)
        for i in range(window - 1, n):
            a = arr_x[i - window + 1 : i + 1]
            b = arr_y[i - window + 1 : i + 1]
            # Short-circuit constant inputs to avoid ConstantInputWarning.
            if np.all(a == a[0]) or np.all(b == b[0]):
                out.iloc[i] = np.nan
                continue
            r = spearmanr(a, b, nan_policy="omit").correlation
            out.iloc[i] = float(r) if r is not None and np.isfinite(r) else np.nan
        return out

    def _evaluate_single_asset(
        self,
        signal: pd.Series,
        forward_returns: pd.Series,
        horizon: int,
        frequency: str,
    ) -> SignalMetrics:
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer.")

        periods_per_year, _ = _FREQUENCY_TABLE[frequency]
        rolling_window = _ROLLING_IC_WINDOW[frequency]

        sig = signal.astype(float).sort_index()
        fwd_full = forward_returns.astype(float).sort_index()
        fwd = fwd_full.shift(-horizon - 1)

        paired = pd.concat({"signal": sig, "fwd": fwd}, axis=1, join="inner").dropna()
        n_obs = int(len(paired))

        if n_obs >= 2:
            s_vals = paired["signal"].to_numpy(dtype=float)
            f_vals = paired["fwd"].to_numpy(dtype=float)
            if np.all(s_vals == s_vals[0]) or np.all(f_vals == f_vals[0]):
                ic_mean = float("nan")
            else:
                r = spearmanr(s_vals, f_vals, nan_policy="omit").correlation
                ic_mean = float(r) if r is not None and np.isfinite(r) else float("nan")

            rolling_ic = self._rolling_spearman(
                paired["signal"], paired["fwd"], window=rolling_window
            )
            rolling_clean = rolling_ic.dropna()
            if rolling_clean.size >= 2:
                ic_std = float(np.nanstd(rolling_ic.to_numpy(dtype=float), ddof=1))
                icir = (
                    float(ic_mean / ic_std)
                    if np.isfinite(ic_mean) and np.isfinite(ic_std) and ic_std != 0.0
                    else float("nan")
                )
                ic_pos = float(np.nanmean((rolling_ic > 0.0).to_numpy(dtype=float)))
            else:
                ic_std = float("nan")
                icir = float("nan")
                ic_pos = float("nan")
        else:
            ic_mean = float("nan")
            ic_std = float("nan")
            icir = float("nan")
            ic_pos = float("nan")

        if n_obs >= 1:
            s_vals = paired["signal"].to_numpy(dtype=float)
            f_vals = paired["fwd"].to_numpy(dtype=float)
            nonzero_mask = s_vals != 0
            if nonzero_mask.sum() > 0:
                hit_rate = float(np.mean(
                    np.sign(s_vals[nonzero_mask]) == np.sign(f_vals[nonzero_mask])
                ))
            else:
                hit_rate = float("nan")

            pnl = s_vals * f_vals
            if pnl.size >= 2:
                mu = float(np.mean(pnl))
                sd = float(np.std(pnl, ddof=1))
                sharpe = (
                    float((mu / sd) * np.sqrt(periods_per_year))
                    if np.isfinite(sd) and sd != 0.0
                    else float("nan")
                )
            else:
                sharpe = float("nan")
        else:
            hit_rate = float("nan")
            sharpe = float("nan")

        turnover = float(sig.diff().abs().mean()) if sig.size >= 2 else float("nan")

        return SignalMetrics(
            ic_mean=ic_mean,
            ic_std=ic_std,
            icir=icir,
            ic_positive_pct=ic_pos,
            hit_rate=hit_rate,
            signal_sharpe=sharpe,
            turnover=turnover,
            decay_halflife=1.0,
            n_observations=n_obs,
            forward_return_horizon=int(horizon),
            frequency=frequency,
        )

    @staticmethod
    def _decay_halflife(ic: pd.Series) -> float:
        x = ic.dropna().to_numpy(dtype=float)
        if x.size < 5:
            return float("nan")
        x = x - np.nanmean(x)
        if not np.isfinite(x).all():
            return float("nan")
        if float(np.nanstd(x)) == 0.0:
            return float("nan")

        for lag in range(1, min(252, x.size // 2) + 1):
            a = x[:-lag]
            b = x[lag:]
            if a.size < 3:
                break
            corr = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(corr) and corr <= 0.5:
                return float(lag)
        return float("nan")

    def evaluate(
        self,
        signal: pd.Series,
        forward_returns: pd.Series,
        horizon: int,
        frequency: Frequency = "daily",
    ) -> SignalMetrics:
        """Evaluate a signal vs forward returns at a given horizon and frequency.

        Args:
            signal: Signal Series indexed by (date, asset) for the multi-asset
                path, or by a plain ``DatetimeIndex`` for a single-asset signal.
                Values should be in [-1, 1].
            forward_returns: 1-period log returns aligned to the signal index.
                The forward-return shift is applied internally as
                ``returns.shift(-(horizon + 1))`` (per asset for MultiIndex).
                Internal resampling will compound (sum) log returns to ``frequency``
                before shifting.
            horizon: Forward horizon in *periods at* ``frequency``. For
                ``frequency='monthly'`` and ``horizon=3``, evaluates predictive
                power over the next 3 months.
            frequency: 'daily' (default), 'weekly', or 'monthly'. Both signal
                and returns are resampled to this frequency before evaluation.

        Returns:
            SignalMetrics. ``forward_return_horizon`` is in periods of ``frequency``.

        Raises:
            ValueError: If ``frequency`` is unknown or ``horizon <= 0``.
        """
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer.")
        _validate_frequency(frequency)

        periods_per_year, _ = _FREQUENCY_TABLE[frequency]

        # Resample inputs to the target frequency. At 'daily' this is a no-op.
        sig_resampled = _resample_signal(signal, frequency)
        ret_resampled = _resample_log_returns(forward_returns, frequency)

        if sig_resampled.index.nlevels == 1:
            return self._evaluate_single_asset(
                sig_resampled, ret_resampled, horizon, frequency
            )

        # Multi-asset path. The contract is that ``forward_returns`` is a series
        # of 1-period log returns (after any resampling). The forward shift is
        # always applied here: ``fwd_t = returns.shift(-(horizon + 1))`` per asset.
        fwd = self._apply_forward_return_convention(ret_resampled, horizon)

        paired = pd.concat({"signal": sig_resampled, "fwd": fwd}, axis=1).dropna()
        n_obs = int(len(paired))

        ic = self._ic_by_date(sig_resampled, fwd)
        ic_mean = float(np.nanmean(ic.to_numpy(dtype=float))) if len(ic) else float("nan")
        ic_std = float(np.nanstd(ic.to_numpy(dtype=float), ddof=1)) if len(ic) else float("nan")
        icir = (
            float(ic_mean / ic_std)
            if np.isfinite(ic_mean) and np.isfinite(ic_std) and ic_std != 0
            else float("nan")
        )
        ic_pos = float(np.nanmean((ic > 0.0).to_numpy(dtype=float))) if len(ic) else float("nan")

        if n_obs > 0:
            sig_arr = paired["signal"].to_numpy(dtype=float)
            fwd_arr = paired["fwd"].to_numpy(dtype=float)
            nonzero_mask = sig_arr != 0
            if nonzero_mask.sum() > 0:
                hit_rate = float(np.mean(
                    np.sign(sig_arr[nonzero_mask]) == np.sign(fwd_arr[nonzero_mask])
                ))
            else:
                hit_rate = float("nan")
        else:
            hit_rate = float("nan")

        sharpe = self._signal_sharpe(sig_resampled, fwd, annualisation_factor=periods_per_year)
        turnover = self._turnover(sig_resampled)
        halflife = self._decay_halflife(ic)

        return SignalMetrics(
            ic_mean=ic_mean,
            ic_std=ic_std,
            icir=icir,
            ic_positive_pct=ic_pos,
            hit_rate=hit_rate,
            signal_sharpe=sharpe,
            turnover=turnover,
            decay_halflife=halflife,
            n_observations=n_obs,
            forward_return_horizon=int(horizon),
            frequency=frequency,
        )
