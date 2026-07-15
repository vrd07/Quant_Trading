# Order-Flow Marking Tool (Stage 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interactive historical order-flow marking tool for XAUUSD — Dukascopy tick fetcher → pure-function microstructure feature library → Plotly Dash viewer that draws proxy delta, heatmap, and event marks. Research-only; nothing wired into live trading.

**Architecture:** Three layers with one-way dependencies: `scripts/fetch_dukascopy_ticks.py` (network → per-day Parquet under `data/ticks/`), `src/microstructure/features.py` (pure functions: ticks in, arrays/`FlowEvent`s out — no I/O except `load_ticks`, no ML, no state), `scripts/orderflow_viewer.py` (Dash app consuming both). Stage 2 ML would consume `features.py` unchanged.

**Tech Stack:** Python 3.11 venv (`./venv/bin/python`), pandas/numpy, pyarrow (new dep), plotly 6.8 + dash 4.2 (already in venv), pytest.

**Spec:** `docs/superpowers/specs/2026-07-15-orderflow-marking-design.md`

## Global Constraints

- Run everything with the repo venv: `./venv/bin/python`, `./venv/bin/pytest`. `pytest.ini` sets `pythonpath = .` so imports are `from src.microstructure...` / `from scripts...` from repo root.
- All order-flow quantities are PROXIES from Dukascopy quote ticks (bid/ask + indicative liquidity). Never name anything "true volume"/"DOM". Docstrings must say "proxy".
- Do NOT touch the canonical 5m CSV pipeline (`refresh_historical_data.py`, `data/historical/`), any `config_live_*.yaml`, strategy registry, or risk engine. No strategy-propagation checklist applies.
- All detector thresholds are kwargs with defaults; nothing hard-coded inside function bodies.
- Tick store: `data/ticks/{SYMBOL}/YYYY-MM-DD.parquet`, columns `ts, bid, ask, bid_vol, ask_vol`. Days immutable; re-fetch overwrites.
- Tick Parquets must never be committed: Task 1 adds `data/ticks/` to `.gitignore` (it is NOT covered today). The repo `.gitignore` ignores `**/*.md` — commit plan/spec docs with `git add -f` only.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

---

### Task 1: Dukascopy tick fetcher + Parquet store

**Files:**
- Create: `scripts/fetch_dukascopy_ticks.py`
- Test: `tests/unit/test_fetch_dukascopy_ticks.py`
- Modify: `requirements.txt` (append pyarrow line)

**Interfaces:**
- Consumes: `DEFAULT_POINTS` dict from `scripts/fetch_dukascopy.py` (exists; `XAUUSD: 0.001`).
- Produces (used by Tasks 2 & 6):
  - `decode_bi5(raw: bytes, base_ts: datetime, point: float) -> pd.DataFrame` — columns `ts, bid, ask, bid_vol, ask_vol`
  - `day_path(symbol: str, day: date, ticks_dir: Path | None = None) -> Path`
  - `ensure_ticks(symbol: str, start: date, end: date, point: float | None = None, ticks_dir: Path | None = None, workers: int = 6) -> list[Path]`
  - `TICKS_DIR: Path` (= `<repo>/data/ticks`)

- [ ] **Step 1: Install pyarrow and record the dependency**

```bash
./venv/bin/pip install "pyarrow>=15.0.0"
```

Append to `requirements.txt`:

```
pyarrow>=15.0.0            # tick Parquet store (order-flow research)
```

Append to `.gitignore` (tick store is fetch-on-demand, never committed):

```
data/ticks/
```

- [ ] **Step 2: Write the failing decode tests**

Create `tests/unit/test_fetch_dukascopy_ticks.py`:

```python
"""Unit tests for the Dukascopy tick decoder — no network."""
import lzma
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from scripts.fetch_dukascopy_ticks import TICK_RECORD, day_path, decode_bi5


def test_decode_bi5_two_ticks():
    base = datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
    # Records are big-endian: offset_ms, ask_points, bid_points, ask_vol, bid_vol
    raw = TICK_RECORD.pack(250, 3350120, 3350050, 1.25, 2.5) + \
          TICK_RECORD.pack(1500, 3350200, 3350150, 0.5, 0.75)
    df = decode_bi5(lzma.compress(raw), base, point=0.001)
    assert list(df.columns) == ["ts", "bid", "ask", "bid_vol", "ask_vol"]
    assert len(df) == 2
    assert df.loc[0, "ts"] == pd.Timestamp("2026-07-01 09:00:00.250", tz="UTC")
    assert df.loc[0, "bid"] == pytest.approx(3350.050)
    assert df.loc[0, "ask"] == pytest.approx(3350.120)
    assert df.loc[0, "bid_vol"] == pytest.approx(2.5)
    assert df.loc[1, "ask_vol"] == pytest.approx(0.5)


def test_decode_bi5_empty_hour():
    base = datetime(2026, 7, 1, 3, tzinfo=timezone.utc)
    df = decode_bi5(lzma.compress(b""), base, point=0.001)
    assert df.empty
    assert list(df.columns) == ["ts", "bid", "ask", "bid_vol", "ask_vol"]


def test_day_path_layout(tmp_path):
    p = day_path("XAUUSD", date(2026, 7, 1), ticks_dir=tmp_path)
    assert p == tmp_path / "XAUUSD" / "2026-07-01.parquet"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./venv/bin/pytest tests/unit/test_fetch_dukascopy_ticks.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'scripts.fetch_dukascopy_ticks'`

- [ ] **Step 4: Write the fetcher**

Create `scripts/fetch_dukascopy_ticks.py`:

