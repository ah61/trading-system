"""Tests for walk-forward backtest engines (synthetic data only).

5.7 contract: data is ``Dict[catalogue_variable_name, pd.Series]`` and
``signal.instruments`` enumerates the tradeable instruments.
"""

from __future__ import annotations

import math
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")

from matplotlib import pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import pandas as pd
import pytest

from src.backtest import cpcv as cpcv_mod
from src.backtest.cpcv import CPCVEngine
from src.backtest.engine import BacktestEngine
from src.backtest.results import BacktestResult, CPCVResult
from src.backtest.tearsheet import TearsheetGenerator
from src.backtest.walk_forward import (
    WalkForwardEngine,
    expanding_fold_train_bar_counts,
    rolling_fold_train_bar_counts,
)
from src.portfolio.costs import CostModel
from src.signals.base import Signal
from src.signals.rates.trend import RatesTrendSignal


# Names used throughout the suite to stand in for catalogue variable names.
# Pre-5.7 these were column names inside a single "prices" DataFrame; under
# 5.7 they're top-level keys in the data dict, each pointing at a Series.
_INSTRUMENTS = ["AAA", "BBB"]


def _price_data(n: int = 40, seed: int = 0) -> Dict[str, pd.Series]:
    """Build synthetic price Series for two instruments.

    Returns a dict shaped to the 5.7 contract: keys are instrument names,
    values are 1-D price Series indexed by UTC datetime.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    a = 100.0 + np.cumsum(rng.normal(0.0, 0.5, size=n))
    b = 200.0 + np.cumsum(rng.normal(0.0, 0.3, size=n))
    return {
        "AAA": pd.Series(a, index=idx, name="AAA"),
        "BBB": pd.Series(b, index=idx, name="BBB"),
    }


def _default_cfg(**overrides: Any) -> dict:
    """Standard portfolio config used across the test suite."""
    cfg = {
        "asset_classes": {"AAA": "equity", "BBB": "equity"},
        "vol_window": 5,
        "sizing_method": "vol_target",
        "target_vol": 0.10,
        "gross_limit": 2.0,
        "net_limit": 1.0,
    }
    cfg.update(overrides)
    return cfg


class _MomentumSignal(Signal):
    name = "mom"
    asset_class = "equity"
    signal_type = "momentum"
    frequency = "daily"
    params: Dict[str, Any] = {}
    required_variables = ["AAA", "BBB"]
    instruments = ["AAA", "BBB"]
    evaluation_horizons = [1]

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        close = data["AAA"].astype(float)
        sig = close.pct_change(periods=2).fillna(0.0)
        return sig.clip(-1.0, 1.0)


def _calendar_from_data(data: Dict[str, pd.Series]) -> pd.DatetimeIndex:
    """Union of all Series indices, used to derive start/end dates in tests."""
    ix: pd.DatetimeIndex | None = None
    for s in data.values():
        ix = s.index if ix is None else ix.union(s.index)
    assert ix is not None
    return pd.DatetimeIndex(ix).sort_values()


def test_signal_compute_is_causal_under_future_perturbation() -> None:
    base = _price_data(30)
    data = {"TLT_CLOSE": base["AAA"].rename("TLT_CLOSE")}
    tlt = data["TLT_CLOSE"]
    t_cut = tlt.index[len(tlt.index) // 2]

    sig = RatesTrendSignal()
    output_full = sig.compute(data)

    perturbed = tlt.copy()
    perturbed.loc[tlt.index > t_cut] = perturbed.loc[tlt.index > t_cut] * 2.0
    output_perturbed = sig.compute({"TLT_CLOSE": perturbed})

    assert output_full.loc[:t_cut].equals(output_perturbed.loc[:t_cut])


def test_backtest_result_has_required_fields() -> None:
    data = _price_data(25)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data = _price_data(35, seed=1)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    gross_cum = res.gross_returns.fillna(0).sum()
    net_cum = res.net_returns.fillna(0).sum()
    assert net_cum <= gross_cum + 1e-10
    assert bool((res.net_returns < res.gross_returns).any())


def test_walk_forward_expanding_window_grows() -> None:
    n = 45
    train_w, test_w = 12, 6
    data = _price_data(n, seed=2)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data = _price_data(n, seed=3)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data = _price_data(40, seed=11)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data = _price_data(45, seed=12)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data2 = _price_data(40, seed=13)
    cal2 = _calendar_from_data(data2)
    start2 = cal2[0].date()
    end2 = cal2[-1].date()
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
    data = _price_data(38, seed=14)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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
    data = _price_data(42, seed=15)
    cal = _calendar_from_data(data)
    start = cal[0].date()
    end = cal[-1].date()
    cfg = _default_cfg()
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


def _synthetic_backtest_result() -> BacktestResult:
    idx = pd.date_range("2021-01-01", periods=300, freq="D", tz="UTC")
    base = pd.Series(0.0005 + 0.002 * np.sin(np.arange(len(idx)) / 12.0), index=idx)
    costs = pd.Series(0.00005 + 0.00002 * (np.arange(len(idx)) % 5 == 0), index=idx)
    gross_returns = base.astype(float)
    net_returns = (base - costs).astype(float)
    trades = pd.DataFrame(
        {
            "AAA": 0.01 * np.sin(np.arange(len(idx)) / 7.0),
            "BBB": 0.01 * np.cos(np.arange(len(idx)) / 9.0),
        },
        index=idx,
    )
    return BacktestResult(
        gross_returns=gross_returns,
        net_returns=net_returns,
        annualised_return=0.12,
        annualised_vol=0.18,
        sharpe_ratio=1.25,
        sortino_ratio=1.75,
        max_drawdown=-0.08,
        max_drawdown_duration=42,
        calmar_ratio=1.5,
        hit_rate=0.54,
        avg_trade_return=0.0007,
        total_cost_bps=150.0,
        turnover_annual=4.2,
        trades=trades,
    )


def test_tearsheet_returns_figure() -> None:
    result = _synthetic_backtest_result()
    fig = TearsheetGenerator().generate(result)

    assert isinstance(fig, Figure)
    plt.close(fig)


def test_tearsheet_saves_file(tmp_path: Path) -> None:
    result = _synthetic_backtest_result()
    output_path = tmp_path / "tearsheet.png"

    fig = TearsheetGenerator().generate(result, output_path=output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    plt.close(fig)


def test_engine_rejects_legacy_portfolio_config_instruments_key() -> None:
    data = _price_data(50)
    cfg = _default_cfg(instruments=list(_INSTRUMENTS))
    with pytest.raises(ValueError, match="portfolio_config\\['instruments'\\] is no longer accepted"):
        BacktestEngine().run(
            signals=[_MomentumSignal()],
            data=data,
            portfolio_config=cfg,
            cost_model=CostModel(spread_bps={"AAA": 1.0, "BBB": 1.0}),
            start_date=data["AAA"].index[0].date(),
            end_date=data["AAA"].index[-1].date(),
            method="expanding",
            train_window=10,
            test_window=5,
        )


def test_engine_reads_signal_instruments_for_price_panel() -> None:
    data = _price_data(40)
    cal = _calendar_from_data(data)
    cfg = _default_cfg()
    result = BacktestEngine().run(
        signals=[_MomentumSignal()],
        data=data,
        portfolio_config=cfg,
        cost_model=CostModel(spread_bps={"AAA": 1.0, "BBB": 1.0}),
        start_date=cal[0].date(),
        end_date=cal[-1].date(),
        method="expanding",
        train_window=10,
        test_window=5,
    )
    assert len(result.net_returns) == 5


def test_summary_dict_has_required_keys() -> None:
    result = _synthetic_backtest_result()

    assert set(result.summary_dict()) == {
        "annualised_return",
        "annualised_vol",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown",
        "max_drawdown_duration",
        "calmar_ratio",
        "hit_rate",
        "avg_trade_return",
        "total_cost_bps",
        "turnover_annual",
    }
