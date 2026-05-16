"""Base interfaces for trading signals.

Signals receive variables by catalogue name (DD-007), not vendor ticker. The
canonical input to ``compute()`` is ``Dict[catalogue_variable_name, pd.Series]``
where each Series is a 1-D level/rate/return time series, UTC-indexed.

This is the 5.7 contract. Pre-5.7 the input was ``Dict[ticker, pd.DataFrame]``
and signals had to dig out a ``close`` column. The catalogue does that
extraction once, in ``VariableCatalog._series_from_df``.
"""

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
    required_variables: list[str]
    instruments: list[str]
    evaluation_horizons: list[int]


class Signal(ABC):
    """Abstract base class for all signals.

    Subclasses must declare class attributes ``name``, ``asset_class``,
    ``signal_type``, ``frequency``, ``params``, ``required_variables``,
    ``instruments``, and ``evaluation_horizons``. These are validated in
    ``__init_subclass__`` and may be overwritten per instance from YAML
    config in the subclass's ``__init__``.

    Attribute contract:
        required_variables: catalogue variable names that compute()
            consumes as inputs.
        instruments: catalogue variable names representing the tradeable
            instruments the signal expresses positions over.
        evaluation_horizons: list of horizons (in periods of ``frequency``)
            at which the signal is evaluated.

    ``required_variables`` and ``instruments`` may be identical, disjoint,
    or overlapping. The signal class decides based on its semantics:
        - Single-asset signals (Rates Trend): identical.
        - Cross-sectional factor signals computed from prices (Equity
          Momentum): identical.
        - Cross-sectional signals computed from one variable set and
          traded on another (FX Carry: rates in, FX out): disjoint.
    """

    # Required class attributes for subclasses.
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_variables: list[str]
    instruments: list[str]
    evaluation_horizons: list[int]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls is Signal:
            return

        required = (
            "name", "asset_class", "signal_type",
            "frequency", "params", "required_variables",
            "instruments", "evaluation_horizons",
        )
        missing = [k for k in required if k not in cls.__dict__]
        if missing:
            raise TypeError(
                f"{cls.__name__} must define class attributes: {', '.join(missing)}"
            )

        horizons = getattr(cls, "evaluation_horizons")
        if not isinstance(horizons, list) or len(horizons) == 0:
            raise TypeError(
                f"{cls.__name__}.evaluation_horizons must be a non-empty list of "
                "positive integers."
            )
        if not all(isinstance(h, int) and h > 0 for h in horizons):
            raise TypeError(
                f"{cls.__name__}.evaluation_horizons must contain only positive "
                f"integers; got {horizons!r}."
            )

        # Basic shape/type checks for early failure.
        _RequiredSignalAttrs(
            name=str(getattr(cls, "name")),
            asset_class=str(getattr(cls, "asset_class")),
            signal_type=str(getattr(cls, "signal_type")),
            frequency=str(getattr(cls, "frequency")),
            params=dict(getattr(cls, "params")),
            required_variables=list(getattr(cls, "required_variables")),
            instruments=list(getattr(cls, "instruments")),
            evaluation_horizons=list(horizons),
        )

    @abstractmethod
    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        """Compute a raw (pre-normalisation) signal series.

        Args:
            data: Mapping of catalogue variable name -> ``pd.Series``. Each
                key must equal one of the names declared in
                ``self.required_variables``. Each Series is a 1-D time
                series (price level, rate, or other scalar variable) with a
                UTC ``DatetimeIndex``.

        Returns:
            The raw signal series. For single-asset signals, a flat
            ``pd.Series`` indexed by UTC ``DatetimeIndex``. For
            cross-sectional signals, a ``pd.Series`` indexed by a 2-level
            ``MultiIndex`` of ``(date, asset)``.

        Notes:
            Implementations must be free of lookahead bias: the value at
            time ``t`` may use only data with index ``<= t``.
        """

    def instrument_prices(self, data: Dict[str, pd.Series]) -> pd.Series:
        """Return prices indexed compatibly with signal output's asset axis.

        Default implementation: assume self.instruments matches catalogue
        variable names present in `data`. Pack into MultiIndex (date, asset)
        Series with level-1 name "instrument". Subclasses override when:
          - the signal is single-asset (return plain DatetimeIndex Series)
          - instrument labels differ from catalogue variable names (FX Carry
            pair labels ≠ spot variable names)
          - inversion or other per-instrument transform is needed.

        Raises:
            KeyError: If any name in self.instruments is missing from data.
        """
        from src.utils.panels import pack_panel_to_multiindex

        missing = [n for n in self.instruments if n not in data]
        if missing:
            raise KeyError(
                f"instrument_prices: missing variables in data: {missing}. "
                f"signal.instruments={self.instruments}; "
                f"data.keys={sorted(data.keys())}"
            )
        series_by_asset = {name: data[name] for name in self.instruments}
        return pack_panel_to_multiindex(
            series_by_asset, asset_level_name="instrument"
        )

    def normalise(
        self, signal: pd.Series, method: str = "zscore", window: int = 252
    ) -> pd.Series:
        """Normalise a raw signal to a bounded range.

        Args:
            signal: Raw signal series.
            method: ``'zscore'`` (rolling z, clipped at ±3, scaled to [-1, 1])
                or ``'rank'`` (cross-sectional rank by date for MultiIndex,
                global percentile rank otherwise; scaled to [-1, 1]).
            window: Rolling window length for z-score (ignored for rank).

        Returns:
            Normalised signal series.

        Raises:
            ValueError: If ``method`` is unknown.
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
            "required_variables": self.required_variables,
            "instruments": self.instruments,
            "evaluation_horizons": self.evaluation_horizons,
        }
