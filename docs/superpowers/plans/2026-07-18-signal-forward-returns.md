# Signal Forward-Return Analyzer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether each order-flow mark type would have made money as a trade — reconstruct signals from tick history, label each with a cost-aware triple-barrier R outcome, and report per mark×direction verdicts with IS/OOS split and significance.

**Architecture:** Pure core `src/microstructure/forward_returns.py` (event_direction, atr, label_event, summarize — no I/O, no ML) + research script `scripts/analyze_signal_forward_returns.py` (reconstruct via features.py detectors, label, render console table + markdown report). Research-only; registered nowhere.

**Tech Stack:** Python 3.11 venv (`./venv/bin/python`), pandas/numpy, pytest. Reuses Stage-1 `src/microstructure/features.py` and `scripts/fetch_dukascopy_ticks.py`.

**Spec:** `docs/superpowers/specs/2026-07-18-signal-forward-returns-design.md`

## Global Constraints

- Repo venv for everything: `./venv/bin/python`, `./venv/bin/pytest`, from repo root (`pytest.ini` sets `pythonpath = .`).
- Do NOT modify `src/microstructure/features.py`, `scripts/fetch_dukascopy_ticks.py`, live trading code, configs, or the bridge. Consume existing exports as-is.
- Reads the immutable `data/ticks/` store only (never writes it); the report goes to `reports/`.
- `src/microstructure/forward_returns.py` is PURE: no file/network I/O, no ML, no global state. All thresholds are params.
- R = risk-multiple, where 1R = `sl_atr × ATR` in price. Costs are `cost_pts` per side, charged BOTH entry and exit = total `2 × cost_pts` in price, converted to R and subtracted from every outcome.
- A `dead`/`thin` sweep is a valid, valuable result — success is a trustworthy verdict, not a found edge. Do not tune barrier defaults to manufacture a `CANDIDATE`.
- The worktree carries unrelated uncommitted `config_live_*.yaml` / `data/strategy_risk_weights.json` changes and an untracked `ncat` file — never stage, commit, or revert them. `git add` by explicit path only.
- Commit messages: the harness shell flattens newlines in `git commit -m` — ALWAYS write the message to a scratch file with the Write tool (NOT printf/echo), with a real blank line before the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, then `git commit -F <file>` and verify with `git log -1 --format=%B`.

---

### Task 1: Pure core part A — event_direction, atr, label_event

**Files:**
- Create: `src/microstructure/forward_returns.py`
- Test: `tests/unit/test_forward_returns.py`

