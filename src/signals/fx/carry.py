"""FX carry signal.

Cross-sectional carry across G10 currencies, anchored on a single base
currency (USD by default), using 3-month interbank rate differentials as a
proxy for forward-implied carry.

Input contract (5.7):
    Signal consumes ``Dict[catalogue_variable_name, pd.Series]`` where each
    value is a rate series indexed by UTC ``DatetimeIndex``. The
    ``rate_series`` parameter maps each currency code to its catalogue
    variable name (e.g. ``USD: DFF``, ``EUR: EUR_RATE``); the catalogue
    knows how to fetch each via the appropriate source.
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
    required_variables: list[str]
    known_limitations: list[str]


class FXCarrySignal(Signal):
    """Cross-sectional FX carry signal based on 3M rate differentials.

    Pair construction (DD-005, Milestone 5.5):
        With ``base_currency = USD``, produces 6 pairs of the form
        ``<non-USD>/USD`` for the 7-currency G10 universe. Pair labels are
        mechanical; market-convention translation is a Phase 6 display-layer
        concern. The math is identical under either labelling.

    Lookahead:
        Inputs are shifted by 1 period before differencing and ranking, so
        the signal at time t uses only rate observations available at or
        before t.
    """

    # Required by `Signal.__init_subclass__`. Overwritten per-instance from
    # YAML config.
    name: str = "fx_carry"
    asset_class: str = "fx"
    signal_type: str = "carry"
    frequency: str = "daily"
    params: dict[str, Any] = {}
    required_variables: list[str] = []

    _DEFAULT_CONFIG_PATH = Path("configs/signals/fx_carry.yaml")

    def __init__(self, config_path: str | Path | None = None) -> None:
        cfg = self._parse_config(self._load_config(config_path))
        self.name = cfg.name
        self.asset_class = cfg.asset_class
        self.signal_type = cfg.signal_type
        self.frequency = cfg.frequency
        self.params = cfg.params
        self.required_variables = cfg.required_variables
        self._known_limitations = cfg.known_limitations

    @classmethod
    def _load_config(cls, config_path: str | Path | None) -> dict[str, Any]:
        """Load YAML configuration. Kept separate for test monkeypatching."""
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
            raise TypeError(
                "config.parameters.rate_series must be a mapping of "
                "currency -> catalogue variable name."
            )

        # required_variables is derived directly from the rate_series mapping:
        # the RHS values are catalogue variable names. Deduplicated and sorted
        # for determinism.
        if isinstance(rate_series, dict) and rate_series:
            required_variables = sorted({str(v) for v in rate_series.values()})
        else:
            required_variables = []

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
                "base_currency": str(params.get("base_currency", "USD")),
            },
            required_variables=required_variables,
            known_limitations=known_limitations,
        )

    @staticmethod
    def _iter_pairs(currencies: Iterable[str], base: str = "USD") -> list[tuple[str, str]]:
        """Construct currency pairs anchored on a common base currency.

        Standard cross-sectional carry uses one base currency (usually USD)
        and ranks all *other* currencies' carry vs that base. For 7
        currencies including the base, this returns 6 pairs of the form
        ``(quote, base)``. Fixed in Milestone 5.5; see PROGRESS.md for the
        prior bug.

        Pair naming convention: ``(quote, base)`` so ``("EUR", "USD")`` →
        the EUR/USD pair, meaning "long EUR, short USD". Positive carry
        differential ``rate[quote] - rate[base]`` signals "go long this pair".

        Args:
            currencies: All currencies in the universe, including the base.
            base: The anchor currency (default ``"USD"``).

        Returns:
            List of ``(quote, base)`` tuples for every non-base currency.

        Raises:
            ValueError: If ``base`` is not in ``currencies``.
        """
        cur = [str(c) for c in currencies]
        if base not in cur:
            raise ValueError(
                f"base currency {base!r} not in universe {cur!r}. "
                "FX Carry needs the base currency's rate series."
            )
        return [(ccy, base) for ccy in cur if ccy != base]

    def compute(self, data: Dict[str, pd.Series]) -> pd.Series:
        """Compute the FX carry signal (cross-sectional ranks) for configured pairs.

        Args:
            data: Mapping of catalogue variable name -> rate ``pd.Series``.
                Must contain an entry for every catalogue variable referenced
                in ``parameters.rate_series`` values.

        Returns:
            A ``pd.Series`` indexed by a ``(date, pair)`` ``MultiIndex`` with
            values in [-1, 1]. Pair labels are ``"<quote>/<base>"``.

        Raises:
            KeyError: If a required catalogue variable is missing from ``data``.
            ValueError: If configuration is inconsistent (e.g., < 2 currencies
                or empty ``rate_series``).
        """
        lookback = int(self.params.get("lookback_smooth", 1))
        n_long = int(self.params.get("n_long", 3))
        n_short = int(self.params.get("n_short", 3))
        rate_series: dict[str, str] = dict(self.params.get("rate_series", {}))

        if not rate_series:
            raise ValueError(
                "FXCarrySignal requires parameters.rate_series "
                "(currency -> catalogue variable name)."
            )

        missing = sorted(set(rate_series.values()) - set(data.keys()))
        if missing:
            raise KeyError(f"Missing required catalogue variables in data: {missing}")

        # Materialise rate Series per currency, normalising index to UTC.
        rates_by_ccy: dict[str, pd.Series] = {}
        for ccy, var_name in rate_series.items():
            s = data[var_name].astype(float)
            s.index = pd.to_datetime(s.index, utc=True)
            rates_by_ccy[ccy] = s.sort_index()

        if len(rates_by_ccy) < 2:
            raise ValueError("Need at least 2 currencies to form FX pairs.")

        base_currency = str(self.params.get("base_currency", "USD"))
        pairs = self._iter_pairs(rates_by_ccy.keys(), base=base_currency)

        # Enforce no-lookahead: use only information at or before t by
        # shifting inputs by 1 period.
        shifted_rates = {ccy: s.shift(1) for ccy, s in rates_by_ccy.items()}

        diffs: dict[str, pd.Series] = {}
        for quote, base in pairs:
            # Positive when the quote currency has the higher rate.
            diff = shifted_rates[quote].sub(shifted_rates[base])
            if lookback > 1:
                diff = diff.rolling(window=lookback, min_periods=lookback).mean()
            pair_name = f"{quote}/{base}"
            diffs[pair_name] = diff

        diff_df = pd.DataFrame(diffs).sort_index()

        # Cross-sectional percentile rank per date, scaled to [-1, 1].
        rank_pct = diff_df.rank(axis=1, method="average", pct=True)
        scaled = (rank_pct * 2.0) - 1.0

        # Apply long/short selection: keep only top/bottom buckets; zero the rest.
        n_pairs = diff_df.shape[1]
        n_long_eff = max(0, min(n_long, n_pairs))
        n_short_eff = max(0, min(n_short, n_pairs - n_long_eff))

        if n_long_eff == 0 and n_short_eff == 0:
            out_df = scaled * 0.0
        else:
            # Ordinal rank (1=lowest..N=highest) for deterministic bucket selection.
            rank_ord = diff_df.rank(axis=1, method="first", ascending=True)
            long_mask = rank_ord > (n_pairs - n_long_eff)
            short_mask = rank_ord <= n_short_eff
            keep = long_mask | short_mask
            out_df = scaled.where(keep, other=0.0)

        out = out_df.stack(future_stack=True)
        out.index = out.index.set_names(["date", "pair"])
        return out.astype(float)

    def get_metadata(self) -> dict[str, Any]:
        meta = super().get_metadata()
        meta["known_limitations"] = list(self._known_limitations)
        return meta
