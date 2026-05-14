# ARCHITECTURE.md
# Systematic Multi-Asset Trading System

**Version:** 0.2
**Last Updated:** 2026-05-14
**Status:** Phases 0–4 implemented; Phase 5 (Signal Hardening) in progress

**Vocabulary note (v0.2):** This document uses **"Stage 1"** and **"Stage 2"**
to describe the data-quality tier of the system — Stage 1 is the current
public-data implementation (FRED, Yahoo, ETF proxies); Stage 2 is the
post-Bloomberg / point-in-time upgrade. These map to **Phases 0–6** and
**Phase 7** respectively in `ROADMAP.md`. Previous versions of this document
used "Phase 1 / Phase 2" for these tiers, which collided with the ROADMAP
phase numbers. All such references have been renamed in v0.2.

---

## 0. Purpose of This Document

This is the single source of truth for system design. Every Cursor session and every Claude
conversation should reference the relevant section before any code is written or reviewed.
If a design decision is made that changes this document, update it immediately and commit.

---

## 1. System Philosophy

### 1.1 Core Principles

1. **Simplicity first, complexity earned.** Every additional signal, parameter, or model
   must justify its existence with genuine out-of-sample evidence.

2. **Process over outcome.** A strategy that loses money for a statistically explainable
   reason is more valuable than one that makes money for an unknown reason.

3. **Overfitting is the primary enemy.** DSR, PBO, and Hansen's SPA corrections are not
   optional add-ons — they are baked into the evaluation pipeline from day one.

4. **Raw data is sacred.** Raw data is immutable once stored. All transformations are
   tracked, versioned, and reproducible.

5. **No production logic in notebooks.** Notebooks are for exploration only. All reusable
   logic lives in `src/` with tests.

6. **Transaction costs are real.** If a strategy does not survive realistic cost modelling,
   it does not exist.

### 1.2 Target Asset Classes (Stage 1)

| Asset Class | Instruments (Stage 1)         | Instruments (Stage 2, post-Bloomberg) |
|-------------|-------------------------------|---------------------------------------|
| FX          | G10 spot + rate differential  | G10 forward rates, EM carry           |
| Rates       | ETF proxies (TLT, IEF, SHY)   | Treasury futures (ZN, ZB, ZF)         |
| Equities    | S&P 500 cash (adjusted close) | Point-in-time Russell 1000 universe   |

### 1.3 Signal Universe (Target — build incrementally)

| Asset Class | Signal           | Type            | Stage   |
|-------------|------------------|-----------------|---------|
| FX          | Carry            | Risk premium    | Stage 1 |
| FX          | Momentum         | Trend           | Stage 1 |
| FX          | PPP deviation    | Value/MR        | Stage 2 |
| FX          | Positioning      | Sentiment       | Stage 2 |
| FX          | Macro surprise   | Macro           | Stage 2 |
| Rates       | Trend (SMA)      | Trend           | Stage 1 |
| Rates       | Carry (slope)    | Risk premium    | Stage 1 |
| Rates       | Macro surprise   | Macro           | Stage 2 |
| Equities    | Momentum (12-1)  | Trend           | Stage 1 |
| Equities    | Value (E/P)      | Value           | Stage 2 |
| Equities    | Quality          | Factor          | Stage 2 |

---

## 2. System Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   LAYER 1: DATA PIPELINE                 │
│                                                          │
│   DataSource (base)                                      │
│   ├── FREDSource        (macro, rates, Alfred vintages)  │
│   ├── YahooSource       (equities, ETF proxies)          │
│   ├── IBSource          (live feed, paper trading)       │
│   └── QuandlSource      (futures, Stage 2)               │
│                                                          │
│   DataCleaner           (outliers, fills, adjustments)   │
│   DataStore             (DuckDB, raw/adjusted/derived)   │
└────────────────────────┬────────────────────────────────┘
                         │  standard DataFrames
┌────────────────────────▼────────────────────────────────┐
│                  LAYER 2: SIGNAL ENGINE                  │
│                                                          │
│   Signal (base)                                          │
│   ├── compute(data) → signal_series                      │
│   ├── evaluate(signal, returns) → SignalMetrics          │
│   └── metadata (params, asset class, frequency)          │
│                                                          │
│   SignalLibrary         (registry of all signals)        │
│   SignalEvaluator       (IC, ICIR, Sharpe, decay)        │
│   SignalCorrector       (DSR, PBO, Hansen SPA)           │
│   SignalCombiner        (equal weight, IC-weight, MVO)   │
└────────────────────────┬────────────────────────────────┘
                         │  combined signal series
