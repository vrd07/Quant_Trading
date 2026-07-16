# Live Order-Flow Marking (Stage 1.5) — Design

**Date:** 2026-07-16
**Status:** Approved by user (all sections)
**Builds on:** `docs/superpowers/specs/2026-07-15-orderflow-marking-design.md` (Stage 1, shipped 2026-07-16, commits 6cc7303..fa2e00d)
**Symbol scope:** XAUUSD. Research-only; zero live-trading wiring.

## Goal

Real-time intraday signals: as each live candle closes, run the Stage-1 detectors
on today's data and surface new marks immediately in the viewer, plus two
"where are the buyers/sellers" liquidity layers, plus a bounded broker-DOM probe.

## Decisions (user)

1. **Data = hybrid.** Dukascopy publishes an hour's ticks only after the hour
   completes (1–2h lag), so: passive 1 Hz tap of the EA's `mt5_status.json` for
   the recent window (the `volatility_monitor` pattern — NEVER a second bridge
   connection) + Dukascopy backfill for published hours. Tap data has no
   liquidity weights and a constant sample rate.
2. **Delivery = LIVE mode in `orderflow_viewer.py`** (auto-refresh chart +
   scrolling signal feed). Alert stream (console/Telegram) deferred.
3. **Closed candles only** — a mark appears seconds after its bar closes and
   never repaints. Chart-level detector output may shift as day-percentiles
   evolve; the FEED is append-only (see SignalFeed).
4. **Order-location layers = all three**: defended-level map (evidence),
   inferred liquidity pools (heuristic), broker DOM probe (bounded probe;
   a real DOM layer is designed only if the probe proves real depth exists).
   Honesty rule: spot gold has no consolidated order book; real resting-order
   data exists only on COMEX futures feeds. The first two layers are proxies
   and are labeled as such in the UI.

## Architecture

Three units, one-way deps: `live_feed.py` (I/O + state) → `live_marks.py`
(pure engine) → viewer LIVE mode (UI). `features.py` stays untouched and pure.

### 1. `src/microstructure/live_feed.py` — tap + stitcher

- **`StatusTap`** (thread): polls `mt5_status.json` once per second
  (mtime-checked, read-only). On change, appends
  `(ts_utc, bid, ask, bid_vol=0.5, ask_vol=0.5)` to an in-memory list; spills
  to `data/ticks_live/{SYMBOL}/{YYYY-MM-DD}.csv` (append mode) every ~60 s.
  Crash/restart re-reads the CSV. Flat quotes are recorded (tick rule handles
  them). Broker symbol `XAUUSDs` maps to `XAUUSD`. Start is idempotent.
- **Backfill + stitcher**: `ensure_ticks` must NOT be called for the current
  day (it would cache a partial day Parquet in the immutable `data/ticks/`
  store that never self-heals). Instead a `DukaBackfill` fetches completed
  hours individually via `fetch_hour`, caches non-empty hours as per-hour
  Parquets under `data/ticks_live/`, and retries unpublished hours at most
  every ~10 min. `stitch_day(duka, tap)` then finds the last published
  timestamp and appends tap rows strictly after it.
  Overwrite `bid_vol/ask_vol = 0.5` across the WHOLE merged frame
  (count-weighted flow everywhere — no scale cliff at the stitch boundary).
  Output = `load_ticks` shape (mid/spread derived) so detectors run unchanged.
- Startup mid-day: the gap between last published hour and viewer launch stays
  unmarked until Dukascopy publishes it (~2 h self-heal). Accepted.

### 2. `src/microstructure/live_marks.py` — pure engine

- **`closed_candle_events(df, timeframe, params, now) -> list[FlowEvent]`**:
  run the 5 detectors, drop events whose bar hasn't closed
  (`bar_start + timeframe <= now`). In the tap-covered window only
  divergence / absorption / imbalance can fire (1 Hz sampling gives constant
  arrival rate → sweep burst leg and withdrawal rate leg are inert there);
  sweeps/withdrawals firm up when hours backfill. Accepted degradation.
