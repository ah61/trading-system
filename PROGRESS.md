# PROGRESS.md
# Build Log and Current Status

**Last Updated:** 2026-05-15
**Tracks ROADMAP.md version:** 0.2

---

## Current Status

| Field | Value |
|---|---|
| **Roadmap phase** | Phase 5 — Signal Hardening |
| **Active milestone** | 5.7 — Variable Catalogue (complete; deferred test/refresh items remain) |
| **Tests** | 151 passing |
| **Next action** | Write `scripts/backtest_strategy.py` (Phase 6 prerequisite) — see handoff note below |

---

## Completed Phases

### Phase 0 — Environment Setup ✅
- Repo created, venv, .gitignore, directory structure

### Phase 1 — Data Pipeline ✅
- Milestone 1.1: DataStore (DuckDB) — 4 tests
- Milestone 1.2: FREDSource + Alfred vintage — 4 tests
- Milestone 1.3: YahooSource — 4 tests
- Milestone 1.4: DataCleaner — 5 tests
- Tag: `phase1-complete`
- Fix: `fetch_vintage` DataFrame parsing (fredapi returns DataFrame not Series)
- Fix: YahooSource MultiIndex columns (yfinance v0.2+ returns MultiIndex)
- Verified: vintage CPI data differs from revised by 1.77 points

### Phase 2 — Signal Engine ✅
- Milestone 2.1: Signal base class + SignalMetrics — 5 tests
- Milestone 2.2: FXCarrySignal — 3 tests
- Milestone 2.3: RatesTrendSignal — 4 tests
- Milestone 2.4: EquityMomentumSignal — 4 tests
- Milestone 2.5: SignalEvaluator (IC, ICIR, hit rate, Sharpe, decay) — 6 tests
- Milestone 2.6: Corrections (DSR, PBO, Hansen SPA) — 6 tests
- Tag: `phase2-complete`
- Fix: SignalEvaluator now handles single-asset and multi-asset (cross-sectional) signals correctly

### Phase 3 — Portfolio Engine ✅
- Milestone 3.1: CostModel — 5 tests
- Milestone 3.2: PositionSizer (vol target, risk parity) — 4 tests
- Milestone 3.3: PortfolioConstructor (gross/net limits, trades) — 5 tests
- Tag: `phase3-complete`

### Phase 4 — Backtest Engine ✅
- Milestone 4.1: BacktestEngine + WalkForwardEngine — 5 tests
- Milestone 4.2: CPCVEngine — 4 tests
- Milestone 4.3: TearsheetGenerator — 3 tests
- Tag: `phase4-complete`
- End-to-end validation on real data
- Rates Trend on TLT (2010-2024): Sharpe -0.52 OOS — expected, 2022-2023 rates shock
- Tag: `phase4-validated`

---

## Phase 5 — Signal Hardening 🔄 (in progress)

### Pre-Phase 5 Signal Evaluation (preparation work)

Completed before Phase 5 was formally scoped. Results motivated the Phase 5
milestones now in ROADMAP v0.2. Historical results preserved at the bottom
of this file (`Pre-5.2 Signal Evaluation Results`).

- [x] All exploratory signal evaluations run with manual resampling
- [x] Hit rate bug fixed (exclude zero signals)
- [x] `future_stack` deprecation fixes
- [x] Phase 5 go-decision recorded (see "Historical Decision Record" at bottom)

### Milestone 5.2 — Frequency Layer ✅

**Code:** 2026-05-14. **Cleanup + bug fixes:** 2026-05-14.

**Code changes:**
- [x] Add `frequency` parameter to `SignalEvaluator.evaluate()` — `'daily'` | `'weekly'` | `'monthly'`
- [x] Resampling rule: signal = first non-zero per period; carry forward all-zero periods
- [x] Resampling rule: log returns summed within each period (CONVENTIONS §3.2)
- [x] Forward-return shift expressed in periods at chosen frequency: `shift(-(H+1))`
- [x] Annualisation factor scales with frequency: 252 / 52 / 12
- [x] Rolling-IC window scales with frequency: 63 / 13 / 3 (~1 quarter at each)
- [x] Add `frequency` field to `SignalMetrics` dataclass (breaking change)
- [x] Suppress `ConstantInputWarning` from monthly-on-daily evaluation
- [x] 13 new tests in `tests/test_evaluation.py` (72 → 85 passing)
- [x] Backward compatible: omitting `frequency` defaults to `'daily'`,
      numerically identical to pre-5.2 behaviour

**Cleanup changes:**
- [x] `configs/signals/fx_carry.yaml` updated → `frequency: monthly` (was `daily`)
- [x] `configs/signals/equity_momentum.yaml` — already `frequency: monthly`, confirmed
- [x] `configs/signals/rates_trend.yaml` — already `frequency: daily`, confirmed
- [x] `scripts/evaluate_signals.py` written — reproducible runner for all three signals
      using the new frequency layer, no manual resampling. Writes Markdown report
      to `reports/signal_evaluation_phase1.md`.
- [x] Run `python scripts/evaluate_signals.py` and capture results
      (see "Re-evaluation Results (5.2)" below)