**Interfaces:**
- Produces (used by Task 2 & 3):
  - `event_direction(kind: str) -> str | None` — `"long"` / `"short"` / `None`.
  - `atr(bars: pd.DataFrame, period: int = 14) -> pd.Series` — Wilder-style rolling-mean true range over `open/high/low/close` bars.
  - `@dataclass(frozen=True) LabelConfig(sl_atr=1.0, tp_atr=2.0, max_hold_bars=16, cost_pts=0.4, timeframe="15min")`
  - `label_event(mids: pd.Series, direction: str, atr_val: float, cfg: LabelConfig) -> dict | None` — `mids` = UTC-indexed mid-price Series from entry forward (entry = first element). Returns `{"direction","outcome","R_net","bars_held","mae","mfe"}` (`outcome ∈ {"target","stop","time"}`), or `None` if `atr_val<=0` or `mids` empty.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_forward_returns.py`:

```python
"""Unit tests for src/microstructure/forward_returns.py — synthetic, no I/O."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import forward_returns as fr


def mids(prices, start="2026-07-16 09:00", freq="30s"):
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.Series([float(p) for p in prices], index=idx)


CFG = fr.LabelConfig(sl_atr=1.0, tp_atr=2.0, max_hold_bars=16, cost_pts=0.0,
                     timeframe="15min")


class TestEventDirection:
    def test_long_short_none(self):
        assert fr.event_direction("bullish_divergence") == "long"
        assert fr.event_direction("sweep_low") == "long"
        assert fr.event_direction("absorption_of_selling") == "long"
        assert fr.event_direction("imbalance_buy") == "long"
        assert fr.event_direction("bearish_divergence") == "short"
        assert fr.event_direction("sweep_high") == "short"
        assert fr.event_direction("absorption_of_buying") == "short"
        assert fr.event_direction("imbalance_sell") == "short"
        assert fr.event_direction("liquidity_withdrawal") is None


class TestAtr:
    def test_atr_constant_range(self):
        idx = pd.date_range("2026-07-16 09:00", periods=20, freq="15min", tz="UTC")
        bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 100.0,
                             "close": 100.5}, index=idx)
        a = fr.atr(bars, period=14)
        assert a.iloc[-1] == pytest.approx(1.0)   # every TR == 1.0


class TestLabelEvent:
    def test_target_before_stop(self):
        # long entry 100, atr 1 -> stop 99, target 102; price rises to 102
        out = fr.label_event(mids([100, 100.5, 102.0]), "long", 1.0, CFG)
        assert out["outcome"] == "target"
        assert out["R_net"] == pytest.approx(2.0)   # tp_atr/sl_atr, zero cost
        assert out["mfe"] == pytest.approx(2.0)

    def test_gap_to_stop(self):
        out = fr.label_event(mids([100, 99.5, 99.0]), "long", 1.0, CFG)
        assert out["outcome"] == "stop"
        assert out["R_net"] == pytest.approx(-1.0)

    def test_intrabar_stop_then_target_counts_as_stop(self):
        # dips to 99 (stop) FIRST, then to 102 (target) -> must be STOP
        out = fr.label_event(mids([100, 99.0, 102.0]), "long", 1.0, CFG)
        assert out["outcome"] == "stop"

    def test_time_exit_signed_r(self):
        # never hits 99 or 102 within max_hold; ends at 100.5 -> +0.5R
        cfg = fr.LabelConfig(sl_atr=1.0, tp_atr=2.0, max_hold_bars=1,
                             cost_pts=0.0, timeframe="15min")
        out = fr.label_event(mids([100, 100.2, 100.5], freq="20s"), "long", 1.0, cfg)
        assert out["outcome"] == "time"
        assert out["R_net"] == pytest.approx(0.5)

    def test_short_direction_target(self):
        # short entry 100, target 98, stop 101; price falls to 98
        out = fr.label_event(mids([100, 99.0, 98.0]), "short", 1.0, CFG)
        assert out["outcome"] == "target"
        assert out["R_net"] == pytest.approx(2.0)

    def test_costs_strictly_lower_r(self):
        free = fr.label_event(mids([100, 102.0]), "long", 1.0, CFG)
        costed = fr.label_event(mids([100, 102.0]), "long", 1.0,
                                fr.LabelConfig(cost_pts=0.5))
        assert costed["R_net"] < free["R_net"]

    def test_degenerate_atr_returns_none(self):
        assert fr.label_event(mids([100, 101]), "long", 0.0, CFG) is None
        assert fr.label_event(pd.Series(dtype=float), "long", 1.0, CFG) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/pytest tests/unit/test_forward_returns.py -v`
Expected: ERROR — `ModuleNotFoundError: ... 'src.microstructure.forward_returns'`

- [ ] **Step 3: Implement**

Create `src/microstructure/forward_returns.py`:

```python
"""
Forward-return labeling for order-flow marks (Stage-2 front half).

Pure: no I/O, no ML, no global state. R = risk-multiple (1R = sl_atr x ATR in
price). Every quantity here is a PROXY measurement over proxy signals — this
tool decides whether a mark is worth trading, not whether the marks are "real
order flow". A sweep of `dead`/`thin` verdicts is a valid, money-saving result.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_LONG = {"bullish_divergence", "sweep_low", "absorption_of_selling", "imbalance_buy"}
_SHORT = {"bearish_divergence", "sweep_high", "absorption_of_buying", "imbalance_sell"}


