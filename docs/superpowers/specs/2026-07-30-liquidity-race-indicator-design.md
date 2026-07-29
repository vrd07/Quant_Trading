# Liquidity Race Indicator — Design

**Date:** 2026-07-30
**Status:** Approved, ready for implementation planning
**Scope:** Research + chart marking tool. Zero live-system wiring.

## Problem

Mark XAUUSD liquidity pools on the MT5 chart, and rank them by which price is most
likely to reach **first**. The marking half has prior art in this repo
(`src/microstructure/live_marks.py::liquidity_pools`), but it orders levels by raw
distance only. The ranking half is new, and the requirement is that the displayed
number be a measured frequency rather than a hand-tuned score.

## Decisions

| Question | Decision |
|---|---|
| Level families | Un-swept swing highs/lows, equal highs/lows, session & period extremes. **No round numbers.** |
| Ranking derivation | Calibrated on history, coefficients baked into the indicator |
| Horizon | Next 24 hours, rolling |
| Displayed metric | `P(this level is hit first)` — a true race over the live set |
| Acceptance gate | Must beat a distance-only baseline OOS **and** be calibrated OOS |
| Architecture | Offline Python calibration → self-contained MQL5 indicator + parity harness |
| History | Extend XAUUSD back to 2022 via `scripts/fetch_dukascopy.py` before calibrating |

### Architecture rationale

Three architectures were considered:

- **A (chosen)** — offline calibration, coefficients baked into a self-contained
  `.mq5`. Attach and it works: no Python process, no bridge, no CSV staleness, and
  levels derive from the broker's own bars so the chart matches what the EA trades.
  Cost: level-detection logic exists in two languages and can drift **silently**.
  Mitigated by a mandatory parity harness (below).
- **B** — Python computes everything, MQL5 renders a JSON. Zero drift, single source
  of truth, and could later consume tick-flow features from the orderflow tool. Rejected
  because the chart goes blank whenever the writer process dies, and it would be a third
  always-on process beside the bot and `volatility_monitor`.
- **C** — pure MQL5 with hand-picked weights. Rejected: that is an uncalibrated model.

## Components

| Path | Role |
|---|---|
| `src/microstructure/liquidity_levels.py` | **The Python definition.** Pure functions: detect levels from a bar frame, build the per-level feature vector. Generalises `live_marks.py::liquidity_pools`. Imported by both the research script and the parity checker so Python cannot disagree with itself. |
| `scripts/research_liquidity_race.py` | Calibration study: walk history, label first-touch, fit the conditional logit, emit report + coefficients. |
| `mt5_indicators/liquidity_coefficients.mqh` | **Generated, committed.** Fitted β, z-score constants, calibration window, and OOS metrics as comments. Regenerating is the only sanctioned way weights change — never hand-edited. |
| `mt5_indicators/GoldenChart_Liquidity.mq5` | The indicator: detect, feature-ise, score, rank, draw. |
| `scripts/check_liquidity_parity.py` | Drift guard: diff the indicator's CSV export against the Python definition over the same window. Non-optional. |
| `tests/unit/test_liquidity_levels.py` | Unit tests for detection, feature math, and fitter recovery. |
| `reports/liquidity_race_calibration.md` | Generated: reliability curves, OOS metrics, baseline comparison, effective N. |

## Data flow

```
CALIBRATE (offline, rerun ~quarterly)
  XAUUSD_5m_real.csv (extended to 2022) -> resample 15m -> per snapshot bar:
      detect live level set -> features -> forward-walk 96 bars (24h)
      -> label "which member was hit first, or none"
  -> conditional-logit fit
  -> liquidity_coefficients.mqh + reports/liquidity_race_calibration.md

LIVE (on chart)
  MT5 15m bars -> detect level set -> features -> beta.x -> softmax -> rank
  -> rays + labels + ranking panel
```

### Timeframe constraint

Detection timeframe is fixed at the calibration timeframe (15m) and is **independent of
the chart the indicator is attached to**. If detection followed chart TF, the same
indicator on M1 and H1 would produce different level sets while displaying probabilities
calibrated for neither. It is exposed as an input defaulting to 15m; changing it away
from the calibrated value prints a warning to the Experts log. Attaching to a 5m or H1
chart for viewing is fine — the levels stay 15m-derived.

## Level definition

This section is load-bearing twice: Python and MQL5 must implement it identically, and
`check_liquidity_parity.py` diffs them against it.

### Swing pivots

Bar *i* is a swing high when `high[i] == max(high[i-N .. i+N])`, with **N = 5** (same
fractal convention as `bos_structure`). It confirms N bars late, so there is no lookahead.
It is a liquidity pool only while **un-swept**: the first later bar whose high trades at
or above it deletes the level from the set. Mirror for swing lows.

