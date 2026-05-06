"""Base interfaces for trading signals."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class _RequiredSignalAttrs:
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_data: list[str]


class Signal(ABC):
    """Abstract base class for all signals."""

    # Required class attributes for subclasses.
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_data: list[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls is Signal:
            return

        required = ("name", "asset_class", "signal_type", "frequency", "params", "required_data")
        missing = [k for k in required if k not in cls.__dict__]
        if missing:
            raise TypeError(f"{cls.__name__} must define class attributes: {', '.join(missing)}")

        # Basic shape/type checks for early failure.
        _RequiredSignalAttrs(
            name=str(getattr(cls, "name")),
            asset_class=str(getattr(cls, "asset_class")),
            signal_type=str(getattr(cls, "signal_type")),
            frequency=str(getattr(cls, "frequency")),
            params=dict(getattr(cls, "params")),
            required_data=list(getattr(cls, "required_data")),
        )

    @abstractmethod
    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        """Compute a raw (pre-normalisation) signal series.

        Args:
            data: Mapping of input dataset names to DataFrames.

        Returns:
            Raw signal series. Index must be a UTC DatetimeIndex and values must be float.

        Notes:
            Implementations must be free of lookahead bias.
        """

    def normalise(self, signal: pd.Series, method: str = "zscore", window: int = 252) -> pd.Series:
        """Normalise a raw signal to a bounded range.

        Args:
            signal: Raw signal series.
            method: 'zscore' or 'rank'.
            window: Rolling window length for z-score.

        Returns:
            Normalised signal series.

        Raises:
            ValueError: If `method` is unknown.
        """
        if method == "zscore":
            s = signal.astype(float)
            mean = s.rolling(window=window, min_periods=window).mean()
            std = s.rolling(window=window, min_periods=window).std()
            z = (s - mean) / std.replace(0.0, np.nan)
            z = z.clip(-3.0, 3.0) / 3.0
            return z

        if method == "rank":
            s = signal.astype(float)
            if isinstance(s.index, pd.MultiIndex) and len(s.index.levels) >= 2:
                # Cross-sectional rank by date level (assumed level 0).
                pct = s.groupby(level=0).rank(pct=True)
                return (pct * 2.0) - 1.0
            pct = s.rank(pct=True)
            return (pct * 2.0) - 1.0

        raise ValueError(f"Unknown normalisation method: {method!r}")

    def get_metadata(self) -> dict[str, Any]:
        """Return metadata about this signal."""
        return {
            "name": self.name,
            "asset_class": self.asset_class,
            "signal_type": self.signal_type,
            "frequency": self.frequency,
            "params": self.params,
            "required_data": self.required_data,
        }