- [ ] Clean up `tests/Archive/signal_evaluator.py` (dead code left from earlier archive)

**Bug fixes discovered during runner execution (2026-05-14):**
- [x] **Library bug:** `SignalEvaluator.evaluate()` multi-asset path had a
      heuristic that picked unshifted returns over shifted ones when both produced
      valid pairings. Result: horizon parameter was ignored for any case where
      input returns were already valid forward returns. Identical IC/ICIR across
      all horizons for Equity Momentum is the symptom that caught it.
      **Fix:** removed heuristic; evaluator now always applies `shift(-(horizon+1))`
      per asset. Contract is single-meaning: input is 1-period log returns.
- [x] **Test debt:** Three existing tests (`test_ic_mean_near_zero_for_random_signal`,
      `test_icir_computed_correctly`, `test_n_observations_correct`) pre-shifted
      returns manually, depending on the broken heuristic to pick "as_is".
      Updated to pass 1-period returns directly. Two "perfect signal" tests
      (`test_ic_mean_positive_for_perfect_signal`, `test_hit_rate_one_for_perfect_signal`,
      `test_signal_sharpe_positive_for_good_signal`) now use `np.roll(signal, +(h+1))`
      to pre-arrange returns so post-shift alignment is exact.
- [x] **Runner bug:** `panel.stack(future_stack=True, dropna=False)` is invalid
      in newer pandas. Removed `dropna=False`. FX Carry was crashing on this.

### Milestone 5.3 — Variable Library ✅

**Completed 2026-05-14.**

- [x] `configs/data/variables/macro.yaml` — 14 FRED series (DFF, GS10, GS2, T10YIE,
      CPIAUCSL, PAYEMS, plus 7 G10 interbank rates including 5 placeholders for 5.5)
- [x] `configs/data/variables/market.yaml` — 11 Yahoo tickers (4 rate ETFs,
      7 FX spot pairs including 5 placeholders for 5.5)
- [x] `configs/data/variables/transformations.yaml` — 7 transformed variables
      (DFF z-score, yield curve slope, CPI YoY, TLT log returns, TLT 63d vol,
      EURUSD log returns, GBPUSD log returns)
- [x] `configs/data/derived_variables.yaml` — 4 derived (3 signals + 1 regime
      indicator placeholder for 5.7)
- [x] `src/data/variable_catalog.py` — `VariableCatalog` class with load,
      strict validation, lineage walk, used-by (direct + transitive)
- [x] 14 new tests in `tests/test_variable_catalog.py` (85 → 99 passing)
- [x] Real catalog validates strict-mode load: 35 variables, no unresolved refs

**Design decisions recorded:**
- Strict validation on by default; `strict=False` available for partial dev states
- `used_by` is computed from the inputs/source_variable graph, not authored in YAML
- Catalog is read-only and does not wire into `DataStore` for 5.3 (5.4 concern)
- `VariableCatalog.get_lineage()` walks the variable dependency graph;
  `DataStore.get_lineage()` traces storage-layer materialisation — these answer
  different questions and are kept separate
- File-layer convention enforced: macro.yaml/market.yaml hold raw,
  transformations.yaml holds transformed, derived_variables.yaml holds derived

### Milestone 5.1 — Reference Documents ⬜
- [ ] `docs/phase2_signal_engine.docx`
- [ ] `docs/phase3_portfolio_engine.docx`
- [ ] `docs/phase4_backtest_engine.docx`

### Milestone 5.4 — Data Persistence ✅

**Completed 2026-05-14.**

- [x] `src/data/cached_source.py` — `CachedSource` wrapper composes any
      `DataSource` with a `DataStore`. Exposes `fetch_or_load()` (cache-then-fetch)
      and a `fetch()` compatibility shim that defaults to daily frequency.
- [x] Raw layer: written on first fetch, read on subsequent calls. Range
      extension supported via "overwrite if superset" semantics.
- [x] Adjusted layer: optional `DataCleaner` parameter; cleaned data is
      written to `adjusted.duckdb` at version 1.
- [x] Failure isolation: if `DataCleaner.clean()` raises, raw is still cached
      but adjusted is NOT written (cache is not corrupted by partial state).
- [x] `force_refresh=True` parameter on `fetch_or_load` bypasses the cache.
      Refetched range must be a superset of the cached one (refusing to clobber
      with a narrower range).
- [x] Cache check is business-day aware: a calendar end on a weekend doesn't
      cause spurious misses when the stored data covers all business days in
      the request.
- [x] `scripts/evaluate_signals.py` refactored to route fetches through
      `CachedSource`. New `--refresh` CLI flag exposed for forced re-fetches.
- [x] 14 new tests in `tests/test_cached_source.py` (99 → 113 passing).

**Post-completion bug fixes (caught during runner verification, 2026-05-14):**
- [x] **Ticker sanitisation**: `DataStore._validate_ident` rejects identifiers
      with non-alphanumeric chars; Yahoo FX tickers (`EURUSD=X` etc.) and
      tickers with dashes/dots couldn't be cached. Added `_sanitize_ticker`
      that maps `= - . ^ /` → `_` for store keys; vendor still sees the
      original ticker on fetch.
