"""Tests for transformation_executor against a stateful catalogue."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from textwrap import dedent

import numpy as np
import pandas as pd
import pytest

from src.data import transformations as T
from src.data.sources.base import DataSource
from src.data.store import DataStore
from src.data.transformation_executor import TRANSFORM_REGISTRY, execute_transformation
from src.data.variable_catalog import VariableCatalog


class _StubSource(DataSource):
    def __init__(self, name: str, frequency: str) -> None:
        self.name = name
        self.frequency = frequency

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        if self.frequency == "daily":
            index = pd.bdate_range(start, end, tz="UTC")
        else:
            index = pd.date_range(start, end, freq="ME", tz="UTC")
        close = pd.Series(np.linspace(100.0, 100.0 + len(index), len(index)), index=index)
        df = pd.DataFrame({"close": close.astype(np.float64)})
        self.validate(df)
        return df

    def get_metadata(self, ticker: str) -> dict:
        return {"source": self.name, "ticker": ticker, "frequency": self.frequency, "known_limitations": ""}


def _write_catalog(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def _build_cat(tmp_path: Path, store: DataStore, files: dict[str, str]) -> VariableCatalog:
    _write_catalog(tmp_path, files)
    return VariableCatalog.load(
        tmp_path,
        sources={"yahoo": _StubSource("yahoo", "daily"), "fred": _StubSource("fred", "monthly")},
        store=store,
    )


@pytest.fixture
def tmp_store(tmp_path: Path) -> DataStore:
    return DataStore(data_dir=tmp_path)


def test_executor_resolves_single_input_transformation(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
        "variables/transformations.yaml": """
            TLT_LOG_RET:
              layer: transformed
              source_variable: TLT_CLOSE
              transformation: log_return
              window: 1
              frequency: daily
        """,
    })
    spec = cat.get_spec("TLT_LOG_RET")
    out = execute_transformation(
        spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 6, 30), force_refresh=False,
    )
    raw = cat.get("TLT_CLOSE", start=date(2020, 1, 1), end=date(2020, 6, 30))
    expected = T.log_return(raw, window=1).reindex(out.index)
    pd.testing.assert_series_equal(out, expected, check_names=False, atol=1e-12)
    assert out.name == "TLT_LOG_RET"


def test_executor_resolves_chain_via_catalogue(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
        "variables/transformations.yaml": """
            TLT_LOG_RET:
              layer: transformed
              source_variable: TLT_CLOSE
              transformation: log_return
              window: 1
              frequency: daily
            TLT_VOL_63D:
              layer: transformed
              source_variable: TLT_LOG_RET
              transformation: rolling_vol
              window: 63
              frequency: daily
              annualised: true
        """,
    })
    spec = cat.get_spec("TLT_VOL_63D")
    out = execute_transformation(
        spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 12, 31), force_refresh=False,
    )
    log_ret = cat.get("TLT_LOG_RET", start=date(2020, 1, 1), end=date(2020, 12, 31))
    expected = T.rolling_vol(log_ret, window=63, annualised=True, frequency="daily")
    expected = expected.reindex(out.index)
    pd.testing.assert_series_equal(out, expected, check_names=False, atol=1e-12)


def test_executor_raises_on_frequency_mismatch(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
        "variables/transformations.yaml": """
            TLT_LOG_RET_MONTHLY:
              layer: transformed
              source_variable: TLT_CLOSE
              transformation: log_return
              window: 1
              frequency: monthly
        """,
    })
    spec = cat.get_spec("TLT_LOG_RET_MONTHLY")
    with pytest.raises(ValueError, match="resample"):
        execute_transformation(
            spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 12, 31), force_refresh=False,
        )


def test_executor_raises_on_unknown_transformation(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/transformations.yaml": """
            BAD:
              layer: transformed
              source_variable: TLT_CLOSE
              transformation: made_up
              frequency: daily
        """,
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
    })
    spec = cat.get_spec("BAD")
    with pytest.raises(ValueError, match="TRANSFORM_REGISTRY|made_up"):
        execute_transformation(
            spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 3, 31), force_refresh=False,
        )


def test_executor_raises_on_derived_layer_spec(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "derived_variables.yaml": """
            sig:
              layer: derived
              type: signal
              inputs: [TLT_CLOSE]
              frequency: daily
        """,
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
    })
    spec = cat.get_spec("sig")
    with pytest.raises(ValueError, match="layer='transformed'"):
        execute_transformation(
            spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 3, 31), force_refresh=False,
        )


def test_executor_difference_resolves_both_sources(tmp_path: Path, tmp_store: DataStore) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/macro.yaml": """
            GS10:
              layer: raw
              source: fred
              series_id: GS10
              frequency: monthly
            GS2:
              layer: raw
              source: fred
              series_id: GS2
              frequency: monthly
        """,
        "variables/transformations.yaml": """
            GS10_GS2_SLOPE:
              layer: transformed
              sources: [GS10, GS2]
              transformation: difference
              frequency: monthly
        """,
    })
    spec = cat.get_spec("GS10_GS2_SLOPE")
    out = execute_transformation(
        spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 12, 31), force_refresh=False,
    )
    gs10 = cat.get("GS10", frequency="monthly", start=date(2020, 1, 1), end=date(2020, 12, 31))
    gs2 = cat.get("GS2", frequency="monthly", start=date(2020, 1, 1), end=date(2020, 12, 31))
    expected = T.difference(gs10, gs2).reindex(out.index)
    pd.testing.assert_series_equal(out, expected, check_names=False, atol=1e-12)


def test_executor_difference_raises_if_sources_differ_in_frequency(
    tmp_path: Path, tmp_store: DataStore,
) -> None:
    cat = _build_cat(tmp_path, tmp_store, {
        "variables/macro.yaml": """
            GS10:
              layer: raw
              source: fred
              series_id: GS10
              frequency: monthly
        """,
        "variables/market.yaml": """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
        "variables/transformations.yaml": """
            BAD_DIFF:
              layer: transformed
              sources: [GS10, TLT_CLOSE]
              transformation: difference
              frequency: monthly
        """,
    })
    spec = cat.get_spec("BAD_DIFF")
    with pytest.raises(ValueError, match="same frequency"):
        execute_transformation(
            spec, cat, frequency=None, start=date(2020, 1, 1), end=date(2020, 12, 31), force_refresh=False,
        )
