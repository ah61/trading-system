"""Evaluate all Phase 1 / Stage 1 signals through the SignalEvaluator frequency layer.

Reproducible runner for signal evaluation. Under Milestone 5.7, all data access
routes through ``VariableCatalog`` — there are no direct Yahoo or FRED calls in
this file. Variable names follow DD-007 conventions:

  - ``TLT_CLOSE``, ``EURUSD``, ``USDJPY``, ... — market prices (Yahoo)
  - ``DFF``, ``EUR_RATE``, ``GBP_RATE``, ``AUD_RATE``, ``NZD_RATE``,
    ``CAD_RATE``, ``JPY_RATE``, ``CHF_RATE`` — interbank rates (FRED)
  - ``AAPL_CLOSE``, ``MSFT_CLOSE``, ... — universe-expanded equity closes (Yahoo)

Usage (from repo root):
    python scripts/evaluate_signals.py
    python scripts/evaluate_signals.py --start 2010-01-01 --end 2024-12-31
    python scripts/evaluate_signals.py --signal rates_trend
    python scripts/evaluate_signals.py --refresh
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

# Project root on sys.path so `python scripts/evaluate_signals.py` works.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.sources.fred import FREDSource
from src.data.sources.yahoo import YahooSource
from src.data.store import DataStore
from src.data.variable_catalog import VariableCatalog
from src.evaluation.signal_evaluator import SignalEvaluator, SignalMetrics
from src.reporting.output_manager import OutputManager
from src.signals.base import Signal
from src.signals.equities.momentum import EquityMomentumSignal
from src.signals.fx.carry import FXCarrySignal
from src.signals.rates.trend import RatesTrendSignal


DEFAULT_START = date(2010, 1, 1)
DEFAULT_END = date(2024, 12, 31)
REPORT_PATH = ROOT / "reports" / "signal_evaluation_phase1.md"
DATA_DIR = ROOT / "data"


# ---------------------------------------------------------------------------
# Catalogue construction
# ---------------------------------------------------------------------------


def build_catalog() -> VariableCatalog:
    """Build a stateful VariableCatalog wired to FRED + Yahoo + DataStore.

    The catalogue handles all source routing internally: signals request
    variables by catalogue name, the catalogue dispatches to the right
    source. Use ``catalog.get(name, frequency=..., start=..., end=...)`` to
    pull a Series; the underlying ``CachedSource`` wrapper handles
    write-through caching to ``data/raw/raw.duckdb`` on first fetch.
    """
    store = DataStore(data_dir=DATA_DIR)
    return VariableCatalog.load(
        root=ROOT / "configs" / "data",
        sources={"fred": FREDSource(), "yahoo": YahooSource()},
        store=store,
    )


def fetch_variables(
    catalog: VariableCatalog,
    names: list[str],
    *,
    frequency: str | None,
    start: date,
    end: date,
    force_refresh: bool = False,
) -> dict[str, pd.Series]:
    """Fetch a list of variables from the catalogue.

    Args:
        catalog: Initialised stateful catalogue.
        names: Catalogue variable names to fetch.
        frequency: Target frequency, or ``None`` to keep each variable's
            native frequency. Pass the signal's ``frequency`` here when
            feeding the signal's own inputs; pass ``"daily"`` when fetching
            instrument prices for evaluation (the SignalEvaluator frequency
            layer resamples downstream).
        start: Inclusive start date.
        end: Inclusive end date.
        force_refresh: If True, bypass the DataStore cache for each fetch.
    """
    out: dict[str, pd.Series] = {}
    for name in names:
        out[name] = catalog.get(
            name, frequency=frequency, start=start, end=end, force_refresh=force_refresh,
        )
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationResult:
    signal_name: str
    frequency: str
    rows: list[tuple[int, SignalMetrics]]  # (horizon, metrics)


def evaluate_at_horizons(
    signal: pd.Series,
    prices: pd.Series,
    horizons: list[int],
    frequency: str,
) -> list[tuple[int, SignalMetrics]]:
    ev = SignalEvaluator()
    out = []
    for h in horizons:
        m = ev.evaluate(
            signal=signal, prices=prices,
            horizon=h, frequency=frequency,
        )
        out.append((h, m))
    return out


def evaluate_signal(
    catalog: VariableCatalog,
    sig_obj: Signal,
    start: date,
    end: date,
    *,
    force_refresh: bool = False,
) -> EvaluationResult:
    """Generic signal evaluator. Reads attributes from the signal.

    - required_variables → compute() inputs, fetched at signal.frequency
    - instruments → instrument_prices() inputs, fetched at "daily"
    - evaluation_horizons → horizons looped over
    - frequency → passed to evaluator's frequency layer
    """
    logger.info("=== {} ===", sig_obj.name)
    # Fetch at daily for both compute inputs and prices. The catalogue
    # forward-fills monthly variables (e.g. non-USD G10 interbank rates)
    # to daily per DD-004 — the value on each business day is the most
    # recent published monthly print as of that day. This produces a
    # signal output with daily-indexed level-0 dates that aligns with
    # daily prices; the evaluator's frequency layer resamples both to
    # signal.frequency together.
    #
    # The original DD-010 prompt prescribed fetching compute inputs at
    # signal.frequency. That was wrong: it produced month-start signal
    # dates that couldn't join with daily price dates and gave N=0.
    compute_inputs = fetch_variables(
        catalog, sig_obj.required_variables,
        frequency="daily",
        start=start, end=end,
        force_refresh=force_refresh,
    )
    price_inputs = fetch_variables(
        catalog, sig_obj.instruments,
        frequency="daily",
        start=start, end=end,
        force_refresh=force_refresh,
    )
    signal = sig_obj.compute(compute_inputs).dropna()
    prices = sig_obj.instrument_prices(price_inputs)
    rows = evaluate_at_horizons(
        signal, prices, sig_obj.evaluation_horizons,
        frequency=sig_obj.frequency,
    )
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
    lines.append("**Evaluator:** SignalEvaluator with Milestone 5.2 frequency layer.  ")
    lines.append("**Data access:** Routed through VariableCatalog (Milestone 5.7).\n")
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
    lines.append("- **Rates Trend:** TLT_CLOSE close-to-close log returns, daily horizons.")
    lines.append("- **FX Carry:** USD-anchored G10 spot variables (`EURUSD`, `GBPUSD`, "
                 "`AUDUSD`, `NZDUSD`, `USDCAD`, `USDJPY`, `USDCHF`). Pairs labelled "
                 "`<non-USD>/USD` mechanically (DD-005); USDXXX-orientation spots are "
                 "inverted in `instrument_prices()`. SignalEvaluator resamples daily "
                 "returns to monthly internally.")
    lines.append("- **Equity Momentum:** Per-variable daily log returns from "
                 "`{ticker}_CLOSE` catalogue names, resampled to monthly by the frequency layer.")
    lines.append("- **Annualisation:** Sharpe annualised by sqrt(252) for daily, "
                 "sqrt(12) for monthly (frequency layer handles automatically).")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


SIGNAL_REGISTRY: list[type[Signal]] = [
    RatesTrendSignal,
    FXCarrySignal,
    EquityMomentumSignal,
]


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
        "--signal", choices=[RatesTrendSignal.name, FXCarrySignal.name, EquityMomentumSignal.name],
        default=None,
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

    if args.refresh:
        logger.info("Refresh requested: catalog calls will use force_refresh=True")

    catalog = build_catalog()

    results: list[EvaluationResult] = []
    for sig_cls in SIGNAL_REGISTRY:
        sig_obj = sig_cls()
        if args.signal is not None and sig_obj.name != args.signal:
            continue
        try:
            results.append(
                evaluate_signal(
                    catalog, sig_obj, args.start, args.end,
                    force_refresh=args.refresh,
                )
            )
        except Exception as e:
            logger.exception("Signal {} failed: {}", sig_cls.__name__, e)

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
        # Milestone 5.6: structured output via OutputManager. Each evaluation
        # becomes a `reports/variables/{ts}_signal_evaluation/` run.
        mgr = OutputManager(reports_root=ROOT / "reports")
        run = mgr.new_variable(
            name="signal_evaluation",
            config={
                "start": args.start.isoformat(),
                "end": args.end.isoformat(),
                "signals": [r.signal_name for r in results],
                "refresh": args.refresh,
                "data_access": "VariableCatalog (5.7)",
            },
        )
        report = render_report(results, args.start, args.end)
        (run.path / "results.md").write_text(report, encoding="utf-8")
        rows: list[dict[str, Any]] = []
        for r in results:
            for horizon, m in r.rows:
                rows.append({
                    "signal": r.signal_name,
                    "frequency": r.frequency,
                    "horizon": horizon,
                    "ic": m.ic_mean,
                    "icir": m.icir,
                    "hit_rate": m.hit_rate,
                    "sharpe": m.signal_sharpe,
                    "n": m.n_observations,
                })
        pd.DataFrame(rows).to_csv(run.path / "results.csv", index=False)
        run.finalize()
        logger.info("Structured report written: {}", run.path)

        # Legacy path (deprecated; kept for now to avoid breaking outside refs).
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        logger.info("Legacy report written: {}", REPORT_PATH)

    return 0


if __name__ == "__main__":
    sys.exit(main())
