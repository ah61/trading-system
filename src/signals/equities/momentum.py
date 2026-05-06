"""Equity cross-sectional momentum (12-1) signal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

import numpy as np
import pandas as pd
import yaml

from src.signals.base import Signal


@dataclass(frozen=True, slots=True)
class _EquityMomentumConfig:
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_data: list[str]


class EquityMomentumSignal(Signal):
    """Cross-sectional equity momentum signal using 12-1 month returns.

    Logic:
        - Compute 12-1 month total return for each stock (formation window, skipping recent month).
        - Rank cross-sectionally by date.
        - Long top decile and short bottom decile; set others to 0.
        - Return scaled cross-sectional rank signal in [-1, 1].

    Notes:
        - No-lookahead is enforced by shifting daily closes by 1 day before monthly resampling.
        - Using a "current members" universe is survivorship-biased; this is flagged in metadata.
    """

    # Required class attributes for `Signal.__init_subclass__`. Overwritten from config at init.
    name: str = "equity_momentum"
    asset_class: str = "equities"
    signal_type: str = "momentum"
    frequency: str = "monthly"
    params: dict[str, Any] = {}
    required_data: list[str] = []

    _DEFAULT_CONFIG_PATH = Path("configs/signals/equity_momentum.yaml")

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._parse_config(self._load_config(config_path))
        self.name = cfg.name
        self.asset_class = cfg.asset_class
        self.signal_type = cfg.signal_type
        self.frequency = cfg.frequency
        self.params = cfg.params
        self.required_data = cfg.required_data

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for this signal.

        Kept separate so tests can monkeypatch it (no file I/O).
        """
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Signal config must be a YAML mapping at the top level.")
        return raw

    @classmethod
    def _load_universe_tickers(cls, universe: str) -> list[str]:
        """Load tickers for a named universe.

        Expected file format: `configs/universes/{universe}.yaml` with top-level `tickers: [...]`.
        Tests can monkeypatch this method to avoid file I/O.
        """
        path = Path("configs/universes") / f"{universe}.yaml"
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict) or "tickers" not in raw or not isinstance(raw["tickers"], list):
            raise TypeError(f"Universe file {path.as_posix()!r} must be a mapping with 'tickers' list.")
        return [str(t) for t in raw["tickers"]]

    @classmethod
    def _parse_config(cls, raw: dict[str, Any]) -> _EquityMomentumConfig:
        signal_meta = raw.get("signal", {}) or {}
        params = raw.get("parameters", {}) or {}

        if not isinstance(signal_meta, dict):
            raise TypeError("config.signal must be a mapping.")
        if not isinstance(params, dict):
            raise TypeError("config.parameters must be a mapping.")

        formation_months = int(params.get("formation_months", 12))
        skip_months = int(params.get("skip_months", 1))
        universe = str(params.get("universe", "sp500_current"))
        rebalance_freq = str(params.get("rebalance_freq", "monthly"))

        if formation_months <= 0:
            raise ValueError("formation_months must be positive.")
        if skip_months < 0:
            raise ValueError("skip_months must be non-negative.")
        if rebalance_freq != "monthly":
            raise ValueError("Only rebalance_freq='monthly' is supported.")

        tickers = cls._load_universe_tickers(universe)
        if not tickers:
            raise ValueError(f"Universe {universe!r} resolved to empty ticker list.")

        return _EquityMomentumConfig(
            name=str(signal_meta.get("name", "equity_momentum")),
            asset_class=str(signal_meta.get("asset_class", "equities")),
            signal_type=str(signal_meta.get("signal_type", "momentum")),
            frequency=str(signal_meta.get("frequency", "monthly")),
            params={
                "formation_months": formation_months,
                "skip_months": skip_months,
                "universe": universe,
                "rebalance_freq": rebalance_freq,
            },
            required_data=tickers,
        )

    @staticmethod
    def _to_monthly_close(close: pd.Series) -> pd.Series:
        close = close.astype(np.float64)
        close.index = pd.to_datetime(close.index, utc=True)
        close = close.sort_index()
        # No-lookahead: at month-end t, use information available up to t-1.
        shifted = close.shift(1)
        monthly = shifted.resample("ME").last()
        monthly.index = pd.to_datetime(monthly.index, utc=True)
        return monthly

    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        """Compute cross-sectional 12-1 momentum signals.

        Args:
            data: Mapping of ticker -> DataFrame with a 'close' column and a DatetimeIndex.

        Returns:
            A MultiIndex Series indexed by (date, ticker) with values in [-1, 1].
        """
        formation_months = int(self.params["formation_months"])
        skip_months = int(self.params["skip_months"])
        tickers: list[str] = list(self.required_data)

        missing = sorted(set(tickers) - set(data.keys()))
        if missing:
            raise KeyError(f"Missing required tickers in data: {missing}")

        monthly_px: dict[str, pd.Series] = {}
        for tkr in tickers:
            df = data[tkr]
            if "close" not in df.columns:
                raise KeyError(f"Expected 'close' column for {tkr!r}.")
            monthly_px[tkr] = self._to_monthly_close(df["close"])

        px_df = pd.DataFrame(monthly_px).sort_index()

        # 12-1 momentum return: (P_{t-skip} / P_{t-skip-formation}) - 1
        p_skip = px_df.shift(skip_months)
        p_form = px_df.shift(skip_months + formation_months)
        ret = (p_skip / p_form) - 1.0

        # Cross-sectional ranks per date -> [-1, 1].
        rank_pct = ret.rank(axis=1, method="average", pct=True)
        scaled = (rank_pct * 2.0) - 1.0

        # Long top decile, short bottom decile.
        n = scaled.shape[1]
        k = max(1, int(np.floor(n * 0.1)))
        ord_rank = ret.rank(axis=1, method="first", ascending=True)  # 1..n
        long_mask = ord_rank > (n - k)
        short_mask = ord_rank <= k
        keep = long_mask | short_mask
        out_df = scaled.where(keep, other=0.0)

        out = out_df.stack(dropna=False)
        out.index = out.index.set_names(["date", "ticker"])
        return out.astype(float)

    def get_metadata(self) -> dict[str, Any]:
        meta = super().get_metadata()
        # Flag survivorship bias for "current members" type universes.
        meta["survivorship_biased"] = True
        return meta

