from __future__ import annotations

import numpy as np
import pandas as pd

from src.evaluation.signal_evaluator import SignalEvaluator
from src.evaluation.corrections import (
    SPAResult,
    deflated_sharpe_ratio,
    hansens_spa_test,
    probability_of_backtest_overfitting,
)


def _panel_series(dates: pd.DatetimeIndex, assets: list[str], values: np.ndarray) -> pd.Series:
    idx = pd.MultiIndex.from_product([dates, assets], names=["date", "asset"])
    return pd.Series(values.reshape(len(dates) * len(assets)), index=idx, dtype=float)


def test_ic_mean_positive_for_perfect_signal() -> None:
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=120, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(30)]

    # Cross-sectional signal each day.
    signal_vals = rng.normal(size=(len(dates), len(assets)))
    signal = _panel_series(dates, assets, signal_vals)

    # Perfect forward returns: monotone in signal.
    fwd = _panel_series(dates, assets, signal_vals)
    horizon = 1
    log_returns = fwd

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    assert m.ic_mean > 0.9


def test_ic_mean_near_zero_for_random_signal() -> None:
    rng = np.random.default_rng(1)
    dates = pd.date_range("2024-01-01", periods=120, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(40)]

    signal = _panel_series(dates, assets, rng.normal(size=(len(dates), len(assets))))
    fwd = _panel_series(dates, assets, rng.normal(size=(len(dates), len(assets))))
    horizon = 5
    log_returns = fwd.unstack().shift(horizon + 1).stack(future_stack=False, dropna=False)

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    assert abs(m.ic_mean) < 0.3


def test_icir_computed_correctly() -> None:
    rng = np.random.default_rng(2)
    dates = pd.date_range("2024-01-01", periods=160, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(25)]

    signal_vals = rng.normal(size=(len(dates), len(assets)))
    noise = rng.normal(scale=0.5, size=(len(dates), len(assets)))
    signal = _panel_series(dates, assets, signal_vals)
    fwd = _panel_series(dates, assets, signal_vals + noise)
    horizon = 1
    log_returns = fwd.unstack().shift(horizon + 1).stack(future_stack=False, dropna=False)

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    if np.isfinite(m.ic_std) and m.ic_std != 0:
        assert m.icir == m.ic_mean / m.ic_std


def test_hit_rate_one_for_perfect_signal() -> None:
    dates = pd.date_range("2024-01-01", periods=120, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(20)]
    vals = np.tile(np.linspace(-1.0, 1.0, len(assets)), (len(dates), 1))
    signal = _panel_series(dates, assets, vals)
    fwd = _panel_series(dates, assets, vals)
    horizon = 1
    log_returns = fwd

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    assert m.hit_rate == 1.0


def test_signal_sharpe_positive_for_good_signal() -> None:
    rng = np.random.default_rng(3)
    dates = pd.date_range("2024-01-01", periods=180, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(30)]

    signal_vals = rng.normal(size=(len(dates), len(assets)))
    signal = _panel_series(dates, assets, signal_vals)
    fwd = _panel_series(dates, assets, signal_vals * 0.02 + rng.normal(scale=0.01, size=signal_vals.shape))
    horizon = 1
    log_returns = fwd

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    assert m.signal_sharpe > 0.0


def test_n_observations_correct() -> None:
    rng = np.random.default_rng(4)
    dates = pd.date_range("2024-01-01", periods=90, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(10)]
    signal_vals = rng.normal(size=(len(dates), len(assets)))
    signal = _panel_series(dates, assets, signal_vals)

    fwd = _panel_series(dates, assets, rng.normal(size=(len(dates), len(assets))))
    horizon = 5
    log_returns = fwd.unstack().shift(horizon + 1).stack(future_stack=False, dropna=False)

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)

    # After applying shift(-(h+1)), the last (h+1) dates per asset are NaN.
    expected = (len(dates) - (horizon + 1)) * len(assets)
    assert m.n_observations == expected


