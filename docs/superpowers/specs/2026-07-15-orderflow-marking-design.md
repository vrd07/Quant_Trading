# Order-Flow Microstructure Marking Tool — Design

**Date:** 2026-07-15
**Status:** Approved (Stage 1); Stage 2 gated on Stage 1 verdict
**Symbol scope:** XAUUSD (research-only; nothing wired into live trading)

## Problem & data reality

The user wants "ML on order flow" — microstructure patterns, volume analysis, L1–L5
depth, delta, and heatmap-based order-flow marking. Hard constraint established up
front and accepted:

- Spot XAUUSD/FX is decentralized OTC — **no consolidated order book exists**. True
  L2+ depth, real delta, footprint, and Bookmap-style liquidity heatmaps exist only on
  centralized exchanges (COMEX GC futures via paid feeds).
- Our MT5 bridge ships quote ticks and bar `tick_volume` (quote-update counts) only;
  no DOM capture; broker DOM for CFDs is absent or synthetic.
- Historical store is 5m bars; we hold zero tick history today.

**Decision (user):** build on **proxy data** — Dukascopy historical tick data (bid/ask
price + indicative bid/ask liquidity per tick), free, richer than our live feed. All
"order flow" quantities below are honest approximations, labeled as such.

**Decision (user):** staged delivery. **Stage 1** = interactive historical marking
tool (no ML, no trading). **Stage 2** (only if Stage 1 marks visibly line up with
price behavior) = ML research pass under the standard `backtest.md` 8-gate.

**Decision (user):** historical/interactive first; live marking deferred to a
possible post-Stage-2 follow-up.

**Approach chosen:** A — reusable pure-function feature library + Plotly Dash viewer
(the `kalman_sim.py` pattern), so Stage 2 consumes the same feature code unchanged.

## Stage 1 components

### 1. Tick fetcher — `scripts/fetch_dukascopy_ticks.py`

- Sibling of `scripts/fetch_dukascopy.py`, reusing its retry/LZMA-decode idiom.
- Downloads per-hour files `{SYMBOL}/{yyyy}/{mm-1:02d}/{dd:02d}/{HH}h_ticks.bi5`.
  Record `>IIIff`: offset-ms, ask-points, bid-points, ask-vol, bid-vol. Volumes are
  Dukascopy indicative liquidity — treated as weights, never as true traded size.
- Output: per-day Parquet `data/ticks/{SYMBOL}/YYYY-MM-DD.parquet` with columns
  `ts, bid, ask, bid_vol, ask_vol`. Parquet because busy gold days run 200–500k ticks.
  Days are immutable; re-fetch overwrites (idempotent).
- CLI: `--symbol XAUUSD --start ... --end ...`, threaded per-hour fetches, weekends
  skipped. Fetch on demand from the viewer; no bulk multi-year download.
- Does NOT touch the canonical 5m CSV pipeline or the weekly refresh job.

### 2. Feature library — `src/microstructure/features.py`

Pure functions: ticks in, arrays/events out. No state, no I/O, no ML. All thresholds
are kwargs with defaults (viewer exposes them as sliders). Loader helper
`load_ticks(symbol, start, end)` reads the Parquet range and derives `mid`, `spread`.

Core transforms:
- `sign_ticks(df)` — tick-rule: mid uptick = +1 (buyer-initiated), downtick = −1,
  unchanged inherits. Signed flow = sign × liquidity weight.
- `cumulative_delta(df)` — running signed-flow sum; resampleable to any bar interval.
- `volume_at_price(df, price_bin, time_bin)` — 2-D (time × price) liquidity-weighted
  activity histogram = the heatmap layer; collapsed profile → HVN/LVN nodes.

Event detectors (each returns `(timestamp, price, strength)` events = the marks):
- `delta_divergence(bars, delta)` — price new high/low unconfirmed by delta.
- `absorption_zones(df)` — heavy one-sided flow, mid pinned in a tight band.
- `imbalance_events(df, bars)` — per-bar per-price-bin flow ratio beyond threshold
  (footprint-style stacked imbalances).
- `sweep_events(df, swing_levels)` — tick-rate burst piercing a recent swing level
  and reversing within N seconds.
- `liquidity_withdrawal(df)` — spread widening beyond rolling percentile + quote-rate
  drop.

### 3. Viewer — `scripts/orderflow_viewer.py`

Plotly Dash app (launch: `python scripts/orderflow_viewer.py` → local browser).

- Main panel: XAUUSD candles (1m/5m/15m toggle, resampled from ticks); volume-at-price
  heatmap rendered behind candles (Plotly `Heatmap`, opacity-scaled); HVN/LVN lines;
  event marks overlaid — divergence flags, absorption shaded rectangles, imbalance
  cells, sweep arrows, withdrawal bands.
- Sub-panel: cumulative delta line + per-bar delta histogram, shared x-axis.
- Sidebar: date-range picker (auto-fetches missing Parquet days with progress note),
  per-mark-type checkboxes, threshold sliders wired to feature kwargs.
- Hover tooltips carry each mark's numbers (delta magnitude, flow ratio, spread pct).
- Data flow: range → `load_ticks()` → features (memoized on range+params, so slider
  tweaks re-run detectors only) → figure rebuild.
- Performance: vectorized pandas/numpy; ranges beyond ~2 weeks downsample heatmap
  time-bins instead of failing.

### 4. Testing & success criteria

- `tests/unit/test_microstructure_features.py`: synthetic tick frames per detector
  (e.g. constructed absorption pattern → exactly one zone); decode test for the tick
  fetcher against a small checked-in `.bi5` fixture.
- Stage 1 success = user's visual verdict scrubbing recent weeks. No trading wiring,
  no strategy registration — the CLAUDE.md strategy-propagation checklist does not
  apply.

## Stage 2 promotion gate (pre-agreed, separate spec later)

Only if Stage 1 marks look consistently meaningful: `features.py` → labels
(forward-return or triple-barrier) → gradient-boosted classifier → walk-forward
IS/OOS → strict-fill `backtest.md` 8-gate. Base-rate expectation is "no edge";
the marking tool retains standalone value regardless.

## Out of scope

- Paid futures depth data (COMEX GC via Databento etc.) — revisit only if Stage 2
  succeeds and wants real depth.
- Broker DOM probe (`MarketBookAdd` EA extension) — likely empty/synthetic; not worth
  a day unless the user asks.
- Live real-time marking — post-Stage-2 follow-up at most.