### Equal highs / lows

Cluster un-swept pivots within **`0.10 x ATR(14)`** of each other. ATR-scaled rather than
the existing fixed `eq_tol_pts = 0.5`, because gold ran 2763 -> 4106 within the current
CSV alone and a fixed point tolerance makes the calibration non-stationary.

A cluster of 2 or more collapses into one `equal_highs` / `equal_lows` level priced at
the cluster **extreme** (max for highs, min for lows) — the price at which every stop in
the cluster is actually taken. Constituent solo pivots are removed so nothing is
double-marked.

### Session & period extremes

Prior *completed* session high/low, current session high/low (live, still forming),
prior-day high/low, prior-week high/low.

A **forming** session extreme cannot be "swept" by definition — when price is printing a
new session high, that level *is* current price and would score a spurious ~100%. So a
current-session extreme enters the set only while it sits at least `0.25 x ATR` from
price; inside that band it is not a pool, it is just price.

Sessions in UTC: **Asia 00:00–07:00, London 07:00–16:00, NY 13:00–21:00** — the Asia
window matches what `london_breakout` already uses. Overlap between London and NY is
intentional and normal.

The un-swept rule applies here too: once PDH trades through, it drops out.

The indicator derives its UTC offset from `TimeGMT()` vs `TimeCurrent()` rather than
trusting broker server time (see `project_broker_tz_fix`).

### The choice set

Because the ranking is a conditional logit over competing alternatives, the *set* is part
of the model. It must be defined identically in calibration and live or the probabilities
are wrong:

> The **6 nearest un-swept levels on each side**, restricted to those within
> **8 x ATR(14)** of current price. Levels within `0.10 x ATR` of each other merge,
> keeping the higher-priority type: equal-cluster > session/period extreme > solo swing.

Levels beyond 8 ATR are effectively unreachable inside 24h and would only dilute the
softmax.

### Touch

A 15m bar wick reaching the level: `high >= level` for levels above price, `low <= level`
for levels below. Wicks count — that is the stop run. Calibration uses the same bid-based
bars the chart draws, so the definition is consistent even though a real buy-side stop
fills on the ask.

## The race study

### Snapshots and labels

At every 15m bar close after a **1000-bar warmup** (500 bars for the ATR percentile
window + 480 for a full prior week of 15m bars, rounded up): build the choice set,
compute features, then walk
forward 96 bars (24h). The label is which member of the set was touched first, or
**`none`** if nothing was touched inside the horizon.

When a single bar spans two levels, the **nearer one is credited**. Within-bar ordering is
unknowable from OHLC, and crediting the farther level would bias the model toward distance.

### Model — conditional logit with an outside option

For each level in the set, `v_i = beta . x_i`. The "nothing gets hit" alternative is pinned
at `v_0 = 0`.

```
P(level i first) = exp(v_i) / (1 + sum_j exp(v_j))
P(none in 24h)   = 1        / (1 + sum_j exp(v_j))
```

McFadden's conditional logit is the correct estimator here: it handles a choice set that
changes size bar to bar, its probabilities sum to 1 *including* the very common "price
goes nowhere" outcome, and it reduces in MQL5 to a dot product, an `exp`, and a normalise.

### Features

Roughly 12, z-scored using calibration-set mean/std baked into the `.mqh`:

