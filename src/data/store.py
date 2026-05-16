"""DuckDB-backed immutable layered time series storage.

This module provides the `DataStore` class which implements a three-layer data
storage model for time series:
- raw (immutable)
- adjusted (versioned)
- derived
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
from loguru import logger

from src.exceptions import StorageError


Layer = Literal["raw", "adjusted", "derived"]


_VALID_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_ADJ_VERSION_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)$")


def _validate_ident(value: str, label: str) -> None:
    """Validate a string is safe for use in a DuckDB identifier."""
    if not value or not _VALID_IDENT_RE.fullmatch(value):
        raise StorageError(f"Invalid {label}: {value!r}. Use only [A-Za-z0-9_].")


def _quote_ident(ident: str) -> str:
    """Quote an identifier for use in SQL."""
    return '"' + ident.replace('"', '""') + '"'


def _ensure_datetime_index_utc(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a DatetimeIndex coerced to UTC."""
    if not isinstance(df.index, pd.DatetimeIndex):
        raise StorageError("df must have a DatetimeIndex.")

    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    out = df.copy()
    out.index = idx
    out.index.name = out.index.name or "timestamp"
    return out


def _df_to_storage_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Convert a time-indexed DataFrame into a frame suitable for DuckDB."""
    df_utc = _ensure_datetime_index_utc(df)
    storage = df_utc.reset_index()
    # Ensure deterministic timestamp column name.
    if storage.columns[0] != "timestamp":
        storage = storage.rename(columns={storage.columns[0]: "timestamp"})
    return storage


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    res = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_name = ?",
        [table],
    ).fetchone()
    return bool(res and int(res[0]) > 0)


def _list_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}").fetchone()[0])


@dataclass(frozen=True, slots=True)
class DataStore:
    """Store and retrieve time series data in layered DuckDB files.

    Args:
        data_dir: Base directory that will contain `raw/`, `adjusted/`, and
            `derived/` subdirectories with their respective DuckDB files.
    """

    data_dir: Path

    @property
    def raw_db_path(self) -> Path:
        """Path to the raw layer DuckDB file."""
        return self.data_dir / "raw" / "raw.duckdb"

    @property
    def adjusted_db_path(self) -> Path:
        """Path to the adjusted layer DuckDB file."""
        return self.data_dir / "adjusted" / "adjusted.duckdb"

    @property
    def derived_db_path(self) -> Path:
        """Path to the derived layer DuckDB file."""
        return self.data_dir / "derived" / "derived.duckdb"

    def _db_path_for_layer(self, layer: Layer) -> Path:
        if layer == "raw":
            return self.raw_db_path
        if layer == "adjusted":
            return self.adjusted_db_path
        if layer == "derived":
            return self.derived_db_path
        raise StorageError(f"Unknown layer: {layer!r}")

    def _connect(self, layer: Layer) -> duckdb.DuckDBPyConnection:
        path = self._db_path_for_layer(layer)
        path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(path))

    @staticmethod
    def _raw_table_name(source: str, ticker: str, frequency: str) -> str:
        _validate_ident(source, "source")
        _validate_ident(ticker, "ticker")
        _validate_ident(frequency, "frequency")
        return f"{source}_{ticker}_{frequency}"

    @staticmethod
    def _adjusted_table_name(source: str, ticker: str, frequency: str, version: int) -> str:
        if version < 0:
            raise StorageError("version must be a non-negative integer.")
        base = DataStore._raw_table_name(source, ticker, frequency)
        return f"{base}_v{version}"

    @staticmethod
    def _derived_table_name(name: str, frequency: str) -> str:
        _validate_ident(name, "name")
        _validate_ident(frequency, "frequency")
        return f"{name}_{frequency}"

    def write_raw(self, df: pd.DataFrame, source: str, ticker: str, frequency: str) -> None:
        """Write an immutable raw series.

        Raises:
            StorageError: If the table already exists or df is invalid.
        """
        if "close" not in df.columns:
            raise StorageError("df must have at least a 'close' column.")

        table = self._raw_table_name(source, ticker, frequency)
        storage = _df_to_storage_frame(df)

        logger.info("Writing raw series to table {}", table)
        with self._connect("raw") as con:
            if _table_exists(con, table):
                raise StorageError(f"Raw table already exists: {table}")
            con.register("_df", storage)
            con.execute(f"CREATE TABLE {_quote_ident(table)} AS SELECT * FROM _df")

    def write_adjusted(
        self, df: pd.DataFrame, source: str, ticker: str, frequency: str, version: int
    ) -> None:
        """Write an adjusted series to a versioned table (overwrite allowed)."""
        if "close" not in df.columns:
            raise StorageError("df must have at least a 'close' column.")

        table = self._adjusted_table_name(source, ticker, frequency, version)
        storage = _df_to_storage_frame(df)

        logger.info("Writing adjusted series to table {}", table)
        with self._connect("adjusted") as con:
            con.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")
            con.register("_df", storage)
            con.execute(f"CREATE TABLE {_quote_ident(table)} AS SELECT * FROM _df")

    def write_derived(self, df: pd.DataFrame, name: str, frequency: str) -> None:
        """Write a derived series to the derived layer."""
        if "close" not in df.columns:
            raise StorageError("df must have at least a 'close' column.")

        table = self._derived_table_name(name, frequency)
        storage = _df_to_storage_frame(df)

        logger.info("Writing derived series to table {}", table)
        with self._connect("derived") as con:
            con.execute(f"DROP TABLE IF EXISTS {_quote_ident(table)}")
            con.register("_df", storage)
            con.execute(f"CREATE TABLE {_quote_ident(table)} AS SELECT * FROM _df")

    def read(
        self,
        source: str,
        ticker: str,
        frequency: str,
        layer: Layer = "adjusted",
        version: int | Literal["latest"] = "latest",
    ) -> pd.DataFrame:
        """Read a stored series and return a DataFrame with a UTC DatetimeIndex.

        Raises:
            StorageError: If the requested table cannot be found.
        """
        _validate_ident(source, "source")
        _validate_ident(ticker, "ticker")
        _validate_ident(frequency, "frequency")

        if layer == "raw":
            table = self._raw_table_name(source, ticker, frequency)
            path_layer: Layer = "raw"
        elif layer == "adjusted":
            path_layer = "adjusted"
            base = self._raw_table_name(source, ticker, frequency)
            if version == "latest":
                with self._connect("adjusted") as con:
                    candidates = [t for t in _list_tables(con) if t.startswith(f"{base}_v")]
                    best = self._pick_latest_adjusted_table(candidates, base)
                    if best is None:
                        raise StorageError(f"Adjusted table not found for base: {base}")
                    table = best
            else:
                if not isinstance(version, int):
                    raise StorageError("version must be an int or 'latest'.")
                table = self._adjusted_table_name(source, ticker, frequency, version)
        elif layer == "derived":
            raise StorageError(
                "Reading derived layer requires a derived 'name'. "
                "Use write_derived/read via list_available to discover derived tables."
            )
        else:
            raise StorageError(f"Unknown layer: {layer!r}")

        with self._connect(path_layer) as con:
            if not _table_exists(con, table):
                raise StorageError(f"Table not found: {table}")
            df = con.execute(f"SELECT * FROM {_quote_ident(table)} ORDER BY timestamp").df()

        if "timestamp" not in df.columns:
            raise StorageError(f"Stored table missing 'timestamp' column: {table}")
        ts = pd.to_datetime(df["timestamp"], utc=True)
        df = df.drop(columns=["timestamp"])
        df.index = pd.DatetimeIndex(ts, name="timestamp")
        return df

    def read_derived_by_table(self, table: str) -> pd.DataFrame:
        """Read a derived table by exact table name.

        Used by VariableCatalog for transformed-variable cache lookup where the
        table name includes a spec hash. Returns a DataFrame with UTC DatetimeIndex.

        Raises:
            StorageError: If the table does not exist.
        """
        _validate_ident(table, "table")
        with self._connect("derived") as con:
            if not _table_exists(con, table):
                raise StorageError(f"Derived table not found: {table}")
            df = con.execute(
                f"SELECT * FROM {_quote_ident(table)} ORDER BY timestamp"
            ).df()

        if "timestamp" not in df.columns:
            raise StorageError(f"Stored table missing 'timestamp' column: {table}")
        ts = pd.to_datetime(df["timestamp"], utc=True)
        df = df.drop(columns=["timestamp"])
        df.index = pd.DatetimeIndex(ts, name="timestamp")
        return df

    @staticmethod
    def _pick_latest_adjusted_table(candidates: list[str], base: str) -> str | None:
        best_version = -1
        best_table: str | None = None
        for t in candidates:
            if not t.startswith(f"{base}_v"):
                continue
            m = _ADJ_VERSION_RE.fullmatch(t)
            if not m:
                continue
            try:
                v = int(m.group("version"))
            except ValueError:
                continue
            if v > best_version:
                best_version = v
                best_table = t
        return best_table

    def list_available(self, layer: Layer | None = None) -> pd.DataFrame:
        """List available series tables and counts across layers."""
        layers: list[Layer] = ["raw", "adjusted", "derived"] if layer is None else [layer]
        for lyr in layers:
            if lyr not in ("raw", "adjusted", "derived"):
                raise StorageError(f"Unknown layer: {lyr!r}")

        records: list[dict[str, Any]] = []
        for lyr in layers:
            with self._connect(lyr) as con:
                for table in _list_tables(con):
                    parsed = self._parse_table_name(lyr, table)
                    if parsed is None:
                        continue
                    rec = {
                        "layer": lyr,
                        "source": parsed.get("source"),
                        "ticker": parsed.get("ticker"),
                        "frequency": parsed.get("frequency"),
                        "version": parsed.get("version"),
                        "row_count": _row_count(con, table),
                    }
                    records.append(rec)

        df = pd.DataFrame.from_records(
            records, columns=["layer", "source", "ticker", "frequency", "version", "row_count"]
        )
        if df.empty:
            return df

        df["version"] = pd.to_numeric(df["version"], errors="coerce").astype("Int64")
        return df.sort_values(by=["layer", "source", "ticker", "frequency", "version"], ignore_index=True)

    @staticmethod
    def _parse_table_name(layer: Layer, table: str) -> dict[str, Any] | None:
        if layer == "raw":
            parts = table.split("_")
            if len(parts) != 3:
                return None
            return {"source": parts[0], "ticker": parts[1], "frequency": parts[2], "version": None}

        if layer == "adjusted":
            m = _ADJ_VERSION_RE.fullmatch(table)
            if not m:
                return None
            base = m.group("base")
            parts = base.split("_")
            if len(parts) != 3:
                return None
            return {
                "source": parts[0],
                "ticker": parts[1],
                "frequency": parts[2],
                "version": int(m.group("version")),
            }

        if layer == "derived":
            parts = table.split("_")
            if len(parts) < 2:
                return None
            frequency = parts[-1]
            name = "_".join(parts[:-1])
            return {"source": name, "ticker": None, "frequency": frequency, "version": None}

        return None

    def get_lineage(self, source: str, ticker: str, frequency: str) -> dict[str, Any]:
        """Return raw → adjusted → derived lineage information for a series."""
        base = self._raw_table_name(source, ticker, frequency)

        raw_table = base
        with self._connect("raw") as con_raw:
            raw_exists = _table_exists(con_raw, raw_table)

        adjusted_versions: list[int] = []
        with self._connect("adjusted") as con_adj:
            candidates = [t for t in _list_tables(con_adj) if t.startswith(f"{base}_v")]
            for t in candidates:
                m = _ADJ_VERSION_RE.fullmatch(t)
                if not m:
                    continue
                adjusted_versions.append(int(m.group("version")))
        adjusted_versions.sort()

        derived_tables: list[str] = []
        with self._connect("derived") as con_der:
            # Heuristic: derived tables that start with "{source}_{ticker}_" and end with _{frequency}
            prefix = f"{source}_{ticker}_"
            suffix = f"_{frequency}"
            for t in _list_tables(con_der):
                if t.startswith(prefix) and t.endswith(suffix):
                    derived_tables.append(t)
        derived_tables.sort()

        return {
            "raw": raw_table if raw_exists else None,
            "adjusted_versions": adjusted_versions,
            "derived": derived_tables,
        }

