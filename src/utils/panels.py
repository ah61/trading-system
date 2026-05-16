"""Shared utilities for evaluation/portfolio panel assembly."""

from __future__ import annotations

import pandas as pd


def pack_panel_to_multiindex(
    series_by_asset: dict[str, pd.Series],
    *,
    asset_level_name: str,
) -> pd.Series:
    """Pack {asset: Series} → MultiIndex (date, asset) Series.

    Each input Series must be 1-D with a DatetimeIndex. Output uses the
    union date index, sorted ascending. The asset level uses the supplied
    `asset_level_name` (e.g. "pair", "variable", "ticker").

    `asset_level_name` is cosmetic — SignalEvaluator aligns by index
    position, not level name. Use signal-domain names ("pair", "variable")
    for readability in dumps and reports; the math doesn't depend on it.
    """
    if not series_by_asset:
        raise ValueError("series_by_asset must be non-empty.")
    panel = pd.DataFrame(
        {k: s.astype(float).sort_index() for k, s in series_by_asset.items()}
    ).sort_index()
    stacked = panel.stack(future_stack=True)
    stacked.index = stacked.index.set_names(["date", asset_level_name])
    return stacked.astype(float)
