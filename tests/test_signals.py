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

