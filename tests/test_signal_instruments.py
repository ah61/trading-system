from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
import pytest

from src.signals.base import Signal
from src.signals.equities.momentum import EquityMomentumSignal
from src.signals.fx.carry import FXCarrySignal
from src.signals.rates.trend import RatesTrendSignal


def _daily_idx(n: int = 10, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="B", tz="UTC")


def _panel(dates: pd.DatetimeIndex, assets: list[str], values: np.ndarray) -> pd.Series:
    idx = pd.MultiIndex.from_product([dates, assets], names=["date", "asset"])
    return pd.Series(values.reshape(len(dates) * len(assets)), index=idx, dtype=float)


# ---------------------------------------------------------------------------
# Base class validation
# ---------------------------------------------------------------------------


class _DefaultPackSignal(Signal):
    name = "pack"
    asset_class = "eq"
    signal_type = "t"
    frequency = "daily"
    params = {}
    required_variables = ["AAA", "BBB"]
    instruments = ["AAA", "BBB"]
    evaluation_horizons = [1]

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        return pd.Series(0.0, index=_daily_idx(5))


def test_signal_subclass_missing_instruments_attr_raises() -> None:
    with pytest.raises(TypeError, match="instruments"):

        class _MissingInstruments(Signal):
            name = "x"
            asset_class = "x"
            signal_type = "x"
            frequency = "daily"
            params = {}
            required_variables = ["A"]
            evaluation_horizons = [1]

            def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
                return data["A"]


def test_signal_subclass_missing_evaluation_horizons_raises() -> None:
    with pytest.raises(TypeError, match="evaluation_horizons"):

        class _MissingHorizons(Signal):
            name = "x"
            asset_class = "x"
            signal_type = "x"
            frequency = "daily"
            params = {}
            required_variables = ["A"]
            instruments = ["A"]

            def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
                return data["A"]


def test_signal_subclass_evaluation_horizons_empty_raises() -> None:
    with pytest.raises(TypeError, match="non-empty"):

        class _EmptyHorizons(Signal):
            name = "x"
            asset_class = "x"
            signal_type = "x"
            frequency = "daily"
            params = {}
            required_variables = ["A"]
            instruments = ["A"]
            evaluation_horizons: list[int] = []

            def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
                return data["A"]


def test_signal_subclass_evaluation_horizons_non_int_raises() -> None:
    with pytest.raises(TypeError, match="positive integers"):

        class _BadHorizons(Signal):
            name = "x"
            asset_class = "x"
            signal_type = "x"
            frequency = "daily"
            params = {}
            required_variables = ["A"]
            instruments = ["A"]
            evaluation_horizons = [0]

            def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
                return data["A"]


def test_default_instrument_prices_packs_to_multiindex() -> None:
    dates = _daily_idx(6)
    data = {
        "AAA": pd.Series(np.linspace(100, 101, len(dates)), index=dates),
        "BBB": pd.Series(np.linspace(200, 202, len(dates)), index=dates),
    }
    out = _DefaultPackSignal().instrument_prices(data)
    assert isinstance(out.index, pd.MultiIndex)
    assert out.index.names == ["date", "instrument"]
    assert set(out.index.get_level_values("instrument")) == {"AAA", "BBB"}


def test_default_instrument_prices_raises_on_missing_variable() -> None:
    dates = _daily_idx(4)
    with pytest.raises(KeyError, match="missing variables"):
        _DefaultPackSignal().instrument_prices(
            {"AAA": pd.Series(1.0, index=dates)}
        )


# ---------------------------------------------------------------------------
# Rates trend
# ---------------------------------------------------------------------------


def test_rates_trend_instruments_is_single_asset() -> None:
    sig = RatesTrendSignal()
    assert sig.instruments == [sig.params["variable"]]
    assert len(sig.instruments) == 1


def test_rates_trend_instrument_prices_returns_datetimeindex_series() -> None:
    sig = RatesTrendSignal()
    dates = _daily_idx(8)
    prices = pd.Series(np.linspace(90, 95, len(dates)), index=dates)
    out = sig.instrument_prices({sig.instruments[0]: prices})
    assert isinstance(out.index, pd.DatetimeIndex)
    assert not isinstance(out.index, pd.MultiIndex)
    pd.testing.assert_series_equal(out, prices.astype(float).sort_index())


def test_rates_trend_required_variables_and_instruments_identical() -> None:
    sig = RatesTrendSignal()
    assert sig.required_variables == sig.instruments


def test_rates_trend_evaluation_horizons() -> None:
    sig = RatesTrendSignal()
    assert sig.evaluation_horizons == [1, 5, 21, 63]


# ---------------------------------------------------------------------------
# FX carry
# ---------------------------------------------------------------------------


def test_fx_carry_instruments_unique_spot_variables() -> None:
    sig = FXCarrySignal()
    assert sig.instruments == sorted(
        {"EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDJPY", "USDCHF"}
    )


def test_fx_carry_required_variables_and_instruments_disjoint() -> None:
    sig = FXCarrySignal()
    assert set(sig.required_variables).isdisjoint(set(sig.instruments))
    assert sig.required_variables
    assert sig.instruments


def test_fx_carry_instrument_prices_inverts_usdxxx() -> None:
    sig = FXCarrySignal()
    dates = _daily_idx(5)
    data = {"USDJPY": pd.Series(150.0, index=dates)}
    out = sig.instrument_prices(data)
    jpy = out.xs("JPY/USD", level="pair")
    assert float(jpy.iloc[0]) == pytest.approx(1.0 / 150.0)


def test_fx_carry_instrument_prices_no_invert_xxxusd() -> None:
    sig = FXCarrySignal()
    dates = _daily_idx(5)
    data = {"EURUSD": pd.Series(1.10, index=dates)}
    out = sig.instrument_prices(data)
    eur = out.xs("EUR/USD", level="pair")
    assert float(eur.iloc[0]) == pytest.approx(1.10)


def test_fx_carry_instrument_prices_all_six_pairs_present() -> None:
    sig = FXCarrySignal()
    dates = _daily_idx(5)
    data = {
        v: pd.Series(1.0 + i * 0.01, index=dates)
        for i, v in enumerate(sig.instruments)
    }
    out = sig.instrument_prices(data)
    pairs = set(out.index.get_level_values("pair"))
    assert pairs == set(sig.params["pair_to_spot"].keys())


def test_fx_carry_evaluation_horizons() -> None:
    sig = FXCarrySignal()
    assert sig.evaluation_horizons == [1, 2, 3, 6]


# ---------------------------------------------------------------------------
# Equity momentum
# ---------------------------------------------------------------------------


def test_equity_momentum_instruments_equals_required_variables() -> None:
    sig = EquityMomentumSignal()
    assert sig.instruments == sig.required_variables
    assert len(sig.instruments) > 0


def test_equity_momentum_instrument_prices_asset_level_named_variable() -> None:
    sig = EquityMomentumSignal()
    var = sig.instruments[0]
    sig.instruments = [var]
    dates = _daily_idx(10)
    data = {var: pd.Series(np.linspace(100, 110, len(dates)), index=dates)}
    out = sig.instrument_prices(data)
    assert out.index.names == ["date", "variable"]


def test_equity_momentum_evaluation_horizons() -> None:
    sig = EquityMomentumSignal()
    assert sig.evaluation_horizons == [1, 2, 3, 6]
