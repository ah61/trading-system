"""Base interfaces for external data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from src.exceptions import DataValidationError


class DataSource(ABC):
    """Abstract base class for all data sources."""

    @abstractmethod
    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch a time series from the source.

        Args:
            ticker: Source-specific series identifier.
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            DataFrame indexed by UTC timestamps with at minimum a float64 `close`
            column. Subclasses are responsible for ensuring no NaNs.
        """

    def validate(self, df: pd.DataFrame) -> bool:
        """Validate a fetched DataFrame for downstream consumption.

        Checks:
        - DatetimeIndex
        - UTC timezone
        - `close` column exists
        - no NaN values
        - `close` dtype is float64

        Args:
            df: DataFrame to validate.

        Raises:
            DataValidationError: If any validation check fails.

        Returns:
            True if all checks pass.
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise DataValidationError("Expected df.index to be a pandas DatetimeIndex.")

        if df.index.tz is None:
            raise DataValidationError("Expected df.index to be timezone-aware (UTC).")

        tz_str = str(df.index.tz)
        if tz_str not in ("UTC", "UTC+00:00"):
            raise DataValidationError(f"Expected df.index timezone UTC, got {tz_str!r}.")

        if "close" not in df.columns:
            raise DataValidationError("Expected a 'close' column.")

        if df.isna().any().any():
            raise DataValidationError("Expected no NaN values in DataFrame.")

        if df["close"].dtype != np.float64:
            raise DataValidationError(f"Expected 'close' dtype float64, got {df['close'].dtype!r}.")

        return True

    @abstractmethod
    def get_metadata(self, ticker: str) -> dict[str, Any]:
        """Return metadata for a ticker.

        Returns:
            Dict with at minimum:
            - source
            - ticker
            - frequency
            - known_limitations
        """

