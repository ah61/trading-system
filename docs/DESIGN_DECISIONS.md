# DESIGN_DECISIONS.md
# Design Rationale and Open Questions

**Version:** 0.1
**Last Updated:** 2026-05-14
**Status:** Living document — append decisions, do not rewrite history.

---

## 0. Purpose

This document captures **why** key design choices were made, not just **what** they
are. ARCHITECTURE.md describes the system as it is. ROADMAP.md describes what
gets built when. This document explains the reasoning behind both, so a future
contributor (or future self) can re-examine decisions with full context.

Decisions are appended in chronological order with date stamps. Older decisions
are not edited; if a decision is superseded, a new entry is added that
references and overturns the earlier one.

---

## DD-001 — IB integration scope and timing
**Date:** 2026-05-14
**Status:** Decided

### Context
Initial Phase 5 plan had IB integration deferred to Phase 6 (paper trading). User
asked whether IB should come earlier, specifically as a third historical data
source alongside FRED and Yahoo. Concern was data quality at daily frequency.

### Decision
IB integration is scoped narrowly to **FX (real-time and historical) and futures
(future-phase)** only. IB is **not** used as a general-purpose source. Equities
continue from Yahoo; macro continues from FRED. IB integration is scheduled as
Milestone 5.12, before Phase 6 but after the catalogue and transformation
infrastructure is in place.

