"""Backtest result container and performance statistics."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

import numpy as np
import pandas as pd


@dataclass(slots=True)
class BacktestResult:
    """Aggregated outputs from a single backtest segment.

    Attributes:
        gross_returns: Period portfolio returns before transaction costs.
        net_returns: Period portfolio returns after `CostModel.apply_costs`.
        annualised_return: Arithmetic annualised mean return (daily * 252).
        annualised_vol: Annualised volatility of net returns (sample std * sqrt(252)).
        sharpe_ratio: Annualised Sharpe of net returns (excess vs 0).
        sortino_ratio: Annualised Sortino using downside deviation of net returns.
        max_drawdown: Maximum peak-to-trough drawdown on cumulative net wealth.
        max_drawdown_duration: Length (in periods) of the max drawdown episode.
        calmar_ratio: annualised_return / abs(max_drawdown) when max_drawdown != 0.
        hit_rate: Fraction of net return periods strictly greater than zero.
        avg_trade_return: Mean gross return on periods with non-zero trading activity.
        total_cost_bps: Sum of per-period cost drag expressed as bps ( (gross-net)*10000 ).
        turnover_annual: Annualised one-way turnover from absolute weight changes.
        trades: Per-instrument trade sizes (weight changes) on the backtest calendar.
    """

    gross_returns: pd.Series
    net_returns: pd.Series
    annualised_return: float
    annualised_vol: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    calmar_ratio: float
    hit_rate: float
    avg_trade_return: float
    total_cost_bps: float
    turnover_annual: float
    trades: pd.DataFrame


@dataclass(slots=True)
class CPCVResult:
    """Outputs from combinatorial purged cross-validation (CPCV) over OOS paths.

    Attributes:
        oos_sharpe_distribution: One out-of-sample Sharpe ratio per evaluated CPCV path.
        oos_sharpe_mean: Mean of ``oos_sharpe_distribution`` (finite values only).
        oos_sharpe_std: Sample standard deviation of ``oos_sharpe_distribution``.
        oos_sharpe_median: Median of ``oos_sharpe_distribution``.
        pbo: Probability of backtest overfitting (rank-logit construction on IS vs OOS Sharpes).
        n_paths: Number of OOS paths generated (capped combinatorially).
    """

    oos_sharpe_distribution: pd.Series
    oos_sharpe_mean: float
    oos_sharpe_std: float
    oos_sharpe_median: float
    pbo: float
    n_paths: int


def _max_drawdown_episode_duration(returns: pd.Series) -> int:
    """Count periods from the last touch of the pre-trough high until recovery to that high."""
    r = returns.dropna().astype(float)
    if r.size < 2:
        return 0
    w = (1.0 + r).cumprod().to_numpy(dtype=float)
    peak_running = np.maximum.accumulate(w)
    dd = w / peak_running - 1.0
    if float(dd.min()) >= -1e-15:
        return 0
    trough_i = int(np.argmin(dd))
    target = float(peak_running[trough_i])
    start_i = 0
    for i in range(trough_i, -1, -1):
        if abs(float(w[i]) - target) <= 1e-9 * max(1.0, abs(target)):
            start_i = i
            break
    rec_i = len(w) - 1
    for j in range(trough_i + 1, len(w)):
        if float(w[j]) >= target - 1e-12:
            rec_i = j
            break
    return int(rec_i - start_i)


def build_backtest_result(
    gross_returns: pd.Series,
    net_returns: pd.Series,
    trades: pd.DataFrame,
) -> BacktestResult:
    """Compute summary statistics and wrap a `BacktestResult`."""
    g = gross_returns.astype(float)
    n = net_returns.astype(float).reindex(g.index).fillna(0.0)

    if len(n) >= 2:
        mu = float(n.mean())
        sd = float(n.std(ddof=1))
        annualised_return = float(mu * 252.0)
        annualised_vol = float(sd * sqrt(252.0)) if np.isfinite(sd) else float("nan")
        sharpe_ratio = float((mu / sd) * sqrt(252.0)) if sd > 0.0 and np.isfinite(sd) else float("nan")
    else:
        annualised_return = float("nan")
        annualised_vol = float("nan")
        sharpe_ratio = float("nan")

    neg = n.copy()
    neg[neg > 0.0] = 0.0
    downside = float(neg.std(ddof=1)) if len(neg) >= 2 else float("nan")
    m = float(n.mean())
    sortino_ratio = (
        float((m / downside) * sqrt(252.0))
        if np.isfinite(downside) and downside > 0.0
        else float("nan")
    )

    wealth = (1.0 + n.fillna(0.0)).cumprod()
    peak = wealth.cummax()
    dd = wealth / peak - 1.0
    max_drawdown = float(dd.min()) if not dd.empty else 0.0
    max_drawdown_duration = _max_drawdown_episode_duration(n)

    calmar_ratio = (
        float(annualised_return / abs(max_drawdown))
        if max_drawdown < -1e-15 and np.isfinite(annualised_return)
        else float("nan")
    )

    hit_rate = float((n > 0.0).mean()) if len(n) else float("nan")

    tr = trades.reindex(g.index).fillna(0.0)
    active = tr.abs().sum(axis=1) > 0.0
    if active.any():
        avg_trade_return = float(g.loc[active].mean())
    else:
        avg_trade_return = float("nan")

    total_cost_bps = float(((g - n) * 10_000.0).sum())

    daily_turn = tr.abs().sum(axis=1)
    turnover_annual = float(daily_turn.mean() * 252.0) if len(daily_turn) else float("nan")

    return BacktestResult(
        gross_returns=g,
        net_returns=n,
        annualised_return=annualised_return,
        annualised_vol=annualised_vol,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        max_drawdown=max_drawdown,
        max_drawdown_duration=max_drawdown_duration,
        calmar_ratio=calmar_ratio,
        hit_rate=hit_rate,
        avg_trade_return=avg_trade_return,
        total_cost_bps=total_cost_bps,
        turnover_annual=turnover_annual,
        trades=tr,
    )
