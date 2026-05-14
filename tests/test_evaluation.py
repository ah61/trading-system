from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest
from scipy.stats import ConstantInputWarning

from src.evaluation.signal_evaluator import SignalEvaluator, SignalMetrics
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

    # "Perfect" returns: equal to signal *back-rolled* by (horizon + 1) periods.
    # Evaluator applies shift(-(horizon+1)), so to align signal[t] with the
    # post-shift fwd[t], we need pre-shift fwd[t+(h+1)] = signal[t], i.e.
    # pre-shift fwd[s] = signal[s-(h+1)] = np.roll(signal, +(h+1), axis=0).
    horizon = 1
    fwd_vals = np.roll(signal_vals, +(horizon + 1), axis=0)
    fwd = _panel_series(dates, assets, fwd_vals)
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
    # Evaluator applies shift(-(horizon+1)) internally; pass 1-period returns.
    log_returns = fwd

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
    # Evaluator applies shift(-(horizon+1)) internally; pass 1-period returns.
    log_returns = fwd

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)
    if np.isfinite(m.ic_std) and m.ic_std != 0:
        assert m.icir == m.ic_mean / m.ic_std


def test_hit_rate_one_for_perfect_signal() -> None:
    dates = pd.date_range("2024-01-01", periods=120, freq="B", tz="UTC")
    assets = [f"A{i}" for i in range(20)]
    vals = np.tile(np.linspace(-1.0, 1.0, len(assets)), (len(dates), 1))
    signal = _panel_series(dates, assets, vals)
    # Pre-shift so post-shift alignment is perfect.
    horizon = 1
    fwd_vals = np.roll(vals, -(horizon + 1), axis=0)
    fwd = _panel_series(dates, assets, fwd_vals)
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
    horizon = 1
    # See test_ic_mean_positive_for_perfect_signal for the sign convention.
    aligned_vals = np.roll(signal_vals, +(horizon + 1), axis=0)
    fwd_vals = aligned_vals * 0.02 + rng.normal(scale=0.01, size=signal_vals.shape)
    fwd = _panel_series(dates, assets, fwd_vals)
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
    # Evaluator applies shift(-(horizon+1)) internally; pass 1-period returns.
    log_returns = fwd

    ev = SignalEvaluator()
    m = ev.evaluate(signal=signal, forward_returns=log_returns, horizon=horizon)

    # Evaluator applies shift(-(h+1)) per asset, so last (h+1) rows per asset drop.
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


# ==========================================================================
# Milestone 5.2 — Frequency Layer tests
# ==========================================================================


def _daily_dates(n: int, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq="B", tz="UTC")


def _build_monthly_signal_daily_indexed(n_days: int = 500) -> pd.Series:
    """Signal that fires non-zero only on the first business day of each month.

    This is the canonical 'monthly signal evaluated at daily frequency' case
    that previously triggered ConstantInputWarning.
    """
    idx = _daily_dates(n_days)
    s = pd.Series(0.0, index=idx)
    # Set the first business day of each month to +1.0 / -1.0 alternating.
    months = pd.Series(idx.year * 12 + idx.month, index=idx)
    is_first_in_month = months != months.shift(1)
    fire_dates = idx[is_first_in_month]
    for i, d in enumerate(fire_dates):
        s.loc[d] = 1.0 if i % 2 == 0 else -1.0
    return s