- [x] **Boundary slack**: cache-coverage check was strict at calendar
      boundaries, causing spurious misses when (i) the calendar end fell on a
      weekend, (ii) DuckDB's `timestamptz` storage shifted dates by ±1 day, or
      (iii) the calendar start was a holiday (e.g. Jan 1). Added 5-business-day
      slack at the start and 1-day slack at the end.
- [x] 6 new boundary/sanitisation tests (113 → 119 passing).

**Design decisions recorded:**
- `CachedSource` is a composition wrapper, not an inheritance subclass of
  `DataSource`. Sources stay storage-agnostic; the wrapper decides when to
  hit the network. This keeps the 99 pre-5.4 tests stable.
- Cache key is `(source_name, ticker, frequency)`; date range is handled by
  storing the union of all fetched dates and slicing on read.
- `VariableCatalog.get_lineage()` and `DataStore.get_lineage()` remain separate
  concerns; 5.4 does not bridge them. Catalog walks variable dependencies;
  store traces storage materialisation. Both useful, both distinct.

### Milestone 5.5 — G10 FX Expansion ✅

**Completed 2026-05-14.**

- [x] Add AUD, NZD, CAD, JPY, CHF rate series — declared in
      `configs/data/variables/macro.yaml` (Milestone 5.3, made live here)
- [x] Add corresponding FX spot pairs to `configs/data/variables/market.yaml`
      (already declared in 5.3, made live here)
- [x] **Fix `_iter_pairs` to anchor on USD base** — changed from
      `(a, b) for a != b` (which produced 21 ordered pairs for 7 currencies,
      double-counting every trade) to `[(ccy, base) for ccy in cur if ccy != base]`,
      producing 6 USD-anchored pairs. Pair labels now mechanically `<non-USD>/USD`.
- [x] Surface `base_currency` parameter through YAML config; defaults to "USD".
- [x] Update `configs/signals/fx_carry.yaml` — 7 currencies in `rate_series`,
      `base_currency: USD`, `frequency: monthly` (was already monthly from 5.2).
- [x] Update `scripts/evaluate_signals.py::fx_carry_forward_returns` —
      replaces hardcoded 2-pair EUR/GBP negation logic with a 7-pair
      USD-anchored mapping. Negation only applied where Yahoo ticker convention
      requires it (USDXXX=X for JPY/CAD/CHF).
- [x] Tests still pass at 119/119; no test updated (no test referenced
      pre-5.5 pair labels).

**Re-evaluation results (vs pre-5.5 4-pair version):**

| Horizon | IC | ICIR | Hit | Sharpe | N |
|---------|---------|---------|------|---------|------|
| 1mo | +0.0059 | +0.0106 | 0.5014 | -0.0737 | 1079 |
| 2mo | -0.0056 | -0.0100 | 0.5009 | -0.1051 | 1072 |
| 3mo | -0.0015 | -0.0027 | 0.5005 | -0.0573 | 1065 |
| 6mo | -0.0011 | -0.0019 | 0.4990 | -0.0720 | 1044 |

Observations:
- N rose ~50% (700 → 1070) as expected from 4 → 6 pairs.
- IC dropped to near zero with all-negative ICIR across horizons.
- Cross-sectional carry has been broadly unprofitable post-2010 at monthly
  frequency in this universe. Whether the underlying carry premium is genuinely
  absent or whether the monthly-rate-differential proxy is too coarse remains
  an open question — to be revisited when Stage 2 daily rate data and proper
  forward-spot basis are available.
- This is a real signal-quality finding, not a methodology bug. Phase 5 is
  infrastructure; signal-quality testing comes next.

### Phase 5 reordering (2026-05-14)

Original Phase 5 had only three remaining milestones (5.6 universe expansion,
5.7 regime filter). After a design discussion, the plan was substantially
restructured. Documented in `DESIGN_DECISIONS.md`. New milestone list:

| # | Milestone | Status |
|---|---|---|
| 5.6 | Output Container + Reporting Hygiene | ✅ complete |
| 5.7 | Variable Catalogue: stateful lookup | ✅ complete (deferred test/refresh items remain) |
| 5.8 | Transformation Pipeline + Derived Variable Persistence | ⬜ |
| 5.9 | FX Carry Quarterly Horizon Experiment | ⬜ |
| 5.10 | Universe Expansion (FX EM, equities, rates) | ⬜ |
| 5.11 | Rates Trend Regime Filter (was 5.7) | ⬜ |
| 5.12 | IBSource (FX focus) | ⬜ |
| 5.13 | Forward-Spot Basis Carry Signal | ⬜ |
| 5.14 | Vol Conditioning Experiment on FX Carry | ⬜ |
| 5.16 | Portfolio layer Series unification (LOW PRIORITY placeholder) | ⬜ |

### Milestone 5.6 — Output Container + Reporting Hygiene ✅

**Completed 2026-05-15.**

- [x] `src/reporting/manifest.py` — `Manifest` dataclass + `capture_manifest()`
      that grabs git commit, dirty state, timestamp, Python/platform, and
      config snapshot. JSON read/write round-trip.
