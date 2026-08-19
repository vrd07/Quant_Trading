# Volume Profile Indicator — Design

**Date:** 2026-08-17
**Status:** Approved for implementation
**Scope:** Chart + research tooling. Not a strategy. Not wired into `src/strategies`, `src/risk`, or `src/execution`.

## 1. Purpose

Mark VAH, VAL and VPOC automatically on the live XAUUSD chart, in distinct
colors with named labels, at the highest fidelity a gold CFD allows — and on
top of that substrate, produce the **Auction Market Theory context read** the
World Class Edge methodology is built on: profile shape (P / b / D), open type
relative to prior value, balance vs out-of-balance regime, initial balance, and
a multi-session composite.

Accuracy on the **live, developing** profile is the primary requirement. A
profile that is correct on history but drifts intraday is worthless for the
stated use.

### 1.1 The honesty constraint

There is no real traded volume on a gold CFD. MT5's `volume` and
`volume_real` fields are zero or synthetic for XAUUSD — brokers publish tick
counts, not contracts. Genuine volume-at-price for gold exists only on CME GC
futures.

Everything this indicator draws is therefore a **tick-density profile**. The
panel says so. No part of the UI implies traded contracts. This is a property
of the instrument, not a defect to engineer around.

## 2. Provenance and portability

The design follows the methodology taught in the World Class Edge masterclasses
(Tom Vorwald's PBD framework, Fabio Valentini's profile framing, Serge
Hoffmann's balance-day scalping). Source material was lesson-level course
descriptions; the videos carry no transcripts, so **spoken numeric thresholds
were not recoverable**. They also do not exist in the published literature —
see §9.0.

What ports to an MT5 gold CFD and what does not:

| Course concept | Portable? |
|---|---|
| Volume profile, VAH/VAL/VPOC | Yes — as tick density |
| P / b / D shape classification | Yes |
| Open type vs prior value area | Yes |
| Balance vs out-of-balance regime | Yes |
| Initial balance | Yes (see §12 caveat on IVB) |
| Naked POC, "left-side" HTF levels | Yes |
| LVN as absorption zones | Yes, as levels |
| **Footprint charts** | **No.** Requires exchange trade prints classified by aggressor side. |
| **CVD / delta / absorption confirmation** | **No honest version exists on a CFD.** |
| **Order book speed / speed of tape** | **No.** No depth feed on this bridge. |

The methodology's *Location* layer ports in full. Its *Aggression* layer does
not, and is deliberately absent rather than approximated — an uptick/downtick
"delta" built from quote mids would look authoritative and carry no
information. Users must not read the absence as an oversight.

## 3. Architecture

Three units, mirroring the established `GoldenChart_Liquidity` pattern.

| Unit | File | Responsibility |
|---|---|---|
| Definition | `src/microstructure/volume_profile.py` | Pure Python. Binning, POC, value area, shape, open type, regime, IB, nodes, naked POC. No MT5 import. This file *is* the spec. |
| Chart | `mt5_indicators/GoldenChart_VolumeProfile.mq5` | A port of the above. Object prefix `GC_VP_`. |
| Proof | `scripts/check_volume_profile_parity.py` | Proves the port agrees with the definition. |

**Scope boundary**, enforced by
`tests/unit/test_volume_profile.py::TestScopeBoundary`: `volume_profile.py`
imports nothing from `src/strategies`, `src/risk`, `src/execution`. Wiring
these levels or regimes into a trading decision is a separate decision behind
the full `backtest.md` 8-gate process.

⚠️ Not to be confused with `src/monitoring/liquidity_levels.py` or
`src/microstructure/liquidity_levels.py` — unrelated modules.

## 4. Session definition

One profile per **broker D1 candle**, read via `iTime(_Symbol, PERIOD_D1, i)`.

Rationale: the profile then covers exactly one daily candle, so it can be
eyeball-verified against the chart. Gold brokers typically sit at GMT+2/+3
precisely so the D1 boundary lands on the 17:00-ET NY close, which is the
correct auction boundary.

- `InpProfileDays` (default 10) completed sessions, plus the developing one.
- `InpSessionUTCOverride` (default `-1` = off): if the broker's D1 boundary is
  wrong for this account, force sessions to start at this fixed UTC hour and
  run for exactly 24 hours, ignoring D1 entirely. No DST shifting.
- A session yielding fewer than `InpMinSessionTicks` (default 5,000) usable
  ticks — holidays, half-sessions, a broken feed — is **skipped entirely**
  rather than drawn as a garbage profile.

## 5. Binning

Rows are anchored to an **absolute price grid**:

```
row_index(p) = floor(p / row_size)
row_low(i)   = i * row_size
row_high(i)  = (i + 1) * row_size
row_mid(i)   = (i + 0.5) * row_size
```

`row_size` = `InpRowSize` (default `0.10`, i.e. 10 cents).

⚠️ **Deliberate deviation from the published MQL5 reference**, which anchors
bins to each session's low (`floor((p - session_low) / size)`). Session
anchoring shifts the grid by a random sub-cent offset every day, so the same
price falls in a different row on different days and VPOCs are not comparable
across sessions. Absolute anchoring costs nothing and makes every level
reproducible and cross-session comparable.

`InpRowMode` switches to a fixed-row-count mode (height derived from session
range) for users who want TradingView's default behavior. Absolute-grid fixed
size is the default.

⚠️ Course settings quoted for NQ/ES (e.g. a 24-row profile) are
instrument-specific, not methodological. They are **not** adopted; 24 rows on
gold is a different object entirely.

## 6. Volume accumulation

### 6.1 Tick mode (primary)

`CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL, from_msc, to_msc)`.

Each accepted tick contributes weight `1.0` at its **mid price**
`(bid + ask) / 2`. `InpTickPriceMode` allows Mid / Bid / Ask; parity requires
Python to use the same choice.

**Spread filter (live-accuracy fix).** A tick whose spread exceeds
`InpMaxSpreadUSD` (default `1.00`) is **rejected**. At the daily rollover and
during news, gold's spread blows out past $1 and the mid price lands in a row
where nothing actually traded. These are quote artifacts, not activity.
Rejected ticks are counted and shown in the panel so the filter can never hide
a data problem silently.

⚠️ All distance-valued inputs in this design are expressed in **price units
(USD), never in "points"**. Brokers quote XAUUSD at either 2 or 3 digits, so
`_Point` is `0.01` on some feeds and `0.001` on others — a points-denominated
default would silently mean $1.00 on one broker and $0.10 on another, and the
$0.10 version would reject nearly every tick.

A tick is otherwise valid if it has a non-zero bid and ask and falls inside
the session window.

### 6.2 M1 fallback

If tick history is genuinely unavailable (see §7.2), drill into M1 bars and
spread each bar's `tick_volume` **uniformly** across the rows its high–low
spans. Uniform is the standard and is reproducible; no OHLC weighting.

Per-profile source is recorded as `TICK`, `M1` or `PENDING` and **displayed**.
You always know which fidelity you are looking at.

## 7. The developing profile — live correctness

This section is the reason the design exists. Two MT5 semantics make the
obvious implementation silently wrong.

### 7.1 Incremental cursor: retain-and-replay the boundary millisecond

`CopyTicksRange` is **inclusive on both `from_msc` and `to_msc`**, and
**multiple distinct ticks can share one millisecond**. So the naive cursor
`from_msc = last_seen_msc` re-returns every tick at that millisecond on every
refresh — a compounding double-count that inflates the developing profile and
drags VPOC toward whatever price was busiest at the last refresh boundary.
Skipping one tick does not fix it, because the duplicates are distinct ticks.

Correct algorithm. Invariant: **nothing at or after `cursor_msc` has been
processed.**

```
1. n = CopyTicksRange(sym, ticks, COPY_TICKS_ALL, cursor_msc, now_msc)
2. if n <= 0: return          (handle n < 0 per §7.2)
3. boundary = ticks[n-1].time_msc
4. process every tick with time_msc <  boundary
5. cursor_msc = boundary       (the tail at `boundary` stays unprocessed)
```

The final partial millisecond is deferred one refresh cycle — irrelevant at a
5s cadence. If an entire batch shares one millisecond, nothing is processed and
the cursor holds; the next cycle resolves it.

This design is also **inherently gap-healing**: if the terminal disconnects,
the cursor does not advance, and the next successful call backfills the missed
span automatically. No special reconnect path is needed.

`cursor_msc` initialises to the session start in milliseconds.

### 7.2 Never degrade silently while history is downloading

On first attach `CopyTicksRange` returns `-1` while MT5 is still synchronising
tick history. A naive implementation reads that as "no ticks" and falls back to
M1 — permanently, and invisibly.

On `n < 0`: mark the profile `PENDING`, log the `GetLastError()` value, and
**retry on the next timer tick**. Only after `InpTickRetryLimit` (default 12,
≈60s at a 5s cadence) consecutive failures does the profile fall back to M1 and
relabel itself `M1`. The panel shows `PENDING` throughout, so a
reduced-fidelity profile can never be mistaken for a full one.

### 7.3 Session rollover

Detected on the timer, not only on new-bar: when `iTime(_Symbol, PERIOD_D1, 0)`
changes, freeze the developing profile into the completed cache, emit its final
levels and shape, and start a fresh developing profile with a new cursor.

### 7.4 Caching

Completed sessions are computed **once** and cached in a struct array; they are
never recomputed. Only the developing profile recomputes, and only
incrementally. `InpRefreshSec` default 5.

### 7.5 Integrity readout

The panel displays, for the developing profile: source (`TICK`/`M1`/`PENDING`),
ticks accepted, ticks rejected by the spread filter, session elapsed %, and
last update time. Plus an internal assertion that `sum(row_volumes)` equals
ticks accepted — a cheap invariant that catches any accumulation bug
immediately rather than after it has quietly moved a level.

## 8. POC and value area

### 8.1 POC

Row with maximum volume. Tie → the row whose mid is closest to
`(session_high + session_low) / 2` (CQG rule). Still tied → the lower row
index. Fully deterministic.

### 8.2 Value area — TradingView / CQG single-row expansion

```
target = total_volume * InpValueAreaPct   (default 0.70)
va = poc_volume;  hi = lo = poc_index

while va < target:
    above = (hi+1 <= max_row) ? vol[hi+1] : NONE
    below = (lo-1 >= min_row) ? vol[lo-1] : NONE
    if above is NONE and below is NONE: break
    if above is NONE: absorb below
    elif below is NONE: absorb above
    elif above > below: absorb above
    elif below > above: absorb below
    else:                                  # tie
        d_above = (hi+1) - poc_index
        d_below = poc_index - (lo-1)
        absorb the nearer;  if equidistant, absorb above   # TradingView rule
```

`InpVAAlgorithm` switches to the classic Steidlmayer/CBOT two-row-pair method
(sum the two rows above vs the two below, absorb the winning pair) for users
matching Sierra Chart or ThinkOrSwim. Single-row is the default so levels
agree with TradingView.

### 8.3 Level definitions

Platforms disagree here, so this is explicit:

- **VPOC** = `row_mid(poc_index)`
- **VAH** = `row_high(hi)` — upper edge of the highest absorbed row
- **VAL** = `row_low(lo)` — lower edge of the lowest absorbed row

This guarantees the band genuinely *contains* its ≥70% of volume.

## 9. Profile shape classification — P / b / D

The central object of the methodology. Shape drives regime, and regime decides
whether mean-reversion or directional logic is even appropriate.

### 9.0 Why this is calibrated rather than looked up

**No published numeric cutoffs for P / b / D exist.** NinjaTrader's own
reference, QuantVue, Optimus Futures and the TradingView educational material
all teach the shapes as visual pattern recognition — "POC typically located in
the centre", "long and thin lower section". The only number the field
quantifies is the value area at 70%. The World Class Edge lessons are no
exception.

So the constants cannot be sourced; they can only be *chosen*. This section
reduces the choice to a single constant and grounds that one against a
published frequency rather than against taste.

### 9.1 Primary statistic — volume-distribution skewness

```
weight_i = vol_i / total_volume
mean     = Σ weight_i · row_mid_i
var      = Σ weight_i · (row_mid_i − mean)²
sd       = √var
skew     = Σ weight_i · (row_mid_i − mean)³ / sd³
```

Chosen over POC position because it is **dimensionless** (comparable across
sessions, volatility regimes and instruments), uses the **whole distribution**
rather than a single argmax row that a few ticks can move, and has a **natural
zero** — a symmetric profile is exactly a D.

Sign convention, stated because it is easy to invert:

- **negative skew** → mass at high prices, thin tail below → **P**
- **positive skew** → mass at low prices, thin tail above → **b**

⚠️ **A published implementation disagrees on the meaning.** BackQuant's
*Volume Profile Skew* labels positive skew (bottom-heavy) as "ACCUMULATION",
i.e. bullish. Vorwald's b-model is bottom-heavy and **bearish** — same
geometry, opposite reading. This spec follows the course. Do not import that
script's semantics on the assumption that identical geometry implies identical
meaning.

Degenerate guard: if `sd == 0`, or the profile has fewer than
`InpMinRowsForShape` (default 5) non-empty rows, shape is `UNCLASSIFIED` and
skew is not reported.

### 9.2 Corroborating measures — displayed, never classifying

Always shown next to the letter so the geometry behind it stays visible:

```
poc_position    = (VPOC − low) / (high − low)      # 0 = at low, 1 = at high
va_width_frac   = (VAH − VAL) / (high − low)
upper_tail_frac = volume_above(VAH) / total_volume
lower_tail_frac = volume_below(VAL) / total_volume
```

### 9.3 Classification — one constant

```
P              skew <= −InpSkewThreshold
b              skew >= +InpSkewThreshold
D              |skew| <  InpSkewThreshold
UNCLASSIFIED   degenerate profile only (§9.1 guard)
```

`InpSkewThreshold` is the **only** constant the classification turns on. The
value-area-width term from an earlier draft was removed: it added a second free
parameter without adding information the skew did not already carry, and
`va_width_frac` remains visible as a corroborator.

Every non-degenerate session therefore receives a letter. That is safe
*because the skew value is always displayed* — a session at `skew −0.02` is
visibly marginal and a session at `skew −1.40` visibly is not. Marginality is
read from the number, not encoded in a fuzzy middle band.

### 9.4 Calibrating `InpSkewThreshold`

`scripts/calibrate_profile_shape.py` builds profiles over the 151 sessions of
XAUUSD tick data in `data/ticks/XAUUSD/` and sweeps the threshold.

**Objective:** choose the threshold whose resulting **D (balanced) frequency is
closest to 50%** — Dalton's base rate for normal/rotational days in *Mind Over
Markets*, and the most-cited and most robust number in that taxonomy.

Why this objective and not another:

- It targets a **published frequency**, not a preference.
- It is **outcome-free.** No trade results enter the calibration, so it is
  structurally incapable of overfitting to profit — the failure mode recorded
  in `project_rsi_reversal_m1`.
- It is calibrated **on gold**, not inherited from index futures.

⚠️ **Stated limits of the anchor.** Dalton's day types are a *different
taxonomy* from P/b/D — his Normal Day is defined by initial-balance extension,
not by profile skew — and his base rates come from **equity-index RTH
sessions**, not 23-hour gold. The 50% target is therefore a defensible prior,
not ground truth. The resulting P / b / UNCLASSIFIED split is **reported as an
output, never targeted**, and the calibration is judged on whether the sweep is
stable (a broad plateau) rather than on hitting 50% exactly.

Output: `reports/volume_profile_shape_calibration.md`, recording the sweep, the
chosen threshold, target vs achieved D frequency, the full class distribution,
and the plateau width. Re-run if `InpRowSize` or the session definition changes,
since both alter the histogram the skew is computed from.

Until the calibration has been run, the code ships an uncalibrated placeholder
and the panel renders `⚠ UNCALIBRATED` on every frame. That marker is not
suppressible.

### 9.5 Developing-profile guard

A two-hour-old profile always looks like something it isn't — every session
starts life looking like a P or a b simply because it has only travelled one
direction so far. Shape and regime on the developing profile therefore report
`FORMING` until the session is `InpRegimeMinElapsedPct` (default 50%) elapsed.

## 10. Open type and value migration

### 10.1 Open type — today's open vs the prior session's value area

Vorwald's decision input for counter-trade vs trend-follow vs fade-the-gap.

```
OPEN_ABOVE_RANGE   open >  prior_high
OPEN_ABOVE_VA      prior_VAH <  open <= prior_high
OPEN_INSIDE_VA     prior_VAL <= open <= prior_VAH
OPEN_BELOW_VA      prior_low <= open <  prior_VAL
OPEN_BELOW_RANGE   open <  prior_low
```

The session's open is the first accepted tick's price (tick mode) or the first
M1 bar's open (fallback).

### 10.2 Value migration — today's VA vs the prior session's VA

```
HIGHER              VAL_today > VAH_prior          (no overlap, up)
LOWER               VAH_today < VAL_prior          (no overlap, down)
OVERLAPPING_HIGHER  overlap, both edges higher
OVERLAPPING_LOWER   overlap, both edges lower
INSIDE              today's VA contained in prior VA
ENGULFING           today's VA contains prior VA
```

Evaluated on completed sessions; shown as `FORMING` for the developing one per
§9.5.

## 11. Balance / imbalance regime

Fabio's "profile framing" — the regime word that gates strategy family.

```
BALANCED             shape == D
OUT_OF_BALANCE_UP    shape == P
OUT_OF_BALANCE_DOWN  shape == b
UNCLEAR              shape == UNCLASSIFIED
FORMING              developing profile below the §9.5 elapsed threshold
```

Displayed as the panel's headline line. It is a **description of the completed
auction, not a forecast**, and is labelled as such.

## 12. Initial Balance

`InpIBMinutes` (default 60) from session start. Marks IB high, IB low, and IB
extension guides at ±1× the IB range (standard market-profile convention).

⚠️ Fabio's **IVB (Initial Volume Breakout)** is explicitly described as an
evolution of the range-based ORB that incorporates volume, and his rule is not
recoverable from the course descriptions. What ships is **standard
range-based IB**, and the README says so. This is not IVB and must not be
labelled as such.

## 13. Composite profile

A second profile spanning `InpCompositeDays` (default 5) completed sessions,
with its own VAH/VAL/VPOC, its own shape classification, and a distinct color
set. This is the multi-session balance structure the methodology frames
against; daily-only profiles miss it.

The composite is rebuilt by summing the cached per-session row histograms — no
tick re-read — so it is nearly free.

## 14. HVN / LVN

⚠️ **LVN is the load-bearing one.** Per the course material, absorption occurs
most often at low-volume nodes — the thin "gaps" left in the auction. HVN is
secondary context.

Prominence-based, not a bare threshold:

- **LVN** = local minimum between two HVNs whose volume is ≤ `InpLVNRatio`
  (default 0.50) of the lower flanking HVN.
- **HVN** = local maximum with prominence ≥ `InpHVNProminencePct` (default 15%)
  of POC volume, and ≥ `InpNodeMinSeparationRows` (default 10 rows = $1.00)
  from any stronger HVN.

⚠️ **These three thresholds are uncalibrated display heuristics** — unlike
`InpSkewThreshold`, which §9.4 grounds against a published frequency, these
three are chosen. They are warned about in the `.mq5` header, the Python
docstring and the README. `InpShowHVN` can be disabled independently of
`InpShowLVN`.

## 15. Naked / virgin POC

A completed session's POC that no *later* bar's `[low, high]` has traded
through. Walk forward from the session's end over M15 bars; the first bar whose
range contains the POC price tags it.

Naked POCs are drawn extended to the right edge in a distinct dashed style with
their date in the label. Tagged POCs are dropped. These are the "left-side
levels" the methodology uses to judge whether a setup's risk-to-reward is
viable.

The scan covers exactly the `InpProfileDays` completed sessions — there is no
separate lookback input. Raising `InpProfileDays` extends naked-POC history
along with everything else, so the two can never disagree about which sessions
exist.

## 16. Rendering

| Element | Color | Style |
|---|---|---|
| VPOC | `clrGold` | solid, width 2 |
| VAH | `clrDeepSkyBlue` | solid, width 1 |
| VAL | `clrTomato` | solid, width 1 |
| Naked POC | `clrMagenta` | dashed, width 1 |
| Composite VPOC / VAH / VAL | `clrOrchid` | solid, width 2, dotted edges |
| IB high / low | `clrSlateGray` | dash-dot |
| Histogram (in VA) | `clrSteelBlue` | filled rectangle |
| Histogram (outside VA) | `clrDimGray` | filled rectangle |
| LVN | `clrDarkOrange` | dotted |
| HVN | `clrDarkSlateGray` | shelf |

Every level line carries a **right-anchored text label naming it and its
price** — `VPOC 4493.15`, `VAH 4501.20`, `VAL 4486.40` — per the original
requirement. Each completed session additionally carries its shape letter
(`P`, `b`, `D`, `?`) at the profile's top edge. All colors are inputs.

**Histogram geometry.** Each row is one `OBJ_RECTANGLE` anchored at the
session's start time, extending right for
`(row_volume / max_row_volume) * session_duration * InpHistogramWidthPct`
(default `0.35`). Bars therefore grow rightward from the session's left edge
and never overflow into the next session.

**Object budget.** ~400 rows per gold session; ten sessions of histogram would
be 4,000 objects and would crawl. The histogram is therefore drawn only for the
most recent `InpHistogramProfiles` (default 2) profiles; older sessions get
lines and labels only. A hard cap `InpMaxObjects` (default 3,000) logs a
warning and stops drawing rather than freezing the terminal.

All objects use prefix `GC_VP_` and are removed in `OnDeinit`.

## 17. Context panel

The consolidated read, one corner block:

```
XAUUSD VOLUME PROFILE          src TICK   skewT 0.42
─ DEVELOPING ───────────────────────────
 shape    P   skew −0.88  (poc .71  vaW .48  tailLo .06)
 regime   OUT OF BALANCE ↑
 open     ABOVE VA   (prior VAH 4501.20)
 VPOC 4508.40   VAH 4512.10   VAL 4496.30
 IB   4499.80 – 4507.60  (60m)
─ PRIOR SESSION ────────────────────────
 shape    D   skew +0.11   value  OVERLAPPING HIGHER
 VPOC 4493.15   VAH 4501.20   VAL 4486.40
─ COMPOSITE 5d ─────────────────────────
 shape    D   skew −0.07
 VPOC 4488.90   VAH 4515.60   VAL 4470.20
─ naked POC  4462.75 (11 Aug)  4441.20 (06 Aug)
 ticks 184,302   rejected 412   elapsed 63%
```

`InpShowPanel` toggles it. The `src` field, the `skew` values and the
`⚠ UNCALIBRATED` marker (shown in place of `skewT` until §9.4 has been run) are
never suppressible — they are the honesty markers. The letter is never
displayed without its skew value beside it.

## 18. Alerts

Fire when price touches VAH / VAL / VPOC of the prior session, and optionally
of the developing profile (`InpAlertDeveloping`, default false — a developing
level moves, so it re-arms constantly). `InpAlertLVN` additionally alerts on
LVN touches, since those are the method's absorption zones.

A **touch** is defined at tick level: the current bid crosses the level in
either direction since the previous tick. Bar-based touch detection would miss
intrabar tags entirely, which is the case that matters on a live chart.

Debounce: one alert per level per session, plus a re-arm band — price must
leave by ≥ `InpAlertRearmUSD` (default `0.50`) before that level can fire
again. `Alert()` and `SendNotification()` behind separate inputs.

## 19. Parity

⚠️ **What parity can and cannot prove.** Dukascopy ticks (`data/ticks/XAUUSD/`)
are not the broker's ticks, so absolute row volumes will never match. A naive
cross-vendor comparison would be a *fake* test that passes or fails for reasons
unrelated to correctness.

What is actually verified, which covers every line of algorithm logic:

1. **Algorithm layer (exact).** With `InpExportCSV = true` the indicator
   exports its per-row histogram *and* its resolved outputs. Python recomputes
   POC / VAH / VAL / shape / regime / open type / value migration / HVN / LVN
   **from that exported histogram** and asserts exact equality on levels and
   classifications, set equality on nodes.
2. **Grid layer.** The indicator exports its bin edges; Python verifies them
   against the `floor(p / row_size)` formula independently.
3. **Binning layer.** Python's accumulation is unit-tested against synthetic
   tick fixtures with hand-computed answers.

Only the raw data source differs, and that difference is disclosed rather than
papered over.

Workflow matches the liquidity indicator: export → copy CSVs to `data/parity/`
→ `python scripts/check_volume_profile_parity.py --dir data/parity`. Levels and
labels must match exactly; volumes to 1e-4. **Do not relax the tolerances to
make it pass.**

## 20. Tests

`tests/unit/test_volume_profile.py`:

- Value-area expansion against hand-computed fixtures, **including both tie
  branches** (nearer-to-POC, and equidistant→above).
- Two-row-pair algorithm against its own fixtures.
- Absolute grid anchoring; a price exactly on a row boundary.
- POC tie-break, both stages.
- Invariant: value area always contains ≥ `InpValueAreaPct` of total volume.
- Edge cases: single-row profile, perfectly flat profile, empty session.
- **Skew**: a symmetric histogram gives `skew == 0` exactly; a mirrored
  histogram gives exactly negated skew; skew is **invariant under a uniform
  price rescale** (multiply every row price by k) and **invariant under a
  uniform volume rescale** (multiply every row volume by c). These two
  invariances are precisely what make the statistic comparable across
  sessions and volatility regimes — the reason it was chosen over POC
  position — so they are asserted, not assumed.
- **Shape classifier**: hand-built P, b and D histograms classify correctly;
  the sign convention is pinned by an explicit test (mass-at-highs ⇒ `P`) so an
  inversion cannot pass; a degenerate profile returns `UNCLASSIFIED`; the §9.5
  elapsed guard returns `FORMING`.
- **Open type**: all five branches, including open exactly on `prior_VAH` and
  exactly on `prior_low`.
- **Value migration**: all six branches, including exact-edge equality.
- Composite equals the sum of its constituent session histograms.
- Incremental-cursor simulation: feeding ticks in arbitrary batch splits —
  including batches that split *within* a millisecond — produces a histogram
  identical to one-shot processing. This is the §7.1 regression test.
- Spread-filter rejection accounting.
- HVN/LVN determinism.
- `TestScopeBoundary`: no imports from strategies / risk / execution.

⚠️ Use `abs=0.0` on any near-zero comparison. `pytest.approx` uses
`max(rel * expected, 1e-12)`, so an expected value below 1e-12 compares equal to
zero and the assertion passes vacuously.

## 21. Files

**New**
- `src/microstructure/volume_profile.py`
- `mt5_indicators/GoldenChart_VolumeProfile.mq5`
- `scripts/check_volume_profile_parity.py`
- `scripts/calibrate_profile_shape.py`
- `tests/unit/test_volume_profile.py`

**Generated (never hand-edit)**
- `reports/volume_profile_shape_calibration.md` — written by
  `calibrate_profile_shape.py`. Hand-tuning the threshold it reports turns a
  calibrated constant back into a guess.

**Updated**
- `mt5_indicators/README.md`

## 22. Non-goals

- Not wired into any strategy, and **no edge is claimed**. These are reference
  levels and a context read. The shape/regime labels describe the auction that
  already happened; they do not forecast.
- No real-volume claim. Tick density only (§1.1).
- No footprint, delta, CVD, or tape-speed surrogate (§2).
- No IVB — standard range-based IB only (§12).
- Candle profiles (per-candle volume distribution) are portable and deliberately
  deferred to a possible v2.
- No multi-symbol support in v1 — XAUUSD on the attached chart. The code has no
  hard symbol gate, so it will render on other symbols; only the `$0.10` row
  default is gold-specific.
