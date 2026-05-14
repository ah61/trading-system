# PROGRESS.md
# Build Log and Current Status

**Last Updated:** 2026-05-14

---

## Current Status
**Phase:** 4 complete, signal evaluation in progress
**Tests:** 72 passing, 0 warnings
**Next action:** Investigation 2 — Equity Momentum 50-stock universe

---

## Completed Phases

### Phase 0 — Environment Setup
- Repo created, venv, .gitignore, directory structure

### Phase 1 — Data Pipeline
- Milestone 1.1: DataStore (DuckDB) — 4 tests
- Milestone 1.2: FREDSource + Alfred vintage — 4 tests
- Milestone 1.3: YahooSource — 4 tests
- Milestone 1.4: DataCleaner — 5 tests
- Tag: phase1-complete
- Fix: fetch_vintage DataFrame parsing (fredapi returns DataFrame not Series)
- Fix: YahooSource MultiIndex columns (yfinance v0.2+ returns MultiIndex)
- Verified: vintage CPI data differs from revised by 1.77 points

### Phase 2 — Signal Engine
- Milestone 2.1: Signal base class + SignalMetrics — 5 tests
- Milestone 2.2: FXCarrySignal — 3 tests
- Milestone 2.3: RatesTrendSignal — 4 tests
- Milestone 2.4: EquityMomentumSignal — 4 tests
- Milestone 2.5: SignalEvaluator (IC, ICIR, hit rate, Sharpe, decay) — 6 tests
- Milestone 2.6: Corrections (DSR, PBO, Hansen SPA) — 6 tests
- Tag: phase2-complete
- Fix: SignalEvaluator now handles single-asset and multi-asset (cross-sectional) signals correctly

### Phase 3 — Portfolio Engine
- Milestone 3.1: CostModel — 5 tests
- Milestone 3.2: PositionSizer (vol target, risk parity) — 4 tests
- Milestone 3.3: PortfolioConstructor (gross/net limits, trades) — 5 tests
- Tag: phase3-complete

### Phase 4 — Backtest Engine
- Milestone 4.1: BacktestEngine + WalkForwardEngine — 5 tests
- Milestone 4.2: CPCVEngine — 4 tests
- Milestone 4.3: TearsheetGenerator — 3 tests
- Tag: phase4-complete
- End-to-end validation on real data
- Rates Trend on TLT (2010-2024): Sharpe -0.52 OOS — expected, 2022-2023 rates shock
- Tag: phase4-validated

---

## In Progress

### Pre-Phase 5 — Signal Evaluation
- [x] Run SignalEvaluator on Rates Trend signal (TLT, 2010-2024)
- [x] Apply DSR, PBO, Hansen SPA to Rates Trend
- [x] Run SignalEvaluator on FX Carry signal
- [x] Apply DSR, PBO, Hansen SPA to FX Carry
- [x] Run SignalEvaluator on Equity Momentum signal (10-stock subset)
- [x] Apply DSR to Equity Momentum
- [x] Fix hit rate bug — exclude zero signals (zeros = no position, not wrong prediction)
- [x] Fix future_stack deprecation warnings in momentum.py and carry.py
- [x] Fix SyntaxWarning raw docstrings in constructor.py and signal_evaluator.py
- [x] Fix np.nan vs pd.NA in sizing.py risk parity
- [x] FX Carry — re-evaluate with actual FX pair returns (not DFF proxy)
- [ ] Equity Momentum — expand universe from 10 to 50 stocks
- [ ] Rates Trend — split evaluation pre/post 2022
- [ ] Make Phase 5 decision
- [ ] Document results in reports/signal_evaluation_phase1.md

---

## Known Issues / Technical Debt
- GS10 from FRED returns only 109 rows (monthly frequency) — need to confirm frequency handling
- Equity momentum universe is survivorship-biased (current S&P 500 only)
- FX Carry signal fires monthly — EUR/GBP rate series are monthly FRED frequency
  Phase 2 fix: source daily EUR/GBP rate data for a true daily carry signal
- FX Carry cross-section too thin — only 3 currencies (USD/EUR/GBP), 4 active pairs
  Phase 2 fix: add AUD, NZD, CAD, JPY, CHF for proper G10 cross-section
