# ROADMAP.md
# Build Phases and Completion Criteria

**Version:** 0.6
**Last Updated:** 2026-05-16
**Rule:** Do not begin a phase until all completion criteria for the previous phase are met.
Completion means: tests pass, data contracts are verified, and the module has been
reviewed in Claude.ai against ARCHITECTURE.md.

**Companion documents:**
- `ARCHITECTURE.md` — system design (source of truth for structure)
- `DESIGN_DECISIONS.md` — rationale for major design choices (why, not what)
- `PROGRESS.md` — current execution state and known issues

---

## Status Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Environment Setup | ✅ Complete |
| 1 | Data Pipeline | ✅ Complete |
| 2 | Signal Engine | ✅ Complete |
| 3 | Portfolio Engine | ✅ Complete |
| 4 | Backtest Engine | ✅ Complete |
| 5 | Signal Hardening | 🔄 In Progress |
| 6 | Paper Trading (IB) | ⬜ Not Started |
| 7 | Live Capital + Signal Expansion | ⬜ Not Started |

---

## Phase 0 — Environment Setup ✅
**Goal:** Working development environment, repo structure, and basic tooling.

### Completion Criteria — MET
- [x] GitHub repo created: `trading-system`
- [x] Python 3.11 virtual environment
- [x] All dependencies installed
- [x] `.env` and `.env.example` created
- [x] `.gitignore` covers all sensitive paths
- [x] `black` and `ruff` configured
- [x] Full directory structure per `CONVENTIONS.md`
- [x] `src/exceptions.py` with all custom exception classes
- [x] `python -m pytest tests/` runs with no import errors

---

## Phase 1 — Data Pipeline ✅
**Goal:** Reliable, clean, point-in-time data for FX and Rates. Stored in DuckDB.

### Completion Criteria — MET
- [x] DataStore (DuckDB) — write/read round-trip, lineage, versioning
- [x] FREDSource + Alfred vintage fetch working
- [x] YahooSource with auto-adjust, MultiIndex fix
- [x] DataCleaner — outlier detection, forward fill, DataGapError
- [x] All tests pass — 17 tests

### Known Issues (carry forward to Phase 5)
- DataStore is not being used in practice — data fetched live each session
- FRED API flaps intermittently — cache data to `data/cache/` as workaround

---

## Phase 2 — Signal Engine ✅
**Goal:** Three working signals with full evaluation and overfitting corrections.

### Completion Criteria — MET
- [x] Signal base class with compute(), normalise(), evaluate(), get_metadata()
- [x] FXCarrySignal — cross-sectional rate differential ranking
- [x] RatesTrendSignal — SMA crossover on TLT
- [x] EquityMomentumSignal — 12-1 month cross-sectional momentum
- [x] SignalEvaluator — IC, ICIR, hit rate, Sharpe, decay at 4 horizons
- [x] Corrections — DSR, PBO, Hansen SPA
- [x] All tests pass — 32 tests

### Signal Evaluation Results (Phase 1 data, public sources)
| Signal | Best Horizon | IC | ICIR | Hit Rate | DSR | Decision |
|--------|-------------|-----|------|----------|-----|----------|
| Rates Trend | 1d | 0.0117 | 0.1120 | 0.5117 | 0.000 | FAIL |
| FX Carry | 2m | 0.1239 | 0.1345 | 0.5463 | N/A | BORDERLINE |
| Equity Momentum | 3m | 0.0309 | 0.0675 | 0.4924 | 0.000 | BORDERLINE |

See `PROGRESS.md` for full evaluation results and per-period breakdowns.

---

## Phase 3 — Portfolio Engine ✅
**Goal:** Vol-targeted, cost-adjusted portfolio from signal outputs.

### Completion Criteria — MET
- [x] CostModel — IB commission schedule, spread assumptions
- [x] PositionSizer — vol target (10% default), risk parity across asset classes
- [x] PortfolioConstructor — signal → weights → trades pipeline
- [x] All tests pass — 14 tests

---

## Phase 4 — Backtest Engine ✅
**Goal:** Walk-forward validated, overfitting-corrected backtest of full portfolio.

### Completion Criteria — MET
- [x] BacktestEngine — strict no-lookahead, full trade log
- [x] WalkForwardEngine — anchored and rolling modes
- [x] CPCVEngine — combinatorial purged cross-validation
- [x] TearsheetGenerator — pyfolio-reloaded integration
- [x] End-to-end validation: Rates Trend on TLT (2010-2024)
- [x] All tests pass — 72 tests total (all phases)

