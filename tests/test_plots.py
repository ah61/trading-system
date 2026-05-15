"""Tests for src.reporting.plots.

These are smoke tests: they verify the functions run, accept the documented
inputs, and produce a file when ``save_path`` is provided. They do NOT verify
visual correctness — that would require image comparison or human inspection,
neither of which adds enough value to justify the cost here.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

# Force a non-interactive backend BEFORE importing pyplot anywhere else.
matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest

from src.reporting.plots import (
    plot_correlation_matrix,
    plot_cumulative_returns,
    plot_drawdown,
    plot_ic_over_time,
    plot_signal_heatmap,
)


@pytest.fixture
def simple_returns() -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    rng = np.random.default_rng(42)
    return pd.Series(rng.normal(0.0005, 0.01, 252), index=idx, name="strategy")


@pytest.fixture
def multi_returns() -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        rng.normal(0.0005, 0.01, (252, 3)),
        index=idx,
        columns=["A", "B", "C"],
    )


@pytest.fixture
def ic_series() -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=180, freq="ME")
    rng = np.random.default_rng(7)
    return pd.Series(rng.normal(0.02, 0.1, 180), index=idx, name="ic")


# ---------------------------------------------------------------------------
# Cumulative returns
# ---------------------------------------------------------------------------


def test_plot_cumulative_returns_single_saves_png(tmp_path: Path, simple_returns: pd.Series) -> None:
    out = tmp_path / "cum.png"
    result = plot_cumulative_returns(simple_returns, save_path=out)
    assert result is None
    assert out.exists()
    assert out.stat().st_size > 0


def test_plot_cumulative_returns_multi(tmp_path: Path, multi_returns: pd.DataFrame) -> None:
    out = tmp_path / "cum_multi.png"
    plot_cumulative_returns(multi_returns, save_path=out)
    assert out.exists()


def test_plot_cumulative_returns_returns_figure_when_no_save(
    simple_returns: pd.Series,
) -> None:
    fig = plot_cumulative_returns(simple_returns)
    assert fig is not None
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_plot_cumulative_returns_svg(tmp_path: Path, simple_returns: pd.Series) -> None:
    out = tmp_path / "cum.svg"
    plot_cumulative_returns(simple_returns, save_path=out, save_format="svg")
    assert out.exists()


# ---------------------------------------------------------------------------
# IC over time
# ---------------------------------------------------------------------------


def test_plot_ic_over_time_no_window(tmp_path: Path, ic_series: pd.Series) -> None:
    out = tmp_path / "ic.png"
    plot_ic_over_time(ic_series, save_path=out)
    assert out.exists()


def test_plot_ic_over_time_with_window(tmp_path: Path, ic_series: pd.Series) -> None:
    out = tmp_path / "ic_rolling.png"
    plot_ic_over_time(ic_series, window=12, save_path=out)
    assert out.exists()


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------


def test_plot_drawdown(tmp_path: Path, simple_returns: pd.Series) -> None:
    out = tmp_path / "dd.png"
    plot_drawdown(simple_returns, save_path=out)
    assert out.exists()


# ---------------------------------------------------------------------------
# Signal heatmap
# ---------------------------------------------------------------------------


def test_plot_signal_heatmap(tmp_path: Path) -> None:
    idx = pd.date_range("2020-01-01", periods=60, freq="ME")
    rng = np.random.default_rng(11)
    panel = pd.DataFrame(rng.uniform(-1, 1, (60, 5)), index=idx,
                         columns=["A", "B", "C", "D", "E"])
    out = tmp_path / "heatmap.png"
    plot_signal_heatmap(panel, save_path=out)
    assert out.exists()


# ---------------------------------------------------------------------------
# Correlation matrix
# ---------------------------------------------------------------------------


def test_plot_correlation_matrix_small_annotated(tmp_path: Path) -> None:
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    rng = np.random.default_rng(13)
    df = pd.DataFrame(rng.normal(0, 1, (252, 4)), index=idx,
                      columns=["a", "b", "c", "d"])
    out = tmp_path / "corr.png"
    plot_correlation_matrix(df, save_path=out)
    assert out.exists()


def test_plot_correlation_matrix_large_skips_annotation(tmp_path: Path) -> None:
    """20-column matrix — annotation should be auto-skipped (still runs)."""
    idx = pd.date_range("2020-01-01", periods=252, freq="B")
    rng = np.random.default_rng(13)
    df = pd.DataFrame(rng.normal(0, 1, (252, 20)), index=idx,
                      columns=[f"c{i}" for i in range(20)])
    out = tmp_path / "corr_big.png"
    plot_correlation_matrix(df, save_path=out)
    assert out.exists()


# ---------------------------------------------------------------------------
# Bad format rejection
# ---------------------------------------------------------------------------


def test_invalid_format_raises(tmp_path: Path, simple_returns: pd.Series) -> None:
    with pytest.raises(ValueError, match="save_format"):
        plot_cumulative_returns(simple_returns, save_path=tmp_path / "x", save_format="pdf")