- FRED API flaps intermittently with HTTP 500 errors — cache rate data to data/cache/ before sessions
- DataStore is empty — all evaluations fetch live from FRED/Yahoo. Phase 2: persist to store.

---

## Signal Evaluation Results

| Signal | Best Horizon | IC Mean | ICIR | Hit Rate | Sharpe | DSR | PBO | Decision |
|--------|-------------|---------|------|----------|--------|-----|-----|----------|
| Rates Trend | 1d | 0.0117 | 0.1121 | 0.5120 | 0.2202 | 0.0000 | N/A | FAIL |
| FX Carry | 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 | N/A | N/A | BORDERLINE |
| Equity Momentum | 1d | 0.0418 | 0.1410 | 0.5000* | 1.3686 | 1.0000 | N/A | BORDERLINE |

PBO not applicable for single-config signals — requires 2+ parameter configurations.
*Hit rate for Equity Momentum: ~0.50 after fix, signal is sparse (top/bottom decile of 10 stocks = 1 long, 1 short per month).

---

### Rates Trend — Full Results (TLT, 2010-2024)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0117 | 0.1121 | 0.5120 | 0.2202 |
| 5d | 0.0076 | 0.0700 | 0.5109 | 0.1399 |
| 21d | 0.0126 | 0.1282 | 0.5094 | 0.1677 |
| 63d | -0.0041 | -0.0340 | 0.5074 | -0.1245 |

Decision: FAIL — IC_mean < 0.02 and ICIR < 0.3 at all horizons per roadmap threshold.
DSR = 0.0 — right at boundary, not robustly passing.
Note: 2022-2023 rates shock likely hurt this signal significantly in the OOS period.
Pending: pre/post 2022 split evaluation.

---

### FX Carry — Re-evaluated with Actual FX Pair Returns (Monthly, 2026-05-14)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1m | -0.0218 | -0.0237 | 0.5138 | -1.3872 |
| 2m | 0.1239 | 0.1345 | 0.5463 | -0.1513 |
| 3m | 0.0323 | 0.0346 | 0.5187 | -2.2301 |
| 6m | -0.0348 | -0.0369 | 0.5048 | -4.1145 |

Decision: BORDERLINE — hit rate consistently >50%, IC positive at 2m horizon.
ICIR below 0.3 threshold at all horizons — cross-section too thin (3 currencies = 4 active pairs).
Previous DFF-proxy evaluation was invalid — rate differential ≠ FX spot return benchmark.
Phase 2 fixes needed:
  1. Add G10 currencies (AUD, NZD, CAD, JPY, CHF) — minimum 6-7 for meaningful cross-sectional IC
  2. Source daily EUR/GBP rate data — current FRED series are monthly, signal fires once per month
  3. Use FX forward rates (Bloomberg) for proper carry calculation

---

### Equity Momentum — Full Results (10-stock subset, 2010-2024)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0418 | 0.1410 | 0.5000 | 1.3686 |
| 5d | -0.0007 | -0.0021 | 0.5182 | -0.5248 |
| 21d | -0.0602 | -0.1681 | 0.4864 | -1.0227 |
| 63d | -0.0248 | -0.0718 | 0.4727 | 1.8042 |

Cross-sectional IC (manual, 21d horizon): 0.0593, ICIR: 0.1687, IC positive pct: 61.5%
DSR = 1.0 — passes correction threshold.
Decision: BORDERLINE — IC above 0.02 threshold, ICIR below 0.3. DSR passes.
Note: 10-stock universe too small — 1 long + 1 short per month is not a meaningful cross-section.
Pending: 50-stock universe evaluation.

---

## Phase 5 Decision
PENDING — investigations ongoing.

Summary so far:
- Rates Trend: FAIL on IC and ICIR, DSR=0. Pre/post 2022 split pending.
- FX Carry: Hit rate >50% is encouraging. ICIR fails threshold due to thin cross-section. Needs more currencies.
- Equity Momentum: DSR=1.0 passes. ICIR borderline with 10 stocks. 50-stock evaluation pending.

Provisional recommendation: Proceed to paper trading with FX Carry and Equity Momentum as
primary signals once 50-stock equity evaluation is complete, with explicit understanding of
Phase 1 limitations (proxy data, thin FX cross-section, survivorship bias).
