"""Tests for the variable catalog (Milestone 5.3).

Self-contained: builds tiny YAML fixtures in tmp_path; does not load the real
project catalog. The real catalog is exercised by import-time sanity in
`test_real_catalog_loads_strict` at the bottom.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from textwrap import dedent
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.data.sources.base import DataSource
from src.data.store import DataStore
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
# Stateful data access (5.7)
# ---------------------------------------------------------------------------


_DFF_MACRO = {
    "variables/macro.yaml": """
        DFF:
          layer: raw
          source: FRED
          series_id: DFF
          frequency: daily
    """,
}

_GS10_MONTHLY = {
    "variables/macro.yaml": """
        GS10:
          layer: raw
          source: FRED
          series_id: GS10
          frequency: monthly
    """,
}

_RAW_PLUS_TRANSFORMED = {
    **_DFF_MACRO,
    "variables/transformations.yaml": """
        DFF_Z:
          layer: transformed
          source_variable: DFF
          transformation: rolling_zscore
          window: 252
          frequency: daily
    """,
}


class _StubSource(DataSource):
    """Deterministic in-memory source for stateful catalogue tests."""

    def __init__(
        self,
        name: str,
        frequency: str,
        value_fn: Callable[[pd.Timestamp], float] | None = None,
        *,
        month_end_min: date | None = None,
        month_end_max: date | None = None,
    ) -> None:
        self.name = name
        self.frequency = frequency
        self._value_fn = value_fn or self._default_value_fn
        self._month_end_min = month_end_min
        self._month_end_max = month_end_max
        self.call_count = 0
        self.last_fetch: tuple[str, date, date] | None = None

    @staticmethod
    def _default_value_fn(ts: pd.Timestamp) -> float:
        return float(hash(ts.date()) % 10_000) / 100.0

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        self.last_fetch = (ticker, start, end)
        if self.frequency == "daily":
            index = pd.bdate_range(start, end, tz="UTC")
        elif self.frequency == "monthly":
            index = pd.date_range(start, end, freq="ME", tz="UTC")
            if self._month_end_min is not None:
                floor = pd.Timestamp(self._month_end_min, tz="UTC")
                index = index[index >= floor]
            if self._month_end_max is not None:
                ceiling = pd.Timestamp(self._month_end_max, tz="UTC")
                index = index[index <= ceiling]
        else:
            raise ValueError(f"Unsupported stub frequency: {self.frequency!r}")
        values = np.array([self._value_fn(ts) for ts in index], dtype=np.float64)
        df = pd.DataFrame({"close": values}, index=index)
        self.validate(df)
        return df

    def get_metadata(self, ticker: str) -> dict[str, Any]:
        return {
            "source": self.name,
            "ticker": ticker,
            "frequency": self.frequency,
            "known_limitations": "stub",
        }


@pytest.fixture
def tmp_store(tmp_path: Path) -> DataStore:
    return DataStore(data_dir=tmp_path)


def _build_cat(
    tmp_path: Path,
    catalog_files: dict[str, str],
    sources: dict[str, DataSource],
    store: DataStore,
) -> VariableCatalog:
    _write_catalog(tmp_path, catalog_files)
    return VariableCatalog.load(tmp_path, sources=sources, store=store)


def test_get_returns_series_for_raw_variable(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    series = cat.get("DFF", start=date(2020, 1, 1), end=date(2020, 12, 31))
    assert isinstance(series, pd.Series)
    assert series.dtype == np.float64
    assert series.name == "DFF"
    assert len(series) > 0


def test_get_uses_native_frequency_when_unspecified(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    series = cat.get("DFF", start=date(2020, 1, 1), end=date(2020, 3, 31))
    expected = pd.bdate_range(date(2020, 1, 1), date(2020, 3, 31), tz="UTC")
    assert len(series) == len(expected)
    assert series.index.equals(expected)


def test_get_resamples_daily_to_monthly(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 12, 31)
    daily = cat.get("DFF", start=start, end=end)
    monthly = cat.get("DFF", frequency="monthly", start=start, end=end)
    assert 10 <= len(monthly) <= 12
    for month_end in monthly.index:
        month_start = month_end.replace(day=1)
        daily_slice = daily.loc[month_start:month_end]
        assert monthly.loc[month_end] == daily_slice.iloc[-1]


def test_get_forward_fills_monthly_to_daily(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="monthly")
    cat = _build_cat(tmp_path, _GS10_MONTHLY, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 12, 31)
    series = cat.get("GS10", frequency="daily", start=start, end=end)
    expected_index = pd.bdate_range(start, end, tz="UTC")
    assert series.index.equals(expected_index)
    assert not series.isna().any()
    feb_days = series.loc["2020-02-03":"2020-02-29"]
    assert len(feb_days) >= 2
    assert feb_days.iloc[0] == feb_days.iloc[1]


def test_get_forward_fill_anchors_on_requested_range(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    stub = _StubSource(name="fred", frequency="monthly")
    cat = _build_cat(tmp_path, _GS10_MONTHLY, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 12, 31)
    series = cat.get("GS10", frequency="daily", start=start, end=end)
    expected_index = pd.bdate_range(start, end, tz="UTC")
    assert series.index.equals(expected_index)
    assert not series.isna().any()


def test_get_forward_fill_raises_when_request_predates_source(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    stub = _StubSource(
        name="fred", frequency="monthly", month_end_min=date(2020, 3, 31),
    )
    cat = _build_cat(tmp_path, _GS10_MONTHLY, sources={"fred": stub}, store=tmp_store)
    with pytest.raises(CatalogError, match="does not cover"):
        cat.get(
            "GS10",
            frequency="daily",
            start=date(2020, 1, 1),
            end=date(2020, 12, 31),
        )


def test_get_forward_fill_extends_past_last_print(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    stub = _StubSource(
        name="fred",
        frequency="monthly",
        month_end_max=date(2020, 9, 30),
    )
    cat = _build_cat(tmp_path, _GS10_MONTHLY, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 12, 31)
    series = cat.get("GS10", frequency="daily", start=start, end=end)
    expected_index = pd.bdate_range(start, end, tz="UTC")
    assert series.index.equals(expected_index)
    sep_value = series.loc["2020-09-30"]
    oct_onward = series.loc["2020-10-01":]
    assert len(oct_onward) > 0
    assert (oct_onward == sep_value).all()


def test_get_raises_in_registry_only_mode(tmp_path: Path) -> None:
    _write_catalog(tmp_path, _DFF_MACRO)
    cat = VariableCatalog.load(tmp_path)
    with pytest.raises(CatalogError, match="registry-only mode"):
        cat.get("DFF")


def test_get_raises_for_unknown_variable(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    with pytest.raises(CatalogError, match="Unknown variable"):
        cat.get("DOES_NOT_EXIST")


def test_get_raises_for_transformed_variable(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(
        tmp_path, _RAW_PLUS_TRANSFORMED, sources={"fred": stub}, store=tmp_store,
    )
    with pytest.raises(ValueError, match="layer='transformed'"):
        cat.get("DFF_Z")


def test_universe_expansion_produces_per_ticker_specs(tmp_path: Path) -> None:
    universe_dir = tmp_path / "universes"
    universe_dir.mkdir(parents=True)
    (universe_dir / "test_universe.yaml").write_text(
        dedent("""
            template:
              layer: raw
              source: yahoo
              frequency: daily
              instrument_type: equity
              adjustment: auto_adjust
              variable_name_pattern: "{ticker}_CLOSE"
            tickers:
              - AAA
              - BBB
              - CCC
        """).strip() + "\n",
        encoding="utf-8",
    )
    cat = VariableCatalog.load(tmp_path)
    names = set(cat.names())
    assert {"AAA_CLOSE", "BBB_CLOSE", "CCC_CLOSE"} <= names
    spec = cat.get_spec("AAA_CLOSE")
    assert spec.layer == "raw"
    assert spec.spec["ticker"] == "AAA"
    assert spec.spec["source"] == "yahoo"


def test_universe_expanded_variable_works_with_get(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    universe_dir = tmp_path / "universes"
    universe_dir.mkdir(parents=True)
    (universe_dir / "test_universe.yaml").write_text(
        dedent("""
            template:
              layer: raw
              source: yahoo
              frequency: daily
              instrument_type: equity
              adjustment: auto_adjust
              variable_name_pattern: "{ticker}_CLOSE"
            tickers:
              - AAA
              - BBB
              - CCC
        """).strip() + "\n",
        encoding="utf-8",
    )
    stub = _StubSource(name="yahoo", frequency="daily")
    cat = VariableCatalog.load(tmp_path, sources={"yahoo": stub}, store=tmp_store)
    series = cat.get("AAA_CLOSE", start=date(2020, 1, 1), end=date(2020, 12, 31))
    assert isinstance(series, pd.Series)
    assert series.name == "AAA_CLOSE"
    assert len(series) > 0


def test_get_force_refresh_bypasses_cache(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 1, 31)
    cat.get("DFF", start=start, end=end)
    cat.get("DFF", start=start, end=end)
    assert stub.call_count == 1
    cat.get("DFF", start=start, end=end, force_refresh=True)
    assert stub.call_count == 2


def test_get_force_refresh_default_false(tmp_path: Path, tmp_store: DataStore) -> None:
    stub = _StubSource(name="fred", frequency="daily")
    cat = _build_cat(tmp_path, _DFF_MACRO, sources={"fred": stub}, store=tmp_store)
    start, end = date(2020, 1, 1), date(2020, 1, 31)
    cat.get("DFF", start=start, end=end)
    cat.get("DFF", start=start, end=end)
    assert stub.call_count == 1


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
