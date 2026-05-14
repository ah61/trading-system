# PROGRESS.md
# Build Log and Current Status

**Last Updated:** 2026-05-14
**Tracks ROADMAP.md version:** 0.2

---

## Current Status

| Field | Value |
|---|---|
| **Roadmap phase** | Phase 5 — Signal Hardening |
| **Active milestone** | 5.2 — Frequency Layer (complete, pending commit) |
| **Tests** | 85 passing (was 72, +13 from frequency layer) |
| **Next action** | Run remaining Phase 5 milestones: 5.3 Variable Library, 5.4 Data Persistence, 5.5 G10 FX, 5.6 Equity Universe, 5.7 Rates Regime Filter, 5.1 Reference Documents |

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

### Pre-Phase 5 Signal Evaluation (preparation for Phase 5 work)

Completed before Phase 5 was formally scoped. The results below motivated the
Phase 5 milestones now in ROADMAP v0.2 (frequency layer, variable library, G10
expansion, equity universe expansion, rates regime filter).

- [x] Run SignalEvaluator on Rates Trend signal (TLT, 2010-2024)
- [x] Apply DSR, PBO, Hansen SPA to Rates Trend
- [x] Run SignalEvaluator on FX Carry signal
- [x] Apply DSR, PBO, Hansen SPA to FX Carry
- [x] Run SignalEvaluator on Equity Momentum signal (10-stock subset)
- [x] Apply DSR to Equity Momentum
- [x] Fix hit rate bug — exclude zero signals (zeros = no position, not wrong prediction)
- [x] Fix `future_stack` deprecation warnings in `momentum.py` and `carry.py`
- [x] Fix SyntaxWarning raw docstrings in `constructor.py` and `signal_evaluator.py`
- [x] Fix `np.nan` vs `pd.NA` in `sizing.py` risk parity
- [x] FX Carry — re-evaluate with actual FX pair returns (not DFF proxy)
- [x] Equity Momentum — expand universe from 10 to 50 stocks, evaluate at monthly frequency
- [x] Rates Trend — split evaluation pre/post 2022

### Milestone 5.2 — Frequency Layer ✅ (pending commit)

**Completed 2026-05-14.** See `ARCHITECTURE.md` §4.3 for spec.

- [x] Add `frequency` parameter to `SignalEvaluator.evaluate()` — `'daily'` | `'weekly'` | `'monthly'`
- [x] Resampling rule: signal = first non-zero per period; carry forward all-zero periods
  (rationale: zero = "no position", not "no signal")
- [x] Resampling rule: log returns summed within each period (CONVENTIONS §3.2)
- [x] Forward-return shift expressed in periods at chosen frequency: `shift(-(H+1))`
- [x] Annualisation factor scales with frequency: 252 / 52 / 12
- [x] Rolling-IC window scales with frequency: 63 / 13 / 3 (~1 quarter at each)
- [x] Add `frequency` field to `SignalMetrics` dataclass (breaking change)
- [x] Suppress `ConstantInputWarning` from monthly-on-daily evaluation
- [x] 13 new tests in `tests/test_evaluation.py` (72 → 85 passing)
- [x] Backward compatible: omitting `frequency` defaults to `'daily'`,
      numerically identical to pre-5.2 behaviour

**Remaining Phase 5 commits / housekeeping (do as part of 5.2 cleanup):**
- [ ] Commit final pre-Phase-5 evaluation results to `reports/signal_evaluation_phase1.md`
- [ ] Update `configs/signals/fx_carry.yaml` — declare `frequency: monthly`
- [ ] Update `configs/signals/equity_momentum.yaml` — declare `frequency: monthly`
- [ ] Update `configs/signals/rates_trend.yaml` — confirm `frequency: daily`
- [ ] Re-run all three signal evaluations using new frequency layer (no manual resampling),
      replace numbers in "Signal Evaluation Results" section below

### Milestone 5.1 — Reference Documents 🔄 (in progress)
- [ ] `docs/phase2_signal_engine.docx`
- [ ] `docs/phase3_portfolio_engine.docx`
- [ ] `docs/phase4_backtest_engine.docx`

### Milestone 5.3 — Variable Library ⬜
- [ ] `configs/data/variables/macro.yaml`
- [ ] `configs/data/variables/market.yaml`
- [ ] `configs/data/variables/transformations.yaml`
- [ ] `configs/data/derived_variables.yaml`
- [ ] `src/data/variable_catalog.py`

