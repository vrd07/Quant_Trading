# Liquidity-pool TP snapping — design

Date: 2026-08-07
Status: design approved, not implemented
Related: `docs/superpowers/specs/2026-07-30-liquidity-race-indicator-design.md`,
`reports/liquidity_race_calibration.md`

## Purpose

Use the liquidity-pool map built for `GoldenChart_Liquidity.mq5` to place take-profits
at real structure instead of at an arbitrary `rr × sl_dist` multiple.

When a strategy's take-profit lands just beyond a wall of resting orders, price
frequently reaches the pool, reverses, and never fills the target. This overlay moves
the take-profit to just short of such a pool, and does nothing else.

## What this is NOT

- **Not a new strategy.** No new entries, no new signals, no change to direction.
- **Not a stop-loss or sizing change.** Entries, stops and lot sizing are untouched, so
  every strategy's validated risk geometry survives intact.
- **Not a use of the race model's ranking.** See "Honest scope" below.

## Honest scope: what the model actually contributes

The calibrated model earns its keep on the *probability*, not the *ranking*. From
`reports/liquidity_race_calibration.md`:

- OOS log-loss 0.646 vs distance baseline 0.990 — a real win.
- OOS conditional top-1 **0.7165 model vs 0.7207 distance** — at picking which pool wins
  the race, the model is marginally *worse* than "the nearest one".
- Of 12 features, only four carry weight. Two of those (`atr_pctile` −2.16,
  `session_london` +0.99) are snapshot-constant and therefore cannot reorder levels
  within a snapshot at all. The two that vary (`n_closer_same_side` −4.62,
  `log_dist_atr` −0.96) are both monotone in distance.
- Pool *identity* adds essentially nothing over distance: `type_equal` β = +0.032,
  `type_session` β = +0.056.

**Consequence for this design: the level map does the work; the probability model is
nearly inert.** Candidate pools inside a ±25% band all sit at similar distances, so a
probability filter would almost never change which pool is selected.

This is deliberate. Variant 1 ships as pure geometry. A probability gate is a follow-up
to test only if the geometry shows signal — not a component to include now so the model
appears to be carrying weight it is not.

## Rule

For a signal with entry `E`, stop `SL`, take-profit `TP`, and ATR `A`:

1. Let `d = |TP − E|` be the current target distance.
2. Build the choice set at the current 15m bar. Keep only pools on the trade side
   (above `E` for BUY, below for SELL).
3. Keep pools whose distance from `E` lies in `[d × (1 − band_pct), d × (1 + band_pct)]`.
4. Pick the surviving pool **nearest to `E`** — the first wall price would encounter, not
   the one closest to the old target. When several pools sit in the band, the earliest is
   the one that can stop the move.
5. New target: `P − buffer_atr × A` for BUY, `P + buffer_atr × A` for SELL.
6. If no pool survives, leave `TP` unchanged.

```
entry ----------------------------- 100.0
current TP (rr 2.0) --------------- 106.6      band = [104.95, 108.25]

  pool at 106.1   inside band   -> TP moves to 106.0    RR 2.0 -> 1.82
  pool at 103.0   below band    -> TP unchanged
  pool at 112.0   above band    -> TP unchanged
```

The band is bounded on both sides, so the realized reward:risk is perturbed, never
collapsed: with `band_pct = 0.25`, an RR 2.0 trade lands in `[1.5, 2.5]` before the
buffer, and slightly inside that after it.

Note this makes the `min_rr = 1.2` guard a backstop rather than an active filter for the
three target strategies, which all run `rr: 2.0` — the band alone already floors them at
1.5. It binds only if the overlay is later pointed at a lower-RR configuration.

## Architecture

### Hook point

`RiskProcessor.calculate_stops()` (`src/risk/risk_processor.py:61`), as a final step
after the per-strategy branches have resolved `sl` and `tp`.

That single site reaches every path that matters:

| Path | Constructs RiskProcessor at |
|---|---|
| Live | `src/execution/execution_engine.py:81` |
| Backtest (ensemble) | `src/backtest/ensemble_engine.py:115` |
| Backtest (single) | `src/backtest/backtest_engine.py:135` |

One code path in research and production is what makes the A/B meaningful.

### Why not `execution_engine`

The backtest does not run BudgetSL at all — `ensemble_engine.py:363` calls
`calculate_stops()` and uses the result directly. An overlay hooked into
`execution_engine` would exist live and be invisible to every backtest.

### Bar access

New optional constructor keyword on `RiskProcessor`:

```python
def __init__(self, global_config, bar_provider: BarProvider | None = None)
```

`BarProvider = Callable[[str, int], pd.DataFrame | None]`, called as
`bar_provider(symbol_ticker, history_bars)`.

Default `None` leaves the overlay inert, so all six existing `RiskProcessor(...)` call
sites and their tests are unaffected.

**Provider contract:**
- Returns the most recent `history_bars` completed 15m bars, or `None`.
- Indexed by a timezone-aware UTC `DatetimeIndex`.
- Columns `open`, `high`, `low`, `close`, `volume` — the schema
  `scripts/research_liquidity_race.py::load_15m` produces.
- Returns `None` rather than a short frame if fewer than `history_bars` are available.

### Fixed bar count (correctness, not tuning)

`build_context()` computes ATR and EMA **recursively from index 0**, and `atr_pctile` is
a 500-bar rolling rank. The resulting pool set therefore depends on how many bars are
supplied. If the live path passes a different count than the backtest, the two silently
disagree and "same code, same result" stops being true.

