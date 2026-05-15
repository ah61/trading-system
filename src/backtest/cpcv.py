"""Combinatorial purged cross-validation (CPCV) orchestration for backtests.

5.7 contract: data is ``Dict[catalogue_variable_name, pd.Series]``. The only
substantive change vs the pre-5.7 module is ``_restrict_data_to_timestamps``,
which now slices Series instead of DataFrames.

References:
    López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
"""

from __future__ import annotations

import itertools
import math
from datetime import date
from typing import Dict, List

import numpy as np
import pandas as pd
from loguru import logger
from scipy.special import logit
from scipy.stats import rankdata

from src.backtest.engine import BacktestEngine, _as_utc_index, _build_calendar
from src.backtest.results import CPCVResult
from src.portfolio.costs import CostModel
from src.signals.base import Signal

_MAX_COMBINATIONS = 50


def _restrict_data_to_timestamps(
    data: Dict[str, pd.Series],
    timestamps: pd.DatetimeIndex,
) -> Dict[str, pd.Series]:
    """Return a shallow copy of ``data`` restricted to rows whose index is in ``timestamps``."""
    allow = set(pd.DatetimeIndex(timestamps))
    out: Dict[str, pd.Series] = {}
    for name, series in data.items():
        s = series.copy()
        s.index = _as_utc_index(s.index)
        out[name] = s.loc[s.index.isin(allow)].sort_index()
    return out


def _pbo_rank_logit(is_sharpes: List[float], oos_sharpes: List[float]) -> float:
    """Symmetric rank-based PBO estimate from paired in-sample and OOS Sharpes.

    Uses the logit-rank difference construction described in López de Prado (2018), Ch. 7.

    Args:
        is_sharpes: In-sample Sharpe ratio per CPCV path (same ordering as ``oos_sharpes``).
        oos_sharpes: Out-of-sample Sharpe ratio per CPCV path.

    Returns:
        Probability in ``[0, 1]``; ``0.5`` when fewer than two finite pairs exist.
    """
    is_a = np.asarray(is_sharpes, dtype=float)
    oos_a = np.asarray(oos_sharpes, dtype=float)
    mask = np.isfinite(is_a) & np.isfinite(oos_a)
    if int(mask.sum()) < 2:
        return 0.5
    is_a = is_a[mask]
    oos_a = oos_a[mask]
    n = int(is_a.size)
    r_is = rankdata(is_a)
    r_oos = rankdata(oos_a)
    eps = 1e-9
    p_is = (r_is - 0.5) / float(n)
    p_oos = (r_oos - 0.5) / float(n)
    p_is = np.clip(p_is, eps, 1.0 - eps)
    p_oos = np.clip(p_oos, eps, 1.0 - eps)
    omega = logit(p_is) - logit(p_oos)
    return float(np.clip(np.mean(omega > 0.0), 0.0, 1.0))


