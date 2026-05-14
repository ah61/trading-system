"""Tests for CachedSource (Milestone 5.4).

Self-contained: uses a FakeSource that records calls and an in-tmp_path DataStore.
No network access; no real FRED/Yahoo calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.data.cached_source import CachedSource, CacheError
from src.data.cleaning import DataCleaner
from src.data.sources.base import DataSource
from src.data.store import DataStore
from src.exceptions import DataGapError


# ---------------------------------------------------------------------------
# Fake source — records every fetch call, returns synthetic data
# ---------------------------------------------------------------------------


class FakeSource(DataSource):
    """Records calls to fetch(); returns synthetic business-day series."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self._fail_with = fail_with

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((ticker, start, end))
        if self._fail_with is not None:
            raise self._fail_with
        idx = pd.date_range(start, end, freq="B", tz="UTC")
        # Reproducible synthetic close prices.
        rng = np.random.default_rng(abs(hash((ticker, start, end))) % (2**32))
        close = 100 + rng.standard_normal(len(idx)).cumsum()
        df = pd.DataFrame({"close": close.astype(np.float64), "source": "fake"}, index=idx)
        df.index.name = "timestamp"
        return df

    def get_metadata(self, ticker: str) -> dict[str, Any]:
        return {
            "source": "fake",
            "ticker": ticker,
            "frequency": "daily",
            "known_limitations": [],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> DataStore:
    return DataStore(data_dir=tmp_path / "data")


def _make_cached(tmp_path: Path, *, with_cleaner: bool = False) -> tuple[CachedSource, FakeSource]:
    src = FakeSource()
    store = _make_store(tmp_path)
    cleaner = DataCleaner() if with_cleaner else None
    return CachedSource(source=src, store=store, source_name="fake", cleaner=cleaner), src


# ---------------------------------------------------------------------------
# Cache miss → fetch → store
# ---------------------------------------------------------------------------


def test_first_call_fetches_and_caches(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    df = cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    assert len(fake.calls) == 1
    assert len(df) > 0
    # Verify the raw table is now in the store.
    listed = cached.store.list_available(layer="raw")
    assert "AAA" in set(listed["ticker"])


def test_second_call_uses_cache(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    cached.fetch_or_load("AAA", date(2024, 2, 1), date(2024, 5, 31), frequency="daily")
    # Second call covered by cache; underlying source called only once.
    assert len(fake.calls) == 1


def test_cache_returns_sliced_range(tmp_path: Path) -> None:
    cached, _ = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    df = cached.fetch_or_load(
        "AAA", date(2024, 3, 1), date(2024, 3, 31), frequency="daily"
    )
    assert df.index.min() >= pd.Timestamp("2024-03-01", tz="UTC")
    assert df.index.max() <= pd.Timestamp("2024-03-31", tz="UTC") + pd.Timedelta(days=1)


# ---------------------------------------------------------------------------
# Range extension: superset triggers refetch
# ---------------------------------------------------------------------------


def test_wider_range_triggers_refetch(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 3, 1), date(2024, 5, 31), frequency="daily")
    assert len(fake.calls) == 1
    # Request a wider range — cache doesn't cover it, must refetch.
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    assert len(fake.calls) == 2


def test_refetch_with_superset_overwrites_raw(tmp_path: Path) -> None:
    cached, _ = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 3, 1), date(2024, 5, 31), frequency="daily")
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    # Read the full raw table directly and confirm it covers the wider range.
    raw = cached.store.read(source="fake", ticker="AAA", frequency="daily", layer="raw")
    assert raw.index.min() <= pd.Timestamp("2024-01-02", tz="UTC")
    assert raw.index.max() >= pd.Timestamp("2024-06-27", tz="UTC")


# ---------------------------------------------------------------------------
# force_refresh
# ---------------------------------------------------------------------------


def test_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    cached.fetch_or_load(
        "AAA",
        date(2024, 1, 1),
        date(2024, 6, 30),
        frequency="daily",
        force_refresh=True,
    )
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# Multiple tickers don't collide
# ---------------------------------------------------------------------------