`history_bars` is a fixed constant — **2500**, matching the MQL5 `InpHistoryBars`
default — applied identically in both paths. It is not a tunable.

### New module

`src/microstructure/tp_overlay.py`, exposing one pure function:

```python
snap_take_profit(entry, side, stop_loss, take_profit, bars_15m, atr, cfg)
    -> tuple[Decimal, str]      # (take_profit, reason)
```

No state, no I/O, no clock. Identical inputs always produce an identical target, which
keeps the hot path deterministic and lets the function be unit-tested standalone. It
builds a `FrameContext`, calls the existing `build_choice_set()`, and filters to the
trade side. `reason` is a short tag for the structured log (`snapped`, `no_pool`,
`below_min_rr`, `broker_min`, `no_bars`, `error`).

Frames are cached by `(symbol, last_bar_timestamp)` so a burst of signals inside one 15m
bar builds the context once.

### Guards

All non-negotiable. Any guard that trips leaves the take-profit exactly as the strategy
set it.

- **XAUUSD only.** The calibration is gold-15m and does not transfer to other symbols.
- Never move the target across `entry`.
- Never land inside `symbol.min_stops_distance`.
- Never reduce realized reward:risk below `min_rr` (default 1.2).
- Only pools strictly inside the band qualify; nothing outside it is ever chosen.
- **Fail open.** Missing bars, missing ATR, a malformed frame, or any exception returns
  the original take-profit and logs. The overlay can decline to act; it can never block,
  delay, or corrupt a trade.

## Configuration

New `risk.liquidity_tp_overlay` block in all 8 configs, disabled everywhere until the
A/B earns otherwise:

```yaml
risk:
  liquidity_tp_overlay:
    enabled: false
    band_pct: 0.25
    buffer_atr: 0.05
    min_rr: 1.2
    history_bars: 2500
    strategies: [squeeze_breakout, stoch_pullback, bos_structure]
    symbols: [XAUUSD]
```

### Strategy scope, and why kalman_regime is excluded

The three listed strategies carry `preserve_structural_sl`, so their take-profit comes
from the strategy and is used unchanged by both the live and backtest paths. Backtest
TP == live TP, and an A/B measures the thing that will actually run.

`kalman_regime` does not have that property. Its live target is
`rr × budget_sl_dist` (`execution_engine.py:341-347`) while its backtest target is
`tp_atr_multiplier × ATR` (`risk_processor.py:106`). A backtest A/B on kalman would
validate a take-profit it never uses live, producing a number that looks like evidence
and is not. It is excluded until that discrepancy is addressed on its own terms.

## Scope boundary

`tests/unit/test_liquidity_levels.py:1418-1426` currently asserts that nothing in
`src/strategies`, `src/risk` or `src/execution` references the liquidity modules.

This test is **narrowed, not deleted**. It will permit exactly one crossing —
`src/risk/risk_processor.py` importing `src.microstructure.tp_overlay` — and continue to
fail on every other reference, including any import of `liquidity_race`.

Naming the new module to slip past the existing substring check without amending the
test would satisfy the assertion while defeating its purpose. The boundary is being
crossed deliberately and the test should say so.

## Validation

Production engine, `--timeframe 15m --slippage strict`, overlay off vs on, per strategy,
at $25k and $50k, both risk-bypassed and `--enforce-risk`.

**Ship criterion:** improves **both 2025 and 2026**, per strategy — the standard already
set by the squeeze_breakout filters. A full-span-only improvement is rejected as
single-regime selection.

Each strategy is judged independently. Enabling it for one and not another is a valid
outcome.

### Expected statistical power

| Strategy | Trades (full span) | Expectation |
|---|---|---|
| `stoch_pullback` | 711 | readable |
| `squeeze_breakout` | 273 | readable |
| `bos_structure` | 125 | likely inconclusive, not negative |

The band rule should touch roughly a third of trades. On `bos_structure` that is ~40
affected trades, which will not separate from noise. An inconclusive result there means
"leave disabled", not "the idea failed".

## Testing

Unit tests target the guards directly, each as a distinct failure mode:

- pool on the wrong side of entry is rejected
- pool inside `min_stops_distance` is rejected
- snap that would breach `min_rr` is rejected
- band boundaries: pools just inside and just outside, both sides
- no qualifying pool → target returned unchanged
- malformed / short frame → target returned unchanged, `reason == "no_bars"`
- determinism: same inputs, same output across repeated calls
- non-XAUUSD symbol → overlay does not run

Plus an integration test asserting that `RiskProcessor` with `bar_provider=None`
produces byte-identical stops to the current implementation, which protects every
existing strategy from silent drift.

## Risks

| Risk | Mitigation |
|---|---|
| Live 15m history short of 2500 bars after restart (see `project_preload_timeout_starvation`) | Provider returns `None`; overlay no-ops and logs. Existing behaviour is the fallback. |
| Pool set drifts between live and backtest | Fixed `history_bars`, identical code path, determinism test. |
| Overlay adds latency to the signal path | Frame cached per 15m bar; `build_choice_set` is O(scan_bars) and signals are rare. |
| The improvement is real but small and single-regime | Both-years ship criterion. |
| Model appears to justify the design when geometry is doing the work | Stated explicitly above; probability gate deferred to a follow-up. |