- [x] `src/reporting/output_manager.py` — `OutputManager` class with three
      factory methods (`new_exploratory`, `new_variable`, `new_strategy`)
      routing to `reports/{exploratory,variables,strategies}/{ts}_{name}/`.
      Each run gets a `plots/` subdir, manifest, and index.csv entry.
- [x] `src/reporting/plots.py` — 5 reusable plot functions: cumulative returns,
      IC over time, drawdown, signal heatmap, correlation matrix. PNG default
      at 150 DPI; SVG via `save_format` parameter.
- [x] `notebooks/README.md` — naming convention + lifecycle rules
      (research stays in notebooks; promoted code goes to `src/` with tests).
- [x] `.gitignore` updated: reports content gitignored, but
      `reports/variables/index.csv` and `reports/strategies/index.csv` tracked
      so formal runs are visible at a glance.
- [x] `scripts/evaluate_signals.py` refactored to write via
      `OutputManager.new_variable()`. Each run now produces a
      `reports/variables/{ts}_signal_evaluation/` folder with manifest,
      `results.md`, and `results.csv`. Legacy
      `reports/signal_evaluation_phase1.md` still written for backward compat.
- [x] Existing `reports/signal_evaluation_phase1.md` migrated to
      `reports/variables/baseline/results.md`.
- [x] 28 new tests across `test_manifest.py`, `test_output_manager.py`,
      `test_plots.py` (119 → 147 passing).

### Milestone 5.7 — Variable Catalogue (checkpoint, partial)

**Status:** Stable subset shipped 2026-05-15. The breaking signal-interface
change was deferred to a follow-up session to avoid hitting the context limit
mid-refactor — that work is documented in the next subsection.

**Shipped in this checkpoint:**
- [x] DESIGN_DECISIONS.md DD-007 (variable naming convention)
- [x] DESIGN_DECISIONS.md DD-008 (template-based universe handling)
- [x] `CPIAUCSL` → `CPI_HEADLINE` rename in `configs/data/variables/macro.yaml`
- [x] Updated `source_variable` reference in `configs/data/variables/transformations.yaml`
- [x] `VariableCatalog` promoted to stateful: accepts `sources` and `store`,
      adds `get(name, frequency, start, end) -> pd.Series` for raw variables
      with native or resampled frequency
- [x] `VariableCatalog.get()` (5.3, returns VariableSpec) renamed to `get_spec()`
      to free the `get()` name for data access
- [x] Universe expansion: `configs/data/universes/*.yaml` template-based,
      auto-expanded into per-ticker VariableSpec at load time
- [x] Backward-compatible: existing 14 test_variable_catalog.py tests still
      pass against the new code (with one trivial rename `get → get_spec`)
- [x] `configs/data/universes/sp500_current.yaml` example with the new template
      format (placeholder ticker list — copy actual universe contents on migrate)

### Milestone 5.7 — Variable Catalogue (continued, signal-interface change shipped 2026-05-15) ✅

Signal-interface change (the breaking refactor deferred from the 5.7
checkpoint) is now complete. Engine boundary refactored under "option A
hybrid" — see below.

**Shipped in this session:**

Signal layer (interface change):
- [x] `src/signals/base.py`: `compute(data: Dict[str, pd.DataFrame])` →
      `compute(data: Dict[str, pd.Series])`. `required_data` →
      `required_variables`.
- [x] `src/signals/rates/trend.py`: consumes Series by catalogue name
      (`TLT_CLOSE`).
- [x] `src/signals/fx/carry.py`: `rate_series` RHS values are now catalogue
      variable names (`DFF`, `EUR_RATE`, `GBP_RATE`, ...). `required_variables`
      derived from `rate_series` values (sorted, deduplicated).
- [x] `src/signals/equities/momentum.py`: reads universe from
      `configs/data/universes/{universe}.yaml` (new template-format path);
      applies `variable_name_pattern` to produce catalogue names
      (`AAPL_CLOSE`, `MSFT_CLOSE`, ...). Asset level in signal output is the
      catalogue variable name, not the raw ticker.
- [x] `configs/signals/rates_trend.yaml`: `ticker: TLT` → `variable: TLT_CLOSE`.
- [x] `configs/signals/fx_carry.yaml`: `rate_series` RHS migrated to catalogue
      names. `data_requirements` block removed (now derived).
- [x] `tests/test_signals.py`: all fixtures pass `pd.Series` directly. 4 new
      contract tests added (20 tests total in the file).
- [x] `scripts/evaluate_signals.py`: routes all data access through
      `VariableCatalog.load(...)` and `catalog.get(name, frequency, start, end)`.
      No direct `FREDSource`/`YahooSource`/`CachedSource` calls in the
      evaluation paths.

Backtest engine layer (option A hybrid):
- [x] `src/backtest/engine.py`: public API now `Dict[str, pd.Series]`.
      `portfolio_config['prices_key']` removed; replaced by
      `portfolio_config['instruments']` (explicit list of catalogue variable
      names representing tradeable instruments). Engine assembles the price
      panel internally at the portfolio handoff via `_assemble_price_panel`.
- [x] `src/backtest/walk_forward.py`: type-hint update only. Orchestration
      logic unchanged.
