"""Statistical corrections required before accepting a signal.

Implements:
 - Deflated Sharpe Ratio (Bailey & López de Prado, 2014)
 - Probability of Backtest Overfitting (Bailey et al., 2014)
 - Hansen's Superior Predictive Ability test (Hansen, 2005)
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb

import numpy as np
import pandas as pd
from scipy.stats import norm


def _annualized_sharpe(returns: pd.Series | np.ndarray, periods_per_year: float = 252.0) -> float:
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 2:
        return float("nan")
    mu = float(np.mean(x))
    sd = float(np.std(x, ddof=1))
    if not np.isfinite(sd) or sd == 0.0:
        return float("-inf")
    return float((mu / sd) * np.sqrt(periods_per_year))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    skewness: float,
    kurtosis: float,
) -> float:
    """Compute the Deflated Sharpe Ratio (DSR).

    DSR adjusts an observed Sharpe ratio for:
    - Multiple testing / selection over many trials
    - Non-normality (skewness, kurtosis)
    - Finite sample length

    Args:
        observed_sharpe: Observed Sharpe ratio.
        n_trials: Number of trials/strategies/configurations searched over.
        n_observations: Number of observations used to estimate Sharpe (e.g., daily bars).
        skewness: Sample skewness of the return series.
        kurtosis: Sample kurtosis of the return series (Pearson kurtosis; normal = 3).

    Returns:
        Deflated Sharpe Ratio in [0, 1].

    Notes:
        Thresholding guidance:
        - DSR > 0 required
        - DSR > 0.5 considered robust
    """
    if not np.isfinite(observed_sharpe):
        return float("nan")
    if n_trials <= 0:
        raise ValueError("n_trials must be a positive integer.")
    if n_observations <= 1:
        raise ValueError("n_observations must be > 1.")

    # Bailey & López de Prado (2014) expected maximum Sharpe from n_trials.
    # For n_trials == 1, the expected "max" is the mean (0 under symmetry), and
    # the asymptotic extreme-value approximation becomes ill-posed (ppf(0) = -inf).
    if n_trials == 1:
        sr_star = 0.0
    else:
        euler_gamma = 0.5772
        e = 2.718
        p1 = float(1.0 - 1.0 / n_trials)
        p2 = float(1.0 - 1.0 / (n_trials * e))
        eps = 1e-12
        z1 = float(norm.ppf(np.clip(p1, eps, 1.0 - eps)))
        z2 = float(norm.ppf(np.clip(p2, eps, 1.0 - eps)))
        sr_star = float(((1.0 - euler_gamma) * z1) + (euler_gamma * z2))

    sr = float(observed_sharpe)
    var_sr = (1.0 - float(skewness) * sr + ((float(kurtosis) - 1.0) / 4.0) * (sr**2)) / (
        n_observations - 1
    )
    if not np.isfinite(var_sr) or var_sr <= 0.0:
        return float("nan")

    z = (sr - sr_star) / float(np.sqrt(var_sr))
    return float(norm.cdf(z))


def probability_of_backtest_overfitting(
    returns_matrix: pd.DataFrame,
    n_partitions: int = 16,
) -> float:
    """Compute the Probability of Backtest Overfitting (PBO).

    Args:
        returns_matrix: DataFrame with rows=time and cols=parameter configurations.
        n_partitions: Number of equal time partitions (must be even).

    Returns:
        PBO in [0, 1]. Reject a signal if PBO > 0.5.
    """
    if not isinstance(returns_matrix, pd.DataFrame):
        raise TypeError("returns_matrix must be a pandas DataFrame.")
    if returns_matrix.shape[1] < 2:
        raise ValueError("returns_matrix must have at least 2 parameter configurations (columns).")
    if n_partitions <= 1 or n_partitions % 2 != 0:
        raise ValueError("n_partitions must be an even integer >= 2.")

    rm = returns_matrix.astype(float).copy()
    rm = rm.replace([np.inf, -np.inf], np.nan).dropna(axis=0, how="any")
    n_rows = int(rm.shape[0])
    if n_rows < n_partitions * 2:
        raise ValueError("returns_matrix has too few rows for the requested number of partitions.")

    # Enforce equal chunk sizes by trimming the tail.
    n_trim = n_rows - (n_rows % n_partitions)
    rm = rm.iloc[:n_trim]
    chunk_size = n_trim // n_partitions
    if chunk_size <= 0:
        raise ValueError("n_partitions is too large for the number of observations.")

    chunks: list[np.ndarray] = []
    for i in range(n_partitions):
        start = i * chunk_size
        end = start + chunk_size
        chunks.append(np.arange(start, end, dtype=int))

    k = n_partitions // 2
    all_chunk_ids = list(range(n_partitions))
    total_combos = comb(n_partitions, k)
    combos_iter = combinations(all_chunk_ids, k)
    if total_combos > 100:
        rng = np.random.default_rng(0)
        sampled = rng.choice(total_combos, size=100, replace=False)
        sampled_set = set(int(i) for i in sampled)
        combos: list[tuple[int, ...]] = []
        for idx, c in enumerate(combos_iter):
            if idx in sampled_set:
                combos.append(c)
                if len(combos) >= 100:
                    break
    else:
        combos = list(combos_iter)

    underperform_count = 0
    n_trials = 0

    rm_values = rm.to_numpy(dtype=float, copy=False)
    for train_chunk_ids in combos:
        train_idx = np.concatenate([chunks[i] for i in train_chunk_ids])
        test_chunk_ids = [i for i in all_chunk_ids if i not in set(train_chunk_ids)]
        test_idx = np.concatenate([chunks[i] for i in test_chunk_ids])

        train = rm_values[train_idx, :]
        test = rm_values[test_idx, :]

        # In-sample Sharpe across configs.
        train_mu = np.mean(train, axis=0)
        train_sd = np.std(train, axis=0, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            train_sharpe = (train_mu / train_sd) * np.sqrt(252.0)
        train_sharpe = np.where(np.isfinite(train_sharpe), train_sharpe, -np.inf)
        best_idx = int(np.argmax(train_sharpe))

        # Out-of-sample Sharpe for chosen config and median across all configs.
        test_mu = np.mean(test, axis=0)
        test_sd = np.std(test, axis=0, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            test_sharpe = (test_mu / test_sd) * np.sqrt(252.0)
        test_sharpe = np.where(np.isfinite(test_sharpe), test_sharpe, -np.inf)

        chosen_oos = float(test_sharpe[best_idx])
        median_oos = float(np.median(test_sharpe))

        if chosen_oos < median_oos:
            underperform_count += 1
        n_trials += 1

    if n_trials == 0:
        return float("nan")
    return float(underperform_count / n_trials)


@dataclass(frozen=True, slots=True)
class SPAResult:
    """Result of Hansen's SPA test."""

    p_value: float
    reject_null: bool
    best_strategy_idx: int


