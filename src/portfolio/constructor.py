"""Portfolio construction from signals, prices, and risk limits.

This module converts per-instrument signals into target portfolio weights, applies simple
portfolio-level risk limits, and produces a rebalancing trades matrix (weight changes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

import pandas as pd
import yaml

from src.portfolio.costs import CostModel
from src.portfolio.sizing import PositionSizer


@dataclass(slots=True)
class PortfolioConstructor:
    """Construct target weights and trades from signals.

    Attributes:
        position_sizer: Sizing engine used to convert signals into raw weights.
        cost_model: Optional cost model stored for downstream simulation steps.
        max_drawdown_stop: Optional risk control threshold (stored for future use).
    """

    position_sizer: PositionSizer = PositionSizer()
    cost_model: CostModel | None = None
    max_drawdown_stop: float = 0.20

    _DEFAULT_CONFIG_PATH = Path("configs/portfolio/risk_limits.yaml")

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for portfolio risk limits.

        This is a separate method to allow monkeypatching in tests (no file I/O).
        """
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Portfolio risk limits config must be a YAML mapping at the top level.")
        return raw

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        position_sizer: PositionSizer | None = None,
        cost_model: CostModel | None = None,
    ) -> "PortfolioConstructor":
        """Create a PortfolioConstructor using defaults from YAML.

        Args:
            config_path: Optional override path. If None, uses the repo default
                `configs/portfolio/risk_limits.yaml`.
            position_sizer: Optional PositionSizer instance to use.
            cost_model: Optional CostModel instance to store.

        Returns:
            Configured PortfolioConstructor.
        """
        raw = cls._load_config(config_path)
        max_dd = float(raw.get("max_drawdown_stop", cls.max_drawdown_stop))
        return cls(
            position_sizer=PositionSizer() if position_sizer is None else position_sizer,
            cost_model=cost_model,
            max_drawdown_stop=max_dd,
        )

    def construct(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        asset_classes: Dict[str, str],
        sizing_method: str = "vol_target",
        target_vol: float = 0.10,
        gross_limit: float = 2.0,
        net_limit: float = 0.20,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Construct target weights and rebalance trades.

        Args:
            signals: Signal matrix with index=date, columns=instruments, values in [-1, 1].
            prices: Price matrix with index=date, columns=instruments.
            asset_classes: Mapping of instrument -> asset class. Required for risk parity.
            sizing_method: Sizing approach: 'vol_target' or 'risk_parity'.
            target_vol: Annualised volatility target (e.g. 0.10 for 10%).
            gross_limit: Maximum gross exposure: \(\sum_i |w_i| \le\) gross_limit.
            net_limit: Maximum net exposure: \(|\sum_i w_i| \le\) net_limit.

        Returns:
            weights: Target weights per instrument per date.
            trades: Weight changes (rebalance trades), where trades[t] = weights[t] - weights[t-1]
                and trades[first_row] = weights[first_row].
        """
        if not isinstance(signals, pd.DataFrame):
            raise TypeError("signals must be a pandas DataFrame.")
        if not isinstance(prices, pd.DataFrame):
            raise TypeError("prices must be a pandas DataFrame.")
        if gross_limit <= 0.0:
            raise ValueError("gross_limit must be > 0.")
        if net_limit < 0.0:
            raise ValueError("net_limit must be >= 0.")

        method = str(sizing_method).lower().strip()
        if method not in {"vol_target", "risk_parity"}:
            raise ValueError("sizing_method must be 'vol_target' or 'risk_parity'.")

        if signals.empty:
            empty = signals.copy().astype(float)
            trades = empty.copy()
            return empty, trades

        # Step 1: raw weights from PositionSizer.
        if method == "vol_target":
            weights = self.position_sizer.volatility_target(
                signals,
                prices,
                target_vol=float(target_vol),
                vol_window=int(self.position_sizer.vol_window),
            )
        else:
            weights = self.position_sizer.risk_parity(
                signals,
                prices,
                asset_classes=asset_classes,
                target_vol=float(target_vol),
                vol_window=int(self.position_sizer.vol_window),
            )

        weights = weights.reindex_like(signals).fillna(0.0).astype(float)

        # Steps 2-3: enforce portfolio-level risk limits per date.
        weights = self._enforce_gross_limit(weights, gross_limit=float(gross_limit))
        weights = self._enforce_net_limit(weights, net_limit=float(net_limit))

        # Steps 4-5: compute trades as weight differences; first row is full entry.
        trades = weights.diff().fillna(0.0)
        if len(weights.index) > 0:
            trades.iloc[0] = weights.iloc[0]

        return weights, trades

    @staticmethod
    def _enforce_gross_limit(weights: pd.DataFrame, gross_limit: float) -> pd.DataFrame:
        gross = weights.abs().sum(axis=1)
        scale = (gross_limit / gross.where(gross != 0.0)).clip(upper=1.0).fillna(1.0)
        return weights.mul(scale, axis=0)

    @classmethod
    def _enforce_net_limit(cls, weights: pd.DataFrame, net_limit: float) -> pd.DataFrame:
        if net_limit == float("inf"):
            return weights
        out = weights.copy()
        for dt in out.index:
            out.loc[dt] = cls._enforce_net_limit_row(out.loc[dt], net_limit=net_limit)
        return out

    @staticmethod
    def _enforce_net_limit_row(row: pd.Series, net_limit: float) -> pd.Series:
        w = row.astype(float).copy()
        net = float(w.sum())
        excess = abs(net) - float(net_limit)
        if excess <= 0.0:
            return w

        direction = 1.0 if net > 0.0 else -1.0
        candidates = w[w * direction > 0.0]
        if candidates.empty:
            return w

        order = candidates.abs().sort_values(ascending=False).index
        remaining = excess
        for col in order:
            if remaining <= 0.0:
                break
            cur = float(w.loc[col])
            reducible = abs(cur)
            delta = min(reducible, remaining)
            w.loc[col] = cur - direction * delta
            remaining -= delta
        return w
