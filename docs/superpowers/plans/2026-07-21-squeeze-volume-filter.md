# Squeeze-Breakout Volume Filter (Smell-Test) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a research-only script that tests whether COMEX GC-futures volume (coil dry-up + break surge) separates winning from losing `squeeze_breakout` trades, emitting a GREEN/RED verdict on whether to buy multi-year GC data.

**Architecture:** Pure math (GC loader, RVOL features, native-geometry labeler, split stats) lives in `src/microstructure/squeeze_volume.py` and is unit-tested. Orchestration (reconstruct squeeze signals via the real strategy class, wire everything, write the report) lives in `scripts/research_squeeze_volume.py` and is run-verified. Zero production code is modified — `squeeze_breakout_strategy.py` and `forward_returns.py` are imported read-only.

**Tech Stack:** Python 3, pandas, numpy, yfinance (GC=F hourly), pytest.

## Global Constraints

- **Research-only. ZERO live-trading wiring.** No edits to `src/strategies/`, `config/`, `src/risk/`, `src/execution/`, or `src/microstructure/features.py`. New code only in `src/microstructure/squeeze_volume.py`, `scripts/research_squeeze_volume.py`, `tests/unit/test_squeeze_volume.py`.
- **Reuse, do not reimplement, the strategy.** Signals come from `SqueezeBreakoutStrategy` instantiated with its validated defaults (`{'enabled': True}` — every `config.get` default equals the shipped value: `sl_points=33`, `rr=2.0`, `htf_ema_period=400`).
- **Outcomes use the strategy's NATIVE geometry** — fixed 33pt SL / 66pt TP (RR2.0), not the generic ATR triple-barrier.
- **Cost = 0.5 pt/side** (`cost_pts=0.5`) — matches squeeze's cost-robust strict research figure.
- **Causal guard is mandatory:** `break_rvol` uses only GC hours whose full bar closed at/before the break timestamp — never the break's own in-progress hour.
- **yfinance GC hourly only** — daily GC volume is broken. Never read `GC=F` daily volume.
- **The verdict is a SPEND decision, not a trade decision.** ~12–18 trades cannot justify any live change; a clean split justifies buying data, nothing more. The report must say so.
- **Commit style:** end commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. New `.md` files under `docs/superpowers/` need `git add -f` (a broad `**/*.md` ignore rule covers that dir; existing specs are force-added).

---

### Task 1: GC hourly volume loader + cache

**Files:**
- Create: `src/microstructure/squeeze_volume.py`
- Test: `tests/unit/test_squeeze_volume.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_gc_hourly(start: date, end: date, cache_dir: Path | None = None, downloader=None) -> pd.Series` — returns a UTC-indexed hourly volume Series named `"volume"`. `downloader` is an injectable `Callable[[date, date], pd.DataFrame]` returning a frame with a `Volume` column and a DatetimeIndex (defaults to a yfinance wrapper); injectability is what makes it testable offline. Caches to `data/gc_futures/GC_1h_{start}_{end}.parquet`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_squeeze_volume.py
from datetime import date
from pathlib import Path

import pandas as pd

from src.microstructure import squeeze_volume as sv


def _fake_downloader(start, end):
    idx = pd.date_range("2026-05-08 00:00", periods=6, freq="1h", tz="UTC")
    return pd.DataFrame({"Volume": [100, 200, 300, 400, 500, 600]}, index=idx)


def test_load_gc_hourly_returns_utc_volume_series(tmp_path):
    s = sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                          cache_dir=tmp_path, downloader=_fake_downloader)
    assert isinstance(s, pd.Series)
    assert s.name == "volume"
    assert str(s.index.tz) == "UTC"
    assert list(s.values) == [100, 200, 300, 400, 500, 600]


def test_load_gc_hourly_caches_and_reuses(tmp_path):
    sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                      cache_dir=tmp_path, downloader=_fake_downloader)
    cache = tmp_path / "GC_1h_2026-05-08_2026-05-08.parquet"
    assert cache.exists()

    def _boom(start, end):
        raise AssertionError("downloader must not be called when cache exists")

    s = sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                          cache_dir=tmp_path, downloader=_boom)
    assert list(s.values) == [100, 200, 300, 400, 500, 600]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.microstructure.squeeze_volume'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/microstructure/squeeze_volume.py