def test_evaluator_handles_single_asset_signal() -> None:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=200, freq="B", tz="UTC")

    signal_vals = rng.normal(size=len(dates))
    fwd_vals = signal_vals * 0.5 + rng.normal(scale=0.5, size=len(dates))

    signal = pd.Series(signal_vals, index=dates, dtype=float, name="signal")
    log_returns = pd.Series(fwd_vals, index=dates, dtype=float, name="ret")

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=1)

    assert m.forward_return_horizon == 1
    # After applying shift(-(horizon + 1)) the last 2 obs become NaN and are dropped.
    assert m.n_observations == len(dates) - 2
    assert np.isfinite(m.ic_mean)
    assert np.isfinite(m.hit_rate)
    assert 0.0 <= m.hit_rate <= 1.0
    assert np.isfinite(m.turnover)
    assert m.decay_halflife == 1.0


def test_dsr_perfect_signal_high() -> None:
    dsr = deflated_sharpe_ratio(
        observed_sharpe=3.0,
        n_trials=1,
        n_observations=252,
        skewness=0.0,
        kurtosis=3.0,
    )
    assert 0.99 <= dsr <= 1.0


def test_dsr_low_sharpe_many_trials() -> None:
    dsr = deflated_sharpe_ratio(
        observed_sharpe=0.2,
        n_trials=1000,
        n_observations=252,
        skewness=0.0,
        kurtosis=3.0,
    )
    assert 0.0 <= dsr <= 0.05


def test_pbo_returns_float_between_zero_and_one() -> None:
    rng = np.random.default_rng(10)
    rm = pd.DataFrame(rng.normal(scale=0.01, size=(320, 8)))
    pbo = probability_of_backtest_overfitting(rm, n_partitions=16)
    assert 0.0 <= pbo <= 1.0


def test_pbo_overfit_strategy_high_pbo() -> None:
    # Construct two configs that alternate regime dominance by partition:
    # whichever config wins in-sample loses out-of-sample for every split.
    rng = np.random.default_rng(11)
    n_partitions = 16
    rows_per_partition = 10
    n = n_partitions * rows_per_partition

    r0 = np.empty(n, dtype=float)
    r1 = np.empty(n, dtype=float)
    for p in range(n_partitions):
        start = p * rows_per_partition
        end = start + rows_per_partition
        if p % 2 == 0:
            r0[start:end] = 0.01
            r1[start:end] = -0.01
        else:
            r0[start:end] = -0.01
            r1[start:end] = 0.01

    # Add tiny noise so Sharpe is well-defined.
    r0 = r0 + rng.normal(scale=1e-4, size=n)
    r1 = r1 + rng.normal(scale=1e-4, size=n)

    rm = pd.DataFrame({"cfg0": r0, "cfg1": r1})
    pbo = probability_of_backtest_overfitting(rm, n_partitions=n_partitions)
    assert pbo > 0.5


def test_spa_returns_spa_result() -> None:
    rng = np.random.default_rng(12)
    n = 250
    bench = pd.Series(rng.normal(scale=0.01, size=n))
    strat = pd.DataFrame(
        {
            "s0": rng.normal(scale=0.01, size=n),
            "s1": rng.normal(scale=0.01, size=n),
            "s2": rng.normal(scale=0.01, size=n),
        }
    )
    res = hansens_spa_test(bench, strat, n_bootstrap=200, significance=0.05)
    assert isinstance(res, SPAResult)
    assert hasattr(res, "p_value")
    assert hasattr(res, "reject_null")
    assert hasattr(res, "best_strategy_idx")
    assert 0.0 <= res.p_value <= 1.0


def test_spa_best_strategy_beats_benchmark() -> None:
    rng = np.random.default_rng(13)
    n = 300
    bench = pd.Series(np.zeros(n, dtype=float))
    strat = pd.DataFrame(
        {
            "winner": 0.001 + rng.normal(scale=0.0002, size=n),
            "flat": rng.normal(scale=0.0002, size=n),
            "loser": -0.001 + rng.normal(scale=0.0002, size=n),
        }
    )
    res = hansens_spa_test(bench, strat, n_bootstrap=300, significance=0.05)
    assert res.best_strategy_idx == 0
    assert res.reject_null is True