def _stationary_bootstrap_indices(
    n: int,
    rng: np.random.Generator,
    block_length: int,
) -> np.ndarray:
    if n <= 0:
        raise ValueError("n must be positive.")
    if block_length <= 0:
        raise ValueError("block_length must be positive.")

    p = 1.0 / float(block_length)
    idx = np.empty(n, dtype=int)
    idx[0] = int(rng.integers(0, n))
    for t in range(1, n):
        if float(rng.random()) < p:
            idx[t] = int(rng.integers(0, n))
        else:
            idx[t] = (idx[t - 1] + 1) % n
    return idx


def hansens_spa_test(
    benchmark_returns: pd.Series,
    strategy_returns_matrix: pd.DataFrame,
    n_bootstrap: int = 1000,
    significance: float = 0.05,
) -> SPAResult:
    """Run Hansen's (2005) Superior Predictive Ability (SPA) test.

    Tests whether the best strategy in a candidate set genuinely outperforms a benchmark, while
    accounting for data-snooping across multiple strategies.

    Args:
        benchmark_returns: Benchmark return series.
        strategy_returns_matrix: DataFrame with strategy returns (rows=time, cols=strategies).
        n_bootstrap: Number of stationary bootstrap samples.
        significance: Test significance level used for `reject_null`.

    Returns:
        SPAResult with p_value, reject_null, and best_strategy_idx.
    """
    if not isinstance(benchmark_returns, pd.Series):
        raise TypeError("benchmark_returns must be a pandas Series.")
    if not isinstance(strategy_returns_matrix, pd.DataFrame):
        raise TypeError("strategy_returns_matrix must be a pandas DataFrame.")
    if n_bootstrap <= 0:
        raise ValueError("n_bootstrap must be positive.")
    if not (0.0 < significance < 1.0):
        raise ValueError("significance must be in (0, 1).")
    if strategy_returns_matrix.shape[1] < 1:
        raise ValueError("strategy_returns_matrix must have at least one strategy (one column).")

    bench, strat = benchmark_returns.astype(float).align(
        strategy_returns_matrix.astype(float),
        join="inner",
        axis=0,
    )
    aligned = pd.concat({"bench": bench, "strat": strat}, axis=1).replace([np.inf, -np.inf], np.nan)
    aligned = aligned.dropna(axis=0, how="any")
    if aligned.empty:
        return SPAResult(p_value=float("nan"), reject_null=False, best_strategy_idx=-1)

    bench = aligned["bench"]
    if isinstance(bench, pd.DataFrame):
        bench = bench.iloc[:, 0]
    strat = aligned["strat"]
    d = strat.sub(bench, axis=0)  # performance differential vs benchmark

    n = int(d.shape[0])
    block_length = int(n**0.25)
    block_length = max(1, block_length)

    mean_d = d.mean(axis=0).to_numpy(dtype=float, copy=False)
    best_idx = int(np.argmax(mean_d))
    observed_stat = float(np.max(mean_d))

    # Bootstrap under the null by centering each strategy's differential at zero.
    d0 = d - d.mean(axis=0)
    d0_values = d0.to_numpy(dtype=float, copy=False)

    rng = np.random.default_rng(0)
    boot_stats = np.empty(n_bootstrap, dtype=float)
    for b in range(n_bootstrap):
        idx = _stationary_bootstrap_indices(n=n, rng=rng, block_length=block_length)
        m = np.mean(d0_values[idx, :], axis=0)
        boot_stats[b] = float(np.max(m))

    # One-sided p-value: P(bootstrap max >= observed max)
    p_value = float((1.0 + np.sum(boot_stats >= observed_stat)) / (n_bootstrap + 1.0))
    p_value = float(np.clip(p_value, 0.0, 1.0))
    reject = bool((p_value < significance) and (observed_stat > 0.0))

    return SPAResult(p_value=p_value, reject_null=reject, best_strategy_idx=best_idx)