def test_multiple_tickers_isolated(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    cached.fetch_or_load("BBB", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    assert len(fake.calls) == 2
    cached.fetch_or_load("AAA", date(2024, 2, 1), date(2024, 3, 1), frequency="daily")
    # Still 2 — AAA was a cache hit.
    assert len(fake.calls) == 2


def test_multiple_frequencies_isolated(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="monthly")
    assert len(fake.calls) == 2  # different frequencies are different tables


# ---------------------------------------------------------------------------
# Adjusted layer (cleaning)
# ---------------------------------------------------------------------------


def test_with_cleaner_writes_adjusted(tmp_path: Path) -> None:
    cached, _ = _make_cached(tmp_path, with_cleaner=True)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    listed = cached.store.list_available()
    layers = set(listed["layer"])
    assert "raw" in layers
    assert "adjusted" in layers


def test_request_adjusted_layer_returns_cleaned(tmp_path: Path) -> None:
    cached, _ = _make_cached(tmp_path, with_cleaner=True)
    df = cached.fetch_or_load(
        "AAA",
        date(2024, 1, 1),
        date(2024, 6, 30),
        frequency="daily",
        layer="adjusted",
    )
    # Cleaned frames have these added columns per DataCleaner.clean().
    assert "is_outlier" in df.columns
    assert "fill_type" in df.columns
    assert "clean_version" in df.columns


def test_request_adjusted_without_cleaner_raises(tmp_path: Path) -> None:
    cached, _ = _make_cached(tmp_path, with_cleaner=False)
    with pytest.raises(ValueError, match="Cannot return adjusted layer"):
        cached.fetch_or_load(
            "AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily", layer="adjusted"
        )


# ---------------------------------------------------------------------------
# Errors do not corrupt cache
# ---------------------------------------------------------------------------


def test_failed_cleaning_does_not_write_adjusted(tmp_path: Path) -> None:
    """If DataCleaner.clean() raises, adjusted is NOT written but raw is.

    We force a cleaning failure by feeding the cleaner a dataframe with a large
    NaN gap. Easiest way: monkey-patch the FakeSource to return data with a gap.
    """
    src = FakeSource()
    store = _make_store(tmp_path)
    cached = CachedSource(source=src, store=store, source_name="fake", cleaner=DataCleaner())

    # Patch fetch to return a dataframe with a 5-day NaN gap, which DataCleaner
    # rejects (max_ffill_days=3).
    original_fetch = src.fetch

    def fetch_with_gap(ticker: str, start: date, end: date) -> pd.DataFrame:
        df = original_fetch(ticker, start, end)
        # Force 5 consecutive NaNs in the middle.
        df.iloc[20:25, df.columns.get_loc("close")] = np.nan
        return df

    src.fetch = fetch_with_gap  # type: ignore[method-assign]

    with pytest.raises(DataGapError):
        cached.fetch_or_load(
            "AAA",
            date(2024, 1, 1),
            date(2024, 6, 30),
            frequency="daily",
            layer="adjusted",
        )

    listed = cached.store.list_available()
    layers = set(listed["layer"])
    # Raw was written; adjusted was NOT.
    assert "raw" in layers
    assert "adjusted" not in layers


# ---------------------------------------------------------------------------
# Refusing to shrink cached range
# ---------------------------------------------------------------------------


def test_non_superset_refetch_is_rejected(tmp_path: Path) -> None:
    """If something forces a refetch with a strictly narrower range, refuse.

    The motivating scenario: a caller passes force_refresh=True but with a smaller
    date range than what's already cached. Honouring it would corrupt the cache
    by shrinking the stored range. CacheError protects against this.
    """
    cached, _ = _make_cached(tmp_path)
    cached.fetch_or_load("AAA", date(2024, 1, 1), date(2024, 6, 30), frequency="daily")
    with pytest.raises(CacheError, match="non-superset"):
        cached.fetch_or_load(
            "AAA",
            date(2024, 3, 1),
            date(2024, 4, 30),
            frequency="daily",
            force_refresh=True,
        )


# ---------------------------------------------------------------------------
# Compatibility shim: fetch() works as a drop-in DataSource method
# ---------------------------------------------------------------------------


def test_fetch_shim_defaults_to_daily(tmp_path: Path) -> None:
    cached, fake = _make_cached(tmp_path)
    df = cached.fetch("AAA", date(2024, 1, 1), date(2024, 6, 30))
    assert len(df) > 0
    # Cache hit on second call confirms shim wrote with frequency='daily'.
    cached.fetch("AAA", date(2024, 1, 1), date(2024, 6, 30))
    assert len(fake.calls) == 1
