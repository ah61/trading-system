"""Tests for src.reporting.output_manager."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.reporting.output_manager import OutputManager


# ---------------------------------------------------------------------------
# Folder creation per run kind
# ---------------------------------------------------------------------------


def test_new_exploratory_creates_correct_path(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_exploratory("my_experiment")
    assert run.path.parent.name == "exploratory"
    assert "my_experiment" in run.path.name
    assert run.path.exists()
    assert run.plots_dir.exists()
    assert run.plots_dir.name == "plots"


def test_new_variable_creates_correct_path(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_variable("fx_carry")
    assert run.path.parent.name == "variables"
    assert "fx_carry" in run.path.name


def test_new_strategy_creates_correct_path(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_strategy("fx_carry_monthly_2010-2024")
    assert run.path.parent.name == "strategies"
    assert "fx_carry_monthly_2010-2024" in run.path.name


# ---------------------------------------------------------------------------
# Name sanitisation and collision handling
# ---------------------------------------------------------------------------


def test_name_with_special_chars_is_sanitised(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_variable("fx carry: monthly!")
    # Spaces / colon / bang → underscore; no whitespace in path component.
    assert " " not in run.path.name
    assert ":" not in run.path.name
    assert "!" not in run.path.name


def test_empty_name_raises(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    with pytest.raises(ValueError, match="empty"):
        mgr.new_variable("!!!")


# ---------------------------------------------------------------------------
# Manifest and index behavior
# ---------------------------------------------------------------------------


def test_finalize_writes_manifest(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_variable("fx_carry", config={"frequency": "monthly"})
    run.finalize()

    manifest_path = run.path / "manifest.json"
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["run_kind"] == "variable"
    assert "fx_carry" in data["run_id"]
    assert data["config"]["frequency"] == "monthly"


def test_finalize_is_idempotent(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_variable("idem")
    run.finalize()
    run.finalize()  # second call must not raise
    # Index should have exactly one row.
    rows = list(csv.DictReader((tmp_path / "variables" / "index.csv").open()))
    assert len(rows) == 1


def test_finalize_appends_to_index(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    mgr.new_variable("first").finalize()
    mgr.new_variable("second").finalize()

    index = tmp_path / "variables" / "index.csv"
    assert index.exists()
    rows = list(csv.DictReader(index.open()))
    assert len(rows) == 2
    assert any("first" in r["run_id"] for r in rows)
    assert any("second" in r["run_id"] for r in rows)


def test_indexes_are_isolated_per_kind(tmp_path: Path) -> None:
    """Strategy runs should not appear in the variable index, and vice versa."""
    mgr = OutputManager(reports_root=tmp_path)
    mgr.new_variable("var_run").finalize()
    mgr.new_strategy("strat_run").finalize()
    mgr.new_exploratory("expl_run").finalize()

    for kind, subdir in [("variable", "variables"), ("strategy", "strategies"), ("exploratory", "exploratory")]:
        idx = tmp_path / subdir / "index.csv"
        rows = list(csv.DictReader(idx.open()))
        assert len(rows) == 1, f"{subdir}: expected 1 row, got {len(rows)}"


def test_index_has_expected_columns(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    mgr.new_strategy("s").finalize()
    rows = list(csv.DictReader((tmp_path / "strategies" / "index.csv").open()))
    assert rows[0].keys() == {"timestamp", "run_id", "path", "git_commit", "git_dirty"}


# ---------------------------------------------------------------------------
# Plots directory
# ---------------------------------------------------------------------------


def test_plots_dir_is_under_run_path(tmp_path: Path) -> None:
    mgr = OutputManager(reports_root=tmp_path)
    run = mgr.new_strategy("s")
    assert run.plots_dir.parent == run.path