---

## Phase 5 — Signal Hardening 🔄
**Goal:** Strengthen signals, build variable library, fix data infrastructure, expand universes.
**Target duration:** 4-6 weeks
**Depends on:** Phase 4 ✅

This phase was not in the original roadmap but is required before paper trading.
Evaluation in Phase 4 revealed that all three signals are BORDERLINE or FAIL on public
data with thin cross-sections. Phase 5 addresses the root causes systematically.

---

### Milestone 5.1 — Reference Documents
**Goal:** Written documentation of Phases 2-4 for reference and interview use.
**Status:** 🔄 In Progress

- [ ] `docs/phase2_signal_engine.docx` — signal logic, IC/ICIR formulas, DSR/PBO/SPA
- [ ] `docs/phase3_portfolio_engine.docx` — vol targeting, risk parity, cost model, Kelly
- [ ] `docs/phase4_backtest_engine.docx` — walk-forward, CPCV, result metrics

---

### Milestone 5.2 — Frequency Layer
**Goal:** Evaluation pipeline handles daily/weekly/monthly automatically for any signal.

**Problem:** All signals are currently evaluated at daily frequency even when they
rebalance monthly. This produces misleading results (repeated identical values,
constant-input warnings, misaligned IC calculations).

**Tasks:**
- [ ] Add `frequency` parameter to `SignalEvaluator.evaluate()`
  - `'daily'` — current behaviour
  - `'weekly'` — resample signal and returns to weekly before evaluation
  - `'monthly'` — resample to monthly, use month-start alignment
- [ ] Resampling logic: for signal, take first non-zero value per period;
  for returns, compound daily returns over the period
- [ ] Update all three signal configs to declare their natural frequency
- [ ] Re-run all signal evaluations at correct frequency
- [ ] Add tests for frequency resampling in `tests/test_evaluation.py`

**Completion criteria:**
- [ ] `evaluator.evaluate(sig, returns, horizon=3, frequency='monthly')` works without
  manual resampling
- [ ] No ConstantInputWarning for monthly signals evaluated at monthly frequency
- [ ] All three signals re-evaluated at their natural frequency; results in PROGRESS.md

---

### Milestone 5.3 — Variable Library
**Goal:** Single source of truth for all variables — raw, transformed, and derived.

**Architecture:**
```
configs/data/variables/
├── macro.yaml          — FRED series (rates, inflation, employment)
├── market.yaml         — prices, FX, ETFs from Yahoo/IB
├── sentiment.yaml      — COT positioning, VIX, put/call ratio
├── alternative.yaml    — earnings, credit spreads, non-standard sources
└── transformations.yaml — all computed/transformed variables (any domain)

configs/data/derived_variables.yaml — signals, regime indicators, portfolio outputs
```

**Each variable entry contains:**
```yaml
# Raw variable example
DFF:
  domain: macro
  layer: raw
  source: FRED
  series_id: DFF
  description: Federal Funds Effective Rate
  frequency: daily
  unit: percent
  use_alfred_vintage: false
  used_by: [fx_carry, regime_filter]

# Transformed variable example
DFF_ZSCORE_252:
  domain: macro
  layer: transformed
  source_variable: DFF
  transformation: rolling_zscore
  window: 252
  description: DFF rolling 252-day z-score
  frequency: daily

# Derived variable example (in derived_variables.yaml)
fx_carry_signal:
  layer: derived
  type: signal
  inputs: [DFF_ZSCORE_252, EUR_RATE_ZSCORE, GBP_RATE_ZSCORE]
  script: src/signals/fx/carry.py
  frequency: monthly
  output_range: [-1, 1]
```

**Tasks:**
- [ ] Create `configs/data/variables/macro.yaml` — all FRED series used or planned
- [ ] Create `configs/data/variables/market.yaml` — all Yahoo/IB tickers
- [ ] Create `configs/data/variables/transformations.yaml` — all derived transformations
- [ ] Create `configs/data/derived_variables.yaml` — all signals and indicators
- [ ] Write `src/data/variable_catalog.py` — loads and validates catalog, resolves lineage
- [ ] Add `DataStore.get_lineage()` implementation using catalog
- [ ] Add tests for catalog loading and lineage resolution