┌────────────────────────▼────────────────────────────────┐
│                LAYER 3: PORTFOLIO ENGINE                 │
│                                                          │
│   PositionSizer         (vol target, Kelly, risk parity) │
│   PortfolioConstructor  (gross/net limits, correlation)  │
│   CostModel             (commission, spread, impact)     │
│   RiskManager           (drawdown stops, exposure caps)  │
└────────────────────────┬────────────────────────────────┘
                         │  position series + P&L
┌────────────────────────▼────────────────────────────────┐
│              LAYER 4: BACKTEST / VALIDATION              │
│                                                          │
│   BacktestEngine        (wraps all layers, no lookahead) │
│   WalkForwardEngine     (anchored + rolling)             │
│   CPCVEngine            (combinatorial purged CV)        │
│   ResultEvaluator       (DSR, PBO, SPA, tearsheet)       │
└─────────────────────────────────────────────────────────┘
```

Each layer is independent. Higher layers depend on lower layers only through
well-defined data contracts (see Section 5). No layer may import from a higher layer.

---

## 3. Layer 1 — Data Pipeline

### 3.1 DataSource Base Class

**File:** `src/data/sources/base.py`

**Responsibilities:**
- Define the interface all data sources must implement
- Handle authentication (via environment variables only, never hardcoded)
- Log all fetch operations with timestamps

**Interface:**
```python
class DataSource(ABC):
    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame
    def fetch_batch(self, tickers: List[str], start: date, end: date) -> Dict[str, pd.DataFrame]
    def validate(self, df: pd.DataFrame) -> ValidationResult
    def get_metadata(self, ticker: str) -> dict
```

**Output contract:** Every `fetch()` call returns a DataFrame with:
- DatetimeIndex (UTC, business days, no gaps unless market closed)
- Columns depend on source type but always include `close`
- No NaN values (cleaning happens downstream in DataCleaner)
- Column `source` populated with source identifier string

### 3.2 Source Implementations

#### FREDSource
**File:** `src/data/sources/fred.py`

Key behaviours:
- Use `fredapi` library
- **Alfred vintages by default** — always fetch point-in-time data using `observation_start`
  and `vintage_dates` where available. This is mandatory for macro signals to avoid
  look-ahead bias from data revisions.
- Series list stored in `configs/data/fred_series.yaml`, not hardcoded

Priority series (Stage 1):
- `DFF` — Fed Funds Rate (FX carry proxy)
- `GS10` — 10Y Treasury yield
- `T10YIE` — 10Y inflation breakeven
- `CPIAUCSL` — CPI (for macro surprise signal, use Alfred vintage)
- `PAYEMS` — Non-farm payrolls (use Alfred vintage)
- Euribor, BoE, BoJ policy rates (FX carry)

#### YahooSource
**File:** `src/data/sources/yahoo.py`

Key behaviours:
- Use `yfinance` library
- Always fetch `auto_adjust=True` (dividend and split adjusted)
- Flag known data quality issues per ticker in `configs/data/yahoo_known_issues.yaml`
- For equities universe: document survivorship bias limitation explicitly in metadata

Priority tickers (Stage 1):
- ETF proxies: `TLT`, `IEF`, `SHY`, `HYG`
- FX pairs: `EURUSD=X`, `GBPUSD=X`, `AUDUSD=X`, `NZDUSD=X`, `USDCAD=X`, `USDCHF=X`, `USDJPY=X`
- Equity universe: current S&P 500 constituents (sourced from Wikipedia scrape, documented as survivorship-biased)

#### IBSource
**File:** `src/data/sources/ib.py`

Key behaviours:
- Use `ib_insync` library
- Used for paper trading validation and live execution only in Stage 1
  (Stage 1 = ROADMAP Phase 6 paper trading; pre-Bloomberg historical data still
  sourced from FRED/Yahoo)
- Historical data from IB used only for reconciliation, not primary backtesting source
- Requires TWS or IB Gateway running locally on port 7497 (paper) / 7496 (live)

#### QuandlSource (Stage 2)
**File:** `src/data/sources/quandl.py`

- Futures continuous contracts (CME, ICE)
- Requires Nasdaq Data Link API key
- Build interface now, implement when subscription is active

### 3.3 DataCleaner

**File:** `src/data/cleaning.py`

**Responsibilities:**
- Outlier detection: flag values > 5 sigma from rolling 252-day mean
- Missing data policy:
  - Forward fill up to 3 consecutive business days (document assumption)
  - Beyond 3 days: raise `DataGapError`, do not silently fill
- Corporate actions handler for equities (verify Yahoo adjustments)
- Futures roll handler (Stage 2): back-adjusted vs. proportional adjustment
- All cleaning operations are logged with reasons — never silently modify data

**Output:** Cleaned DataFrame with added columns:
- `is_outlier` (bool)
- `fill_type` (None / 'ffill' / 'adjusted')
- `clean_version` (integer, incremented on each clean pass)

### 3.4 DataStore

**File:** `src/data/store.py`

**Technology:** DuckDB (local, file-based, SQL queryable, fast on DataFrames)

**Schema — three separate databases:**

```
data/
├── raw.duckdb          # Immutable. Never modified after write.
│   └── {source}_{ticker}_{frequency}
├── adjusted.duckdb     # Cleaned + adjusted. Versioned.
│   └── {source}_{ticker}_{frequency}_v{n}
└── derived.duckdb      # Signals, features, portfolio outputs.
    └── {signal_name}_{asset_class}_{frequency}
