"""Tests for walk-forward backtest engines (synthetic data only)."""

from __future__ import annotations

import math
from dataclasses import fields
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from src.backtest import cpcv as cpcv_mod
from src.backtest.cpcv import CPCVEngine
from src.backtest.engine import BacktestEngine
from src.backtest.results import BacktestResult, CPCVResult
from src.backtest.walk_forward import (
    WalkForwardEngine,
    expanding_fold_train_bar_counts,
    rolling_fold_train_bar_counts,
)
from src.portfolio.costs import CostModel
from src.signals.base import Signal


def _prices(n: int = 40, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    a = 100.0 + np.cumsum(rng.normal(0.0, 0.5, size=n))
    b = 200.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    return pd.DataFrame({"AAA": a, "BBB": b}, index=idx)


class _SpyNoLookaheadSignal(Signal):
    """Records the latest timestamp seen in ``prices`` on every ``compute`` call."""

    name = "spy_no_lookahead"
    asset_class = "equity"
    signal_type = "spy"
    frequency = "daily"
    params: Dict[str, Any] = {}
    required_data = ["prices"]

    call_max_timestamps: List[pd.Timestamp] = []

    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        px = data["prices"]
        mx = pd.Timestamp(px.index.max())
        if mx.tzinfo is None:
            mx = mx.tz_localize("UTC")
        else:
            mx = mx.tz_convert("UTC")
        self.call_max_timestamps.append(mx)
        close = px["AAA"].astype(float)
        return close.pct_change().fillna(0.0)


class _MomentumSignal(Signal):
    name = "mom"
    asset_class = "equity"
    signal_type = "momentum"
    frequency = "daily"
    params: Dict[str, Any] = {}
    required_data = ["prices"]

    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        close = data["prices"]["AAA"].astype(float)
        sig = close.pct_change(periods=2).fillna(0.0)
        return sig.clip(-1.0, 1.0)


@pytest.fixture(autouse=True)
def _reset_spy() -> None:
    _SpyNoLookaheadSignal.call_max_timestamps.clear()
    yield
    _SpyNoLookaheadSignal.call_max_timestamps.clear()


def test_no_lookahead_enforced() -> None:
    prices = _prices(30)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    spy = _SpyNoLookaheadSignal()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
        "sizing_method": "vol_target",
        "target_vol": 0.10,
        "gross_limit": 2.0,
        "net_limit": 1.0,
    }
    engine = BacktestEngine()
    engine.run(
        signals=[spy],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        method="expanding",
        train_window=10,
        test_window=5,
    )
    cal = pd.DatetimeIndex(prices.index).sort_values()
    history = cal[cal <= cal[-1]]
    assert len(spy.call_max_timestamps) == len(history)
    for i, mx in enumerate(spy.call_max_timestamps):
        assert mx <= history[i]


def test_backtest_result_has_required_fields() -> None:
    prices = _prices(25)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    res = BacktestEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        method="expanding",
        train_window=10,
        test_window=5,
    )
    names = {f.name for f in fields(BacktestResult)}
    for n in names:
        assert hasattr(res, n)


def test_net_returns_less_than_gross() -> None:
    prices = _prices(35, seed=1)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    model = CostModel(
        spread_bps={"AAA": 50.0, "BBB": 50.0},
        market_impact_model="linear",
        impact_coefficient=1.0,
    )
    res = BacktestEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=model,
        start_date=start,
        end_date=end,
        method="expanding",
        train_window=15,
        test_window=10,
    )
    assert len(res.gross_returns) == len(res.net_returns)
    assert bool((res.net_returns <= res.gross_returns + 1e-12).all())
    assert bool((res.net_returns < res.gross_returns).any())


def test_walk_forward_expanding_window_grows() -> None:
    n = 45
    train_w, test_w = 12, 6
    prices = _prices(n, seed=2)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    wf = WalkForwardEngine()
    results = wf.run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        mode="expanding",
        train_window=train_w,
        test_window=test_w,
    )
    expected_train = expanding_fold_train_bar_counts(n, train_w, test_w)
    assert len(results) == len(expected_train)
    for i in range(1, len(expected_train)):
        assert expected_train[i - 1] < expected_train[i]


def test_walk_forward_rolling_windows_fixed() -> None:
    n = 50
    train_w, test_w = 14, 7
    prices = _prices(n, seed=3)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    wf = WalkForwardEngine()
    results = wf.run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        mode="rolling",
        train_window=train_w,
        test_window=test_w,
    )
    n_folds = len(results)
    expected = rolling_fold_train_bar_counts(n_folds, train_w)
    assert all(t == train_w for t in expected)
    assert n_folds >= 2


def test_cpcv_result_has_required_fields() -> None:
    prices = _prices(40, seed=11)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    res = CPCVEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        n_groups=4,
        k_test=2,
    )
    names = {f.name for f in fields(CPCVResult)}
    for n in names:
        assert hasattr(res, n)


def test_cpcv_n_paths_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    prices = _prices(45, seed=12)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    model = CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0})
    cap = int(cpcv_mod._MAX_COMBINATIONS)
    n_groups, k_test = 4, 2
    res = CPCVEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=model,
        start_date=start,
        end_date=end,
        n_groups=n_groups,
        k_test=k_test,
    )
    assert res.n_paths == min(math.comb(n_groups, k_test), cap)

    monkeypatch.setattr(cpcv_mod, "_MAX_COMBINATIONS", 4)
    prices2 = _prices(40, seed=13)
    data2 = {"prices": prices2}
    start2 = prices2.index[0].date()
    end2 = prices2.index[-1].date()
    res2 = CPCVEngine().run(
        signals=[_MomentumSignal()],
        data=data2,
        portfolio_config=cfg,
        cost_model=model,
        start_date=start2,
        end_date=end2,
        n_groups=5,
        k_test=2,
    )
    assert res2.n_paths == min(math.comb(5, 2), 4)


def test_cpcv_oos_sharpe_is_series() -> None:
    prices = _prices(38, seed=14)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    res = CPCVEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        n_groups=4,
        k_test=2,
    )
    assert isinstance(res.oos_sharpe_distribution, pd.Series)


def test_cpcv_pbo_between_zero_and_one() -> None:
    prices = _prices(42, seed=15)
    data = {"prices": prices}
    start = prices.index[0].date()
    end = prices.index[-1].date()
    cfg = {
        "prices_key": "prices",
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
    }
    res = CPCVEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 0.0, "BBB": 0.0}),
        start_date=start,
        end_date=end,
        n_groups=4,
        k_test=2,
    )
    assert 0.0 <= res.pbo <= 1.0
