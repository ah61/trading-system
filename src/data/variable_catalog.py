"""Variable catalog — single source of truth for variable definitions.

Loads variable declarations from YAML files under `configs/data/variables/` and
`configs/data/derived_variables.yaml`, validates references, and exposes
lineage and reverse-dependency queries.

See ROADMAP.md Milestone 5.3 for the design rationale and ARCHITECTURE.md for
the broader data pipeline context. The catalog is read-only: it does not
materialise data or wire into DataStore. Storage-layer lineage is a separate
concern handled by `src/data/store.py::DataStore.get_lineage()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from src.exceptions import TradingSystemError


Layer = Literal["raw", "transformed", "derived"]


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

    def __init__(self, variables: dict[str, VariableSpec], *, strict: bool = True) -> None:
        self._vars: dict[str, VariableSpec] = dict(variables)
        if strict:
            self._validate_all_references()
        # Build the reverse-dependency map ("used_by") from the inputs/source_variable
        # graph. Computed, not authored in YAML.
        self._used_by: dict[str, set[str]] = self._build_used_by()

    # ---- construction ----------------------------------------------------

    @classmethod
    def load(
        cls,
        root: Path | str = _DEFAULT_CATALOG_ROOT,
        *,
        strict: bool = True,
    ) -> "VariableCatalog":
        """Load all catalog files from `root`.

        Expected layout (under `root`):
            variables/macro.yaml
            variables/market.yaml
            variables/transformations.yaml          (optional)
            derived_variables.yaml                  (optional)

        Args:
            root: Catalog root directory. Default: `configs/data`.
            strict: If True (default), raise on unresolved references between
                variables. Pass `strict=False` for partial development states.
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

        # Derived file at the catalog root.
        derived_path = root / _DERIVED_FILE
        if derived_path.exists():
            cls._load_file_into(derived_path, variables)

        return cls(variables, strict=strict)

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

    def get(self, name: str) -> VariableSpec:
        """Return the full VariableSpec for `name`."""
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