def event_direction(kind: str) -> str | None:
    """Implied trade side of a FlowEvent kind; None = directionless."""
    if kind in _LONG:
        return "long"
    if kind in _SHORT:
        return "short"
    return None


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Rolling-mean true range over open/high/low/close bars."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


@dataclass(frozen=True)
class LabelConfig:
    sl_atr: float = 1.0
    tp_atr: float = 2.0
    max_hold_bars: int = 16
    cost_pts: float = 0.4
    timeframe: str = "15min"


def label_event(mids: pd.Series, direction: str, atr_val: float,
                cfg: LabelConfig) -> dict | None:
    """Triple-barrier outcome over the tick path `mids` (entry = mids[0]).

    Walks ticks chronologically so an intrabar stop-then-target counts as the
    STOP. R_net is net of round-trip cost (2 x cost_pts, converted to R).
    """
    if atr_val <= 0 or len(mids) == 0:
        return None
    risk = cfg.sl_atr * atr_val
    entry = float(mids.iloc[0])
    entry_ts = mids.index[0]
    deadline = entry_ts + cfg.max_hold_bars * pd.Timedelta(cfg.timeframe)
    sign = 1.0 if direction == "long" else -1.0
    stop = entry - sign * risk
    target = entry + sign * cfg.tp_atr * atr_val
    cost_R = 2.0 * cfg.cost_pts / risk

    mfe = mae = 0.0
    outcome, gross_R, exit_i = "time", 0.0, len(mids) - 1
    for i in range(len(mids)):
        px = float(mids.iloc[i])
        excursion = sign * (px - entry) / risk
        mfe, mae = max(mfe, excursion), min(mae, excursion)
        hit_stop = (px <= stop) if direction == "long" else (px >= stop)
        hit_tgt = (px >= target) if direction == "long" else (px <= target)
        if hit_stop:                       # checked first: ties resolve to stop
            outcome, gross_R, exit_i = "stop", -1.0, i
            break
        if hit_tgt:
            outcome, gross_R, exit_i = "target", cfg.tp_atr / cfg.sl_atr, i
            break
        if mids.index[i] >= deadline:
            outcome, gross_R, exit_i = "time", sign * (px - entry) / risk, i
            break
    else:
        gross_R = sign * (float(mids.iloc[-1]) - entry) / risk
    return {"direction": direction, "outcome": outcome,
            "R_net": gross_R - cost_R, "bars_held": exit_i,
            "mae": mae, "mfe": mfe}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_forward_returns.py -v`
Expected: all PASS (11 tests)

- [ ] **Step 5: Commit** (message via file)

Message file content:
```
feat: forward-return triple-barrier labeling core

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/forward_returns.py tests/unit/test_forward_returns.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 2: Pure core part B — summarize (stats, IS/OOS, verdict)

**Files:**
- Modify: `src/microstructure/forward_returns.py` (append)
- Test: `tests/unit/test_forward_returns.py` (append)

