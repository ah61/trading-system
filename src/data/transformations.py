"""Pure transformation functions for catalogue-computed variables.

Each function is stateless: no catalogue access, no persistence, no I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_PERIODS_BY_FREQUENCY: dict[str, int] = {
    "daily": 252,
    "weekly": 52,
    "monthly": 12,
    "quarterly": 4,
}

_ANNUALISATION_BY_FREQUENCY: dict[str, float] = {
    "daily": float(np.sqrt(252)),
    "weekly": float(np.sqrt(52)),
    "monthly": float(np.sqrt(12)),
    "quarterly": float(np.sqrt(4)),
}


def _periods_for_frequency(frequency: str) -> int:
    if frequency not in _PERIODS_BY_FREQUENCY:
        raise ValueError(
            f"Unsupported frequency {frequency!r}. "
            f"Expected one of {sorted(_PERIODS_BY_FREQUENCY)}."
        )
    return _PERIODS_BY_FREQUENCY[frequency]


def _annualisation_factor(frequency: str) -> float:
    if frequency not in _ANNUALISATION_BY_FREQUENCY:
        raise ValueError(
            f"Unsupported frequency {frequency!r}. "
            f"Expected one of {sorted(_ANNUALISATION_BY_FREQUENCY)}."
        )
    return _ANNUALISATION_BY_FREQUENCY[frequency]


def rolling_zscore(s: pd.Series, *, window: int) -> pd.Series:
    """Rolling z-score: (x - rolling_mean) / rolling_std.

    Uses pandas default ddof=1 (sample std). Warm-up period NaNs preserved.
    """
    mean = s.rolling(window=window, min_periods=window).mean()
    std = s.rolling(window=window, min_periods=window).std()
    return (s - mean) / std


def difference(lhs: pd.Series, rhs: pd.Series) -> pd.Series:
    """Pointwise difference: lhs - rhs. Aligns indices via pandas default."""
    return lhs - rhs


def yoy_pct_change(s: pd.Series, *, frequency: str) -> pd.Series:
    """Year-on-year percentage change.

    Periods-back inferred from frequency: daily=252, weekly=52, monthly=12,
    quarterly=4.
    """
    periods = _periods_for_frequency(frequency)
    return s.pct_change(periods=periods)


def log_return(s: pd.Series, *, window: int = 1) -> pd.Series:
    """Log return over ``window`` periods: log(s / s.shift(window))."""
    return np.log(s / s.shift(window))


def rolling_vol(
    s: pd.Series,
    *,
    window: int,
    annualised: bool,
    frequency: str,
) -> pd.Series:
    """Rolling realised volatility on a return series.

    Computes ``s.rolling(window).std()`` with pandas default ddof=1. When
    ``annualised`` is True, multiplies by sqrt(annualisation factor) for the
    given frequency.
    """
    vol = s.rolling(window=window, min_periods=window).std()
    if annualised:
        vol = vol * _annualisation_factor(frequency)
    return vol
