# Squeeze-Breakout Volume Filter — Smell-Test Design

**Date:** 2026-07-21
**Status:** Approved by user (design sections)
**Symbol scope:** XAUUSD. Research-only; ZERO live-trading wiring.
**Depends on:** `src/strategies/squeeze_breakout_strategy.py` (the strategy being
filtered), `src/microstructure/forward_returns.py` (labeling precedent), the
free-data verdict below.

## Motivation

The order-flow marking tool's 10-week validation (2026-07-20) showed three of
four proxy detectors carried no forward edge, and the DOM probe returned a
synthetic (perfectly mirrored) book — spot XAUUSD has no real volume tape.
Root cause: Dukascopy `bid_vol`/`ask_vol` is an indicative liquidity weight,
constant on ~75% of ticks, so every "volume" detector was a tick-count detector
in disguise.

Real traded volume for gold exists only on **COMEX GC futures**. Rather than
build another standalone signal (the pattern that has produced only nulls for
two months — EURUSD, GBPUSD, crypto, Fourier, EMA-retest, daily-swing,
order-flow marks), this project applies GC volume as a **confirmation filter on
an already-validated edge**: `squeeze_breakout`. Every large win in this repo
came from filtering an existing edge (HTF gate PF 1.21→1.44, loser filters,
stoch EMA-distance filter), never from a new standalone signal.

`squeeze_breakout`'s known bleed is "fakeout" breaks — shallow, low-participation
expansions that mean-revert. Its SELL side still loses net −$1,984 full-span.
A volume-participation filter targets exactly that failure mode with information
the strategy does not currently have.

## Hypothesis (user-selected: "coil dry-up + break surge")

The textbook squeeze volume signature:

- **Coil phase** — volume contracts (a *real* compression dries up).
- **Break bar** — volume surges (real participation drives the expansion).

A confirmed squeeze has both; a fakeout breaks on no surge. Two causal
relative-volume features per breakout:

- **`coil_rvol`** = mean GC hourly volume across the coil window ÷ a longer
  trailing baseline. Real dry-up ⇒ **< 1**. Causal: the coil precedes the break.
- **`break_rvol`** = volume of the last *completed* GC hour at/before the break
  ÷ its trailing average. Real participation ⇒ **> 1**. **Causal guard:** never
  use the break bar's own in-progress GC hour — always the prior completed hour.

If the filter survives to shipping (a later spec), the gate is
`break_rvol ≥ threshold` (optionally also `coil_rvol ≤ threshold`).

## Goal of THIS project (a spend decision, not a trade decision)

The free GC/XAUUSD overlap is ~40 trading days (local XAUUSD ticks from
2026-05-01; free GC 1h volume from ~2026-05-08), and `squeeze_breakout` fires
~0.3×/day post-gate, so this smell-test sees only **~12–18 trades**. That
cannot prove edge and MUST NOT justify any live change.

The single question it answers: **does a clean directional split exist, worth
paying for multi-year GC data to test at scale?**

- **GREEN (justifies data spend):** high-`break_rvol` breaks visibly out-R the
  low ones, and/or the SELL-side bleed concentrates in low-volume breaks.
- **RED (drop it):** the split is flat or inverted.

Explicitly stated in the report: ~15 trades can split cleanly by chance, so
GREEN justifies *spend*, never *ship*.

## Approach (selected: standalone research script)

`scripts/research_squeeze_volume.py` — a pure research script. **Touches zero
production code.** Mirrors the `forward_returns.py` precedent, and ingests paid
multi-year GC data unchanged when it arrives.

Rejected alternatives:
- Extend the backtest engine — the harness wants full-history CSV; a 40-day
  volume window can't A/B honestly, and it means touching production for a
  smell-test.
- Wire the filter into `squeeze_breakout_strategy.py` now — that is the eventual
  *ship* form; building it before evidence repeats the order-flow build-first
  mistake. It becomes a separate spec only if this smell-test is GREEN.

## Architecture

Three units, one-way deps: data loaders → alignment/feature engine (pure) →
report. `squeeze_breakout_strategy.py` and `forward_returns.py` are imported
read-only and unchanged.

### 1. Signal reconstruction
Instantiate `SqueezeBreakoutStrategy` over 15m XAUUSD bars (resampled from local
Dukascopy ticks, 2026-05-01+). Walk a sliding window calling `on_bar`, collecting
each emitted signal's `{ts, side, entry_price, stop, target}`. Reusing the class
(not reimplementing) guarantees no signal drift from the live strategy.

### 2. GC volume loader (`load_gc_hourly`)
`yfinance GC=F` at 1h. **Hourly only** — yfinance GC *daily* volume is broken
(inconsistent with its own hourly). Returned as a UTC-indexed hourly volume
series. Cached to a parquet under `data/gc_futures/` so reruns are offline.

### 3. Feature engine (pure — `coil_rvol`, `break_rvol`)
Given the hourly GC series and a breakout timestamp, compute the two features
with the causal prior-completed-hour rule. Pure function of (series, ts, window
params) — unit-testable in isolation, no I/O.

### 4. Outcome labeler
Each reconstructed breakout is labeled with `squeeze_breakout`'s **native**
geometry — fixed 33pt SL / 66pt TP (RR2.0) — walked forward over the XAUUSD mid
path, so the R matches the real strategy (not the generic triple-barrier). Reuse
the path-walking helper from `forward_returns.py` where it fits.

### 5. Report (`reports/squeeze_volume_smell_test.md`)
- Split table: win% and mean R for high vs low `break_rvol` (and `coil_rvol`)
  buckets, with n per cell. Bucket boundary is the **median of the observed
  feature across the reconstructed trades** (a data-driven split, not a
  hand-picked threshold — with ~15 trades any fixed cutoff would be arbitrary).
- SELL-only cut (the bleed).
- One-line GREEN/RED verdict + the "~15 trades can split by chance" caveat.

## Data caveats (printed in the report, not buried)

1. GC is COMEX futures, not spot XAUUSD — different instrument, ~23h vs 24h
   session, a maintenance break to align around. Volume is used only as a
   *relative percentile*, so absolute miscalibration matters less.
2. yfinance GC *daily* volume is broken — hourly only.
3. 1h volume is coarser than the 15m break; the prior-completed-hour proxy is a
   deliberate, causal approximation, lagged by up to one hour.

## Testing

- `coil_rvol` / `break_rvol` computation on a hand-built hourly series.
- **Causal guard test:** a case where using the break's own hour would flip the
  verdict — the test asserts the prior completed hour is used and fails if it
  ever peeks ahead.
- Volume-bucket split stats on a synthetic set of labeled trades.

## Out of scope

- Any change to `squeeze_breakout_strategy.py`, configs, or the risk engine.
- Buying data — that is the *next* spec, gated on a GREEN verdict here.
- Signed volume / delta and the RVOL-only and volume-direction hypotheses —
  deferred; this project tests the coil-dry-up + break-surge hypothesis only.

## Next steps by verdict

- **GREEN** → spec 2: procure multi-year GC intraday data, re-run at scale
  through the full every-year gate.
- **GREEN at scale** → spec 3: wire the filter into `squeeze_breakout_strategy.py`
  behind a disabled-by-default config flag, A/B through the production engine.
- **RED** → stop; record the null in memory.
