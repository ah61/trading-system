"""Rates trend signal based on moving average crossover."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
import yaml

from src.signals.base import Signal


@dataclass(frozen=True, slots=True)
class _RatesTrendConfig:
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_variables: list[str]


class RatesTrendSignal(Signal):
    """Trend-following rates signal using SMA crossovers.

    Signal logic:
        - Compute fast and slow SMAs of the configured price variable
          (default: ``TLT_CLOSE``).
        - Signal is +1 when fast SMA > slow SMA, -1 when fast SMA < slow SMA.
        - If ``scale_by_distance`` is enabled, scale magnitude by the
          relative distance between MAs (squashed to [-1, 1] via ``tanh``).

    Input contract (5.7):
        ``compute(data)`` receives ``data[<variable>]`` as a ``pd.Series`` of
        close prices indexed by UTC ``DatetimeIndex``. The catalogue variable
        name (e.g. ``TLT_CLOSE``) is the dict key, not the underlying Yahoo
        ticker (``TLT``).

    Notes:
        To avoid lookahead bias, prices are shifted by 1 period so the signal
        at time t uses information available at or before t (i.e., up to the
        t-1 close).
    """

    # Required class attributes for `Signal.__init_subclass__`. Overwritten
    # from config at instance init.
    name: str = "rates_trend"
    asset_class: str = "rates"
    signal_type: str = "trend"
    frequency: str = "daily"
    params: dict[str, Any] = {}
    required_variables: list[str] = ["TLT_CLOSE"]

    _DEFAULT_CONFIG_PATH = Path("configs/signals/rates_trend.yaml")

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._parse_config(self._load_config(config_path))
        self.name = cfg.name
        self.asset_class = cfg.asset_class
        self.signal_type = cfg.signal_type
        self.frequency = cfg.frequency
        self.params = cfg.params
        self.required_variables = cfg.required_variables

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for this signal.

        Kept as a separate method so tests can monkeypatch it (no file I/O).
        """
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Signal config must be a YAML mapping at the top level.")
        return raw

    @staticmethod
    def _parse_config(raw: dict[str, Any]) -> _RatesTrendConfig:
        signal_meta = raw.get("signal", {}) or {}
        params = raw.get("parameters", {}) or {}

        if not isinstance(signal_meta, dict):
            raise TypeError("config.signal must be a mapping.")
        if not isinstance(params, dict):
            raise TypeError("config.parameters must be a mapping.")

        variable = str(params.get("variable", "TLT_CLOSE"))
        fast_window = int(params.get("fast_window", 50))
        slow_window = int(params.get("slow_window", 200))
        scale_by_distance = bool(params.get("scale_by_distance", False))

        if fast_window <= 0 or slow_window <= 0:
            raise ValueError("fast_window and slow_window must be positive integers.")

        return _RatesTrendConfig(
            name=str(signal_meta.get("name", "rates_trend")),
            asset_class=str(signal_meta.get("asset_class", "rates")),
            signal_type=str(signal_meta.get("signal_type", "trend")),
            frequency=str(signal_meta.get("frequency", "daily")),
            params={
                "variable": variable,
                "fast_window": fast_window,
                "slow_window": slow_window,
                "scale_by_distance": scale_by_distance,
            },
            required_variables=[variable],
        )

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        """Compute the rates trend signal for the configured variable.

        Args:
            data: Mapping of catalogue variable name -> ``pd.Series``. Must
                contain an entry for ``self.params["variable"]`` (e.g.
                ``"TLT_CLOSE"``).

        Returns:
            Normalised signal series in [-1, 1] with UTC ``DatetimeIndex``.

        Raises:
            KeyError: If the required variable is missing from ``data``.
        """
        variable = str(self.params["variable"])
        fast_window = int(self.params["fast_window"])
        slow_window = int(self.params["slow_window"])
        scale_by_distance = bool(self.params["scale_by_distance"])

        if variable not in data:
            raise KeyError(f"Missing required variable: {variable!r}")

        close = data[variable].astype(np.float64)
        close.index = pd.to_datetime(close.index, utc=True)
        close = close.sort_index()

        # No-lookahead: signal at t uses information up to the t-1 close.
        px = close.shift(1)

        fast = px.rolling(window=fast_window, min_periods=fast_window).mean()
        slow = px.rolling(window=slow_window, min_periods=slow_window).mean()

        direction = np.sign((fast - slow).astype(np.float64))
        direction = direction.replace(0.0, np.nan)

        if scale_by_distance:
            # Scale by relative distance between MAs and squash to [-1, 1].
            rel = (fast - slow) / slow.replace(0.0, np.nan)
            magnitude = np.tanh(rel.abs() * 10.0)
            raw = direction * magnitude
        else:
            raw = direction

        out = raw.clip(-1.0, 1.0).astype(float)
        out.index = pd.to_datetime(out.index, utc=True)
        return out