```

**Key methods:**
```python
class DataStore:
    def write_raw(self, df, source, ticker, frequency) -> None
    def write_adjusted(self, df, source, ticker, frequency, version) -> None
    def read(self, ticker, frequency, layer='adjusted', version='latest') -> pd.DataFrame
    def list_available(self, layer=None) -> pd.DataFrame
    def get_lineage(self, ticker) -> dict   # raw → adjusted → derived chain
```

**Critical rule:** `write_raw()` raises an error if the ticker already exists.
Raw data is never overwritten — append only, or raise.

---

## 4. Layer 2 — Signal Engine

### 4.1 Signal Base Class

**File:** `src/signals/base.py`

```python
class Signal(ABC):
    # Must be defined by every subclass
    name: str
    asset_class: str          # 'fx', 'rates', 'equities'
    signal_type: str          # 'carry', 'momentum', 'value', 'macro', 'sentiment'
    frequency: str            # 'daily', 'weekly'
    params: dict              # all parameters — no hardcoding in compute()
    required_data: List[str]  # ticker list this signal needs

    @abstractmethod
    def compute(self, data: Dict[str, pd.DataFrame]) -> pd.Series:
        """
        Returns a signal series: index=date, values=raw signal (pre-normalisation).
        Must be free of lookahead bias — can only use data available at each point in time.
        """

    def normalise(self, signal: pd.Series, method='zscore', window=252) -> pd.Series:
        """Standardise signal. Default: rolling z-score. Subclasses may override."""

    def evaluate(self, signal: pd.Series, forward_returns: pd.Series) -> SignalMetrics:
        """Convenience wrapper around `SignalEvaluator.evaluate()`. Computes IC, ICIR,
        hit rate, Sharpe, decay at the signal's natural `frequency`. Returns a
        `SignalMetrics` dataclass. See `src/evaluation/signal_evaluator.py`."""

    def get_metadata(self) -> dict:
        """Returns full signal specification for logging and reproducibility."""
