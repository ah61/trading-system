"""Data cleaning utilities for time series used in systematic trading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from loguru import logger

from src.exceptions import DataGapError, DataValidationError


FillType = Literal["ffill"]


@dataclass(frozen=True, slots=True)
class DataCleaner:
    """Clean time series data with strict, logged policies."""

    clean_version: int = 1
    outlier_window: int = 252
    outlier_sigma: float = 5.0
    max_ffill_days: int = 3

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean a DataFrame in a strictly logged manner.

        Policies:
        - Missing data: forward-fill up to 3 consecutive business days (logged per fill).
          If more than 3 consecutive business days are missing, raise DataGapError.
        - Outliers: flag values > 5 sigma from rolling 252-day mean.

        Args:
            df: Input DataFrame with a UTC DatetimeIndex and a `close` column.

        Returns:
            A new DataFrame with added columns:
            - is_outlier (bool)
            - fill_type (str or None)
            - clean_version (int)

        Raises:
            DataValidationError: If input schema/timezone is invalid.
            DataGapError: If missing data exceeds the allowable fill threshold.
        """
        self._validate_input(df)

        out = df.copy()
        out = out.sort_index()

        fill_type: pd.Series = pd.Series(index=out.index, data=None, dtype="object")
        out["fill_type"] = fill_type

        out = self._apply_forward_fill_policy(out)
        out["is_outlier"] = self._flag_outliers(out["close"])
        out["clean_version"] = int(self.clean_version)
        out["is_outlier"] = out["is_outlier"].fillna(False).astype(bool)

        return out

    @staticmethod
    def _validate_input(df: pd.DataFrame) -> None:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise DataValidationError("Expected df.index to be a pandas DatetimeIndex.")
        if df.index.tz is None:
            raise DataValidationError("Expected df.index to be timezone-aware (UTC).")
        tz_str = str(df.index.tz)
        if tz_str not in ("UTC", "UTC+00:00"):
            raise DataValidationError(f"Expected df.index timezone UTC, got {tz_str!r}.")
        if "close" not in df.columns:
            raise DataValidationError("Expected a 'close' column.")

    def _apply_forward_fill_policy(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"].astype(np.float64, copy=False)
        na_mask = close.isna()
        if not na_mask.any():
            return df

        # Identify consecutive NaN runs on the existing (business-day) index.
        run_id = na_mask.ne(na_mask.shift(fill_value=False)).cumsum()
        run_lengths = na_mask.groupby(run_id).sum()

        # Any NaN-run longer than max_ffill_days violates the policy.
        too_long = run_lengths[run_lengths > self.max_ffill_days]
        if not too_long.empty:
            # Choose the first violating run to report.
            bad_run = int(too_long.index[0])
            bad_idx = df.index[run_id == bad_run]
            start = bad_idx.min()
            end = bad_idx.max()
            raise DataGapError(
                f"Missing data gap exceeds {self.max_ffill_days} business days "
                f"({len(bad_idx)} days) from {start} to {end}."
            )

        filled = df.copy()
        filled_before = filled["close"].copy()
        filled["close"] = close.ffill(limit=self.max_ffill_days)

        # Ensure we didn't silently leave NaNs (should not happen if runs are within limit).
        remaining = int(filled["close"].isna().sum())
        if remaining:
            raise DataGapError(
                f"{remaining} NaN values remain after forward fill limit={self.max_ffill_days}."
            )

        # Mark and log every filled cell.
        changed = filled_before.isna() & filled["close"].notna()
        if changed.any():
            for ts in filled.index[changed]:
                new_val = float(filled.at[ts, "close"])
                filled.at[ts, "fill_type"] = "ffill"
                logger.info("Filled close via ffill at {} -> {}", ts, new_val)

        return filled

    def _flag_outliers(self, close: pd.Series) -> pd.Series:
        mean = close.rolling(window=self.outlier_window, min_periods=self.outlier_window).mean()
        std = close.rolling(window=self.outlier_window, min_periods=self.outlier_window).std()
        z = (close - mean) / std
        return (z.abs() > float(self.outlier_sigma)).fillna(False).astype(bool)

