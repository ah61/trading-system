"""Signal evaluation metrics and helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import spearmanr


@dataclass(frozen=True, slots=True)
class SignalMetrics:
    """Container for signal evaluation metrics."""

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


class SignalEvaluator:
    """Evaluate a signal against forward returns."""

    @staticmethod
    def _apply_forward_return_convention(log_returns: pd.Series, horizon: int) -> pd.Series:
        r"""Apply CONVENTIONS.md forward-return shift.

        Convention:
            If input is a 1-day log return series \(r_t\), then the forward return for horizon H
            with an execution lag is:

                fwd_t(H) = r.shift(-(H + 1))

        This helper applies the shift only; `evaluate()` decides whether to use shifted or
        unshifted inputs based on alignment with `signal`.
        """
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer.")

        return log_returns.shift(-(horizon + 1))

    @staticmethod
    def _ic_by_date(signal: pd.Series, fwd: pd.Series) -> pd.Series:
        if not isinstance(signal.index, pd.MultiIndex) or signal.index.nlevels < 2:
            raise ValueError("signal must be a MultiIndex Series indexed by (date, asset).")
        if not isinstance(fwd.index, pd.MultiIndex) or fwd.index.nlevels < 2:
            raise ValueError("forward_returns must be a MultiIndex Series indexed by (date, asset).")

        signal = signal.astype(float)
        fwd = fwd.astype(float)
        # Align on the MultiIndex intersection
        aligned = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        if aligned.empty:
            return pd.Series(dtype=float)

        def _spearman(group: pd.DataFrame) -> float:
            x = group["signal"].to_numpy(dtype=float, copy=False)
            y = group["fwd"].to_numpy(dtype=float, copy=False)
            if x.size < 2:
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
    def _signal_sharpe(signal: pd.Series, fwd: pd.Series) -> float:
        aligned = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        if aligned.empty:
            return float("nan")
        pnl = (aligned["signal"] * aligned["fwd"]).groupby(level=0, sort=True).mean()
        mu = float(pnl.mean())
        sd = float(pnl.std(ddof=1))
        if not np.isfinite(sd) or sd == 0.0:
            return float("nan")
        return float((mu / sd) * np.sqrt(252.0))

    @staticmethod
    def _turnover(signal: pd.Series) -> float:
        if not isinstance(signal.index, pd.MultiIndex) or signal.index.nlevels < 2:
            return float("nan")
        df = signal.unstack().sort_index()
        return float(df.diff().abs().stack().mean())

    @staticmethod
    def _rolling_spearman(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
        """Rolling Spearman IC over a fixed-size window.

        Returns a Series aligned to ``x.index``; positions where the window cannot be
        formed (or the window is degenerate) contain NaN.
        """
        n = int(len(x))
        out = pd.Series(np.nan, index=x.index, dtype=float)
        if window < 2 or n < window:
            return out
        arr_x = x.to_numpy(dtype=float)
        arr_y = y.to_numpy(dtype=float)
        for i in range(window - 1, n):
            a = arr_x[i - window + 1 : i + 1]
            b = arr_y[i - window + 1 : i + 1]
            r = spearmanr(a, b, nan_policy="omit").correlation
            out.iloc[i] = float(r) if r is not None and np.isfinite(r) else np.nan
        return out

    def _evaluate_single_asset(
        self,
        signal: pd.Series,
        forward_returns: pd.Series,
        horizon: int,
    ) -> "SignalMetrics":
        if horizon <= 0:
            raise ValueError("horizon must be a positive integer.")

        sig = signal.astype(float).sort_index()
        fwd_full = forward_returns.astype(float).sort_index()
        fwd = fwd_full.shift(-horizon - 1)

        paired = pd.concat({"signal": sig, "fwd": fwd}, axis=1, join="inner").dropna()
        n_obs = int(len(paired))

        if n_obs >= 2:
            s_vals = paired["signal"].to_numpy(dtype=float)
            f_vals = paired["fwd"].to_numpy(dtype=float)
            r = spearmanr(s_vals, f_vals, nan_policy="omit").correlation
            ic_mean = float(r) if r is not None and np.isfinite(r) else float("nan")

            rolling_ic = self._rolling_spearman(paired["signal"], paired["fwd"], window=63)
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
                    float((mu / sd) * np.sqrt(252.0))
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

    def evaluate(self, signal: pd.Series, forward_returns: pd.Series, horizon: int) -> SignalMetrics:
        """Evaluate a signal vs forward returns at a given horizon.

        Args:
            signal: Signal Series indexed by (date, asset) for the multi-asset path, or
                by a plain ``DatetimeIndex`` for a single-asset signal. Values should be in
                [-1, 1].
            forward_returns: Either:
                - forward returns already aligned to (date, asset), or
                - 1-day log returns (date, asset) in which case the forward return convention is applied
                  internally:
                      fwd = log_returns.shift(-(horizon + 1))  # +1 for execution lag
            horizon: Horizon in trading days.

        Returns:
            SignalMetrics containing IC statistics, hit rate, weighted-return Sharpe, turnover, and
            other diagnostics.
        """
        if signal.index.nlevels == 1:
            return self._evaluate_single_asset(signal, forward_returns, horizon)

        # Multi-asset path. ``signal`` and ``forward_returns`` are both MultiIndex
        # (date, asset) Series. The forward-return shift is applied per asset (via the
        # convention helper). Two callers exist in practice: those passing 1-day log
        # returns (need the internal shift) and those passing already-aligned forward
        # returns (no further shift); pick whichever yields the larger aligned panel.
        fwd_as_is = forward_returns
        fwd_shifted = self._apply_forward_return_convention(forward_returns, horizon)

        paired_as_is = pd.concat({"signal": signal, "fwd": fwd_as_is}, axis=1).dropna()
        paired_shifted = pd.concat({"signal": signal, "fwd": fwd_shifted}, axis=1).dropna()
        fwd = fwd_shifted if len(paired_shifted) >= len(paired_as_is) else fwd_as_is

        # Align for metrics requiring paired observations.
        paired = pd.concat({"signal": signal, "fwd": fwd}, axis=1).dropna()
        n_obs = int(len(paired))

        # Cross-sectional IC: one Spearman rank-correlation per date across assets.
        ic = self._ic_by_date(signal, fwd)
        ic_mean = float(np.nanmean(ic.to_numpy(dtype=float))) if len(ic) else float("nan")
        ic_std = float(np.nanstd(ic.to_numpy(dtype=float), ddof=1)) if len(ic) else float("nan")
        icir = float(ic_mean / ic_std) if np.isfinite(ic_mean) and np.isfinite(ic_std) and ic_std != 0 else float("nan")
        ic_pos = float(np.nanmean((ic > 0.0).to_numpy(dtype=float))) if len(ic) else float("nan")

        # hit_rate: global fraction of (date, asset) pairs whose signs match.
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

        sharpe = self._signal_sharpe(signal, fwd)
        turnover = self._turnover(signal)
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
        )