- [x] `src/backtest/cpcv.py`: `_restrict_data_to_timestamps` slices Series
      instead of DataFrames; type hints updated.
- [x] `tests/test_backtest.py`: data fixtures now build `Dict[str, pd.Series]`
      directly (helper renamed `_prices` → `_price_data`). `portfolio_config`
      updated to use `instruments` + `asset_classes` instead of `prices_key`.
      Spy signals (`_SpyNoLookaheadSignal`, `_MomentumSignal`) declare
      `required_variables = ["AAA", "BBB"]` and read Series directly.

**Deliberately NOT changed (option A hybrid rationale):**

The portfolio layer was deliberately left untouched.
`src/portfolio/sizing.py`, `src/portfolio/constructor.py`, and
`src/portfolio/costs.py` still consume a wide price-panel DataFrame
(columns = instruments, index = dates) — the panel is now built inside the
engine immediately before the handoff to `PortfolioConstructor.construct`.

Rationale:
- Cross-sectional vol math and risk parity are naturally expressed on wide
  panels. Rewriting them to operate on dict-of-Series would mean either
  rebuilding a panel internally (= zero functional change with extra ceremony)
  or rewriting the math to loop over Series (= churn risking subtle bugs in
  working code).
- The 5.7 contract is correct *at the signal layer*. The portfolio layer is a
  distinct abstraction — a panel of instruments at one frequency — that the
  catalogue contract doesn't naturally describe.
- The translation point is one function (`_assemble_price_panel` in
  `engine.py`) and is explicitly commented as the contract boundary.

**Test count:** 147 → **151 passing** (verified 2026-05-15 via
`pytest tests/ --tb=no -q`). Signal-layer changes added 4 net tests
(test_signals went 16 → 20). Backtest tests are refactored in place, same
count.

**End-to-end coverage gap (read this carefully):**

After this session, three different code paths each have *partial* validation:

1. `signal.compute` ← unit tests in `test_signals.py` exercise this with
   synthetic Series fixtures. Strong coverage.
2. `catalogue → signal → SignalEvaluator` ← `scripts/evaluate_signals.py`
   drives this end to end with real data. Verified working.
3. `catalogue → engine → portfolio` ← **nothing drives this end to end.**
   Tests in `test_backtest.py` synthesise their own Series fixtures and don't
   construct a real catalogue. The runner (`evaluate_signals.py`) doesn't
   invoke the backtest engine.

"Tests passing" therefore does not mean "the system runs end to end." A real
strategy backtest (catalogue → engine → portfolio → tearsheet) needs a driver
script before paper trading. Suggested: a `scripts/backtest_strategy.py` that
mirrors `evaluate_signals.py`'s structure but calls `BacktestEngine.run`
instead of `SignalEvaluator.evaluate`. Out of scope for 5.7; flagged as a
Phase 6 prerequisite.

**Known limitations introduced by this refactor (not bugs, but worth
documenting):**

- **`scripts/evaluate_signals.py --refresh` is advisory.** The catalogue's
  `get()` doesn't expose `force_refresh`. To force a refresh, delete
  `data/raw/raw.duckdb` before running. Cleanest follow-up: add
  `force_refresh: bool = False` to `VariableCatalog.get()` and thread it
  through.
- **Engine prices the portfolio off the first instrument.** Pre-5.7 behaviour
  was identical: the engine used a single-column reference (either the
  `"close"` column if it existed, else the first column). The post-5.7 engine
  uses the first instrument in the `instruments` list. This is a known
  limitation for multi-instrument backtests; not introduced by 5.7 but now
  visibly attributable to it. Real multi-instrument portfolio returns need to
  weight per-instrument forward returns by per-instrument weights — a future
  refactor.
- **FX pair labels are mechanical (`<non-USD>/USD`)** — see DD-005.
  Display-layer translation is a Phase 6 prerequisite. Unchanged from before.

**Deferred items from the original 5.7 list:**

- [ ] New tests for the catalogue stateful API (`tests/test_variable_catalog.py`):
      catalogue.get returning Series, universe expansion producing per-ticker
      specs, resampling on `get()`. These test the catalogue itself, not the
      signals; not in this session's scope.
- [ ] `force_refresh: bool` plumbed through `VariableCatalog.get()` and the
      runner.

**Future milestone placeholder — 5.16 Portfolio layer Series unification
(LOW PRIORITY):**

If downstream use cases ever require it, the portfolio layer could be unified
onto the Series-per-variable contract instead of the wide-panel DataFrame.
Cost: rewriting `PositionSizer.volatility_target`, `PositionSizer.risk_parity`,
`PortfolioConstructor.construct`, and the `CostModel.apply_costs` call paths.
Benefit: contract uniformity end-to-end.

Don't do this speculatively. Wait for a concrete need (e.g., a signal that
produces per-variable forward returns and the engine needs to consume them
without coercing through a panel).

### Handoff note for next session

