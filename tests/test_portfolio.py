import math

import pandas as pd
import pytest

from src.portfolio.costs import CostModel
from src.portfolio.constructor import PortfolioConstructor
from src.portfolio.sizing import PositionSizer


def test_estimate_cost_linear_impact() -> None:
    model = CostModel(spread_bps={"ABC": 2.0}, market_impact_model="linear", impact_coefficient=10.0)
    cost = model.estimate_cost("ABC", trade_size=10.0, adv=100.0)
    assert cost == pytest.approx(2.0 + 10.0 * (10.0 / 100.0))


def test_estimate_cost_sqrt_impact() -> None:
    model = CostModel(spread_bps={"ABC": 2.0}, market_impact_model="sqrt", impact_coefficient=10.0)
    cost = model.estimate_cost("ABC", trade_size=10.0, adv=100.0)
    assert cost == pytest.approx(2.0 + 10.0 * math.sqrt(10.0 / 100.0))


def test_apply_costs_reduces_returns() -> None:
    idx = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"])
    gross = pd.Series([0.01, 0.02, 0.03], index=idx)
    trades = pd.DataFrame({"ABC": [0.0, 0.2, 0.0]}, index=idx)

    model = CostModel(spread_bps={"ABC": 5.0}, market_impact_model="linear", impact_coefficient=10.0)
    net = model.apply_costs(gross, trades)

    assert net.loc[idx[1]] < gross.loc[idx[1]]
    assert net.loc[idx[0]] == gross.loc[idx[0]]
    assert net.loc[idx[2]] == gross.loc[idx[2]]


def test_zero_trades_no_cost() -> None:
    model = CostModel(spread_bps={"ABC": 5.0}, market_impact_model="linear", impact_coefficient=10.0)
    assert model.estimate_cost("ABC", trade_size=0.0, adv=100.0) == 0.0
    assert model.estimate_cost("ABC", trade_size=0.0, adv=1.0) == 0.0


def test_spread_cost_scales_with_bps() -> None:
    trade_size = 10.0
    adv = 100.0

    low = CostModel(spread_bps={"ABC": 1.0}, market_impact_model="linear", impact_coefficient=10.0)
    high = CostModel(spread_bps={"ABC": 10.0}, market_impact_model="linear", impact_coefficient=10.0)

    assert high.estimate_cost("ABC", trade_size=trade_size, adv=adv) > low.estimate_cost(
        "ABC", trade_size=trade_size, adv=adv
    )


def _synthetic_prices(index: pd.DatetimeIndex) -> pd.DataFrame:
    a = [100.0 + 0.2 * i + (0.5 if i % 2 == 0 else -0.5) for i in range(len(index))]
    b = [200.0 + 0.1 * i + (0.2 if i % 3 == 0 else -0.2) for i in range(len(index))]
    return pd.DataFrame({"A": a, "B": b}, index=index)


def test_vol_target_weights_scale_with_signal() -> None:
    idx = pd.date_range("2020-01-01", periods=30, freq="D")
    prices = _synthetic_prices(idx)
    sizer = PositionSizer()

    signals_low = pd.DataFrame({"A": [0.2] * len(idx), "B": [0.2] * len(idx)}, index=idx)
    signals_high = pd.DataFrame({"A": [0.8] * len(idx), "B": [0.2] * len(idx)}, index=idx)

    w_low = sizer.volatility_target(signals_low, prices, target_vol=0.10, vol_window=5)
    w_high = sizer.volatility_target(signals_high, prices, target_vol=0.10, vol_window=5)

    assert abs(float(w_high.iloc[-1]["A"])) > abs(float(w_low.iloc[-1]["A"]))


def test_vol_target_output_shape_matches_input() -> None:
    idx = pd.date_range("2020-01-01", periods=25, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [0.3] * len(idx), "B": [-0.4] * len(idx)}, index=idx)

    sizer = PositionSizer()
    w = sizer.volatility_target(signals, prices, target_vol=0.10, vol_window=5)

    assert w.shape == signals.shape
    assert list(w.columns) == list(signals.columns)
    assert w.index.equals(signals.index)


def test_risk_parity_equal_risk_per_asset_class() -> None:
    idx = pd.date_range("2020-01-01", periods=40, freq="D")
    prices = pd.DataFrame(
        {
            "EQ1": [100.0 + 0.2 * i + (0.4 if i % 2 == 0 else -0.4) for i in range(len(idx))],
            "EQ2": [120.0 + 0.1 * i + (0.3 if i % 3 == 0 else -0.3) for i in range(len(idx))],
            "FX1": [80.0 + 0.15 * i + (0.2 if i % 4 == 0 else -0.2) for i in range(len(idx))],
            "FX2": [90.0 + 0.05 * i + (0.25 if i % 5 == 0 else -0.25) for i in range(len(idx))],
        },
        index=idx,
    )
    signals = pd.DataFrame(1.0, index=idx, columns=list(prices.columns))
    asset_classes = {"EQ1": "equity", "EQ2": "equity", "FX1": "fx", "FX2": "fx"}

    sizer = PositionSizer()
    w = sizer.risk_parity(signals, prices, asset_classes=asset_classes, target_vol=0.10, vol_window=5)
    last = w.iloc[-1]

    equity_weight = abs(float(last["EQ1"])) + abs(float(last["EQ2"]))
    fx_weight = abs(float(last["FX1"])) + abs(float(last["FX2"]))
    assert equity_weight == pytest.approx(fx_weight, rel=1e-6, abs=1e-8)


