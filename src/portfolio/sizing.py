"""Position sizing methods for portfolio construction.

Phase 1 implements:
  - Volatility targeting (per-instrument realised vol, diagonal risk approximation)
  - Simple hierarchical risk parity by asset class
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import yaml


@dataclass(frozen=True, slots=True)
class PositionSizer:
    """Compute target portfolio weights from signals and prices."""

    target_vol: float = 0.10
    vol_window: int = 60
    kelly_fraction: float = 0.5  # Phase 1: stored for reference only

    _DEFAULT_CONFIG_PATH = Path("configs/portfolio/sizing.yaml")

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for the position sizer.

        This is a separate method to allow monkeypatching in tests (no file I/O).
        """
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Position sizer config must be a YAML mapping at the top level.")
        return raw

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> "PositionSizer":
        """Create a PositionSizer using defaults from YAML.

        Args:
            config_path: Optional override path. If None, uses the repo default
                `configs/portfolio/sizing.yaml`.

        Returns:
            Configured PositionSizer.
        """
        raw = cls._load_config(config_path)
        target_vol = float(raw.get("target_vol", cls.target_vol))
        vol_window = int(raw.get("vol_window", cls.vol_window))
        kelly_fraction = float(raw.get("kelly_fraction", cls.kelly_fraction))
        return cls(target_vol=target_vol, vol_window=vol_window, kelly_fraction=kelly_fraction)

    def volatility_target(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        target_vol: float = 0.10,
        vol_window: int = 60,
    ) -> pd.DataFrame:
        """Compute volatility-targeted weights.

        Args:
            signals: DataFrame of signals in [-1, 1]. Index=date, columns=instruments.
            prices: DataFrame of prices. Index=date, columns=instruments.
            target_vol: Annualised portfolio volatility target (e.g. 0.10 for 10%).
            vol_window: Rolling window (days) for realised volatility estimation.

        Returns:
            DataFrame of target weights with the same shape as `signals`. Warm-up rows are
            dropped so the returned DataFrame contains no NaN values.
        """
        s, p = self._align_signals_prices(signals, prices)
        vol = self._realised_vol(p, window=vol_window)

        # Base weights: signal scaled by inverse vol.
        inv_vol = (target_vol / vol).astype(float)
        weights = (s.astype(float) * inv_vol).astype(float)

        # Portfolio scaling using diagonal risk approximation.
        weights = self._scale_to_target_vol(weights, vol, target_vol=target_vol)

        # Ensure output has no NaN and preserves input shape.
        return weights.reindex_like(s).fillna(0.0)

    def risk_parity(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        asset_classes: Dict[str, str],
        target_vol: float = 0.10,
        vol_window: int = 60,
    ) -> pd.DataFrame:
        """Compute hierarchical risk parity weights by asset class.

        Logic:
            - Each asset class receives equal budget (1 / n_asset_classes).
            - Within each asset class, instruments are allocated by inverse volatility and
              signal magnitude (|signal| / vol), retaining the signal sign.
            - The final portfolio is scaled to approximately match `target_vol` using a
              diagonal risk approximation.

        Args:
            signals: DataFrame of signals in [-1, 1]. Index=date, columns=instruments.
            prices: DataFrame of prices. Index=date, columns=instruments.
            asset_classes: Mapping of instrument -> asset class name.
            target_vol: Annualised portfolio volatility target.
            vol_window: Rolling window (days) for realised volatility estimation.

        Returns:
            DataFrame of target weights. Warm-up rows are dropped so the returned DataFrame
            contains no NaN values.
        """
        s, p = self._align_signals_prices(signals, prices)

        missing = [c for c in s.columns if c not in asset_classes]
        if missing:
            raise KeyError(f"asset_classes missing mappings for: {missing}")

        vol = self._realised_vol(p, window=vol_window)
        raw = (s.astype(float) / vol.astype(float)).astype(float)

        classes = sorted({str(asset_classes[c]) for c in s.columns})
        if not classes:
            return s.iloc[0:0].copy()
        class_budget = 1.0 / float(len(classes))

        out = pd.DataFrame(0.0, index=s.index, columns=s.columns)
        for cls_name in classes:
            cols = [c for c in s.columns if str(asset_classes[c]) == cls_name]
            if not cols:
                continue
            denom = raw[cols].abs().sum(axis=1).replace(0.0, pd.NA)
            weights_cls = raw[cols].div(denom, axis=0).fillna(0.0) * class_budget
            out.loc[:, cols] = weights_cls

        out = self._scale_to_target_vol(out, vol, target_vol=target_vol)
        return out.reindex_like(s).fillna(0.0)

    @staticmethod
    def _align_signals_prices(signals: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not isinstance(signals, pd.DataFrame):
            raise TypeError("signals must be a pandas DataFrame.")
        if not isinstance(prices, pd.DataFrame):
            raise TypeError("prices must be a pandas DataFrame.")
        if signals.empty:
            return signals.copy(), prices.reindex(signals.index).reindex(columns=signals.columns).copy()

        missing_cols = [c for c in signals.columns if c not in prices.columns]
        if missing_cols:
            raise ValueError(f"prices missing columns required by signals: {missing_cols}")

        s = signals.copy()
        p = prices.reindex(s.index).reindex(columns=s.columns).copy()
        return s, p

    @staticmethod
    def _realised_vol(prices: pd.DataFrame, window: int) -> pd.DataFrame:
        if window <= 1:
            raise ValueError("vol_window must be > 1.")
        prices_f = prices.astype(float)

        # Use log returns per CONVENTIONS.md: log(prices).diff()
        log_prices = prices_f.apply(lambda col: col.map(lambda v: log(v) if pd.notna(v) else float("nan")))
        log_returns = log_prices.diff()

        vol_raw = log_returns.rolling(window=window, min_periods=window).std() * sqrt(252.0)
        return vol_raw.ffill()

    @staticmethod
    def _scale_to_target_vol(weights: pd.DataFrame, vol: pd.DataFrame, target_vol: float) -> pd.DataFrame:
        aligned_weights = weights.reindex(vol.index).copy()
        aligned_vol = vol.reindex(aligned_weights.index)

        # Diagonal risk approximation: sigma_p ~= sqrt(sum_i (w_i * sigma_i)^2)
        w = aligned_weights.fillna(0.0)
        v = aligned_vol.fillna(0.0)
        port_vol = (w * v).pow(2).sum(axis=1).pow(0.5)
        scale = (target_vol / port_vol.where(port_vol != 0.0)).fillna(0.0)
        return w.mul(scale, axis=0)