> Milestone 5.7 is now complete. Signal-interface change + engine boundary
> refactor both shipped. **151 tests passing** (verified 2026-05-15). The
> `evaluate_signals.py` runner uses the catalogue end-to-end and is verified.
> The `catalogue → engine → portfolio` path is **not** end-to-end verified —
> there is no script yet that drives a full backtest from the catalogue.
> Write `scripts/backtest_strategy.py` before paper trading.
>
> Still deferred from 5.7: catalogue stateful-API tests
> (`tests/test_variable_catalog.py`) and `force_refresh` plumbing through
> `VariableCatalog.get()`. Both are small, isolated, and can be done in any
> order.
>
> See PROGRESS.md §5.7 (continued) for the option-A hybrid rationale
> explaining why the portfolio layer was deliberately not refactored. If you
> ever want full Series-throughout uniformity, that's milestone 5.16
> placeholder — but read the rationale first; it explains why I'd push back
> on doing it speculatively.

---

### IB account setup ✅ (2026-05-14)

Prerequisites for Milestone 5.12 completed:

- Paper account: username `phbojg566`, account number `DUP730772`
- Real-time market data sharing: enabled (paper ↔ live account)
- Subscriptions active (all fee-waived): IDEALPRO FX, US Real-Time NCS, US/EU Bond Quotes
- IB Gateway installed, paper login tested, status all green
- API Settings: port 7497, ActiveX/Socket Clients enabled, message log file enabled,
  Read-Only API unchecked, Trusted IPs empty (localhost-only)

Outstanding for later: CME futures real-time subscription (~$10/month) needed for
Phase 7.2 Treasury futures work.

---

## Known Issues / Technical Debt

### Code quality / cleanups outstanding

- **`tests/Archive/signal_evaluator.py`** is dead code — leftover from the
  earlier archive step that fixed pytest collection. Delete the file or move
  the whole `tests/Archive/` directory outside `tests/`.
- **`FXCarrySignal._iter_pairs` generates both (a, b) and (b, a) directions**
  — double-counts trades (USD/EUR and EUR/USD encode the same position).
  Cross-section breadth is 3 trades, not 6 pairs. Fix scheduled for Milestone 5.5
  (see above).

### Data
- `GS10` from FRED returns only 109 rows (monthly frequency) — confirm during
  Milestone 5.3 variable library (declare `frequency: monthly` in catalog)
- Equity momentum universe is survivorship-biased (current S&P 500 only);
  Stage 2 / ROADMAP Phase 7.2 fix (CRSP point-in-time)
- FX Carry signal fires monthly — EUR/GBP rate series are monthly FRED frequency.
  Daily EUR/GBP rate data needed for daily carry — Stage 2 / ROADMAP Phase 7.2 (Bloomberg)
- FX Carry cross-section too thin — only 3 currencies (USD/EUR/GBP), 4 active pairs.
  **Resolved by Milestone 5.5** (G10 expansion to 7 currencies, 6 USD-anchored pairs)
- FRED API flaps intermittently with HTTP 500 errors — **resolved by 5.4**.
  First successful fetch is cached to `data/raw/raw.duckdb`; subsequent runs
  read from the store. Use `--refresh` to force re-fetch.
- DataStore was empty pre-5.4 — **resolved by 5.4**. `scripts/evaluate_signals.py`
  now populates the store on first run.

### Signals
- Rates Trend is regime-dependent — fails in post-trend consolidation (2023-2024).
  Fixed in Milestone 5.11 (regime filter, was 5.7 pre-reorder)
- **FX pair labels do not follow market convention.** From Milestone 5.5 onward,
  FX Carry produces mechanical pair labels of the form `<non-USD>/USD` for all
  pairs (e.g. `JPY/USD`, `CAD/USD`, `CHF/USD`). Market convention for those
  three uses USD-first ordering (`USD/JPY`, `USD/CAD`, `USD/CHF`) because the
  resulting *quoted price* is greater than 1. The signal math is correct under
  either convention; the issue is purely cosmetic. **Why it matters:** before
  using Interactive Brokers for paper trading (Phase 6) and before showing
  results to traders, add a display-layer translation that emits market-
  convention labels in reports and order tickets. Internal signal output and
  storage keys stay mechanical. **Add as a Phase 6 prerequisite.**

### Documentation
- `ARCHITECTURE.md` was bumped to v0.2 on 2026-05-14: renamed prior "Phase 1 / Phase 2"
  references to "Stage 1 / Stage 2" to avoid collision with new ROADMAP phase numbering
- `SignalMetrics` in `ARCHITECTURE.md` §4.3 updated to include `frequency` field
  (5.2 breaking change). Confirmed no external constructors via grep on 2026-05-14

---

## Re-evaluation Results (5.2)

**Generated:** 2026-05-14 via `python scripts/evaluate_signals.py` over period 2010-01-01 to 2024-12-31.
**Report:** `reports/signal_evaluation_phase1.md` (auto-generated, do not hand-edit).

### Rates Trend (daily frequency)

| Horizon | IC | ICIR | Hit Rate | Sharpe | N |
|---------|------|------|----------|--------|------|
| 1d  | +0.0096 | +0.0930 | 0.5111 | +0.1833 | 3571 |
| 5d  | +0.0036 | +0.0327 | 0.5094 | +0.0938 | 3567 |
| 21d | +0.0123 | +0.1254 | 0.5086 | +0.1691 | 3551 |
| 63d | +0.0018 | +0.0151 | 0.5130 | -0.0492 | 3509 |

