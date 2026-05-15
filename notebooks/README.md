# Notebooks

Exploratory / research notebooks live here. **Production logic does not.**

## Lifecycle

Notebooks are scratch by design. They have a short lifecycle:

1. **Pull data** via the variable catalogue (post-5.7) or via existing source
   wrappers / CachedSource directly.
2. **Try things** — transformations, models, plots — iterate quickly.
3. **Save outputs** to `reports/exploratory/{timestamp}_{experiment_name}/`
   via `OutputManager.new_exploratory()`. This captures a manifest with the
   git commit so the run is reproducible later.
4. **Promote what works.** If a function or class becomes useful in more than
   one notebook, move it into `src/` with tests before reusing. **Never let
   notebooks import from other notebooks.**
5. **Delete or archive.** When an idea is exhausted, the notebook itself can
   be deleted. The `reports/exploratory/` output and its manifest preserve
   the result.

## Naming convention

```
YYYY-MM-DD_short_description.ipynb
```

Examples:
- `2026-05-15_fx_carry_quarterly_horizon.ipynb`
- `2026-05-20_tlt_vol_regime_explore.ipynb`
- `2026-06-01_em_fx_carry_universe_review.ipynb`

Date is when the notebook was *started*, not the most recent edit.

## What goes in `src/` versus `notebooks/`

| Rule | Notebook? | `src/`? |
|---|---|---|
| One-off plot or table | ✅ | ❌ |
| Reusable function (used in 2+ notebooks) | ❌ | ✅ |
| Code that other notebooks would benefit from | ❌ | ✅ |
| Data fetching or transformation logic | ❌ | ✅ |
| Quick eyeballing of a hypothesis | ✅ | ❌ |
| Code that needs tests | ❌ | ✅ |

When you find yourself copy-pasting from one notebook to another, that's the
signal to move the shared code to `src/`.

## Required imports

Notebooks should rely on the project's `src/` modules:

```python
import sys
from pathlib import Path
# Ensure repo root is on sys.path for `from src...` imports
sys.path.insert(0, str(Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()))

from src.reporting.output_manager import OutputManager
from src.reporting.plots import (
    plot_cumulative_returns, plot_ic_over_time, plot_drawdown,
    plot_signal_heatmap, plot_correlation_matrix,
)
```

## Output handling

Inside the notebook:

```python
mgr = OutputManager()
run = mgr.new_exploratory(
    name="fx_carry_quarterly_horizon",
    config={"horizons_tested": [1, 2, 3, 6, 12], "universe": "G10"},
)

# Save plots, tables, etc.
plot_cumulative_returns(returns, save_path=run.plots_dir / "cumret.png")
results_df.to_csv(run.path / "results.csv", index=False)
(run.path / "notes.md").write_text("Brief writeup...")

run.finalize()  # writes manifest, updates index
print(f"Saved to {run.path}")
```

## Git

Notebooks themselves are tracked. Their cleared outputs (cell outputs after
`Restart & Clear All`) keep diffs readable. Don't commit notebooks with large
embedded plots or tables — the structured outputs in `reports/` are the
authoritative artifact.
