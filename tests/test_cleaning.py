from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from loguru import logger

from src.data.cleaning import DataCleaner
from src.exceptions import DataGapError


def _make_close_df(n: int = 300) -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    close = np.linspace(100.0, 110.0, num=n, dtype=np.float64)
    return pd.DataFrame({"close": close}, index=idx)


def test_outliers_correctly_flagged() -> None:
    df = _make_close_df(300)
    # Inject outliers far beyond 5-sigma once rolling window is available.
    df.loc[df.index[260], "close"] = 10_000.0
    df.loc[df.index[261], "close"] = -10_000.0
    cleaner = DataCleaner()
    out = cleaner.clean(df)
    assert out.loc[df.index[260], "is_outlier"] == True  # noqa: E712
    assert out.loc[df.index[261], "is_outlier"] == True  # noqa: E712
    assert out.loc[df.index[259], "is_outlier"] == False  # noqa: E712


def test_data_gap_error_raised_for_4_plus_day_gap() -> None:
    df = _make_close_df(50)
    df.loc[df.index[10:14], "close"] = np.nan  # 4 consecutive business days
    cleaner = DataCleaner()
    with pytest.raises(DataGapError):
        _ = cleaner.clean(df)


def test_forward_fill_within_limit() -> None:
    df = _make_close_df(50)
    df.loc[df.index[10:12], "close"] = np.nan  # 2 consecutive business days
    cleaner = DataCleaner()
    out = cleaner.clean(df)
    assert out.loc[df.index[10], "close"] == out.loc[df.index[9], "close"]
    assert out.loc[df.index[11], "close"] == out.loc[df.index[9], "close"]
    assert out.loc[df.index[10], "fill_type"] == "ffill"
    assert out.loc[df.index[11], "fill_type"] == "ffill"
    assert out["fill_type"].isna().sum() > 0


def test_no_silent_fills() -> None:
    df = _make_close_df(50)
    df.loc[df.index[20:22], "close"] = np.nan  # 2 fills expected
    logs: list[str] = []

    def _sink(message: Any) -> None:
        logs.append(str(message))

    sink_id = logger.add(_sink, level="INFO")
    try:
        cleaner = DataCleaner()
        out = cleaner.clean(df)
    finally:
        logger.remove(sink_id)

    filled_rows = out["fill_type"].eq("ffill").sum()
    assert filled_rows == 2
    # Require at least one log entry per filled row.
    fill_logs = [l for l in logs if "Filled close via ffill" in l]
    assert len(fill_logs) >= filled_rows


def test_clean_version_column_added() -> None:
    df = _make_close_df(10)
    cleaner = DataCleaner()
    out = cleaner.clean(df)
    assert "clean_version" in out.columns
    assert (out["clean_version"] == 1).all()