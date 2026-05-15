"""Equity cross-sectional momentum (12-1) signal."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

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
    required_variables: list[str]


class EquityMomentumSignal(Signal):
    """Cross-sectional equity momentum signal using 12-1 month returns.

    Logic:
        - Compute 12-1 month total return for each name in the universe
          (formation window of ``formation_months`` months, skipping the
          most recent ``skip_months`` months to avoid 1-month reversal).
        - Rank cross-sectionally by date.
        - Long top decile and short bottom decile; zero out the middle.
        - Return scaled cross-sectional rank in [-1, 1].

    Input contract (5.7):
        ``compute(data)`` receives ``data[<var_name>]`` as a ``pd.Series`` of
        close prices indexed by UTC ``DatetimeIndex``. Catalogue variable
        names follow the universe's ``variable_name_pattern`` template
        (typically ``"{ticker}_CLOSE"``, see DD-008).

    Lookahead:
        Daily closes are shifted by 1 day before monthly resampling, so the
        signal at month-end t uses information available up to t-1.

    Survivorship bias:
        Using a "current members" universe is survivorship-biased. The flag
        is set on the metadata. Stage 2 fix in Phase 7.2 (CRSP point-in-time).
    """

    # Required class attributes for `Signal.__init_subclass__`. Overwritten
    # from config at instance init.
    name: str = "equity_momentum"
    asset_class: str = "equities"
    signal_type: str = "momentum"
    frequency: str = "monthly"
    params: dict[str, Any] = {}
    required_variables: list[str] = []

    _DEFAULT_CONFIG_PATH = Path("configs/signals/equity_momentum.yaml")
    _UNIVERSE_DIR = Path("configs/data/universes")
    _DEFAULT_NAME_PATTERN = "{ticker}_CLOSE"

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
        """Load YAML configuration. Kept separate for test monkeypatching."""
        path = Path(config_path) if config_path is not None else cls._DEFAULT_CONFIG_PATH
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise TypeError("Signal config must be a YAML mapping at the top level.")
        return raw

    @classmethod
    def _load_universe(cls, universe: str) -> tuple[list[str], str]:
        """Load a universe file and return ``(tickers, variable_name_pattern)``.

        Reads from ``configs/data/universes/{universe}.yaml`` using the
        template-based schema (DD-008). The returned name pattern is applied
        to each ticker by ``_parse_config`` to produce the catalogue variable
        names this signal will request.

        Args:
            universe: Universe identifier (filename stem).

        Returns:
            ``(tickers, name_pattern)``. ``name_pattern`` defaults to
            ``"{ticker}_CLOSE"`` if the universe file omits
            ``template.variable_name_pattern``.

        Raises:
            TypeError: If the file shape is wrong.
        """
        path = cls._UNIVERSE_DIR / f"{universe}.yaml"
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict) or "tickers" not in raw or not isinstance(raw["tickers"], list):
            raise TypeError(
                f"Universe file {path.as_posix()!r} must be a mapping with a 'tickers' list."
            )
        tickers = [str(t) for t in raw["tickers"]]

        template = raw.get("template")
        name_pattern = cls._DEFAULT_NAME_PATTERN
        if isinstance(template, dict):
            pat = template.get("variable_name_pattern")
            if isinstance(pat, str) and "{ticker}" in pat:
                name_pattern = pat
        return tickers, name_pattern

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

        tickers, name_pattern = cls._load_universe(universe)
        if not tickers:
            raise ValueError(f"Universe {universe!r} resolved to empty ticker list.")

        variables = [name_pattern.format(ticker=t) for t in tickers]

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
                # Persisted for downstream debugging / reporting. Not used by compute().
                "variable_name_pattern": name_pattern,
            },
            required_variables=variables,
        )

    @staticmethod
    def _to_monthly_close(close: pd.Series) -> pd.Series:
        close = close.astype(np.float64)
        close.index = pd.to_datetime(close.index, utc=True)
        close = close.sort_index()
        # No-lookahead: at month-end t, use information up to t-1.
        shifted = close.shift(1)
        monthly = shifted.resample("ME").last()
        monthly.index = pd.to_datetime(monthly.index, utc=True)
        return monthly

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        """Compute cross-sectional 12-1 momentum signals.

        Args:
            data: Mapping of catalogue variable name -> ``pd.Series`` of
                daily close prices. Must contain an entry for every name in
                ``self.required_variables``.

        Returns:
            ``pd.Series`` indexed by a ``(date, variable)`` ``MultiIndex``
            with values in [-1, 1]. The asset level uses catalogue variable
            names (e.g. ``"AAPL_CLOSE"``), not raw tickers — downstream
            consumers may translate via the universe's name pattern if a
            ticker-level label is needed.

        Raises:
            KeyError: If a required variable is missing from ``data``.
        """
        formation_months = int(self.params["formation_months"])
        skip_months = int(self.params["skip_months"])
        variables: list[str] = list(self.required_variables)

        missing = sorted(set(variables) - set(data.keys()))
        if missing:
            raise KeyError(f"Missing required variables in data: {missing}")

        monthly_px: dict[str, pd.Series] = {
            var: self._to_monthly_close(data[var]) for var in variables
        }
        px_df = pd.DataFrame(monthly_px).sort_index()

        # 12-1 momentum return: (P_{t-skip} / P_{t-skip-formation}) - 1
        p_skip = px_df.shift(skip_months)
        p_form = px_df.shift(skip_months + formation_months)
        ret = (p_skip / p_form) - 1.0

        # Cross-sectional percentile rank per date, scaled to [-1, 1].
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

        out = out_df.stack(future_stack=True)
        out.index = out.index.set_names(["date", "variable"])
        return out.astype(float)

    def get_metadata(self) -> dict[str, Any]:
        meta = super().get_metadata()
        # Flag survivorship bias: "current members" universes overstate returns.
        meta["survivorship_biased"] = True
        return meta
