"""FX carry signal.

Implements a cross-sectional carry signal for FX pairs using 3M interest rate differentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

import pandas as pd
import yaml

from src.signals.base import Signal


@dataclass(frozen=True, slots=True)
class _FXCarryConfig:
    name: str
    asset_class: str
    signal_type: str
    frequency: str
    params: dict[str, Any]
    required_data: list[str]
    known_limitations: list[str]


class FXCarrySignal(Signal):
    """Cross-sectional FX carry signal based on 3M rate differentials.

    Notes:
        - To avoid lookahead bias, the signal at time t is computed using only inputs available
          at or before time t. This implementation enforces that by shifting input rate series
          by 1 day before computing differentials and ranks.
    """

    # Required by `Signal.__init_subclass__`. These are overwritten at instance level from config.
    name: str = "fx_carry"
    asset_class: str = "fx"
    signal_type: str = "carry"
    frequency: str = "daily"
    params: dict[str, Any] = {}
    required_data: list[str] = []

    _DEFAULT_CONFIG_PATH = Path("configs/signals/fx_carry.yaml")

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._parse_config(self._load_config(config_path))
        self.name = cfg.name
        self.asset_class = cfg.asset_class
        self.signal_type = cfg.signal_type
        self.frequency = cfg.frequency
        self.params = cfg.params
        self.required_data = cfg.required_data
        self._known_limitations = cfg.known_limitations

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for this signal.

        This is a separate method to allow monkeypatching in tests (no file I/O).
        """
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Signal config must be a YAML mapping at the top level.")
        return raw

    @staticmethod
    def _parse_config(raw: dict[str, Any]) -> _FXCarryConfig:
        signal_meta = raw.get("signal", {}) or {}
        params = raw.get("parameters", {}) or {}
        known_limitations = raw.get("known_limitations", []) or []
        data_requirements = raw.get("data_requirements", []) or []
        if not isinstance(data_requirements, list):
            raise TypeError("config.data_requirements must be a list of series IDs.")

        if not isinstance(signal_meta, dict):
            raise TypeError("config.signal must be a mapping.")
        if not isinstance(params, dict):
            raise TypeError("config.parameters must be a mapping.")
        if not isinstance(known_limitations, list) or not all(
            isinstance(x, str) for x in known_limitations
        ):
            raise TypeError("config.known_limitations must be a list of strings.")

        rate_series = params.get("rate_series", {})
        if rate_series is not None and not isinstance(rate_series, dict):
            raise TypeError("config.parameters.rate_series must be a mapping of currency->FRED series ID.")

        required_data: list[str]
        if isinstance(rate_series, dict) and rate_series:
            required_data = sorted({str(v) for v in rate_series.values()})
        else:
            required_data = [str(x) for x in data_requirements]

        return _FXCarryConfig(
            name=str(signal_meta.get("name", "fx_carry")),
            asset_class=str(signal_meta.get("asset_class", "fx")),
            signal_type=str(signal_meta.get("signal_type", "carry")),
            frequency=str(signal_meta.get("frequency", "daily")),
            params={
                **params,
                "lookback_smooth": int(params.get("lookback_smooth", 1)),
                "n_long": int(params.get("n_long", 3)),
                "n_short": int(params.get("n_short", 3)),
                "rate_series": dict(rate_series) if isinstance(rate_series, dict) else {},
            },
            required_data=required_data,
            known_limitations=known_limitations,
        )

    @staticmethod
    def _series_from_df(df: pd.DataFrame) -> pd.Series:
        if "close" in df.columns:
            s = df["close"]
        elif "value" in df.columns:
            s = df["value"]
        elif df.shape[1] == 1:
            s = df.iloc[:, 0]
        else:
            raise KeyError("Rate DataFrame must have a 'close' or 'value' column.")
        s = s.astype(float)
        s.index = pd.to_datetime(s.index, utc=True)
        return s.sort_index()

    @staticmethod
    def _iter_pairs(currencies: Iterable[str]) -> list[tuple[str, str]]:
        cur = [str(c) for c in currencies]
        return [(a, b) for a in cur for b in cur if a != b]

    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        """Compute the FX carry signal (cross-sectional ranks) for configured currency pairs.

        Args:
            data: Mapping of series ID -> DataFrame containing the rate series (daily).

        Returns:
            A MultiIndex Series indexed by (date, pair) with values in [-1, 1].

        Raises:
            KeyError: If required rate series are missing from `data`.
            ValueError: If configuration is inconsistent (e.g., insufficient currencies).
        """
        lookback = int(self.params.get("lookback_smooth", 1))
        n_long = int(self.params.get("n_long", 3))
        n_short = int(self.params.get("n_short", 3))
        rate_series: dict[str, str] = dict(self.params.get("rate_series", {}))

        if not rate_series:
            raise ValueError("FXCarrySignal requires parameters.rate_series (currency -> FRED series ID).")

        missing = sorted(set(rate_series.values()) - set(data.keys()))
        if missing:
            raise KeyError(f"Missing required rate series in data: {missing}")

        rates_by_ccy: dict[str, pd.Series] = {
            ccy: self._series_from_df(data[series_id]) for ccy, series_id in rate_series.items()
        }

        if len(rates_by_ccy) < 2:
            raise ValueError("Need at least 2 currencies to form FX pairs.")

        pairs = self._iter_pairs(rates_by_ccy.keys())

        # Enforce no-lookahead: use only information available at time t by shifting inputs.
        shifted_rates = {ccy: s.shift(1) for ccy, s in rates_by_ccy.items()}

        diffs: dict[str, pd.Series] = {}
        for base, quote in pairs:
            diff = shifted_rates[base].sub(shifted_rates[quote])
            if lookback > 1:
                diff = diff.rolling(window=lookback, min_periods=lookback).mean()
            pair_name = f"{base}/{quote}"
            diffs[pair_name] = diff

        diff_df = pd.DataFrame(diffs).sort_index()

        # Cross-sectional rank per date, scaled to [-1, 1].
        rank_pct = diff_df.rank(axis=1, method="average", pct=True)
        scaled = (rank_pct * 2.0) - 1.0

        # Apply long/short selection: keep only top/bottom buckets; set others to 0.
        n_pairs = diff_df.shape[1]
        n_long_eff = max(0, min(n_long, n_pairs))
        n_short_eff = max(0, min(n_short, n_pairs - n_long_eff))

        if n_long_eff == 0 and n_short_eff == 0:
            out_df = scaled * 0.0
        else:
            # Rank (1=lowest ... N=highest) to identify buckets deterministically.
            rank_ord = diff_df.rank(axis=1, method="first", ascending=True)
            long_mask = rank_ord > (n_pairs - n_long_eff)
            short_mask = rank_ord <= n_short_eff
            keep = long_mask | short_mask
            out_df = scaled.where(keep, other=0.0)

        out = out_df.stack(dropna=False)
        out.index = out.index.set_names(["date", "pair"])
        out = out.astype(float)
        return out

    def get_metadata(self) -> dict[str, Any]:
        meta = super().get_metadata()
        meta["known_limitations"] = list(self._known_limitations)
        return meta

