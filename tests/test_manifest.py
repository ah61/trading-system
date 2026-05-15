"""Tests for src.reporting.manifest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.reporting.manifest import Manifest, capture_manifest


def test_capture_manifest_basic_fields() -> None:
    m = capture_manifest(run_kind="variable", run_id="test_run")
    assert m.run_kind == "variable"
    assert m.run_id == "test_run"
    assert m.timestamp.endswith("+00:00") or m.timestamp.endswith("Z") or "+" in m.timestamp
    assert m.python_version  # populated
    assert m.platform  # populated


def test_capture_manifest_rejects_bad_kind() -> None:
    with pytest.raises(ValueError, match="run_kind"):
        capture_manifest(run_kind="bogus", run_id="x")


def test_capture_manifest_captures_config_and_extra() -> None:
    cfg = {"frequency": "monthly", "horizons": [1, 2, 3]}
    extra = {"note": "manual run"}
    m = capture_manifest(run_kind="exploratory", run_id="x", config=cfg, extra=extra)
    assert m.config == cfg
    assert m.extra == extra


def test_manifest_roundtrip(tmp_path: Path) -> None:
    """Write then load yields equal manifest."""
    m = capture_manifest(run_kind="strategy", run_id="rt", config={"k": "v"})
    path = tmp_path / "manifest.json"
    m.write(path)
    loaded = Manifest.load(path)
    assert loaded.run_kind == m.run_kind
    assert loaded.run_id == m.run_id
    assert loaded.timestamp == m.timestamp
    assert loaded.config == m.config


def test_manifest_load_tolerates_unknown_fields(tmp_path: Path) -> None:
    """Future schema additions should be loadable from older code."""
    payload = {
        "timestamp": "2026-05-15T00:00:00+00:00",
        "run_kind": "variable",
        "run_id": "x",
        "git_commit": "abc",
        "git_dirty": False,
        "python_version": "3.13.4",
        "platform": "Linux-x86_64",
        "config": {},
        "extra": {},
        # An unknown field added later — must not raise.
        "future_field": "future_value",
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = Manifest.load(path)
    # Unknown field captured under extra.
    assert loaded.extra.get("future_field") == "future_value"


def test_manifest_write_creates_parent_dirs(tmp_path: Path) -> None:
    m = capture_manifest(run_kind="variable", run_id="x")
    path = tmp_path / "nested" / "deeper" / "manifest.json"
    m.write(path)
    assert path.exists()
