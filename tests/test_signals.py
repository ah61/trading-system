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