**Sanity check vs historical (manual resampling):** Numbers match closely
(historical 1d IC was 0.0117, new 0.0096; same magnitude, same sign across
all horizons). Frequency layer reproduces manual resampling. ✓

### FX Carry (monthly frequency)

**Pre-5.5 (4-pair, double-counted):**

| Horizon | IC | ICIR | Hit Rate | Sharpe | N |
|---------|------|------|----------|--------|------|
| 1m | -0.0010 | -0.0015 | 0.5028 | -0.1394 | 708 |
| 2m | -0.0161 | -0.0236 | 0.5057 | -0.1670 | 704 |
| 3m | -0.0070 | -0.0104 | 0.5000 | -0.0996 | 700 |
| 6m | +0.0184 | +0.0268 | 0.5029 | -0.0533 | 688 |

**Post-5.5 (6-pair USD-anchored, full G10):**

| Horizon | IC | ICIR | Hit Rate | Sharpe | N |
|---------|------|------|----------|--------|------|
| 1m | +0.0059 | +0.0106 | 0.5014 | -0.0737 | 1079 |
| 2m | -0.0056 | -0.0100 | 0.5009 | -0.1051 | 1072 |
| 3m | -0.0015 | -0.0027 | 0.5005 | -0.0573 | 1065 |
| 6m | -0.0011 | -0.0019 | 0.4990 | -0.0720 | 1044 |

**Read:** Cross-section now reflects the full G10 set (USD anchor + EUR, GBP,
AUD, NZD, CAD, JPY, CHF) with mechanically-consistent pair construction. N
grew ~50% as expected. IC is near zero across horizons, ICIR effectively
zero — this is a real signal-quality finding, not a methodology bug. The
post-2010 monthly carry trade has been broadly unprofitable in this universe.
Whether the signal is genuinely absent or whether the monthly-rate-differential
proxy is too coarse is an open question for Stage 2 (daily forward rates,
proper forward-spot basis carry).

### Equity Momentum (monthly frequency)

| Horizon | IC | ICIR | Hit Rate | Sharpe | N |
|---------|------|------|----------|--------|------|
| 1m | +0.0484 | +0.0870 | 0.4900 | +0.4425 | 1353 |
| 2m | +0.0359 | +0.0798 | 0.4989 | +0.5007 | 1343 |
| 3m | +0.0343 | +0.0766 | 0.5026 | +0.4520 | 1333 |
| 6m | -0.0074 | -0.0170 | 0.5042 | +0.3633 | 1303 |

**Sanity check vs historical:** Shape matches (positive IC at 1-3m, fading to
zero at 6m). Best monthly IC moved from 3m (historical 0.0309) to 1m (new
0.0484); same order of magnitude. Differences explained by exact universe
composition and date-range edges. ICIR still below 0.3 threshold — Milestone 5.10
(expanded universe) is the fix.

### Summary of differences vs Pre-5.2

| Signal | Pre-5.2 verdict | 5.2 verdict | Change |
|---|---|---|---|
| Rates Trend | FAIL (IC 0.0117, ICIR 0.1120 at 1d) | Same (IC 0.0096, ICIR 0.0930 at 1d) | Within noise. |
| FX Carry | BORDERLINE (IC +0.1239 at 2m) | NEUTRAL (IC near zero, no horizon clearly best) | Material — methodology differs. Resolves in 5.5. |
| Equity Momentum | BORDERLINE (IC 0.0309 at 3m) | Same (IC 0.0484 at 1m, 0.0359 at 2m) | Within noise. Shape preserved. |

**Bottom line:** Frequency layer reproduces historical results for Rates Trend
and Equity Momentum. FX Carry differs but in a way that is expected given the
`_iter_pairs` double-counting issue — the methodology has known problems that
Milestone 5.5 will fix. None of the new results change the Phase 6 paper-trading
go-decision.

---

## Pre-5.2 Signal Evaluation Results (historical)

Produced via manual resampling before the frequency layer existed. Preserved
for comparison against the 5.2 re-evaluation.

### Summary

| Signal | Best Horizon | IC Mean | ICIR | Hit Rate | Sharpe | DSR | Decision |
|--------|-------------|---------|------|----------|--------|-----|----------|
| Rates Trend | 1d | 0.0117 | 0.1120 | 0.5117 | 0.2202 | 0.000 | FAIL |
| FX Carry | 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 | N/A | BORDERLINE |
| Equity Momentum | 3m | 0.0309 | 0.0675 | 0.4924 | 0.3280 | 0.000 | BORDERLINE |

### Rates Trend — Full Results + Pre/Post 2022 Split (TLT)

#### Full period (2010-2024, 3522 obs)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0117 | 0.1120 | 0.5117 | 0.2202 |
| 5d | 0.0076 | 0.0700 | 0.5106 | 0.1399 |
| 21d | 0.0126 | 0.1282 | 0.5091 | 0.1677 |
| 63d | -0.0041 | -0.0341 | 0.5071 | -0.1245 |

