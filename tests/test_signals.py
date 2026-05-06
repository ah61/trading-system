from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd
import pytest

from src.signals.base import Signal


def test_signal_cannot_instantiate_directly() -> None:
    with pytest.raises(TypeError):
        _ = Signal()  # type: ignore[abstract]


class _ConcreteSignal(Signal):
    name = "test_signal"
    asset_class = "fx"
    signal_type = "momentum"
    frequency = "daily"
    params = {"window": 5}
    required_data = ["prices"]

    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        s = data["prices"]["close"].astype(float)
        out = s.diff()
        out.index = pd.to_datetime(out.index, utc=True)
        return out


def test_normalise_zscore_output_within_bounds() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2020-01-01", periods=400, freq="B", tz="UTC")
    raw = pd.Series(np.random.default_rng(0).normal(size=len(idx)), index=idx)

    norm = sig.normalise(raw, method="zscore", window=252).dropna()
    assert (norm >= -1.0).all()
    assert (norm <= 1.0).all()


def test_normalise_rank_output_within_bounds() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2024-01-01", periods=50, freq="B", tz="UTC")
    raw = pd.Series(np.linspace(-5.0, 5.0, num=len(idx)), index=idx)

    norm = sig.normalise(raw, method="rank")
    assert (norm >= -1.0).all()
    assert (norm <= 1.0).all()


def test_get_metadata_returns_required_keys() -> None:
    sig = _ConcreteSignal()
    meta = sig.get_metadata()
    assert set(meta.keys()) == {
        "name",
        "asset_class",
        "signal_type",
        "frequency",
        "params",
        "required_data",
    }


def test_normalise_unknown_method_raises() -> None:
    sig = _ConcreteSignal()
    idx = pd.date_range("2024-01-01", periods=10, freq="B", tz="UTC")
    raw = pd.Series(np.arange(len(idx), dtype=float), index=idx)
    with pytest.raises(ValueError):
        _ = sig.normalise(raw, method="nope")


def test_fx_carry_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    cfg = {
        "signal": {
            "name": "fx_carry",
            "asset_class": "fx",
            "signal_type": "carry",
            "frequency": "daily",
        },
        "parameters": {
            "rate_series": {"USD": "DFF", "EUR": "EURIBOR3M", "GBP": "GBPRATE3M"},
            "lookback_smooth": 1,
            "n_long": 2,
            "n_short": 2,
        },
        "data_requirements": ["DFF", "EURIBOR3M", "GBPRATE3M"],
        "known_limitations": ["a", "b"],
    }
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=6, freq="B", tz="UTC")
    data: Dict[str, pd.DataFrame] = {
        "DFF": pd.DataFrame({"close": np.linspace(5.0, 5.5, len(idx), dtype=np.float64)}, index=idx),
        "EURIBOR3M": pd.DataFrame(
            {"close": np.linspace(3.0, 3.2, len(idx), dtype=np.float64)}, index=idx
        ),
        "GBPRATE3M": pd.DataFrame(
            {"close": np.linspace(4.0, 4.1, len(idx), dtype=np.float64)}, index=idx
        ),
    }

    sig = FXCarrySignal()
    out = sig.compute(data)

    assert isinstance(out, pd.Series)
    assert out.dtype == float
    assert out.index.nlevels == 2
    assert (out.dropna() >= -1.0).all()
    assert (out.dropna() <= 1.0).all()


def test_fx_carry_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    cfg = {
        "signal": {
            "name": "fx_carry",
            "asset_class": "fx",
            "signal_type": "carry",
            "frequency": "daily",
        },
        "parameters": {
            "rate_series": {"USD": "DFF", "EUR": "EURIBOR3M", "GBP": "GBPRATE3M"},
            "lookback_smooth": 1,
            "n_long": 2,
            "n_short": 2,
        },
        "data_requirements": ["DFF", "EURIBOR3M", "GBPRATE3M"],
        "known_limitations": ["a"],
    }
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=8, freq="B", tz="UTC")
    base = {
        "DFF": pd.DataFrame({"close": np.linspace(5.0, 5.7, len(idx), dtype=np.float64)}, index=idx),
        "EURIBOR3M": pd.DataFrame(
            {"close": np.linspace(3.0, 3.1, len(idx), dtype=np.float64)}, index=idx
        ),
        "GBPRATE3M": pd.DataFrame(
            {"close": np.linspace(4.0, 4.3, len(idx), dtype=np.float64)}, index=idx
        ),
    }
    perturbed = {k: v.copy() for k, v in base.items()}

    # Perturb at t+1 only.
    t = idx[4]
    t_plus_1 = idx[5]
    perturbed["EURIBOR3M"].loc[t_plus_1, "close"] += np.float64(10.0)

    sig = FXCarrySignal()
    out_base = sig.compute(base)
    out_perturbed = sig.compute(perturbed)

    assert out_base.xs(t, level=0).equals(out_perturbed.xs(t, level=0))