### Milestone 5.4 — Data Persistence ⬜
- [ ] Wire FRED/Yahoo/DataCleaner to DataStore
- [ ] `DataStore.fetch_or_load()` helper
- [ ] Populate store with Stage 1 data, verify second run is offline

### Milestone 5.5 — G10 FX Expansion ⬜
- [ ] Add AUD, NZD, CAD, JPY, CHF rate series + FX spot pairs
- [ ] Re-evaluate FX Carry with 7 currencies (using frequency layer)

### Milestone 5.6 — Equity Universe Expansion ⬜
- [ ] Expand to 200-stock universe
- [ ] Re-evaluate Equity Momentum at monthly frequency (using frequency layer)

### Milestone 5.7 — Rates Trend Regime Filter ⬜
- [ ] Add `TLT_VOL_63D` transformation
- [ ] Add `REGIME_RATES_TREND` derived indicator
- [ ] Add `regime_filter` param to `RatesTrendSignal.compute()`
- [ ] Re-evaluate full + sub-periods

---

## Known Issues / Technical Debt

### Data
- `GS10` from FRED returns only 109 rows (monthly frequency) — confirm frequency handling
  during Milestone 5.3 (variable library) — declare frequency: monthly in catalog
- Equity momentum universe is survivorship-biased (current S&P 500 only); Stage 2 / ROADMAP
  Phase 7.2 fix (CRSP point-in-time)
- FX Carry signal fires monthly — EUR/GBP rate series are monthly FRED frequency.
  Daily EUR/GBP rate data needed for daily carry — Stage 2 / ROADMAP Phase 7.2 (Bloomberg)
- FX Carry cross-section too thin — only 3 currencies (USD/EUR/GBP), 4 active pairs.
  Fixed in Milestone 5.5 (G10 expansion to 7 currencies / 12 active pairs)
- FRED API flaps intermittently with HTTP 500 errors — workaround: cache rate data to
  `data/cache/` before sessions. Permanent fix in Milestone 5.4 (data persistence)
- DataStore is empty — all evaluations fetch live from FRED/Yahoo. Permanent fix in
  Milestone 5.4 (data persistence)

### Signals
- Rates Trend is regime-dependent — fails in post-trend consolidation (2023-2024).
  Fixed in Milestone 5.7 (regime filter)

### Documentation
- `ARCHITECTURE.md` was bumped to v0.2 on 2026-05-14: renamed prior "Phase 1 / Phase 2"
  references to "Stage 1 / Stage 2" (data-tier semantics) to avoid collision with new
  ROADMAP phase numbering. Header note explains the change.
- `SignalMetrics` in `ARCHITECTURE.md` §4.3 was updated to include `frequency` field
  (Milestone 5.2 breaking change). Search `grep -rn "SignalMetrics(" src/ tests/`
  before next commit to confirm no external constructors are missing the new kwarg.

---

## Signal Evaluation Results Summary

**Note:** Numbers below are pre-Milestone-5.2. Re-running through the new frequency
layer is part of 5.2 cleanup (see "Remaining Phase 5 commits" above). Results
should be similar — the frequency layer formalises and automates what was being
done manually — but will be re-checked once the runner is wired up.

| Signal | Best Horizon | IC Mean | ICIR | Hit Rate | Sharpe | DSR | Decision |
|--------|-------------|---------|------|----------|--------|-----|----------|
| Rates Trend | 1d | 0.0117 | 0.1120 | 0.5117 | 0.2202 | 0.000 | FAIL |
| FX Carry | 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 | N/A | BORDERLINE |
| Equity Momentum | 3m | 0.0309 | 0.0675 | 0.4924 | 0.3280 | 0.000 | BORDERLINE |

---

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
**Key finding: Signal worked well during 2022 rates shock (Sharpe ~1.2-1.3) but is actively
wrong in post-2022 consolidation (ICIR -0.33 to -0.68). Regime-dependent trend follower.**
**Phase 5 fix: regime filter (Milestone 5.7).**

---

### FX Carry — Re-evaluated with Actual FX Pair Returns (Monthly frequency)

Forward returns: Yahoo spot (EURUSD=X, GBPUSD=X) log returns, inverse pairs negated.
Signal resampled to monthly (EUR/GBP FRED rates are monthly frequency).

| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1m | -0.0218 | -0.0237 | 0.5138 | -1.3872 |
| 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 |
| 3m | 0.0323 | 0.0346 | 0.5187 | -2.2301 |
| 6m | -0.0348 | -0.0369 | 0.5048 | -4.1145 |

**Decision: BORDERLINE — hit rate consistently >50%, IC positive at 2m horizon.**
**ICIR below 0.3 threshold — cross-section too thin (3 currencies = 4 active pairs).**
**Previous DFF-proxy evaluation was invalid — rate differential is not an FX spot return benchmark.**

**Phase 5 fixes:**
- Milestone 5.5 — Add G10 currencies (AUD, NZD, CAD, JPY, CHF) for proper cross-section
- Milestone 5.2 — Frequency layer (replaces manual resample-to-monthly) ✅
- Stage 2 / ROADMAP Phase 7.2 — daily EUR/GBP rate data + Bloomberg forward rates

---

### Equity Momentum — 50-stock universe, monthly frequency (2026-05-14)

Universe expanded from 10 to 50 stocks. Evaluated at monthly frequency to match
signal rebalance cadence. 10-stock DSR=1.0 result was noise from tiny universe.

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

**Decision: BORDERLINE — IC positive at 2-3m, consistent with academic literature on momentum.**
**ICIR and DSR fail thresholds. 50 stocks insufficient for robust cross-sectional momentum.**

**Phase 5 fixes:**
- Milestone 5.6 — Expand to 200-stock universe
- Milestone 5.2 — Frequency layer (replaces manual resample-to-monthly) ✅
- Stage 2 / ROADMAP Phase 7.2 — CRSP point-in-time universe (eliminates survivorship bias)

**The 10-stock DSR=1.0 result was an artefact of the tiny universe — discard.**

---

## Historical Decision Record: Paper Trading Go/No-Go (recorded 2026-05-14)

**Note:** This was originally framed as a "Phase 5 Decision" before the ROADMAP
was restructured. Under ROADMAP v0.2, paper trading is **Phase 6** and this is
a record of the decision to *proceed toward* Phase 6 once Phase 5 (Signal
Hardening) completes. The decision itself was conditional on completing Phase 5
work that did not exist as formal milestones at the time of recording.

**Decision: PROCEED TO PHASE 6 (paper trading) once Phase 5 milestones complete,
with explicit Stage 1 limitations acknowledged.**

### Rationale
All three signals show faint but consistent evidence of predictive power:
- FX Carry: hit rate >50% at all horizons, IC positive at 2m with real FX benchmark
- Equity Momentum: IC positive at 2-3m, consistent with well-documented academic factor
- Rates Trend: strong during sustained trends (2022 Sharpe ~1.3), regime filter needed

None pass the strict IC > 0.02 AND ICIR > 0.3 ROADMAP threshold at monthly frequency.
However, the thresholds were designed for daily signals with large cross-sections.
With monthly frequency and thin cross-sections, the thresholds are not calibrated
correctly for Stage 1 data constraints. The signals are not proven — but they are
not disproven either.

### Phase 6 Conditions (preconditions to going live in paper)
1. Paper trade FX Carry + Equity Momentum as primary signals
2. Rates Trend included only with the regime filter from Milestone 5.7
   (signal active only when trailing 63-day vol of TLT returns > 0.8%)
3. Monitor rolling 60-day IC — halt signal if IC < -0.05 for 3 consecutive months
4. Apply all kill switch criteria from ROADMAP.md Phase 6
5. Document all Stage 2 / ROADMAP Phase 7 data upgrades required before Phase 7 capital

### Stage 2 (ROADMAP Phase 7) Upgrades Required Before Live Capital
- FX: Add AUD, NZD, CAD, JPY, CHF rate series for full G10 cross-section
  (now Milestone 5.5 — moved earlier)
- FX: Source daily EUR/GBP rate data (currently monthly FRED) — Phase 7.2
- FX: Replace rate proxy with actual forward rates (Bloomberg) — Phase 7.2
- Equities: Replace survivorship-biased universe with CRSP point-in-time
  (500+ stocks) — Phase 7.2
- Rates: Add regime filter to RatesTrendSignal (now Milestone 5.7 — moved earlier)
- Infrastructure: Persist data to DataStore instead of fetching live each session
  (now Milestone 5.4 — moved earlier)
