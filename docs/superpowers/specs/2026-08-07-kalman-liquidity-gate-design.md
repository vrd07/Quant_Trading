# Kalman adverse-liquidity veto gate — design

Date: 2026-08-07
Status: design approved, not implemented
Related: `docs/superpowers/specs/2026-08-07-liquidity-tp-overlay-design.md` (independent),
`docs/superpowers/specs/2026-07-30-liquidity-race-indicator-design.md`,
`reports/liquidity_race_calibration.md`

## Purpose

Use the liquidity-pool detection built for `GoldenChart_Liquidity.mq5` to suppress
kalman_regime entries that are about to be run over by a stop sweep.

## Hypothesis

> An un-swept liquidity pool on the **adverse** side of a kalman entry, close in ATR
> terms, predicts a stop-out at above the base rate — price runs the pool first, takes
> the stop, then does what kalman predicted.

"Adverse" is *below* entry for a BUY and *above* entry for a SELL: the side price is
drawn toward to sweep resting stops, which is the side the stop sits on.

In RANGE this is the classic failure of fading into resting liquidity — short at z=+2
with equal highs 0.4 ATR overhead, the sweep takes the stop, the reversion happens
without you. In TREND the same test applies but should behave differently, since a pool
*ahead* of the trade is a magnet rather than a hazard. The two modes therefore get
independent thresholds and are judged independently.

## Why this is not already refuted

`reports/liquidity_race_calibration.md` found pool *identity* carries almost no weight
(`type_equal` β=+0.032, `type_session` β=+0.056), which could be read as "pools don't
matter beyond distance". That reading over-applies the result.

**Every row in that dataset is a pool.** The model compared pools against other pools and
never against ordinary price levels, so it establishes only that a session high is no
more attractive than a swing high at equal distance. Whether a pool is more attractive
than an arbitrary price at equal distance — which is exactly what this gate assumes — is
untested by that calibration.

## Scope

- **kalman_regime only.** Other strategies are out of scope for this spec.
- **XAUUSD only**, inherited from kalman's existing in-code gold-only prefix gate, and
  required anyway because the pool detection is calibrated on gold 15m.
- **Detection only, no model.** `build_choice_set()` is pure geometry. The 12-feature
  vector, the baked coefficients and the probabilities are not used. A veto needs to know
  *where* the pools are, not how likely each is to be hit first.

That last point also drops the bar-history requirement from 2500 (needed for the 500-bar
ATR-percentile feature) to roughly 1100 — `scan_bars` 1000 plus pivot warmup.

## Mechanism

At signal time, with the resolved `side`, `entry`, and `current_atr`:

1. Build the choice set from the kalman bar window at the signal bar.
2. Keep pools on the adverse side only.
3. Let `d` be the distance from entry to the nearest such pool, in ATR units.
4. If `d <= adverse_pool_atr[mode]`, drop the signal and log the reason.
5. Otherwise pass the signal through unchanged.

```
RANGE SELL fires at z=+2.1, close 3402.0, ATR 6.0

pools above (adverse side):
  EQH   3404.4    dist 0.40 ATR    <- nearest
  ASIAH 3411.0    dist 1.50 ATR

adverse_pool_atr.range = 0.5
  0.40 <= 0.50  =>  SIGNAL DROPPED

nearest adverse pool beyond 0.5 ATR  =>  signal passes unchanged
```

### ATR units, not stop-distance units

The proximity test is expressed in ATR, deliberately. Kalman's backtest stop is
`sl_atr_multiplier × ATR` (`risk_processor.py:99-100`) while its live stop is rewritten
by BudgetSL off the operator's dollar budget (`execution_engine.py:298`). A gate phrased
in stop-distances would mean different things in research and production. ATR is
identical in both paths.

This is the same discrepancy that excluded kalman from the TP overlay spec. The entry
gate is immune to it; the TP overlay was not.

### Insertion point

A **single** gate in `on_bar()`, placed after side resolution and before the long-only
gate — i.e. after the `if side is None: return None` block at
`kalman_regime_strategy.py:566-573`, before line 576.

At that point both branches have resolved `side`, and `is_trend` is in scope to select
the per-mode threshold. One insertion site serves both modes.

Rejected alternative: adding a Layer 4 inside `_range_structural_ok` plus a duplicate
gate in the TREND branch. That method's stated job is "apply the enabled RANGE
confirmation layers"; a gate shared by both modes does not belong inside it, and
duplicating the logic across two sites invites drift.

### Statelessness

The gate is a pure function of the current bar window — no arming, no latch, no memory
between bars. It satisfies the "strategies must be stateless" constraint in CLAUDE.md
directly.

This is the main structural advantage over the sweep-then-enter variant, which would
require arming state and would change entry times and prices, forcing kalman's edge to be
re-established from scratch rather than compared.

### Causality

