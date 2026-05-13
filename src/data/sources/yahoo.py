"""Yahoo Finance data source adapter using `yfinance`."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

from src.data.sources.base import DataSource
from src.exceptions import DataFetchError, DataValidationError

load_dotenv()


_PRIORITY_ETFS: set[str] = {"TLT", "IEF", "SHY", "HYG"}
_PRIORITY_FX: set[str] = {
    "EURUSD=X",
    "GBPUSD=X",
    "AUDUSD=X",
    "NZDUSD=X",
    "USDCAD=X",
    "USDCHF=X",
    "USDJPY=X",
}

_EQUITY_UNIVERSE_RE = re.compile(r"^[A-Z]{1,5}$")


def _is_equity_universe_ticker(ticker: str) -> bool:
    """Heuristic to flag survivorship-biased equity universe tickers (e.g., current S&P 500)."""
    if ticker in _PRIORITY_ETFS:
        return False
    if "=" in ticker:
        return False
    return bool(_EQUITY_UNIVERSE_RE.fullmatch(ticker))


class YahooSource(DataSource):
    """Yahoo Finance data source using `yfinance.download`."""

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch a series from Yahoo Finance with `auto_adjust=True`."""
        try:
            import yfinance as yf  # type: ignore
        except Exception as e:  # pragma: no cover
            raise DataFetchError(f"yfinance is not installed or failed to import: {e}") from e

        try:
            raw = yf.download(
                ticker,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
            )
        except Exception as e:
            raise DataFetchError(f"Yahoo download failed for {ticker!r}: {e}") from e

        if raw is None or getattr(raw, "empty", True):
            raise DataFetchError(f"No data returned for {ticker!r}.")

        df = self._to_contract_df(raw, ticker=ticker)
        df = self._forward_fill_limited(df, limit=3, ticker=ticker)

        try:
            self.validate(df)
        except DataValidationError as e:
            raise DataFetchError(f"Fetched data failed validation for {ticker!r}: {e}") from e

        return df

    def get_metadata(self, ticker: str) -> dict[str, Any]:
        """Return metadata for a ticker."""
        survivorship_biased = _is_equity_universe_ticker(ticker)
        known_limitations: list[str] = [
            "Yahoo Finance data can be revised or adjusted without notice.",
            "Corporate actions and adjustment methodology may differ from vendor-grade data.",
            "Timezone conventions can vary; index is normalized to UTC by this adapter.",
        ]
        if ticker in _PRIORITY_FX or ticker.endswith("=X"):
            known_limitations.append("FX tickers are synthetic and may have gaps around holidays/weekends.")
        if survivorship_biased:
            known_limitations.append(
                "Equity universe tickers (e.g., current S&P 500 members) are survivorship-biased."
            )

        return {
            "source": "yahoo",
            "ticker": ticker,
            "frequency": "daily",
            "known_limitations": known_limitations,
            "survivorship_biased": survivorship_biased,
        }

    @staticmethod
    def _to_contract_df(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if not isinstance(raw.index, pd.DatetimeIndex):
            raise DataFetchError(f"Yahoo returned non-datetime index for {ticker!r}.")

        # yfinance returns MultiIndex columns like ('Close', 'TLT') for single-ticker
        # downloads in newer versions. Flatten to the OHLCV field level so the rest of
        # this method can treat columns as simple strings.
        if isinstance(raw.columns, pd.MultiIndex):
            try:
                flat = raw.xs(ticker, axis=1, level=1)
            except KeyError:
                flat = raw.droplevel(1, axis=1)
        else:
            flat = raw

        lower_to_actual = {str(c).lower(): c for c in flat.columns}
        if "close" in lower_to_actual:
            close_col = lower_to_actual["close"]
        elif "adj close" in lower_to_actual:
            close_col = lower_to_actual["adj close"]
        else:
            raise DataFetchError(f"Yahoo returned no Close column for {ticker!r}.")

        close = flat[close_col]
        if isinstance(close, pd.DataFrame):
            # Defensive: if duplicate columns remain, take the first one.
            close = close.iloc[:, 0]

        idx = pd.to_datetime(close.index, utc=True)
        df = pd.DataFrame(
            {
                "close": close.to_numpy(dtype=np.float64, copy=False),
                "source": "yahoo",
            },
            index=pd.DatetimeIndex(idx, name="timestamp"),
        )
        return df.sort_index()

    @staticmethod
    def _forward_fill_limited(df: pd.DataFrame, limit: int, ticker: str) -> pd.DataFrame:
        before_na = int(df["close"].isna().sum())
        if before_na:
            logger.info("Forward-filling {} NaNs for {}", before_na, ticker)

        out = df.copy()
        out["close"] = out["close"].ffill(limit=limit)
        remaining = int(out["close"].isna().sum())
        if remaining:
            raise DataFetchError(
                f"{ticker!r} has {remaining} remaining NaN values after forward fill (limit={limit})."
            )
        return out

