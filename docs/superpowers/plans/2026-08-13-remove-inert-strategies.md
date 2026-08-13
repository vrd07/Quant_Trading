# Remove Inert Strategies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete eight strategies that provably cannot reach the risk engine, and collapse `ConfluenceGate` to the ~30-line allowlist that is the only part of it still doing work — with zero change to live behaviour.

**Architecture:** Six of the eight (`vwap`, `momentum`, `sbr`, `asia_range_fade`, `smc_ob`, `fibonacci_retracement`) exist only as `ConfluenceGate` combo legs, and no combo has ever formed. Two (`wavelet_cycle`, `ema200_nasdaq`) are already `enabled: false` with published refutations. Removing the six removes every input to the combo machinery, so COMBO A/B/C, `combo_sniper`, and the exhaustion modifier all become unreachable and are deleted with them. The `SOLO_ALLOWED` / `KILL_LIST` safety net is extracted into a new `StrategyAllowlist`.

**Tech Stack:** Python 3, pytest, PyYAML. Repo venv at `./venv/bin/python`. Spec: `docs/superpowers/specs/2026-08-13-remove-inert-strategies-design.md`.

## Global Constraints

- Branch is `chore/remove-inert-strategies`, already created, spec committed at `34839c0`.
- **Live behaviour must be byte-identical.** The Task 7 backtest trade-list diff is the gate. If it diverges, discard the branch — do not patch it.
- All eight survivors (`kalman_regime`, `squeeze_breakout`, `stoch_pullback`, `bos_structure`, `london_breakout`, `monday_drift`, `index_overnight`, `wednesday_drift`) are already in `SOLO_ALLOWED` at `confluence_gate.py:57`. This is why the change is behaviour-preserving.
- All 8 live configs have `strategies.confluence_gate.enabled: true` (verified). `StrategyAllowlist` therefore takes **no enable flag** — it is unconditional. A disable flag on a safety net is a foot-gun.
- Do **not** delete anything under `reports/`, `scripts/research_*.py`, or `src/cycles/`. Do **not** remove rejection notices from `CLAUDE.md` — rewrite them to say the code was deleted.
- The 8 `config/config_live*.yaml` files are already dirty with an unrelated one-line news-CSV date rollover (`2026-08-12` → `2026-08-13`). Leave those hunks alone; do not stage them.
- Use `./venv/bin/python` and `./venv/bin/pytest` — the system python has no `yaml`.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

### Task 1: Capture the baseline

No code changes. This produces the artifact Task 7 compares against, and it must run on the pre-change tree.

**Files:**
- Create: `/tmp/baseline/pytest_before.txt`, `/tmp/baseline/backtest_before.txt`

- [ ] **Step 1: Confirm the tree is at the pre-change state**

```bash
cd /Users/varadbandekar/Documents/Quant_trading
git branch --show-current   # expect: chore/remove-inert-strategies
git log --oneline -1        # expect: 34839c0 docs(spec): ...
```

- [ ] **Step 2: Record the test baseline**

```bash
mkdir -p /tmp/baseline
./venv/bin/pytest -q 2>&1 | tail -20 | tee /tmp/baseline/pytest_before.txt
```

Expected: a summary line near `971 passed, 26 skipped`. Record the exact numbers — Task 7 compares against them.

- [ ] **Step 3: Record the backtest baseline**

```bash
./venv/bin/python scripts/run_backtest.py --symbols XAUUSD --timeframe 15m \
  2>&1 | tee /tmp/baseline/backtest_before.txt
tail -40 /tmp/baseline/backtest_before.txt
```

Expected: a completed run with a trade count and summary metrics. If it errors, **stop and report** — a broken baseline makes the whole verification meaningless.

- [ ] **Step 4: Note the exact trade count and net PnL**

Read them out of `/tmp/baseline/backtest_before.txt` and write them into the task notes. Task 7 needs both numbers.

No commit — these are throwaway artifacts outside the repo.

---

### Task 2: Create `StrategyAllowlist`

**Files:**
- Create: `src/strategies/strategy_allowlist.py`
- Test: `tests/unit/test_strategy_allowlist.py`

