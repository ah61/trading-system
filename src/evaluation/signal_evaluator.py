"""Signal evaluation metrics and helpers."""

from __future__ import annotations

from dataclasses import dataclass


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

