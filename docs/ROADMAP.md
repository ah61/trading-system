# ROADMAP.md
# Build Phases and Completion Criteria

**Rule:** Do not begin a phase until all completion criteria for the previous phase are met.
Completion means: tests pass, data contracts are verified, and the module has been
reviewed in Claude.ai against ARCHITECTURE.md.

---

## Phase 0 — Environment Setup
**Goal:** Working development environment, repo structure, and basic tooling.
**Target duration:** 1-2 days

### Tasks
- [ ] Create GitHub repo: `trading-system`
- [ ] Set up Python 3.11 virtual environment (`python -m venv .venv`)
- [ ] Install all dependencies from `requirements.txt` and `requirements-dev.txt`
- [ ] Create `.env` file with placeholder keys
- [ ] Create `.env.example` with documented placeholders (commit this, not `.env`)
- [ ] Verify `.gitignore` covers all sensitive paths
- [ ] Set up `black` and `ruff` (add to `requirements-dev.txt`)
- [ ] Create full directory structure per `CONVENTIONS.md`
- [ ] Write `src/exceptions.py` with all custom exception classes
- [ ] Verify Cursor is connected to Anthropic API key and model is claude-sonnet

### Completion Criteria
- [ ] `python -m pytest tests/` runs (zero tests, but no import errors)
- [ ] `black src/` and `ruff src/` run cleanly on empty modules
- [ ] Git repo has initial commit with structure only

### requirements.txt
```
pandas>=2.0
numpy>=1.24
scipy>=1.10
duckdb>=0.9
fredapi
yfinance
ib_insync
pyfolio-reloaded
statsmodels
matplotlib
seaborn
python-dotenv
pyyaml
loguru
```

### requirements-dev.txt
```
pytest
pytest-cov
black
ruff
ipykernel
jupyter
```

---

## Phase 1 — Data Pipeline
**Goal:** Reliable, clean, point-in-time data for FX and Rates. Stored in DuckDB.
**Target duration:** 2-3 weeks
**Depends on:** Phase 0

### Milestone 1.1 — DataStore
- [ ] Implement `src/data/store.py` (DuckDB wrapper)
- [ ] `write_raw()` raises on duplicate, append-only behaviour confirmed
- [ ] `read()` with layer and version selection works
- [ ] `list_available()` returns correct inventory
- [ ] `get_lineage()` traces raw → adjusted chain

**Tests:** `tests/test_store.py`
- [ ] Write/read round-trip preserves DatetimeIndex and dtypes
- [ ] `write_raw()` raises `DataValidationError` on duplicate
- [ ] Version selection returns correct version

### Milestone 1.2 — FREDSource
- [ ] Implement `src/data/sources/base.py` (abstract base)
- [ ] Implement `src/data/sources/fred.py`
- [ ] Alfred vintage fetch working for at least: `CPIAUCSL`, `PAYEMS`
- [ ] `configs/data/fred_series.yaml` populated with Phase 1 series
- [ ] Data stored in `raw.duckdb` after fetch

**Tests:** `tests/test_sources.py`
- [ ] `fetch()` returns correct schema (DatetimeIndex UTC, float64)
- [ ] Alfred vintage fetch returns different values than realtime for revised series
- [ ] `validate()` catches missing columns and wrong dtypes

### Milestone 1.3 — YahooSource
- [ ] Implement `src/data/sources/yahoo.py`
- [ ] `auto_adjust=True` enforced
- [ ] G10 FX spot pairs fetched and stored
- [ ] Rate ETFs (TLT, IEF, SHY) fetched and stored
- [ ] S&P 500 equity universe fetched (survivorship bias documented in metadata)

**Tests:** `tests/test_sources.py`
- [ ] `fetch()` returns adjusted prices for a split stock (verify vs. known event)
- [ ] FX tickers return correct currency pair orientation

### Milestone 1.4 — DataCleaner
- [ ] Implement `src/data/cleaning.py`
- [ ] Outlier detection (5-sigma rolling) working
- [ ] Forward fill up to 3 days, `DataGapError` beyond 3
- [ ] All operations logged with `loguru`
- [ ] `is_outlier` and `fill_type` columns added

**Tests:** `tests/test_cleaning.py`
- [ ] Outliers correctly flagged (inject synthetic outliers)
- [ ] `DataGapError` raised for 4+ day gaps
- [ ] Silent fill never occurs — every fill operation has a log entry