`build_choice_set(ctx, t)` is causal by construction: pivots are only scanned up to
`t - pivot_n` because an N-bar fractal cannot be confirmed until N bars later, and the
un-swept test only looks at bars in `(formation, t]`. No pool is visible to the gate
before the market could have known it.

## New module

`src/microstructure/pool_gate.py`, exposing one pure function:

```python
nearest_adverse_pool_atr(bars, entry, side, atr, params) -> float | None
```

Returns the ATR-normalised distance to the nearest adverse pool, or `None` when there is
no such pool or the frame is too short to build a choice set. No state, no I/O, no clock.

Kept separate from `tp_overlay.py` (the other spec) — different responsibility, different
consumer.

## Configuration

```yaml
strategies:
  kalman_regime:
    liquidity_gate:
      enabled: false
      adverse_pool_atr:
        range: 0.5
        trend: 0.5
      scan_bars: 1000
```

Added to all 8 configs, disabled everywhere until the A/B earns it. Thresholds shown are
placeholders pending the diagnostic — they are **selected on IS only**, see below.

**Fail-open.** Short history, missing ATR, a malformed frame, or any exception passes the
signal through unchanged. The gate may decline to veto; it may never veto on absent data.

## Validation

### Task 1 — diagnostic (the gate on everything else)

`scripts/research_kalman_liquidity.py`: instrument every kalman signal across the full
backtest span with the adverse-pool distance at signal time, then report win rate and
expectancy bucketed by that distance, split TREND/RANGE and BUY/SELL.

Outputs, all first-class:

- Win rate and mean R per adverse-distance bucket, per mode, per side.
- Base rate for comparison.
- **Surviving trade count per mode at each candidate threshold.**

Two stop conditions:

- **No effect.** If adverse-pool trades do not underperform the base rate, the gate is
  unbuildable and the work stops here. This is a real and likely outcome.
- **Too few survivors.** Kalman fires rarely and carries essentially all of the
  ConfluenceGate's signal flow. If a threshold with a real effect leaves a mode with
  fewer than ~80 trades over the full span, the result cannot separate from noise and
  that mode stays disabled regardless of how good the bucket table looks.

Report: `reports/kalman_liquidity_gate.md`.

### Threshold selection — the guard that matters most

`adverse_pool_atr` is chosen on **IS 2022-01-01..2025-12-31 only** and tested on
**2026**. The OOS slice is not consulted during selection.

Sweeping the threshold across all data and reporting the best cell is exactly the trap
recorded in `project_rsi_reversal_m1`: PF 1.67 with a 12/12 parameter plateau that
collapsed to PF 0.39 on a fresh holdout, because selecting on all data contaminates both
halves. Kalman has already produced three no-edge results under re-tuning; a contaminated
fourth would be worse than useless.

### Task 2 — A/B

`run_backtest --timeframe 15m --slippage strict`, gate off vs on, plus `--enforce-risk`
given kalman's documented kill-switch sensitivity.

**Ship criterion:** improves **both 2025 and 2026**. A full-span-only improvement is
rejected as single-regime selection. Each mode is judged independently — enabling the
gate for RANGE and not TREND is a valid outcome, and is the outcome the hypothesis
actually predicts.

## Scope boundary

`tests/unit/test_liquidity_levels.py:1418-1426` asserts nothing in `src/strategies`,
`src/risk` or `src/execution` imports the liquidity modules.

The test is **narrowed, not deleted**, to permit exactly one further crossing —
`src/strategies/kalman_regime_strategy.py` importing `src.microstructure.pool_gate` —
alongside the crossing the TP-overlay spec adds. Every other reference, including any
import of `liquidity_race`, continues to fail.

## Testing

- Adverse side is correct for BUY (below) and SELL (above) — a gate with the sides
  transposed would veto on the *target* side and would still produce a plausible-looking
  trade reduction, so this is tested explicitly in both directions.
- Pool exactly at the threshold vetoes; pool just beyond it passes.
- No adverse pool → signal passes.
- Short frame → signal passes, gate reports `None`.
- `enabled: false` → `on_bar` output is byte-identical to current behaviour across a
  fixture set of bars, protecting the shipped strategy from silent drift.
- Determinism: same window, same verdict, repeated calls.
- Per-mode thresholds are read independently (a RANGE-only configuration must not gate
  TREND).

## Risks

| Risk | Mitigation |
|---|---|
| Hypothesis is false — pools no more attractive than ordinary levels | Task 1 diagnostic is the gate; work stops there. |
| Threshold overfit to the full span | IS-only selection, 2026 held out. |
| Veto starves an already-thin trade count | Minimum-survivor stop condition in Task 1. |
| Kalman window shorter than `scan_bars` at runtime | Fail-open; diagnostic reports how often this fires. |
| Effect real in RANGE, absent in TREND, and a pooled result hides both | Modes measured and shipped independently. |
| Signal/outcome join picks up wall-clock rather than bar time | Diagnostic joins on bar timestamps only — see `project_squeeze_volume_filter_smelltest` for the `Signal.timestamp=now()` footgun. |