```python
#!/usr/bin/env python3
"""
Fetch Dukascopy per-hour TICK data (bid/ask quotes + indicative liquidity)
into per-day Parquet files: data/ticks/{SYMBOL}/YYYY-MM-DD.parquet.

Research-only store for the order-flow marking tool (spec:
docs/superpowers/specs/2026-07-15-orderflow-marking-design.md). Parallel to —
and never touching — the canonical 5m CSV pipeline.

Tick file URL: {BASE}/{SYMBOL}/{yyyy}/{mm-1:02d}/{dd:02d}/{HH}h_ticks.bi5
LZMA-compressed, 20-byte big-endian records:
    offset_ms uint32, ask_points uint32, bid_points uint32,
    ask_vol float32, bid_vol float32
Volumes are Dukascopy's indicative liquidity — proxy weights, NOT true traded
size (spot gold has no consolidated tape).

Usage:
    python scripts/fetch_dukascopy_ticks.py --symbol XAUUSD --start 2026-06-30 --end 2026-07-04
"""
import argparse
import lzma
import struct
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_dukascopy import DEFAULT_POINTS  # noqa: E402

BASE = "https://datafeed.dukascopy.com/datafeed"
TICK_RECORD = struct.Struct(">IIIff")  # offset_ms, ask_pts, bid_pts, ask_vol, bid_vol
TICKS_DIR = PROJECT_ROOT / "data" / "ticks"


def decode_bi5(raw: bytes, base_ts: datetime, point: float) -> pd.DataFrame:
    """Decode one LZMA hour-of-ticks blob. Pure; empty input -> empty frame."""
    data = lzma.decompress(raw) if raw else b""
    rows = [
        (base_ts + timedelta(milliseconds=off), bid * point, ask * point, bid_vol, ask_vol)
        for (off, ask, bid, ask_vol, bid_vol) in TICK_RECORD.iter_unpack(data)
    ]
    return pd.DataFrame(rows, columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])


def day_path(symbol: str, day: date, ticks_dir: Path | None = None) -> Path:
    return (ticks_dir or TICKS_DIR) / symbol / f"{day.isoformat()}.parquet"


def fetch_hour(symbol: str, day: date, hour: int, point: float,
               retries: int = 4) -> pd.DataFrame | None:
    url = (f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/"
           f"{hour:02d}h_ticks.bi5")
    raw = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # hour not published
            if attempt == retries - 1:
                print(f"  ⚠️ {day} {hour:02d}h fetch failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ⚠️ {day} {hour:02d}h fetch failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))
    if not raw:
        return None  # closed-market hour — empty body
    base_ts = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
    df = decode_bi5(raw, base_ts, point)
    return df if not df.empty else None


def fetch_day_ticks(symbol: str, day: date, point: float,
                    workers: int = 6) -> pd.DataFrame | None:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        frames = [df for df in ex.map(
            lambda h: fetch_hour(symbol, day, h, point), range(24)) if df is not None]
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)


def ensure_ticks(symbol: str, start: date, end: date, point: float | None = None,
                 ticks_dir: Path | None = None, workers: int = 6) -> list[Path]:
    """Fetch any missing days in [start, end]; return paths that exist after."""
    point = point or DEFAULT_POINTS.get(symbol)
    if point is None:
        raise ValueError(f"unknown point size for {symbol!r} — pass point=")
    paths = []
    day = start
    while day <= end:
        if day.weekday() != 5:  # Saturday: FX closed all day
            p = day_path(symbol, day, ticks_dir)
            if not p.exists():
                df = fetch_day_ticks(symbol, day, point, workers)
                if df is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    df.to_parquet(p, index=False)
                    print(f"  ✅ {day}: {len(df):,} ticks → {p.name}")
            if p.exists():
                paths.append(p)
        day += timedelta(days=1)
    return paths


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch Dukascopy ticks → per-day Parquet")
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--point", type=float, default=None)
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()
    paths = ensure_ticks(args.symbol, date.fromisoformat(args.start),
                         date.fromisoformat(args.end), args.point,
                         workers=args.workers)
    if not paths:
        print("❌ no tick data fetched")
        return 1
    print(f"✅ {args.symbol}: {len(paths)} day files under {paths[0].parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_fetch_dukascopy_ticks.py -v`
Expected: 3 PASS

- [ ] **Step 6: Live verification — fetch one real day**

```bash
./venv/bin/python scripts/fetch_dukascopy_ticks.py --symbol XAUUSD --start 2026-07-09 --end 2026-07-09
./venv/bin/python -c "
import pandas as pd
df = pd.read_parquet('data/ticks/XAUUSD/2026-07-09.parquet')
print(len(df), 'ticks'); print(df.head(3)); print(df.ts.min(), '→', df.ts.max())
assert len(df) > 50_000 and (df.ask >= df.bid).all()
print('OK')"
```