```

**Rule:** `compute()` may only use data with index <= current date. The BacktestEngine
enforces this by slicing data before passing to `compute()`.

### 4.2 Signal Implementations (Stage 1)

#### FX Carry Signal
**File:** `src/signals/fx/carry.py`

Logic:
1. For each G10 pair, compute interest rate differential (domestic - foreign 3M rate from FRED)
2. Rank pairs by differential (long high-yield, short low-yield)
3. Return cross-sectional rank signal normalised to [-1, 1]

Parameters (in `configs/signals/fx_carry.yaml`):
- `rate_series`: dict mapping currency to FRED series ID
- `lookback_smooth`: days to smooth rate differential (default: 1)
- `n_long`: number of pairs to go long (default: 3)
- `n_short`: number of pairs to go short (default: 3)

Known limitations (document in signal metadata):
- Rate differential is a proxy for forward premium — not exact
- Does not account for actual rollover costs in spot FX

#### FX Momentum Signal
**File:** `src/signals/fx/momentum.py`

Logic:
1. Compute total return over lookback window (default: 12 months, skip last month)
2. Rank pairs by return
3. Return cross-sectional rank signal

Parameters:
- `lookback_months`: formation period (default: 12)
- `skip_months`: most recent months to skip (default: 1, avoids reversal)

#### Rates Trend Signal
**File:** `src/signals/rates/trend.py`

Logic:
1. Compute fast and slow SMAs of ETF price series
2. Signal = +1 if fast > slow, -1 if fast < slow
3. Optionally scale by distance between MAs

Parameters:
- `fast_window`: default 50
- `slow_window`: default 200
- `scale_by_distance`: bool, default False

**Robustness requirement:** Must be evaluated across a grid of
(fast, slow) pairs. Report results for full grid, not just the best pair.

#### Rates Carry Signal
**File:** `src/signals/rates/carry.py`

Logic:
1. Yield curve slope = 10Y yield - 3M yield (from FRED)
2. Positive slope = positive carry for being long duration
3. Signal = slope normalised by rolling z-score

#### Equity Momentum Signal
**File:** `src/signals/equities/momentum.py`

Logic:
1. For each stock in universe, compute 12-1 month total return
2. Rank cross-sectionally
3. Long top decile, short bottom decile (or long-only top quintile for Stage 1)

Parameters:
- `formation_months`: 12
- `skip_months`: 1
- `universe`: 'sp500_current' (with survivorship bias flag)
- `rebalance_freq`: 'monthly'

### 4.3 SignalEvaluator

**File:** `src/evaluation/signal_evaluator.py`

Computes and returns a `SignalMetrics` dataclass for any signal:

```python
@dataclass
class SignalMetrics:
    ic_mean: float          # Mean IC (rank correlation with forward returns)
    ic_std: float           # Std of IC
    icir: float             # IC / IC_std — target > 0.5
    ic_positive_pct: float  # % of periods with positive IC
    hit_rate: float         # % directionally correct predictions (zero signals excluded)
    signal_sharpe: float    # Annualised Sharpe of signal-weighted returns
    turnover: float         # Mean absolute change in signal per period
    decay_halflife: int     # Periods for IC to decay to 50% — determines rebalance freq
    n_observations: int
    forward_return_horizon: int  # In periods of `frequency` (see below)
    frequency: str          # 'daily' | 'weekly' | 'monthly'
```

**Evaluation horizons:** Default — evaluate at 1, 5, 21, 63 days for daily signals;
at 1, 2, 3, 6 months for monthly signals; analogous for weekly. The horizon with
the strongest IC determines the signal's natural rebalancing frequency.

**Frequency layer (Milestone 5.2):** `SignalEvaluator.evaluate(..., frequency=...)`
resamples both signal and returns to the chosen frequency before evaluation. The
signal is resampled by taking the first non-zero value per period (carrying
forward for all-zero periods); log returns are summed over each period
(compounding under the log convention, CONVENTIONS.md §3.2). The forward-return
shift `shift(-(horizon + 1))` is applied in periods of the chosen frequency.
Annualisation of Sharpe uses 252 / 52 / 12 for daily / weekly / monthly.

### 4.4 SignalCorrector

**File:** `src/evaluation/corrections.py`

Implements three corrections. All are mandatory before any signal is accepted.

#### Deflated Sharpe Ratio (DSR)
Reference: Bailey & López de Prado (2014)

Adjusts observed Sharpe for:
- Number of trials (strategies tested)
- Non-normality (skewness, kurtosis of returns)
- Series length

```python
def deflated_sharpe_ratio(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    skewness: float,
    kurtosis: float
) -> float
```

**Threshold:** DSR > 0 required to consider a signal. DSR > 0.5 considered robust.

#### Probability of Backtest Overfitting (PBO)
Reference: Bailey et al. (2014) — Combinatorial Symmetric Cross-Validation

Steps:
1. Partition time series into N subsets (default N=16)
2. Enumerate all combinations of N/2 subsets as "training"
3. For each combination: find best param in-sample, evaluate out-of-sample
4. PBO = fraction of trials where best in-sample config underperforms median out-of-sample

```python
def probability_of_backtest_overfitting(
    returns_matrix: pd.DataFrame,  # rows=time, cols=param configs
    n_partitions: int = 16
) -> float   # PBO in [0, 1], lower is better. Reject if > 0.5
```

#### Hansen's SPA Test
Reference: Hansen (2005)

Tests whether the best strategy genuinely outperforms a benchmark,
accounting for the fact that we selected it as "best" from many candidates.

```python
def hansens_spa_test(
    benchmark_returns: pd.Series,
    strategy_returns_matrix: pd.DataFrame,
    n_bootstrap: int = 1000,
    significance: float = 0.05
) -> SPAResult  # p_value, reject_null, best_strategy_idx
```

### 4.5 SignalCombiner

**File:** `src/signals/combiner.py`

Combination methods (implement in this order):

1. **Equal weight** — baseline, always computed for comparison
2. **IC-weighted** — weight by rolling ICIR over trailing 252 days
3. **Correlation-penalised** — IC-weighted but penalise signals with high pairwise correlation
4. **MVO-weighted** — treat signals as assets, mean-variance optimise (Stage 2)

```python
class SignalCombiner:
    def combine(
        self,
        signals: Dict[str, pd.Series],
        method: str = 'equal',
        metrics: Optional[Dict[str, SignalMetrics]] = None
    ) -> pd.Series
