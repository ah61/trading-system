"""Evaluation utilities (IC, Sharpe, turnover, etc.)."""

from __future__ import annotations

from src.evaluation.corrections import (
    SPAResult,
    deflated_sharpe_ratio,
    hansens_spa_test,
    probability_of_backtest_overfitting,
)
from src.evaluation.signal_evaluator import SignalEvaluator, SignalMetrics

__all__ = [
    "SPAResult",
    "SignalEvaluator",
    "SignalMetrics",
    "deflated_sharpe_ratio",
    "hansens_spa_test",
    "probability_of_backtest_overfitting",
]

