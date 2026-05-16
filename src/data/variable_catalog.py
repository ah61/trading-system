"""Variable catalog — single source of truth for variable definitions AND data access.

Loads variable declarations from YAML files under `configs/data/variables/` and
`configs/data/derived_variables.yaml`. Provides two layers of functionality:

1. **Registry** (5.3 baseline): variable specs, lineage, validation, used_by.
2. **Stateful data access** (5.7): given source instances and a DataStore,
   ``get(name, frequency=...)`` returns a pd.Series for any raw variable,
   routing to the right source via the variable's spec.

The stateful API replaces signal-level direct calls to FREDSource / YahooSource.
After 5.7, signals declare ``required_variables`` (catalogue names) and the
runner uses the catalogue to populate the data dict passed to ``compute()``.

See ROADMAP.md Milestone 5.7 and DESIGN_DECISIONS.md DD-006 for context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import numpy as np
import pandas as pd
import yaml
from loguru import logger

from src.data.cached_source import CachedSource
from src.data.sources.base import DataSource
from src.data.store import DataStore
from src.exceptions import TradingSystemError


Layer = Literal["raw", "transformed", "derived"]
Frequency = Literal["daily", "weekly", "monthly"]


class CatalogError(TradingSystemError):
    """Raised on catalog load or validation errors."""


@dataclass(frozen=True, slots=True)
class VariableSpec:
    """One entry in the catalog.

    Attributes:
        name: Unique identifier (key in the YAML mapping).
        layer: 'raw' (sourced externally), 'transformed' (computed from one or
            more variables), or 'derived' (signals and regime indicators).
        source_file: Catalog file the entry was loaded from. Useful for errors.
        spec: The full YAML payload, as a dict. Schema is layer-specific.
    """

    name: str
    layer: str
    source_file: str
    spec: dict[str, Any] = field(hash=False, compare=False)


# YAML files in the catalog. Order matters only for nicer error messages —
# the loader validates references after loading everything.
_DEFAULT_CATALOG_ROOT = Path("configs/data")
_RAW_AND_TRANSFORMED_DIR = "variables"
_DERIVED_FILE = "derived_variables.yaml"

_VALID_LAYERS = {"raw", "transformed", "derived"}


def _expected_layer_for_file(filename: str) -> str | None:
    """Return the expected `layer:` for entries in a given catalog file.

    The convention is: macro.yaml/market.yaml hold raw; transformations.yaml holds
    transformed; derived_variables.yaml holds derived. Mixing layers within a file
    is treated as an error.
    """
    name = Path(filename).name
    if name == "transformations.yaml":
        return "transformed"
    if name == "derived_variables.yaml":
        return "derived"
    if name in {"macro.yaml", "market.yaml", "sentiment.yaml", "alternative.yaml"}:
        return "raw"
    return None  # Unknown filename — let validation rely on the entry's declared layer.


class VariableCatalog:
    """Loads and queries the variable catalog.

    Use `VariableCatalog.load()` to construct from disk. Construction is otherwise
    explicit for testing — pass `variables=` directly.

    The catalog is immutable after construction. Re-load to pick up YAML changes.
    """

    def __init__(
        self,
        variables: dict[str, VariableSpec],
        *,
        strict: bool = True,
        sources: Mapping[str, DataSource] | None = None,
        store: DataStore | None = None,
    ) -> None:
        """Construct a catalogue.

        Args:
            variables: Mapping of name -> ``VariableSpec``. Use ``load()`` to
                build from disk.
            strict: If True, raise on unresolved variable references.
            sources: Mapping of source identifier (lowercase, e.g. ``"fred"``,
                ``"yahoo"``) to ``DataSource`` instance. Required for ``get()``;
                optional for registry-only use.
            store: ``DataStore`` for cached fetches. Required for ``get()``.

        Notes:
            Registry-only mode (sources=None, store=None) preserves the 5.3
            behaviour and is useful for catalogue inspection without I/O.
            ``get()`` raises if called in registry-only mode.
        """
        self._vars: dict[str, VariableSpec] = dict(variables)
        if strict:
            self._validate_all_references()
        # Build the reverse-dependency map ("used_by") from the inputs/source_variable
        # graph. Computed, not authored in YAML.
        self._used_by: dict[str, set[str]] = self._build_used_by()

        # Stateful data-access wiring (5.7). Build CachedSource wrappers for each
        # provided source so the catalogue benefits from the same cache-first
        # behaviour as the runner.
        self._store = store
        self._cached_sources: dict[str, CachedSource] = {}
        if sources is not None:
            if store is None:
                raise CatalogError("Providing `sources` requires a `store`.")
            for src_name, src in sources.items():
                key = src_name.lower()
                self._cached_sources[key] = CachedSource(
                    source=src, store=store, source_name=key,
                )

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(
        cls,
        root: Path | str = _DEFAULT_CATALOG_ROOT,
        *,
        strict: bool = True,
        sources: Mapping[str, DataSource] | None = None,
        store: DataStore | None = None,
    ) -> "VariableCatalog":
        """Load all catalog files from `root`.

        Expected layout (under `root`):
            variables/macro.yaml
            variables/market.yaml
            variables/transformations.yaml          (optional)
            derived_variables.yaml                  (optional)
            universes/*.yaml                        (optional, template-expanded)

        Args:
            root: Catalog root directory. Default: `configs/data`.
            strict: If True (default), raise on unresolved references between
                variables. Pass `strict=False` for partial development states.
            sources: Optional mapping of source name -> DataSource instance.
                Required for stateful ``get()``.
            store: Optional DataStore. Required for stateful ``get()``.
        """
        root = Path(root)
        if not root.exists():
            raise CatalogError(f"Catalog root not found: {root}")

        variables: dict[str, VariableSpec] = {}

        # Raw + transformed files live under `variables/`.
        raw_xform_dir = root / _RAW_AND_TRANSFORMED_DIR
        if raw_xform_dir.exists():
            for path in sorted(raw_xform_dir.glob("*.yaml")):
                cls._load_file_into(path, variables)

        # Universe files: template-expanded per DESIGN_DECISIONS.md DD-008.
        # Each entry produces one variable spec per ticker, treated as if
        # declared in `market.yaml`.
        universe_dir = root / "universes"
        if universe_dir.exists():
            for path in sorted(universe_dir.glob("*.yaml")):
                cls._load_universe_into(path, variables)

        # Derived file at the catalog root.
        derived_path = root / _DERIVED_FILE
        if derived_path.exists():
            cls._load_file_into(derived_path, variables)

        return cls(variables, strict=strict, sources=sources, store=store)

    @classmethod
    def _load_file_into(
        cls, path: Path, accumulator: dict[str, VariableSpec]
    ) -> None:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise CatalogError(f"{path}: top level must be a mapping, got {type(raw).__name__}.")

        expected_layer = _expected_layer_for_file(path.name)

        for name, entry in raw.items():
            if not isinstance(entry, dict):
                raise CatalogError(
                    f"{path}: entry {name!r} must be a mapping, got {type(entry).__name__}."
                )
            declared_layer = entry.get("layer")
            if declared_layer not in _VALID_LAYERS:
                raise CatalogError(
                    f"{path}: entry {name!r} has invalid layer {declared_layer!r}. "
                    f"Must be one of {sorted(_VALID_LAYERS)}."
                )
            if expected_layer is not None and declared_layer != expected_layer:
                raise CatalogError(
                    f"{path}: entry {name!r} declares layer={declared_layer!r} but file "
                    f"convention requires {expected_layer!r}."
                )

            if name in accumulator:
                prev = accumulator[name].source_file
                raise CatalogError(
                    f"Duplicate variable name {name!r}: defined in {prev} and {path}."
                )

            accumulator[name] = VariableSpec(
                name=name,
                layer=declared_layer,
                source_file=str(path),
                spec=entry,
            )

    @classmethod
    def _load_universe_into(
        cls, path: Path, accumulator: dict[str, VariableSpec]
    ) -> None:
        """Expand a universe YAML file into per-ticker variable specs.

        Schema:

            template:
              layer: raw
              source: yahoo
              frequency: daily
              instrument_type: equity
              adjustment: auto_adjust
              variable_name_pattern: "{ticker}_CLOSE"
            tickers:
              - AAPL
              - MSFT
              - ...

        Each ticker becomes a full ``VariableSpec`` identical in structure to
        a hand-declared market.yaml entry. The ``variable_name_pattern`` field
        uses Python ``str.format`` substitution with ``{ticker}``.

        Conflicts with existing variable names are surfaced as ``CatalogError``.
        """
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise CatalogError(
                f"{path}: universe file top level must be a mapping, got {type(raw).__name__}."
            )

        template = raw.get("template")
        tickers = raw.get("tickers")
        if not isinstance(template, dict):
            raise CatalogError(f"{path}: missing or non-mapping 'template' field.")
        if not isinstance(tickers, list) or not tickers:
            raise CatalogError(f"{path}: missing or empty 'tickers' list.")

        name_pattern = template.get("variable_name_pattern")
        if not isinstance(name_pattern, str) or "{ticker}" not in name_pattern:
            raise CatalogError(
                f"{path}: 'template.variable_name_pattern' must contain '{{ticker}}'."
            )
        layer = template.get("layer", "raw")
        if layer != "raw":
            raise CatalogError(
                f"{path}: universe templates currently only support layer=raw; got {layer!r}."
            )

        # Fields copied from the template to each expanded spec. We drop
        # `variable_name_pattern` (it's metadata about expansion, not part
        # of the variable spec itself).
        per_ticker_fields = {k: v for k, v in template.items() if k != "variable_name_pattern"}

        for ticker in tickers:
            ticker_str = str(ticker)
            try:
                name = name_pattern.format(ticker=ticker_str)
            except (KeyError, IndexError) as e:
                raise CatalogError(
                    f"{path}: variable_name_pattern formatting failed for ticker {ticker_str!r}: {e}"
                )
            # The vendor identifier for this expanded spec is the ticker itself.
            # Universe-expanded variables are always vendor-specified, never a
            # FRED series_id.
            entry = {**per_ticker_fields, "ticker": ticker_str}

            if name in accumulator:
                prev = accumulator[name].source_file
                raise CatalogError(
                    f"Duplicate variable name {name!r}: defined in {prev} and "
                    f"template-expanded from {path}."
                )
            accumulator[name] = VariableSpec(
                name=name,
                layer=layer,
                source_file=str(path),
                spec=entry,
            )

    # ---- validation ------------------------------------------------------

    def _validate_all_references(self) -> None:
        unresolved: list[str] = []
        for name, spec in self._vars.items():
            refs = self._direct_dependencies(spec.spec)
            for ref in refs:
                if ref not in self._vars:
                    unresolved.append(
                        f"{spec.source_file}: {name!r} references undefined variable {ref!r}"
                    )

        if unresolved:
            joined = "\n  ".join(unresolved)
            raise CatalogError(f"Unresolved variable references:\n  {joined}")

        # Cycle detection across the full graph.
        for name in self._vars:
            self._detect_cycle(name, visiting=set(), path=[])

    def _detect_cycle(
        self, name: str, *, visiting: set[str], path: list[str]
    ) -> None:
        if name in visiting:
            cycle = " -> ".join(path + [name])
            raise CatalogError(f"Cycle in variable graph: {cycle}")
        if name not in self._vars:
            return  # Already flagged by reference validation if strict.
        visiting.add(name)
        path.append(name)
        for dep in self._direct_dependencies(self._vars[name].spec):
            self._detect_cycle(dep, visiting=visiting, path=path)
        path.pop()
        visiting.remove(name)

    @staticmethod
    def _direct_dependencies(spec: dict[str, Any]) -> list[str]:
        """Return the names this entry directly depends on.

        Looks at three optional fields:
            - source_variable: single name (transformed)
            - sources: list of names (transformed with multi-input)
            - inputs: list of names (derived)
        Raw entries have no dependencies and should declare none of these.
        """
        deps: list[str] = []
        sv = spec.get("source_variable")
        if isinstance(sv, str):
            deps.append(sv)
        srcs = spec.get("sources")
        if isinstance(srcs, list):
            deps.extend(str(x) for x in srcs)
        inputs = spec.get("inputs")
        if isinstance(inputs, list):
            deps.extend(str(x) for x in inputs)
        return deps

    def _build_used_by(self) -> dict[str, set[str]]:
        used_by: dict[str, set[str]] = {name: set() for name in self._vars}
        for name, spec in self._vars.items():
            for dep in self._direct_dependencies(spec.spec):
                if dep in used_by:
                    used_by[dep].add(name)
        return used_by

    # ---- query API -------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._vars

    def __len__(self) -> int:
        return len(self._vars)

    def __iter__(self) -> Iterable[str]:
        return iter(self._vars)

    def names(self) -> list[str]:
        """All variable names in load order."""
        return list(self._vars.keys())

    def get_spec(self, name: str) -> VariableSpec:
        """Return the full VariableSpec for `name`.

        Renamed from ``get()`` in 5.7 to free that name for the new data-access
        method. Use ``get_spec()`` to inspect a variable's declaration; use
        ``get()`` to retrieve its actual time series.
        """
        if name not in self._vars:
            raise CatalogError(f"Unknown variable: {name!r}")
        return self._vars[name]

    def filter_by_layer(self, layer: Layer) -> list[str]:
        """All variable names with the given layer."""
        if layer not in _VALID_LAYERS:
            raise CatalogError(f"Invalid layer: {layer!r}")
        return [n for n, s in self._vars.items() if s.layer == layer]

    def get_lineage(self, name: str) -> dict[str, Any]:
        """Return the dependency lineage for `name`.

        Walks the inputs/source_variable graph depth-first from `name` and
        returns a tree-like structure:

            {
                "name": name,
                "layer": "...",
                "spec_summary": {"transformation": ..., "type": ..., ...},
                "depends_on": [<lineage subtree>, ...],
            }

        Raw variables have an empty `depends_on`. Note: this is *variable lineage*
        (catalog graph), not *storage lineage* (DataStore.get_lineage which traces
        raw → adjusted → derived materialisations).
        """
        if name not in self._vars:
            raise CatalogError(f"Unknown variable: {name!r}")
        return self._lineage_recurse(name, visited=set())

    def _lineage_recurse(self, name: str, *, visited: set[str]) -> dict[str, Any]:
        if name in visited:
            # Cycles are already prevented at construction; defensive.
            raise CatalogError(f"Cycle hit at {name!r} during lineage walk.")
        visited = visited | {name}
        spec = self._vars[name]
        summary = {
            k: spec.spec[k]
            for k in ("type", "transformation", "source", "series_id", "ticker", "frequency")
            if k in spec.spec
        }
        deps = self._direct_dependencies(spec.spec)
        return {
            "name": name,
            "layer": spec.layer,
            "spec_summary": summary,
            "depends_on": [self._lineage_recurse(d, visited=visited) for d in deps],
        }

    def get_used_by(self, name: str) -> list[str]:
        """All variables that directly depend on `name` (one hop)."""
        if name not in self._used_by:
            raise CatalogError(f"Unknown variable: {name!r}")
        return sorted(self._used_by[name])

    def get_used_by_transitive(self, name: str) -> list[str]:
        """All variables that depend on `name`, directly or indirectly."""
        if name not in self._used_by:
            raise CatalogError(f"Unknown variable: {name!r}")
        out: set[str] = set()
        frontier = list(self._used_by[name])
        while frontier:
            cur = frontier.pop()
            if cur in out:
                continue
            out.add(cur)
            frontier.extend(self._used_by.get(cur, ()))
        return sorted(out)

    # ---- stateful data access (5.7) -------------------------------------

    def get(
        self,
        name: str,
        *,
        frequency: Frequency | None = None,
        start: date | None = None,
        end: date | None = None,
        force_refresh: bool = False,
    ) -> pd.Series:
        """Fetch a variable's time series, routing through the appropriate source.

        Args:
            name: Catalogue variable name.
            frequency: Target frequency. If None (default), returns at native
                frequency. If specified and different from native, resamples
                using DD-004 policy (forward-fill if coarser → finer; aggregate
                if finer → coarser).
            start: Start date inclusive. If None, returns full cached range.
            end: End date inclusive. If None, returns full cached range.
            force_refresh: If True, bypass the cache and re-fetch from the source.
                The fetched range must be a superset of any cached range — see
                ``CachedSource.fetch_or_load`` for semantics.

        Returns:
            A pd.Series indexed by UTC DatetimeIndex, dtype float64.

        Raises:
            CatalogError: If the variable is unknown, the catalogue is in
                registry-only mode, or the variable's source isn't wired in.
            ValueError: If the variable is not a raw variable (transformed and
                derived variables are not yet served by ``get()`` — that's 5.8).
        """
        if not self._cached_sources or self._store is None:
            raise CatalogError(
                "Catalogue is in registry-only mode. Construct with sources= "
                "and store= to enable get()."
            )
        if name not in self._vars:
            raise CatalogError(f"Unknown variable: {name!r}")

        spec = self._vars[name]
        if spec.layer != "raw":
            raise ValueError(
                f"Variable {name!r} is layer={spec.layer!r}. Catalogue.get() "
                f"currently serves only raw variables; transformed/derived are 5.8."
            )

        source_name, ticker_or_id = self._resolve_source_and_ticker(spec)
        cached = self._cached_sources.get(source_name)
        if cached is None:
            raise CatalogError(
                f"Variable {name!r} needs source {source_name!r}, but it isn't "
                f"wired in. Available: {sorted(self._cached_sources)}"
            )

        native_freq = str(spec.spec.get("frequency", "daily"))
        # Date range: default to a wide window if not provided. We use 2010-01-01
        # to today as a sensible default consistent with the runner.
        fetch_start = start or date(2010, 1, 1)
        fetch_end = end or date.today()

        df = cached.fetch_or_load(
            ticker_or_id,
            fetch_start,
            fetch_end,
            frequency=native_freq,
            force_refresh=force_refresh,
        )
        series = self._series_from_df(df, variable_name=name)

        # Resample if a target frequency was requested and differs from native.
        if frequency is not None and frequency != native_freq:
            series = self._resample(series, source_freq=native_freq, target_freq=frequency)

        return series

    # ---- internals for stateful access ----------------------------------

    @staticmethod
    def _resolve_source_and_ticker(spec: VariableSpec) -> tuple[str, str]:
        """Return ``(source_key, vendor_identifier)`` for a raw variable.

        Reads ``source`` and either ``series_id`` (FRED) or ``ticker`` (Yahoo/IB)
        from the spec. Source names are lowercased to match the cached_sources
        dict keys.
        """
        s = spec.spec
        source = s.get("source")
        if not isinstance(source, str):
            raise CatalogError(
                f"Variable {spec.name!r} missing or non-string 'source' field."
            )
        # Prefer series_id (FRED convention) then ticker (Yahoo/IB convention).
        identifier = s.get("series_id") or s.get("ticker")
        if not isinstance(identifier, str):
            raise CatalogError(
                f"Variable {spec.name!r} missing 'series_id' or 'ticker' field."
            )
        return source.lower(), identifier

    @staticmethod
    def _series_from_df(df: pd.DataFrame, *, variable_name: str) -> pd.Series:
        """Extract a single Series from a DataFrame returned by a source.

        Sources return DataFrames with a ``close`` (or ``value``) column plus
        metadata. The catalogue returns a Series since each variable maps to
        one time series.
        """
        for col in ("close", "value"):
            if col in df.columns:
                s = df[col].astype(np.float64)
                s.name = variable_name
                return s
        if df.shape[1] == 1:
            s = df.iloc[:, 0].astype(np.float64)
            s.name = variable_name
            return s
        raise CatalogError(
            f"DataFrame for {variable_name!r} has no 'close' or 'value' column "
            f"and multiple columns: {list(df.columns)}"
        )

    @staticmethod
    def _resample(series: pd.Series, *, source_freq: str, target_freq: str) -> pd.Series:
        """Resample a series from source frequency to target frequency.

        Policy (DD-004):
        - Finer → coarser (e.g. daily → monthly): aggregate via last-of-period
          for prices. This catalogue returns Series; the caller knows whether
          they're prices, rates, or returns, but since this is a raw-variable
          path the value is usually a price/level. Using ``last`` is the
          conservative default.
        - Coarser → finer (e.g. monthly → daily): forward-fill. Information
          content remains coarse, but the index aligns with daily signals.
        - Never interpolate.
        """
        freq_rank = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3}
        src_rank = freq_rank.get(source_freq, 0)
        tgt_rank = freq_rank.get(target_freq, 0)

        if src_rank == tgt_rank:
            return series

        pandas_freq = {
            "daily": "B", "weekly": "W-FRI", "monthly": "ME", "quarterly": "QE",
        }[target_freq]

        if src_rank < tgt_rank:
            # Source is finer than target → aggregate down. last() is the
            # right default for level series (prices, rates, indices). If the
            # caller needs sum-of-returns, they should pass a returns series
            # and use SignalEvaluator's own resampling pipeline.
            return series.resample(pandas_freq).last()
        # Source is coarser than target → forward-fill onto the finer index.
        # The forward-fill produces a series that "looks daily" but has stale
        # values between source-frequency prints. DD-004 documents this.
        target_index = pd.date_range(
            start=series.index.min(), end=series.index.max(),
            freq=pandas_freq, tz=series.index.tz,
        )
        return series.reindex(target_index, method="ffill")