**Interfaces:**
- Consumes: labeled events shaped `{"ts": pd.Timestamp, "kind": str, "direction": str, "R_net": float, "bars_held": int}` (the script builds these by merging `label_event` output with each signal's `ts`/`kind`).
- Produces (used by Task 3):
  - `summarize(events: list[dict], split_frac: float = 0.7) -> dict` — returns `{"boundary_ts": pd.Timestamp | None, "cells": list[CellStat]}` where each `CellStat` is a dict with keys `kind, direction, n, n_is, n_oos, expectancy, exp_is, exp_oos, win_rate, profit_factor, total_R, t_stat, median_bars, verdict` and `verdict ∈ {"CANDIDATE","one-sided","thin","dead"}`. Cells sorted by `total_R` desc.
  - `MIN_CELL_N = 30` (module constant).

Verdict rule (exact): `thin` if `n_is < MIN_CELL_N or n_oos < MIN_CELL_N`; else `CANDIDATE` if `exp_is > 0 and exp_oos > 0 and t_stat > 2`; else `one-sided` if `exp_is > 0 or exp_oos > 0`; else `dead`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_forward_returns.py`)

```python
class TestSummarize:
    def _events(self, kind, direction, r_list, start="2026-07-01"):
        idx = pd.date_range(start, periods=len(r_list), freq="1h", tz="UTC")
        return [{"ts": t, "kind": kind, "direction": direction,
                 "R_net": float(r), "bars_held": 3}
                for t, r in zip(idx, r_list)]

    def test_expectancy_pf_winrate(self):
        evs = self._events("imbalance_buy", "long", [2.0, -1.0, 2.0, -1.0])
        cell = fr.summarize(evs)["cells"][0]
        assert cell["n"] == 4
        assert cell["expectancy"] == pytest.approx(0.5)
        assert cell["win_rate"] == pytest.approx(0.5)
        assert cell["profit_factor"] == pytest.approx(2.0)  # 4 / 2

    def test_thin_when_small_n(self):
        cell = fr.summarize(self._events("sweep_low", "long", [1.0, 1.0]))["cells"][0]
        assert cell["verdict"] == "thin"

    def test_candidate_needs_both_halves_positive_and_significant(self):
        # 100 strongly-positive events, low variance; split 0.6 -> both
        # halves >=30, both positive, t>2 -> CANDIDATE
        evs = self._events("sweep_low", "long", [1.0, 0.9] * 50)
        cell = fr.summarize(evs, split_frac=0.6)["cells"][0]
        assert cell["n_is"] >= 30 and cell["n_oos"] >= 30
        assert cell["verdict"] == "CANDIDATE"

    def test_one_sided_flagged(self):
        # IS all +1, OOS all -1 (>=30 each) -> one-sided, not candidate
        pos = self._events("imbalance_buy", "long", [1.0] * 45, start="2026-07-01")
        neg = self._events("imbalance_buy", "long", [-1.0] * 45, start="2026-08-01")
        cell = fr.summarize(pos + neg, split_frac=0.5)["cells"][0]
        assert cell["verdict"] == "one-sided"

    def test_dead_when_both_negative(self):
        # 100 negative events; split 0.6 -> both halves >=30 and negative -> dead
        evs = self._events("sweep_high", "short", [-1.0, -0.9] * 50)
        cell = fr.summarize(evs, split_frac=0.6)["cells"][0]
        assert cell["n_is"] >= 30 and cell["n_oos"] >= 30
        assert cell["verdict"] == "dead"

    def test_cells_sorted_by_total_r_desc(self):
        good = self._events("sweep_low", "long", [1.0] * 10, start="2026-07-01")
        bad = self._events("sweep_high", "short", [-1.0] * 10, start="2026-07-01")
        cells = fr.summarize(good + bad)["cells"]
        assert cells[0]["total_R"] >= cells[1]["total_R"]
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_forward_returns.py::TestSummarize -v`
Expected: FAIL — `AttributeError: ... 'summarize'`

- [ ] **Step 3: Implement** (append to `src/microstructure/forward_returns.py`)

```python
MIN_CELL_N = 30


def _cell_stats(kind: str, direction: str, rows: list[dict],
                boundary_ts) -> dict:
    r = np.array([e["R_net"] for e in rows], dtype=float)
    is_r = np.array([e["R_net"] for e in rows if e["ts"] <= boundary_ts])
    oos_r = np.array([e["R_net"] for e in rows if e["ts"] > boundary_ts])
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    t_stat = float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 else 0.0
    exp_is = float(is_r.mean()) if len(is_r) else 0.0
    exp_oos = float(oos_r.mean()) if len(oos_r) else 0.0
    n_is, n_oos = len(is_r), len(oos_r)
    if n_is < MIN_CELL_N or n_oos < MIN_CELL_N:
        verdict = "thin"
    elif exp_is > 0 and exp_oos > 0 and t_stat > 2:
        verdict = "CANDIDATE"
    elif exp_is > 0 or exp_oos > 0:
        verdict = "one-sided"
    else:
        verdict = "dead"
    return {"kind": kind, "direction": direction, "n": len(r),
            "n_is": n_is, "n_oos": n_oos, "expectancy": float(r.mean()),
            "exp_is": exp_is, "exp_oos": exp_oos,
            "win_rate": float((r > 0).mean()), "profit_factor": pf,
            "total_R": float(r.sum()), "t_stat": t_stat,
            "median_bars": float(np.median([e["bars_held"] for e in rows])),
            "verdict": verdict}


def summarize(events: list[dict], split_frac: float = 0.7) -> dict:
    """Per (kind, direction) triple-barrier stats with a global time IS/OOS
    split and a CANDIDATE/one-sided/thin/dead verdict per cell."""
    if not events:
        return {"boundary_ts": None, "cells": []}
    ts_sorted = sorted(e["ts"] for e in events)
    boundary_ts = ts_sorted[min(int(len(ts_sorted) * split_frac),
                                len(ts_sorted) - 1)]
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        groups.setdefault((e["kind"], e["direction"]), []).append(e)
    cells = [_cell_stats(k, d, rows, boundary_ts) for (k, d), rows in groups.items()]
    cells.sort(key=lambda c: c["total_R"], reverse=True)
    return {"boundary_ts": boundary_ts, "cells": cells}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_forward_returns.py -v`
Expected: all PASS (17 tests)

- [ ] **Step 5: Commit** (message via file)

```
feat: forward-return summarize with IS/OOS split + verdicts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/forward_returns.py tests/unit/test_forward_returns.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 3: Orchestration script + report

**Files:**
- Create: `scripts/analyze_signal_forward_returns.py`

**Interfaces:**
- Consumes: `forward_returns` (all of Tasks 1–2); `features` (`resample_bars`, `delta_divergence`, `bar_delta`, `absorption_zones`, `imbalance_events`, `sweep_events`, `liquidity_withdrawal`); `fetch_dukascopy_ticks.ensure_ticks`; the Stage-1 `features.load_ticks`.
- Produces: `python scripts/analyze_signal_forward_returns.py --symbol XAUUSD --start … --end …` → console table + `reports/signal_forward_returns.md`. Keeps `reconstruct_events(df, timeframe) -> list[dict]` and `label_all(df, events, cfg) -> list[dict]` as importable top-level functions (no printing inside) for the smoke test.

- [ ] **Step 1: Write the script**

Create `scripts/analyze_signal_forward_returns.py`:

```python
#!/usr/bin/env python3
"""
Forward-return analyzer for order-flow marks (Stage-2 front half).

Reconstructs signals by replaying the five features.py detectors over
Dukascopy tick history, labels each with a cost-aware triple-barrier R
outcome, and reports per mark x direction verdicts with a 70/30 IS/OOS
split and significance. The live {day}_signals.jsonl feed is analyzed as a
separate cohort. A sweep of dead/thin verdicts is a valid, money-saving
result — success is a trustworthy verdict, not a found edge.

    python scripts/analyze_signal_forward_returns.py --symbol XAUUSD \
        --start 2026-05-01 --end 2026-07-15
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_dukascopy_ticks import ensure_ticks  # noqa: E402
from src.microstructure import features as ft  # noqa: E402
from src.microstructure import forward_returns as fr  # noqa: E402

REPORT = PROJECT_ROOT / "reports" / "signal_forward_returns.md"
LIVE_DIR = PROJECT_ROOT / "data" / "ticks_live"


def reconstruct_events(df: pd.DataFrame, timeframe: str) -> list[dict]:
    """Replay all five detectors -> flat [{ts, kind, price}] table."""
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    evs = []
    evs += ft.delta_divergence(bars, delta)
    evs += ft.absorption_zones(df)
    evs += ft.imbalance_events(df, freq=timeframe)
    evs += ft.sweep_events(df)
    evs += ft.liquidity_withdrawal(df)
    return [{"ts": e.ts, "kind": e.kind, "price": float(e.price)} for e in evs]


def label_all(df: pd.DataFrame, events: list[dict], cfg: fr.LabelConfig) -> list[dict]:
    """Attach a triple-barrier outcome to each directional event."""
    bars = ft.resample_bars(df, cfg.timeframe)
    atr_series = fr.atr(bars, period=14)
    mids = df["mid"]
    out = []
    for e in events:
        direction = fr.event_direction(e["kind"])
        if direction is None:
            continue
        bar_ts = e["ts"].floor(cfg.timeframe)
        if bar_ts not in atr_series.index:
            continue
        atr_val = atr_series.loc[bar_ts]
        if pd.isna(atr_val) or atr_val <= 0:
            continue
        path = mids.loc[e["ts"]:]
        lab = fr.label_event(path, direction, float(atr_val), cfg)
        if lab is None:
            continue
        out.append({"ts": e["ts"], "kind": e["kind"], **lab})
    return out


def load_live_events(symbol: str) -> list[dict]:
    """Parse Stage-1.5 {day}_signals.jsonl feeds into [{ts, kind, price}]."""
    root = LIVE_DIR / symbol
    evs = []
    if not root.exists():
        return evs
    for p in sorted(root.glob("*_signals.jsonl")):
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            evs.append({"ts": pd.Timestamp(d["bar_ts"]), "kind": d["kind"],
                        "price": float(d["price"])})
    return evs


def _table(cells: list[dict]) -> str:
    hdr = (f"| {'kind':22} | {'dir':5} | {'n':>4} | {'exp_R':>6} | {'PF':>5} "
           f"| {'win%':>5} | {'totR':>7} | {'t':>5} | {'IS':>6} | {'OOS':>6} | verdict |")
    sep = "|" + "|".join("-" * len(c) for c in hdr.split("|")[1:-1]) + "|"
    rows = [hdr, sep]
    for c in cells:
        pf = "inf" if c["profit_factor"] == float("inf") else f"{c['profit_factor']:.2f}"
        rows.append(
            f"| {c['kind']:22} | {c['direction']:5} | {c['n']:>4} | "
            f"{c['expectancy']:>6.2f} | {pf:>5} | {c['win_rate']*100:>4.0f}% | "
            f"{c['total_R']:>7.1f} | {c['t_stat']:>5.2f} | {c['exp_is']:>6.2f} | "
            f"{c['exp_oos']:>6.2f} | {c['verdict']} |")
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Order-flow mark forward-return analyzer")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--timeframe", default="15min")
    p.add_argument("--sl-atr", type=float, default=1.0)
    p.add_argument("--tp-atr", type=float, default=2.0)
    p.add_argument("--max-hold", type=int, default=16)
    p.add_argument("--cost-pts", type=float, default=0.4)
    p.add_argument("--split-frac", type=float, default=0.7)
    args = p.parse_args()

    cfg = fr.LabelConfig(sl_atr=args.sl_atr, tp_atr=args.tp_atr,
                         max_hold_bars=args.max_hold, cost_pts=args.cost_pts,
                         timeframe=args.timeframe)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Fetching {args.symbol} ticks {start}..{end} …")
    ensure_ticks(args.symbol, start, end)
    df = ft.load_ticks(args.symbol, start, end)
    print(f"{len(df):,} ticks; reconstructing signals on {args.timeframe} …")

    hist = fr.summarize(label_all(df, reconstruct_events(df, args.timeframe), cfg),
                        split_frac=args.split_frac)
    live_raw = load_live_events(args.symbol)
    live = fr.summarize(label_all(df, live_raw, cfg), split_frac=args.split_frac) \
        if live_raw else {"boundary_ts": None, "cells": []}

    n_dir = len(hist["cells"])
    note = (f"{n_dir} directional cells tested; at p<0.05 expect ~{0.05*n_dir:.1f} "
            f"false positives by chance — treat a lone significant cell with suspicion.")
    print("\n=== HISTORICAL (reconstructed) ===")
    print(_table(hist["cells"]))
    print("\n" + note)
    if live["cells"]:
        print("\n=== LIVE cohort (real-time, thin OOS) ===")
        print(_table(live["cells"]))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w") as f:
        f.write(f"# Signal forward-return analysis — {args.symbol}\n\n")
        f.write(f"Range {start}..{end} · {args.timeframe} · triple-barrier "
                f"sl {args.sl_atr}×ATR / tp {args.tp_atr}×ATR / hold "
                f"{args.max_hold} bars / cost {args.cost_pts}pt/side · "
                f"IS/OOS split {args.split_frac:.0%} at {hist['boundary_ts']}\n\n")
        f.write("## Historical (reconstructed from tick history)\n\n")
        f.write(_table(hist["cells"]) + "\n\n")
        f.write(note + "\n\n")
        if live["cells"]:
            f.write("## Live cohort (Stage-1.5 feed, real-time, thin)\n\n")
            f.write(_table(live["cells"]) + "\n\n")
        cands = [c for c in hist["cells"] if c["verdict"] == "CANDIDATE"]
        f.write("## Bottom line\n\n")
        if cands:
            f.write("Candidate cell(s) that survived both halves + significance:\n")
            for c in cands:
                f.write(f"- **{c['kind']} {c['direction']}** — exp {c['expectancy']:.2f}R, "
                        f"PF {c['profit_factor']:.2f}, t {c['t_stat']:.2f}, n {c['n']}. "
                        f"Next: full backtest.md gate before any live use.\n")
        else:
            f.write("No cell cleared the CANDIDATE bar (n≥30/half, both halves "
                    "positive, t>2). On this sample the marks carry no tradeable "
                    "forward edge after costs — a valid, money-saving result.\n")
    print(f"\nReport written to {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Import smoke test**

Run: `./venv/bin/python -c "from scripts.analyze_signal_forward_returns import reconstruct_events, label_all, load_live_events; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 3: End-to-end smoke on real ticks already on disk**

The 2026-07-07..09 XAUUSD ticks were fetched in Stage 1. Run the analyzer over them:

```bash
./venv/bin/python scripts/analyze_signal_forward_returns.py --symbol XAUUSD \
    --start 2026-07-07 --end 2026-07-09 --timeframe 15min 2>&1 | tail -25
```

Expected: a HISTORICAL table with the 8 directional cells (most `thin` on a 3-day sample), the multiple-testing note, and `Report written to …/reports/signal_forward_returns.md`. No traceback. (A short range will mostly show `thin` — that is correct behavior, not a failure.)

- [ ] **Step 4: Verify the report file rendered**

```bash
./venv/bin/python -c "
from pathlib import Path
r = Path('reports/signal_forward_returns.md').read_text()
assert 'Historical' in r and 'Bottom line' in r and 'triple-barrier' in r
print('report OK,', len(r), 'chars')"
```

Expected: `report OK, <n> chars`.

- [ ] **Step 5: Commit** (message via file)

```
feat: signal forward-return analyzer script + markdown report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add scripts/analyze_signal_forward_returns.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 4: Final verification

- [ ] **Step 1: Full unit suite**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass, zero failures (no count regression vs run at task start).

- [ ] **Step 2: Confirm no live-system files touched**

```bash
git log --stat --oneline <base>..HEAD | grep -E "^\s+\S+\s+\|" | awk '{print $1}' | sort -u
```

Expected: ONLY `src/microstructure/forward_returns.py`, `scripts/analyze_signal_forward_returns.py`, `tests/unit/test_forward_returns.py` (plus the already-committed spec/plan docs) — nothing under `config/`, `src/strategies/`, `src/risk/`, `src/execution/`, and NOT `src/microstructure/features.py`.

- [ ] **Step 3: Real multi-week run (report to the user, not a gate)**

Run the analyzer over a real multi-week range for the actual verdict to relay:

```bash
./venv/bin/python scripts/analyze_signal_forward_returns.py --symbol XAUUSD \
    --start 2026-05-01 --end 2026-07-15 --timeframe 15min 2>&1 | tail -30
```

Relay the resulting table and bottom-line verdict in the final summary. `dead`/`thin` across the board is a real, reportable answer — do not tune params to change it.