```

---

## 5. Data Contracts Between Layers

These contracts are mandatory. No exceptions.

### Layer 1 → Layer 2 (Data to Signals)
```
pd.DataFrame:
  index: DatetimeIndex (UTC, daily frequency, business days only)
  columns: at minimum ['close'], optionally ['open', 'high', 'low', 'volume']
  dtypes: float64 for price/rate columns
  NaN policy: no NaN values (DataCleaner guarantees this)
  metadata: df.attrs dict with {source, ticker, frequency, clean_version, known_limitations}
```

### Layer 2 → Layer 3 (Signals to Portfolio)
```
pd.Series:
  index: DatetimeIndex (same frequency as input data)
  values: float in [-1, 1] (normalised, z-scored or ranked)
  name: signal identifier string (e.g. 'fx_carry_g10')
  attrs: {asset_class, signal_type, last_evaluated: date, icir, dsr}
```

### Layer 3 → Layer 4 (Portfolio to Backtest)
```
pd.DataFrame:
  index: DatetimeIndex
  columns: instrument identifiers
  values: target weights (sum to 1 for long-only, sum to 0 for long-short)
  attrs: {gross_exposure, net_exposure, n_positions, rebalance_cost_estimate}
```

---

## 6. Layer 3 — Portfolio Engine

### 6.1 PositionSizer

**File:** `src/portfolio/sizing.py`

Methods:

**Volatility targeting (default):**
- Target annualised portfolio volatility σ* (default: 10%)
- Position size ∝ σ* / (σ_instrument × √252)
- Use rolling 60-day realised vol for σ_instrument

**Risk parity across asset classes:**
- Each asset class gets equal risk budget (1/3 each for Stage 1)
- Within asset class: vol-weight individual positions

**Kelly (reference only initially):**
- Half-Kelly as upper bound check
- Never used as primary sizing method until strategy has 2+ years live history

### 6.2 CostModel

**File:** `src/portfolio/costs.py`

```python
@dataclass
class CostModel:
    commission_per_trade: float       # IB schedule: ~$0.005/share or $1 min
    spread_bps: Dict[str, float]      # instrument → spread in bps
    market_impact_model: str          # 'linear' | 'sqrt' (Almgren-Chriss)
    impact_coefficient: float         # scales with trade size / ADV

def estimate_cost(self, trade: Trade, adv: float) -> float
def apply_costs(self, gross_returns: pd.Series, trades: pd.DataFrame) -> pd.Series
```

**Spread assumptions (conservative, Stage 1):**
- G10 FX spot: 1-2 bps
- Treasury ETFs: 1-3 bps
- Large-cap equities: 3-10 bps

---

## 7. Layer 4 — Backtest Engine

### 7.1 BacktestEngine

**File:** `src/backtest/engine.py`

Core responsibilities:
- Enforce strict no-lookahead-bias: data sliced to `t` before any signal computation
- Apply transaction costs on every rebalance
- Record full trade log (not just P&L)
- Never expose future data to any layer during the backtest loop

```python
class BacktestEngine:
    def run(
        self,
        signals: List[Signal],
        portfolio_config: PortfolioConfig,
        data: DataStore,
        method: str,           # 'walk_forward' | 'expanding' | 'cpcv'
        train_window: int,     # trading days
        test_window: int,      # trading days
        cost_model: CostModel,
        start_date: date,
        end_date: date
    ) -> BacktestResult
