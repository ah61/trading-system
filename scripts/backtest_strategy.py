"""Run a single-signal strategy backtest through the full Phase 6 pipeline.

Catalogue → signal → BacktestEngine → portfolio → tearsheet. Data access routes
through ``VariableCatalog`` (Milestone 5.7); there are no direct source calls here.

Usage (from repo root):
    python scripts/backtest_strategy.py --signal rates_trend
    python scripts/backtest_strategy.py --signal rates_trend --start 2010-01-01 --end 2024-12-31
    python scripts/backtest_strategy.py --signal rates_trend --method rolling --refresh
    python scripts/backtest_strategy.py --signal rates_trend --no-tearsheet
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest.engine import BacktestEngine
from src.backtest.tearsheet import TearsheetGenerator
from src.data.sources.fred import FREDSource
from src.data.sources.yahoo import YahooSource
from src.data.store import DataStore
from src.data.variable_catalog import VariableCatalog
from src.portfolio.costs import CostModel
from src.reporting.output_manager import OutputManager, Run
from src.signals.base import Signal
from src.signals.rates.trend import RatesTrendSignal

DEFAULT_START = date(2010, 1, 1)
DEFAULT_END = date(2024, 12, 31)
DATA_DIR = ROOT / "data"
CONFIGS_DIR = ROOT / "configs"
CATALOG_ROOT = CONFIGS_DIR / "data"
SIGNALS_DIR = CONFIGS_DIR / "signals"

# Extensible registry — add a class entry when wiring a second signal.
SIGNAL_CLASS_REGISTRY: dict[str, type[Signal]] = {
    "rates_trend": RatesTrendSignal,
}

# Per-signal portfolio wiring — extract to per-signal config when we add a second signal.
PORTFOLIO_BY_SIGNAL: dict[str, dict[str, Any]] = {
    "rates_trend": {
        "instruments": ["TLT_CLOSE"],
        "asset_classes": {"TLT_CLOSE": "rates"},
        "sizing_method": "vol_target",
        "target_vol": 0.10,
        "vol_window": 60,
        "gross_limit": 2.0,
        "net_limit": 0.20,
    },
}


def signal_choices() -> list[str]:
    """CLI choices from ``configs/signals/*.yaml`` filenames (without extension)."""
    if not SIGNALS_DIR.exists():
        return sorted(SIGNAL_CLASS_REGISTRY.keys())
    return sorted(p.stem for p in SIGNALS_DIR.glob("*.yaml"))


def build_catalog(
    *,
    catalog_root: Path = CATALOG_ROOT,
    data_dir: Path = DATA_DIR,
) -> VariableCatalog:
    store = DataStore(data_dir=data_dir)
    return VariableCatalog.load(
        root=catalog_root,
        sources={"fred": FREDSource(), "yahoo": YahooSource()},
        store=store,
    )


def load_signal(signal_name: str, *, configs_dir: Path = CONFIGS_DIR) -> Signal:
    if signal_name not in SIGNAL_CLASS_REGISTRY:
        raise ValueError(
            f"Signal {signal_name!r} has no class registered in SIGNAL_CLASS_REGISTRY. "
            f"Available: {sorted(SIGNAL_CLASS_REGISTRY)}"
        )
    config_path = configs_dir / "signals" / f"{signal_name}.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Signal config not found: {config_path}")
    return SIGNAL_CLASS_REGISTRY[signal_name](config_path=config_path)


def fetch_variables(
    catalog: VariableCatalog,
    names: list[str],
    *,
    frequency: str,
    start: date,
    end: date,
    force_refresh: bool = False,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for name in names:
        out[name] = catalog.get(
            name, frequency=frequency, start=start, end=end, force_refresh=force_refresh,
        )
    return out


def build_cost_model(signal_name: str, portfolio_config: dict[str, Any]) -> CostModel:
    """Inline defaults for Phase 6 single-signal runs (2 bps spread on rates ETFs)."""
    spread_bps: dict[str, float] = {}
    for inst in portfolio_config.get("instruments", []):
        asset = portfolio_config.get("asset_classes", {}).get(inst, "")
        if asset == "rates" or inst.endswith("_CLOSE"):
            spread_bps[str(inst)] = 2.0
    if not spread_bps:
        spread_bps["__default_treasury_etf__"] = 2.0
    logger.info("CostModel for {}: spread_bps={}", signal_name, spread_bps)
    return CostModel(spread_bps=spread_bps, market_impact_model="linear", impact_coefficient=10.0)


def render_results_md(
    result: Any,
    *,
    signal_name: str,
    start: date,
    end: date,
    method: str,
) -> str:
    summary = result.summary_dict()
    lines = [
        f"# Backtest — {signal_name}\n",
        f"**Period:** {start.isoformat()} to {end.isoformat()}  ",
        f"**Method:** {method}  ",
        f"**Test periods:** {len(result.net_returns)}  \n",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, value in summary.items():
        if isinstance(value, float):
            lines.append(f"| {key} | {value:.6g} |")
        else:
            lines.append(f"| {key} | {value} |")
    lines.append("")
    return "\n".join(lines)


def run_backtest(
    args: argparse.Namespace,
    *,
    catalog: VariableCatalog | None = None,
    reports_root: Path | None = None,
    catalog_root: Path | None = None,
    data_dir: Path | None = None,
    configs_dir: Path | None = None,
) -> Run:
    """Execute one strategy backtest and write structured outputs."""
    _catalog_root = catalog_root or CATALOG_ROOT
    _data_dir = data_dir or DATA_DIR
    _configs_dir = configs_dir or CONFIGS_DIR
    _reports_root = reports_root or (ROOT / "reports")

    signal = load_signal(args.signal, configs_dir=_configs_dir)
    if args.signal not in PORTFOLIO_BY_SIGNAL:
        raise ValueError(f"No portfolio_config mapping for signal {args.signal!r}.")
    portfolio_config = dict(PORTFOLIO_BY_SIGNAL[args.signal])

    cat = catalog if catalog is not None else build_catalog(
        catalog_root=_catalog_root, data_dir=_data_dir,
    )

    logger.info(
        "Fetching {} variable(s) for {} ({})",
        len(signal.required_variables), args.signal, signal.frequency,
    )
    data = fetch_variables(
        cat,
        list(signal.required_variables),
        frequency=signal.frequency,
        start=args.start,
        end=args.end,
        force_refresh=args.refresh,
    )

    cost_model = build_cost_model(args.signal, portfolio_config)
    engine = BacktestEngine()

    logger.info(
        "Running BacktestEngine: signal={} method={} start={} end={}",
        args.signal, args.method, args.start, args.end,
    )
    result = engine.run(
        signals=[signal],
        data=data,
        portfolio_config=portfolio_config,
        cost_model=cost_model,
        start_date=args.start,
        end_date=args.end,
        method=args.method,
    )

    mgr = OutputManager(reports_root=_reports_root)
    run = mgr.new_strategy(
        strategy_id=f"{args.signal}_{args.method}",
        config={
            "signal": args.signal,
            "method": args.method,
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "instruments": portfolio_config["instruments"],
            "refresh": args.refresh,
        },
    )

    results_md = render_results_md(
        result,
        signal_name=args.signal,
        start=args.start,
        end=args.end,
        method=args.method,
    )
    (run.path / "results.md").write_text(results_md, encoding="utf-8")

    summary = result.summary_dict()
    (run.path / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8",
    )

    if not args.no_tearsheet:
        tearsheet_path = run.plots_dir / f"{args.signal}_tearsheet.png"
        TearsheetGenerator().generate(result, output_path=tearsheet_path, show=False)
        logger.info("Tearsheet written: {}", tearsheet_path)

    run.finalize()
    logger.info(
        "Backtest complete: sharpe={:.4f} max_dd={:.2%} → {}",
        summary.get("sharpe_ratio", float("nan")),
        summary.get("max_drawdown", 0.0),
        run.path,
    )
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a single-signal backtest via VariableCatalog and BacktestEngine.",
    )
    parser.add_argument(
        "--signal",
        required=True,
        choices=signal_choices(),
        help="Signal config name (configs/signals/<name>.yaml).",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=DEFAULT_START,
        help=f"Start date (YYYY-MM-DD). Default: {DEFAULT_START.isoformat()}.",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=DEFAULT_END,
        help=f"End date (YYYY-MM-DD). Default: {DEFAULT_END.isoformat()}.",
    )
    parser.add_argument(
        "--method",
        choices=("expanding", "rolling"),
        default="expanding",
        help="In-sample history window for signal.compute (default: expanding).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the DataStore cache and re-fetch from FRED/Yahoo.",
    )
    parser.add_argument(
        "--no-tearsheet",
        action="store_true",
        help="Skip matplotlib tearsheet generation.",
    )
    args = parser.parse_args(argv)

    if args.refresh:
        logger.info("Refresh requested: catalog calls will use force_refresh=True")

    run = run_backtest(args)
    print(f"Backtest output: {run.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