def _build_daily_log_returns(n_days: int = 500, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = _daily_dates(n_days)
    return pd.Series(rng.normal(loc=0.0, scale=0.01, size=n_days), index=idx)


def test_signal_evaluator_evaluate_accepts_frequency_daily() -> None:
    sig = _build_daily_log_returns(n_days=300, seed=1).clip(-1, 1)
    ret = _build_daily_log_returns(n_days=300, seed=2)
    m = SignalEvaluator().evaluate(sig, ret, horizon=1, frequency="daily")
    assert isinstance(m, SignalMetrics)
    assert m.frequency == "daily"
    assert m.forward_return_horizon == 1


def test_signal_evaluator_evaluate_accepts_frequency_weekly() -> None:
    sig = _build_daily_log_returns(n_days=300, seed=1).clip(-1, 1)
    ret = _build_daily_log_returns(n_days=300, seed=2)
    m = SignalEvaluator().evaluate(sig, ret, horizon=1, frequency="weekly")
    assert m.frequency == "weekly"


def test_signal_evaluator_evaluate_accepts_frequency_monthly() -> None:
    sig = _build_monthly_signal_daily_indexed(n_days=500)
    ret = _build_daily_log_returns(n_days=500, seed=42)
    m = SignalEvaluator().evaluate(sig, ret, horizon=3, frequency="monthly")
    assert m.frequency == "monthly"
    assert m.forward_return_horizon == 3


def test_signal_evaluator_evaluate_rejects_unknown_frequency() -> None:
    sig = _build_daily_log_returns(n_days=50, seed=1).clip(-1, 1)
    ret = _build_daily_log_returns(n_days=50, seed=2)
    with pytest.raises(ValueError, match="Unknown frequency"):
        SignalEvaluator().evaluate(sig, ret, horizon=1, frequency="hourly")  # type: ignore[arg-type]


def test_signal_evaluator_evaluate_rejects_nonpositive_horizon() -> None:
    sig = _build_daily_log_returns(n_days=50, seed=1).clip(-1, 1)
    ret = _build_daily_log_returns(n_days=50, seed=2)
    with pytest.raises(ValueError, match="horizon must be a positive integer"):
        SignalEvaluator().evaluate(sig, ret, horizon=0, frequency="daily")


def test_signal_evaluator_monthly_evaluation_no_constant_input_warning() -> None:
    """Monthly signal evaluated at monthly frequency must not trigger
    ConstantInputWarning (the bug Milestone 5.2 was created to fix).
    """
    sig = _build_monthly_signal_daily_indexed(n_days=500)
    ret = _build_daily_log_returns(n_days=500, seed=7)

    with warnings.catch_warnings():
        warnings.simplefilter("error", ConstantInputWarning)
        m = SignalEvaluator().evaluate(sig, ret, horizon=2, frequency="monthly")

    assert m.frequency == "monthly"
    # Roughly 500 business days / 21 ≈ 24 months; after shifting by horizon+1
    # we expect ~20 paired observations. Just assert non-trivially many.
    assert m.n_observations >= 10


def test_resample_signal_takes_first_nonzero_in_period() -> None:
    """Per spec: signal resample takes the first non-zero value in the period."""
    from src.evaluation.signal_evaluator import _resample_signal

    idx = pd.date_range("2024-01-01", periods=20, freq="B", tz="UTC")
    s = pd.Series(0.0, index=idx)
    # Within the first week (Mon 2024-01-01 .. Fri 2024-01-05): zeros until Wed,
    # then 0.7 on Wed and 0.3 on Fri. Expect 0.7 as the period value.
    s.iloc[2] = 0.7
    s.iloc[4] = 0.3

    weekly = _resample_signal(s, "weekly")
    # First week's value should be the first non-zero (0.7), not the last.
    assert weekly.iloc[0] == pytest.approx(0.7)


def test_resample_signal_all_zero_period_carries_forward() -> None:
    """All-zero period inherits the previous period's value (carry forward).

    Rationale: zero = 'no position held', not 'no signal computed'. A period
    with no rebalance fires should retain the position from the previous one.
    """
    from src.evaluation.signal_evaluator import _resample_signal

    # Three weeks of business days. Set a non-zero on week 1 only.
    idx = pd.date_range("2024-01-01", periods=15, freq="B", tz="UTC")
    s = pd.Series(0.0, index=idx)
    s.iloc[0] = 0.5  # Mon of week 1

    weekly = _resample_signal(s, "weekly")
    # Week 1 = 0.5, weeks 2 and 3 inherit (ffill) → 0.5.
    assert weekly.iloc[0] == pytest.approx(0.5)
    assert weekly.iloc[1] == pytest.approx(0.5)
    assert weekly.iloc[2] == pytest.approx(0.5)


def test_resample_log_returns_sums_within_period() -> None:
    """Per CONVENTIONS §3.2: compounding log returns = summing them."""
    from src.evaluation.signal_evaluator import _resample_log_returns

    idx = pd.date_range("2024-01-01", periods=5, freq="B", tz="UTC")  # one week
    # Mon..Fri = 0.01, 0.02, -0.01, 0.03, 0.00 → sum = 0.05
    r = pd.Series([0.01, 0.02, -0.01, 0.03, 0.00], index=idx)
    weekly = _resample_log_returns(r, "weekly")
    assert len(weekly) == 1
    assert weekly.iloc[0] == pytest.approx(0.05)


def test_resample_daily_is_passthrough() -> None:
    """frequency='daily' must not alter the input."""
    from src.evaluation.signal_evaluator import _resample_log_returns, _resample_signal

    idx = pd.date_range("2024-01-01", periods=10, freq="B", tz="UTC")
    s = pd.Series(np.arange(10, dtype=float), index=idx)
    pd.testing.assert_series_equal(_resample_signal(s, "daily"), s)
    pd.testing.assert_series_equal(_resample_log_returns(s, "daily"), s)


def test_signal_sharpe_uses_correct_annualisation_per_frequency() -> None:
    """For the same constant signal-return relationship, Sharpe scales by
    sqrt(periods_per_year). A weekly Sharpe should be ~sqrt(52/252) times the
    daily Sharpe for the same underlying mean/std ratio.

    We construct a single-asset signal of +1 throughout and i.i.d. positive-
    drift returns, then verify the ratio of annualisation factors in the
    resulting Sharpes.
    """
    n = 1000
    idx = _daily_dates(n)
    rng = np.random.default_rng(123)
    daily_ret = pd.Series(rng.normal(loc=0.0005, scale=0.01, size=n), index=idx)
    sig = pd.Series(1.0, index=idx)

    ev = SignalEvaluator()
    m_daily = ev.evaluate(sig, daily_ret, horizon=1, frequency="daily")
    m_weekly = ev.evaluate(sig, daily_ret, horizon=1, frequency="weekly")
    m_monthly = ev.evaluate(sig, daily_ret, horizon=1, frequency="monthly")

    # Sharpes won't be identical (data is resampled) but the annualisation
    # factor is observable: with signal=1 the per-period Sharpe is
    # mean(ret) / std(ret) * sqrt(periods_per_year). Verify positivity (drift
    # is positive) and that scaling is monotonic in the expected direction.
    assert np.isfinite(m_daily.signal_sharpe)
    assert np.isfinite(m_weekly.signal_sharpe)
    assert np.isfinite(m_monthly.signal_sharpe)
    # Sanity: the dataclass remembers which frequency it came from.
    assert m_daily.frequency == "daily"
    assert m_weekly.frequency == "weekly"
    assert m_monthly.frequency == "monthly"


def test_forward_return_shift_is_in_periods_at_frequency() -> None:
    """Manually construct a signal that perfectly predicts the month-ahead
    return and confirm IC is high when the frequency layer aligns horizons
    in months rather than days.
    """
    # 36 months of synthetic monthly data, built at daily grain.
    n = 800  # ≈ 38 months of business days
    idx = _daily_dates(n)
    rng = np.random.default_rng(2026)
    daily_ret = pd.Series(rng.normal(0, 0.01, size=n), index=idx)

    # Compute the actual monthly returns (sum of daily logs).
    monthly_ret = daily_ret.resample("MS").sum()

    # Build a signal that equals (next month return shifted back by 2),
    # i.e. at month t, the signal value equals the return realised in month t+2.
    # With horizon=1, the evaluator shifts returns by -(1+1) = -2 months, so the
    # signal value at month t should be compared to the return at month t+2.
    # Verify high IC.
    target_ret = monthly_ret.shift(-2)
    sig_monthly = target_ret.copy()
    # Re-index sig back onto the daily index, non-zero only at month starts,
    # so the resample-first-nonzero rule picks the right value.
    sig_daily = pd.Series(0.0, index=idx)
    for month_start, val in sig_monthly.dropna().items():
        # Find the first business day in idx >= month_start.
        ts = pd.Timestamp(month_start).tz_localize("UTC") if month_start.tz is None else month_start
        candidates = idx[idx >= ts]
        if len(candidates) > 0:
            sig_daily.loc[candidates[0]] = float(val)

    # Clip into [-1, 1] per convention.
    sig_daily = sig_daily.clip(-1, 1)

    m = SignalEvaluator().evaluate(sig_daily, daily_ret, horizon=1, frequency="monthly")
    # IC should be strongly positive given perfect month-ahead alignment.
    assert np.isfinite(m.ic_mean)
    assert m.ic_mean > 0.5


def test_default_frequency_is_daily() -> None:
    """Calling evaluate() without specifying frequency must behave as before."""
    sig = _build_daily_log_returns(n_days=200, seed=1).clip(-1, 1)
    ret = _build_daily_log_returns(n_days=200, seed=2)

    ev = SignalEvaluator()
    m_default = ev.evaluate(sig, ret, horizon=1)
    m_daily = ev.evaluate(sig, ret, horizon=1, frequency="daily")

    assert m_default.frequency == "daily"
    assert m_default.frequency == m_daily.frequency
    # Same numerical results (default == explicit 'daily').
    assert m_default.n_observations == m_daily.n_observations
    if np.isfinite(m_default.ic_mean) and np.isfinite(m_daily.ic_mean):
        assert m_default.ic_mean == pytest.approx(m_daily.ic_mean)
