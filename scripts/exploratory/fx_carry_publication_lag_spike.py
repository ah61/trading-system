"""One-off diagnostic: FX Carry IC under simulated FRED publication lag.

The catalogue forward-fills monthly G10 interbank rates to daily (DD-004).
FRED labels a month-start print on the 1st but often publishes ~30 business
days later, so the baseline runner may read rates before they were knowable.
This spike re-evaluates FX Carry twice (baseline vs lag-shifted rates) and
compares IC/ICIR/hit/Sharpe/N per horizon. See PROGRESS.md "Active Issues".

Not a production runner. Prompt: FX Carry publication-lag diagnostic spike.
Do not treat exploratory output as the canonical signal-evaluation baseline.

Usage (from repo root):
    python scripts/exploratory/fx_carry_publication_lag_spike.py
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
LAG_BUSINESS_DAYS = 30

# TODO: read from catalogue spec native_frequency once exposed; hardcode is fine for this spike.
MONTHLY_RATE_VARIABLES: list[str] = [
    "EUR_RATE",
    "GBP_RATE",
    "AUD_RATE",
    "NZD_RATE",
    "CAD_RATE",
    "JPY_RATE",
    "CHF_RATE",
]


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


def apply_publication_lag(compute_inputs: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Forward-shift monthly rate series on the daily index (in-process lag)."""
    lagged = dict(compute_inputs)
    for name in MONTHLY_RATE_VARIABLES:
        lagged[name] = compute_inputs[name].shift(
            periods=LAG_BUSINESS_DAYS, freq="B"
        )
    return lagged


def evaluate_fx_carry(
    sig_obj: FXCarrySignal,
    compute_inputs: dict[str, pd.Series],
    price_inputs: dict[str, pd.Series],
) -> list[tuple[int, SignalMetrics]]:
    signal = sig_obj.compute(compute_inputs).dropna()
    prices = sig_obj.instrument_prices(price_inputs)
    ev = SignalEvaluator()
    out: list[tuple[int, SignalMetrics]] = []
    for h in sig_obj.evaluation_horizons:
        m = ev.evaluate(
            signal=signal, prices=prices,
            horizon=h, frequency=sig_obj.frequency,
        )
        out.append((h, m))
    return out


def _fmt(v: float) -> str:
    if pd.isna(v):
        return "nan"
    return f"{v:+.4f}" if abs(v) < 10 else f"{v:.4f}"


def print_metrics_table(title: str, rows: list[tuple[int, SignalMetrics]], freq: str) -> None:
    suffix = {"daily": "d", "weekly": "w", "monthly": "m"}.get(freq, "?")
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


def print_delta_table(
    baseline: list[tuple[int, SignalMetrics]],
    lagged: list[tuple[int, SignalMetrics]],
    freq: str,
) -> None:
    suffix = {"daily": "d", "weekly": "w", "monthly": "m"}.get(freq, "?")
    print("\n=== Delta (lagged − baseline) ===")
    print(
        f"{'Horizon':<10} {'|ΔIC|':>10} {'|ΔICIR|':>10} "
        f"{'N_base':>8} {'N_lag':>8} {'ΔN':>8}"
    )
    for (h_b, m_b), (h_l, m_l) in zip(baseline, lagged):
        assert h_b == h_l
        d_ic = abs(m_l.ic_mean - m_b.ic_mean) if (
            pd.notna(m_l.ic_mean) and pd.notna(m_b.ic_mean)
        ) else float("nan")
        d_icir = abs(m_l.icir - m_b.icir) if (
            pd.notna(m_l.icir) and pd.notna(m_b.icir)
        ) else float("nan")
        dn = m_l.n_observations - m_b.n_observations
        print(
            f"{h_b}{suffix:<9} "
            f"{_fmt(d_ic):>10} "
            f"{_fmt(d_icir):>10} "
            f"{m_b.n_observations:>8} "
            f"{m_l.n_observations:>8} "
            f"{dn:>8}"
        )


def metrics_row_md(horizon: int, m: SignalMetrics, freq: str) -> str:
    suffix = {"daily": "d", "weekly": "w", "monthly": "m"}.get(freq, "?")
    return (
        f"| {horizon}{suffix} "
        f"| {m.ic_mean:+.4f} "
        f"| {m.icir:+.4f} "
        f"| {m.hit_rate:.4f} "
        f"| {m.signal_sharpe:+.4f} "
        f"| {m.n_observations} |"
    )


