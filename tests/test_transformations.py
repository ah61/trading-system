"""Tests for pure transformation functions."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.data import transformations as T


def test_rolling_zscore_matches_manual_calculation() -> None:
    idx = pd.date_range("2020-01-01", periods=30, freq="D", tz="UTC")
    s = pd.Series(np.linspace(1.0, 30.0, len(idx)), index=idx)
    window = 10
    out = T.rolling_zscore(s, window=window)
    mean = s.rolling(window=window, min_periods=window).mean()
    std = s.rolling(window=window, min_periods=window).std()
    expected = (s - mean) / std
    pd.testing.assert_series_equal(out, expected, atol=1e-10, rtol=0)


def test_rolling_zscore_preserves_warmup_nans() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D", tz="UTC")
    s = pd.Series(np.arange(20, dtype=float), index=idx)
    out = T.rolling_zscore(s, window=10)
    assert out.iloc[:9].isna().all()
    assert out.iloc[9:].notna().any()


def test_difference_subtracts_pointwise() -> None:
    lhs = pd.Series([5.0, 6.0, 7.0])
    rhs = pd.Series([1.0, 2.0, 3.0])
    out = T.difference(lhs, rhs)
    pd.testing.assert_series_equal(out, pd.Series([4.0, 4.0, 4.0]))


def test_difference_aligns_on_index() -> None:
    lhs = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-04"]))
    rhs = pd.Series([10.0, 20.0], index=pd.to_datetime(["2020-01-01", "2020-01-03"]))
    out = T.difference(lhs, rhs)
    assert len(out) == 4
    assert out.loc["2020-01-01"] == -9.0
    assert pd.isna(out.loc["2020-01-02"])
    assert pd.isna(out.loc["2020-01-03"])
    assert pd.isna(out.loc["2020-01-04"])


def test_yoy_pct_change_monthly_uses_12_periods() -> None:
    idx = pd.date_range("2018-01-31", periods=24, freq="ME", tz="UTC")
    s = pd.Series(np.arange(24, dtype=float) + 100.0, index=idx)
    out = T.yoy_pct_change(s, frequency="monthly")
    pd.testing.assert_series_equal(out, s.pct_change(periods=12), check_names=False)


def test_yoy_pct_change_daily_uses_252_periods() -> None:
    idx = pd.bdate_range("2018-01-01", periods=300, tz="UTC")
    s = pd.Series(np.arange(300, dtype=float) + 50.0, index=idx)
    out = T.yoy_pct_change(s, frequency="daily")
    pd.testing.assert_series_equal(out, s.pct_change(periods=252), check_names=False)


def test_yoy_pct_change_quarterly_uses_4_periods() -> None:
    idx = pd.date_range("2018-03-31", periods=12, freq="QE", tz="UTC")
    s = pd.Series(np.arange(12, dtype=float) + 10.0, index=idx)
    out = T.yoy_pct_change(s, frequency="quarterly")
    pd.testing.assert_series_equal(out, s.pct_change(periods=4), check_names=False)


def test_yoy_pct_change_unsupported_frequency_raises() -> None:
    s = pd.Series([1.0, 2.0, 3.0])
    with pytest.raises(ValueError, match="Unsupported frequency"):
        T.yoy_pct_change(s, frequency="hourly")


def test_log_return_default_window_1() -> None:
    prices = pd.Series([100.0, 102.0, 101.0, 105.0])
    out = T.log_return(prices)
    expected = np.log(prices / prices.shift(1))
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_log_return_custom_window() -> None:
    prices = pd.Series([100.0, 102.0, 104.0, 108.0, 110.0])
    out = T.log_return(prices, window=5)
    expected = np.log(prices / prices.shift(5))
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_rolling_vol_annualised_daily_uses_sqrt_252() -> None:
    idx = pd.bdate_range("2020-01-01", periods=100, tz="UTC")
    rets = pd.Series(np.random.default_rng(0).normal(0, 0.01, len(idx)), index=idx)
    out = T.rolling_vol(rets, window=20, annualised=True, frequency="daily")
    raw = rets.rolling(window=20, min_periods=20).std()
    expected = raw * math.sqrt(252)
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_rolling_vol_annualised_monthly_uses_sqrt_12() -> None:
    idx = pd.date_range("2020-01-31", periods=24, freq="ME", tz="UTC")
    rets = pd.Series(np.random.default_rng(1).normal(0, 0.02, len(idx)), index=idx)
    out = T.rolling_vol(rets, window=6, annualised=True, frequency="monthly")
    raw = rets.rolling(window=6, min_periods=6).std()
    expected = raw * math.sqrt(12)
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_rolling_vol_not_annualised_no_multiplier() -> None:
    idx = pd.bdate_range("2020-01-01", periods=50, tz="UTC")
    rets = pd.Series(np.random.default_rng(2).normal(0, 0.01, len(idx)), index=idx)
    out = T.rolling_vol(rets, window=10, annualised=False, frequency="daily")
    expected = rets.rolling(window=10, min_periods=10).std()
    pd.testing.assert_series_equal(out, expected, check_names=False)


def test_rolling_vol_unsupported_frequency_raises() -> None:
    s = pd.Series([0.01, 0.02, -0.01])
    with pytest.raises(ValueError, match="Unsupported frequency"):
        T.rolling_vol(s, window=2, annualised=True, frequency="hourly")
