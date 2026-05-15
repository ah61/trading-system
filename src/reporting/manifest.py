"""Reproducibility manifests for report outputs.

Every run that produces an output writes a ``manifest.json`` alongside its
results. The manifest captures everything needed to understand and (ideally)
reproduce the run: git commit, dirty state, timestamp, config, runtime
metadata.

A manifest is intentionally simple — a flat JSON file — so it can be read by
humans, diffed, or grep'd from the command line. The fields are stable: do not
remove or rename fields in existing manifests when the schema evolves; add
new optional fields instead.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Manifest:
    """Reproducibility manifest for a single run output.

    Attributes:
        timestamp: ISO 8601 UTC timestamp of when the run started.
        run_kind: ``"exploratory"``, ``"variable"``, or ``"strategy"``.
        run_id: Human-readable identifier for the run (folder-name fragment).
        git_commit: Git SHA at run time, or ``"unknown"`` if not in a repo.
        git_dirty: True if the working tree had uncommitted changes.
        python_version: Python version string.
        platform: OS / architecture string.
        config: Arbitrary dict snapshotting the configuration used (signal
            params, data ranges, etc.). Free-form per call site.
        extra: Free-form extra fields for caller-specific metadata.
    """

    timestamp: str
    run_kind: str
    run_id: str
    git_commit: str = "unknown"
    git_dirty: bool = False
    python_version: str = ""
    platform: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, path: Path) -> None:
        """Write the manifest to ``path`` as JSON (UTF-8, indented)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Load a manifest from JSON. Unknown fields are dropped into ``extra``."""
        data = json.loads(path.read_text(encoding="utf-8"))
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in known_fields}
        unknown = {k: v for k, v in data.items() if k not in known_fields}
        if unknown:
            kwargs.setdefault("extra", {}).update(unknown)
        return cls(**kwargs)


def capture_manifest(
    run_kind: str,
    run_id: str,
    config: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Manifest:
    """Build a manifest capturing the current environment.

    Args:
        run_kind: One of ``"exploratory"``, ``"variable"``, ``"strategy"``.
        run_id: Human-readable run identifier.
        config: Run configuration snapshot. Should be JSON-serialisable.
        extra: Arbitrary additional metadata.

    Returns:
        A populated ``Manifest`` instance, ready to write.
    """
    if run_kind not in {"exploratory", "variable", "strategy"}:
        raise ValueError(
            f"run_kind must be one of 'exploratory', 'variable', 'strategy'; got {run_kind!r}"
        )
    commit, dirty = _git_state()
    return Manifest(
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        run_kind=run_kind,
        run_id=run_id,
        git_commit=commit,
        git_dirty=dirty,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform=f"{platform.system()}-{platform.machine()}",
        config=dict(config or {}),
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _git_state() -> tuple[str, bool]:
    """Return ``(commit_sha, is_dirty)`` for the current working tree.

    Returns ``("unknown", False)`` if git is unavailable or this isn't a repo.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown", False

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return commit, False

    return commit, bool(status.strip())
