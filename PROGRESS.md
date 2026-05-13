# PROGRESS.md
# Build Log and Current Status

**Last Updated:** 2026-05-13

---

## Current Status
**Phase:** 4 complete, pre-Phase 5 signal evaluation in progress
**Tests:** 72 passing
**Next action:** Investigate Equity Momentum hit rate anomaly, then make Phase 5 decision

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
- [ ] Investigate Equity Momentum hit rate anomaly (~9% vs expected ~50%)
- [ ] Document results in reports/signal_evaluation_phase1.md
- [ ] Decision: proceed to Phase 5 only if DSR > 0 and PBO < 0.5 for at least one signal

---

## Known Issues / Technical Debt
- FutureWarning: stack(dropna=False) deprecated in pandas 2.x — carry.py:198, momentum.py:181
- FutureWarning: Downcasting on fillna in sizing.py:139
- FutureWarning: fill_method in pct_change in engine.py
- GS10 from FRED returns only 109 rows (monthly frequency) — need to confirm frequency handling
- Equity momentum universe is survivorship-biased (current S&P 500 only)
- FX Carry evaluation uses DFF log returns as proxy — not ideal, should use actual FX pair returns
- Equity Momentum hit rate ~9% is anomalous — evaluator may be misaligning monthly signal with daily returns

---

## Signal Evaluation Results

| Signal | Best Horizon | IC Mean | ICIR | Hit Rate | Sharpe | DSR | PBO | Decision |
|--------|-------------|---------|------|----------|--------|-----|-----|----------|
| Rates Trend | 1d | 0.0117 | 0.1121 | 0.5120 | 0.2202 | 0.0000 | N/A | FAIL |
| FX Carry | 1d | 0.0234 | 0.1501 | 0.8143 | 0.6138 | 0.0000 | N/A | BORDERLINE |
| Equity Momentum | 63d | -0.0248 | -0.0718 | 0.0932 | 1.8041 | 1.0000 | N/A | INVESTIGATE |

PBO not applicable for single-config signals — requires 2+ parameter configurations.

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

### FX Carry — Full Results (DFF/EUR/GBP rates, 2010-2024)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0234 | 0.1501 | 0.8143 | 0.6138 |
| 5d | 0.0313 | -0.3468 | 0.8071 | -0.4939 |
| 21d | -0.0047 | -0.0505 | 0.8091 | -0.1898 |
| 63d | 0.0075 | 0.0651 | 0.8122 | 0.1878 |

Decision: BORDERLINE — IC_mean > 0.02 at 1d but ICIR < 0.3. Hit rate ~81% is strong.
DSR = 0.0 — right at boundary.
Note: Forward return proxy (DFF log returns) is not ideal for FX carry evaluation.
The 81% hit rate against rate changes is interesting but the correct benchmark
should be actual FX pair returns. This needs re-evaluation with proper FX data.

### Equity Momentum — Full Results (10-stock subset, 2010-2024)
| Horizon | IC | ICIR | Hit Rate | Sharpe |
|---------|-----|------|----------|--------|
| 1d | 0.0418 | 0.1410 | 0.0958 | 1.3686 |
| 5d | -0.0007 | -0.0021 | 0.1008 | -0.5247 |
| 21d | -0.0602 | -0.1681 | 0.0966 | -1.0227 |
| 63d | -0.0248 | -0.0718 | 0.0932 | 1.8041 |

Decision: INVESTIGATE — DSR = 1.0 is promising but hit rate ~9% is anomalous.
Likely cause: multi-asset evaluator collapsing monthly signal against daily returns
causing misalignment. Needs fix before results can be trusted.
Universe: 10-stock subset only — not representative of full S&P 500 momentum.
Survivorship bias applies — current members only.

---

## Phase 5 Decision
PENDING — waiting for Equity Momentum investigation.
FX Carry is borderline and warrants re-evaluation with proper FX return data.
No signal clearly passes DSR > 0 and ICIR > 0.3 simultaneously yet.