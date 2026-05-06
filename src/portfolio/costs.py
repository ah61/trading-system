"""Transaction cost models for portfolio simulation.

This module provides a lightweight, configurable cost model intended for use in
systematic backtests. Phase 1 focuses on conservative spread + market impact.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import yaml


@dataclass(frozen=True, slots=True)
class CostModel:
    """Simple transaction cost model (spread + market impact).

    Notes:
        - Costs are returned in basis points (bps) of notional per trade.
        - Commission is stored for future extension, but is not included in the bps estimate
          because converting per-share commissions to bps requires price and share notionals.
    """

    commission_per_trade: float = 0.005
    spread_bps: Dict[str, float] = None  # type: ignore[assignment]
    market_impact_model: str = "linear"
    impact_coefficient: float = 10.0

    _DEFAULT_CONFIG_PATH = Path("configs/portfolio/costs.yaml")
    _DEFAULT_FX_KEY = "__default_g10_fx_spot__"
    _DEFAULT_TSY_ETF_KEY = "__default_treasury_etf__"
    _DEFAULT_EQUITY_KEY = "__default_large_cap_equity__"

    def __post_init__(self) -> None:
        spread = {} if self.spread_bps is None else dict(self.spread_bps)
        object.__setattr__(self, "spread_bps", spread)

        model = str(self.market_impact_model).lower().strip()
        if model not in {"linear", "sqrt"}:
            raise ValueError("market_impact_model must be 'linear' or 'sqrt'.")
        object.__setattr__(self, "market_impact_model", model)

        if self.impact_coefficient < 0:
            raise ValueError("impact_coefficient must be non-negative.")
        if self.commission_per_trade < 0:
            raise ValueError("commission_per_trade must be non-negative.")

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration for the cost model.

        This is a separate method to allow monkeypatching in tests (no file I/O).
        """

        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Cost model config must be a YAML mapping at the top level.")
        return raw

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> CostModel:
        """Create a CostModel using defaults from YAML.

        Args:
            config_path: Optional override path. If None, uses the repo default
                `configs/portfolio/costs.yaml`.

        Returns:
            Configured CostModel.
        """

        raw = cls._load_config(config_path)
        commission = float(raw.get("commission_per_trade", cls.commission_per_trade))
        impact_model = str(raw.get("market_impact_model", cls.market_impact_model))
        impact_coeff = float(raw.get("impact_coefficient", cls.impact_coefficient))
        spreads_raw = raw.get("spread_bps", {}) or {}
        if not isinstance(spreads_raw, dict):
            raise TypeError("config.spread_bps must be a mapping of instrument->spread_bps.")
        spreads = {str(k): float(v) for k, v in spreads_raw.items()}
        return cls(
            commission_per_trade=commission,
            spread_bps=spreads,
            market_impact_model=impact_model,
            impact_coefficient=impact_coeff,
        )

    def estimate_cost(self, instrument: str, trade_size: float, adv: float) -> float:
        """Estimate total transaction cost for a single trade.

        The total cost is returned in basis points (bps) of notional and includes:
        - Spread cost (conservative defaults by asset class / instrument)
        - Market impact (Almgren-Chriss style): linear or square-root in participation rate

        Args:
            instrument: Instrument identifier (e.g. "EUR/USD", "TLT", "AAPL").
            trade_size: Trade size in the same units as `adv` (absolute value used).
            adv: Average daily volume in the same units as `trade_size`.

        Returns:
            Total cost in bps for a single trade.
        """

        ts = abs(float(trade_size))
        if ts == 0.0:
            return 0.0

        adv_f = float(adv)
        if adv_f <= 0.0:
            raise ValueError("adv must be > 0 for non-zero trades.")

        spread = self._spread_for_instrument(str(instrument))

        participation = ts / adv_f
        if self.market_impact_model == "linear":
            impact = self.impact_coefficient * participation
        else:
            impact = self.impact_coefficient * sqrt(participation)

        return float(spread + impact)

    def apply_costs(self, gross_returns: pd.Series, trades: pd.DataFrame) -> pd.Series:
        """Apply transaction costs to a gross return series.

        This deducts estimated costs on rebalance dates where non-zero trades occur.

        Assumption:
            Since `adv` and prices are not provided, this method treats each trade entry as a
            *participation proxy* and uses `adv=1.0` when calling `estimate_cost`. In practice,
            you should scale `trades` accordingly (e.g., trade_notional / ADV_notional).

        Args:
            gross_returns: Return series indexed by date (any timezone; treated as labels).
            trades: DataFrame of trade sizes (or participation proxy). Index should align to
                `gross_returns.index`. Columns are instrument identifiers. Values are trade sizes.

        Returns:
            Net returns series with costs deducted on rebalance dates.
        """

        if not isinstance(gross_returns, pd.Series):
            raise TypeError("gross_returns must be a pandas Series.")
        if not isinstance(trades, pd.DataFrame):
            raise TypeError("trades must be a pandas DataFrame.")

        net = gross_returns.astype(float).copy()
        if trades.empty:
            return net

        trades_aligned = trades.reindex(net.index).fillna(0.0)

        for dt, row in trades_aligned.iterrows():
            total_cost_bps = 0.0
            for instrument, trade_size in row.items():
                ts = float(trade_size)
                if ts == 0.0:
                    continue
                total_cost_bps += self.estimate_cost(str(instrument), ts, adv=1.0)

            if total_cost_bps != 0.0:
                net.loc[dt] = float(net.loc[dt]) - (total_cost_bps / 10_000.0)

        return net

    def _spread_for_instrument(self, instrument: str) -> float:
        if instrument in self.spread_bps:
            return float(self.spread_bps[instrument])

        # Conservative defaults for Phase 1.
        if "/" in instrument:
            return float(self.spread_bps.get(self._DEFAULT_FX_KEY, 1.5))
        if instrument in {"TLT", "IEF", "SHY"}:
            return float(self.spread_bps.get(self._DEFAULT_TSY_ETF_KEY, 2.0))
        return float(self.spread_bps.get(self._DEFAULT_EQUITY_KEY, 5.0))