**Completion criteria:**
- [ ] Every variable used in Phases 1-4 has a catalog entry
- [ ] `variable_catalog.get_lineage('fx_carry_signal')` returns full chain to raw sources
- [ ] Catalog validates on load — no undefined source_variable references

---

### Milestone 5.4 — Data Persistence
**Goal:** DataStore is actually used — fetch once, read many times.

**Problem:** DataStore exists but is empty. Every session re-fetches from FRED/Yahoo.
FRED flaps intermittently. This is fragile and slow.

**Tasks:**
- [ ] Wire `FREDSource.fetch()` to write to `raw.duckdb` on first fetch
- [ ] Wire `YahooSource.fetch()` to write to `raw.duckdb` on first fetch
- [ ] Wire `DataCleaner.clean()` to write output to `adjusted.duckdb`
- [ ] Add `DataStore.fetch_or_load(source, ticker, frequency)` — reads from store if
  available, fetches and stores if not
- [ ] Add cache invalidation: `force_refresh=True` parameter to re-fetch
- [ ] Populate store with all Phase 1 data (FRED rate series, Yahoo FX + ETFs + equities)
- [ ] Add tests verifying data round-trips through store correctly

**Completion criteria:**
- [ ] `store.list_available()` shows all Phase 1 series
- [ ] Running evaluation scripts twice: second run uses store, no network calls
- [ ] `get_lineage()` traces raw → adjusted for each stored series

---

### Milestone 5.5 — G10 FX Expansion ✅
**Goal:** Full G10 cross-section for FX Carry signal.

**Completed 2026-05-14.**

7-currency USD-anchored cross-section (6 active pairs after USD-anchoring,
replacing pre-5.5 4-pair bilateral scheme that double-counted positions).
Pair labels mechanically `<non-USD>/USD` — see DESIGN_DECISIONS.md DD-005 for
market-convention deferral.

**Results:** IC near zero across horizons, ICIR effectively zero. Signal-
quality finding; not a methodology bug. Infrastructure milestone; methodology
fixes in subsequent milestones.

---

### Milestone 5.6 — Output Container + Reporting Hygiene ✅
**Goal:** Structured output storage with reproducibility manifests; no more
overwriting reports.

**Completed 2026-05-15.**

`OutputManager` routes runs into `reports/{exploratory,variables,strategies}/`
with timestamped folders, `manifest.json` (git commit, dirty flag, config
snapshot), and per-kind `index.csv`. Five reusable plot functions in
`src/reporting/plots.py`. `scripts/evaluate_signals.py` refactored to use the
manager. 28 new tests (119 → 147 passing). See PROGRESS.md §5.6 and
CONVENTIONS.md §8 for details.

---

### Milestone 5.7 — Variable Catalogue Wired Into Pipeline ✅
**Goal:** Make the `VariableCatalog` (5.3 registry) actually serve data, not
just declare it. Cache-first lookup with transformation support.

**Completed 2026-05-15** (catalogue + signal interface + engine boundary, in
two checkpoints). **Deferred items closed out 2026-05-16.** See PROGRESS.md
§5.7 for full detail.

Catalogue promoted from stateless registry to stateful runtime object holding
DataStore + sources. `catalogue.get(name, frequency, start, end) -> pd.Series`
returns variables with cache-first lookup. Template-based universe expansion
(`configs/data/universes/*.yaml`) per DD-008. Signal interface changed to
`Dict[str, pd.Series]` keyed by catalogue variable name (DD-007). Backtest
engine accepts the Series contract on its public API and translates to the
portfolio-layer panel at one explicit boundary (`_assemble_price_panel`) —
"option A hybrid" per DD-009.

**Deferred items closed out 2026-05-16:**
- [x] `tests/test_variable_catalog.py` additions for the stateful API: 11 new
      tests covering `get()` returning Series, native vs resampled frequency,
      registry-only error, transformed-variable deferral (5.8 pin), universe
      expansion end-to-end, and `force_refresh` cache bypass (14 → 25 tests
      in this file).
- [x] `force_refresh: bool = False` plumbed through `VariableCatalog.get()`
      and threaded into the `--refresh` CLI flag in
      `scripts/evaluate_signals.py` (no longer advisory).

Test count: 147 → 151 (signal refactor) → 162 (deferred close-out) →
**165** (resample anchoring fix and regression tests).