class CPCVEngine:
    """Run combinatorial purged cross-validation using ``BacktestEngine`` OOS segments."""

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
        n_groups: int = 10,
        k_test: int = 2,
    ) -> CPCVResult:
        """Execute CPCV by enumerating held-out group combinations (capped at 50).

        The timeline is partitioned into ``n_groups`` contiguous blocks of (approximately) equal
        length. Each combination of ``k_test`` blocks defines an OOS calendar; the complement
        defines the in-sample region used only for the PBO rank construction.

        Args:
            signals: Signal objects passed through to ``BacktestEngine``.
            data: ``Dict[catalogue_variable_name, pd.Series]`` (5.7 contract).
            portfolio_config: Portfolio construction settings; must include
                ``instruments`` and ``asset_classes`` (see ``BacktestEngine.run``).
            cost_model: Shared cost model for every path.
            start_date: Inclusive global calendar start.
            end_date: Inclusive global calendar end.
            n_groups: Number of contiguous partitions of the aligned calendar.
            k_test: Number of partitions held out as OOS per path.

        Returns:
            ``CPCVResult`` with OOS Sharpe distribution, summary moments, and PBO.

        Raises:
            ValueError: If ``n_groups``, ``k_test``, or the calendar cannot support CPCV.
        """
        if n_groups < 2:
            raise ValueError("n_groups must be >= 2.")
        if k_test < 1 or k_test > n_groups:
            raise ValueError("k_test must satisfy 1 <= k_test <= n_groups.")

        calendar = _build_calendar(data, start_date, end_date)
        n_cal = len(calendar)
        if n_cal < n_groups + 1:
            raise ValueError(
                f"Calendar length {n_cal} must be at least n_groups + 1 ({n_groups + 1}) for CPCV "
                "(one warmup bar is reserved before the first possible OOS date)."
            )

        tail = np.arange(1, n_cal, dtype=int)
        splits = np.array_split(tail, n_groups)
        combos_all = list(itertools.combinations(range(n_groups), k_test))
        combos = combos_all[:_MAX_COMBINATIONS]
        n_paths = len(combos)
        n_comb = math.comb(n_groups, k_test)

        logger.info(
            "CPCVEngine: n_cal={n} n_groups={g} k_test={k} paths={p} (cap {c}, full comb {fc})",
            n=n_cal,
            g=n_groups,
            k=k_test,
            p=n_paths,
            c=_MAX_COMBINATIONS,
            fc=n_comb,
        )

        oos_sharpes: List[float] = []
        is_sharpes: List[float] = []

        for combo in combos:
            test_idx = np.unique(np.concatenate([splits[g] for g in combo]))
            test_cal = pd.DatetimeIndex(calendar[test_idx])
            train_mask = np.ones(n_cal, dtype=bool)
            train_mask[test_idx] = False
            train_cal = pd.DatetimeIndex(calendar[train_mask])

            train_window = max(5, min(252 * 5, int((calendar < test_cal[0]).sum())))
            oos_res = self._engine.run(
                signals=signals,
                data=data,
                portfolio_config=portfolio_config,
                cost_model=cost_model,
                start_date=start_date,
                end_date=end_date,
                method="expanding",
                train_window=train_window,
                test_window=1,
                test_dates=test_cal,
            )
            oos_sharpes.append(float(oos_res.sharpe_ratio))

            is_sh = self._is_sharpe_train_tail(
                signals=signals,
                data=data,
                portfolio_config=portfolio_config,
                cost_model=cost_model,
                train_cal=train_cal,
            )
            is_sharpes.append(is_sh)

        oos_series = pd.Series(oos_sharpes, dtype=float, name="oos_sharpe")
        finite = oos_series.replace([np.inf, -np.inf], np.nan).dropna()
        if finite.empty:
            mean_s = float("nan")
            std_s = float("nan")
            med_s = float("nan")
        else:
            mean_s = float(finite.mean())
            std_s = float(finite.std(ddof=1)) if finite.size > 1 else 0.0
            med_s = float(finite.median())

        pbo = _pbo_rank_logit(is_sharpes, oos_sharpes)

        return CPCVResult(
            oos_sharpe_distribution=oos_series,
            oos_sharpe_mean=mean_s,
            oos_sharpe_std=std_s,
            oos_sharpe_median=med_s,
            pbo=pbo,
            n_paths=n_paths,
        )

    def _is_sharpe_train_tail(
        self,
        signals: List[Signal],
        data: Dict[str, pd.Series],
        portfolio_config: dict,
        cost_model: CostModel,
        train_cal: pd.DatetimeIndex,
    ) -> float:
        """In-sample Sharpe on a terminal slice of the train-only calendar (for PBO ranks)."""
        if len(train_cal) < 8:
            return float("nan")
        train_data = _restrict_data_to_timestamps(data, train_cal)
        tr_start = train_cal[0].date()
        tr_end = train_cal[-1].date()
        is_test_len = max(2, min(20, len(train_cal) // 2))
        is_test_dates = train_cal[-is_test_len:]
        train_window = max(3, min(252 * 5, len(train_cal) - is_test_len - 1))
        try:
            res = self._engine.run(
                signals=signals,
                data=train_data,
                portfolio_config=portfolio_config,
                cost_model=cost_model,
                start_date=tr_start,
                end_date=tr_end,
                method="expanding",
                train_window=train_window,
                test_window=1,
                test_dates=is_test_dates,
            )
        except ValueError:
            return float("nan")
        return float(res.sharpe_ratio)
