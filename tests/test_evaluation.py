from __future__ import annotations

import numpy as np
import pandas as pd

from src.evaluation.signal_evaluator import SignalEvaluator


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
    log_returns = fwd.unstack().shift(horizon + 1).stack(dropna=False)

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
    log_returns = fwd.unstack().shift(horizon + 1).stack(dropna=False)

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
    log_returns = fwd.unstack().shift(horizon + 1).stack(dropna=False)

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)

    # After applying shift(-(h+1)), the last (h+1) dates per asset are NaN.
    expected = (len(dates) - (horizon + 1)) * len(assets)
    assert m.n_observations == expected

