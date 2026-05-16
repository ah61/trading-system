"""Cached wrapper around a `DataSource` that persists fetched series to a `DataStore`.

Behaviour
---------
Given a bare ``DataSource`` (e.g. ``FREDSource``, ``YahooSource``) and a
``DataStore``, ``CachedSource`` exposes a ``fetch_or_load`` method that:

1. Checks the raw layer in ``DataStore`` for the requested ``(source, ticker, frequency)``.
2. If present and the cached date range covers ``[start, end]``: read from the store.
3. Otherwise: fetch from the underlying source, write to ``raw.duckdb`` (with
   write-if-missing-else-replace semantics — see Notes), and return the slice.

If a ``DataCleaner`` is provided, cleaning runs once after a fresh fetch and the
cleaned DataFrame is written to ``adjusted.duckdb`` at version 1. Reads from the
"adjusted" layer return the cleaned frame; reads from "raw" return the unmodified
fetch.

Notes
-----
- ``DataStore.write_raw`` is append-only and raises if the table already exists,
  per CONVENTIONS §1 (raw is immutable). To support range extension within a
  ticker, ``CachedSource`` does not call ``write_raw`` directly; it uses a
  lower-level ``_force_overwrite_raw`` helper that drops and re-creates the raw
  table. This intentionally narrows the "raw is immutable" guarantee: raw is
  immutable *for a given fetch* but may be overwritten if a later fetch
  legitimately retrieves a wider date range. The new raw frame is required to
  be a strict superset of the previous one along the date axis; otherwise the
  cache rejects the write to protect against data corruption.
- This module does not touch ``derived.duckdb``. Derived storage is for
  signal/feature output and is the concern of evaluation/portfolio code.
- ``force_refresh=True`` short-circuits the cache check and re-fetches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.data.cleaning import DataCleaner
from src.data.sources.base import DataSource
from src.data.store import DataStore, _quote_ident, _table_exists
from src.exceptions import DataValidationError, StorageError, TradingSystemError


_ADJUSTED_DEFAULT_VERSION = 1

# Publication-lag tolerance per native frequency. The cache is considered
# to cover [start, end] if the most-recent cached observation is within
# this many days of `end`. Generous values absorb publication delays
# (FRED monthly typically prints with ~30-day lag; quarterly even more).
_PUBLICATION_LAG_DAYS: dict[str, int] = {
    "daily": 1,
    "weekly": 7,
    "monthly": 45,
    "quarterly": 120,
}


def _publication_lag_days(frequency: str) -> int:
    """Return the publication-lag tolerance in calendar days for a frequency.

    Unknown frequencies default to 1 day (conservative — strict daily
    behaviour). Caller is expected to validate frequency upstream.
    """
    return _PUBLICATION_LAG_DAYS.get(frequency.lower(), 1)


class CacheError(TradingSystemError):
    """Raised on cache invariants violations (e.g. range shrink on overwrite)."""


@dataclass(frozen=True, slots=True)
class CachedSource:
    """A `DataSource`-compatible wrapper that caches fetches in a `DataStore`.

    Args:
        source: The underlying data source (e.g. ``FREDSource()``).
        store: The ``DataStore`` to cache into.
        source_name: Short identifier for the source in store table names (e.g.
            ``"fred"``, ``"yahoo"``). Must be lowercase alphanumeric/underscore.
        cleaner: Optional ``DataCleaner``. If provided, fresh fetches are cleaned
            and the cleaned frame is written to the adjusted layer.
    """

    source: DataSource
    store: DataStore
    source_name: str
    cleaner: DataCleaner | None = None

    # ----- public API -----------------------------------------------------

    def fetch_or_load(
        self,
        ticker: str,
        start: date,
        end: date,
        frequency: str,
        *,
        force_refresh: bool = False,
        layer: str = "raw",
    ) -> pd.DataFrame:
        """Return the requested series, fetching only if not already cached.

        Args:
            ticker: Source-specific series identifier (e.g. ``"DFF"``, ``"TLT"``).
            start: Start date (inclusive).
            end: End date (inclusive).
            frequency: Series frequency (e.g. ``"daily"``, ``"monthly"``). Used as
                part of the store table name; must match how downstream consumers
                identify the series.
            force_refresh: If True, bypass cache check and refetch.
            layer: Which layer to return. ``"raw"`` (default) returns the
                unmodified fetched data. ``"adjusted"`` returns the cleaned
                version (requires a ``cleaner``).

        Returns:
            DataFrame with UTC ``DatetimeIndex`` and at least a ``close`` column,
            sliced to ``[start, end]``.
        """
        if layer not in {"raw", "adjusted"}:
            raise ValueError(f"layer must be 'raw' or 'adjusted', got {layer!r}.")
        if layer == "adjusted" and self.cleaner is None:
            raise ValueError(
                "Cannot return adjusted layer without a configured DataCleaner."
            )

        if not force_refresh and self._raw_covers_range(ticker, frequency, start, end):
            logger.info(
                "[CachedSource] HIT  {}:{} ({}..{}) — reading from store",
                self.source_name, ticker, start, end,
            )
            return self._read_from_store(ticker, frequency, start, end, layer=layer)

        logger.info(
            "[CachedSource] MISS {}:{} — fetching from underlying source",
            self.source_name, ticker,
        )
        return self._fetch_and_cache(ticker, start, end, frequency, return_layer=layer)

    # Convenience: a DataSource-compatible signature so CachedSource is mostly
    # drop-in replaceable in code that just calls .fetch(ticker, start, end).
    # Callers that want the cache benefits should prefer fetch_or_load.
    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Compatibility shim. Calls ``fetch_or_load`` with ``frequency='daily'``.

        For non-daily series, use ``fetch_or_load`` directly with the correct
        frequency.
        """
        return self.fetch_or_load(ticker, start, end, frequency="daily")

    # ----- internals ------------------------------------------------------

    @staticmethod
    def _sanitize_ticker(ticker: str) -> str:
        """Sanitize a ticker for use as a DuckDB identifier.

        `DataStore._validate_ident` enforces ``[A-Za-z0-9_]`` because identifiers
        appear directly in SQL. Some real-world tickers contain ``=`` (Yahoo FX,
        e.g. ``EURUSD=X``), ``-`` (BRK-B), ``.`` (ticker classes), or ``^``
        (indices). Sanitisation here is one-way and only used for store keys;
        the original ticker is still passed to the underlying source's
        ``fetch()`` so the vendor sees what it expects.

        Note: this mapping must be deterministic and collision-free for the
        ticker set in use. If two distinct tickers were to sanitise to the same
        store key, we'd have silent data crossover. The current mapping (=,-,.,^
        → _) is collision-free for known Stage 1 tickers but should be
        revisited if exotic symbols are added.
        """
        out = ticker
        for bad in ("=", "-", ".", "^", "/"):
            out = out.replace(bad, "_")
        return out

    def _raw_table_name(self, ticker: str, frequency: str) -> str:
        # Reuse the store's table-name convention without poking at internals.
        return self.store._raw_table_name(
            self.source_name, self._sanitize_ticker(ticker), frequency
        )

    def _raw_covers_range(
        self, ticker: str, frequency: str, start: date, end: date
    ) -> bool:
        """True if the raw cache exists and covers [start, end].

        "Covers" means: the cached range starts at or before ``start`` and ends
        at or after the last business day in ``[start, end]``. The business-day
        check matters because weekly/monthly data and weekend endpoints often
        mean the latest stored business day is several days before the calendar
        ``end``; a strict end >= end check produces spurious cache misses.
        """
        table = self._raw_table_name(ticker, frequency)
        path = self.store.raw_db_path
        if not path.exists():
            return False
        try:
            with duckdb.connect(str(path)) as con:
                if not _table_exists(con, table):
                    return False
                row = con.execute(
                    f"SELECT MIN(timestamp), MAX(timestamp) FROM {_quote_ident(table)}"
                ).fetchone()
        except Exception as e:
            logger.warning("[CachedSource] cache probe failed for {}: {}", table, e)
            return False
        if not row or row[0] is None or row[1] is None:
            return False

        cached_start = self._timestamp_to_utc_date(row[0])
        cached_end = self._timestamp_to_utc_date(row[1])

        # Find the actual business days within [start, end]. The cache covers
        # the request iff every business day in that range is at or after the
        # cached start AND at or before the cached end. A calendar start/end
        # that falls on a weekend or holiday should not cause spurious misses.
        bdays_in_range = pd.bdate_range(start=start, end=end)
        if len(bdays_in_range) == 0:
            # No business days requested at all — trivially covered.
            return True
        required_start = bdays_in_range[0].date()
        required_end = bdays_in_range[-1].date()

        # Slack at both boundaries. ``pd.bdate_range`` returns Mon–Fri but does
        # not exclude market holidays, so a "required" date can fall on a closed
        # market day (e.g. Jan 1, July 4, Thanksgiving) where the source returns
        # no data — and the cache correctly contains no data for that day. A
        # strict boundary check then produces spurious misses every time a
        # holiday lands within a few days of the request boundary.
        #
        # End: 1 day of slack covers the timezone storage offset that DuckDB
        # applies (timestamptz stored in server-local tz can shift dates by 1).
        # Start: up to 5 business days of slack covers New Year's, MLK Day,
        # Christmas, and other multi-day market closures at year boundaries.
        start_slack = pd.tseries.offsets.BDay(5)
        # End-side slack: max of tz roundtrip slack (1 day, see comments above)
        # and publication-lag slack for the requested frequency.
        end_slack_days = max(1, _publication_lag_days(frequency))
        start_threshold = (pd.Timestamp(required_start) + start_slack).date()
        end_threshold = required_end - pd.Timedelta(days=end_slack_days).to_pytimedelta()
        return cached_start <= start_threshold and cached_end >= end_threshold

    @staticmethod
    def _timestamp_to_utc_date(value: Any) -> date:
        """Normalise an arbitrary timestamp value (possibly tz-aware, possibly
        an ISO string) to a UTC calendar date."""
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.date()

    def _read_from_store(
        self, ticker: str, frequency: str, start: date, end: date, *, layer: str
    ) -> pd.DataFrame:
        safe_ticker = self._sanitize_ticker(ticker)
        if layer == "raw":
            df = self.store.read(
                source=self.source_name, ticker=safe_ticker, frequency=frequency, layer="raw"
            )
        else:
            df = self.store.read(
                source=self.source_name,
                ticker=safe_ticker,
                frequency=frequency,
                layer="adjusted",
                version="latest",
            )
        return self._slice_range(df, start, end)

    def _fetch_and_cache(
        self,
        ticker: str,
        start: date,
        end: date,
        frequency: str,
        *,
        return_layer: str,
    ) -> pd.DataFrame:
        # Fetch fresh from the source. Source-level validation has already run.
        df = self.source.fetch(ticker, start, end)

        # Write raw with overwrite-if-superset semantics.
        self._write_raw_overwrite_if_superset(df, ticker, frequency)

        # Run cleaning if configured. Cleaning failures must NOT corrupt the
        # adjusted layer — propagate the error after cleaning the raw layer
        # (raw write already succeeded). The caller will see DataGapError or
        # similar and can decide what to do.
        cleaned_df: pd.DataFrame | None = None
        if self.cleaner is not None:
            try:
                cleaned_df = self.cleaner.clean(df)
            except Exception as e:
                logger.error(
                    "[CachedSource] cleaning failed for {}:{} — raw cached, "
                    "adjusted NOT written: {}",
                    self.source_name, ticker, e,
                )
                if return_layer == "adjusted":
                    raise
                # Otherwise return raw slice; user didn't ask for adjusted.
                return self._slice_range(df, start, end)

            self.store.write_adjusted(
                cleaned_df,
                source=self.source_name,
                ticker=self._sanitize_ticker(ticker),
                frequency=frequency,
                version=_ADJUSTED_DEFAULT_VERSION,
            )

        if return_layer == "raw":
            return self._slice_range(df, start, end)
        # return_layer == "adjusted"
        assert cleaned_df is not None  # guaranteed by the layer/cleaner check above
        return self._slice_range(cleaned_df, start, end)

    def _write_raw_overwrite_if_superset(
        self, df: pd.DataFrame, ticker: str, frequency: str
    ) -> None:
        """Write raw, allowing overwrite only when the new range is a superset."""
        table = self._raw_table_name(ticker, frequency)
        path = self.store.raw_db_path
        path.parent.mkdir(parents=True, exist_ok=True)

        # Discover the current raw range (if any).
        existing_min: pd.Timestamp | None = None
        existing_max: pd.Timestamp | None = None
        if path.exists():
            with duckdb.connect(str(path)) as con:
                if _table_exists(con, table):
                    row = con.execute(
                        f"SELECT MIN(timestamp), MAX(timestamp) FROM {_quote_ident(table)}"
                    ).fetchone()
                    if row and row[0] is not None and row[1] is not None:
                        existing_min = pd.Timestamp(row[0])
                        existing_max = pd.Timestamp(row[1])

        new_min = pd.Timestamp(df.index.min())
        new_max = pd.Timestamp(df.index.max())

        if existing_min is not None and existing_max is not None:
            # New range must be a superset; otherwise refuse to clobber.
            existing_min_utc = self._to_utc(existing_min)
            existing_max_utc = self._to_utc(existing_max)
            new_min_utc = self._to_utc(new_min)
            new_max_utc = self._to_utc(new_max)
            if new_min_utc > existing_min_utc or new_max_utc < existing_max_utc:
                raise CacheError(
                    f"Refusing to overwrite raw cache for {self.source_name}:{ticker} "
                    f"with a non-superset range. Existing=[{existing_min_utc}..{existing_max_utc}], "
                    f"new=[{new_min_utc}..{new_max_utc}]. Use force_refresh=True with a wider "
                    f"date range or read from the cache instead."
                )
            # Same or wider: replace.
            with duckdb.connect(str(path)) as con:
                con.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")

        # Either the table didn't exist or we just dropped it. Use write_raw,
        # which is the documented write path and emits the structured log entry.
        try:
            self.store.write_raw(df, self.source_name, self._sanitize_ticker(ticker), frequency)
        except StorageError as e:
            # Defensive: if write_raw still fails (e.g. concurrent writer),
            # surface the error rather than silently retry.
            raise CacheError(f"Failed to write raw cache for {ticker}: {e}") from e

    @staticmethod
    def _to_utc(ts: pd.Timestamp) -> pd.Timestamp:
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")

    @staticmethod
    def _slice_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        start_ts = pd.Timestamp(start, tz="UTC")
        # Inclusive end: include all of the end date by extending to next midnight UTC.
        end_ts = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        return df[(df.index >= start_ts) & (df.index < end_ts)]

    def get_metadata(self, ticker: str) -> dict[str, Any]:
        """Pass-through to the underlying source."""
        return self.source.get_metadata(ticker)

    def validate(self, df: pd.DataFrame) -> bool:
        """Pass-through to the underlying source's validator."""
        # CachedSource is not a DataSource subclass (since it's frozen + composes
        # rather than inherits), but expose validate() for callers that want it.
        return self.source.validate(df)
