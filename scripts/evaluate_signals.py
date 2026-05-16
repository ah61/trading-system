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

import numpy as np
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
            the underlying price series used to construct forward returns
            (the SignalEvaluator frequency layer resamples downstream).
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
# Forward-return construction per signal
# ---------------------------------------------------------------------------


def rates_trend_forward_returns(tlt_close: pd.Series) -> pd.Series:
    """Single-asset daily log returns for TLT."""
    close = tlt_close.astype(float).sort_index()
    return np.log(close / close.shift(1)).dropna()


def equity_momentum_forward_returns(
    price_series: dict[str, pd.Series],
) -> pd.Series:
    """Per-variable daily log returns, returned as a MultiIndex ``(date, variable)`` Series.

    Asset level uses catalogue variable names (e.g. ``"AAPL_CLOSE"``), matching
    the signal output's asset level so the SignalEvaluator can align cross-
    section without translation.
    """
    series_by_var = {}
    for var, close in price_series.items():
        c = close.astype(float).sort_index()
        series_by_var[var] = np.log(c / c.shift(1))
    panel = pd.DataFrame(series_by_var).sort_index()
    stacked = panel.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["date", "variable"])
    return stacked.astype(float)


# Maps the FXCarrySignal's mechanical pair labels to the catalogue spot
# variable used for forward-return construction, plus a negate flag for
# pairs where the spot variable's market-convention orientation is reversed
# vs the signal pair (USDXXX vs XXXUSD). See market.yaml for the variable
# declarations.
FX_PAIR_TO_SPOT_VARIABLE: dict[str, tuple[str, bool]] = {
    # signal pair label → (catalogue spot variable name, negate?)
    "EUR/USD": ("EURUSD", False),
    "GBP/USD": ("GBPUSD", False),
    "AUD/USD": ("AUDUSD", False),
    "NZD/USD": ("NZDUSD", False),
    "CAD/USD": ("USDCAD", True),
    "JPY/USD": ("USDJPY", True),
    "CHF/USD": ("USDCHF", True),
}

FX_SPOT_VARIABLES: list[str] = sorted({v for v, _ in FX_PAIR_TO_SPOT_VARIABLE.values()})


