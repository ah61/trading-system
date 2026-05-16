from __future__ import annotations

import pandas as pd
import pytest

from src.utils.panels import pack_panel_to_multiindex


def _series(start: str, periods: int, value: float) -> pd.Series:
    idx = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    return pd.Series(value, index=idx, dtype=float)


def test_pack_panel_basic_three_assets() -> None:
    a = _series("2024-01-01", 5, 1.0)
    b = _series("2024-01-02", 5, 2.0)
    c = _series("2024-01-03", 5, 3.0)
    out = pack_panel_to_multiindex(
        {"A": a, "B": b, "C": c}, asset_level_name="asset"
    )
    assert isinstance(out.index, pd.MultiIndex)
    assert out.index.names == ["date", "asset"]
    assert out.index.nlevels == 2
    assert set(out.index.get_level_values("asset").unique()) == {"A", "B", "C"}


def test_pack_panel_mismatched_dates_union_with_nan() -> None:
    short = _series("2024-01-01", 3, 10.0)
    long = _series("2024-01-01", 6, 20.0)
    out = pack_panel_to_multiindex(
        {"short": short, "long": long}, asset_level_name="ticker"
    )
    dates = out.index.get_level_values("date").unique()
    assert len(dates) == 6
    sub_short = out.xs("short", level="ticker")
    assert sub_short.iloc[-3:].isna().all()


def test_pack_panel_empty_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        pack_panel_to_multiindex({}, asset_level_name="x")


def test_pack_panel_asset_level_name() -> None:
    s = _series("2024-01-01", 4, 1.5)
    out = pack_panel_to_multiindex({"X": s}, asset_level_name="pair")
    assert out.index.names[1] == "pair"


def test_pack_panel_insertion_order_independent() -> None:
    a = _series("2024-01-01", 4, 1.0)
    b = _series("2024-01-01", 4, 2.0)
    out1 = pack_panel_to_multiindex({"B": b, "A": a}, asset_level_name="v")
    out2 = pack_panel_to_multiindex({"A": a, "B": b}, asset_level_name="v")
    pd.testing.assert_series_equal(out1.sort_index(), out2.sort_index())