```

### 7.2 Walk-Forward Engine

**File:** `src/backtest/walk_forward.py`

Two modes:
1. **Anchored expanding window:** train window grows, test window slides forward
   - Use for non-stationary signals (macro, carry)
   - More conservative estimate of out-of-sample performance
2. **Rolling fixed window:** both windows slide forward
   - Use for mean-reversion signals with shorter memory

**Default split (Stage 1):**
- Train: 2010-01-01 to 2019-12-31 (in-sample)
- Test: 2020-01-01 to present (out-of-sample)
- Rationale: OOS period contains COVID (2020), rates shock (2022), AI rally (2023-24)

### 7.3 CPCV Engine

**File:** `src/backtest/cpcv.py`

Reference: López de Prado (2018) — *Advances in Financial Machine Learning*

- Partition time series into N groups (default: 10)
- For each combination of k groups as test set: train on remainder
- Aggregates OOS path across all combinations
- Produces distribution of OOS Sharpe ratios — not a single number

Computationally expensive. Run after initial walk-forward confirms signal viability.

### 7.4 ResultEvaluator

**File:** `src/backtest/results.py`

```python
@dataclass
class BacktestResult:
    # Returns
    gross_returns: pd.Series
    net_returns: pd.Series       # after costs
    # Risk metrics
    annualised_return: float
    annualised_vol: float
    sharpe_ratio: float
    dsr: float                   # Deflated Sharpe
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_duration: int   # days
    calmar_ratio: float
    # Signal quality
    hit_rate: float
    avg_trade_return: float
    # Costs
    total_cost_bps: float
    avg_cost_per_trade_bps: float
    turnover_annual: float
    # Overfitting tests
    pbo: float
    spa_p_value: float
    # Trade log
    trades: pd.DataFrame
```

Tearsheet output via `pyfolio` or custom matplotlib report saved to `reports/`.

---

## 8. Configuration System

All strategy parameters live in YAML files under `configs/`. No hardcoded parameters anywhere in `src/`.

```
configs/
├── data/
│   ├── fred_series.yaml          # FRED series IDs and metadata
│   ├── yahoo_tickers.yaml        # Yahoo tickers, known issues
│   └── universe_sp500.yaml       # SP500 constituents with dates (survivorship-biased, flagged)
├── signals/
│   ├── fx_carry.yaml
│   ├── fx_momentum.yaml
│   ├── rates_trend.yaml
│   ├── rates_carry.yaml
│   └── equity_momentum.yaml
└── portfolio/
    ├── sizing.yaml               # vol target, Kelly fraction
    ├── costs.yaml                # spread assumptions by instrument
    └── risk_limits.yaml          # gross exposure, net exposure, drawdown stops
```

---

## 9. Key Dependencies

```
# Core
pandas >= 2.0
numpy >= 1.24
scipy >= 1.10
duckdb >= 0.9

# Data sources
fredapi
yfinance
ib_insync              # Interactive Brokers

# Backtesting / stats
pyfolio-reloaded       # tearsheets (maintained fork of pyfolio)
statsmodels            # statistical tests

# Visualisation
matplotlib
seaborn

# Utilities
python-dotenv          # environment variable management
pyyaml                 # config loading
loguru                 # structured logging
pytest                 # testing
```

---

## 10. Dependency Rules (Layer Isolation)

```
src/data/       → no imports from src/signals, src/portfolio, src/backtest
src/signals/    → may import from src/data only
src/portfolio/  → may import from src/signals, src/data
src/backtest/   → may import from all src/ modules
src/evaluation/ → may import from src/signals, src/data (no circular deps)
```

Enforced via `pytest` import tests — any violation fails CI.

---

## 11. Known Limitations (Stage 1 — Document, Do Not Hide)

These are the limitations of the current Stage 1 implementation (public data:
FRED, Yahoo, ETF proxies). Stage 2 = post-Bloomberg upgrade = ROADMAP Phase 7.2.

| Limitation | Impact | Fix |
|------------|--------|-----|
| FX carry uses rate differential proxy, not forward rates | Carry P&L approximate | Stage 2 — Bloomberg forward rates (ROADMAP Phase 7.2) |
| Equity universe is survivorship-biased (current SP500 only) | Backtest returns overstated, especially pre-2010 | Stage 2 — CRSP/point-in-time (ROADMAP Phase 7.2) |
| Rates use ETF proxies, not futures | No leverage dynamics, tracking error | Stage 2 — Quandl futures (ROADMAP Phase 7.2) |
| IB historical data used for reconciliation only | Primary backtest data from Yahoo/FRED | Ongoing |
| No short-selling cost model for equities | Long-short equity P&L overstated | Stage 2 (ROADMAP Phase 7.2) |
| FRED macro data not fully point-in-time for all series | Minor lookahead bias on revised series | Ongoing (use Alfred where available) |
