from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src.data.sources.fred import FREDSource
from src.data.sources.yahoo import YahooSource
from src.exceptions import ConfigError, DataFetchError


def _mock_fred_series(n: int = 10, start: str = "2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(np.linspace(1.0, 2.0, num=n, dtype=np.float64), index=idx)


def test_fred_fetch_returns_correct_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")

    mock_fred = MagicMock()
    mock_fred.get_series.return_value = _mock_fred_series(5)

    with patch("src.data.sources.fred.Fred", autospec=False) as FredCls:
        FredCls.return_value = mock_fred
        src = FREDSource()
        df = src.fetch("DFF", start=date(2024, 1, 1), end=date(2024, 1, 5))

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert str(df.index.tz) in ("UTC", "UTC+00:00")
    assert "close" in df.columns
    assert df["close"].dtype == np.float64
    assert not df.isna().any().any()


def test_fred_fetch_forward_fill_limit_exceeded_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")

    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    values = np.array([1.0, np.nan, np.nan, np.nan, np.nan, 2.0, 2.1, 2.2, 2.3, 2.4], dtype=np.float64)
    series = pd.Series(values, index=idx)

    mock_fred = MagicMock()
    mock_fred.get_series.return_value = series

    with patch("src.data.sources.fred.Fred", autospec=False) as FredCls:
        FredCls.return_value = mock_fred
        src = FREDSource()
        with pytest.raises(DataFetchError):
            _ = src.fetch("GS10", start=date(2024, 1, 1), end=date(2024, 1, 10))


def test_fred_missing_api_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        _ = FREDSource()


def test_fred_fetch_vintage_uses_as_of_date_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")

    mock_fred = MagicMock()
    mock_df = pd.DataFrame(
        {
            "realtime_start": [pd.Timestamp("2015-03-01")],
            "date": [pd.Timestamp("2024-01-15")],
            "value": [10.0],
        }
    )
    mock_fred.get_series_as_of_date.return_value = mock_df

    with patch("src.data.sources.fred.Fred", autospec=False) as FredCls:
        FredCls.return_value = mock_fred
        src = FREDSource()
        df = src.fetch_vintage(
            "CPIAUCSL",
            start=date(2024, 1, 1),
            end=date(2024, 1, 10),
            as_of_date=date(2024, 1, 15),
        )

    mock_fred.get_series_as_of_date.assert_called_once_with("CPIAUCSL", date(2024, 1, 15))

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert str(df.index.tz) in ("UTC", "UTC+00:00")
    assert "close" in df.columns
    assert df["close"].dtype == np.float64
    assert not df.isna().any().any()


def _mock_yahoo_df(close: np.ndarray, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(close), freq="D")
    return pd.DataFrame({"Close": close}, index=idx)


def test_yahoo_fetch_returns_correct_schema() -> None:
    close = np.linspace(10.0, 20.0, num=5, dtype=np.float64)
    mock_df = _mock_yahoo_df(close)

    with patch("yfinance.download", autospec=True) as mock_dl:
        mock_dl.return_value = mock_df
        src = YahooSource()
        df = src.fetch("EURUSD=X", start=date(2024, 1, 1), end=date(2024, 1, 6))

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert str(df.index.tz) in ("UTC", "UTC+00:00")
    assert "close" in df.columns
    assert "source" in df.columns
    assert (df["source"] == "yahoo").all()
    assert df["close"].dtype == np.float64
    assert not df.isna().any().any()


def test_yahoo_fetch_auto_adjust_enforced() -> None:
    close = np.linspace(10.0, 20.0, num=3, dtype=np.float64)
    mock_df = _mock_yahoo_df(close)

    with patch("yfinance.download", autospec=True) as mock_dl:
        mock_dl.return_value = mock_df
        src = YahooSource()
        _ = src.fetch("TLT", start=date(2024, 1, 1), end=date(2024, 1, 4))

    _, kwargs = mock_dl.call_args
    assert kwargs.get("auto_adjust") is True


def test_yahoo_fetch_forward_fill_limit_exceeded_raises() -> None:
    close = np.array([1.0, np.nan, np.nan, np.nan, np.nan, 2.0], dtype=np.float64)
    mock_df = _mock_yahoo_df(close)

    with patch("yfinance.download", autospec=True) as mock_dl:
        mock_dl.return_value = mock_df
        src = YahooSource()
        with pytest.raises(DataFetchError):
            _ = src.fetch("IEF", start=date(2024, 1, 1), end=date(2024, 1, 10))


def test_yahoo_metadata_survivorship_flag() -> None:
    src = YahooSource()
    meta = src.get_metadata("AAPL")
    assert meta["source"] == "yahoo"
    assert meta["ticker"] == "AAPL"
    assert meta["survivorship_biased"] is True

