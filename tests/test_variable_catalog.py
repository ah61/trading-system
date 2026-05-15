"""Tests for the variable catalog (Milestone 5.3).

Self-contained: builds tiny YAML fixtures in tmp_path; does not load the real
project catalog. The real catalog is exercised by import-time sanity in
`test_real_catalog_loads_strict` at the bottom.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from src.data.variable_catalog import CatalogError, VariableCatalog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_catalog(root: Path, files: dict[str, str]) -> None:
    """Write a set of catalog files under `root`.

    Keys of `files` are paths relative to `root` (e.g. "variables/macro.yaml");
    values are YAML strings (dedented).
    """
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Load + structure
# ---------------------------------------------------------------------------


def test_load_minimal_catalog(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    assert "DFF" in cat
    assert len(cat) == 1
    assert cat.get_spec("DFF").layer == "raw"


def test_load_three_layers(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
        "variables/transformations.yaml": """
            DFF_Z:
              layer: transformed
              source_variable: DFF
              transformation: rolling_zscore
              window: 252
              frequency: daily
        """,
        "derived_variables.yaml": """
            fake_signal:
              layer: derived
              type: signal
              inputs: [DFF_Z]
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    assert set(cat.names()) == {"DFF", "DFF_Z", "fake_signal"}
    assert cat.filter_by_layer("raw") == ["DFF"]
    assert cat.filter_by_layer("transformed") == ["DFF_Z"]
    assert cat.filter_by_layer("derived") == ["fake_signal"]


# ---------------------------------------------------------------------------
# Validation: unresolved references
# ---------------------------------------------------------------------------


def test_unresolved_source_variable_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/transformations.yaml": """
            DFF_Z:
              layer: transformed
              source_variable: NONEXISTENT
              transformation: rolling_zscore
              window: 252
              frequency: daily
        """,
    })
    with pytest.raises(CatalogError, match="Unresolved variable references"):
        VariableCatalog.load(tmp_path)


def test_unresolved_inputs_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "derived_variables.yaml": """
            sig:
              layer: derived
              type: signal
              inputs: [NOT_THERE]
              frequency: daily
        """,
    })
    with pytest.raises(CatalogError, match="NOT_THERE"):
        VariableCatalog.load(tmp_path)


def test_strict_false_allows_unresolved(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "derived_variables.yaml": """
            sig:
              layer: derived
              type: signal
              inputs: [NOT_THERE]
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path, strict=False)
    # Catalog loads but the unresolved name is not a node.
    assert "sig" in cat
    assert "NOT_THERE" not in cat


# ---------------------------------------------------------------------------
# Validation: file-layer convention
# ---------------------------------------------------------------------------


def test_mixed_layer_in_file_rejected(tmp_path: Path) -> None:
    # macro.yaml should hold raw; declaring transformed here is wrong.
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            wrong_entry:
              layer: transformed
              source_variable: DFF
              transformation: log_return
              window: 1
              frequency: daily
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
    })
    with pytest.raises(CatalogError, match="file convention requires"):
        VariableCatalog.load(tmp_path)


# ---------------------------------------------------------------------------
# Validation: duplicates
# ---------------------------------------------------------------------------


def test_duplicate_variable_name_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
        "variables/market.yaml": """
            DFF:
              layer: raw
              source: yahoo
              ticker: DFF
              frequency: daily
        """,
    })
    with pytest.raises(CatalogError, match="Duplicate variable name"):
        VariableCatalog.load(tmp_path)


# ---------------------------------------------------------------------------
# Validation: cycles
# ---------------------------------------------------------------------------


def test_cycle_in_graph_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/transformations.yaml": """
            A:
              layer: transformed
              source_variable: B
              transformation: log_return
              window: 1
              frequency: daily
            B:
              layer: transformed
              source_variable: A
              transformation: log_return
              window: 1
              frequency: daily
        """,
    })
    with pytest.raises(CatalogError, match="Cycle"):
        VariableCatalog.load(tmp_path)


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def test_lineage_walks_full_chain(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
        "variables/transformations.yaml": """
            DFF_Z:
              layer: transformed
              source_variable: DFF
              transformation: rolling_zscore
              window: 252
              frequency: daily
        """,
        "derived_variables.yaml": """
            sig:
              layer: derived
              type: signal
              inputs: [DFF_Z]
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    lineage = cat.get_lineage("sig")
    assert lineage["name"] == "sig"
    assert lineage["layer"] == "derived"
    assert len(lineage["depends_on"]) == 1
    assert lineage["depends_on"][0]["name"] == "DFF_Z"
    assert lineage["depends_on"][0]["depends_on"][0]["name"] == "DFF"
    assert lineage["depends_on"][0]["depends_on"][0]["layer"] == "raw"
    assert lineage["depends_on"][0]["depends_on"][0]["depends_on"] == []


def test_lineage_handles_multi_input_sources(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            GS10:
              layer: raw
              source: FRED
              series_id: GS10
              frequency: monthly
            GS2:
              layer: raw
              source: FRED
              series_id: GS2
              frequency: monthly
        """,
        "variables/transformations.yaml": """
            SLOPE:
              layer: transformed
              sources: [GS10, GS2]
              transformation: difference
              frequency: monthly
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    lineage = cat.get_lineage("SLOPE")
    dep_names = {d["name"] for d in lineage["depends_on"]}
    assert dep_names == {"GS10", "GS2"}


def test_lineage_unknown_name_raises(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    with pytest.raises(CatalogError, match="Unknown variable"):
        cat.get_lineage("nope")


# ---------------------------------------------------------------------------
# used_by
# ---------------------------------------------------------------------------


def test_used_by_direct(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
        "variables/transformations.yaml": """
            A:
              layer: transformed
              source_variable: DFF
              transformation: log_return
              window: 1
              frequency: daily
            B:
              layer: transformed
              source_variable: DFF
              transformation: rolling_zscore
              window: 252
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    assert cat.get_used_by("DFF") == ["A", "B"]
    assert cat.get_used_by("A") == []


def test_used_by_transitive(tmp_path: Path) -> None:
    _write_catalog(tmp_path, {
        "variables/macro.yaml": """
            DFF:
              layer: raw
              source: FRED
              series_id: DFF
              frequency: daily
        """,
        "variables/transformations.yaml": """
            A:
              layer: transformed
              source_variable: DFF
              transformation: rolling_zscore
              window: 252
              frequency: daily
        """,
        "derived_variables.yaml": """
            sig:
              layer: derived
              type: signal
              inputs: [A]
              frequency: daily
        """,
    })
    cat = VariableCatalog.load(tmp_path)
    assert cat.get_used_by("DFF") == ["A"]
    assert cat.get_used_by_transitive("DFF") == ["A", "sig"]


# ---------------------------------------------------------------------------
# Real project catalog
# ---------------------------------------------------------------------------


def test_real_catalog_loads_strict() -> None:
    """The real configs/data/ catalog must load cleanly in strict mode.

    This is the contract: the catalog is the source of truth, so it should
    always validate. If this test fails after a config change, fix the YAML
    rather than relaxing the test.
    """
    project_root = Path(__file__).resolve().parents[1]
    catalog_root = project_root / "configs" / "data"
    if not catalog_root.exists():
        pytest.skip("configs/data/ not present in this checkout")
    cat = VariableCatalog.load(catalog_root, strict=True)
    # Sanity: must contain the three Phase 1 signals.
    names = set(cat.names())
    expected_signals = {"fx_carry_signal", "rates_trend_signal", "equity_momentum_signal"}
    missing = expected_signals - names
    assert not missing, f"Expected signals missing from catalog: {missing}"
