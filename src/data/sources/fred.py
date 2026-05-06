"""FRED data source adapter."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

from src.data.sources.base import DataSource
from src.exceptions import ConfigError, DataFetchError, DataValidationError

try:  # pragma: no cover
    from fredapi import Fred  # type: ignore
except Exception:  # pragma: no cover
    Fred = None  # type: ignore[assignment]

# Load .env once at import time — must be at module level so monkeypatch.delenv works in tests
load_dotenv()

_PRIORITY_SERIES: dict[str, dict[str, Any]] = {
    "DFF": {
        "title": "Fed Funds Rate",
        "frequency": "daily",
        "known_limitations": [
            "Can be revised historically; use vintage when doing macro backtests.",
            "Business-day timing can differ from market-close conventions.",
        ],
        "vintage_available": True,
    },
    "GS10": {
        "title": "10Y Treasury yield",
        "frequency": "daily",
        "known_limitations": [
            "Can have missing observations on holidays; small forward-fill may be acceptable.",
        ],
        "vintage_available": True,
    },
    "T10YIE": {
        "title": "10Y inflation breakeven",
        "frequency": "daily",
        "known_limitations": [
            "May have gaps/illiquidity around holidays; validate gap handling for your use case.",
        ],
        "vintage_available": True,
    },
    "CPIAUCSL": {
        "title": "CPI (vintage available, use for macro signals)",
        "frequency": "monthly",
        "known_limitations": [
            "Released with lag; align signal timing to release dates to avoid lookahead.",
            "Revised historically; use vintage data for point-in-time backtests.",
        ],
        "vintage_available": True,
    },
    "PAYEMS": {
        "title": "Non-farm payrolls (vintage available, use for macro signals)",
        "frequency": "monthly",
        "known_limitations": [
            "Released with lag; align signals to release schedule.",
            "Heavily revised; use vintage data for point-in-time macro signals.",
        ],
        "vintage_available": True,
    },
}


class FREDSource(DataSource):
    """FRED data source using `fredapi`."""

    def __init__(self) -> None:
        """Initialize FRED client from environment configuration."""
        api_key = os.getenv("FRED_API_KEY")
        if not api_key:
            raise ConfigError("FRED_API_KEY is not set in the environment.")

        if Fred is None:
            raise ConfigError("fredapi is not installed or failed to import.")

        self._fred = Fred(api_key=api_key)

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch a FRED series as a time series DataFrame."""
        try:
            series = self._fred.get_series(
                ticker, observation_start=start.isoformat(), observation_end=end.isoformat()
            )
        except Exception as e:
            raise DataFetchError(f"FRED API call failed for {ticker!r}: {e}") from e

        if series is None:
            raise DataFetchError(f"FRED series not found: {ticker!r}")

        df = self._series_to_frame(series)
        df = self._forward_fill_limited(df, limit=3, ticker=ticker)

        try:
            self.validate(df)
        except DataValidationError as e:
            raise DataFetchError(f"Fetched data failed validation for {ticker!r}: {e}") from e

        return df

    def fetch_vintage(self, ticker: str, start: date, end: date, as_of_date: date) -> pd.DataFrame:
        """Fetch a vintage (point-in-time) series as of `as_of_date`.

        This avoids lookahead bias for macro series that are revised.
        """
        try:
            series = self._fred.get_series_as_of_date(ticker, as_of_date)
        except Exception as e:
            raise DataFetchError(
                f"FRED vintage API call failed for {ticker!r} as_of {as_of_date.isoformat()}: {e}"
            ) from e

        if series is None:
            raise DataFetchError(f"FRED vintage series not found: {ticker!r}")

        if isinstance(series, pd.DataFrame):
            if "value" not in series.columns or "date" not in series.columns:
                raise DataFetchError(
                    f"Unexpected FRED vintage format for {ticker!r}: expected 'date' and 'value' columns."
                )
            # Set date as index, take last value per date (most recent vintage as of as_of_date)
            series = series.set_index("date")["value"].groupby(level=0).last()

        df = self._series_to_frame(series)
        df = df.loc[
            (df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))
        ]
        df = self._forward_fill_limited(df, limit=3, ticker=ticker)

        try:
            self.validate(df)
        except DataValidationError as e:
            raise DataFetchError(f"Vintage data failed validation for {ticker!r}: {e}") from e

        return df

    def get_metadata(self, ticker: str) -> dict[str, Any]:
        """Return metadata for a ticker."""
        info = _PRIORITY_SERIES.get(ticker, {})
        frequency = str(info.get("frequency", "unknown"))
        known_limitations = list(info.get("known_limitations", ["Series metadata not curated."]))

        return {
            "source": "fred",
            "ticker": ticker,
            "frequency": frequency,
            "known_limitations": known_limitations,
            "vintage_available": True,
        }

    @staticmethod
    def _series_to_frame(series: Any) -> pd.DataFrame:
        s = pd.Series(series)
        s.index = pd.to_datetime(s.index, utc=True)
        df = pd.DataFrame({"close": s.astype(np.float64)})
        df.index.name = "timestamp"
        return df.sort_index()

    @staticmethod
    def _forward_fill_limited(df: pd.DataFrame, limit: int, ticker: str) -> pd.DataFrame:
        before_na = int(df["close"].isna().sum())
        if before_na:
            logger.info("Forward-filling {} NaNs for {}", before_na, ticker)

        filled = df.copy()
        filled["close"] = filled["close"].ffill(limit=limit)

        remaining = int(filled["close"].isna().sum())
        if remaining:
            raise DataFetchError(
                f"{ticker!r} has {remaining} remaining NaN values after forward fill (limit={limit})."
            )

        return filled