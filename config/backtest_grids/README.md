# Per-Strategy Parameter Grids

Search-space definitions consumed by the auto-retune driver in `scripts/run_backtest.py`. One file per strategy, file name = `strategies.<key>` from `config_live_*.yaml`.

> **Spec authority.** `backtest.md` §5.2 (grid cap), §7 (3-tier auto-retune), §11 (open questions). If this README and `backtest.md` disagree, `backtest.md` wins.

---

## Schema

```yaml
strategy: <key as in config_live_*.yaml strategies block>
file: src/strategies/<strategy_file>.py
description: <one-line>
notes: |
  <multi-line context: why these ranges, what's known about sensitivity>

# Production values as of <date> (config_live_10000.yaml). Tier 1 grid centers here.
anchor:
  param_a: <value>

# Tier 1 — entry-logic params (cartesian product, capped at max_combos.tier1)
# Tried FIRST during optimization. If OOS fails, driver auto-expands ranges per §7.1.
tier1_entry:
  param_a: [v1, v2, v3]
  param_b: [v1, v2]

# Tier 2 — risk knobs (one-dimension sweep — vary one at a time, others held at anchor)
# Tried SECOND if tier1 fails. Cheap and high-leverage on PF/DD.
tier2_risk:
  atr_stop_multiplier: [...]
  rr_ratio: [...]
  cooldown_bars: [...]

# Tier 3 — structural filters / regime / session gates (cartesian, usually small)
# Tried THIRD as a last resort before disabling. Structural changes, not parameter.
tier3_filters:
  long_only: [false, true]
  regime_preset: [trend_only, all]
  session_preset: [current, london_ny]

max_combos:
  tier1: 200
  tier2: 30
  tier3: 50
```

## Tier semantics (matches `backtest.md` §7)

| Tier | Type | When tried | Cap | What it varies |
|------|------|------------|-----|----------------|
| 1 | Entry-logic | First, on every walk-forward window | 200 | Lookbacks, thresholds, signal-strength gates |
| 2 | Risk knobs | If tier1 OOS fails | 30 (one-dim) | SL/TP multipliers, trail, cooldown |
| 3 | Structural filters | If tier2 OOS fails | 50 | Regime gate, session gate, long_only, MTF/EMA confluence flags |

If all three tiers fail OOS, strategy is auto-disabled (§7).

## Driver expectations

- **Tier 1 expansion (§7 step 1).** When the driver "expands grid by 2×", it adds intermediate values inside the *existing* ranges declared here — it does NOT widen ranges beyond what's listed. Widening ranges is a research decision; the grid file is the contract.
- **Tier 2 one-dim sweep.** For each param `p` in `tier2_risk`, hold all *other* tier2 params at `anchor`, vary only `p`. Total runs = sum of len(values) across keys, NOT cartesian.
- **Tier 3 cartesian.** Cartesian product, but enforce `max_combos.tier3` by random-sampling if exceeded.
- **Anchor preservation.** During tier 2 and tier 3, *tier 1 entry params stay at the IS-winner from tier 1*, not at `anchor`. (Anchor is only the starting point.)

## Shared presets

To avoid repeating long lists across files, these named presets are resolved by the driver:

### `regime_preset`
| Preset | Resolves to |
|--------|-------------|
| `trend_only` | `only_in_regime: TREND` |
| `range_only` | `only_in_regime: RANGE` |
| `volatile_only` | `only_in_regime: VOLATILE` |
| `trend_volatile` | `enabled_regimes: [TREND, VOLATILE]` |
| `trend_range` | `enabled_regimes: [TREND, RANGE]` |
| `all` | `only_in_regime: null` |

### `session_preset`
| Preset | UTC hours |
|--------|-----------|
| `all` | 0–23 |
| `asia` | 0–7 |
| `london` | 7–12 |
| `overlap` | 12–16 |
| `ny` | 16–22 |
| `london_ny` | 7–22 |
| `current` | whatever's in `anchor.session_hours` |

## Adding a new strategy

1. Drop a `<strategy_key>.yaml` in this directory using the schema above.
2. Add its `STRATEGY_WEIGHTS` entry (`scripts/regime_classifier.py:243`).
3. Wire it into every `config_live_*.yaml` (see `CLAUDE.md` §"Propagating Strategy Changes").
4. The first backtest run picks it up automatically.

## Stress days

Every grid winner is also reported against the §5.5 stress-day list (Russia–Ukraine, gilt blow-up, SVB, yen unwind, US elections, FOMC/NFP). These do NOT veto a strategy; they're diagnostic.