def test_vol_target_respects_sign() -> None:
    idx = pd.date_range("2020-01-01", periods=30, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [-0.7] * len(idx), "B": [0.6] * len(idx)}, index=idx)

    sizer = PositionSizer()
    w = sizer.volatility_target(signals, prices, target_vol=0.10, vol_window=5)

    assert float(w.iloc[-1]["A"]) < 0.0
    assert float(w.iloc[-1]["B"]) > 0.0


def test_construct_returns_weights_and_trades() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [0.6] * len(idx), "B": [-0.4] * len(idx)}, index=idx)
    asset_classes = {"A": "equity", "B": "equity"}

    pc = PortfolioConstructor(position_sizer=PositionSizer(vol_window=5))
    weights, trades = pc.construct(
        signals=signals,
        prices=prices,
        asset_classes=asset_classes,
        sizing_method="vol_target",
        target_vol=0.10,
    )

    assert isinstance(weights, pd.DataFrame)
    assert isinstance(trades, pd.DataFrame)
    assert weights.shape == signals.shape
    assert trades.shape == signals.shape
    assert list(weights.columns) == list(signals.columns)
    assert weights.index.equals(signals.index)
    assert trades.index.equals(signals.index)


def test_gross_limit_enforced() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [1.0] * len(idx), "B": [1.0] * len(idx)}, index=idx)
    asset_classes = {"A": "equity", "B": "equity"}

    pc = PortfolioConstructor(position_sizer=PositionSizer(vol_window=5))
    weights, _ = pc.construct(
        signals=signals,
        prices=prices,
        asset_classes=asset_classes,
        sizing_method="vol_target",
        target_vol=0.10,
        gross_limit=1.0,
        net_limit=10.0,
    )

    gross = weights.abs().sum(axis=1)
    assert bool((gross <= 1.0 + 1e-12).all())


def test_net_limit_enforced() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [1.0] * len(idx), "B": [1.0] * len(idx)}, index=idx)
    asset_classes = {"A": "equity", "B": "equity"}

    pc = PortfolioConstructor(position_sizer=PositionSizer(vol_window=5))
    weights, _ = pc.construct(
        signals=signals,
        prices=prices,
        asset_classes=asset_classes,
        sizing_method="vol_target",
        target_vol=0.10,
        gross_limit=10.0,
        net_limit=0.1,
    )

    net = weights.sum(axis=1).abs()
    assert bool((net <= 0.1 + 1e-10).all())


def test_trades_are_weight_differences() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [0.8] * len(idx), "B": [-0.2] * len(idx)}, index=idx)
    asset_classes = {"A": "equity", "B": "equity"}

    pc = PortfolioConstructor(position_sizer=PositionSizer(vol_window=5))
    weights, trades = pc.construct(
        signals=signals,
        prices=prices,
        asset_classes=asset_classes,
        sizing_method="vol_target",
        target_vol=0.10,
    )

    diff = weights.diff().fillna(0.0)
    diff.iloc[0] = weights.iloc[0]
    pd.testing.assert_frame_equal(trades, diff)


def test_enforce_net_limit_matches_row_reference() -> None:
    weights = pd.DataFrame(
        [
            [0.05, 0.05, -0.02, 0.0],       # net 0.08, within limit
            [0.07, 0.05, -0.02, 0.0],       # net 0.10, exactly at limit
            [0.15, 0.05, -0.05, 0.0],       # net 0.15, single-column reduction
            [0.20, 0.10, 0.05, -0.05],     # net 0.30, multi-column reduction
            [0.05, 0.03, -0.50, 0.0],       # net -0.42, negative direction
            [0.0, 0.0, 0.0, 0.0],           # all zeros
            [-0.20, -0.10, 0.05, 0.0],      # net -0.25, negative reduction
        ],
        columns=["a", "b", "c", "d"],
    )
    net_limit = 0.10

    ref = weights.copy()
    for dt in ref.index:
        ref.loc[dt] = PortfolioConstructor._enforce_net_limit_row(
            ref.loc[dt], net_limit=net_limit,
        )

    out = PortfolioConstructor._enforce_net_limit(weights, net_limit=net_limit)

    pd.testing.assert_frame_equal(out, ref, atol=1e-12, rtol=0)
    assert (out.sum(axis=1).abs() <= net_limit + 1e-12).all()
    pd.testing.assert_series_equal(out.iloc[0], weights.iloc[0])
    pd.testing.assert_series_equal(out.iloc[1], weights.iloc[1])
    pd.testing.assert_series_equal(out.iloc[5], weights.iloc[5])


def test_first_row_trades_equal_weights() -> None:
    idx = pd.date_range("2020-01-01", periods=20, freq="D")
    prices = _synthetic_prices(idx)
    signals = pd.DataFrame({"A": [0.3] * len(idx), "B": [0.3] * len(idx)}, index=idx)
    asset_classes = {"A": "equity", "B": "equity"}

    pc = PortfolioConstructor(position_sizer=PositionSizer(vol_window=5))
    weights, trades = pc.construct(
        signals=signals,
        prices=prices,
        asset_classes=asset_classes,
        sizing_method="vol_target",
        target_vol=0.10,
    )

    pd.testing.assert_series_equal(trades.iloc[0], weights.iloc[0])

