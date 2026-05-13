# PROGRESS.md
# Build Log and Current Status

**Last Updated:** 2026-05-13

---

## Current Status
**Phase:** 4 complete, pre-Phase 5 signal evaluation in progress
**Tests:** 71 passing
**Next action:** Run full signal evaluation (Rates Trend first, then FX Carry, Equity Momentum)

---

## Completed Phases

### Phase 0 — Environment Setup ✅
- Repo created, venv, .gitignore, directory structure
- Tag: (no tag, initial commit)

### Phase 1 — Data Pipeline ✅
- Milestone 1.1: DataStore (DuckDB) — 4 tests
- Milestone 1.2: FREDSource + Alfred vintage — 4 tests
- Milestone 1.3: YahooSource — 4 tests
- Milestone 1.4: DataCleaner — 5 tests
- Tag: phase1-complete
- Fix: fetch_vintage DataFrame parsing (fredapi returns DataFrame not Series)
- Fix: YahooSource MultiIndex columns (yfinance v0.2+ returns MultiIndex)
- Verified: vintage CPI data differs from revised by 1.77 points ✅

### Phase 2 — Signal Engine ✅
- Milestone 2.1: Signal base class + SignalMetrics — 5 tests
- Milestone 2.2: FXCarrySignal — 3 tests
- Milestone 2.3: RatesTrendSignal — 4 tests
- Milestone 2.4: EquityMomentumSignal — 4 tests
- Milestone 2.5: SignalEvaluator (IC, ICIR, hit rate, Sharpe, decay) — 6 tests
- Milestone 2.6: Corrections (DSR, PBO, Hansen SPA) — 6 tests
- Tag: phase2-complete

### Phase 3 — Portfolio Engine ✅
- Milestone 3.1: CostModel — 5 tests
- Milestone 3.2: PositionSizer (vol target, risk parity) — 4 tests
- Milestone 3.3: PortfolioConstructor (gross/net limits, trades) — 5 tests
- Tag: phase3-complete

### Phase 4 — Backtest Engine ✅
- Milestone 4.1: BacktestEngine + WalkForwardEngine — 5 tests
- Milestone 4.2: CPCVEngine — 4 tests
- Milestone 4.3: TearsheetGenerator — 3 tests
- Tag: phase4-complete
- End-to-end validation on real data ✅
- Rates Trend on TLT (2010-2024): Sharpe -0.52 OOS — expected, 2022-2023 rates shock
- Tag: phase4-validated

---

## In Progress

### Pre-Phase 5 — Signal Evaluation
- [ ] Run SignalEvaluator on Rates Trend signal (TLT, 2010-2024)
- [ ] Apply DSR, PBO, Hansen SPA to Rates Trend
- [ ] Run SignalEvaluator on FX Carry signal
- [ ] Apply DSR, PBO, Hansen SPA to FX Carry
- [ ] Run SignalEvaluator on Equity Momentum signal
- [ ] Apply DSR, PBO, Hansen SPA to Equity Momentum
- [ ] Document results in reports/signal_evaluation_phase1.md
- [ ] Decision: proceed to Phase 5 only if DSR > 0 and PBO < 0.5 for at least one signal

---

## Known Issues / Technical Debt
- FutureWarning: stack(dropna=False) deprecated in pandas 2.x — carry.py:198, momentum.py:181
- FutureWarning: Downcasting on fillna in sizing.py:139
- FutureWarning: fill_method in pct_change in engine.py
- GS10 from FRED returns only 109 rows (monthly frequency) — need to confirm frequency handling
- Equity momentum universe is survivorship-biased (current S&P 500 only)

---

## Signal Evaluation Results (to be filled in)

| Signal | IC Mean | ICIR | DSR | PBO | Decision |
|--------|---------|------|-----|-----|----------|
| Rates Trend | TBD | TBD | TBD | TBD | TBD |
| FX Carry | TBD | TBD | TBD | TBD | TBD |
| Equity Momentum | TBD | TBD | TBD | TBD | TBD |