### Phase 1 Completion Criteria
- [ ] Full data pipeline end-to-end: FRED fetch → clean → store → read
- [ ] Full data pipeline end-to-end: Yahoo fetch → clean → store → read
- [ ] All tests pass with `pytest --cov=src/data` showing >80% coverage
- [ ] `ARCHITECTURE.md` Section 3 reviewed against implementation — update if needed

---

## Phase 2 — Signal Engine (Phase 1 Signals)
**Goal:** Three working signals (FX Carry, Rates Trend, Equity Momentum) with full evaluation.
**Target duration:** 3-4 weeks
**Depends on:** Phase 1

### Milestone 2.1 — Signal Base Class
- [ ] Implement `src/signals/base.py`
- [ ] `compute()` abstract method enforced
- [ ] `normalise()` working (z-score and rank methods)
- [ ] `get_metadata()` returns full spec dict
- [ ] `SignalMetrics` dataclass defined in `src/evaluation/signal_evaluator.py`

**Tests:**
- [ ] Cannot instantiate `Signal` directly (abstract)
- [ ] `normalise()` output is within [-1, 1] after clipping

### Milestone 2.2 — FX Carry Signal
- [ ] Implement `src/signals/fx/carry.py`
- [ ] Uses FRED interest rate differentials
- [ ] Cross-sectional ranking across G10 pairs
- [ ] `configs/signals/fx_carry.yaml` created
- [ ] Known limitations documented in metadata

**Validation (not a test — manual review):**
- [ ] Signal is positive for AUD, NZD (historically high-yielders) vs. JPY (historically low)
  over most of the 2010-2019 period
- [ ] Signal flips correctly around known rate cycle changes (e.g. Fed hiking 2015-2018)

### Milestone 2.3 — Rates Trend Signal
- [ ] Implement `src/signals/rates/trend.py`
- [ ] SMA crossover on TLT
- [ ] Evaluated across full parameter grid (fast: 20,50,100; slow: 100,200,252)
- [ ] Grid results saved to `reports/rates_trend_grid.csv`

**Validation:**
- [ ] Signal is negative during 2022 rates sell-off (long duration was wrong in 2022)
- [ ] Signal is positive during 2019-2020 rates rally

### Milestone 2.4 — Equity Momentum Signal
- [ ] Implement `src/signals/equities/momentum.py`
- [ ] 12-1 month formation period
- [ ] Monthly rebalancing
- [ ] Survivorship bias flagged in metadata

### Milestone 2.5 — SignalEvaluator
- [ ] Implement `src/evaluation/signal_evaluator.py`
- [ ] IC computed at 1d, 5d, 21d, 63d horizons for all three signals
- [ ] ICIR, hit rate, decay half-life all computed
- [ ] Results saved to `reports/signal_evaluation_phase1.csv`

**Completion threshold:** Accept signal if IC_mean > 0.02 AND ICIR > 0.3 at any horizon.
Document and keep signals that fail — the failure is information.

### Milestone 2.6 — Corrections
- [ ] Implement DSR in `src/evaluation/corrections.py`
- [ ] Implement PBO in `src/evaluation/corrections.py`
- [ ] Implement Hansen's SPA in `src/evaluation/corrections.py`
- [ ] All three run on all three signals
- [ ] Results documented in `reports/corrections_phase1.md`

### Phase 2 Completion Criteria
- [ ] Three signals computed, evaluated, and correction-adjusted
- [ ] At least one signal passes DSR > 0 and PBO < 0.5
- [ ] All signal tests pass including lookahead bias test
- [ ] `ARCHITECTURE.md` Section 4 reviewed and updated

---

## Phase 3 — Portfolio Engine
**Goal:** Vol-targeted, cost-adjusted portfolio from signal outputs.
**Target duration:** 2 weeks
**Depends on:** Phase 2

### Milestone 3.1 — CostModel
- [ ] Implement `src/portfolio/costs.py`
- [ ] IB commission schedule hardcoded as default, overridable via config
- [ ] Spread assumptions from `configs/portfolio/costs.yaml`
- [ ] `apply_costs()` produces net return series

### Milestone 3.2 — PositionSizer
- [ ] Volatility targeting implemented (default: 10% annualised)
- [ ] Risk parity across asset classes implemented
- [ ] Position sizes respect gross/net exposure limits from config

### Milestone 3.3 — PortfolioConstructor
- [ ] Combines signal → size → weight pipeline
- [ ] Outputs target weight DataFrame per ARCHITECTURE.md data contract
- [ ] Full trade log generated on each rebalance