### Rationale
**Where IB is genuinely better:**
- Real tradable FX rates with forward points (vs Yahoo's single mid-rate)
- Treasury futures with proper continuous contracts (vs ETF proxies)
- Forward-spot basis enables true CIP-implied carry signal (5.13)

**Where IB is no better:**
- Equity daily closes — Yahoo `auto_adjust=True` matches IB adjusted data
- Macro series — IB doesn't carry these

**Operational cost of IB:**
- TWS/Gateway must be running locally for every API call
- Connection drops require reconnect logic
- ~50 req/sec API limit, daily caps on historical data
- Async/await patterns (via `ib_insync`) inconsistent with rest of codebase

### Why before Phase 6 but after 5.7/5.8
The catalogue (5.7) handles source selection ("EURUSD: prefer IB, fall back to
Yahoo"). Building IB before the catalogue would mean wiring it in twice — once
into signal code directly, then again into the catalogue. Cheaper to do it once.

### Open items
- CME futures real-time subscription not active on the IB account. Required for
  Phase 7.2 work; ~$10/month. Address when 7.2 begins, not before.

---

## DD-002 — Universe expansion strategy
**Date:** 2026-05-14
**Status:** Decided

### Context
Discussion of whether to expand the equity universe from 10 to 200 stocks
(original ROADMAP target). User pushed back: expansion should be considered
along three axes (asset class diversity, native frequency, cross-section breadth),
not just one.

### Decision
Universe expansion in **5.10**, with the following targets:

| Asset class | Current | Post-5.10 | Rationale |
|---|---|---|---|
| FX | G10 (7 USD-anchored pairs) | G10 + 5 EM (MXN, BRL, ZAR, INR, TRY) = 12 pairs | EM brings genuinely different factor exposure; G10 alone is 2-3 hidden factors |
| Equities | 10 tickers | ~55 sector-balanced (5 per GICS sector × 11 sectors) | N=10 too thin for cross-sectional momentum ranking |
| Rates | 4 ETFs (TLT, IEF, SHY, HYG) | + TIP, LQD, HYG (already there) ≈ 6 ETFs | Modest expansion; major rates work waits for futures (Phase 7.2) |

### Rationale
**Why EM FX (not just adding more G10):** G10 currencies share 2-3 dominant
macro factors (USD strength, risk appetite, carry). Adding more G10 doesn't
increase factor diversity. EM does.

**Why 55 equities not 200:** Phase 5 is infrastructure validation. The current
signals show near-zero IC. More data won't fix bad methodology. 55 stocks is
enough for meaningful cross-sectional ranking (top decile = 5-6 names) without
the cache footprint and fetch time of 200+ tickers. Re-evaluate after methodology
work is more mature.

**Why modest rates expansion:** Curve trades (slope, butterfly) and credit
spreads (HYG vs LQD) need futures-quality data to work properly. Adding more
Treasury ETFs at this stage gets us cross-section breadth that mostly reflects
the same USD duration factor.

### Sector-balancing rationale (equities)
Momentum has well-documented sector-rotation behavior. Picking top-50 by
market cap concentrates in mega-cap tech and produces a momentum signal that's
mostly a "tech beta" signal. Hand-curating 5 stocks per sector ensures the
cross-sectional ranking is meaningful across the economy.

### Open items
- EM FX data quality from Yahoo drops materially outside G10. May need IB
  for these pairs in practice (5.12+). For 5.10 we'll fetch from Yahoo and
  document the quality caveat.
- Survivorship bias persists. Phase 7.2 fix via CRSP.

---

## DD-003 — Modeling layer split
**Date:** 2026-05-14
**Status:** Decided

### Context
Original plan treated "modeling" as a single layer encompassing regime models,
nowcasts, and signal combination. User asked for thoughts; agreed these are
different things and should be separated.

### Decision
The modeling layer splits into three sublayers in ARCHITECTURE.md:

- **2a — Conditioning layer.** Outputs classifications or scalar weights that
  signals consume (e.g. market regime, vol filter, risk-on/risk-off indicator).
  Lives upstream of signals. A conditioning output is "just another variable"
  that flows through the catalogue.

- **2b — Predictive layer.** Fitted models that produce point estimates or
  derived variables (e.g. GDP nowcast, factor model). Require training, walk-
  forward fitting, and no-lookahead testing. Outputs flow into the catalogue
  as derived variables.

- **2c — Combination layer.** Combines multiple signals into a single allocation
  signal. Methods: equal-weight, IC-weighted, correlation-penalised, MVO across
  signals. Conditioning weights apply here.

### Rationale
Conflating these obscures their different roles:
- Conditioning is a **multiplier** — it scales signal strength based on regime
- Predictive is a **producer** — it generates new variables/signals from data
- Combination is an **aggregator** — it merges signals into portfolio targets

Architecturally clean property: because conditioning and predictive outputs
are both `pd.Series` in `derived.duckdb`, they flow through the same catalogue
infrastructure as raw data. Signal code doesn't care whether a variable is
raw, transformed, conditioning, or predicted.

### Open items
- Signal combination methodology not yet decided beyond the four standard
  methods listed in ARCHITECTURE.md. Will be revisited during Phase 6.
- "Where does carry computation belong?" Today it's inline in `compute()`.
  Could arguably be a *derived variable* (rate differential) consumed by a
  thinner signal. Leave as-is until catalogue work (5.7-5.8) settles the
  pattern.

---

## DD-004 — Multi-frequency policy
**Date:** 2026-05-14
**Status:** Decided

### Context
Strategies operate at different frequencies (FX Carry monthly, Rates Trend
daily). Variables have native frequencies that may or may not match the strategy.
Question: how to handle frequency mismatches.

### Decision
Three-rule policy:

1. **Use native frequency when ≥ strategy frequency.** If strategy is daily and
   variable is daily, use as-is. If strategy is monthly and variable is daily,
   resample down using appropriate aggregation (last-of-period for prices, sum
   for returns).

2. **Forward-fill when variable's native frequency is coarser than strategy.**
   If strategy is daily and CPI is monthly, forward-fill the CPI between prints.
   Log the mismatch. Documents the "information content is still monthly even
   if the index is daily" reality.

3. **Never interpolate.** Linear, spline, or model-based interpolation introduces
   look-ahead bias because interpolation uses future values to fill in past
   gaps. Forbidden.

### Rationale
Forward-fill matches the real-world "what was knowable at time t" constraint.
Interpolation does not.

The honest-frequency layer (`information_content_frequency` separate from
`index_frequency`) was considered and rejected as over-engineering for current
needs. Can be added later if it turns out to matter.

### Open items
- Need to verify the frequency layer (5.2) implements all three rules correctly,
  particularly the aggregation methods for non-price variables. Audit during 5.7.

---

## DD-005 — FX pair label convention
**Date:** 2026-05-14
**Status:** Deferred (revisit at Phase 6)

### Context
Milestone 5.5 produces FX pair labels of the form `<non-USD>/USD` mechanically:
`EUR/USD`, `GBP/USD`, ..., `JPY/USD`, `CAD/USD`, `CHF/USD`. For the last three,
market convention uses USD-first ordering (`USD/JPY`, `USD/CAD`, `USD/CHF`).
The signal math is identical; the issue is presentation.

### Decision
Keep mechanical labels internally. Defer market-convention translation to a
display layer to be built before Phase 6 (paper trading via IB) and before any
results are shown to external traders.

### Rationale
Internal consistency is more important than display polish during research.
Adding special-case logic to `_iter_pairs` to flip seniority for some currencies
is ugly and error-prone. A display-layer translation is cleaner.

The cost is that current reports show `JPY/USD` instead of `USD/JPY`. For an
internal research artifact, this is acceptable. For an external one, it isn't.
The trigger for fixing this is "we're about to show this to someone" or "we're
about to wire it to IB order entry."

### Open items
- Add to Phase 6 prerequisites checklist.

---

## DD-006 — Data catalogue: stateful, not stateless
**Date:** 2026-05-14
**Status:** Decided (locks 5.7 design)

### Context
Milestone 5.7 wires the existing `VariableCatalog` (5.3 registry) into the
signal pipeline. Question: should the catalogue be a stateless YAML registry
that signal code queries for metadata only, or a stateful runtime object that
holds references to DataStore and sources and serves data via lookup?

### Decision
**Stateful.** The catalogue object holds DataStore + source references and
exposes `catalogue.get(variable_name, frequency)` which returns a `pd.Series`.
Internally, the catalogue:

1. Checks if the derived form is already in `derived.duckdb` → return it
2. If not, checks if the base variable is in `raw.duckdb` → apply transformation,
   write to derived, return
3. If neither, fetches from source, writes raw, applies transformation, writes
   derived, returns

Signal code doesn't know about DuckDB layers, sources, or transformations.
It just declares what it wants.

### Rationale
Cleanest API for signal authors. Single source of truth for "where does data
come from and how is it computed." Cache-first behavior maximizes reuse and
minimizes redundant computation.

Stateless catalogue would push lookup-and-fetch logic into every signal,
duplicating it 10+ times. That's exactly the redundancy the catalogue is
supposed to eliminate.

### Open items
- Catalogue concurrency: what happens if two signals request the same derived
  variable in parallel? Probably fine given DuckDB's locking, but worth a test.
- Cache invalidation: if a transformation YAML changes, do all derived variables
  re-compute? Probably yes; needs explicit invalidation logic in 5.8.

---

## DD-007 — Variable naming convention
**Date:** 2026-05-15
**Status:** Decided

### Context
When introducing catalogue variables in 5.7, we needed a rule for how to name
them. Three candidates: use vendor IDs as variable names; rename everything to
human names; or a mixed rule. After discussion we agreed the rule needs to
hold across vendor systems (FRED, Yahoo, Bloomberg, IB) and over the project's
lifetime as more variables are added.

### Decision

**Variable names are always chosen by the project**, never inherited from
vendors as policy. The fact that a chosen name happens to match a vendor ID
(e.g. `DFF`) is incidental, not a rule. Vendor identifiers always live inside
the variable's spec, never as the variable name conceptually.

**Convention (UPPER_SNAKE_CASE, source-agnostic, domain-recognisable):**

| Category | Naming rule | Examples |
|---|---|---|
| Headline macro (well-known short ID) | Choose the same string as the vendor where it's already a clean domain term | `DFF`, `GS10`, `UNRATE`, `INDPRO`, `M2SL`, `SPX` |
| Cryptic vendor IDs | Choose a clean human name; vendor ID in spec | `CPI_HEADLINE` (not `CPIAUCSL`), `EUR_RATE` (not `IR3TIB01EZM156N`) |
| Specific instruments | `{ASSET}_{FIELD}` | `TLT_CLOSE`, `EUR_USD_SPOT`, `AAPL_CLOSE` |
| Transformed variables | `{INPUT}_{TRANSFORM}_{PARAMS}`, left-to-right application order | `DFF_ZSCORE_252`, `US_REAL_GDP_DLOG`, `TLT_CLOSE_VOL_63`, `US_REAL_GDP_DLOG_MEAN_4` |
| Derived (signals, regimes) | Descriptive | `FX_CARRY_SIGNAL`, `REGIME_RATES_TREND` |

**Transformation naming details:**
- Suffixes read left-to-right as the order of application. `US_REAL_GDP_DLOG_MEAN_4`
  means "real GDP → log-difference → 4-period rolling mean."
- Suffixes describe transformations we did, not properties of the source.
  If FRED provides a series already seasonally adjusted, don't add `_SA` — the
  SA status lives in the spec description. Add `_SA` only when we performed
  seasonal adjustment ourselves.
- If a transformation chain produces an unwieldy name (4+ suffixes), declare
  an intermediate transformed variable for clarity rather than naming the chain.

### Rationale
**Why not just use vendor IDs.** Some vendor IDs are bookkeeping artifacts
(`CPIAUCSL` carries a FRED-specific `AUCSL` suffix nobody says aloud). Some
are deliberately obscure (`IR3TIB01EZM156N`). Bloomberg has similar issues
(`EUR003M Index` is clean-ish but not how people talk). Vendor IDs cannot
serve as a universal naming rule.

**Why not rename everything aggressively.** When a vendor ID already matches
common usage (`DFF`, `GS10`, `SPX`), renaming it makes the project harder to
read, not easier. The rule "choose a good name" lets us pick `DFF` when `DFF`
is good and `CPI_HEADLINE` when `CPIAUCSL` is bad.

**Why "always chosen by us" as the framing.** Functionally identical to
"inherit if clean, rename if cryptic," but cleaner conceptually: there's only
one rule (choose a good name), not two (inherit OR rename). Removes the
question "is this vendor ID clean enough to inherit?" — there's no inheriting,
only choosing.

### Open items
- For variables where multiple "common" names exist (e.g. 10Y Treasury yield:
  `GS10`, `DGS10`, `USGG10YR`), document the choice in the spec's
  `description` and any common aliases. No automated alias system planned.

---

## DD-008 — Bulk universe handling
**Date:** 2026-05-15
**Status:** Decided

### Context
Equity Momentum operates over 50+ tickers. Declaring each as a separate
variable spec in `market.yaml` is verbose and duplicative. The alternative
considered — implicit/conventional inference inside the catalogue ("any
undeclared ticker is a Yahoo equity by default") — was rejected as too magical.

### Decision

**Universe files declare a variable template plus a ticker list. The
catalogue expands the template into one explicit variable spec per ticker on
load.** Generated specs are first-class: they appear in `catalogue.names()`,
are inspectable via `catalogue.get_spec()`, and obey the same source/frequency
contracts as hand-declared variables.

Example (`configs/data/universes/sp500_sector_balanced.yaml`):
```yaml
template:
  layer: raw
  source: yahoo
  frequency: daily
  instrument_type: equity
  adjustment: auto_adjust
  variable_name_pattern: "{ticker}_CLOSE"

tickers:
  - AAPL
  - MSFT
  - JPM
  # ... 50 more
```

On load, the catalogue produces 50 variable specs, each named per the pattern
(`AAPL_CLOSE`, `MSFT_CLOSE`, ...) and identical in structure to a hand-declared
spec in `market.yaml`.

### Rationale
**Why not implicit inference.** Inference fails the explicitness test: signal
code referencing `data["AAPL_CLOSE"]` should have a discoverable definition.
With inference, there's no spec to inspect — a future contributor has to read
catalogue source to understand what `AAPL_CLOSE` is. Templates preserve
explicitness while eliminating boilerplate.

**Why not per-ticker hand-declaration.** 50+ entries with identical structure
is busywork that obscures the actual content (the ticker list). It also makes
universe-level changes (e.g. switching the entire equity universe from Yahoo
to IB) require editing 50 entries instead of one template.

**Why templates are first-class.** Tests can mock universe expansion. Catalogue
tools (lineage, used_by, names) work uniformly across hand-declared and
template-expanded variables. Switching universes is a YAML edit, not a code
change.

### Implementation notes
- Universe files live in `configs/data/universes/`.
- The catalogue loads universe files after the main variable files. Each
  expanded spec is treated as if it were declared in `market.yaml`.
- Naming pattern is `variable_name_pattern: "{ticker}_CLOSE"` (or `{ticker}_PX`,
  etc.) using `str.format()` substitution.
- Validating no name collisions between hand-declared and template-expanded
  specs happens during catalogue construction; collision is a `CatalogError`.

### Open items
- Template-expanded specs currently only support a single source. Multi-source
  templates (for when EM FX comes from IB-preferred-then-Yahoo) are a future
  extension; the schema can grow to accept a `sources:` list inside the
  template.

---

These are items raised but not resolved. Each should be revisited at the
indicated milestone.

### OQ-001 — Methodology investigation order (revisit at 5.9)
We agreed to investigate methodology fixes before pure universe expansion.
Four candidate fixes, ranked by expected value:

1. Forward-spot basis carry (highest EV, blocked on IB → 5.13)
2. Vol conditioning (medium EV, easy to test → 5.14)
3. Quarterly horizon evaluation (low EV, near-free → 5.9)
4. Signal combination (medium-high EV, requires multiple signals first → Phase 6/7)

Order of execution: 5.9 first (free), then 5.11 (regime filter as prerequisite
infrastructure for 2a conditioning layer), then 5.13 + 5.14.

### OQ-002 — Documentation consolidation (revisit at end of Phase 5)
ROADMAP, ARCHITECTURE, PROGRESS, and now DESIGN_DECISIONS exist in parallel.
Some overlap. At end of Phase 5 review whether to consolidate or maintain
separate.