**Interfaces:**
- Consumes: `Signal` from `src.core.types`, `get_logger` from `src.monitoring.logger` (returns a `TradingLogger` with `.info()`).
- Produces: `StrategyAllowlist(config: dict | None = None)` with `.filter(symbol: str, signals: Iterable[Tuple[str, Signal]]) -> List[Signal]`, plus module-level `SOLO_ALLOWED: frozenset` and `KILL_LIST: frozenset`. Tasks 3 and 4 depend on these exact names.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_strategy_allowlist.py`:

```python
"""StrategyAllowlist — the safety net extracted from ConfluenceGate."""
import pytest

from src.core.types import Signal
from src.core.constants import OrderSide
from src.strategies.strategy_allowlist import (
    StrategyAllowlist,
    SOLO_ALLOWED,
    KILL_LIST,
)


def _sig(strategy_name: str) -> Signal:
    # Every Signal field has a default; strategy_name and side are all the
    # allowlist reads. Note the strength field is `strength`, not `confidence`.
    return Signal(
        strategy_name=strategy_name,
        side=OrderSide.BUY,
        strength=0.7,
    )


class TestStrategyAllowlist:
    def test_allowlisted_strategy_passes_through(self):
        gate = StrategyAllowlist()
        sig = _sig("kalman_regime")
        assert gate.filter("XAUUSD", [("kalman_regime", sig)]) == [sig]

    def test_kill_list_strategy_is_dropped(self):
        gate = StrategyAllowlist()
        assert gate.filter("XAUUSD", [("vwap", _sig("vwap"))]) == []

    def test_unknown_strategy_is_dropped(self):
        """Not allowlisted == not executable. Default deny."""
        gate = StrategyAllowlist()
        assert gate.filter("XAUUSD", [("brand_new", _sig("brand_new"))]) == []

    def test_mixed_batch_keeps_only_allowlisted(self):
        gate = StrategyAllowlist()
        keep = _sig("squeeze_breakout")
        out = gate.filter("XAUUSD", [
            ("vwap", _sig("vwap")),
            ("squeeze_breakout", keep),
            ("smc_ob", _sig("smc_ob")),
        ])
        assert out == [keep]

    def test_empty_input_returns_empty(self):
        assert StrategyAllowlist().filter("XAUUSD", []) == []

    def test_order_is_preserved(self):
        gate = StrategyAllowlist()
        a, b = _sig("kalman_regime"), _sig("bos_structure")
        out = gate.filter("XAUUSD", [("kalman_regime", a), ("bos_structure", b)])
        assert out == [a, b]

    def test_all_eight_survivors_are_allowlisted(self):
        """Guards the spec's behaviour-identity claim."""
        assert SOLO_ALLOWED == {
            "kalman_regime", "squeeze_breakout", "stoch_pullback",
            "bos_structure", "london_breakout", "monday_drift",
            "index_overnight", "wednesday_drift",
        }

    def test_kill_list_covers_both_deletion_rounds(self):
        assert KILL_LIST == {
            "breakout", "mean_reversion", "supply_demand",
            "descending_channel_breakout", "mini_medallion",
            "continuation_breakout",
            "vwap", "momentum", "sbr", "asia_range_fade", "smc_ob",
            "fibonacci_retracement", "wavelet_cycle", "ema200_nasdaq",
        }

    def test_allowlist_and_kill_list_are_disjoint(self):
        assert not (SOLO_ALLOWED & KILL_LIST)

    def test_accepts_config_dict_but_ignores_it(self):
        """Call sites pass the old gate config; it must not crash or re-enable anything."""
        gate = StrategyAllowlist({"enabled": False, "window_minutes": 120})
        assert gate.filter("XAUUSD", [("vwap", _sig("vwap"))]) == []
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
./venv/bin/pytest tests/unit/test_strategy_allowlist.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'src.strategies.strategy_allowlist'`.

That is the *only* acceptable failure here. `Signal` and `OrderSide` are verified: all `Signal` fields are optional, `OrderSide` and `MarketRegime` both live in `src.core.constants`. A `TypeError` from `_sig()` would mean `Signal` changed — stop and re-read `src/core/types.py` rather than working around it.

- [ ] **Step 3: Write the implementation**

Create `src/strategies/strategy_allowlist.py`:

```python
"""
StrategyAllowlist — the safety net extracted from ConfluenceGate.

ConfluenceGate (deleted 2026-08-13) carried two separable jobs: combo
assembly (COMBO A/B/C, ``combo_sniper``, the exhaustion modifier) and a
default-deny allowlist. Every input to the combo half was deleted along with
it — the six confluence-only legs never once produced a combo, across a
window widened 25 -> 120 minutes.

This module keeps only the second job:

  * ``SOLO_ALLOWED`` — strategies permitted to reach the risk engine.
  * ``KILL_LIST``    — names of deleted strategies, so a stale config cannot
                       resurrect one. Configs are hot-swapped between eight
                       files day by day; this is the net under that.

Anything not explicitly allowlisted is dropped. Default deny is the point.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from ..core.types import Signal

# Strategies permitted to execute. A strategy absent from this set cannot
# trade no matter what any config says.
SOLO_ALLOWED: frozenset = frozenset({
    "kalman_regime",
    "squeeze_breakout",
    "stoch_pullback",
    "bos_structure",
    "london_breakout",
    "monday_drift",
    "index_overnight",
    "wednesday_drift",
})

# Deleted strategies. Names are retained so a stale config referencing one is
# dropped loudly rather than crashing the registry lookup.
KILL_LIST: frozenset = frozenset({
    # deleted 2026-06-10 — no backtested edge
    "breakout",
    "mean_reversion",
    "supply_demand",
    "descending_channel_breakout",
    "mini_medallion",
    "continuation_breakout",
    # deleted 2026-08-13 — confluence-only legs whose combos never fired,
    # plus two already-disabled strategies with published refutations
    "vwap",
    "momentum",
    "sbr",
    "asia_range_fade",
    "smc_ob",
    "fibonacci_retracement",
    "wavelet_cycle",
    "ema200_nasdaq",
})


class StrategyAllowlist:
    """Default-deny filter over raw strategy signals."""

    def __init__(self, config: Optional[dict] = None) -> None:
        # `config` is accepted and ignored. Call sites still hand over the old
        # gate config block; there is deliberately no enable flag, because a
        # switch that turns a safety net off is not a safety net.
        from ..monitoring.logger import get_logger
        self._logger = get_logger(__name__)

    def filter(
        self,
        symbol: str,
        signals: Iterable[Tuple[str, Signal]],
    ) -> List[Signal]:
        """Return the executable signals, in input order.

        Args:
            symbol: ticker the signals were generated for (logging only).
            signals: ``(strategy_name, Signal)`` pairs emitted this tick.

        Returns:
            Signals whose strategy is in ``SOLO_ALLOWED``. May be empty.
        """
        out: List[Signal] = []
        for name, sig in signals:
            if name in KILL_LIST:
                self._logger.info(
                    f"[StrategyAllowlist] kill-list drop: {name} on {symbol}"
                )
                continue
            if name not in SOLO_ALLOWED:
                self._logger.info(
                    f"[StrategyAllowlist] not allowlisted, dropped: {name} on {symbol}"
                )
                continue
            out.append(sig)
        return out
```

Note the ordering: the kill-list check logs *before* dropping. The old gate filtered first and then looped over the already-filtered list looking for kill-list names (`confluence_gate.py:169-170`), so that log line could never fire. Don't reproduce that.

- [ ] **Step 4: Run the tests**

```bash
./venv/bin/pytest tests/unit/test_strategy_allowlist.py -q
```

Expected: 10 passed.

- [ ] **Step 5: Confirm nothing else broke**

```bash
./venv/bin/pytest -q 2>&1 | tail -5
```

Expected: baseline count from Task 1, **+10**. Nothing is wired up yet.

- [ ] **Step 6: Commit**

```bash
git add src/strategies/strategy_allowlist.py tests/unit/test_strategy_allowlist.py
git commit -m "feat(strategies): add StrategyAllowlist, the net extracted from ConfluenceGate

Default-deny allowlist plus a 14-name kill list. Not yet wired — ConfluenceGate
is still the active filter until the next commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Swap both call sites, delete `ConfluenceGate`

**Files:**
- Modify: `src/main.py:44` (import), `:403-415` (construction), `:888-910` (filter call)
- Modify: `src/backtest/ensemble_engine.py:47` (import), `:110-111` (construction), `:240-241`, `:326-362` (filter call)
- Delete: `src/strategies/confluence_gate.py`, `tests/unit/test_confluence_gate.py`

**Interfaces:**
- Consumes: `StrategyAllowlist` from Task 2.
- Produces: `self.strategy_allowlist` on both `TradingSystem` and the ensemble engine.

- [ ] **Step 1: Rewire `src/main.py`**

Line 44 — replace the import:

```python
from src.strategies.strategy_allowlist import StrategyAllowlist
```

Lines ~403-415 — replace construction and its log line:

```python
            # StrategyAllowlist — default-deny filter. Only strategies in
            # SOLO_ALLOWED reach the risk engine.
            self.strategy_allowlist = StrategyAllowlist()
            self.logger.info("✓ StrategyAllowlist ready (default-deny)")
```

Lines ~888-910 — replace the whole gate block (the `current_regime` lookup, the exhaustion read, and the `filter(...)` call) with:

```python
                # StrategyAllowlist — default-deny; drops kill-list and any
                # strategy not explicitly allowlisted.
                executable_signals = self.strategy_allowlist.filter(
                    symbol=symbol_ticker,
                    signals=all_signals,
                )
```

- [ ] **Step 2: Check the comment below the call still reads true**

The loop after the call has a comment about composing "any combo multiplier the gate already attached (sniper writes lot_size_multiplier into metadata)". Sniper is gone. Trim that comment to describe only the session multiplier. **Leave the multiplication code itself alone** — other strategies write `lot_size_multiplier` too.

- [ ] **Step 3: Leave `_symbol_regimes` in place**

`src/main.py:408` initialises it, `:1076` writes it, and `:891` (now deleted) was its only reader. It becomes write-only. **Keep it** — it belongs to the nightly regime-classifier integration, not the gate, and removing it means touching the regime-override path, which is out of scope for a behaviour-preserving cleanup. Note it in the commit body as a known leftover.

- [ ] **Step 4: Rewire `src/backtest/ensemble_engine.py`**

Line 47 — `from ..strategies.strategy_allowlist import StrategyAllowlist`

Lines ~110-111:

```python
        self.strategy_allowlist = StrategyAllowlist()
```

Delete the `gate_cfg = cfg.get(...)` line above it.

Lines ~240-241 — delete the `if self.confluence_gate.exhaustion_enabled: tfs_needed.add(...)` block entirely.

Lines ~326-362 — delete the regime-derivation loop, the exhaustion read, and replace the call:

```python
        executable = self.strategy_allowlist.filter(
            symbol=self.symbol.ticker,
            signals=signals,
        )
        for signal in executable:
            self._execute(signal, signal.strategy_name, current_bar)
```

- [ ] **Step 5: Check `bar_ts` and `MarketRegime` are still used**

The `bar_ts` block (`ensemble_engine.py:338-342`) existed to pass `now=` to the gate. Before deleting it:

```bash
grep -n "bar_ts" src/backtest/ensemble_engine.py
grep -n "MarketRegime" src/backtest/ensemble_engine.py src/main.py
```

Delete `bar_ts` only if it has no other reader. Keep the `MarketRegime` import in either file if anything else still references it. Removing a still-used import is how this task fails.

- [ ] **Step 6: Delete the gate**

```bash
git rm src/strategies/confluence_gate.py tests/unit/test_confluence_gate.py
```

- [ ] **Step 7: Verify no dangling references**

```bash
grep -rn "confluence_gate\|ConfluenceGate" src/ scripts/ tests/ --include="*.py"
```

Expected: no output. (Matches in `config/*.yaml` and `CLAUDE.md` are handled in Tasks 5 and 8.)

- [ ] **Step 8: Run the suite**

```bash
./venv/bin/pytest -q 2>&1 | tail -5
```

Expected: green. Count drops by however many tests `test_confluence_gate.py` held.

- [ ] **Step 9: Commit**

```bash
git add -A src/main.py src/backtest/ensemble_engine.py
git commit -m "refactor: replace ConfluenceGate with StrategyAllowlist at both call sites

Combo assembly (COMBO A/B/C, combo_sniper, exhaustion modifier) is deleted with
the legs that fed it. All eight surviving strategies were already in
SOLO_ALLOWED, so the executable set is unchanged.

Known leftover: main.py _symbol_regimes is now write-only. Kept deliberately —
it belongs to the regime-classifier integration, not the gate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Delete the eight strategies, registry entries, and weights

These must move together. A partial deletion fails `test_weights_table_completeness`, which asserts `STRATEGY_WEIGHTS` keys are identical across all three regimes.

**Files:**
- Delete: 8 strategy files, `src/strategies/regime_filter.py`, `src/strategies/multi_timeframe_filter.py`, 3 test files
- Modify: `src/strategies/strategy_manager.py:19-34` (imports), `:43-58` (registry)
- Modify: `scripts/regime_classifier.py:253+` (all 3 regime dicts)
- Modify: `tests/unit/test_regime_classifier.py:294-299` (`required_core`)

- [ ] **Step 1: Update `required_core` first, and watch it fail**

`tests/unit/test_regime_classifier.py:294`:

```python
        required_core = {
            "kalman_regime", "london_breakout", "monday_drift",
            "squeeze_breakout", "stoch_pullback", "index_overnight",
            "wednesday_drift", "bos_structure",
        }
```

```bash
./venv/bin/pytest tests/unit/test_regime_classifier.py -q
```

Expected: **PASS** — `required_core` is a subset check (`required_core - keys`), so shrinking it cannot fail. That is exactly why the cross-regime key-equality assert on the next lines is the real guard, and why Step 2 must touch all three regimes.

- [ ] **Step 2: Remove the 8 keys from all three regimes**

In `scripts/regime_classifier.py`, delete these keys from **each** of `TREND`, `RANGE`, and `VOLATILE`: `momentum`, `vwap`, `sbr`, `asia_range_fade`, `smc_ob`, `fibonacci_retracement`, `ema200_nasdaq`, `wavelet_cycle`.

Then confirm all three have identical keys:

```bash
./venv/bin/python -c "
import sys; sys.path.insert(0,'scripts')
from regime_classifier import STRATEGY_WEIGHTS as W
ks = {r: set(v) for r, v in W.items()}
print({r: len(k) for r, k in ks.items()})
assert len(set(map(frozenset, ks.values()))) == 1, 'regimes diverge'
print('identical keys:', sorted(next(iter(ks.values()))))
"
```

Expected: 8 keys per regime, all eight survivors.

- [ ] **Step 3: Check `resolve_strategy_overrides` for stale names**

```bash
grep -n "vwap\|momentum\|sbr\|asia_range_fade\|smc_ob\|fibonacci\|wavelet\|ema200" scripts/regime_classifier.py
```

Expected after Step 2: no output. If any remain (e.g. in a regime-gating branch), remove them.

- [ ] **Step 4: Strip the registry**

`src/strategies/strategy_manager.py` — delete the 8 imports (lines 19, 20, 22, 23, 24, 25, 33, 34) and the 8 registry entries (lines 43, 44, 46, 47, 48, 49, 57, 58). Line 22 is `from .structure_break_retest import StructureBreakRetestStrategy` (the `sbr` key).

- [ ] **Step 5: Delete the files**

```bash
git rm src/strategies/vwap_strategy.py \
       src/strategies/momentum_strategy.py \
       src/strategies/structure_break_retest.py \
       src/strategies/asia_range_fade_strategy.py \
       src/strategies/smc_ob_strategy.py \
       src/strategies/fibonacci_retracement_strategy.py \
       src/strategies/wavelet_cycle_strategy.py \
       src/strategies/ema200_nasdaq_strategy.py \
       src/strategies/regime_filter.py \
       src/strategies/multi_timeframe_filter.py \
       tests/unit/test_sbr_strategy.py \
       tests/unit/test_wavelet_cycle_strategy.py \
       tests/unit/test_ema200_nasdaq.py
```

`regime_filter.py` is imported only by `vwap_strategy.py`; `multi_timeframe_filter.py` has no importers (both verified).

- [ ] **Step 6: Hunt every dangling import**

```bash
grep -rn "vwap_strategy\|momentum_strategy\|structure_break_retest\|asia_range_fade_strategy\|smc_ob_strategy\|fibonacci_retracement_strategy\|wavelet_cycle_strategy\|ema200_nasdaq_strategy\|regime_filter\|multi_timeframe_filter" src/ tests/ --include="*.py"
```

Expected: no output from `src/` or `tests/`. Matches under `scripts/research_*.py` are **fine and expected** — those are research artifacts and stay. If a live-path file matches, fix it now.

- [ ] **Step 7: Run the suite**

```bash
./venv/bin/pytest -q 2>&1 | tail -15
```

Expected: green. If a collection error names a deleted module, Step 6's grep missed a caller — fix and re-run.

- [ ] **Step 8: Commit**

```bash
git add -A src/strategies/ scripts/regime_classifier.py tests/unit/
git commit -m "refactor(strategies): delete eight inert strategies

vwap, momentum, sbr, asia_range_fade, smc_ob, fibonacci_retracement: existed
only as ConfluenceGate combo legs; no combo ever fired, including after the
window was widened 25 -> 120 minutes.

wavelet_cycle: premise falsified by a scale-invariance test with a passing
positive control. ema200_nasdaq: 0 of 96 grid cells cleared the every-year gate.
Both were already enabled: false.

Also removes regime_filter.py (only vwap imported it) and
multi_timeframe_filter.py (no importers). Research scripts and reports are
retained. Git history holds the deleted code.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Clean the eight configs and the symbol reconciler

**Files:**
- Modify: `config/config_live.yaml`, `config_live_100.yaml`, `config_live_1000.yaml`, `config_live_5000.yaml`, `config_live_10000.yaml`, `config_live_25000.yaml`, `config_live_50000.yaml`, `config_live_100000.yaml`
- Modify: `src/strategies/symbol_reconciler.py:29`

- [ ] **Step 1: In each of the 8 configs, remove**

1. The `strategies.<name>:` block for all eight deleted strategies.
2. The entire `strategies.confluence_gate:` block.
3. Every occurrence of a deleted name in `trading_hours.sessions[].strategies: [...]` whitelists.
4. The `symbols.NAS100:` block — its only consumer was `ema200_nasdaq`.

Leave `strategies.regime_filter:` alone if present — that is a separate config key from the deleted module.

Concretely, in each file this means deleting hunks shaped like:

```yaml
  # --- REMOVE: strategy block ---
  vwap:
    enabled: true
    timeframe: 15m
    deviation_threshold: 2.0        # (params vary per file)

  # --- REMOVE: the whole gate block ---
  confluence_gate:
    enabled: true
    window_minutes: 120
    sniper_lot_multiplier: 1.5
    sniper_cooldown_minutes: 60
    exhaustion_filter:
      enabled: false
      ...
```

and editing session whitelists in place:

```yaml
trading_hours:
  sessions:
    - name: london
      # BEFORE
      strategies: [kalman_regime, vwap, momentum, sbr, squeeze_breakout]
      # AFTER
      strategies: [kalman_regime, squeeze_breakout]
```

and deleting the orphaned symbol block:

```yaml
  # --- REMOVE: only consumer was ema200_nasdaq ---
  NAS100:
    enabled: true
    strategy_whitelist: [ema200_nasdaq]
    min_lot: 0.1                    # PLACEHOLDER spec, never verified
    ...
```

Exact keys differ per file — Step 2 is the authority on whether a file is clean, not this sketch.

- [ ] **Step 2: Verify each config parses and has exactly the 8 survivors**

```bash
for f in config/config_live.yaml config/config_live_{100,1000,5000,10000,25000,50000,100000}.yaml; do
  ./venv/bin/python -c "
import yaml, sys
c = yaml.safe_load(open('$f'))
dead = {'vwap','momentum','sbr','asia_range_fade','smc_ob',
        'fibonacci_retracement','wavelet_cycle','ema200_nasdaq','confluence_gate'}
s = set(c.get('strategies') or {})
bad = s & dead
sess = [x for sn in (c.get('trading_hours') or {}).get('sessions', [])
          for x in (sn.get('strategies') or []) if x in dead]
syms = set(c.get('symbols') or {})
print('$f', 'strategies_leak=', bad or '-', 'session_leak=', set(sess) or '-',
      'NAS100=', 'NAS100' in syms)
"
done
```

Expected for every file: `strategies_leak= -`, `session_leak= -`, `NAS100= False`.

- [ ] **Step 3: Update the symbol reconciler**

`src/strategies/symbol_reconciler.py` — delete line 29 (`'ema200_nasdaq': ['NAS100'],`). Then check the day map:

```bash
grep -n "ema200_nasdaq" src/strategies/symbol_reconciler.py
```

Expected: no output. Remove any `_FIRE_WEEKDAY` entry too. The other seven `_STRATEGY_SYMBOLS` entries are all survivors — leave them.

- [ ] **Step 4: Run the suite**

```bash
./venv/bin/pytest -q 2>&1 | tail -5
```

Expected: green.

- [ ] **Step 5: Commit — configs only, not the news-date churn**

```bash
git add config/config_live*.yaml src/strategies/symbol_reconciler.py
git diff --cached --stat
```

Confirm the diff shows only strategy/session/symbol removals. **If a `news/2026-08-13_news.csv` hunk appears, unstage it** (`git restore --staged` that hunk or use `git add -p`) — it is unrelated pre-existing churn.

```bash
git commit -m "chore(config): drop deleted strategies from all eight live configs

Removes the eight strategy blocks, the confluence_gate block, every session
whitelist entry, and the orphaned symbols.NAS100 block. Also drops
ema200_nasdaq from symbol_reconciler._STRATEGY_SYMBOLS.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Regenerate the runtime overrides

**Files:**
- Modify: `data/config_override*.json` (23 files)

- [ ] **Step 1: Check what the overrides currently carry**

```bash
grep -l "vwap\|momentum\|sbr\|asia_range_fade\|smc_ob\|fibonacci\|wavelet\|ema200" data/config_override*.json | wc -l
```

- [ ] **Step 2: Regenerate**

```bash
./venv/bin/python scripts/regime_classifier.py 2>&1 | tail -20
```

If it needs a config argument, pass the active one: `--config config/config_live_50000.yaml` (`config/ACTIVE_CONFIG` names it). If it needs live MT5 or market data and cannot run offline, **do not fake the output** — report it, and instead hand-edit the JSONs to drop the eight keys, which is safe because `_apply_regime_override()` reads them by name.

- [ ] **Step 3: Verify no deleted name survives**

```bash
grep -l "\"vwap\"\|\"momentum\"\|\"sbr\"\|\"asia_range_fade\"\|\"smc_ob\"\|\"fibonacci_retracement\"\|\"wavelet_cycle\"\|\"ema200_nasdaq\"" data/config_override*.json
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add data/config_override*.json
git commit -m "chore(data): regenerate regime overrides without deleted strategies

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Verification — the load-bearing gate

This is the task the whole change is judged on.

- [ ] **Step 1: Full test suite**

```bash
./venv/bin/pytest -q 2>&1 | tail -10
```

Expected: **zero failures**. The total will be below the Task 1 baseline (four test files removed, one added). Write down the exact number.

- [ ] **Step 2: Re-run the backtest, identical invocation to Task 1**

```bash
./venv/bin/python scripts/run_backtest.py --symbols XAUUSD --timeframe 15m \
  2>&1 | tee /tmp/baseline/backtest_after.txt
```

- [ ] **Step 3: Diff the results**

```bash
diff /tmp/baseline/backtest_before.txt /tmp/baseline/backtest_after.txt && echo "IDENTICAL"
```

Expected: `IDENTICAL`, or differences confined to timestamps/paths/runtime. **Trade count, trade list, and net PnL must match exactly.**

- [ ] **Step 4: If they diverge — stop**

Do not patch. A divergence means something in the deleted set was reachable, which falsifies the premise of the whole change. Report which metric moved and by how much, and leave the branch unmerged for a decision.

- [ ] **Step 5: Report the numbers**

State plainly: tests before → after, backtest trades before → after, net PnL before → after. Do not claim success without pasting the actual output.

---

### Task 8: Documentation and memory

**Files:**
- Modify: `CLAUDE.md`
- Create: memory file + `MEMORY.md` pointer

- [ ] **Step 1: Update the `CLAUDE.md` strategy table**

Remove rows 2–7 (`Momentum`, `VWAP`, `StructureBreakRetest`, `FibonacciRetracement`, `SMCOrderBlock`, `AsiaRangeFade`), row 15 (`EMA200Nasdaq`), row 16 (`WaveletCycle`). Renumber the survivors 1–8.

- [ ] **Step 2: Replace the ConfluenceGate paragraph**

Rewrite the "As of 2026-05-14, raw strategy signals are post-filtered by ConfluenceGate" paragraph as:

```markdown
**As of 2026-08-13, `ConfluenceGate` is DELETED and replaced by
`StrategyAllowlist` (`src/strategies/strategy_allowlist.py`).** The combo policy
from `combine_startegy.md` (COMBO A/B/C, `combo_sniper` at 1.5×, the exhaustion
modifier) never fired a single combo in live trading — including after the
window was widened 25 → 120 minutes — so the six confluence-only legs and the
machinery consuming them were removed together. What remains is default-deny:
`SOLO_ALLOWED` lists the eight strategies permitted to execute, `KILL_LIST`
holds all 14 deleted names so a stale config cannot resurrect one. There is no
enable flag by design.
```

- [ ] **Step 3: Preserve every rejection notice**

The `wavelet_cycle` and `ema200_nasdaq` entries carry hard-won negative results — the falsified scale-invariance premise, the `spectral.dominant_cycle` blindness at `window >= 128`, the 0/96 v2 grid, "do NOT re-research the NY-open anchor-break family". **Move these into the "Researched and REJECTED" section; do not delete them.** Add "code deleted 2026-08-13, see git history" to each. Deleting the warning is how a dead end gets re-researched in six months.

- [ ] **Step 4: Update the propagation checklist**

The "Checklist for a new strategy" at the end of `CLAUDE.md` references `ConfluenceGate`. Point it at `StrategyAllowlist` and note that a new strategy must be added to `SOLO_ALLOWED` or it silently cannot trade.

- [ ] **Step 5: Write the memory file**

Create `/Users/varadbandekar/.claude/projects/-Users-varadbandekar-Documents-Quant-trading/memory/project_inert_strategies_deleted.md`:

```markdown
---
name: project-inert-strategies-deleted
description: 2026-08-13 deleted 8 inert strategies + ConfluenceGate; StrategyAllowlist is now the default-deny net
metadata:
  type: project
---

2026-08-13: deleted `vwap`, `momentum`, `sbr`, `asia_range_fade`, `smc_ob`,
`fibonacci_retracement` (ConfluenceGate combo legs — no combo ever fired),
plus `wavelet_cycle` and `ema200_nasdaq` (already disabled, refuted).
`ConfluenceGate` deleted; `src/strategies/strategy_allowlist.py` keeps
`SOLO_ALLOWED` (8 survivors) + `KILL_LIST` (14 names). No enable flag.

**Why:** live journals showed 123 bot trades, all `kalman_regime`. The six legs
could not reach the risk engine under any config in the repo.

**How to apply:** a new strategy MUST be added to `SOLO_ALLOWED` or it silently
cannot trade — this is now a step in the CLAUDE.md propagation checklist.
Verified behaviour-identical by a before/after XAUUSD 15m backtest trade-list
diff. Related: [[project_kill_list_strategies_deleted]],
[[project_confluence_gate]], [[project_confluence_window_widen]].
```

- [ ] **Step 6: Add the `MEMORY.md` pointer**

Under "## Live strategies and policy" in `MEMORY.md`:

```markdown
- [8 inert strategies DELETED](project_inert_strategies_deleted.md) — 2026-08-13 confluence legs + wavelet + ema200 removed; ConfluenceGate → StrategyAllowlist (default-deny, no enable flag). New strategies MUST join SOLO_ALLOWED.
```

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the 2026-08-13 strategy deletion

Collapses eight rows out of the strategy table, replaces the ConfluenceGate
section with StrategyAllowlist, and moves the wavelet_cycle / ema200_nasdaq
findings into the REJECTED section so the warnings outlive the code.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done criteria

- [ ] `pytest` green, zero failures
- [ ] Backtest trade list identical before vs after
- [ ] `grep -rn "ConfluenceGate" src/ tests/ --include="*.py"` returns nothing
- [ ] All 8 configs parse with exactly the 8 surviving strategies
- [ ] `CLAUDE.md` rejection notices intact for both refuted strategies
- [ ] Branch left unmerged for user review