**Follow-up before `backtest_strategy.py` — shipped 2026-05-16 (commit `6674b24`):**
- [x] Fix `VariableCatalog._resample` anchoring. Forward-fill now anchors
      on caller-provided `start`/`end`; requests predating source coverage
      raise `CatalogError`. Three regression tests added.

---

### Milestone 5.8 — Transformation Pipeline + Derived Variable Persistence
**Goal:** Declared transformations actually execute, and their outputs live in
`derived.duckdb` for reuse across runs and signals.

**Problem:** `configs/data/variables/transformations.yaml` declares 7
transformations (z-scores, log returns, vol, slopes) but no code computes them.
Derived variables don't persist; they get recomputed each run.

**Tasks:**
- [ ] New module `src/data/transformations.py` with one function per
      transformation type (z-score, log return, rolling vol, rate slope, etc.)
- [ ] Transformation executor: given a transformation spec, look up inputs from
      catalogue, apply, return derived series
- [ ] Wire into catalogue: requesting a derived variable triggers transformation
      execution if not cached
- [ ] Persist derived outputs to `derived.duckdb` with proper invalidation
      (re-compute if transformation spec changed)
- [ ] Tests: each transformation has correctness tests; cache invalidation works

**Completion criteria:**
- [ ] All 7 declared transformations execute and persist
- [ ] Second run of any signal that uses derived vars is fully offline
- [ ] Changing a transformation spec triggers re-computation

---

### Milestone 5.9 — FX Carry Quarterly Horizon Experiment
**Goal:** Quick read on whether the near-zero monthly IC is partly a horizon
mismatch (see DESIGN_DECISIONS.md OQ-001).

**Tasks:**
- [ ] Re-run FX Carry evaluation at quarterly horizon using frequency layer
- [ ] Document IC/ICIR at 1m, 2m, 3m, 6m, 12m horizons side-by-side
- [ ] Output via `OutputManager` (5.6) to `reports/exploratory/`

**Completion criteria:**
- [ ] Results documented as a one-off exploratory output
- [ ] Decision logged in PROGRESS.md: which horizon to use going forward

---

### Milestone 5.10 — Universe Expansion (FX EM, Equities, Rates)
**Goal:** Expand cross-section meaningfully now that catalogue/transformation
infrastructure is in place. See DESIGN_DECISIONS.md DD-002.

**Targets:**
- **FX**: G10 + 5 EM (MXN, BRL, ZAR, INR, TRY) = 12 USD-anchored pairs
- **Equities**: ~55 stocks, 5 per GICS sector × 11 sectors (sector-balanced)
- **Rates**: + TIP (TIPS), LQD (IG credit) — total ~6 ETFs

**Tasks:**
- [ ] Add 5 EM FX rate series + spot pairs (note: Yahoo data quality drops
      outside G10 — document caveat)
- [ ] Curate 55-stock equity universe sector-balanced; commit to
      `configs/data/universes/sp500_sector_balanced.yaml`
- [ ] Add TIP and LQD to rate ETF universe
- [ ] Re-evaluate all three signals on expanded universes
- [ ] Document results vs pre-expansion baseline

**Completion criteria:**
- [ ] All three signals re-evaluated, results in `reports/variables/`
- [ ] Comparison tables: pre vs post universe expansion

---

### Milestone 5.11 — Rates Trend Regime Filter
**Goal:** Make Rates Trend usable by conditioning on trending regime. First
piece of conditioning layer (ARCHITECTURE.md Layer 2a).

**Problem:** Rates Trend fails in post-trend consolidation (2023-2024 ICIR =
-0.68). Works well in trending regimes (2022 Sharpe = 1.28). Needs a regime
gate.

**New derived variables needed:**
```yaml
TLT_VOL_63D:
  layer: transformed
  source_variable: TLT_CLOSE
  transformation: rolling_vol
  window: 63
  annualised: true

REGIME_RATES_TREND:
  layer: derived
  type: regime_indicator
  inputs: [TLT_VOL_63D]
  rule: "1 if TLT_VOL_63D > 0.008 else 0"
  description: "1 = trending rates regime, 0 = choppy"
```

**Tasks:**
- [ ] Add `TLT_VOL_63D` to `configs/data/variables/transformations.yaml`
- [ ] Add `REGIME_RATES_TREND` to `configs/data/derived_variables.yaml`
- [ ] Add `regime_filter` parameter to `RatesTrendSignal.compute()`
- [ ] Evaluate Rates Trend with regime filter across full period and sub-periods
- [ ] Add test: signal = 0 when regime indicator = 0

