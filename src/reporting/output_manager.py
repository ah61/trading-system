"""Output management for report artifacts.

``OutputManager`` is the single entry point for code that produces report
outputs. It creates a timestamped folder under the correct top-level reports
directory, writes a reproducibility manifest, and appends an entry to the
appropriate index CSV.

Three flavours of run are supported, each with its own folder root:

- **exploratory**: research / notebook outputs. High churn; not tracked by git.
- **variable**: formal variable evaluations (e.g. signal IC tables). Tracked.
- **strategy**: production strategy backtests. Tracked.

The separation matters because mixing them invites two failure modes:
(1) research plots accidentally referenced as production results; (2) production
plots overwritten during exploration.

Typical usage::

    manager = OutputManager()
    run = manager.new_variable(name="fx_carry", config={"frequency": "monthly"})
    # run.path is a Path under reports/variables/<timestamp>_fx_carry/
    # run.plots_dir is run.path / "plots"
    (run.path / "results.md").write_text("...")
    run.finalize()  # writes manifest + updates index
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.reporting.manifest import Manifest, capture_manifest


# Top-level reports directory layout.
_KIND_TO_SUBDIR: dict[str, str] = {
    "exploratory": "exploratory",
    "variable": "variables",
    "strategy": "strategies",
}

# Index CSV columns. Ordered for human readability.
_INDEX_COLUMNS: list[str] = ["timestamp", "run_id", "path", "git_commit", "git_dirty"]


@dataclass
class Run:
    """A single output run created by ``OutputManager``.

    Attributes:
        path: The directory where outputs for this run should be written.
        plots_dir: Subdirectory for plot files. Created on demand.
        manifest: The manifest object (mutable until ``finalize`` is called).
        index_path: Path to the index CSV for this run kind.
    """

    path: Path
    plots_dir: Path
    manifest: Manifest
    index_path: Path
    _finalized: bool = field(default=False, init=False)

    def finalize(self) -> None:
        """Write the manifest and append to the index. Idempotent."""
        if self._finalized:
            return
        self.manifest.write(self.path / "manifest.json")
        _append_index(self.index_path, self.manifest, self.path)
        self._finalized = True


class OutputManager:
    """Creates structured output directories for different run kinds.

    Args:
        reports_root: Base ``reports/`` directory. Defaults to ``./reports``.
    """

    def __init__(self, reports_root: Path | str = "reports") -> None:
        self.reports_root = Path(reports_root)

    # ----- public API: one factory per run kind --------------------------

    def new_exploratory(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Run:
        """Create a new exploratory-research run output."""
        return self._new("exploratory", name, config, extra)

    def new_variable(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Run:
        """Create a new variable-evaluation run output."""
        return self._new("variable", name, config, extra)

    def new_strategy(
        self,
        strategy_id: str,
        config: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Run:
        """Create a new strategy-backtest run output."""
        return self._new("strategy", strategy_id, config, extra)

    # ----- internals -----------------------------------------------------

    def _new(
        self,
        run_kind: str,
        name: str,
        config: dict[str, Any] | None,
        extra: dict[str, Any] | None,
    ) -> Run:
        if run_kind not in _KIND_TO_SUBDIR:
            raise ValueError(
                f"run_kind must be one of {sorted(_KIND_TO_SUBDIR)}; got {run_kind!r}"
            )

        # Sanitise the name so it's safe as a filename component.
        safe_name = _sanitize_name(name)
        if not safe_name:
            raise ValueError(f"name {name!r} sanitises to empty string; pick something descriptive.")

        timestamp = _now_compact()
        run_id = f"{timestamp}_{safe_name}"

        subdir = self.reports_root / _KIND_TO_SUBDIR[run_kind]
        run_path = subdir / run_id
        run_path.mkdir(parents=True, exist_ok=False)  # fail loudly on collision

        plots_dir = run_path / "plots"
        plots_dir.mkdir(exist_ok=False)

        manifest = capture_manifest(
            run_kind=run_kind,
            run_id=run_id,
            config=config,
            extra=extra,
        )

        return Run(
            path=run_path,
            plots_dir=plots_dir,
            manifest=manifest,
            index_path=subdir / "index.csv",
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_compact() -> str:
    """UTC timestamp as ``YYYY-MM-DD_HH-MM-SS``. Sortable, filename-safe."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")


def _sanitize_name(name: str) -> str:
    """Replace anything that isn't [A-Za-z0-9_-] with an underscore."""
    out_chars: list[str] = []
    for ch in name.strip():
        if ch.isalnum() or ch in "-_":
            out_chars.append(ch)
        else:
            out_chars.append("_")
    # Collapse repeated underscores for readability.
    result = "".join(out_chars)
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_")


def _append_index(index_path: Path, manifest: Manifest, run_path: Path) -> None:
    """Append a row to the index CSV; create with header if needed."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not index_path.exists()
    row = {
        "timestamp": manifest.timestamp,
        "run_id": manifest.run_id,
        "path": str(run_path.relative_to(index_path.parent.parent)),
        "git_commit": manifest.git_commit,
        "git_dirty": str(manifest.git_dirty).lower(),
    }
    # newline='' on Windows to avoid double line endings.
    with index_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_INDEX_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(row)