def render_results_md(
    baseline: list[tuple[int, SignalMetrics]],
    lagged: list[tuple[int, SignalMetrics]],
    *,
    start: date,
    end: date,
    frequency: str,
) -> str:
    lines: list[str] = []
    lines.append("# FX Carry publication-lag spike\n")
    lines.append("## Methodology\n")
    lines.append(
        "Baseline run mirrors `scripts/evaluate_signals.py::evaluate_signal()` "
        "for FX Carry: daily catalogue fetches for rate inputs and spot "
        "instruments, then `SignalEvaluator` at monthly frequency. Lagged run "
        "applies `Series.shift(periods=30, freq='B')` in-process to the seven "
        "monthly FRED rate variables on the **daily-indexed** series returned "
        "by the catalogue (not a separate monthly fetch). `DFF` is unchanged. "
        "Fetch start is not extended; ~30 business days are lost at the head of "
        "the lagged signal by design.\n"
    )
    lines.append(f"**Window:** {start.isoformat()} to {end.isoformat()}  ")
    lines.append(f"**Lag:** {LAG_BUSINESS_DAYS} business days  ")
    lines.append(f"**Shifted variables:** {', '.join(MONTHLY_RATE_VARIABLES)}\n")

    lines.append("## Baseline\n")
    lines.append("| Horizon | IC Mean | ICIR | Hit Rate | Sharpe | N |")
    lines.append("|---|---|---|---|---|---|")
    for h, m in baseline:
        lines.append(metrics_row_md(h, m, frequency))

    lines.append("\n## Lagged\n")
    lines.append("| Horizon | IC Mean | ICIR | Hit Rate | Sharpe | N |")
    lines.append("|---|---|---|---|---|---|")
    for h, m in lagged:
        lines.append(metrics_row_md(h, m, frequency))

    lines.append("\n## Delta\n")
    lines.append("| Horizon | |ΔIC| | |ΔICIR| | N (baseline) | N (lagged) | ΔN |")
    lines.append("|---|---|---|---|---|---|")
    for (h_b, m_b), (h_l, m_l) in zip(baseline, lagged):
        d_ic = abs(m_l.ic_mean - m_b.ic_mean) if (
            pd.notna(m_l.ic_mean) and pd.notna(m_b.ic_mean)
        ) else float("nan")
        d_icir = abs(m_l.icir - m_b.icir) if (
            pd.notna(m_l.icir) and pd.notna(m_b.icir)
        ) else float("nan")
        suffix = {"daily": "d", "weekly": "w", "monthly": "m"}.get(frequency, "?")
        lines.append(
            f"| {h_b}{suffix} "
            f"| {d_ic:.4f} "
            f"| {d_icir:.4f} "
            f"| {m_b.n_observations} "
            f"| {m_l.n_observations} "
            f"| {m_l.n_observations - m_b.n_observations} |"
        )
    lines.append("")
    return "\n".join(lines)


def rows_to_csv_records(
    run_type: str,
    rows: list[tuple[int, SignalMetrics]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for horizon, m in rows:
        out.append({
            "run_type": run_type,
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

    logger.info("Fetching FX Carry inputs once (daily); lag applied in-process")
    compute_inputs = fetch_variables(
        catalog, list(sig_obj.required_variables), start=start, end=end,
    )
    price_inputs = fetch_variables(
        catalog, list(sig_obj.instruments), start=start, end=end,
    )

    baseline_rows = evaluate_fx_carry(sig_obj, compute_inputs, price_inputs)
    lagged_inputs = apply_publication_lag(compute_inputs)
    lagged_rows = evaluate_fx_carry(sig_obj, lagged_inputs, price_inputs)

    freq = sig_obj.frequency
    print_metrics_table("Baseline", baseline_rows, freq)
    print_metrics_table("Lagged (+30 BD on monthly rates)", lagged_rows, freq)
    print_delta_table(baseline_rows, lagged_rows, freq)

    mgr = OutputManager(reports_root=ROOT / "reports")
    run = mgr.new_exploratory(
        name="fx_carry_publication_lag_spike",
        config={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "lag_business_days": LAG_BUSINESS_DAYS,
            "shifted_variables": list(MONTHLY_RATE_VARIABLES),
            "note": "one-off diagnostic; not for the canonical baseline",
        },
    )
    (run.path / "results.md").write_text(
        render_results_md(baseline_rows, lagged_rows, start=start, end=end, frequency=freq),
        encoding="utf-8",
    )
    csv_rows = (
        rows_to_csv_records("baseline", baseline_rows)
        + rows_to_csv_records("lagged", lagged_rows)
    )
    pd.DataFrame(csv_rows).to_csv(run.path / "results.csv", index=False)
    run.finalize()
    logger.info("Exploratory output: {}", run.path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
