"""Shared plotting library for reports and notebooks.

Five reusable plot functions covering the common cases in this project:
cumulative returns, rolling IC, drawdown, signal heatmap, correlation matrix.

Each function takes a ``save_path`` argument. When provided, the figure is
written to disk and the function returns ``None``. When omitted, the function
returns the Matplotlib ``Figure`` so the caller can show or modify it.

Design choices:
- PNG at 150 DPI is the default output format. SVG available via
  ``save_format="svg"`` on any function.
- Plots are deliberately minimal: title, axes, legend if multi-series. No
  fancy styling. The point is fast, comparable, reproducible output —
  not publication quality.
- All functions accept a pre-existing ``ax`` so callers can compose multiple
  plots in one figure. If ``ax`` is None, a new figure is created.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


_DEFAULT_DPI = 150
_DEFAULT_FIGSIZE = (10, 5)


def plot_cumulative_returns(
    returns: pd.Series | pd.DataFrame,
    title: str = "Cumulative Returns",
    save_path: Path | str | None = None,
    save_format: str = "png",
    ax: plt.Axes | None = None,
) -> plt.Figure | None:
    """Cumulative-return line chart.

    Args:
        returns: Series (single line) or DataFrame (one line per column) of
            *period returns* (not prices). Log or simple — caller decides.
        title: Plot title.
        save_path: If provided, save to disk and return None.
        save_format: ``"png"`` or ``"svg"``.
        ax: Optional pre-existing axes to plot into.
    """
    fig, ax = _ensure_axes(ax)
    cum = (1 + returns).cumprod() if _looks_like_simple_returns(returns) else returns.cumsum().pipe(np.exp)
    if isinstance(cum, pd.DataFrame):
        for col in cum.columns:
            ax.plot(cum.index, cum[col], label=str(col))
        ax.legend(loc="best", fontsize=8)
    else:
        ax.plot(cum.index, cum.values)
    ax.set_title(title)
    ax.set_ylabel("Cumulative")
    ax.axhline(1.0, color="grey", lw=0.5, alpha=0.5)
    ax.grid(True, alpha=0.3)
    return _finalize(fig, save_path, save_format)


def plot_ic_over_time(
    ic_series: pd.Series,
    window: int | None = None,
    title: str = "IC over time",
    save_path: Path | str | None = None,
    save_format: str = "png",
    ax: plt.Axes | None = None,
) -> plt.Figure | None:
    """Plot IC time series, optionally with rolling-mean overlay.

    Args:
        ic_series: Per-period IC values.
        window: If provided, add a rolling-mean line of this window length.
        title: Plot title.
    """
    fig, ax = _ensure_axes(ax)
    ax.plot(ic_series.index, ic_series.values, alpha=0.4, label="IC")
    if window is not None and window > 1 and len(ic_series) > window:
        rolling = ic_series.rolling(window=window, min_periods=window).mean()
        ax.plot(rolling.index, rolling.values, color="C1", lw=1.5,
                label=f"Rolling {window}-period mean")
    ax.axhline(0.0, color="black", lw=0.5)
    ax.set_title(title)
    ax.set_ylabel("IC")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    return _finalize(fig, save_path, save_format)


def plot_drawdown(
    returns: pd.Series,
    title: str = "Drawdown",
    save_path: Path | str | None = None,
    save_format: str = "png",
    ax: plt.Axes | None = None,
) -> plt.Figure | None:
    """Underwater (drawdown) chart from a returns series."""
    fig, ax = _ensure_axes(ax)
    if _looks_like_simple_returns(returns):
        equity = (1.0 + returns).cumprod()
    else:
        equity = returns.cumsum().pipe(np.exp)
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    ax.fill_between(dd.index, dd.values, 0.0, color="C3", alpha=0.4)
    ax.plot(dd.index, dd.values, color="C3", lw=0.8)
    ax.set_title(title)
    ax.set_ylabel("Drawdown")
    ax.axhline(0.0, color="black", lw=0.5)
    ax.grid(True, alpha=0.3)
    return _finalize(fig, save_path, save_format)


def plot_signal_heatmap(
    panel: pd.DataFrame,
    title: str = "Signal heatmap",
    save_path: Path | str | None = None,
    save_format: str = "png",
    ax: plt.Axes | None = None,
    cmap: str = "RdBu_r",
) -> plt.Figure | None:
    """Heatmap of a cross-sectional signal panel.

    Args:
        panel: DataFrame with dates on the index and assets on the columns.
            Values are signal scores (typically in [-1, 1]).
        cmap: Matplotlib colormap. Diverging is appropriate for signed signals.
    """
    fig, ax = _ensure_axes(ax, figsize=(10, max(3.0, 0.3 * len(panel.columns))))
    arr = panel.values.T  # rows = assets, cols = dates — reads more naturally
    im = ax.imshow(arr, aspect="auto", cmap=cmap, interpolation="nearest",
                   vmin=-1.0, vmax=1.0, origin="lower")
    ax.set_yticks(np.arange(len(panel.columns)))
    ax.set_yticklabels([str(c) for c in panel.columns], fontsize=8)
    # Reduce x-tick density for readability.
    n = len(panel.index)
    tick_step = max(1, n // 10)
    ax.set_xticks(np.arange(0, n, tick_step))
    ax.set_xticklabels([str(panel.index[i].date()) if hasattr(panel.index[i], "date")
                        else str(panel.index[i])
                        for i in range(0, n, tick_step)],
                       rotation=45, fontsize=7, ha="right")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="signal")
    return _finalize(fig, save_path, save_format)


def plot_correlation_matrix(
    df: pd.DataFrame,
    title: str = "Correlation matrix",
    save_path: Path | str | None = None,
    save_format: str = "png",
    ax: plt.Axes | None = None,
    cmap: str = "RdBu_r",
    annotate: bool = True,
) -> plt.Figure | None:
    """Correlation heatmap with optional cell annotations."""
    corr = df.corr()
    n = len(corr.columns)
    fig, ax = _ensure_axes(ax, figsize=(max(6.0, 0.5 * n), max(5.0, 0.5 * n)))
    im = ax.imshow(corr.values, cmap=cmap, vmin=-1.0, vmax=1.0, aspect="equal")
    ax.set_xticks(np.arange(n))
    ax.set_yticks(np.arange(n))
    ax.set_xticklabels([str(c) for c in corr.columns], rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels([str(c) for c in corr.columns], fontsize=8)
    if annotate and n <= 15:  # annotations are unreadable for big matrices
        for i in range(n):
            for j in range(n):
                ax.text(j, i, f"{corr.iat[i, j]:.2f}", ha="center", va="center",
                        fontsize=7, color="black")
    ax.set_title(title)
    fig.colorbar(im, ax=ax, label="corr")
    return _finalize(fig, save_path, save_format)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_axes(
    ax: plt.Axes | None, figsize: tuple[float, float] = _DEFAULT_FIGSIZE
) -> tuple[plt.Figure, plt.Axes]:
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.figure
    return fig, ax


def _finalize(
    fig: plt.Figure, save_path: Path | str | None, save_format: str
) -> plt.Figure | None:
    """Save to disk if requested and return None; otherwise return the figure."""
    fig.tight_layout()
    if save_path is None:
        return fig
    if save_format not in {"png", "svg"}:
        raise ValueError(f"save_format must be 'png' or 'svg'; got {save_format!r}")
    save_path = Path(save_path)
    if save_path.suffix == "":
        save_path = save_path.with_suffix(f".{save_format}")
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=_DEFAULT_DPI, format=save_format, bbox_inches="tight")
    plt.close(fig)
    return None


def _looks_like_simple_returns(s: pd.Series | pd.DataFrame) -> bool:
    """Heuristic: simple returns are typically |x| < 0.5 in most observations;
    cumulative log returns can be much larger. Used to decide whether to
    compound via (1+r) or via exp(cumsum(r)).
    """
    arr = s.values.flatten() if isinstance(s, pd.DataFrame) else s.values
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0:
        return True
    return float(np.median(np.abs(arr))) < 0.5
