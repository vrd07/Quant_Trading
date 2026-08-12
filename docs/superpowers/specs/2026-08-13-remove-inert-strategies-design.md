# Remove Inert Strategies — Design

**Date:** 2026-08-13
**Status:** Approved (design), pending implementation plan
**Branch:** `chore/remove-inert-strategies`

## Problem

Sixteen strategies are registered. One trades.

Live journal evidence (deduped by `mt5_ticket` across all
`data/logs/trade_journal_config_live_*.csv`):

| Metric | Value |
|---|---|
| Bot trades, all configs | 123 |
| Distinct strategies in the trade log | 1 (`kalman_regime`) |
| Signal-context entries | 93 — kalman 87, `stoch_pullback` 3, `london_breakout` 3 |

Six of the sixteen (`vwap`, `momentum`, `sbr`, `asia_range_fade`, `smc_ob`,
`fibonacci_retracement`) exist **only** as confluence legs. `ConfluenceGate`
requires them to co-fire within a rolling window to emit COMBO A / COMBO B /
`combo_sniper`. That has never happened. A prior investigation
(`project_confluence_window_widen`) widened the window 25 → 120 minutes and
still produced zero combos; the bottleneck is that the primaries themselves are
rare. These six cannot reach the risk engine under any config currently in the
repo.

Two more are already `enabled: false` with published refutations:
`wavelet_cycle` (premise falsified by a scale-invariance test with a passing
positive control) and `ema200_nasdaq` (0 of 96 grid cells cleared the
every-year gate).

The cost of keeping them is not CPU. It is that every config edit, every
regime-weight table, every session whitelist, and every reader of `CLAUDE.md`
pays attention tax on code that cannot trade.

## Scope

**In scope:** deleting eight strategies that provably cannot affect a live
trade, and collapsing `ConfluenceGate` to the part of it that still does work.

**Explicitly out of scope:** any change to what currently trades. This is a
cleanup, not a portfolio decision. Strategies that fire (or could fire) stay —
including ones shipped over their own research verdict (`london_breakout`,
`stoch_pullback`, `monday_drift`). Whether those deserve to trade is a separate
question with a separate answer.

## Success criterion

**Live behaviour must be byte-identical after this change.** The verification
below is designed to prove that, not to assume it. If the before/after backtest
diverges by a single trade, the premise was wrong and the change is reverted.

## Design

### 1. Delete eight strategies

| Strategy | File | Grounds |
|---|---|---|
| `vwap` | `src/strategies/vwap_strategy.py` | COMBO B primary; gate never opened |
| `momentum` | `src/strategies/momentum_strategy.py` | filter-only leg |
| `sbr` | `src/strategies/structure_break_retest.py` | COMBO A primary; gate never opened |
| `asia_range_fade` | `src/strategies/asia_range_fade_strategy.py` | filter-only leg |
| `smc_ob` | `src/strategies/smc_ob_strategy.py` | filter-only leg |
| `fibonacci_retracement` | `src/strategies/fibonacci_retracement_strategy.py` | filter-only leg |
| `wavelet_cycle` | `src/strategies/wavelet_cycle_strategy.py` | premise falsified; weight 0.00; disabled |
| `ema200_nasdaq` | `src/strategies/ema200_nasdaq_strategy.py` | 0/96 variants; disabled |

`src/strategies/regime_filter.py` is imported only by `vwap_strategy.py` and is
deleted with it. `src/strategies/multi_timeframe_filter.py` has no importers and
is deleted as well.

### 2. Replace `ConfluenceGate` with `StrategyAllowlist`

`ConfluenceGate` is wired into both hot paths:

- live: `src/main.py:44` (import), `:407` (construct), `:905` (filter)
- backtest: `src/backtest/ensemble_engine.py:47`, `:111`, `:356`

It does two separable jobs:

1. **Combo assembly** — COMBO A/B/C, `combo_sniper` at 1.5× lots, the
   exhaustion-divergence confidence modifier, and the rolling per-symbol signal
   window. Every input to this is being deleted, so all of it becomes
   unreachable.
2. **Safety allowlist** — `SOLO_ALLOWED` gates which strategies may fire without
   confluence; `KILL_LIST` drops signals from previously-deleted strategies so a
   stale config cannot crash the registry lookup.

Job 2 is retained because configs are hot-swapped between eight files day by
day; it is the only place that guarantees an unvetted strategy name cannot
reach the risk engine.

New module `src/strategies/strategy_allowlist.py` (~30 lines):

```python
SOLO_ALLOWED = frozenset({
    "kalman_regime", "squeeze_breakout", "stoch_pullback", "bos_structure",
    "london_breakout", "monday_drift", "index_overnight", "wednesday_drift",
})

KILL_LIST = frozenset({
    # deleted 2026-06-10
    "breakout", "mean_reversion", "supply_demand",
    "descending_channel_breakout", "mini_medallion", "continuation_breakout",
    # deleted 2026-08-13
    "vwap", "momentum", "sbr", "asia_range_fade", "smc_ob",
    "fibonacci_retracement", "wavelet_cycle", "ema200_nasdaq",
})
```

