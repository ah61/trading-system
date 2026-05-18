"""One-off exploratory: FX Carry evaluation across monthly and quarterly horizons.

Extends the canonical monthly horizons [1, 2, 3, 6] with [9, 12] and adds a
quarterly grid [1, 2, 4]. Milestone 5.9 Part 2. Not a production runner;
do not treat output as the canonical signal-evaluation baseline.

Usage (from repo root):
    python scripts/exploratory/fx_carry_horizons.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.sources.fred import FREDSource
from src.data.sources.yahoo import YahooSource
from src.data.store import DataStore
from src.data.variable_catalog import VariableCatalog
from src.evaluation.signal_evaluator import SignalEvaluator, SignalMetrics
from src.reporting.output_manager import OutputManager
from src.signals.fx.carry import FXCarrySignal

DEFAULT_START = date(2010, 1, 1)
DEFAULT_END = date(2024, 12, 31)
DATA_DIR = ROOT / "data"

MONTHLY_HORIZONS: list[int] = [1, 2, 3, 6, 9, 12]
QUARTERLY_HORIZONS: list[int] = [1, 2, 4]

_HORIZON_SUFFIX = {"daily": "d", "weekly": "w", "monthly": "m", "quarterly": "q"}


def build_catalog() -> VariableCatalog:
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
    start: date,
    end: date,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for name in names:
        out[name] = catalog.get(
            name, frequency="daily", start=start, end=end, force_refresh=False,
        )
    return out


def evaluate_horizons(
    signal: pd.Series,
    prices: pd.Series,
    horizons: list[int],
    frequency: str,
) -> list[tuple[int, SignalMetrics]]:
    ev = SignalEvaluator()
    out: list[tuple[int, SignalMetrics]] = []
    for h in horizons:
        m = ev.evaluate(
            signal=signal, prices=prices,
            horizon=h, frequency=frequency,
        )
        out.append((h, m))
    return out


def _fmt(v: float) -> str:
    if pd.isna(v):
        return "nan"
    return f"{v:+.4f}" if abs(v) < 10 else f"{v:.4f}"


def print_metrics_table(
    title: str,
    rows: list[tuple[int, SignalMetrics]],
    frequency: str,
) -> None:
    suffix = _HORIZON_SUFFIX.get(frequency, "?")
    print(f"\n=== {title} ===")
    print(f"{'Horizon':<10} {'IC':>10} {'ICIR':>10} {'Hit':>10} {'Sharpe':>10} {'N':>8}")
    for horizon, m in rows:
        print(
            f"{horizon}{suffix:<9} "
            f"{_fmt(m.ic_mean):>10} "
            f"{_fmt(m.icir):>10} "
            f"{_fmt(m.hit_rate):>10} "
            f"{_fmt(m.signal_sharpe):>10} "
            f"{m.n_observations:>8}"
        )
        if m.n_observations < 20:
            print(
                f"WARNING: {frequency} h={horizon} has only N={m.n_observations} "
                "observations — interpret with caution"
            )


def _md_cell(v: float, fmt: str) -> str:
    if pd.isna(v):
        return "nan"
    return format(v, fmt)


def metrics_row_md(horizon: int, m: SignalMetrics, frequency: str) -> str:
    suffix = _HORIZON_SUFFIX.get(frequency, "?")
    return (
        f"| {horizon}{suffix} "
        f"| {_md_cell(m.ic_mean, '+.4f')} "
        f"| {_md_cell(m.icir, '+.4f')} "
        f"| {_md_cell(m.hit_rate, '.4f')} "
        f"| {_md_cell(m.signal_sharpe, '+.4f')} "
        f"| {m.n_observations} |"
    )


def render_results_md(
    monthly_rows: list[tuple[int, SignalMetrics]],
    quarterly_rows: list[tuple[int, SignalMetrics]],
    *,
    start: date,
    end: date,
) -> str:
    lines: list[str] = []
    lines.append("# FX Carry horizon grid (Milestone 5.9 Part 2)\n")
    lines.append("## Methodology\n")
    lines.append(
        "Inputs are fetched once at daily frequency through the catalogue. The "
        "evaluator resamples both signal and prices internally to the target "
        "frequency (monthly or quarterly) per its `frequency` argument. Horizon "
        "is in periods of the chosen frequency — months for the monthly grid, "
        "quarters for the quarterly grid.\n"
    )
    lines.append(f"**Window:** {start.isoformat()} to {end.isoformat()}\n")
    lines.append(f"**Monthly horizons:** {MONTHLY_HORIZONS}\n")
    lines.append(f"**Quarterly horizons:** {QUARTERLY_HORIZONS}\n")
    lines.append("### ICIR semantics at quarterly grain\n")
    lines.append(
        "Quarterly ICIR is **finite for FX Carry** and would be **NaN for any "
        "single-asset signal** evaluated at the same frequency. This is a "
        "property of the IC computation, not of the frequency. See DESIGN_DECISIONS "
        "DD-013 for the full reasoning. Briefly:\n"
    )
    lines.append(
        "- Cross-sectional signals (like FX Carry, `MultiIndex(date, pair)`): "
        "IC at each date is the Spearman correlation across pairs of signal "
        "vs forward returns; ICIR is `std`-across-dates of those per-date "
        "ICs. `_ROLLING_IC_WINDOW` is not used on this path. Sample size at "
        "quarterly grain is the number of quarterly dates in the window "
        "(~60 here over 2010–2024) — sufficient for a finite ICIR estimate.\n"
    )
    lines.append(
        "- Single-asset signals (flat `DatetimeIndex`): IC is computed via a "
        "*rolling* Spearman correlation over time at window length "
        "`_ROLLING_IC_WINDOW[frequency]`, which collapses to 1 at quarterly "
        "grain and makes ICIR NaN by design. See "
        "`tests/test_evaluation.py::test_quarterly_icir_is_nan_by_design`.\n"
    )
    lines.append(
        "Monthly and quarterly Sharpes are both annualised by the evaluator "
        "(√12 and √4 respectively via `_FREQUENCY_TABLE`), so the Sharpe "
        "columns are directly comparable across frequencies.\n"
    )

    lines.append("## Monthly grid\n")
    lines.append("| Horizon | IC Mean | ICIR | Hit Rate | Sharpe | N |")
    lines.append("|---|---|---|---|---|---|")
    for h, m in monthly_rows:
        lines.append(metrics_row_md(h, m, "monthly"))

    lines.append("\n## Quarterly grid\n")
    lines.append("| Horizon | IC Mean | ICIR | Hit Rate | Sharpe | N |")
    lines.append("|---|---|---|---|---|---|")
    for h, m in quarterly_rows:
        lines.append(metrics_row_md(h, m, "quarterly"))
    lines.append("")
    return "\n".join(lines)


def rows_to_csv_records(
    frequency: str,
    rows: list[tuple[int, SignalMetrics]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for horizon, m in rows:
        out.append({
            "frequency": frequency,
            "horizon": horizon,
            "ic": m.ic_mean,
            "icir": m.icir,
            "hit_rate": m.hit_rate,
            "sharpe": m.signal_sharpe,
            "n": m.n_observations,
        })
    return out


def main() -> int:
    start, end = DEFAULT_START, DEFAULT_END
    catalog = build_catalog()
    sig_obj = FXCarrySignal()

    logger.info("Fetching FX Carry inputs once (daily)")
    compute_inputs = fetch_variables(
        catalog, list(sig_obj.required_variables), start=start, end=end,
    )
    price_inputs = fetch_variables(
        catalog, list(sig_obj.instruments), start=start, end=end,
    )

    signal = sig_obj.compute(compute_inputs).dropna()
    prices = sig_obj.instrument_prices(price_inputs)

    monthly_rows = evaluate_horizons(
        signal, prices, MONTHLY_HORIZONS, frequency="monthly",
    )
    quarterly_rows = evaluate_horizons(
        signal, prices, QUARTERLY_HORIZONS, frequency="quarterly",
    )

    print_metrics_table("Monthly grid", monthly_rows, "monthly")
    print_metrics_table("Quarterly grid", quarterly_rows, "quarterly")

    mgr = OutputManager(reports_root=ROOT / "reports")
    run = mgr.new_exploratory(
        name="fx_carry_horizons",
        config={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "monthly_horizons": MONTHLY_HORIZONS,
            "quarterly_horizons": QUARTERLY_HORIZONS,
            "note": (
                "one-off horizon-grid experiment for Milestone 5.9 Part 2; "
                "not for the canonical baseline"
            ),
        },
    )
    (run.path / "results.md").write_text(
        render_results_md(monthly_rows, quarterly_rows, start=start, end=end),
        encoding="utf-8",
    )
    csv_rows = (
        rows_to_csv_records("monthly", monthly_rows)
        + rows_to_csv_records("quarterly", quarterly_rows)
    )
    pd.DataFrame(csv_rows).to_csv(run.path / "results.csv", index=False)
    run.finalize()
    logger.info("Exploratory output: {}", run.path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
