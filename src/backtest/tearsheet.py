"""Matplotlib tearsheet generation for backtest results."""

from __future__ import annotations

from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from src.backtest.results import BacktestResult


class TearsheetGenerator:
    """Generate a compact visual performance report for a backtest."""

    def generate(
        self,
        result: BacktestResult,
        output_path: str | Path | None = None,
        show: bool = False,
    ) -> Figure:
        """Build a tearsheet figure and optionally save or display it.

        Args:
            result: Backtest result containing returns, trades, and summary statistics.
            output_path: Optional path to save the figure. Relative paths are written under
                ``reports/``.
            show: If true, display the figure interactively via ``matplotlib``.

        Returns:
            The generated matplotlib figure.
        """
        fig, axes = plt.subplots(3, 2, figsize=(16, 12), constrained_layout=True)
        flat_axes = list(axes.ravel())

        self._plot_cumulative_returns(flat_axes[0], result)
        self._plot_drawdown(flat_axes[1], result)
        self._plot_monthly_returns_heatmap(flat_axes[2], result)
        self._plot_rolling_sharpe(flat_axes[3], result)
        self._plot_trade_cost_breakdown(flat_axes[4], result)
        self._plot_summary_statistics(flat_axes[5], result)

        fig.suptitle("Backtest Tearsheet", fontsize=16)

        if output_path is not None:
            save_path = self._resolve_output_path(output_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(save_path, bbox_inches="tight")

        if show:
            plt.show()

        return fig

    @staticmethod
    def _resolve_output_path(output_path: str | Path) -> Path:
        """Resolve report output paths, placing relative files under ``reports/``."""
        path = Path(output_path)
        if path.is_absolute():
            return path
        if path.parts and path.parts[0] == "reports":
            return path
        return Path("reports") / path

    @staticmethod
    def _aligned_returns(result: BacktestResult) -> tuple[pd.Series, pd.Series]:
        """Return gross and net returns aligned on the gross return index."""
        gross = result.gross_returns.astype(float).dropna()
        net = result.net_returns.astype(float).reindex(gross.index).fillna(0.0)
        return gross, net

    def _plot_cumulative_returns(self, ax: Axes, result: BacktestResult) -> None:
        """Plot cumulative gross and net return paths."""
        gross, net = self._aligned_returns(result)
        gross_curve = (1.0 + gross).cumprod() - 1.0
        net_curve = (1.0 + net).cumprod() - 1.0

        ax.plot(gross_curve.index, gross_curve, label="Gross", linewidth=1.6)
        ax.plot(net_curve.index, net_curve, label="Net", linewidth=1.6)
        ax.axhline(0.0, color="black", linewidth=0.8, alpha=0.5)
        ax.set_title("Cumulative Returns")
        ax.set_ylabel("Return")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.3)

    def _plot_drawdown(self, ax: Axes, result: BacktestResult) -> None:
        """Plot the net return drawdown series as a filled area."""
        _, net = self._aligned_returns(result)
        wealth = (1.0 + net).cumprod()
        drawdown = wealth / wealth.cummax() - 1.0

        ax.fill_between(
            drawdown.index,
            drawdown.to_numpy(dtype=float),
            0.0,
            color="tab:red",
            alpha=0.35,
        )
        ax.plot(drawdown.index, drawdown, color="tab:red", linewidth=1.0)
        ax.axhline(0.0, color="black", linewidth=0.8, alpha=0.5)
        ax.set_title("Drawdown")
        ax.set_ylabel("Drawdown")
        ax.grid(True, alpha=0.3)

    def _plot_monthly_returns_heatmap(self, ax: Axes, result: BacktestResult) -> None:
        """Plot calendar-year rows and month columns for net monthly returns."""
        _, net = self._aligned_returns(result)
        monthly = (1.0 + net).resample("ME").prod() - 1.0
        if monthly.empty:
            ax.set_axis_off()
            ax.set_title("Monthly Returns")
            ax.text(0.5, 0.5, "No monthly returns", ha="center", va="center")
            return

        heatmap = monthly.to_frame("return")
        heatmap["year"] = heatmap.index.year
        heatmap["month"] = heatmap.index.month
        table = heatmap.pivot(index="year", columns="month", values="return").reindex(
            columns=range(1, 13)
        )

        data = np.ma.masked_invalid(table.to_numpy(dtype=float))
        image = ax.imshow(data, aspect="auto", cmap="RdYlGn", vmin=-0.1, vmax=0.1)
        ax.set_title("Monthly Returns")
        month_labels = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ]
        ax.set_xticks(np.arange(12), month_labels)
        ax.set_yticks(np.arange(len(table.index)), [str(year) for year in table.index])
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right", rotation_mode="anchor")

        for row_i, year in enumerate(table.index):
            for col_i, month in enumerate(table.columns):
                value = table.loc[year, month]
                if pd.notna(value):
                    ax.text(
                        col_i,
                        row_i,
                        f"{value:.1%}",
                        ha="center",
                        va="center",
                        fontsize=7,
                    )

        ax.figure.colorbar(image, ax=ax, fraction=0.046, pad=0.04)

    def _plot_rolling_sharpe(self, ax: Axes, result: BacktestResult) -> None:
        """Plot the rolling 252-day annualised Sharpe ratio."""
        _, net = self._aligned_returns(result)
        rolling_mean = net.rolling(window=252, min_periods=20).mean()
        rolling_std = net.rolling(window=252, min_periods=20).std(ddof=1)
        rolling_sharpe = (rolling_mean / rolling_std.replace(0.0, np.nan)) * sqrt(252.0)

        ax.plot(rolling_sharpe.index, rolling_sharpe, color="tab:purple", linewidth=1.4)
        ax.axhline(0.0, color="black", linewidth=0.8, alpha=0.5)
        ax.set_title("Rolling 252-Day Sharpe")
        ax.set_ylabel("Sharpe")
        ax.grid(True, alpha=0.3)

    def _plot_trade_cost_breakdown(self, ax: Axes, result: BacktestResult) -> None:
        """Plot gross-to-net cost drag by calendar month in basis points."""
        gross, net = self._aligned_returns(result)
        cost_bps = (gross - net) * 10_000.0
        period_cost = (
            cost_bps.resample("ME").sum()
            if isinstance(cost_bps.index, pd.DatetimeIndex)
            else cost_bps
        )

        labels = [
            str(idx.date()) if isinstance(idx, pd.Timestamp) else str(idx)
            for idx in period_cost.index
        ]
        ax.bar(labels, period_cost.to_numpy(dtype=float), color="tab:orange")
        ax.set_title("Trade Cost Breakdown")
        ax.set_ylabel("Cost (bps)")
        ax.tick_params(axis="x", labelrotation=45)
        ax.grid(True, axis="y", alpha=0.3)

    def _plot_summary_statistics(self, ax: Axes, result: BacktestResult) -> None:
        """Render key scalar performance metrics as a text table."""
        stats = result.summary_dict()
        rows = [
            ("Annualised return", self._format_percent(stats["annualised_return"])),
            ("Annualised vol", self._format_percent(stats["annualised_vol"])),
            ("Sharpe", self._format_float(stats["sharpe_ratio"])),
            ("Sortino", self._format_float(stats["sortino_ratio"])),
            ("Max drawdown", self._format_percent(stats["max_drawdown"])),
            ("Max drawdown duration", f"{int(stats['max_drawdown_duration'])} periods"),
            ("Hit rate", self._format_percent(stats["hit_rate"])),
            ("Total cost bps", self._format_float(stats["total_cost_bps"])),
            ("Turnover", self._format_float(stats["turnover_annual"])),
        ]
        text = "\n".join(f"{label:<24} {value:>12}" for label, value in rows)

        ax.set_axis_off()
        ax.set_title("Summary Statistics")
        ax.text(
            0.02,
            0.95,
            text,
            transform=ax.transAxes,
            va="top",
            ha="left",
            family="monospace",
            bbox={"boxstyle": "round", "facecolor": "white", "edgecolor": "0.75"},
        )

    @staticmethod
    def _format_percent(value: float | int) -> str:
        """Format a scalar as a percentage."""
        if not np.isfinite(float(value)):
            return "nan"
        return f"{float(value):.2%}"

    @staticmethod
    def _format_float(value: float | int) -> str:
        """Format a scalar with two decimal places."""
        if not np.isfinite(float(value)):
            return "nan"
        return f"{float(value):.2f}"