#### Pre-2022 (2010-2021, 3021 obs)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0044 | 0.0411 | 0.5129 | 0.2184 |
| 5d | 0.0008 | 0.0073 | 0.5108 | 0.1459 |
| 21d | 0.0069 | 0.0684 | 0.5084 | 0.2009 |
| 63d | -0.0007 | -0.0064 | 0.5111 | 0.0652 |

#### 2022 shock (251 obs)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | -0.0010 | -0.0264 | 0.5301 | 1.2340 |
| 5d | 0.0059 | 0.1585 | 0.5388 | 1.2825 |
| 21d | 0.0037 | 0.0572 | 0.5415 | 1.1099 |
| 63d | -0.0810 | -1.1995 | 0.5027 | 0.1764 |

#### Post-2022 (2023-2024, 250 obs)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | -0.0276 | -0.3306 | 0.4879 | -0.5189 |
| 5d | -0.0470 | -0.6332 | 0.4918 | -0.7174 |
| 21d | -0.0373 | -0.6804 | 0.5000 | -0.4207 |
| 63d | -0.0917 | -0.8412 | 0.4785 | -1.3491 |

**Decision: FAIL overall — IC and ICIR below thresholds across full period.**
Signal worked well during 2022 rates shock (Sharpe ~1.2-1.3) but is actively
wrong in post-2022 consolidation (ICIR -0.33 to -0.68). Regime-dependent.
**Phase 5 fix:** regime filter (Milestone 5.11, was 5.7 pre-reorder).

### FX Carry — Actual FX Pair Returns (Monthly)

| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1m | -0.0218 | -0.0237 | 0.5138 | -1.3872 |
| 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 |
| 3m | 0.0323 | 0.0346 | 0.5187 | -2.2301 |
| 6m | -0.0348 | -0.0369 | 0.5048 | -4.1145 |

**Decision: BORDERLINE.** ICIR below threshold — cross-section too thin
(3 currencies, 4 active pairs).
**Phase 5 fixes:** Milestone 5.5 (G10 expansion, fix `_iter_pairs`), Milestone 5.2 ✅.

### Equity Momentum — 50-stock universe, monthly

#### Daily evaluation (50 stocks — misaligned frequency, shown for reference)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | -0.0240 | -0.1455 | 0.4772 | -1.6366 |
| 5d | 0.0151 | 0.1037 | 0.5149 | 1.2394 |
| 21d | 0.0005 | 0.0029 | 0.4869 | 0.5093 |
| 63d | -0.0155 | -0.0953 | 0.4967 | -1.0496 |
DSR = 0.000

#### Monthly evaluation (50 stocks — correct frequency)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1m | 0.0063 | 0.0146 | 0.4752 | -0.7110 |
| 2m | 0.0222 | 0.0499 | 0.4837 | 0.7819 |
| 3m | 0.0309 | 0.0675 | 0.4924 | 0.3280 |
| 6m | -0.0250 | -0.0586 | 0.4792 | -0.9733 |
DSR = 0.000

**Decision: BORDERLINE — IC positive at 2-3m, consistent with academic momentum.**
ICIR and DSR fail thresholds. 50 stocks insufficient.
**Phase 5 fix:** Milestone 5.10 (expanded universe).
**The 10-stock DSR=1.0 earlier result was an artefact of the tiny universe — discard.**

---

## Historical Decision Record: Paper Trading Go/No-Go (recorded 2026-05-14)

This was originally framed as a "Phase 5 Decision" before the ROADMAP was
restructured. Under ROADMAP v0.2, paper trading is **Phase 6** and this is
a record of the decision to *proceed toward* Phase 6 once Phase 5 (Signal
Hardening) completes.

**Decision:** PROCEED TO PHASE 6 (paper trading) once Phase 5 milestones complete,
with explicit Stage 1 limitations acknowledged.

### Rationale
All three signals show faint but consistent evidence of predictive power:
- FX Carry: hit rate >50% at all horizons, IC positive at 2m
- Equity Momentum: IC positive at 2-3m, consistent with academic momentum factor
- Rates Trend: strong in trends (2022 Sharpe ~1.3), regime filter needed

None pass the strict IC > 0.02 AND ICIR > 0.3 ROADMAP threshold at monthly frequency.
However, the thresholds were designed for daily signals with large cross-sections.
With monthly frequency and thin cross-sections, the thresholds are not calibrated
correctly for Stage 1 data constraints. Signals are not proven — but not disproven.

### Phase 6 Preconditions
1. Paper trade FX Carry + Equity Momentum as primary signals
2. Rates Trend included only with the regime filter from Milestone 5.11
   (signal active only when trailing 63-day vol of TLT returns > 0.8%)
3. Monitor rolling 60-day IC — halt signal if IC < -0.05 for 3 consecutive months
4. Apply all kill switch criteria from ROADMAP.md Phase 6
5. Document all Stage 2 / ROADMAP Phase 7 data upgrades required before Phase 7 capital
6. **End-to-end backtest driver script** (`scripts/backtest_strategy.py`) — added
   as a 5.7 finding: the `catalogue → engine → portfolio` path has unit-test
   coverage but no end-to-end runner. Must exist and pass on real data before
   paper trading.