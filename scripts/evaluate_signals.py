"""Evaluate all Phase 1 / Stage 1 signals through the SignalEvaluator frequency layer.

This script is the reproducible entry point for Milestone 5.2's "re-run all signal
evaluations at correct frequency" step. It loads each signal from its YAML config,
fetches the required data live from FRED/Yahoo, runs the new frequency-layer
SignalEvaluator, and writes a Markdown report to `reports/signal_evaluation_phase1.md`.

Usage (from repo root):
    python scripts/evaluate_signals.py
    python scripts/evaluate_signals.py --start 2010-01-01 --end 2024-12-31
    python scripts/evaluate_signals.py --signal rates_trend  # one signal only

Notes:
    - Hits the network. Until Milestone 5.4 (Data Persistence) is done, this
      script does NOT use DataStore. Replace the fetch_* helpers when 5.4 lands.
    - FX Carry pair returns match the PROGRESS.md methodology (Option A):
      EURUSD=X and GBPUSD=X from Yahoo, inverse pairs negated. EUR/GBP and
      GBP/EUR pairs get NaN forward returns and are dropped from the cross-section.
      This is intentional — Milestone 5.5 will revisit pair construction.
    - Equity Momentum runs against the configured universe in
      `configs/universes/sp500_current.yaml`. Per PROGRESS.md, current cached
      universe is 50 stocks.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

# Project root on sys.path so `python scripts/evaluate_signals.py` works.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.cached_source import CachedSource
from src.data.sources.fred import FREDSource
from src.data.sources.yahoo import YahooSource
from src.data.store import DataStore
from src.evaluation.signal_evaluator import SignalEvaluator, SignalMetrics
from src.signals.equities.momentum import EquityMomentumSignal
from src.signals.fx.carry import FXCarrySignal
from src.signals.rates.trend import RatesTrendSignal


DEFAULT_START = date(2010, 1, 1)
DEFAULT_END = date(2024, 12, 31)
REPORT_PATH = ROOT / "reports" / "signal_evaluation_phase1.md"
DATA_DIR = ROOT / "data"


# ---------------------------------------------------------------------------
# Data fetching — Milestone 5.4 routes through DataStore via CachedSource.
# First run fetches from FRED/Yahoo and writes to data/raw/raw.duckdb.
# Subsequent runs read from the store; no network calls.
# Use --refresh on the CLI to force a re-fetch.
# ---------------------------------------------------------------------------


def _make_cached_fred() -> CachedSource:
    store = DataStore(data_dir=DATA_DIR)
    return CachedSource(source=FREDSource(), store=store, source_name="fred")


def _make_cached_yahoo() -> CachedSource:
    store = DataStore(data_dir=DATA_DIR)
    return CachedSource(source=YahooSource(), store=store, source_name="yahoo")


def fetch_fred(
    series_ids: list[str], start: date, end: date, *, force_refresh: bool = False
) -> dict[str, pd.DataFrame]:
    """Fetch FRED series, caching to data/raw/raw.duckdb."""
    cached = _make_cached_fred()
    out: dict[str, pd.DataFrame] = {}
    for sid in series_ids:
        out[sid] = cached.fetch_or_load(
            sid, start, end, frequency="daily", force_refresh=force_refresh
        )
    return out


def fetch_yahoo(
    tickers: list[str], start: date, end: date, *, force_refresh: bool = False
) -> dict[str, pd.DataFrame]:
    """Fetch Yahoo tickers, caching to data/raw/raw.duckdb."""
    cached = _make_cached_yahoo()
    out: dict[str, pd.DataFrame] = {}
    for tkr in tickers:
        out[tkr] = cached.fetch_or_load(
            tkr, start, end, frequency="daily", force_refresh=force_refresh
        )
    return out


# ---------------------------------------------------------------------------
# Forward-return construction per signal
# ---------------------------------------------------------------------------


def rates_trend_forward_returns(tlt_df: pd.DataFrame) -> pd.Series:
    """Single-asset daily log returns for TLT."""
    close = tlt_df["close"].astype(float).sort_index()
    return np.log(close / close.shift(1)).dropna()


def equity_momentum_forward_returns(
    price_data: dict[str, pd.DataFrame],
) -> pd.Series:
    """Per-ticker daily log returns, returned as MultiIndex (date, ticker) Series.

    The frequency layer in SignalEvaluator will resample these to monthly.
    """
    series_by_ticker = {}
    for tkr, df in price_data.items():
        close = df["close"].astype(float).sort_index()
        ret = np.log(close / close.shift(1))
        series_by_ticker[tkr] = ret
    panel = pd.DataFrame(series_by_ticker).sort_index()
    stacked = panel.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["date", "ticker"])
    return stacked.astype(float)


# USD-anchored Yahoo ticker mapping. Signal output labels are mechanically
# "<quote>/<base>" with base="USD" (per _iter_pairs), so all labels are
# "<non-USD>/USD". Yahoo, however, has both XXXUSD=X and USDXXX=X tickers
# depending on market convention:
#   - EUR/USD, GBP/USD, AUD/USD, NZD/USD → Yahoo has XXXUSD=X (USD is quote
#     in market terms). Our pair "<X>/USD" = long X / short USD = long the
#     Yahoo ticker. No negation.
#   - CAD/USD, JPY/USD, CHF/USD → Yahoo has USDXXX=X (USD is base in market
#     terms). Our pair "X/USD" = long X / short USD = short the Yahoo ticker.
#     Must negate the log return.
# This dict encodes both the ticker AND whether to negate.
FX_PAIR_TO_YAHOO: dict[str, tuple[str, bool]] = {
    # signal pair label → (yahoo ticker, negate?)
    "EUR/USD": ("EURUSD=X", False),
    "GBP/USD": ("GBPUSD=X", False),
    "AUD/USD": ("AUDUSD=X", False),
    "NZD/USD": ("NZDUSD=X", False),
    "CAD/USD": ("USDCAD=X", True),
    "JPY/USD": ("USDJPY=X", True),
    "CHF/USD": ("USDCHF=X", True),
}

# The unique set of Yahoo tickers we need to fetch.
FX_YAHOO_TICKERS: list[str] = sorted({t for t, _ in FX_PAIR_TO_YAHOO.values()})


def fx_carry_forward_returns(
    spot_data: dict[str, pd.DataFrame],
    pairs_in_signal: list[str],
) -> pd.Series:
    """Construct per-pair daily log returns from Yahoo USD-anchored spot data.

    Signal pair labels follow ``_iter_pairs`` convention: ``"<quote>/<base>"``
    with base=USD. Yahoo pair tickers follow market convention which puts USD
    first for JPY/CAD/CHF. The mapping in ``FX_PAIR_TO_YAHOO`` translates
    between the two and applies a sign flip where needed.

    Pairs not in the lookup table (e.g. cross rates we don't fetch) return
    NaN-filled series and are silently dropped downstream.
    """
    returns_by_ticker: dict[str, pd.Series] = {}
    for ticker, df in spot_data.items():
        close = df["close"].astype(float).sort_index()
        returns_by_ticker[ticker] = np.log(close / close.shift(1))

    # Common business-day index across all fetched spot rates.
    common_index: pd.DatetimeIndex | None = None
    for r in returns_by_ticker.values():
        common_index = r.index if common_index is None else common_index.intersection(r.index)
    if common_index is None:
        raise RuntimeError("No FX spot data available to construct returns.")
    common_index = common_index.sort_values()

    series_by_pair: dict[str, pd.Series] = {}
    missing_pairs: list[str] = []
    for pair in pairs_in_signal:
        mapping = FX_PAIR_TO_YAHOO.get(pair)
        if mapping is None or mapping[0] not in returns_by_ticker:
            missing_pairs.append(pair)
            series_by_pair[pair] = pd.Series(np.nan, index=common_index)
            continue
        yahoo_ticker, negate = mapping
        r = returns_by_ticker[yahoo_ticker].reindex(common_index)
        series_by_pair[pair] = -r if negate else r

    if missing_pairs:
        logger.warning(
            "FX Carry: no Yahoo data for {} pair(s): {}. These will be NaN-dropped.",
            len(missing_pairs), missing_pairs,
        )

    panel = pd.DataFrame(series_by_pair).sort_index()
    stacked = panel.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["date", "pair"])
    return stacked.astype(float)


# ---------------------------------------------------------------------------
# Evaluation per signal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationResult:
    signal_name: str
    frequency: str
    rows: list[tuple[int, SignalMetrics]]  # (horizon, metrics)


def evaluate_at_horizons(
    signal: pd.Series,
    forward_returns: pd.Series,
    horizons: list[int],
    frequency: str,
) -> list[tuple[int, SignalMetrics]]:
    ev = SignalEvaluator()
    out = []
    for h in horizons:
        m = ev.evaluate(signal=signal, forward_returns=forward_returns,
                        horizon=h, frequency=frequency)
        out.append((h, m))
    return out


def evaluate_rates_trend(start: date, end: date, *, force_refresh: bool = False) -> EvaluationResult:
    logger.info("=== Rates Trend ===")
    sig_obj = RatesTrendSignal()
    tickers = list(sig_obj.required_data)
    data = fetch_yahoo(tickers, start, end, force_refresh=force_refresh)
    signal = sig_obj.compute(data).dropna()
    fwd = rates_trend_forward_returns(data[tickers[0]])
    horizons = [1, 5, 21, 63]  # days
    rows = evaluate_at_horizons(signal, fwd, horizons, frequency=sig_obj.frequency)
    return EvaluationResult(sig_obj.name, sig_obj.frequency, rows)


def evaluate_fx_carry(start: date, end: date, *, force_refresh: bool = False) -> EvaluationResult:
    logger.info("=== FX Carry ===")
    sig_obj = FXCarrySignal()
    fred_data = fetch_fred(list(sig_obj.required_data), start, end, force_refresh=force_refresh)
    yahoo_data = fetch_yahoo(
        FX_YAHOO_TICKERS, start, end, force_refresh=force_refresh
    )
    signal = sig_obj.compute(fred_data).dropna()

    # Extract pair labels from the MultiIndex.
    pairs_in_signal = sorted({p for _, p in signal.index})
    logger.info("FX Carry pairs in signal: {}", pairs_in_signal)

    fwd = fx_carry_forward_returns(spot_data=yahoo_data, pairs_in_signal=pairs_in_signal)
    horizons = [1, 2, 3, 6]  # months
    rows = evaluate_at_horizons(signal, fwd, horizons, frequency=sig_obj.frequency)
    return EvaluationResult(sig_obj.name, sig_obj.frequency, rows)


def evaluate_equity_momentum(start: date, end: date, *, force_refresh: bool = False) -> EvaluationResult:
    logger.info("=== Equity Momentum ===")
    sig_obj = EquityMomentumSignal()
    tickers = list(sig_obj.required_data)
    logger.info("Equity Momentum universe: {} tickers", len(tickers))
    data = fetch_yahoo(tickers, start, end, force_refresh=force_refresh)
    signal = sig_obj.compute(data).dropna()
    fwd = equity_momentum_forward_returns(data)
    horizons = [1, 2, 3, 6]  # months
    rows = evaluate_at_horizons(signal, fwd, horizons, frequency=sig_obj.frequency)
    return EvaluationResult(sig_obj.name, sig_obj.frequency, rows)


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def format_metrics_row(horizon: int, m: SignalMetrics, freq: str) -> str:
    horizon_label = {"daily": "d", "weekly": "w", "monthly": "m"}.get(freq, "?")
    return (
        f"| {horizon}{horizon_label} "
        f"| {m.ic_mean:+.4f} "
        f"| {m.icir:+.4f} "
        f"| {m.hit_rate:.4f} "
        f"| {m.signal_sharpe:+.4f} "
        f"| {m.n_observations} |"
    )


def render_report(results: list[EvaluationResult], start: date, end: date) -> str:
    lines: list[str] = []
    lines.append("# Signal Evaluation — Phase 1 / Stage 1\n")
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Evaluation period:** {start.isoformat()} to {end.isoformat()}  ")
    lines.append("**Evaluator:** SignalEvaluator with Milestone 5.2 frequency layer.\n")
    lines.append("This report is auto-generated by `scripts/evaluate_signals.py`. ")
    lines.append("Do not edit by hand — re-run the script.\n")
    lines.append("---\n")

    for r in results:
        lines.append(f"## {r.signal_name} ({r.frequency} frequency)\n")
        lines.append("| Horizon | IC Mean | ICIR | Hit Rate | Sharpe | N |")
        lines.append("|---|---|---|---|---|---|")
        for horizon, m in r.rows:
            lines.append(format_metrics_row(horizon, m, r.frequency))
        lines.append("")
    lines.append("---\n")
    lines.append("## Methodology Notes\n")
    lines.append("- **Rates Trend:** TLT close-to-close log returns, daily horizons.")
    lines.append("- **FX Carry:** USDEUR/EURUSD/USDGBP/GBPUSD log returns from Yahoo, "
                 "inverse pairs negated. EUR/GBP and GBP/EUR pairs get NaN returns and "
                 "drop from the cross-section. Milestone 5.5 will revisit pair construction.")
    lines.append("- **Equity Momentum:** Per-ticker daily log returns, resampled to "
                 "monthly by the frequency layer (sum of log returns).")
    lines.append("- **Annualisation:** Sharpe annualised by sqrt(252) for daily, "
                 "sqrt(12) for monthly (frequency layer handles automatically).")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


SIGNAL_REGISTRY = {
    "rates_trend": evaluate_rates_trend,
    "fx_carry": evaluate_fx_carry,
    "equity_momentum": evaluate_equity_momentum,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run SignalEvaluator on all Phase 1 / Stage 1 signals."
    )
    parser.add_argument(
        "--start", type=date.fromisoformat, default=DEFAULT_START,
        help=f"Start date (YYYY-MM-DD). Default: {DEFAULT_START.isoformat()}.",
    )
    parser.add_argument(
        "--end", type=date.fromisoformat, default=DEFAULT_END,
        help=f"End date (YYYY-MM-DD). Default: {DEFAULT_END.isoformat()}.",
    )
    parser.add_argument(
        "--signal", choices=list(SIGNAL_REGISTRY.keys()), default=None,
        help="Run only one signal. Default: run all three.",
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip writing the markdown report.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Bypass the DataStore cache and re-fetch all data from FRED/Yahoo.",
    )
    args = parser.parse_args(argv)

    targets = [args.signal] if args.signal else list(SIGNAL_REGISTRY.keys())

    results: list[EvaluationResult] = []
    for name in targets:
        try:
            results.append(
                SIGNAL_REGISTRY[name](args.start, args.end, force_refresh=args.refresh)
            )
        except Exception as e:
            logger.exception("Signal {} failed: {}", name, e)

    # Print to stdout regardless.
    for r in results:
        print(f"\n=== {r.signal_name} ({r.frequency}) ===")
        print(f"{'Horizon':<10} {'IC':>10} {'ICIR':>10} {'Hit':>10} {'Sharpe':>10} {'N':>8}")
        for horizon, m in r.rows:
            print(
                f"{horizon:<10} "
                f"{m.ic_mean:>10.4f} "
                f"{m.icir:>10.4f} "
                f"{m.hit_rate:>10.4f} "
                f"{m.signal_sharpe:>10.4f} "
                f"{m.n_observations:>8}"
            )

    if not args.no_report and results:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        report = render_report(results, args.start, args.end)
        REPORT_PATH.write_text(report, encoding="utf-8")
        logger.info("Report written: {}", REPORT_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
