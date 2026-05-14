# ROADMAP.md
# Build Phases and Completion Criteria

**Version:** 0.2
**Last Updated:** 2026-05-14
**Rule:** Do not begin a phase until all completion criteria for the previous phase are met.
Completion means: tests pass, data contracts are verified, and the module has been
reviewed in Claude.ai against ARCHITECTURE.md.

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

### Milestone 5.5 — G10 FX Expansion
**Goal:** Full G10 cross-section for FX Carry signal (7 currencies, 42 pairs).

**Problem:** Current FX Carry has only 3 currencies (USD/EUR/GBP) = 4 active pairs.
Too few for meaningful cross-sectional IC. ICIR cannot pass 0.3 threshold.

**New currencies to add:** AUD, NZD, CAD, JPY, CHF (+ existing USD, EUR, GBP = 7 total)

**FRED rate series to source:**
```yaml
AUD: IR3TIB01AUM156N   # Australia 3M interbank
NZD: IR3TIB01NZM156N   # New Zealand 3M interbank
CAD: IR3TIB01CAM156N   # Canada 3M interbank
JPY: IR3TIB01JPM156N   # Japan 3M interbank
CHF: IR3TIB01CHM156N   # Switzerland 3M interbank
```

**Yahoo FX tickers to add:**
```
AUDUSD=X, NZDUSD=X, CADUSD=X (or USDCAD=X inverted),
USDJPY=X (inverted), USDCHF=X (inverted)
```

**Tasks:**
- [ ] Add 5 new rate series to `configs/data/variables/macro.yaml`
- [ ] Update `configs/signals/fx_carry.yaml` with full G10 rate_series mapping
- [ ] Fetch and cache all new rate series (note: most are monthly FRED frequency)
- [ ] Fetch and cache all new FX spot pairs from Yahoo
- [ ] Re-evaluate FX Carry with 7 currencies at monthly frequency
- [ ] Validate: signal should be long AUD/NZD, short JPY/CHF over 2010-2019

**Completion criteria:**
- [ ] FX Carry evaluated with 7 currencies, 12 active pairs (n_long=3, n_short=3)
- [ ] ICIR at best monthly horizon documented in PROGRESS.md
- [ ] Sanity check passes (AUD/NZD historically long, JPY/CHF historically short)

---

### Milestone 5.6 — Equity Universe Expansion
**Goal:** 200-500 stock universe for Equity Momentum; re-evaluate at monthly frequency.

**Problem:** 50-stock universe too small for robust cross-sectional IC.
Survivorship bias persists (current S&P 500 members only — Phase 7 fix).

**Tasks:**
- [ ] Expand `configs/universes/sp500_current.yaml` to 200 stocks (diversified sectors)
- [ ] Cache all 200 tickers to `data/cache/` and `raw.duckdb`
- [ ] Re-evaluate Equity Momentum at monthly frequency using frequency layer (Milestone 5.2)
- [ ] Document survivorship bias caveat clearly in results

**Completion criteria:**
- [ ] Equity Momentum evaluated with 200 stocks at monthly frequency
- [ ] ICIR at best monthly horizon documented in PROGRESS.md
- [ ] Comparison table: 10-stock vs 50-stock vs 200-stock results

---

### Milestone 5.7 — Rates Trend Regime Filter
**Goal:** Make Rates Trend usable by conditioning on trending regime.

**Problem:** Rates Trend fails in post-trend consolidation (2023-2024 ICIR = -0.68).
Works well in trending regimes (2022 Sharpe = 1.28). Needs a regime gate.

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
  rule: "1 if TLT_VOL_63D > 0.008 else 0"  # 0.8% annualised daily vol threshold
  description: "1 = trending rates regime, 0 = choppy"
```

**Tasks:**
- [ ] Add `TLT_VOL_63D` to `configs/data/variables/transformations.yaml`
- [ ] Add `REGIME_RATES_TREND` to `configs/data/derived_variables.yaml`
- [ ] Add `regime_filter` parameter to `RatesTrendSignal.compute()`
  - When regime=0, signal output = 0 (no position)
  - When regime=1, signal computed normally
- [ ] Evaluate Rates Trend with regime filter across full period and sub-periods
- [ ] Add test: signal = 0 when regime indicator = 0

**Completion criteria:**
- [ ] Rates Trend with regime filter evaluated pre/post 2022
- [ ] Full-period ICIR improves vs unfiltered version
- [ ] Results documented in PROGRESS.md

---

### Phase 5 Completion Criteria
- [ ] All 72 existing tests still pass
- [ ] Frequency layer working — no manual resampling required in evaluation scripts
- [ ] Variable catalog complete — all Phase 1 variables defined with lineage
- [ ] DataStore populated — `store.list_available()` shows all series
- [ ] FX Carry re-evaluated with 7 currencies — ICIR documented
- [ ] Equity Momentum re-evaluated with 200 stocks — ICIR documented
- [ ] Rates Trend regime filter implemented and evaluated
- [ ] At least one signal passes ICIR > 0.3 after Phase 5 improvements
- [ ] PROGRESS.md updated with all new results
- [ ] `ARCHITECTURE.md` updated to reflect variable library architecture

---

## Phase 6 — Paper Trading (IB) ⬜
**Goal:** Live market validation via Interactive Brokers paper account.
**Target duration:** 3+ months minimum
**Depends on:** Phase 5 complete, at least one signal ICIR > 0.3

**Prerequisites:**
- IB account confirmed ✅ (account exists)
- IB TWS or IB Gateway installed on local machine
- Paper trading account funded (simulated)

### Milestone 6.1 — IBSource Live Feed
- [ ] Connect to IB TWS paper account via `ib_insync`
- [ ] `IBSource.fetch_live()` returns real-time prices matching DataStore schema
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
- [ ] Simple daily report saved to `reports/live/YYYY-MM-DD.md`

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