Expected: 6-figure tick count, sane XAUUSD prices (~thousands), `OK`. (If Dukascopy hasn't published that day yet, use an earlier weekday.)

- [ ] **Step 7: Commit**

```bash
git add scripts/fetch_dukascopy_ticks.py tests/unit/test_fetch_dukascopy_ticks.py requirements.txt .gitignore
git commit -m "feat: Dukascopy tick fetcher with per-day Parquet store

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Microstructure package — loader + core transforms

**Files:**
- Create: `src/microstructure/__init__.py` (empty)
- Create: `src/microstructure/features.py`
- Test: `tests/unit/test_microstructure_features.py`

**Interfaces:**
- Consumes: Parquet layout from Task 1 (`ts, bid, ask, bid_vol, ask_vol`).
- Produces (used by Tasks 3–6). All functions take a tick DataFrame **indexed by UTC `ts`** with columns `bid, ask, bid_vol, ask_vol, mid, spread` (as returned by `load_ticks`):
  - `load_ticks(symbol: str, start: date, end: date, ticks_dir: Path | None = None) -> pd.DataFrame`
  - `sign_ticks(df) -> pd.Series` (+1/−1/0 per tick)
  - `signed_flow(df) -> pd.Series`
  - `cumulative_delta(df) -> pd.Series`
  - `resample_bars(df, freq: str = "5min") -> pd.DataFrame` (columns `open, high, low, close, ticks`)
  - `bar_delta(df, freq: str = "5min") -> pd.DataFrame` (columns `delta, cum_delta`)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_microstructure_features.py`:

```python
"""Unit tests for src/microstructure/features.py — synthetic ticks only."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import features as ft


def make_ticks(mids, start="2026-07-01 09:00", freq="1s", vol=1.0):
    """Synthetic tick frame in load_ticks() shape (UTC ts index, mid/spread)."""
    idx = pd.date_range(start, periods=len(mids), freq=freq, tz="UTC")
    mid = pd.Series(list(mids), index=idx, dtype=float)
    df = pd.DataFrame({
        "bid": mid - 0.05, "ask": mid + 0.05,
        "bid_vol": float(vol), "ask_vol": float(vol),
    })
    df["mid"] = mid
    df["spread"] = df["ask"] - df["bid"]
    return df


class TestCoreTransforms:
    def test_sign_ticks_tick_rule(self):
        df = make_ticks([100.0, 100.1, 100.1, 100.05])
        # first tick has no prior -> 0; unchanged inherits previous sign
        assert ft.sign_ticks(df).tolist() == [0.0, 1.0, 1.0, -1.0]

    def test_cumulative_delta(self):
        df = make_ticks([100.0, 100.1, 100.1, 100.05], vol=1.0)
        # flow = sign * (bid_vol + ask_vol) = sign * 2
        assert ft.cumulative_delta(df).tolist() == [0.0, 2.0, 4.0, 2.0]

    def test_resample_bars_ohlc(self):
        df = make_ticks([100.0, 100.2, 99.9, 100.1], freq="20s")
        bars = ft.resample_bars(df, "1min")
        assert len(bars) == 2
        b0 = bars.iloc[0]
        assert (b0.open, b0.high, b0.low, b0.close, b0.ticks) == (100.0, 100.2, 99.9, 99.9, 3)

    def test_bar_delta_sums_flow_per_bar(self):
        df = make_ticks([100.0, 100.1, 100.2, 100.1], freq="20s")
        d = ft.bar_delta(df, "1min")
        # bar1 ticks: signs 0,+1,+1 -> delta +4; bar2: -1 -> delta -2
        assert d["delta"].tolist() == [4.0, -2.0]
        assert d["cum_delta"].tolist() == [4.0, 2.0]


class TestLoadTicks:
    def test_load_ticks_concats_days_and_derives_mid_spread(self, tmp_path):
        from datetime import date
        root = tmp_path / "XAUUSD"
        root.mkdir()
        for d, px in [("2026-07-01", 3300.0), ("2026-07-02", 3310.0)]:
            pd.DataFrame({
                "ts": pd.date_range(f"{d} 09:00", periods=3, freq="1s", tz="UTC"),
                "bid": px, "ask": px + 0.2, "bid_vol": 1.0, "ask_vol": 1.0,
            }).to_parquet(root / f"{d}.parquet", index=False)
        df = ft.load_ticks("XAUUSD", date(2026, 7, 1), date(2026, 7, 2), ticks_dir=tmp_path)
        assert len(df) == 6
        assert df.index.is_monotonic_increasing
        assert df["mid"].iloc[0] == pytest.approx(3300.1)
        assert df["spread"].iloc[0] == pytest.approx(0.2)

    def test_load_ticks_missing_raises(self, tmp_path):
        from datetime import date
        with pytest.raises(FileNotFoundError):
            ft.load_ticks("XAUUSD", date(2026, 1, 1), date(2026, 1, 2), ticks_dir=tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'src.microstructure'`

- [ ] **Step 3: Implement package + core transforms**

Create empty `src/microstructure/__init__.py`, then `src/microstructure/features.py`:

```python
"""
Pure order-flow feature functions: ticks in, arrays/events out.

Every quantity here is a PROXY computed from Dukascopy quote ticks (bid/ask +
indicative liquidity). Tick-rule delta is not true traded delta; the
volume-at-price heatmap is quoted-activity-at-price, not resting depth —
spot gold has no consolidated order book.

Contract: all functions take a tick DataFrame indexed by UTC ts with columns
bid, ask, bid_vol, ask_vol, mid, spread (the load_ticks() shape). Every
threshold is a kwarg (the viewer exposes them as sliders). No I/O except
load_ticks(); no ML; no state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TICKS_DIR = PROJECT_ROOT / "data" / "ticks"


@dataclass(frozen=True)
class FlowEvent:
    """One detected order-flow event = one mark on the chart."""
    ts: pd.Timestamp
    price: float
    strength: float
    kind: str


# ---------------------------------------------------------------- loading

def load_ticks(symbol: str, start: date, end: date,
               ticks_dir: Path | None = None) -> pd.DataFrame:
    """Read per-day tick Parquets into one UTC-indexed frame with mid/spread."""
    root = (ticks_dir or TICKS_DIR) / symbol
    frames = []
    day = start
    while day <= end:
        p = root / f"{day.isoformat()}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
        day += timedelta(days=1)
    if not frames:
        raise FileNotFoundError(f"no tick files for {symbol} {start}..{end} under {root}")
    df = pd.concat(frames, ignore_index=True).sort_values("ts").set_index("ts")
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    return df


# ------------------------------------------------------- core transforms

def sign_ticks(df: pd.DataFrame) -> pd.Series:
    """Tick rule: mid uptick = +1 (buyer-initiated proxy), downtick = -1,
    unchanged inherits the previous sign; first tick = 0."""
    diff = df["mid"].diff()
    sign = pd.Series(np.sign(diff), index=df.index)
    return sign.replace(0.0, np.nan).ffill().fillna(0.0)


def signed_flow(df: pd.DataFrame) -> pd.Series:
    """Tick sign weighted by indicative liquidity (bid_vol + ask_vol)."""
    return sign_ticks(df) * (df["bid_vol"] + df["ask_vol"])


def cumulative_delta(df: pd.DataFrame) -> pd.Series:
    return signed_flow(df).cumsum()


def resample_bars(df: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Mid-price OHLC bars + tick count per bar."""
    bars = df["mid"].resample(freq).ohlc()
    bars["ticks"] = df["mid"].resample(freq).count()
    return bars.dropna(subset=["open"])


def bar_delta(df: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Per-bar signed-flow sum and its running total."""
    delta = signed_flow(df).resample(freq).sum()
    delta = delta[resample_bars(df, freq).index.intersection(delta.index)]
    return pd.DataFrame({"delta": delta, "cum_delta": delta.cumsum()})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/ tests/unit/test_microstructure_features.py
git commit -m "feat: microstructure package — tick loader + core flow transforms

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Volume-at-price heatmap + HVN/LVN nodes

**Files:**
- Modify: `src/microstructure/features.py` (append)
- Test: `tests/unit/test_microstructure_features.py` (append)

**Interfaces:**
- Produces (used by Task 6):
  - `volume_at_price(df, price_bin: float = 0.5, time_bin: str = "15min") -> pd.DataFrame` — index = price-bin centers, columns = time-bin timestamps, values = liquidity-weighted activity
  - `profile_nodes(vap: pd.DataFrame, hvn_pctile: float = 85.0, lvn_pctile: float = 15.0) -> dict[str, list[float]]` — `{"hvn": [...prices], "lvn": [...prices]}`

- [ ] **Step 1: Write the failing tests** (append to the test file)

```python
class TestVolumeAtPrice:
    def test_histogram_buckets_price_and_time(self):
        # 09:00 block trades at ~3300.0; 09:20 block at ~3305.0
        a = make_ticks([3300.0] * 10, start="2026-07-01 09:00")
        b = make_ticks([3305.0] * 10, start="2026-07-01 09:20")
        df = pd.concat([a, b])
        vap = ft.volume_at_price(df, price_bin=0.5, time_bin="15min")
        assert 3300.0 in vap.index and 3305.0 in vap.index
        t0, t1 = pd.Timestamp("2026-07-01 09:00", tz="UTC"), pd.Timestamp("2026-07-01 09:15", tz="UTC")
        assert vap.loc[3300.0, t0] == pytest.approx(20.0)   # 10 ticks * (1+1) vol
        assert vap.loc[3305.0, t1] == pytest.approx(20.0)
        assert vap.loc[3305.0, t0] == pytest.approx(0.0)

    def test_profile_nodes_hvn_lvn(self):
        heavy = make_ticks([3300.0] * 50, start="2026-07-01 09:00")
        light = make_ticks([3302.0] * 2, start="2026-07-01 09:05")
        mid_ = make_ticks([3304.0] * 10, start="2026-07-01 09:10")
        vap = ft.volume_at_price(pd.concat([heavy, light, mid_]), price_bin=0.5, time_bin="15min")
        nodes = ft.profile_nodes(vap, hvn_pctile=80.0, lvn_pctile=40.0)
        assert 3300.0 in nodes["hvn"]
        assert 3302.0 in nodes["lvn"]
        assert 3304.0 not in nodes["hvn"] and 3304.0 not in nodes["lvn"]
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py::TestVolumeAtPrice -v`
Expected: FAIL — `AttributeError: ... no attribute 'volume_at_price'`

- [ ] **Step 3: Implement** (append to `features.py`)

```python
# ------------------------------------------------------ heatmap / profile

def volume_at_price(df: pd.DataFrame, price_bin: float = 0.5,
                    time_bin: str = "15min") -> pd.DataFrame:
    """2-D activity histogram (price x time): the heatmap layer.
    Values are quoted-liquidity-weighted tick activity — a proxy, not depth."""
    tmp = pd.DataFrame({
        "activity": df["bid_vol"] + df["ask_vol"],
        "pbin": (df["mid"] / price_bin).round() * price_bin,
    })
    vap = (tmp.groupby([pd.Grouper(freq=time_bin), "pbin"])["activity"]
              .sum().unstack(0).fillna(0.0))
    return vap.sort_index()


def profile_nodes(vap: pd.DataFrame, hvn_pctile: float = 85.0,
                  lvn_pctile: float = 15.0) -> dict[str, list[float]]:
    """Collapse the heatmap to a profile; return high/low-volume node prices."""
    profile = vap.sum(axis=1)
    active = profile[profile > 0]
    if active.empty:
        return {"hvn": [], "lvn": []}
    hi = np.percentile(active, hvn_pctile)
    lo = np.percentile(active, lvn_pctile)
    return {"hvn": [float(p) for p in active.index[active >= hi]],
            "lvn": [float(p) for p in active.index[active <= lo]]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/features.py tests/unit/test_microstructure_features.py
git commit -m "feat: volume-at-price heatmap + HVN/LVN profile nodes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Event detectors I — delta divergence + absorption zones

**Files:**
- Modify: `src/microstructure/features.py` (append)
- Test: `tests/unit/test_microstructure_features.py` (append)

**Interfaces:**
- Consumes: `resample_bars`/`bar_delta` outputs (Task 2), `FlowEvent`.
- Produces (used by Task 6):
  - `delta_divergence(bars: pd.DataFrame, delta_bars: pd.DataFrame, lookback: int = 20) -> list[FlowEvent]` — kinds `bearish_divergence` / `bullish_divergence`
  - `absorption_zones(df, bucket: str = "2min", band_pts: float = 0.5, flow_pctile: float = 90.0) -> list[FlowEvent]` — kinds `absorption_of_selling` / `absorption_of_buying`

- [ ] **Step 1: Write the failing tests** (append)

```python
class TestDetectorsI:
    def test_delta_divergence_bearish(self):
        # price grinds to new highs while delta bleeds -> bearish divergence
        idx = pd.date_range("2026-07-01 09:00", periods=30, freq="5min", tz="UTC")
        bars = pd.DataFrame({"open": 0.0, "high": 0.0, "low": 0.0,
                             "close": np.linspace(3300, 3329, 30), "ticks": 10}, index=idx)
        delta_bars = pd.DataFrame({"delta": -1.0,
                                   "cum_delta": np.linspace(-1, -30, 30)}, index=idx)
        events = ft.delta_divergence(bars, delta_bars, lookback=5)
        assert events and all(e.kind == "bearish_divergence" for e in events)
        assert all(e.strength > 0 for e in events)

    def test_delta_divergence_none_when_confirmed(self):
        # price and delta rise together -> no divergence either way
        idx = pd.date_range("2026-07-01 09:00", periods=30, freq="5min", tz="UTC")
        bars = pd.DataFrame({"open": 0.0, "high": 0.0, "low": 0.0,
                             "close": np.linspace(3300, 3329, 30), "ticks": 10}, index=idx)
        delta_bars = pd.DataFrame({"delta": 1.0,
                                   "cum_delta": np.linspace(1, 30, 30)}, index=idx)
        assert ft.delta_divergence(bars, delta_bars, lookback=5) == []

    def test_absorption_flags_one_sided_flow_in_tight_band(self):
        quiet = make_ticks([3300.0] * 10, start="2026-07-01 09:00")          # flow 0
        # 3 sawtooth cycles: 9 downticks of 0.01 then one +0.09 -> net flow -48, range 0.09
        saw = []
        px = 3300.0
        for _ in range(3):
            for _ in range(9):
                px -= 0.01
                saw.append(px)
            px += 0.09
            saw.append(px)
        absorb = make_ticks(saw, start="2026-07-01 09:02")
        trend = make_ticks(list(np.arange(3300.0, 3301.5, 0.05)),
                           start="2026-07-01 09:04")                          # wide range
        events = ft.absorption_zones(pd.concat([quiet, absorb, trend]),
                                     bucket="2min", band_pts=0.3, flow_pctile=50.0)
        assert len(events) == 1
        e = events[0]
        assert e.kind == "absorption_of_selling"
        assert e.ts == pd.Timestamp("2026-07-01 09:02", tz="UTC")
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py::TestDetectorsI -v`
Expected: FAIL — `AttributeError: ... no attribute 'delta_divergence'`

- [ ] **Step 3: Implement** (append to `features.py`)

```python
# ------------------------------------------------------- event detectors

def delta_divergence(bars: pd.DataFrame, delta_bars: pd.DataFrame,
                     lookback: int = 20) -> list[FlowEvent]:
    """Price makes a new lookback high/low that cumulative delta does not
    confirm. Strength = size of the unconfirmed delta gap."""
    close = bars["close"]
    cd = delta_bars["cum_delta"].reindex(bars.index).ffill()
    roll_hi, roll_lo = close.rolling(lookback).max(), close.rolling(lookback).min()
    cd_hi, cd_lo = cd.rolling(lookback).max(), cd.rolling(lookback).min()
    bear = (close >= roll_hi) & (cd < cd_hi)
    bull = (close <= roll_lo) & (cd > cd_lo)
    events = [FlowEvent(ts, float(close.loc[ts]), float(cd_hi.loc[ts] - cd.loc[ts]),
                        "bearish_divergence") for ts in bars.index[bear]]
    events += [FlowEvent(ts, float(close.loc[ts]), float(cd.loc[ts] - cd_lo.loc[ts]),
                         "bullish_divergence") for ts in bars.index[bull]]
    return sorted(events, key=lambda e: e.ts)


def absorption_zones(df: pd.DataFrame, bucket: str = "2min", band_pts: float = 0.5,
                     flow_pctile: float = 90.0) -> list[FlowEvent]:
    """Heavy one-sided signed flow while mid stays pinned inside band_pts:
    someone is absorbing at that level. Heavy selling absorbed = bid strength."""
    flow = signed_flow(df).resample(bucket).sum()
    hi = df["mid"].resample(bucket).max()
    lo = df["mid"].resample(bucket).min()
    px = df["mid"].resample(bucket).mean()
    valid = flow.dropna()
    if valid.empty:
        return []
    thresh = float(np.nanpercentile(np.abs(valid), flow_pctile))
    if thresh <= 0:
        return []
    mask = (np.abs(flow) >= thresh) & ((hi - lo) <= band_pts)
    events = []
    for ts in flow.index[mask.fillna(False)]:
        kind = "absorption_of_selling" if flow.loc[ts] < 0 else "absorption_of_buying"
        events.append(FlowEvent(ts, float(px.loc[ts]), float(abs(flow.loc[ts])), kind))
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/features.py tests/unit/test_microstructure_features.py
git commit -m "feat: delta-divergence and absorption-zone detectors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Event detectors II — imbalance, sweeps, liquidity withdrawal

**Files:**
- Modify: `src/microstructure/features.py` (append)
- Test: `tests/unit/test_microstructure_features.py` (append)

**Interfaces:**
- Produces (used by Task 6):
  - `imbalance_events(df, freq: str = "5min", price_bin: float = 0.5, ratio: float = 3.0, min_activity: float | None = None) -> list[FlowEvent]` — kinds `imbalance_buy` / `imbalance_sell`; strength capped at 99.0
  - `sweep_events(df, swing: str = "30min", bucket: str = "10s", burst_pctile: float = 95.0, revert_s: int = 60) -> list[FlowEvent]` — kinds `sweep_high` / `sweep_low`
  - `liquidity_withdrawal(df, bucket: str = "1min", spread_pctile: float = 95.0, rate_drop: float = 0.5, rate_window: int = 30) -> list[FlowEvent]` — kind `liquidity_withdrawal`

- [ ] **Step 1: Write the failing tests** (append)

```python
class TestDetectorsII:
    def test_imbalance_buy_at_price_bin(self):
        # 20 straight upticks inside one 0.5 price bin, one 5m bar
        df = make_ticks([3300.00 + 0.01 * i for i in range(20)])
        events = ft.imbalance_events(df, freq="5min", price_bin=0.5, ratio=3.0)
        assert len(events) == 1
        assert events[0].kind == "imbalance_buy"
        assert events[0].price == pytest.approx(3300.0)

    def test_sweep_high_burst_pierce_revert(self):
        # 30 min of 1 tick/10s at 3300, one 50-tick burst spiking to 3300.5,
        # then 3 quiet minutes back below the old high
        quiet = make_ticks([3300.0] * 180, freq="10s", start="2026-07-01 09:00")
        burst = make_ticks(list(np.linspace(3300.0, 3300.5, 50)),
                           freq="200ms", start="2026-07-01 09:30")
        after = make_ticks([3299.95] * 18, freq="10s", start="2026-07-01 09:31")
        events = ft.sweep_events(pd.concat([quiet, burst, after]),
                                 swing="30min", bucket="10s",
                                 burst_pctile=99.0, revert_s=60)
        assert [e.kind for e in events] == ["sweep_high"]
        assert events[0].price == pytest.approx(3300.5)

    def test_liquidity_withdrawal_wide_spread_low_rate(self):
        normal = make_ticks([3300.0] * 600, freq="6s", start="2026-07-01 09:00")   # 10/min, spread 0.1
        thin = make_ticks([3300.0] * 10, freq="30s", start="2026-07-01 10:00")     # 2/min
        thin["ask"] = thin["mid"] + 0.25
        thin["bid"] = thin["mid"] - 0.25
        thin["spread"] = thin["ask"] - thin["bid"]
        events = ft.liquidity_withdrawal(pd.concat([normal, thin]), bucket="1min",
                                         spread_pctile=90.0, rate_drop=0.5)
        assert events
        assert all(e.kind == "liquidity_withdrawal" for e in events)
        assert all(e.ts >= pd.Timestamp("2026-07-01 10:00", tz="UTC") for e in events)
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py::TestDetectorsII -v`
Expected: FAIL — `AttributeError: ... no attribute 'imbalance_events'`

- [ ] **Step 3: Implement** (append to `features.py`)

```python
def imbalance_events(df: pd.DataFrame, freq: str = "5min", price_bin: float = 0.5,
                     ratio: float = 3.0,
                     min_activity: float | None = None) -> list[FlowEvent]:
    """Footprint-style stacked imbalance: within one bar and one price bin,
    one side's flow >= ratio x the other. min_activity=None -> median filter."""
    sign = sign_ticks(df)
    act = df["bid_vol"] + df["ask_vol"]
    tmp = pd.DataFrame({
        "buy": act.where(sign > 0, 0.0),
        "sell": act.where(sign < 0, 0.0),
        "pbin": (df["mid"] / price_bin).round() * price_bin,
    })
    g = tmp.groupby([pd.Grouper(freq=freq), "pbin"])[["buy", "sell"]].sum()
    total = g["buy"] + g["sell"]
    if min_activity is None:
        active = total[total > 0]
        min_activity = float(active.median()) if not active.empty else 0.0
    eps = 1e-9
    events = []
    for (ts, price), row in g[total >= min_activity].iterrows():
        if row["buy"] >= ratio * (row["sell"] + eps):
            events.append(FlowEvent(ts, float(price),
                                    min(row["buy"] / (row["sell"] + eps), 99.0),
                                    "imbalance_buy"))
        elif row["sell"] >= ratio * (row["buy"] + eps):
            events.append(FlowEvent(ts, float(price),
                                    min(row["sell"] / (row["buy"] + eps), 99.0),
                                    "imbalance_sell"))
    return events


def sweep_events(df: pd.DataFrame, swing: str = "30min", bucket: str = "10s",
                 burst_pctile: float = 95.0, revert_s: int = 60) -> list[FlowEvent]:
    """Quote-rate burst that pierces the prior swing high/low and closes back
    beyond it within revert_s: a stop-run / sweep proxy."""
    mid = df["mid"]
    rate = mid.resample(bucket).count()
    b_hi, b_lo = mid.resample(bucket).max(), mid.resample(bucket).min()
    b_close = mid.resample(bucket).last()
    active = rate[rate > 0]
    if active.empty:
        return []
    thresh = float(np.nanpercentile(active, burst_pctile))
    swing_n = max(int(pd.Timedelta(swing) / pd.Timedelta(bucket)), 1)
    swing_hi = b_hi.rolling(swing_n, min_periods=1).max().shift(1)
    swing_lo = b_lo.rolling(swing_n, min_periods=1).min().shift(1)
    fwd = max(int(pd.Timedelta(seconds=revert_s) / pd.Timedelta(bucket)), 1)
    future_close = b_close.ffill().shift(-fwd)
    up = (rate >= thresh) & (b_hi > swing_hi) & (future_close < swing_hi)
    dn = (rate >= thresh) & (b_lo < swing_lo) & (future_close > swing_lo)
    events = [FlowEvent(ts, float(b_hi.loc[ts]), float(rate.loc[ts] / max(thresh, 1.0)),
                        "sweep_high") for ts in rate.index[up.fillna(False)]]
    events += [FlowEvent(ts, float(b_lo.loc[ts]), float(rate.loc[ts] / max(thresh, 1.0)),
                         "sweep_low") for ts in rate.index[dn.fillna(False)]]
    return sorted(events, key=lambda e: e.ts)


def liquidity_withdrawal(df: pd.DataFrame, bucket: str = "1min",
                         spread_pctile: float = 95.0, rate_drop: float = 0.5,
                         rate_window: int = 30) -> list[FlowEvent]:
    """Spread blowout + quote-rate collapse vs recent median = liquidity
    pulled (pre-news / thin book warning)."""
    spread = df["spread"].resample(bucket).mean()
    rate = df["mid"].resample(bucket).count()
    px = df["mid"].resample(bucket).last().ffill()
    med_rate = rate.rolling(rate_window, min_periods=5).median()
    s = spread.dropna()
    if s.empty:
        return []
    s_thresh = float(np.nanpercentile(s, spread_pctile))
    if s_thresh <= 0:
        return []
    mask = (spread >= s_thresh) & (rate <= rate_drop * med_rate)
    return [FlowEvent(ts, float(px.loc[ts]), float(spread.loc[ts] / s_thresh),
                      "liquidity_withdrawal")
            for ts in spread.index[mask.fillna(False)]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_microstructure_features.py -v`
Expected: 14 PASS

- [ ] **Step 5: Run the whole unit suite for regressions**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass (422+ tests), no failures.

- [ ] **Step 6: Commit**

```bash
git add src/microstructure/features.py tests/unit/test_microstructure_features.py
git commit -m "feat: imbalance, sweep, and liquidity-withdrawal detectors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Dash viewer

**Files:**
- Create: `scripts/orderflow_viewer.py`

**Interfaces:**
- Consumes: `ensure_ticks` (Task 1); `load_ticks`, `resample_bars`, `bar_delta`, `volume_at_price`, `profile_nodes`, and all five detectors (Tasks 2–5).
- Produces: `python scripts/orderflow_viewer.py [--port 8060] [--symbol XAUUSD]` → browser app. `build_figure(df, timeframe, show, params)` kept as a top-level function (no Dash objects inside) so it stays importable.

- [ ] **Step 1: Write the viewer**

Create `scripts/orderflow_viewer.py`:

```python
#!/usr/bin/env python3
"""
Order-flow marking viewer (Plotly Dash) — Stage 1 of the order-flow spec.

Candles over a volume-at-price heatmap with proxy order-flow marks:
delta divergences, absorption zones, footprint imbalances, sweeps, and
liquidity-withdrawal warnings, plus a cumulative-delta subplot. All
quantities are proxies from Dukascopy quote ticks — no real DOM exists
for spot gold. Sliders re-run detectors instantly; tick loads are cached.

    python scripts/orderflow_viewer.py                 # http://127.0.0.1:8050
    python scripts/orderflow_viewer.py --port 8060 --symbol XAUUSD
"""
import argparse
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html
from plotly.subplots import make_subplots

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_dukascopy_ticks import ensure_ticks  # noqa: E402
from src.microstructure import features as ft  # noqa: E402

MARK_STYLE = {
    "bearish_divergence": dict(symbol="triangle-down", color="#d62728"),
    "bullish_divergence": dict(symbol="triangle-up", color="#2ca02c"),
    "absorption_of_selling": dict(symbol="square", color="#2ca02c"),
    "absorption_of_buying": dict(symbol="square", color="#d62728"),
    "imbalance_buy": dict(symbol="diamond", color="#1f77b4"),
    "imbalance_sell": dict(symbol="diamond", color="#ff7f0e"),
    "sweep_high": dict(symbol="x", color="#d62728"),
    "sweep_low": dict(symbol="x", color="#2ca02c"),
    "liquidity_withdrawal": dict(symbol="line-ns-open", color="#7f7f7f"),
}


@lru_cache(maxsize=4)
def _ticks(symbol: str, start_iso: str, end_iso: str):
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    ensure_ticks(symbol, start, end)
    return ft.load_ticks(symbol, start, end)


def _detect(df, bars, delta, show, p):
    """Run only the enabled detectors; return list[FlowEvent]."""
    events = []
    if "divergence" in show:
        events += ft.delta_divergence(bars, delta, lookback=int(p["lookback"]))
    if "absorption" in show:
        events += ft.absorption_zones(df, band_pts=p["band_pts"],
                                      flow_pctile=p["flow_pctile"])
    if "imbalance" in show:
        events += ft.imbalance_events(df, freq=p["timeframe"],
                                      price_bin=p["price_bin"], ratio=p["ratio"])
    if "sweep" in show:
        events += ft.sweep_events(df, burst_pctile=p["burst_pctile"])
    if "withdrawal" in show:
        events += ft.liquidity_withdrawal(df, spread_pctile=p["spread_pctile"])
    return events


def build_figure(df, timeframe, show, p) -> go.Figure:
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    span_days = max((df.index[-1] - df.index[0]).days, 1)
    time_bin = "15min" if span_days <= 14 else "1h"   # heatmap guard for long ranges
    vap = ft.volume_at_price(df, price_bin=p["price_bin"], time_bin=time_bin)
    nodes = ft.profile_nodes(vap)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.72, 0.28], vertical_spacing=0.03)
    fig.add_trace(go.Heatmap(x=vap.columns, y=vap.index, z=vap.values,
                             colorscale="Blues", opacity=0.5, showscale=False,
                             hoverinfo="skip"), row=1, col=1)
    fig.add_trace(go.Candlestick(x=bars.index, open=bars["open"], high=bars["high"],
                                 low=bars["low"], close=bars["close"],
                                 name=timeframe), row=1, col=1)
    for y in nodes["hvn"]:
        fig.add_hline(y=y, line=dict(color="rgba(255,165,0,0.6)", width=1), row=1, col=1)
    for y in nodes["lvn"]:
        fig.add_hline(y=y, line=dict(color="rgba(128,128,128,0.4)", width=1, dash="dot"),
                      row=1, col=1)

    events = _detect(df, bars, delta, show, {**p, "timeframe": timeframe})
    by_kind = {}
    for e in events:
        by_kind.setdefault(e.kind, []).append(e)
    for kind, evs in by_kind.items():
        style = MARK_STYLE[kind]
        fig.add_trace(go.Scatter(
            x=[e.ts for e in evs], y=[e.price for e in evs], mode="markers",
            name=kind, marker=dict(size=11, line=dict(width=1), **style),
            hovertext=[f"{kind}<br>{e.ts:%m-%d %H:%M}<br>px {e.price:.2f}"
                       f"<br>strength {e.strength:.2f}" for e in evs],
            hoverinfo="text"), row=1, col=1)

    fig.add_trace(go.Bar(x=delta.index, y=delta["delta"], name="delta",
                         marker_color=["#2ca02c" if v >= 0 else "#d62728"
                                       for v in delta["delta"]]), row=2, col=1)
    fig.add_trace(go.Scatter(x=delta.index, y=delta["cum_delta"], name="cum delta",
                             line=dict(color="#1f77b4", width=2)), row=2, col=1)
    fig.update_layout(height=880, xaxis_rangeslider_visible=False,
                      margin=dict(l=40, r=20, t=30, b=30),
                      legend=dict(orientation="h", y=1.02),
                      uirevision="keep-zoom")
    return fig


def make_app(symbol: str) -> Dash:
    app = Dash(__name__)
    end_default = date.today() - timedelta(days=1)
    start_default = end_default - timedelta(days=4)

    def slider(id_, lo, hi, step, val, label):
        return html.Div([html.Label(label, style={"fontSize": "12px"}),
                         dcc.Slider(lo, hi, step, value=val, id=id_,
                                    marks=None, tooltip={"placement": "bottom",
                                                         "always_visible": True})],
                        style={"marginBottom": "6px"})

    controls = html.Div([
        html.H3(f"{symbol} order-flow marks (proxy)"),
        dcc.DatePickerRange(id="dates", start_date=start_default, end_date=end_default,
                            display_format="YYYY-MM-DD"),
        dcc.RadioItems(["1min", "5min", "15min"], "5min", id="timeframe", inline=True),
        dcc.Checklist(["divergence", "absorption", "imbalance", "sweep", "withdrawal"],
                      ["divergence", "absorption", "imbalance", "sweep"], id="show"),
        slider("lookback", 5, 60, 1, 20, "divergence lookback (bars)"),
        slider("band_pts", 0.1, 2.0, 0.1, 0.5, "absorption band (pts)"),
        slider("flow_pctile", 50, 99, 1, 90, "absorption flow pctile"),
        slider("ratio", 2.0, 6.0, 0.5, 3.0, "imbalance ratio"),
        slider("burst_pctile", 80, 99.5, 0.5, 95, "sweep burst pctile"),
        slider("spread_pctile", 80, 99.5, 0.5, 95, "withdrawal spread pctile"),
        slider("price_bin", 0.25, 2.0, 0.25, 0.5, "price bin (pts)"),
    ], style={"width": "270px", "padding": "10px", "flexShrink": "0"})

    app.layout = html.Div([
        controls,
        html.Div(dcc.Loading(dcc.Graph(id="chart")), style={"flexGrow": "1"}),
    ], style={"display": "flex"})

    @app.callback(
        Output("chart", "figure"),
        Input("dates", "start_date"), Input("dates", "end_date"),
        Input("timeframe", "value"), Input("show", "value"),
        Input("lookback", "value"), Input("band_pts", "value"),
        Input("flow_pctile", "value"), Input("ratio", "value"),
        Input("burst_pctile", "value"), Input("spread_pctile", "value"),
        Input("price_bin", "value"))
    def update(start, end, timeframe, show, lookback, band_pts, flow_pctile,
               ratio, burst_pctile, spread_pctile, price_bin):
        df = _ticks(symbol, start[:10], end[:10])
        params = dict(lookback=lookback, band_pts=band_pts, flow_pctile=flow_pctile,
                      ratio=ratio, burst_pctile=burst_pctile,
                      spread_pctile=spread_pctile, price_bin=price_bin)
        return build_figure(df, timeframe, show or [], params)

    return app


def main() -> int:
    p = argparse.ArgumentParser(description="Order-flow marking viewer")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--port", type=int, default=8050)
    args = p.parse_args()
    make_app(args.symbol).run(debug=False, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Import smoke test**

Run: `./venv/bin/python -c "from scripts.orderflow_viewer import build_figure, make_app; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 3: Headless figure test against real ticks**

Uses the day fetched in Task 1 (fetch 2 more if needed):

```bash
./venv/bin/python scripts/fetch_dukascopy_ticks.py --symbol XAUUSD --start 2026-07-07 --end 2026-07-09
./venv/bin/python -c "
from datetime import date
from src.microstructure import features as ft
from scripts.orderflow_viewer import build_figure
df = ft.load_ticks('XAUUSD', date(2026, 7, 7), date(2026, 7, 9))
fig = build_figure(df, '5min',
                   ['divergence', 'absorption', 'imbalance', 'sweep', 'withdrawal'],
                   dict(lookback=20, band_pts=0.5, flow_pctile=90, ratio=3.0,
                        burst_pctile=95, spread_pctile=95, price_bin=0.5))
kinds = [t.name for t in fig.data]
print(len(df), 'ticks;', len(fig.data), 'traces:', kinds)
assert any('5min' in (t.name or '') for t in fig.data)
print('OK')"
```

Expected: tick count in the hundreds of thousands, heatmap + candlestick + delta traces plus some mark traces, `OK`.

- [ ] **Step 4: Manual browser smoke (user-facing check)**

Run: `./venv/bin/python scripts/orderflow_viewer.py`
Open http://127.0.0.1:8050 and verify: candles render over the blue heatmap, HVN/LVN lines visible, delta subplot populated, marks toggle with the checklist, at least one slider visibly changes the marks. Then Ctrl-C.

- [ ] **Step 5: Commit**

```bash
git add scripts/orderflow_viewer.py
git commit -m "feat: order-flow marking viewer (Dash) — heatmap, delta, event marks

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Final verification

- [ ] **Step 1: Full unit suite**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass, zero failures.

- [ ] **Step 2: Confirm no live-system files touched**

Run: `git diff --stat main@{u} 2>/dev/null || git log --stat -7 --oneline`
Expected: only `scripts/fetch_dukascopy_ticks.py`, `scripts/orderflow_viewer.py`, `src/microstructure/*`, `tests/unit/test_*`, `requirements.txt`, and docs — no `config/`, `src/strategies/`, `src/risk/`, `src/execution/` changes.