def fx_carry_forward_returns(
    spot_series: dict[str, pd.Series],
    pairs_in_signal: list[str],
) -> pd.Series:
    """Construct per-pair daily log returns from USD-anchored spot variables.

    Pairs in the signal labelled ``"<X>/USD"`` are mapped to a catalogue spot
    variable whose orientation may be either ``XXXUSD`` (no negation needed)
    or ``USDXXX`` (negate). See DD-005 for label convention.
    """
    returns_by_var: dict[str, pd.Series] = {}
    for var, close in spot_series.items():
        c = close.astype(float).sort_index()
        returns_by_var[var] = np.log(c / c.shift(1))

    # Common business-day index across all fetched spot rates.
    common_index: pd.DatetimeIndex | None = None
    for r in returns_by_var.values():
        common_index = r.index if common_index is None else common_index.intersection(r.index)
    if common_index is None:
        raise RuntimeError("No FX spot data available to construct returns.")
    common_index = common_index.sort_values()

    series_by_pair: dict[str, pd.Series] = {}
    missing_pairs: list[str] = []
    for pair in pairs_in_signal:
        mapping = FX_PAIR_TO_SPOT_VARIABLE.get(pair)
        if mapping is None or mapping[0] not in returns_by_var:
            missing_pairs.append(pair)
            series_by_pair[pair] = pd.Series(np.nan, index=common_index)
            continue
        spot_var, negate = mapping
        r = returns_by_var[spot_var].reindex(common_index)
        series_by_pair[pair] = -r if negate else r

    if missing_pairs:
        logger.warning(
            "FX Carry: no spot data for {} pair(s): {}. These will be NaN-dropped.",
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
        m = ev.evaluate(
            signal=signal, forward_returns=forward_returns,
            horizon=h, frequency=frequency,
        )
        out.append((h, m))
    return out


def evaluate_rates_trend(
    catalog: VariableCatalog, start: date, end: date, *, force_refresh: bool = False,
) -> EvaluationResult:
    logger.info("=== Rates Trend ===")
    sig_obj = RatesTrendSignal()
    # Signal frequency is daily; native frequency of TLT_CLOSE is daily.
    inputs = fetch_variables(
        catalog, sig_obj.required_variables,
        frequency=sig_obj.frequency, start=start, end=end,
        force_refresh=force_refresh,
    )
    signal = sig_obj.compute(inputs).dropna()
    # Forward returns from the same TLT_CLOSE series.
    fwd = rates_trend_forward_returns(inputs[sig_obj.params["variable"]])
    horizons = [1, 5, 21, 63]  # days
    rows = evaluate_at_horizons(signal, fwd, horizons, frequency=sig_obj.frequency)
    return EvaluationResult(sig_obj.name, sig_obj.frequency, rows)


def evaluate_fx_carry(
    catalog: VariableCatalog, start: date, end: date, *, force_refresh: bool = False,
) -> EvaluationResult:
    logger.info("=== FX Carry ===")
    sig_obj = FXCarrySignal()
    # Signal frequency is monthly. DFF is natively daily, the others natively
    # monthly; catalogue resamples whichever side doesn't match.
    rate_inputs = fetch_variables(
        catalog, sig_obj.required_variables,
        frequency=sig_obj.frequency, start=start, end=end,
        force_refresh=force_refresh,
    )
    signal = sig_obj.compute(rate_inputs).dropna()

    pairs_in_signal = sorted({p for _, p in signal.index})
    logger.info("FX Carry pairs in signal: {}", pairs_in_signal)

    # Forward returns from daily spot prices; the SignalEvaluator frequency
    # layer aggregates to monthly inside evaluate().
    spot_inputs = fetch_variables(
        catalog, FX_SPOT_VARIABLES,
        frequency="daily", start=start, end=end,
        force_refresh=force_refresh,
    )
    fwd = fx_carry_forward_returns(spot_series=spot_inputs, pairs_in_signal=pairs_in_signal)
    horizons = [1, 2, 3, 6]  # months
    rows = evaluate_at_horizons(signal, fwd, horizons, frequency=sig_obj.frequency)
    return EvaluationResult(sig_obj.name, sig_obj.frequency, rows)


def evaluate_equity_momentum(
    catalog: VariableCatalog, start: date, end: date, *, force_refresh: bool = False,
) -> EvaluationResult:
    logger.info("=== Equity Momentum ===")
    sig_obj = EquityMomentumSignal()
    logger.info("Equity Momentum universe: {} variables", len(sig_obj.required_variables))
    # Per-name daily closes serve both the signal (which resamples to monthly
    # internally) and the forward-return computation.
    inputs = fetch_variables(
        catalog, sig_obj.required_variables,
        frequency="daily", start=start, end=end,
        force_refresh=force_refresh,
    )
    signal = sig_obj.compute(inputs).dropna()
    fwd = equity_momentum_forward_returns(inputs)
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
                 "negated. SignalEvaluator resamples daily returns to monthly internally.")
    lines.append("- **Equity Momentum:** Per-variable daily log returns from "
                 "`{ticker}_CLOSE` catalogue names, resampled to monthly by the frequency layer.")
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

    if args.refresh:
        logger.info("Refresh requested: catalog calls will use force_refresh=True")

    catalog = build_catalog()
    targets = [args.signal] if args.signal else list(SIGNAL_REGISTRY.keys())

    results: list[EvaluationResult] = []
    for name in targets:
        try:
            results.append(
                SIGNAL_REGISTRY[name](
                    catalog, args.start, args.end, force_refresh=args.refresh,
                )
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
