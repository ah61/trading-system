"""Smoke test for scripts/backtest_strategy.py."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from textwrap import dedent

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backtest_strategy import run_backtest
from src.data.sources.base import DataSource
from src.data.store import DataStore
from src.data.variable_catalog import VariableCatalog


class _StubSource(DataSource):
    """Daily close stub with a gentle random walk for backtest warm-up."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._rng = np.random.default_rng(42)

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        index = pd.bdate_range(start, end, tz="UTC")
        steps = self._rng.normal(0.0, 0.002, size=len(index))
        close = 100.0 * np.exp(np.cumsum(steps))
        df = pd.DataFrame({"close": close.astype(np.float64)}, index=index)
        self.validate(df)
        return df

    def get_metadata(self, ticker: str) -> dict:
        return {
            "source": self.name,
            "ticker": ticker,
            "frequency": "daily",
            "known_limitations": "stub",
        }


def _write_catalog(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).strip() + "\n", encoding="utf-8")


def test_backtest_strategy_runs_end_to_end(tmp_path: Path) -> None:
    catalog_root = tmp_path / "data_catalog"
    configs_dir = tmp_path / "configs"
    data_dir = tmp_path / "data_store"
    reports_root = tmp_path / "reports"

    _write_catalog(
        catalog_root,
        "variables/market.yaml",
        """
            TLT_CLOSE:
              layer: raw
              source: yahoo
              ticker: TLT
              frequency: daily
        """,
    )
    _write_catalog(
        configs_dir,
        "signals/rates_trend.yaml",
        """
            signal:
              name: rates_trend
              asset_class: rates
              signal_type: trend
              frequency: daily
            parameters:
              variable: TLT_CLOSE
              fast_window: 50
              slow_window: 200
              scale_by_distance: false
        """,
    )

    stub = _StubSource(name="yahoo")
    store = DataStore(data_dir=data_dir)
    catalog = VariableCatalog.load(
        catalog_root, sources={"yahoo": stub}, store=store,
    )

    # Minimum calendar for default train_window (1260) + test_window (252) business days.
    args = argparse.Namespace(
        signal="rates_trend",
        start=date(2019, 1, 1),
        end=date(2024, 12, 31),
        method="expanding",
        refresh=False,
        no_tearsheet=True,
    )

    run = run_backtest(
        args,
        catalog=catalog,
        reports_root=reports_root,
        catalog_root=catalog_root,
        data_dir=data_dir,
        configs_dir=configs_dir,
    )

    assert run.path.exists()
    assert reports_root in run.path.parents or run.path.is_relative_to(reports_root)
    strategies_root = reports_root / "strategies"
    assert strategies_root in run.path.parents

    manifest_path = run.path / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["config"]["signal"] == "rates_trend"

    results_md = run.path / "results.md"
    assert results_md.exists()
    assert results_md.read_text(encoding="utf-8").strip()

    tearsheet = run.plots_dir / "rates_trend_tearsheet.png"
    assert not tearsheet.exists()
