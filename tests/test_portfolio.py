import math

import pandas as pd
import pytest

from src.portfolio.costs import CostModel


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