**Completion criteria:**
- [ ] Rates Trend with regime filter evaluated pre/post 2022
- [ ] Full-period ICIR improves vs unfiltered version
- [ ] Conditioning-layer pattern documented for future regime models

---

### Milestone 5.12 — IBSource (FX Focus)
**Goal:** Wire Interactive Brokers as a third data source, scoped narrowly to
FX (real-time and historical). See DESIGN_DECISIONS.md DD-001.

**Prerequisites:** IB paper account exists, Gateway installed and tested,
API port 7497, IDEALPRO FX subscription active. ✅ All confirmed 2026-05-14.

**Tasks:**
- [ ] Implement `src/data/sources/ib.py` using `ib_insync`
- [ ] Async fetch wrapped in sync interface to match `DataSource` contract
- [ ] Connection management: auto-reconnect on drop, retry on timeout
- [ ] Request pacing: respect ~50 req/sec API limit
- [ ] Scope explicitly: FX spot and FX forward points only in this milestone
- [ ] Add to catalogue as preferred source for FX where available
- [ ] Tests with mock IB connection (don't require Gateway running for CI)
- [ ] Integration test with real paper account (manual, documented separately)

**Completion criteria:**
- [ ] FX data fetched from IB matches Yahoo within reasonable tolerance
- [ ] Catalogue can fall back from IB to Yahoo if Gateway is unavailable
- [ ] Documentation: how to start Gateway, how to run the integration test

---

### Milestone 5.13 — Forward-Spot Basis Carry Signal
**Goal:** True CIP-implied carry signal using actual FX forward points from IB.
See DESIGN_DECISIONS.md OQ-001 item 1.

**Problem:** Current FX Carry approximates carry via interest rate differentials.
Real carry is `(forward_rate - spot_rate) / spot_rate`. Post-2008 CIP deviations
mean these differ meaningfully and the deviation has been a documented return
source (Du, Tepper, Verdelhan 2018).

**Tasks:**
- [ ] New signal `src/signals/fx/basis_carry.py`
- [ ] Fetch 1M and 3M forward points for all 12 FX pairs via IB
- [ ] Compute basis carry per pair, rank cross-sectionally
- [ ] Side-by-side evaluation with rate-differential carry
- [ ] Document the CIP deviation as its own data series

**Completion criteria:**
- [ ] Basis carry signal evaluated; IC/ICIR compared to rate-differential carry
- [ ] CIP deviation series available for future analysis

---

### Milestone 5.14 — Vol Conditioning Experiment on FX Carry
**Goal:** Test the conditioning layer infrastructure on FX Carry. Documented
effect: carry works in low-vol regimes, fails in high-vol crises.

**Tasks:**
- [ ] Vol regime indicator from VIX (high/normal/low thresholds)
- [ ] Apply as position-size multiplier on FX Carry signal
- [ ] Evaluate conditional vs unconditional carry
- [ ] Document: does conditioning help, hurt, or wash out?

**Note:** Our backtest period (2010-2024) doesn't include 2008 carry unwind.
Expected effect may be muted vs literature.

**Completion criteria:**
- [ ] Conditional FX Carry evaluated
- [ ] Results documented vs unconditional baseline
- [ ] Decision logged: keep, drop, or revisit with longer backtest

---

### Milestone 5.16 — Portfolio Layer Series Unification (PLACEHOLDER, LOW PRIORITY)
**Status:** ⬜ Not scheduled. Placeholder only.

**Context:** 5.7 landed the signal layer on `Dict[str, pd.Series]` per the
catalogue contract, but deliberately kept the portfolio layer
(`PositionSizer`, `PortfolioConstructor`, `CostModel`) on the wide
DataFrame panel. The engine translates between the two shapes at one
explicit boundary (`BacktestEngine._assemble_price_panel`). See
DESIGN_DECISIONS.md DD-009 for the rationale.

**If ever needed:** rewrite the portfolio layer to consume `Dict[str, pd.Series]`
end-to-end. Cost: rewriting `PositionSizer.volatility_target`,
`PositionSizer.risk_parity`, `PortfolioConstructor.construct`, and the
`CostModel.apply_costs` call paths. Benefit: contract uniformity end-to-end.

**Do not do speculatively.** Wait for a concrete need — e.g., a signal that
produces per-variable forward returns and the engine needs to consume them
without coercing through a panel.

---

### Phase 5 Completion Criteria
- [ ] All existing tests still pass (current: 151)
- [x] Frequency layer working — no manual resampling required in evaluation scripts (5.2)
- [x] Variable catalogue stateful and serving data via cache-first lookup (5.7)
- [ ] Transformation pipeline executes declared transformations and persists derived
      variables (5.8)
- [x] Output container in place with reproducibility manifests (5.6)
- [x] FX Carry re-evaluated on full G10 (5.5)
- [ ] Universe expansion complete (5.10)
- [ ] Rates Trend regime filter implemented and evaluated (5.11)
- [ ] IBSource integrated for FX (5.12)
- [ ] At least one methodology investigation completed (5.9 + 5.13 or 5.14)
- [ ] PROGRESS.md updated with all new results
- [ ] `ARCHITECTURE.md` reflects modeling layer split (2a/2b/2c)
- [ ] `DESIGN_DECISIONS.md` current

---

## Phase 6 — Paper Trading (IB) ⬜
**Goal:** Live market validation via Interactive Brokers paper account.
**Target duration:** 3+ months minimum
**Depends on:** Phase 5 complete, at least one signal ICIR > 0.3

**Prerequisites:**
- IB paper account ✅ (account `phbojg566` / `DUP730772`, 2026-05-14)
- IB Gateway installed and API tested ✅ (port 7497, 2026-05-14)
- IDEALPRO FX subscription active ✅ (2026-05-14)
- IBSource module built in Milestone 5.12 (FX historical/real-time)
- Market-convention FX label translation layer (per DESIGN_DECISIONS.md DD-005)
- **End-to-end backtest driver** ✅ `scripts/backtest_strategy.py` shipped
  2026-05-16 (commit `3095fc0`). Mirrors `scripts/evaluate_signals.py`
  structure; calls `BacktestEngine.run`. Real-data verification on Rates
  Trend / TLT 2010-2024 produced Sharpe -0.55 matching the historical
  Phase 4 validation number — the 5.7 wiring change preserved numerics.
  See PROGRESS.md "Phase 6 prerequisite — `scripts/backtest_strategy.py`"
  for full detail and surfaced limitations.

### Milestone 6.1 — IBSource Live Feed Extension
**Builds on:** Milestone 5.12 `IBSource` (which covers FX historical).
- [ ] Extend `IBSource` to subscribe to real-time bar updates
- [ ] Signal computed on live data verified to match backtest signal on same date
- [ ] Handle market hours, holidays, and connection drops gracefully

### Milestone 6.2 — Order Management
- [ ] Target weights → order sizes computed via CostModel
- [ ] Orders submitted to IB paper account via `ib_insync`
- [ ] Fill prices recorded to `derived.duckdb` with timestamp
- [ ] Slippage: fill price vs mid-price at signal time computed and logged
- [ ] Compare realised slippage to backtest cost assumptions

### Milestone 6.3 — Live Monitoring Dashboard
- [ ] Daily P&L tracked vs backtest expectation
- [ ] Rolling 60-day IC per signal computed and plotted
- [ ] Drawdown monitor vs backtest max drawdown
- [ ] Signal drift alert: IC < -0.05 for 3 consecutive months → email/log alert
- [ ] Use `OutputManager` from 5.6 for daily report storage

### Kill Switch Criteria (hard-coded, not configurable)
Strategy halted automatically if any of:
- Portfolio drawdown > 2× backtest max drawdown
- Rolling 60-day IC < 0 for primary signal for 3 consecutive months
- Monthly net P&L worse than -3σ of backtest monthly distribution
- IB connection lost for > 1 trading day without manual override

### Phase 6 Completion Criteria
- [ ] 3 months paper trading completed without kill switch triggered
- [ ] Annualised paper Sharpe within 1 std of backtest OOS Sharpe
- [ ] Realised slippage ≤ 150% of backtest cost assumptions
- [ ] All three signals monitored with rolling IC
- [ ] Ready for Phase 7 live capital decision

---

## Phase 7 — Live Capital + Signal Expansion ⬜
**Goal:** Deploy real capital and expand signal library incrementally.
**Target duration:** Ongoing
**Depends on:** Phase 6 passing all completion criteria

### 7.1 Live Capital Deployment
- Start with small allocation (define size before going live)
- Scale up only after 6 months live track record
- Keep paper trading running in parallel for comparison

### 7.2 Data Upgrades
- Replace FRED monthly rate proxy with actual FX forward rates (Bloomberg or Quandl)
- Replace ETF proxies with Treasury futures (CME via Quandl)
- Obtain point-in-time equity universe (CRSP or Sharadar) — eliminates survivorship bias
- Source daily G10 rate data (currently monthly FRED frequency)

### 7.3 Additional Signals (add one at a time, full Phase 2-4 evaluation each)
- FX PPP deviation (value/mean-reversion)
- FX positioning — CFTC COT net speculative positioning
- FX macro surprise — CPI, NFP vs consensus (requires Alfred vintage)
- Rates macro surprise — Fed surprise index
- Equity value — E/P, B/P cross-sectional
- Equity quality — ROE, accruals, leverage cross-sectional

### 7.4 Portfolio Expansion
- Add EM FX carry (Phase 2 currencies: BRL, MXN, ZAR, TRY, INR)
- Add credit signals (HYG/LQD spread strategies)
- Formal portfolio optimisation — Black-Litterman or hierarchical risk parity

---

## Variable Library — Planned Contents

### Raw Variables (configs/data/variables/)

**macro.yaml — FRED series**
| Variable | Series ID | Frequency | Alfred Vintage |
|----------|-----------|-----------|----------------|
| DFF | DFF | Daily | No |
| GS10 | GS10 | Monthly | No |
| GS2 | GS2 | Monthly | No |
| T10YIE | T10YIE | Daily | No |
| CPIAUCSL | CPIAUCSL | Monthly | Yes |
| PAYEMS | PAYEMS | Monthly | Yes |
| USD_RATE | DFF | Daily | No |
| EUR_RATE | IR3TIB01EZM156N | Monthly | No |
| GBP_RATE | IR3TIB01GBM156N | Monthly | No |
| AUD_RATE | IR3TIB01AUM156N | Monthly | No |
| NZD_RATE | IR3TIB01NZM156N | Monthly | No |
| CAD_RATE | IR3TIB01CAM156N | Monthly | No |
| JPY_RATE | IR3TIB01JPM156N | Monthly | No |
| CHF_RATE | IR3TIB01CHM156N | Monthly | No |

**market.yaml — Yahoo/IB**
| Variable | Ticker | Type |
|----------|--------|------|
| TLT_CLOSE | TLT | Rate ETF |
| IEF_CLOSE | IEF | Rate ETF |
| SHY_CLOSE | SHY | Rate ETF |
| EURUSD | EURUSD=X | FX spot |
| GBPUSD | GBPUSD=X | FX spot |
| AUDUSD | AUDUSD=X | FX spot |
| NZDUSD | NZDUSD=X | FX spot |
| USDCAD | USDCAD=X | FX spot |
| USDJPY | USDJPY=X | FX spot |
| USDCHF | USDCHF=X | FX spot |
| SP500_* | (200 tickers) | Equity |

**transformations.yaml — computed variables**
| Variable | Source | Transformation | Window |
|----------|--------|----------------|--------|
| DFF_ZSCORE | DFF | rolling_zscore | 252 |
| GS10_GS2_SLOPE | GS10, GS2 | difference | — |
| CPI_YOY | CPIAUCSL | yoy_pct_change | — |
| TLT_VOL_63D | TLT_CLOSE | rolling_vol | 63 |
| TLT_LOG_RET | TLT_CLOSE | log_return | 1 |
| EURUSD_LOG_RET | EURUSD | log_return | 1 |

---

## Interview Readiness Checklist

This project is also an interview asset. At each phase, maintain:

- [ ] Clean GitHub repo (public or shareable) with `README.md` explaining the system
- [ ] Reference documents for each phase (Phases 2-4 docs: Milestone 5.1)
- [ ] Written summary of each strategy: rationale, signal construction, results, limitations
- [ ] Known-failures document: what didn't survive OOS, and why
- [ ] Ability to explain DSR, PBO, Hansen SPA in plain English with your numbers
- [ ] Portfolio-level attribution: return from each asset class and signal
- [ ] PROGRESS.md kept current — the honest build log

The most valuable thing to say in an interview is not "my strategy made X%."
It is: "here is my process, here is what survived it, and here is what I learned from what didn't."