### Phase 3 Completion Criteria
- [ ] End-to-end: signal → portfolio weights → cost-adjusted P&L
- [ ] Net Sharpe of combined portfolio > 0.3 in-sample (if not, document why)
- [ ] Transaction cost drag computed and reported

---

## Phase 4 — Backtest Engine
**Goal:** Walk-forward validated, overfitting-corrected backtest of full portfolio.
**Target duration:** 2-3 weeks
**Depends on:** Phase 3

### Milestone 4.1 — BacktestEngine (core loop)
- [ ] Strict no-lookahead enforcement (data sliced to t at each step)
- [ ] Anchored walk-forward implemented
- [ ] Rolling walk-forward implemented
- [ ] Full `BacktestResult` dataclass populated

### Milestone 4.2 — CPCV Engine
- [ ] Combinatorial Purged Cross-Validation implemented
- [ ] Produces distribution of OOS Sharpe (not a single number)
- [ ] Runtime benchmarked — flag if >1 hour for N=10 partitions

### Milestone 4.3 — Tearsheet and Reporting
- [ ] `pyfolio-reloaded` tearsheet generated for full backtest
- [ ] Custom summary report saved to `reports/`
- [ ] OOS period (2020-present) clearly separated in all charts

### Phase 4 Completion Criteria
- [ ] Walk-forward OOS Sharpe reported for all three strategies
- [ ] CPCV PBO < 0.5 for at least one strategy
- [ ] DSR > 0 for combined portfolio OOS
- [ ] All results documented in `reports/phase4_summary.md`
- [ ] Ready for paper trading if OOS Sharpe > 0.5 net of costs

---

## Phase 5 — Paper Trading
**Goal:** Live market validation via Interactive Brokers paper account.
**Target duration:** 3+ months (minimum before considering live capital)
**Depends on:** Phase 4

### Milestone 5.1 — IBSource Live Feed
- [ ] Real-time data ingestion from IB TWS paper account
- [ ] Signal computed on live data matches backtest signal on same date

### Milestone 5.2 — Order Management
- [ ] Target weights → orders generated
- [ ] Orders submitted to IB paper account
- [ ] Fill prices recorded and compared to backtest cost assumptions

### Milestone 5.3 — Live Monitoring
- [ ] Daily P&L tracked vs. backtest expectation
- [ ] Signal drift alerts (signal IC degradation over rolling 60 days)
- [ ] Drawdown kill switch: halt if drawdown exceeds 2× backtest max drawdown

### Kill Switch Criteria (define before going live)
- Strategy halted if any of:
  - Drawdown > 2× backtest max drawdown
  - Rolling 60-day IC < 0 for primary signal
  - Monthly net P&L worse than -3σ of backtest monthly distribution

### Phase 5 Completion Criteria
- [ ] 3 months paper trading completed
- [ ] Annualised paper Sharpe within 1 std of backtest OOS Sharpe
- [ ] Execution slippage modelled and within cost assumptions
- [ ] Ready for Phase 6 capital allocation decision

---

## Phase 6 — Live Capital + Signal Expansion
**Target duration:** Ongoing
**Depends on:** Phase 5 passing

### 6.1 Additional Signals (add one at a time, validate each)
- FX PPP deviation (value signal)
- FX positioning (CFTC COT)
- FX macro surprise (CPI, NFP)
- Rates macro surprise
- Equity value (E/P, B/P)
- Equity quality

### 6.2 Bloomberg Integration
- Replace FRED rate proxy with actual FX forward rates
- Replace ETF proxies with Treasury futures (Quandl)
- Obtain point-in-time equity universe (eliminate survivorship bias)

### 6.3 Portfolio Expansion
- Add EM FX carry
- Add credit (HYG, LQD spread strategies)
- Formal portfolio optimization (Black-Litterman or risk parity)

---

## Interview Readiness Checklist

This project is also an interview asset. At each phase, maintain:

- [ ] A clean GitHub repo (public or shareable) with `README.md` explaining the system
- [ ] A written summary of each strategy: rationale, signal construction, results, limitations
- [ ] A known-failures document: what didn't survive OOS, and why
- [ ] The ability to explain DSR, PBO, and Hansen's SPA in plain English with your numbers
- [ ] A portfolio-level attribution: how much return came from each asset class and signal

The most valuable thing to say in an interview is not "my strategy made X%."
It is: "here is my process, here is what survived it, and here is what I learned from what didn't."