def test_fx_carry_metadata_has_limitations(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.fx.carry import FXCarrySignal

    limitations = [
        "Rate differential proxies forward premium — approximation only",
        "Does not include actual FX rollover costs",
    ]
    cfg = {
        "signal": {
            "name": "fx_carry",
            "asset_class": "fx",
            "signal_type": "carry",
            "frequency": "daily",
        },
        "parameters": {"rate_series": {"USD": "DFF", "EUR": "EURIBOR3M"}, "lookback_smooth": 1},
        "data_requirements": ["DFF", "EURIBOR3M"],
        "known_limitations": limitations,
    }
    monkeypatch.setattr(FXCarrySignal, "_load_config", classmethod(lambda cls, _: cfg))

    sig = FXCarrySignal()
    meta = sig.get_metadata()
    assert "known_limitations" in meta
    assert meta["known_limitations"] == limitations


def test_rates_trend_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = {
        "signal": {
            "name": "rates_trend",
            "asset_class": "rates",
            "signal_type": "trend",
            "frequency": "daily",
        },
        "parameters": {"ticker": "TLT", "fast_window": 50, "slow_window": 200, "scale_by_distance": False},
    }
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(100.0, 120.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.DataFrame] = {"TLT": pd.DataFrame({"close": close}, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()

    assert isinstance(out, pd.Series)
    assert out.index.tz is not None
    assert str(out.index.tz) in ("UTC", "UTC+00:00")
    assert (out >= -1.0).all()
    assert (out <= 1.0).all()


def test_rates_trend_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = {
        "signal": {
            "name": "rates_trend",
            "asset_class": "rates",
            "signal_type": "trend",
            "frequency": "daily",
        },
        "parameters": {"ticker": "TLT", "fast_window": 50, "slow_window": 200, "scale_by_distance": True},
    }
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=320, freq="B", tz="UTC")
    close = np.linspace(100.0, 130.0, len(idx), dtype=np.float64)
    base: Dict[str, pd.DataFrame] = {"TLT": pd.DataFrame({"close": close}, index=idx)}
    perturbed: Dict[str, pd.DataFrame] = {"TLT": base["TLT"].copy()}

    t = idx[250]
    t_plus_1 = idx[251]
    perturbed["TLT"].loc[t_plus_1, "close"] += np.float64(500.0)

    sig = RatesTrendSignal()
    out_base = sig.compute(base)
    out_perturbed = sig.compute(perturbed)

    assert float(out_base.loc[t]) == float(out_perturbed.loc[t])


def test_rates_trend_signal_negative_in_downtrend(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = {
        "signal": {
            "name": "rates_trend",
            "asset_class": "rates",
            "signal_type": "trend",
            "frequency": "daily",
        },
        "parameters": {"ticker": "TLT", "fast_window": 50, "slow_window": 200, "scale_by_distance": False},
    }
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(120.0, 100.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.DataFrame] = {"TLT": pd.DataFrame({"close": close}, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()
    assert float(out.iloc[-1]) < 0.0


def test_rates_trend_signal_positive_in_uptrend(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.rates.trend import RatesTrendSignal

    cfg = {
        "signal": {
            "name": "rates_trend",
            "asset_class": "rates",
            "signal_type": "trend",
            "frequency": "daily",
        },
        "parameters": {"ticker": "TLT", "fast_window": 50, "slow_window": 200, "scale_by_distance": False},
    }
    monkeypatch.setattr(RatesTrendSignal, "_load_config", classmethod(lambda cls, _: cfg))

    idx = pd.date_range("2024-01-01", periods=300, freq="B", tz="UTC")
    close = np.linspace(100.0, 120.0, len(idx), dtype=np.float64)
    data: Dict[str, pd.DataFrame] = {"TLT": pd.DataFrame({"close": close}, index=idx)}

    sig = RatesTrendSignal()
    out = sig.compute(data).dropna()
    assert float(out.iloc[-1]) > 0.0


def test_equity_momentum_compute_returns_series(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = [f"T{i}" for i in range(10)]
    cfg = {
        "signal": {
            "name": "equity_momentum",
            "asset_class": "equities",
            "signal_type": "momentum",
            "frequency": "monthly",
        },
        "parameters": {
            "formation_months": 12,
            "skip_months": 1,
            "universe": "sp500_current",
            "rebalance_freq": "monthly",
        },
    }
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    monkeypatch.setattr(EquityMomentumSignal, "_load_universe_tickers", classmethod(lambda cls, _: tickers))

    idx = pd.date_range("2022-01-03", periods=600, freq="B", tz="UTC")
    data: Dict[str, pd.DataFrame] = {}
    for i, tkr in enumerate(tickers):
        close = np.linspace(100.0, 150.0 + i, len(idx), dtype=np.float64)
        data[tkr] = pd.DataFrame({"close": close}, index=idx)

    sig = EquityMomentumSignal()
    out = sig.compute(data).dropna()

    assert isinstance(out, pd.Series)
    assert out.index.nlevels == 2
    assert (out >= -1.0).all()
    assert (out <= 1.0).all()


def test_equity_momentum_no_lookahead(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = [f"T{i}" for i in range(10)]
    cfg = {
        "signal": {
            "name": "equity_momentum",
            "asset_class": "equities",
            "signal_type": "momentum",
            "frequency": "monthly",
        },
        "parameters": {
            "formation_months": 12,
            "skip_months": 1,
            "universe": "sp500_current",
            "rebalance_freq": "monthly",
        },
    }
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    monkeypatch.setattr(EquityMomentumSignal, "_load_universe_tickers", classmethod(lambda cls, _: tickers))

    idx = pd.date_range("2022-01-03", periods=650, freq="B", tz="UTC")
    base: Dict[str, pd.DataFrame] = {}
    for i, tkr in enumerate(tickers):
        close = np.linspace(100.0, 160.0 + i, len(idx), dtype=np.float64)
        base[tkr] = pd.DataFrame({"close": close}, index=idx)

    perturbed = {k: v.copy() for k, v in base.items()}

    sig = EquityMomentumSignal()
    out_base = sig.compute(base)
    out_perturbed = sig.compute(perturbed)

    # Choose a rebalance date and perturb the next business day (t+1) for one stock.
    t = out_base.index.get_level_values(0).unique().sort_values()[15]
    t_plus_1 = (t + pd.tseries.offsets.BDay(1)).to_pydatetime()
    t_plus_1 = pd.Timestamp(t_plus_1)
    perturbed[tickers[0]].loc[t_plus_1, "close"] += np.float64(9999.0)

    out_perturbed2 = sig.compute(perturbed)
    assert out_base.xs(t, level=0).equals(out_perturbed2.xs(t, level=0))


def test_equity_momentum_metadata_survivorship_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = ["A", "B"]
    cfg = {
        "signal": {
            "name": "equity_momentum",
            "asset_class": "equities",
            "signal_type": "momentum",
            "frequency": "monthly",
        },
        "parameters": {"formation_months": 12, "skip_months": 1, "universe": "sp500_current", "rebalance_freq": "monthly"},
    }
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    monkeypatch.setattr(EquityMomentumSignal, "_load_universe_tickers", classmethod(lambda cls, _: tickers))

    sig = EquityMomentumSignal()
    meta = sig.get_metadata()
    assert meta.get("survivorship_biased") is True


def test_equity_momentum_winners_positive_losers_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.signals.equities.momentum import EquityMomentumSignal

    tickers = ["A", "B"] + [f"X{i}" for i in range(8)]
    cfg = {
        "signal": {
            "name": "equity_momentum",
            "asset_class": "equities",
            "signal_type": "momentum",
            "frequency": "monthly",
        },
        "parameters": {
            "formation_months": 12,
            "skip_months": 1,
            "universe": "sp500_current",
            "rebalance_freq": "monthly",
        },
    }
    monkeypatch.setattr(EquityMomentumSignal, "_load_config", classmethod(lambda cls, _: cfg))
    monkeypatch.setattr(EquityMomentumSignal, "_load_universe_tickers", classmethod(lambda cls, _: tickers))

    idx = pd.date_range("2022-01-03", periods=650, freq="B", tz="UTC")
    data: Dict[str, pd.DataFrame] = {}

    # A clearly outperforms; B clearly underperforms; others roughly flat.
    data["A"] = pd.DataFrame({"close": np.linspace(100.0, 250.0, len(idx), dtype=np.float64)}, index=idx)
    data["B"] = pd.DataFrame({"close": np.linspace(200.0, 80.0, len(idx), dtype=np.float64)}, index=idx)
    for tkr in tickers[2:]:
        data[tkr] = pd.DataFrame({"close": np.linspace(100.0, 105.0, len(idx), dtype=np.float64)}, index=idx)

    sig = EquityMomentumSignal()
    out = sig.compute(data).dropna()

    last_date = out.index.get_level_values(0).unique().sort_values()[-1]
    cs = out.xs(last_date, level=0)
    assert float(cs.loc["A"]) > 0.0
    assert float(cs.loc["B"]) < 0.0