`filter()` drops anything in `KILL_LIST`, drops anything not in `SOLO_ALLOWED`
(logging once per name), and returns the rest untouched. No windowing, no
lot multipliers, no regime argument.

Both call sites keep a single `.filter(...)` line. The surrounding
exhaustion-timeframe preload blocks (`main.py:896-899`,
`ensemble_engine.py:240-241,345-347`) are deleted with the feature.

`SOLO_ALLOWED` is verified to already contain all eight survivors
(`confluence_gate.py:57`), which is why removing the combo path cannot change
today's behaviour.

### 3. Propagation

Per the checklist in `CLAUDE.md`, all of the following move together. A partial
deletion fails `test_weights_table_completeness` or silently drops a strategy at
runtime.

| Target | Change |
|---|---|
| `src/strategies/strategy_manager.py` | Remove 8 imports + registry entries |
| `src/main.py` | Swap gate import/construction/call; remove exhaustion preload |
| `src/backtest/ensemble_engine.py` | Same three edits |
| `scripts/regime_classifier.py` | Remove 8 keys from `STRATEGY_WEIGHTS` (line 253) in **all three** regimes — keys must stay identical across `TREND`/`RANGE`/`VOLATILE` |
| `tests/unit/test_regime_classifier.py:294` | Update `required_core` |
| `config/config_live{,_100,_1000,_5000,_10000,_25000,_50000,_100000}.yaml` | Remove `strategies.<name>` blocks, `strategies.confluence_gate` block, every `trading_hours.sessions[].strategies` whitelist entry, and the orphaned `symbols.NAS100` block (its only consumer was `ema200_nasdaq`) |
| `data/config_override*.json` (23 files) | Regenerate via `regime_classifier.py` |
| `tests/unit/` | Delete `test_sbr_strategy.py`, `test_wavelet_cycle_strategy.py`, `test_ema200_nasdaq.py`, `test_confluence_gate.py`; add `test_strategy_allowlist.py` |
| `src/strategies/symbol_reconciler.py` | Drop `ema200_nasdaq` from `_STRATEGY_SYMBOLS` (line 29) and from `_FIRE_WEEKDAY` if present. It is the only doomed name in either map — the other seven entries are all survivors |
| `CLAUDE.md` | Collapse rows 2–7, 15, 16 to a single "deleted 2026-08-13" note; keep every rejection notice |

### 4. Deliberately kept

- **`src/cycles/`** (9 modules). No live importer after `wavelet_cycle` goes,
  but `scripts/research_wavelet_*.py` and the parked P1 follow-ups in
  `docs/wavelet-cycle-followups.md` depend on it — including the unfixed
  `spectral.dominant_cycle` blindness at `window >= 128`, which is a live trap
  for any future cycle work. Becomes research-only, the same status as
  `opening_range_breakout_strategy.py`.
- **All `reports/` and `scripts/research_*.py`.** The negative results are the
  most valuable artifact in this repo. `config/backtest_smc.yaml` stays as a
  research config.
- **Every rejection notice in `CLAUDE.md`.** Rewritten to say the code was
  deleted and git history holds it. Deleting the *warning* is how a dead end
  gets re-researched in six months.

### 5. Verification

Both must pass before the branch is offered for review:

1. **`pytest`** — currently 971 passed / 26 skipped. Expect a lower total (four
   test files removed, one added) and **zero failures**.
2. **Backtest equivalence** — `python scripts/run_backtest.py --symbols XAUUSD
   --timeframe 15m` on the merge-base commit and on the branch, over the same
   span. The engine is deterministic (no RNG; `run_backtest.py` exposes no seed
   flag), so a like-for-like run must reproduce exactly. **Trade lists must be
   identical.** This is the load-bearing check: it converts "these were inert"
   from a claim into a measurement.

If (2) diverges, the change is wrong regardless of how clean the diff looks —
something in the deleted set was reachable, and the branch is discarded rather
than patched.

### 6. Risks

| Risk | Mitigation |
|---|---|
| A deleted strategy was reachable via a config not exercised by the backtest | Verification (2) runs on XAUUSD, the only symbol with live flow; the six legs are symbol-agnostic so gate behaviour is symbol-independent |
| Stale config on next hot-swap references a deleted name | `KILL_LIST` absorbs it — the same net used for the 2026-06-10 deletion |
| `regime_classifier.py` nightly job writes back a removed key | Keys removed from `STRATEGY_WEIGHTS` in all 3 regimes; overrides regenerated in the same change |
| Losing the research record | Reports, research scripts, and `CLAUDE.md` rejection notices all retained by design |

## What this does not fix

After this change the bot has eight registered strategies and, on current
evidence, one that trades. The complexity is lower; the edge is unchanged.
The open question — whether `kalman_regime`'s live PF 1.81 on n=74 system-exits
is real, and whether the seven non-firing strategies deserve their slots — is
deliberately left for a separate decision.