1. `log1p(dist_atr)` — `|level - close| / ATR(14)`
2. `n_closer_same_side` — set members between price and this level
3. `side_up` — above/below price (captures gold's drift asymmetry)
4. `type_equal` — one-hot vs solo-swing baseline
5. `type_session` — one-hot vs solo-swing baseline
6. `log_age_bars` — bars since the level formed
7. `touch_count` — prior approaches within `0.25 x ATR` that did not breach
8. `trend_align` — level side vs sign of EMA(50) slope on 15m
9. `atr_pctile` — ATR(14) vs its trailing 500-bar percentile
10. `session_london`, `session_ny` — snapshot-time dummies (Asia is the baseline)
11. `log1p(dist_atr) x atr_pctile` — the one interaction, on the same log-distance term
    as feature 1, because high volatility is precisely what makes a far level reachable

L2-regularised. Regularisation strength selected on IS only, by day-blocked CV.

### Sample-size honesty

105k 5m bars looks like a lot, but 24h forward windows overlap 95-deep. The effective
independent sample is roughly the **number of days**, not the number of snapshots. Naive
standard errors would be about 10x too optimistic — this is the exact failure that
produced the RSI-reversal "12/12 plateau" that then scored PF 0.39 on a fresh holdout
(see `project_rsi_reversal_m1`).

Therefore:

- Fit on all snapshots (more signal, regularised).
- **Evaluate only on day-blocked out-of-sample data**, with block-bootstrap confidence
  intervals resampling whole days.
- Report effective N beside every headline number in the report.

### Splits

Step 0 extends XAUUSD history to 2022 via `scripts/fetch_dukascopy.py`.

- **IS:** 2022-01 -> 2025-12
- **OOS:** 2026-01 -> 2026-07, never touched during fitting or hyperparameter selection

If the Dukascopy extension fails or returns unusable data, fall back to IS 2025-02 ->
2025-12 / OOS 2026-01 -> 2026-07 and state the reduced power prominently in the report.

## Acceptance gate

Not a strategy, so `backtest.md`'s 8 PnL gates do not apply. Two legs, both on OOS:

**Leg 1 — beats distance.** The baseline is not a strawman argmin; it is the *same
conditional logit* fitted with `log1p(dist_atr)` as its only feature. The full model must
win on both OOS multiclass log-loss and OOS top-1 accuracy, and the day-block bootstrap
95% CI on the **log-loss difference** (model minus baseline, resampling whole days) must
exclude zero.

**Leg 2 — calibrated.** Reliability curve by decile of predicted `P(first)` on OOS.
Expected calibration error <= 5 percentage points, and no decile off by more than 10pp.

### Failure handling

The indicator ships either way; only what it displays changes.

- **Leg 1 fails** -> ship in *distance-rank mode*: rank badges plus the baseline
  probability. The report and the indicator's Experts-log banner both state that the
  enriched model did not beat distance.
- **Leg 2 fails alone** -> fit Platt recalibration on IS and re-check OOS. If still
  miscalibrated, display rank badges with no percentage.
- **Both fail** -> rank badges only, distance-ordered, banner states the model is
  uncalibrated.

The chart never shows a number the data did not earn.

## Chart rendering

Each level draws as a ray anchored at the bar where it formed, extended right, so age is
visible at a glance. Buy-side (above price) and sell-side (below) get distinct colours.
**Rank drives weight and style:** `#1` per side is solid and thick, lower ranks
progressively thinner and dashed. Label format: `EQH 4118.50 · #1 · 34%` — type tag,
price, rank, probability.

A corner panel carries the ranking as a readable table:

```
LIQUIDITY RACE — next 24h        ATR 6.2
                          dATR   P(first)
 #1  EQH   4118.50       +1.9      34%
 #2  PDL   4088.20       -2.4      19%
 #3  SwH   4131.00       +4.3       9%
 #4  ASIAL 4079.90       -3.1       7%
 ...
     nothing touched               22%
```

`dATR` is signed distance in ATR multiples — positive above price, negative below.

The `nothing touched` row is permanent. It is the honest denominator; without it a 34%
top rank reads far more confident than it is.

**Refresh:** full re-detection on each closed 15m bar; a 5-second timer recomputes only
the price-dependent features (distance, current-session extremes) so the ranking stays
live between bars.

**Objects:** namespaced `GC_LQ_`, cleared on deinit — same convention as the rest of the
`GoldenChart_*` family.

**Inputs:** detection TF, pivot N, equal-tolerance ATR multiple, levels per side, max
distance ATR, panel on/off, colours, and `InpExportCSV` / `InpExportBars` for the parity
harness.

**No alerts.** Not requested, and they would be the first thing to go stale.

## Testing

**Python unit tests** on hand-built bar fixtures:

- pivot confirmation timing (confirms exactly N bars late, no lookahead)
- un-swept invalidation (level dies on the first breaching wick)
- equal-clustering at ATR tolerance, including the extreme-vs-mean pricing rule
- session extremes across the UTC day and week boundaries
- choice-set capping, distance restriction, and merge priority ordering
- each feature's value against a worked example
- fitter recovery: generate choices from a known beta, confirm the fit recovers it

**Parity harness** — the acceptance test for the MQL5 port. Run the indicator with
`InpExportCSV` over a multi-week window; `check_liquidity_parity.py` re-runs the Python
definition over the same bars and requires:

- level sets match **exactly** (price, type, formation bar)
- scores match to **1e-4**

This requires a manual step on the user's terminal — attach the indicator in export mode
once — the same loop as the EA recompiles.

## Scope boundary

Research and chart only. Nothing imports into `src/strategies`, `src/risk`, or
`src/execution` — this is a marking tool, exactly like the orderflow tool's Stage 1.

If the calibration turns out strong, wiring it into a strategy is a separate decision
behind the full `backtest.md` 8-gate process.

## Out of scope

- Round-number levels (explicitly excluded by the user)
- Price alerts / notifications
- Tick-flow or order-flow features (the orderflow tool's absorption and defended-levels
  layers could feed a future v2, but would require architecture B)
- Any automatic trading action
