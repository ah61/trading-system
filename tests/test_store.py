from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.data.store import DataStore
from src.exceptions import StorageError


def _make_df(n: int = 10) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "close": np.linspace(100.0, 101.0, num=n, dtype=np.float64),
            "volume": np.arange(n, dtype=np.int64),
        },
        index=idx,
    )


def test_write_read_round_trip_preserves_index_and_dtypes(tmp_path: Path) -> None:
    store = DataStore(data_dir=tmp_path / "data")
    df = _make_df(15)

    store.write_adjusted(df, source="yahoo", ticker="EURUSD", frequency="daily", version=1)
    out = store.read(source="yahoo", ticker="EURUSD", frequency="daily", layer="adjusted", version=1)

    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.tz is not None
    assert str(out.index.tz) in ("UTC", "UTC+00:00")

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert str(df.index.tz) in ("UTC", "UTC+00:00")

    assert list(out.index.tz_convert("UTC").astype(str)) == list(df.index.tz_convert("UTC").astype(str))

    assert out["close"].dtype == np.float64
    np.testing.assert_allclose(out["close"].to_numpy(), df["close"].to_numpy(), rtol=0.0, atol=0.0)


def test_write_raw_raises_on_duplicate(tmp_path: Path) -> None:
    store = DataStore(data_dir=tmp_path / "data")
    df = _make_df(5)

    store.write_raw(df, source="fred", ticker="DFF", frequency="daily")
    with pytest.raises(StorageError):
        store.write_raw(df, source="fred", ticker="DFF", frequency="daily")


def test_read_latest_adjusted_returns_highest_version(tmp_path: Path) -> None:
    store = DataStore(data_dir=tmp_path / "data")
    df1 = _make_df(5)
    df2 = _make_df(5) * 2.0

    store.write_adjusted(df1, source="fred", ticker="DFF", frequency="daily", version=1)
    store.write_adjusted(df2, source="fred", ticker="DFF", frequency="daily", version=3)
    store.write_adjusted(df1, source="fred", ticker="DFF", frequency="daily", version=2)

    out = store.read(source="fred", ticker="DFF", frequency="daily", layer="adjusted", version="latest")
    assert isinstance(out.index, pd.DatetimeIndex)
    assert out.index.tz is not None
    assert str(out.index.tz) in ("UTC", "UTC+00:00")

    assert isinstance(df2.index, pd.DatetimeIndex)
    assert df2.index.tz is not None
    assert str(df2.index.tz) in ("UTC", "UTC+00:00")

    assert list(out.index.tz_convert("UTC").astype(str)) == list(df2.index.tz_convert("UTC").astype(str))

    assert out["close"].dtype == np.float64
    np.testing.assert_allclose(out["close"].to_numpy(), df2["close"].to_numpy(), rtol=0.0, atol=0.0)


def test_list_available_inventory(tmp_path: Path) -> None:
    store = DataStore(data_dir=tmp_path / "data")
    raw = _make_df(7)
    adj_v1 = _make_df(7)
    adj_v2 = _make_df(7) * 3.0
    der = _make_df(7)

    store.write_raw(raw, source="fred", ticker="DFF", frequency="daily")
    store.write_adjusted(adj_v1, source="fred", ticker="DFF", frequency="daily", version=1)
    store.write_adjusted(adj_v2, source="fred", ticker="DFF", frequency="daily", version=2)
    store.write_derived(der, name="fred_DFF_spread", frequency="daily")

    inv = store.list_available()
    assert set(inv.columns) == {"layer", "source", "ticker", "frequency", "version", "row_count"}

    raw_rows = inv[(inv["layer"] == "raw") & (inv["source"] == "fred") & (inv["ticker"] == "DFF")]
    assert len(raw_rows) == 1
    assert int(raw_rows.iloc[0]["row_count"]) == len(raw)

    adj_rows = inv[(inv["layer"] == "adjusted") & (inv["source"] == "fred") & (inv["ticker"] == "DFF")]
    assert set(adj_rows["version"].tolist()) == {1, 2}
    assert all(int(x) == len(raw) for x in adj_rows["row_count"].tolist())

    der_rows = inv[(inv["layer"] == "derived") & (inv["source"] == "fred_DFF_spread")]
    assert len(der_rows) == 1
    assert der_rows.iloc[0]["frequency"] == "daily"
    assert int(der_rows.iloc[0]["row_count"]) == len(der)

