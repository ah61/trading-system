"""Walk-forward orchestration over sequential train/test segments.

5.7 contract: data is ``Dict[catalogue_variable_name, pd.Series]``. This
orchestrator does not touch data directly — it threads everything through
``BacktestEngine.run`` — so the change is type-hints only.
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import pandas as pd
from loguru import logger

from src.backtest.engine import BacktestEngine, _build_calendar
from src.backtest.results import BacktestResult
from src.portfolio.costs import CostModel
from src.signals.base import Signal


def expanding_fold_train_bar_counts(
    calendar_length: int,
    train_window: int,
    test_window: int,
) -> List[int]:
    """Return the in-sample bar count before each expanding-mode test block.

    Fold ``k`` uses training history of length ``train_window + k * test_window`` bars
    (indices ``0`` inclusive through the bar before the ``k``-th test block).

    Args:
        calendar_length: Total number of aligned timestamps available.
        train_window: Initial in-sample length before the first test block.
        test_window: Length of each out-of-sample block.

    Returns:
        One train-length integer per completed fold (strictly increasing when ``test_window > 0``).
    """
    out: List[int] = []
    k = 0
    while train_window + (k + 1) * test_window <= calendar_length:
        out.append(train_window + k * test_window)
        k += 1
    return out


def rolling_fold_train_bar_counts(
    n_folds: int,
    train_window: int,
) -> List[int]:
    """Return the constant train length for each rolling fold (``train_window`` repeated)."""
    return [train_window] * int(n_folds)


class WalkForwardEngine:
    """Run chained backtests across expanding or rolling walk-forward segments."""

    def __init__(self) -> None:
        self._engine = BacktestEngine()

    def run(
        self,
        signals: List[Signal],
        data: Dict[str, pd.Series],
        portfolio_config: dict,
        cost_model: CostModel,
        start_date: date,
        end_date: date,
        mode: str = "expanding",
        train_window: int = 252 * 5,
        test_window: int = 252,
    ) -> List[BacktestResult]:
        """Execute walk-forward backtests and return one result per completed fold.

        Args:
            signals: Signal objects passed through to ``BacktestEngine``.
            data: ``Dict[catalogue_variable_name, pd.Series]`` (5.7 contract).
            portfolio_config: Portfolio construction settings; must include
                ``instruments`` and ``asset_classes`` (see ``BacktestEngine.run``).
            cost_model: Shared cost model for every fold.
            start_date: Global in-sample calendar start (inclusive).
            end_date: Global calendar end (inclusive).
            mode: ``"expanding"`` grows the training prefix; ``"rolling"`` slides a fixed train
                window forward by ``test_window`` each fold.
            train_window: Training bars before the first test block (rolling) or minimum train
                length for the first expanding fold.
            test_window: OOS length per fold (also the step for rolling mode).

        Returns:
            A list of ``BacktestResult``, one per fold, in chronological order.

        Raises:
            ValueError: If ``mode`` is unknown or the calendar cannot support at least one fold.
        """
        mode_l = str(mode).lower().strip()
        if mode_l not in {"expanding", "rolling"}:
            raise ValueError("mode must be 'expanding' or 'rolling'.")

        cal = _build_calendar(data, start_date, end_date)
        n = len(cal)
        if n < train_window + test_window:
            raise ValueError(
                f"Calendar length {n} is shorter than train_window + test_window "
                f"({train_window + test_window})."
            )

        results: List[BacktestResult] = []

        if mode_l == "expanding":
            k = 0
            while train_window + (k + 1) * test_window <= n:
                seg_end = cal[train_window + (k + 1) * test_window - 1].date()
                seg_start = cal[0].date()
                logger.info("WalkForward expanding fold {k}: {a}..{b}", k=k, a=seg_start, b=seg_end)
                results.append(
                    self._engine.run(
                        signals=signals,
                        data=data,
                        portfolio_config=portfolio_config,
                        cost_model=cost_model,
                        start_date=seg_start,
                        end_date=seg_end,
                        method="expanding",
                        train_window=train_window,
                        test_window=test_window,
                    )
                )
                k += 1
            if not results:
                raise ValueError("Expanding walk-forward produced zero folds.")
            return results

        # rolling
        offset = 0
        while offset + train_window + test_window <= n:
            seg_start = cal[offset].date()
            seg_end = cal[offset + train_window + test_window - 1].date()
            logger.info(
                "WalkForward rolling fold at offset {o}: {a}..{b}",
                o=offset,
                a=seg_start,
                b=seg_end,
            )
            results.append(
                self._engine.run(
                    signals=signals,
                    data=data,
                    portfolio_config=portfolio_config,
                    cost_model=cost_model,
                    start_date=seg_start,
                    end_date=seg_end,
                    method="rolling",
                    train_window=train_window,
                    test_window=test_window,
                )
            )
            offset += test_window

        if not results:
            raise ValueError("Rolling walk-forward produced zero folds.")
        return results