"""
Squeeze-breakout volume-filter smell-test — pure helpers.

GC-futures relative volume (coil dry-up + break surge) as a confirmation
filter on `squeeze_breakout`. Research-only; decides whether to BUY multi-year
GC data, never whether to trade. See
docs/superpowers/specs/2026-07-21-squeeze-volume-filter-design.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GC_CACHE = PROJECT_ROOT / "data" / "gc_futures"


def _yf_download(start: date, end: date) -> pd.DataFrame:
    import yfinance as yf
    # yfinance `end` is exclusive → +1 day to include the last day.
    return yf.Ticker("GC=F").history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
        interval="1h",
    )


def load_gc_hourly(start: date, end: date, cache_dir: Path | None = None,
                   downloader: Callable[[date, date], pd.DataFrame] | None = None
                   ) -> pd.Series:
    """UTC-indexed hourly GC=F volume. Cached; hourly ONLY (daily is broken)."""
    cache_dir = cache_dir or GC_CACHE
    cache = cache_dir / f"GC_1h_{start.isoformat()}_{end.isoformat()}.parquet"
    if cache.exists():
        s = pd.read_parquet(cache)["volume"]
        s.index = pd.to_datetime(s.index, utc=True)
        s.name = "volume"
        return s
    df = (downloader or _yf_download)(start, end)
    if df is None or df.empty or "Volume" not in df.columns:
        raise ValueError(f"no GC hourly volume for {start}..{end}")
    vol = df["Volume"].copy()
    vol.index = pd.to_datetime(vol.index, utc=True)
    vol = vol[vol.index.notna()]
    vol.name = "volume"
    cache_dir.mkdir(parents=True, exist_ok=True)
    vol.to_frame().to_parquet(cache)
    return vol
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/squeeze_volume.py tests/unit/test_squeeze_volume.py
git commit -F <scratch-msg-file>   # msg: "feat: GC hourly volume loader for squeeze filter smell-test"
```

---

### Task 2: RVOL features (coil_rvol, break_rvol) with causal guard

**Files:**
- Modify: `src/microstructure/squeeze_volume.py`
- Test: `tests/unit/test_squeeze_volume.py`

**Interfaces:**
- Consumes: a UTC hourly volume Series from Task 1.
- Produces:
  - `completed_before(vol: pd.Series, break_ts: pd.Timestamp) -> pd.Series` — the subset of `vol` whose full hour bar has closed by `break_ts` (index label is the hour START, so hour `H` is complete when `H + 1h <= break_ts`).
  - `break_rvol(vol, break_ts, baseline_hours: int = 6) -> float` — last completed hour's volume ÷ mean of the `baseline_hours` completed hours before it. `nan` if insufficient history.
  - `coil_rvol(vol, break_ts, coil_hours: int = 2, baseline_hours: int = 12) -> float` — mean of the `coil_hours` completed hours before the break ÷ mean of the `baseline_hours` hours before THAT window. `nan` if insufficient history.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_squeeze_volume.py
import numpy as np


def _hourly(vals, start="2026-05-08 00:00"):
    idx = pd.date_range(start, periods=len(vals), freq="1h", tz="UTC")
    return pd.Series(vals, index=idx, name="volume")


def test_break_rvol_surge_above_one():
    # baseline hours ~100, last completed hour spikes to 300 → rvol ~3
    vol = _hourly([100, 100, 100, 100, 100, 100, 300])
    # break at 07:20 → last completed hour is 06:00 (spike); 00:00-05:00 baseline
    ts = pd.Timestamp("2026-05-08 07:20", tz="UTC")
    r = sv.break_rvol(vol, ts, baseline_hours=6)
    assert r == pytest.approx(3.0, rel=1e-6)


def test_break_rvol_causal_guard_ignores_break_hour():
    # A massive spike sits in the break's OWN hour (07:00). It must be excluded.
    vol = _hourly([100, 100, 100, 100, 100, 100, 100, 99999])
    ts = pd.Timestamp("2026-05-08 07:20", tz="UTC")  # 07:00 hour still open
    r = sv.break_rvol(vol, ts, baseline_hours=6)
    # last completed hour is 06:00 (value 100) over 00:00-05:00 baseline (100) → 1.0
    assert r == pytest.approx(1.0, rel=1e-6)
    # sanity: the 99999 hour would have blown this up if it leaked
    assert r < 2.0


def test_coil_rvol_dryup_below_one():
    # baseline hours 200, coil hours drop to 50 → rvol 0.25
    vals = [200] * 12 + [50, 50]
    vol = _hourly(vals)
    ts = pd.Timestamp("2026-05-08 14:20", tz="UTC")  # 14:00 open; 13:00 last complete
    r = sv.coil_rvol(vol, ts, coil_hours=2, baseline_hours=12)
    assert r == pytest.approx(0.25, rel=1e-6)


def test_rvol_nan_on_insufficient_history():
    vol = _hourly([100, 100])
    ts = pd.Timestamp("2026-05-08 05:20", tz="UTC")
    assert np.isnan(sv.break_rvol(vol, ts, baseline_hours=6))
    assert np.isnan(sv.coil_rvol(vol, ts, coil_hours=2, baseline_hours=12))
```

Add `import pytest` at the top of the test file if not already present.

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'break_rvol'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/microstructure/squeeze_volume.py
import numpy as np


def completed_before(vol: pd.Series, break_ts: pd.Timestamp) -> pd.Series:
    """Hours whose full bar closed by break_ts (index label = hour start)."""
    return vol[(vol.index + pd.Timedelta("1h")) <= break_ts]


def break_rvol(vol: pd.Series, break_ts: pd.Timestamp,
               baseline_hours: int = 6) -> float:
    c = completed_before(vol, break_ts)
    if len(c) < baseline_hours + 1:
        return float("nan")
    last = float(c.iloc[-1])
    base = float(c.iloc[-(baseline_hours + 1):-1].mean())
    if base <= 0:
        return float("nan")
    return last / base


def coil_rvol(vol: pd.Series, break_ts: pd.Timestamp,
              coil_hours: int = 2, baseline_hours: int = 12) -> float:
    c = completed_before(vol, break_ts)
    if len(c) < coil_hours + baseline_hours:
        return float("nan")
    coil = float(c.iloc[-coil_hours:].mean())
    base = float(c.iloc[-(coil_hours + baseline_hours):-coil_hours].mean())
    if base <= 0:
        return float("nan")
    return coil / base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/squeeze_volume.py tests/unit/test_squeeze_volume.py
git commit -F <scratch-msg-file>   # msg: "feat: causal coil/break RVOL features"
```

---

### Task 3: Native-geometry labeler

**Files:**
- Modify: `src/microstructure/squeeze_volume.py`
- Test: `tests/unit/test_squeeze_volume.py`

**Interfaces:**
- Consumes: a chronological mid-price path (`pd.Series` of floats) starting at/after entry.
- Produces: `label_native(mids: pd.Series, side: str, entry: float, stop: float, target: float, cost_pts: float = 0.5, rr: float = 2.0) -> dict | None` — walks ticks chronologically; first barrier touched decides the outcome (an intrabar stop-before-target counts as the STOP because ticks are ordered). Returns `{"R": float, "outcome": "target"|"stop"|"timeout"}`, R net of round-trip cost. `side` is `"BUY"` or `"SELL"`. `None` if the path is empty or risk is non-positive.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_squeeze_volume.py
def _path(vals):
    idx = pd.date_range("2026-05-08 10:00", periods=len(vals), freq="1min", tz="UTC")
    return pd.Series(vals, index=idx)


def test_label_native_buy_hits_target():
    lab = sv.label_native(_path([2000, 2010, 2033]), "BUY",
                          entry=2000, stop=1967, target=2066, cost_pts=0.0)
    assert lab["outcome"] == "target"
    assert lab["R"] == pytest.approx(2.0)


def test_label_native_buy_hits_stop():
    lab = sv.label_native(_path([2000, 1980, 1967]), "BUY",
                          entry=2000, stop=1967, target=2066, cost_pts=0.0)
    assert lab["outcome"] == "stop"
    assert lab["R"] == pytest.approx(-1.0)


def test_label_native_stop_before_target_is_stop():
    # path dips to the stop, then rockets past target — the stop came first
    lab = sv.label_native(_path([2000, 1966, 2100]), "BUY",
                          entry=2000, stop=1967, target=2066, cost_pts=0.0)
    assert lab["outcome"] == "stop"


def test_label_native_sell_symmetry():
    lab = sv.label_native(_path([2000, 1990, 1934]), "SELL",
                          entry=2000, stop=2033, target=1934, cost_pts=0.0)
    assert lab["outcome"] == "target"
    assert lab["R"] == pytest.approx(2.0)


def test_label_native_cost_reduces_R():
    # risk = 33 pts; cost 0.5/side → 1.0 pt round trip = 1/33 R off the top
    lab = sv.label_native(_path([2000, 2066]), "BUY",
                          entry=2000, stop=1967, target=2066, cost_pts=0.5)
    assert lab["R"] == pytest.approx(2.0 - (1.0 / 33.0), rel=1e-6)


def test_label_native_timeout_marks_to_market():
    lab = sv.label_native(_path([2000, 2016.5]), "BUY",
                          entry=2000, stop=1967, target=2066, cost_pts=0.0)
    assert lab["outcome"] == "timeout"
    assert lab["R"] == pytest.approx(16.5 / 33.0, rel=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: FAIL — `AttributeError: ... 'label_native'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/microstructure/squeeze_volume.py
def label_native(mids: pd.Series, side: str, entry: float, stop: float,
                 target: float, cost_pts: float = 0.5, rr: float = 2.0
                 ) -> dict | None:
    """Fixed-geometry triple-barrier over an ordered mid path. Stop wins ties."""
    if len(mids) == 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    cost_R = (2.0 * cost_pts) / risk
    for px in mids.values:
        px = float(px)
        if side == "BUY":
            if px <= stop:
                return {"R": -1.0 - cost_R, "outcome": "stop"}
            if px >= target:
                return {"R": rr - cost_R, "outcome": "target"}
        else:
            if px >= stop:
                return {"R": -1.0 - cost_R, "outcome": "stop"}
            if px <= target:
                return {"R": rr - cost_R, "outcome": "target"}
    last = float(mids.iloc[-1])
    move = (last - entry) if side == "BUY" else (entry - last)
    return {"R": move / risk - cost_R, "outcome": "timeout"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/squeeze_volume.py tests/unit/test_squeeze_volume.py
git commit -F <scratch-msg-file>   # msg: "feat: native fixed-geometry labeler for squeeze trades"
```

---

### Task 4: Split-stats + verdict

**Files:**
- Modify: `src/microstructure/squeeze_volume.py`
- Test: `tests/unit/test_squeeze_volume.py`

**Interfaces:**
- Consumes: a list of labeled-trade dicts, each `{"R": float, "side": "BUY"|"SELL", "break_rvol": float, "coil_rvol": float}`.
- Produces:
  - `split_stats(trades: list[dict], feature: str) -> dict` — median-splits `trades` on `feature`; returns `{"median": float, "high": {"n","win","mean_R"}, "low": {"n","win","mean_R"}}`. Trades with a `nan` feature are dropped. `win` = fraction with `R > 0`. Empty buckets report `n=0, win=0.0, mean_R=0.0`.
  - `verdict(trades: list[dict], feature: str = "break_rvol", margin: float = 0.15, min_n: int = 3) -> str` — `"GREEN"` if both buckets have `n >= min_n` and `high.mean_R - low.mean_R >= margin`; else `"RED"`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/test_squeeze_volume.py
def _tr(R, side, brv, crv=1.0):
    return {"R": R, "side": side, "break_rvol": brv, "coil_rvol": crv}


def test_split_stats_median_and_buckets():
    trades = [_tr(2.0, "BUY", 3.0), _tr(1.5, "BUY", 2.5),
              _tr(-1.0, "SELL", 0.5), _tr(-1.0, "SELL", 0.7)]
    s = sv.split_stats(trades, "break_rvol")
    assert s["median"] == pytest.approx(1.6)  # median of [0.5,0.7,2.5,3.0]
    assert s["high"]["n"] == 2 and s["high"]["mean_R"] == pytest.approx(1.75)
    assert s["low"]["n"] == 2 and s["low"]["mean_R"] == pytest.approx(-1.0)
    assert s["high"]["win"] == pytest.approx(1.0)
    assert s["low"]["win"] == pytest.approx(0.0)


def test_split_stats_drops_nan_feature():
    trades = [_tr(2.0, "BUY", float("nan")), _tr(1.0, "BUY", 2.0),
              _tr(-1.0, "SELL", 0.5)]
    s = sv.split_stats(trades, "break_rvol")
    assert s["high"]["n"] + s["low"]["n"] == 2


def test_verdict_green_when_high_bucket_outperforms():
    trades = [_tr(2.0, "BUY", 3.0), _tr(1.5, "BUY", 2.5),
              _tr(-1.0, "SELL", 0.5), _tr(-1.0, "SELL", 0.7),
              _tr(1.0, "BUY", 2.2), _tr(-1.0, "SELL", 0.6)]
    assert sv.verdict(trades, "break_rvol") == "GREEN"


def test_verdict_red_when_flat():
    trades = [_tr(1.0, "BUY", 3.0), _tr(-1.0, "BUY", 2.5),
              _tr(1.0, "SELL", 0.5), _tr(-1.0, "SELL", 0.7)]
    assert sv.verdict(trades, "break_rvol") == "RED"


def test_verdict_red_when_too_few():
    trades = [_tr(2.0, "BUY", 3.0), _tr(-1.0, "SELL", 0.5)]
    assert sv.verdict(trades, "break_rvol") == "RED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: FAIL — `AttributeError: ... 'split_stats'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/microstructure/squeeze_volume.py
def _bucket(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win": 0.0, "mean_R": 0.0}
    rs = [t["R"] for t in trades]
    return {"n": n,
            "win": sum(1 for r in rs if r > 0) / n,
            "mean_R": sum(rs) / n}


def split_stats(trades: list[dict], feature: str) -> dict:
    vals = [t for t in trades if not np.isnan(t.get(feature, float("nan")))]
    if not vals:
        return {"median": float("nan"), "high": _bucket([]), "low": _bucket([])}
    median = float(np.median([t[feature] for t in vals]))
    high = [t for t in vals if t[feature] >= median]
    low = [t for t in vals if t[feature] < median]
    return {"median": median, "high": _bucket(high), "low": _bucket(low)}


def verdict(trades: list[dict], feature: str = "break_rvol",
            margin: float = 0.15, min_n: int = 3) -> str:
    s = split_stats(trades, feature)
    hi, lo = s["high"], s["low"]
    if hi["n"] >= min_n and lo["n"] >= min_n \
            and hi["mean_R"] - lo["mean_R"] >= margin:
        return "GREEN"
    return "RED"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_squeeze_volume.py -q`
Expected: PASS (17 passed).

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/squeeze_volume.py tests/unit/test_squeeze_volume.py
git commit -F <scratch-msg-file>   # msg: "feat: median-split stats + GREEN/RED verdict"
```

---

### Task 5: Orchestration script + report + real run

**Files:**
- Create: `scripts/research_squeeze_volume.py`
- Uses (read-only): `src/microstructure/squeeze_volume.py`, `src/microstructure/features.py`, `src/strategies/squeeze_breakout_strategy.py`, `src/core/types.py`.

**Interfaces:**
- Consumes: everything produced in Tasks 1–4.
- Produces: a CLI that writes `reports/squeeze_volume_smell_test.md` and prints the split tables + verdict. `reconstruct_squeeze_signals(bars15: pd.DataFrame) -> list[dict]` returns `[{ts, side, entry, stop, target}]` by replaying the real strategy over a growing window.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""
Squeeze-breakout volume-filter smell-test (front half, GC free data).

Reconstructs squeeze_breakout signals on local XAUUSD ticks, attaches causal
GC coil/break relative volume to each, labels with the strategy's native
33pt/66pt geometry, and reports the win/R split by volume bucket with a
GREEN/RED verdict. GREEN = worth BUYING multi-year GC data; it is NOT a live
signal (~12-18 trades). See the design spec dated 2026-07-21.

    ./venv/bin/python scripts/research_squeeze_volume.py \
        --start 2026-05-08 --end 2026-07-15
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.types import Symbol  # noqa: E402
from src.microstructure import features as ft  # noqa: E402
from src.microstructure import squeeze_volume as sv  # noqa: E402
from src.strategies.squeeze_breakout_strategy import (  # noqa: E402
    SqueezeBreakoutStrategy)

REPORT = PROJECT_ROOT / "reports" / "squeeze_volume_smell_test.md"


def reconstruct_squeeze_signals(bars15: pd.DataFrame) -> list[dict]:
    """Replay the REAL strategy over a growing window (cooldown latch intact)."""
    strat = SqueezeBreakoutStrategy(Symbol(ticker="XAUUSD"), {"enabled": True})
    min_bars = max(strat.pct_window + strat.donch + 5, strat.htf_ema_period)
    sigs = []
    for i in range(min_bars, len(bars15) + 1):
        s = strat.on_bar(bars15.iloc[:i])
        if s is not None:
            sigs.append({
                "ts": pd.Timestamp(s.timestamp),
                "side": s.side.value if hasattr(s.side, "value") else str(s.side),
                "entry": float(s.entry_price),
                "stop": float(s.stop_loss),
                "target": float(s.take_profit),
            })
    return sigs


def _fmt_bucket(name: str, b: dict) -> str:
    return (f"| {name:5} | {b['n']:>3} | {b['win']*100:>4.0f}% "
            f"| {b['mean_R']:>+6.2f} |")


def _split_table(title: str, s: dict) -> str:
    rows = [f"**{title}** (median {s['median']:.2f})",
            "", "| bkt | n | win% | mean_R |", "|-----|---|------|--------|",
            _fmt_bucket("high", s["high"]), _fmt_bucket("low", s["low"]), ""]
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Squeeze volume-filter smell-test")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--cost-pts", type=float, default=0.5)
    args = p.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Loading {args.symbol} ticks {start}..{end} …")
    df = ft.load_ticks(args.symbol, start, end)
    bars15 = ft.resample_bars(df, "15min")
    mids = df["mid"]
    print(f"{len(df):,} ticks, {len(bars15):,} 15m bars; reconstructing signals …")
    sigs = reconstruct_squeeze_signals(bars15)
    print(f"{len(sigs)} squeeze breakouts")

    print("Loading GC hourly volume …")
    gc = sv.load_gc_hourly(start, end)

    trades = []
    for s in sigs:
        ts = s["ts"]
        lab = sv.label_native(mids.loc[ts:], s["side"], s["entry"],
                              s["stop"], s["target"], cost_pts=args.cost_pts)
        if lab is None:
            continue
        trades.append({
            "side": s["side"], "R": lab["R"], "outcome": lab["outcome"],
            "break_rvol": sv.break_rvol(gc, ts),
            "coil_rvol": sv.coil_rvol(gc, ts),
        })

    sells = [t for t in trades if t["side"] == "SELL"]
    brk = sv.split_stats(trades, "break_rvol")
    coil = sv.split_stats(trades, "coil_rvol")
    sell_brk = sv.split_stats(sells, "break_rvol")
    vdt = sv.verdict(trades, "break_rvol")

    body = [
        f"# Squeeze-breakout volume-filter smell-test — {args.symbol}",
        "",
        f"Range {start}..{end} · {len(trades)} labeled squeeze breakouts "
        f"({len(sells)} SELL) · native 33/66pt geometry · cost "
        f"{args.cost_pts}pt/side · GC=F hourly volume.",
        "",
        "## Break RVOL split (all trades)", "", _split_table("break_rvol", brk),
        "## Coil RVOL split (all trades)", "", _split_table("coil_rvol", coil),
        "## Break RVOL split (SELL only — the bleed)", "",
        _split_table("break_rvol · SELL", sell_brk),
        "## Verdict", "",
        f"**{vdt}** on break_rvol.",
        "",
        "⚠️ This sample is ~12–18 trades. A clean split can occur by chance, so "
        "GREEN justifies BUYING multi-year GC data for a proper every-year test "
        "— it does NOT justify any live change. RED = drop the hypothesis.",
        "",
        "Caveats: GC is COMEX futures (not spot XAUUSD; ~23h session, maintenance "
        "break) — volume used only as a relative percentile. yfinance GC daily "
        "volume is broken; hourly only. 1h volume is coarser than the 15m break; "
        "break_rvol uses the last COMPLETED hour (causal, lagged ≤1h).",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(body))
    print("\n".join(body))
    print(f"\nReport written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Syntax/import check**

Run: `./venv/bin/python -c "import ast; ast.parse(open('scripts/research_squeeze_volume.py').read()); print('parse OK')"`
Expected: `parse OK`.

- [ ] **Step 3: Real run over the free-data overlap**

Run: `./venv/bin/python scripts/research_squeeze_volume.py --start 2026-05-08 --end 2026-07-15 2>&1 | tail -40`
Expected: prints tick/bar counts, a signal count (~12–18), three split tables, and a `**GREEN**`/`**RED**` verdict; writes `reports/squeeze_volume_smell_test.md`. (Requires network for the first GC fetch; it caches to `data/gc_futures/`.) If `load_ticks` raises for a missing day, that is a data-coverage issue, not a code bug — note which day and continue.

- [ ] **Step 4: Commit**

```bash
git add scripts/research_squeeze_volume.py reports/squeeze_volume_smell_test.md
git commit -F <scratch-msg-file>   # msg: "feat: squeeze volume-filter smell-test script + report"
```

---

### Task 6: Final verification

- [ ] **Step 1: Full unit suite**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass (489 baseline + 17 new = 506), zero failures.

- [ ] **Step 2: Confirm no production files touched**

```bash
git diff --stat <base>..HEAD | grep -E "config/|src/strategies/|src/risk/|src/execution/|features\.py" || echo "clean — no production files touched"
```
Expected: `clean — no production files touched`.

- [ ] **Step 3: Relay the verdict**

Report the split tables and the GREEN/RED verdict to the user as the deliverable. A RED verdict is a valid, money-saving result — do NOT tune params to force GREEN. Whatever the split says is the answer.

---

## Self-Review

**Spec coverage:**
- GC hourly loader (spec §Architecture 2) → Task 1. ✓
- Coil/break RVOL causal features (spec §Hypothesis, §Architecture 3) → Task 2, incl. the mandatory causal-guard test. ✓
- Native 33/66pt outcome labeler (spec §Architecture 4) → Task 3. ✓
- Median-split bucket stats + SELL cut + verdict (spec §Architecture 5, §Goal) → Task 4 + Task 5 report. ✓
- Signal reconstruction via the real class (spec §Architecture 1) → Task 5. ✓
- Report with data caveats + spend-not-trade framing (spec §Goal, §Data caveats) → Task 5 report body. ✓
- Research-only, zero production edits (spec §Out of scope, Global Constraints) → Task 6 Step 2 guard. ✓
- Median bucket boundary (spec §Report clarification) → Task 4 `split_stats`. ✓

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `load_gc_hourly`→Series consumed by `break_rvol`/`coil_rvol`; `label_native`→`{"R","outcome"}` consumed in Task 5's trade dict; `split_stats`/`verdict` consume `{"R","side","break_rvol","coil_rvol"}` built in Task 5; `reconstruct_squeeze_signals`→`{ts,side,entry,stop,target}` all consumed in `main`. `side` is normalized to the string `"BUY"`/`"SELL"` in Task 5 and matched as such in `label_native`. Consistent.