- **`SignalFeed`**: append-only log, dedup key `(kind, bar_ts, price_bin)`.
  New events get an `emitted_at` stamp and are persisted to
  `data/ticks_live/{SYMBOL}/{day}_signals.jsonl`. Never rewritten — this is
  the paper-trail for judging live usefulness (and any Stage-2 labeling).
- **Defended-level map** (evidence layer):
  `defended_levels(events, band_pts) -> list[(price, side, touches, last_ts)]`
  — clusters today's absorption events by price band; re-defense increments
  `touches`. Render: horizontal bands, opacity ∝ touches; green = absorbed
  selling (buyers defending), red = absorbed buying.
- **Inferred liquidity pools** (heuristic layer):
  `liquidity_pools(bars, swing_bars, eq_tol_pts, round_step)
  -> list[(price, side, kind)]` — confirmed un-swept swing highs/lows,
  equal-high/low clusters (within `eq_tol_pts`), round numbers (`round_step`,
  default 5.0 pts) near price. Render: dashed lines labeled buy-side /
  sell-side liquidity. UI labels it "inferred", not measured.
- All thresholds kwargs → sliders, as in Stage 1.

### 3. Viewer LIVE mode (`scripts/orderflow_viewer.py`)

- Mode switch HISTORY / LIVE. LIVE: starts `StatusTap` (idempotent), locks
  date range to today, enables `dcc.Interval` at 20 s.
- Per interval tick: `build_live_day` → `closed_candle_events` → existing
  `build_figure` + the two new layers → feed panel (scrolling table, newest
  first: `emitted_at | bar | kind | price | strength`, colored by side).
- Status strip: tap freshness (red when `mt5_status.json` stale — e.g. MT5
  closed), last backfilled hour, tick and signal counts today.
- Degradation is visible, never silent: stale tap → red badge, backfill keeps
  working; Dukascopy failure → tap segment extends further back. No crashes.

### 4. Broker DOM probe (bounded, separate deliverable)

- `mt5_bridge/EA_DOMProbe.mq5`: read-only EA — `MarketBookAdd(Symbol())`,
  `OnBookEvent` writes book snapshots (type/price/volume per level) +
  heartbeat to its own file `mt5_dom_probe.json` in Common Files. Sends NO
  commands — cannot race the bot's file bridge.
- `scripts/check_dom_probe.py`: reads the file for ~1 minute, prints verdict:
  `NO BOOK` / `TOP-OF-BOOK ONLY (synthetic)` / `REAL DEPTH (n levels,
  changing volumes)`.
- **Decision gate:** only `REAL DEPTH` triggers a follow-up design for a DOM
  heatmap layer. Other verdicts close the resting-order question permanently.
- User action needed once: compile EA_DOMProbe.mq5 and attach it to the
  `XAUUSDs` chart (bot's EA_FileBridge stays where it is).

## Testing

Unit (synthetic frames, no network/MT5):
- Stitcher: cutover exactly at the published-hour boundary; weight
  normalization applied to both segments; restart re-reads spill CSV.
- `closed_candle_events`: event on a forming bar excluded, included once
  `now` passes the bar close.
- `SignalFeed`: dedup by `(kind, bar_ts, price_bin)`; append-only across
  refreshes; jsonl persistence round-trip.
- `defended_levels`: absorption events within `band_pts` cluster; `touches`
  increments on re-defense.
- `liquidity_pools`: swing/equal/round detection incl. "swept level drops out".
Smoke (headless): tap thread start/stop idempotence against a temp status
file; LIVE-mode server boot. DOM probe verified live by the user (only the
real broker can answer it).

## Out of scope

- Alert stream (console/Telegram) — thin follow-up on the same engine if asked.
- DOM heatmap layer — gated on the probe's `REAL DEPTH` verdict.
- Any change to `features.py`, the live bot, configs, or the bridge protocol.
- Stage-2 ML — still gated separately on accumulated evidence (the signal
  feed jsonl now doubles as its future label source).
