# Live Order-Flow Marking (Stage 1.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real-time intraday order-flow signals — as each live XAUUSD candle closes, the Stage-1 detectors run on a hybrid feed (1 Hz passive MT5 tap + Dukascopy hourly backfill) and new marks appear in a LIVE viewer mode, plus a defended-level map, inferred liquidity pools, and a bounded broker-DOM probe.

**Architecture:** Three units with one-way deps: `src/microstructure/live_feed.py` (I/O + state: StatusTap, DukaBackfill, stitcher) → `src/microstructure/live_marks.py` (pure engine: closed-candle gating, SignalFeed, level maps) → viewer LIVE mode. `features.py` is NOT modified. The DOM probe (read-only EA + checker script) is an independent deliverable gated by its own verdict.

**Tech Stack:** Python 3.11 venv, pandas/numpy/pyarrow, dash 4.2 + plotly 6.8 (installed), MQL5 (probe EA, user-compiled), pytest.

**Spec:** `docs/superpowers/specs/2026-07-16-live-orderflow-marking-design.md`

## Global Constraints

- Repo venv for everything: `./venv/bin/python`, `./venv/bin/pytest`, from repo root (`pytest.ini` sets `pythonpath = .`).
- NEVER touch the MT5 bridge command/response channel — the live tap reads ONLY `mt5_status.json` via `MT5FileClient` (the `volatility_monitor.py` pattern). Do not modify `mt5_bridge/EA_FileBridge.mq5`, `mt5_bridge/mt5_file_client.py`, `config/`, `src/strategies/`, `src/risk/`, `src/execution/`, `src/data/`, `src/main.py`.
- Do NOT modify `src/microstructure/features.py` or `scripts/fetch_dukascopy_ticks.py` — consume their exports as-is.
- NEVER call `ensure_ticks` for the CURRENT day — it would write a partial day Parquet into `data/ticks/` that never self-heals. Live backfill uses its own per-hour cache under `data/ticks_live/`.
- New data dirs are gitignored: Task 1 appends `data/ticks_live/` to `.gitignore`. Never commit data files.
- The worktree carries unrelated uncommitted `config_live_*.yaml` / `data/strategy_risk_weights.json` changes and an untracked `ncat` file — never stage, commit, or revert them. Stage files by explicit path only.
- Commit messages: the harness shell flattens newlines in `git commit -m`; ALWAYS write the message to a scratch file with a real blank line before the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and use `git commit -F <file>`. Verify with `git log -1 --format=%B` after each commit.
- LIVE frames are count-weighted: `bid_vol = ask_vol = 0.5` across the WHOLE stitched frame (tap AND Dukascopy segments) — one tick = weight 1.0, no scale cliff at the stitch boundary.
- All UI-facing quantities remain labeled proxies; the liquidity-pool layer is labeled "inferred".

---

### Task 1: live_feed part A — spill store + pure stitcher

**Files:**
- Create: `src/microstructure/live_feed.py`
- Test: `tests/unit/test_live_feed.py`
- Modify: `.gitignore` (append one line)

**Interfaces:**
- Consumes: nothing from other new tasks; `pandas` only.
- Produces (used by Tasks 2, 3, 5):
  - `LIVE_DIR: Path` (= `<repo>/data/ticks_live`)
  - `spill_path(symbol: str, day: date, live_dir: Path | None = None) -> Path`
  - `append_spill(rows: list[tuple[str, float, float]], path: Path) -> None`
  - `load_spill(symbol: str, day: date, live_dir: Path | None = None) -> pd.DataFrame` (columns `ts, bid, ask`; `ts` tz-aware UTC; empty frame when absent)
  - `match_quote_key(symbol: str, quote_keys) -> str | None`
  - `stitch_day(duka: pd.DataFrame, tap: pd.DataFrame) -> pd.DataFrame` — inputs have columns `ts, bid, ask` (duka may also carry vol columns, ignored); output is the `load_ticks` shape: UTC `ts` index, columns `bid, ask, bid_vol, ask_vol, mid, spread` with `bid_vol = ask_vol = 0.5`; tap rows at or before duka's max `ts` are dropped; empty-input-safe.

- [ ] **Step 1: Append to `.gitignore`**

```
data/ticks_live/
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_live_feed.py`:

```python
"""Unit tests for src/microstructure/live_feed.py — no network, no MT5."""
from datetime import date

import pandas as pd
import pytest

from src.microstructure import live_feed as lf


def _frame(ts_list, bid=3300.0, ask=3300.2):
    return pd.DataFrame({
        "ts": pd.to_datetime(ts_list, utc=True),
        "bid": bid, "ask": ask,
    })


class TestSpill:
    def test_append_and_load_roundtrip(self, tmp_path):
        p = lf.spill_path("XAUUSD", date(2026, 7, 16), live_dir=tmp_path)
        assert p == tmp_path / "XAUUSD" / "2026-07-16.csv"
        lf.append_spill([("2026-07-16T09:00:00+00:00", 3300.0, 3300.2)], p)
        lf.append_spill([("2026-07-16T09:00:01+00:00", 3300.1, 3300.3)], p)
        df = lf.load_spill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path)
        assert len(df) == 2
        assert df["ts"].dt.tz is not None
        assert df.loc[1, "bid"] == pytest.approx(3300.1)

    def test_load_missing_returns_empty(self, tmp_path):
        df = lf.load_spill("XAUUSD", date(2026, 1, 1), live_dir=tmp_path)
        assert df.empty and list(df.columns) == ["ts", "bid", "ask"]


class TestMatchQuoteKey:
    def test_exact_prefix_and_missing(self):
        assert lf.match_quote_key("XAUUSD", ["XAUUSD"]) == "XAUUSD"
        assert lf.match_quote_key("XAUUSD", ["XAUUSDs", "XAUUSDx"]) == "XAUUSDs"
        assert lf.match_quote_key("XAUUSD", ["EURUSD"]) is None


class TestStitchDay:
    def test_tap_rows_before_boundary_dropped_and_weights_normalized(self):
        duka = _frame(["2026-07-16 08:59:58", "2026-07-16 08:59:59"])
        duka["bid_vol"] = 2.5   # Dukascopy liquidity must be overwritten
        duka["ask_vol"] = 1.5
        tap = _frame(["2026-07-16 08:59:59", "2026-07-16 09:00:01"],
                     bid=3301.0, ask=3301.2)
        df = lf.stitch_day(duka, tap)
        assert len(df) == 3            # tap row at 08:59:59 (== boundary) dropped
        assert df.index.is_monotonic_increasing
        assert (df["bid_vol"] == 0.5).all() and (df["ask_vol"] == 0.5).all()
        assert df["mid"].iloc[-1] == pytest.approx(3301.1)
        assert df["spread"].iloc[-1] == pytest.approx(0.2)

    def test_empty_duka_uses_all_tap(self):
        tap = _frame(["2026-07-16 09:00:00", "2026-07-16 09:00:01"])
        df = lf.stitch_day(pd.DataFrame(columns=["ts", "bid", "ask"]), tap)
        assert len(df) == 2 and df["mid"].iloc[0] == pytest.approx(3300.1)

    def test_both_empty(self):
        df = lf.stitch_day(pd.DataFrame(columns=["ts", "bid", "ask"]),
                           pd.DataFrame(columns=["ts", "bid", "ask"]))
        assert df.empty
        assert list(df.columns) == ["bid", "ask", "bid_vol", "ask_vol", "mid", "spread"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./venv/bin/pytest tests/unit/test_live_feed.py -v`
Expected: ERROR — `ModuleNotFoundError: ... 'src.microstructure.live_feed'`

- [ ] **Step 4: Implement**

Create `src/microstructure/live_feed.py`:

```python
"""
Live tick feed: passive 1 Hz tap of the EA status file + Dukascopy stitching.

READ-ONLY against the MT5 bridge: this polls mt5_status.json by mtime (the
volatility_monitor pattern) and never touches the command/response channel,
so it is safe alongside the live bot. Tap samples carry no liquidity info,
so LIVE frames are count-weighted: bid_vol = ask_vol = 0.5 on EVERY tick of
the stitched frame (Dukascopy segment included) — one tick = weight 1.0,
which keeps detector percentiles consistent across the stitch boundary.
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LIVE_DIR = PROJECT_ROOT / "data" / "ticks_live"
SPILL_COLUMNS = ["ts", "bid", "ask"]
STITCHED_COLUMNS = ["bid", "ask", "bid_vol", "ask_vol", "mid", "spread"]


# ---------------------------------------------------------------- spill CSV

def spill_path(symbol: str, day: date, live_dir: Path | None = None) -> Path:
    return (live_dir or LIVE_DIR) / symbol / f"{day.isoformat()}.csv"


def append_spill(rows: list[tuple[str, float, float]], path: Path) -> None:
    """Append (iso_ts, bid, ask) rows; header written only on file creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SPILL_COLUMNS)
        w.writerows(rows)


def load_spill(symbol: str, day: date, live_dir: Path | None = None) -> pd.DataFrame:
    p = spill_path(symbol, day, live_dir)
    if not p.exists():
        return pd.DataFrame(columns=SPILL_COLUMNS)
    df = pd.read_csv(p)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


# ------------------------------------------------------------ symbol match

def match_quote_key(symbol: str, quote_keys) -> str | None:
    """Config symbol (XAUUSD) -> broker quote key (XAUUSDs), shortest prefix."""
    keys = list(quote_keys)
    if symbol in keys:
        return symbol
    candidates = [k for k in keys if k.startswith(symbol)]
    return min(candidates, key=len) if candidates else None


# --------------------------------------------------------------- stitcher

def stitch_day(duka: pd.DataFrame, tap: pd.DataFrame) -> pd.DataFrame:
    """Merge published Dukascopy ticks with tap rows strictly after them.

    Output matches the load_ticks() shape so every detector runs unchanged.
    Weights are forced to 0.5/0.5 everywhere (count-weighted LIVE frame).
    """
    parts = []
    if not duka.empty:
        parts.append(duka[["ts", "bid", "ask"]])
        boundary = duka["ts"].max()
        if not tap.empty:
            parts.append(tap.loc[tap["ts"] > boundary, ["ts", "bid", "ask"]])
    elif not tap.empty:
        parts.append(tap[["ts", "bid", "ask"]])
    if not parts:
        empty = pd.DataFrame(columns=STITCHED_COLUMNS)
        empty.index = pd.DatetimeIndex([], tz="UTC", name="ts")
        return empty
    df = pd.concat(parts, ignore_index=True).sort_values("ts").set_index("ts")
    df["bid_vol"] = 0.5
    df["ask_vol"] = 0.5
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    return df
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_live_feed.py -v`
Expected: 6 PASS

- [ ] **Step 6: Commit** (message via file, per Global Constraints)

Message file content:
```
feat: live-feed spill store + count-weighted day stitcher

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/live_feed.py tests/unit/test_live_feed.py .gitignore
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 2: live_feed part B — StatusTap + DukaBackfill

**Files:**
- Modify: `src/microstructure/live_feed.py` (append)
- Test: `tests/unit/test_live_feed.py` (append)

**Interfaces:**
- Consumes: Task 1 helpers; `fetch_hour`, `TickFetchError`, `DEFAULT_POINTS` from `scripts/fetch_dukascopy_ticks` / `scripts/fetch_dukascopy` (existing).
- Produces (used by Task 5):
  - `class StatusTap(symbol, read_status=None, live_dir=None, interval_s=1.0)` with methods `sample(now=None) -> bool`, `spill() -> None`, `preload_spill(day) -> int`, `rows_df() -> pd.DataFrame` (columns `ts, bid, ask`), `staleness_s() -> float`, `start() -> None` (idempotent thread), `stop() -> None`. `read_status` is a zero-arg callable returning a quotes dict (`{key: {"bid":…, "ask":…}}`) or `None`; default reader wraps `MT5FileClient` with mtime gating.
  - `class DukaBackfill(symbol, day, live_dir=None, retry_min=10.0, fetch_hour_fn=None)` with `refresh(now: datetime) -> pd.DataFrame` (columns `ts, bid, ask, bid_vol, ask_vol` — concat of cached hours, may be empty) and `published_hours() -> list[int]`. Hour cache files: `{live_dir}/{symbol}/duka_{YYYY-MM-DD}_{HH}.parquet`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_live_feed.py`)

```python
class TestStatusTap:
    def test_sample_records_prefix_matched_quote(self, tmp_path):
        quotes = {"XAUUSDs": {"bid": 3300.0, "ask": 3300.2}}
        tap = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        now = pd.Timestamp("2026-07-16 09:00:00", tz="UTC")
        assert tap.sample(now=now) is True
        df = tap.rows_df()
        assert len(df) == 1 and df["bid"].iloc[0] == pytest.approx(3300.0)
        assert tap.staleness_s() < 5.0

    def test_sample_skips_none_missing_and_bad_quotes(self, tmp_path):
        tap = lf.StatusTap("XAUUSD", read_status=lambda: None, live_dir=tmp_path)
        assert tap.sample() is False
        tap2 = lf.StatusTap("XAUUSD", read_status=lambda: {"EURUSD": {"bid": 1, "ask": 1.1}},
                            live_dir=tmp_path)
        assert tap2.sample() is False
        tap3 = lf.StatusTap("XAUUSD", read_status=lambda: {"XAUUSDs": {"bid": 0, "ask": 0}},
                            live_dir=tmp_path)
        assert tap3.sample() is False
        assert tap3.rows_df().empty

    def test_spill_and_preload_roundtrip(self, tmp_path):
        quotes = {"XAUUSDs": {"bid": 3300.0, "ask": 3300.2}}
        tap = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        now = pd.Timestamp("2026-07-16 09:00:00", tz="UTC")
        tap.sample(now=now)
        tap.spill()
        tap2 = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        n = tap2.preload_spill(date(2026, 7, 16))
        assert n == 1 and len(tap2.rows_df()) == 1


class TestDukaBackfill:
    def _fake_fetch(self, calls):
        def fetch(symbol, day, hour, point):
            calls.append(hour)
            if hour == 0:
                return pd.DataFrame({
                    "ts": pd.date_range(f"{day} 00:00", periods=3, freq="1s", tz="UTC"),
                    "bid": 3300.0, "ask": 3300.2, "bid_vol": 1.0, "ask_vol": 1.0,
                })
            return None  # not published yet
        return fetch

    def test_refresh_fetches_only_completed_hours_and_caches(self, tmp_path):
        calls: list[int] = []
        bf = lf.DukaBackfill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path,
                             fetch_hour_fn=self._fake_fetch(calls))
        now = datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc)
        df = bf.refresh(now)
        assert sorted(set(calls)) == [0, 1]          # hour 2 not complete yet
        assert len(df) == 3 and bf.published_hours() == [0]
        assert (tmp_path / "XAUUSD" / "duka_2026-07-16_00.parquet").exists()

    def test_refresh_throttles_retries_but_rereads_cache(self, tmp_path):
        calls: list[int] = []
        bf = lf.DukaBackfill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path,
                             retry_min=10.0, fetch_hour_fn=self._fake_fetch(calls))
        now = datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc)
        bf.refresh(now)
        n_first = len(calls)
        df = bf.refresh(now)                          # immediate second refresh
        assert len(calls) == n_first                  # no new network attempts
        assert len(df) == 3                           # cached hour still served
```

Add to the test file's imports: `from datetime import date, datetime, timezone`.

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_live_feed.py -v`
Expected: new tests FAIL — `AttributeError: ... 'StatusTap'`

- [ ] **Step 3: Implement** (append to `src/microstructure/live_feed.py`)

```python
# ---------------------------------------------------------------- live tap

STALE_AFTER_S = 10.0
SPILL_EVERY_S = 60.0


class StatusTap:
    """1 Hz passive sampler of the EA status file for one symbol.

    read_status is injectable for tests: a zero-arg callable returning the
    quotes dict ({key: {"bid":…, "ask":…}}) or None when nothing new. The
    default reader wraps MT5FileClient with mtime gating — read-only, never
    the command channel. start() is idempotent; restart re-seeds from the
    spill CSV via preload_spill().
    """

    def __init__(self, symbol: str, read_status=None, live_dir: Path | None = None,
                 interval_s: float = 1.0):
        self.symbol = symbol
        self.live_dir = live_dir
        self.interval_s = interval_s
        self._read_status = read_status or self._default_reader()
        self._rows: list[tuple[pd.Timestamp, float, float]] = []
        self._unspilled: list[tuple[str, float, float]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_change = 0.0
        self._last_spill = 0.0

    @staticmethod
    def _default_reader():
        sys.path.insert(0, str(PROJECT_ROOT / "mt5_bridge"))
        from mt5_file_client import MT5FileClient
        client = MT5FileClient()
        state = {"mtime": 0.0}

        def read():
            try:
                mtime = client.status_file.stat().st_mtime
            except FileNotFoundError:
                return None
            if mtime <= state["mtime"]:
                return None
            state["mtime"] = mtime
            try:
                status = client.get_status()
            except Exception:
                return None
            return (status or {}).get("quotes") or None

        return read

    def sample(self, now: pd.Timestamp | None = None) -> bool:
        quotes = self._read_status()
        if not quotes:
            return False
        key = match_quote_key(self.symbol, quotes.keys())
        if key is None:
            return False
        q = quotes[key]
        bid, ask = float(q.get("bid", 0.0)), float(q.get("ask", 0.0))
        if bid <= 0.0 or ask <= 0.0 or ask < bid:
            return False
        ts = now if now is not None else pd.Timestamp.now(tz="UTC")
        with self._lock:
            self._rows.append((ts, bid, ask))
            self._unspilled.append((ts.isoformat(), bid, ask))
        self._last_change = time.time()
        return True

    def spill(self) -> None:
        with self._lock:
            pending, self._unspilled = self._unspilled, []
        if pending:
            day = datetime.now(timezone.utc).date()
            append_spill(pending, spill_path(self.symbol, day, self.live_dir))
        self._last_spill = time.time()

    def preload_spill(self, day: date) -> int:
        df = load_spill(self.symbol, day, self.live_dir)
        with self._lock:
            self._rows = [(r.ts, float(r.bid), float(r.ask))
                          for r in df.itertuples(index=False)] + self._rows
        return len(df)

    def rows_df(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._rows)
        if not rows:
            return pd.DataFrame(columns=SPILL_COLUMNS)
        return pd.DataFrame(rows, columns=SPILL_COLUMNS)

    def staleness_s(self) -> float:
        return float("inf") if self._last_change == 0.0 else time.time() - self._last_change

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval_s):
                try:
                    self.sample()
                    if time.time() - self._last_spill >= SPILL_EVERY_S:
                        self.spill()
                except Exception:
                    pass  # a bad status read must never kill the tap

        self._thread = threading.Thread(target=loop, daemon=True, name="status-tap")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.spill()


# ------------------------------------------------------------- backfill

class DukaBackfill:
    """Per-hour Dukascopy backfill for one (symbol, day) — NEVER writes to
    the immutable data/ticks/ day store. Non-empty hours are cached as
    parquet hour files; unpublished hours are retried at most every
    retry_min minutes (publication lags 1-2 h)."""

    def __init__(self, symbol: str, day: date, live_dir: Path | None = None,
                 retry_min: float = 10.0, fetch_hour_fn=None):
        from scripts.fetch_dukascopy import DEFAULT_POINTS
        self.symbol = symbol
        self.day = day
        self.live_dir = live_dir or LIVE_DIR
        self.retry_min = retry_min
        self._point = DEFAULT_POINTS.get(symbol, 0.001)
        if fetch_hour_fn is None:
            from scripts.fetch_dukascopy_ticks import fetch_hour
            fetch_hour_fn = fetch_hour
        self._fetch_hour = fetch_hour_fn
        self._last_attempt: dict[int, float] = {}

    def hour_path(self, hour: int) -> Path:
        return (self.live_dir / self.symbol /
                f"duka_{self.day.isoformat()}_{hour:02d}.parquet")

    def published_hours(self) -> list[int]:
        return sorted(h for h in range(24) if self.hour_path(h).exists())

    def refresh(self, now: datetime) -> pd.DataFrame:
        from scripts.fetch_dukascopy_ticks import TickFetchError
        for hour in range(24):
            hour_end = datetime(self.day.year, self.day.month, self.day.day,
                                hour, tzinfo=timezone.utc) + timedelta(hours=1)
            if hour_end > now:
                break
            p = self.hour_path(hour)
            if p.exists():
                continue
            if time.time() - self._last_attempt.get(hour, 0.0) < self.retry_min * 60:
                continue
            self._last_attempt[hour] = time.time()
            try:
                df = self._fetch_hour(self.symbol, self.day, hour, self._point)
            except TickFetchError:
                continue
            if df is not None and not df.empty:
                p.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(p, index=False)
        frames = [pd.read_parquet(self.hour_path(h)) for h in self.published_hours()]
        if not frames:
            return pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
        return pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_live_feed.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit** (message via file)

```
feat: StatusTap 1Hz passive sampler + per-hour Dukascopy backfill

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/live_feed.py tests/unit/test_live_feed.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 3: live_marks — closed-candle engine + SignalFeed

**Files:**
- Create: `src/microstructure/live_marks.py`
- Test: `tests/unit/test_live_marks.py`

**Interfaces:**
- Consumes: `features` module (`resample_bars`, `bar_delta`, all 5 detectors, `FlowEvent`).
- Produces (used by Tasks 4, 5):
  - `closed_candle_events(df, timeframe: str, params: dict, now: pd.Timestamp) -> list[FlowEvent]` — params keys exactly: `lookback, band_pts, flow_pctile, ratio, burst_pctile, spread_pctile, price_bin`. Uniform visibility rule: an event is included iff `e.ts.floor(timeframe) + Timedelta(timeframe) <= now`.
  - `@dataclass(frozen=True) FeedEntry(emitted_at: str, bar_ts: str, kind: str, price: float, strength: float)`
  - `class SignalFeed(path: Path | None, price_bin: float = 0.5)` with `entries: list[FeedEntry]` and `ingest(events, now=None) -> list[FeedEntry]` (returns only NEW entries; dedup key `(kind, bar_ts_iso, price_bin_rounded)`; appends jsonl to `path` when set; constructor replays an existing jsonl).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_live_marks.py`:

```python
"""Unit tests for src/microstructure/live_marks.py — synthetic frames only."""
import json

import numpy as np
import pandas as pd
import pytest

from src.microstructure import live_marks as lm
from src.microstructure.features import FlowEvent


def make_ticks(mids, start="2026-07-16 09:00", freq="1s", vol=0.5):
    idx = pd.date_range(start, periods=len(mids), freq=freq, tz="UTC")
    mid = pd.Series(list(mids), index=idx, dtype=float)
    df = pd.DataFrame({"bid": mid - 0.05, "ask": mid + 0.05,
                       "bid_vol": float(vol), "ask_vol": float(vol)})
    df["mid"] = mid
    df["spread"] = df["ask"] - df["bid"]
    return df


PARAMS = dict(lookback=20, band_pts=0.5, flow_pctile=90, ratio=3.0,
              burst_pctile=95, spread_pctile=95, price_bin=0.5)


class TestClosedCandleEvents:
    def test_forming_bar_event_hidden_until_close(self):
        # 20 upticks in the 09:00 5m bar -> one imbalance_buy at bar_ts 09:00
        df = make_ticks([3300.00 + 0.01 * i for i in range(20)])
        during = pd.Timestamp("2026-07-16 09:03:00", tz="UTC")   # bar still forming
        after = pd.Timestamp("2026-07-16 09:05:00", tz="UTC")    # bar closed
        assert lm.closed_candle_events(df, "5min", PARAMS, during) == []
        events = lm.closed_candle_events(df, "5min", PARAMS, after)
        assert any(e.kind == "imbalance_buy" for e in events)

    def test_events_sorted_by_time(self):
        a = make_ticks([3300.00 + 0.01 * i for i in range(20)], start="2026-07-16 09:00")
        b = make_ticks([3310.00 - 0.01 * i for i in range(20)], start="2026-07-16 09:05")
        df = pd.concat([a, b])
        now = pd.Timestamp("2026-07-16 09:10:00", tz="UTC")
        events = lm.closed_candle_events(df, "5min", PARAMS, now)
        ts = [e.ts for e in events]
        assert ts == sorted(ts)


class TestSignalFeed:
    def _events(self):
        return [FlowEvent(pd.Timestamp("2026-07-16 09:00", tz="UTC"), 3300.0, 5.0,
                          "imbalance_buy")]

    def test_dedup_and_new_only(self, tmp_path):
        feed = lm.SignalFeed(tmp_path / "sig.jsonl")
        now = pd.Timestamp("2026-07-16 09:05:01", tz="UTC")
        first = feed.ingest(self._events(), now=now)
        assert len(first) == 1 and first[0].kind == "imbalance_buy"
        assert feed.ingest(self._events(), now=now) == []
        assert len(feed.entries) == 1

    def test_jsonl_persistence_and_replay(self, tmp_path):
        p = tmp_path / "sig.jsonl"
        feed = lm.SignalFeed(p)
        feed.ingest(self._events(), now=pd.Timestamp("2026-07-16 09:05:01", tz="UTC"))
        lines = [json.loads(l) for l in p.read_text().splitlines()]
        assert lines[0]["kind"] == "imbalance_buy"
        feed2 = lm.SignalFeed(p)                      # replay
        assert len(feed2.entries) == 1
        assert feed2.ingest(self._events()) == []     # replayed keys deduped

    def test_no_path_in_memory_only(self):
        feed = lm.SignalFeed(None)
        assert len(feed.ingest(self._events())) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_live_marks.py -v`
Expected: ERROR — `ModuleNotFoundError: ... 'src.microstructure.live_marks'`

- [ ] **Step 3: Implement**

Create `src/microstructure/live_marks.py`:

```python
"""
Pure live-marking engine over a stitched day frame.

Closed-candle gating: a mark becomes visible only once its bar (of the
viewing timeframe) has closed — it then never un-happens in the FEED, which
is append-only and persisted as jsonl (the paper-trail for judging live
usefulness, and any future Stage-2 labeling). The chart's detector output
may still drift as day-percentiles evolve; the feed does not.

No I/O here except SignalFeed's jsonl append. All quantities remain proxies.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from src.microstructure import features as ft
from src.microstructure.features import FlowEvent


def closed_candle_events(df: pd.DataFrame, timeframe: str, params: dict,
                         now: pd.Timestamp) -> list[FlowEvent]:
    """Run all five detectors; keep only events whose bar has closed.

    In the tap-covered window only divergence/absorption/imbalance can fire
    (a 1 Hz tap gives a constant arrival rate, so the sweep burst leg and
    withdrawal rate leg are inert there); sweeps/withdrawals firm up as
    Dukascopy hours backfill.
    """
    if df.empty:
        return []
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    events: list[FlowEvent] = []
    events += ft.delta_divergence(bars, delta, lookback=int(params["lookback"]))
    events += ft.absorption_zones(df, band_pts=params["band_pts"],
                                  flow_pctile=params["flow_pctile"])
    events += ft.imbalance_events(df, freq=timeframe,
                                  price_bin=params["price_bin"], ratio=params["ratio"])
    events += ft.sweep_events(df, burst_pctile=params["burst_pctile"])
    events += ft.liquidity_withdrawal(df, spread_pctile=params["spread_pctile"])
    td = pd.Timedelta(timeframe)
    closed = [e for e in events if e.ts.floor(timeframe) + td <= now]
    return sorted(closed, key=lambda e: (e.ts, e.kind))


@dataclass(frozen=True)
class FeedEntry:
    emitted_at: str
    bar_ts: str
    kind: str
    price: float
    strength: float


class SignalFeed:
    """Append-only signal log with dedup on (kind, bar_ts, price_bin)."""

    def __init__(self, path: Path | None, price_bin: float = 0.5):
        self.path = Path(path) if path is not None else None
        self.price_bin = price_bin
        self.entries: list[FeedEntry] = []
        self._seen: set[tuple] = set()
        if self.path is not None and self.path.exists():
            for line in self.path.read_text().splitlines():
                d = json.loads(line)
                entry = FeedEntry(**d)
                self.entries.append(entry)
                self._seen.add(self._key_from(entry.kind, entry.bar_ts, entry.price))

    def _key_from(self, kind: str, bar_ts_iso: str, price: float) -> tuple:
        return (kind, bar_ts_iso, round(price / self.price_bin) * self.price_bin)

    def ingest(self, events: list[FlowEvent],
               now: pd.Timestamp | None = None) -> list[FeedEntry]:
        stamp = (now if now is not None else pd.Timestamp.now(tz="UTC")).isoformat()
        new: list[FeedEntry] = []
        for e in events:
            key = self._key_from(e.kind, e.ts.isoformat(), e.price)
            if key in self._seen:
                continue
            self._seen.add(key)
            entry = FeedEntry(emitted_at=stamp, bar_ts=e.ts.isoformat(),
                              kind=e.kind, price=float(e.price),
                              strength=float(e.strength))
            self.entries.append(entry)
            new.append(entry)
        if new and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                for entry in new:
                    f.write(json.dumps(asdict(entry)) + "\n")
        return new
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_live_marks.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit** (message via file)

```
feat: closed-candle event gating + append-only signal feed

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/live_marks.py tests/unit/test_live_marks.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 4: live_marks — defended levels + inferred liquidity pools

**Files:**
- Modify: `src/microstructure/live_marks.py` (append)
- Test: `tests/unit/test_live_marks.py` (append)

**Interfaces:**
- Produces (used by Task 5):
  - `@dataclass(frozen=True) DefendedLevel(price: float, side: str, touches: int, last_ts: pd.Timestamp)` — side ∈ {`buyers`, `sellers`}
  - `defended_levels(events: list[FlowEvent], band_pts: float = 1.0) -> list[DefendedLevel]` — clusters absorption events by price band, sorted by touches desc
  - `@dataclass(frozen=True) LiquidityPool(price: float, side: str, kind: str)` — side ∈ {`buy_side`, `sell_side`}; kind ∈ {`swing_high`, `swing_low`, `equal_highs`, `equal_lows`, `round`}
  - `liquidity_pools(bars: pd.DataFrame, swing_bars: int = 5, eq_tol_pts: float = 0.5, round_step: float = 5.0, max_levels: int = 12) -> list[LiquidityPool]` — bars is `resample_bars` output; un-swept confirmed swings, equal-extreme clusters, round numbers; sorted by distance to last close, capped at max_levels

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_live_marks.py`)

```python
class TestDefendedLevels:
    def _absorb(self, price, ts_min, kind="absorption_of_selling"):
        return FlowEvent(pd.Timestamp(f"2026-07-16 09:{ts_min:02d}", tz="UTC"),
                         price, 10.0, kind)

    def test_clusters_by_band_and_counts_touches(self):
        events = [self._absorb(3300.1, 0), self._absorb(3300.4, 10),   # same 1pt band
                  self._absorb(3305.0, 20, "absorption_of_buying"),
                  FlowEvent(pd.Timestamp("2026-07-16 09:30", tz="UTC"),
                            3300.0, 5.0, "imbalance_buy")]              # ignored
        levels = lm.defended_levels(events, band_pts=1.0)
        assert len(levels) == 2
        top = levels[0]
        assert top.touches == 2 and top.side == "buyers"
        assert top.price == pytest.approx((3300.1 + 3300.4) / 2)
        assert top.last_ts == pd.Timestamp("2026-07-16 09:10", tz="UTC")
        assert levels[1].side == "sellers" and levels[1].touches == 1

    def test_empty_without_absorption(self):
        assert lm.defended_levels([]) == []


class TestLiquidityPools:
    def _bars(self, closes, highs=None, lows=None):
        idx = pd.date_range("2026-07-16 09:00", periods=len(closes), freq="5min", tz="UTC")
        c = pd.Series(list(closes), index=idx, dtype=float)
        return pd.DataFrame({"open": c, "high": highs if highs is not None else c + 0.1,
                             "low": lows if lows is not None else c - 0.1,
                             "close": c, "ticks": 10}, index=idx)

    def test_unswept_swing_high_is_buy_side_pool(self):
        # peak at bar 7 (3310), never exceeded later; price ends at 3300
        closes = [3300, 3301, 3302, 3304, 3306, 3308, 3309, 3310,
                  3308, 3306, 3304, 3302, 3301, 3300, 3300, 3300]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3,
                                   round_step=0.0)          # round layer off
        kinds = {(p.kind, p.side) for p in pools}
        assert ("swing_high", "buy_side") in kinds

    def test_swept_swing_dropped(self):
        # first peak 3310 at bar 5 later exceeded by 3312 -> only the later
        # (unswept) high survives
        closes = [3300, 3304, 3308, 3310, 3308, 3304, 3300, 3304,
                  3308, 3312, 3308, 3304, 3300, 3300, 3300, 3300]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=2, round_step=0.0)
        highs = [p.price for p in pools if p.kind == "swing_high"]
        assert 3310.1 not in highs                    # swept peak absent
        assert any(abs(h - 3312.1) < 1e-9 for h in highs)

    def test_round_numbers_both_sides(self):
        closes = [3302.0] * 16
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3, round_step=5.0)
        rounds = [(p.price, p.side) for p in pools if p.kind == "round"]
        assert (3305.0, "buy_side") in rounds
        assert (3300.0, "sell_side") in rounds

    def test_equal_lows_cluster(self):
        # two un-swept swing lows 0.2 pts apart (each strictly above the
        # other's low never being breached later) -> one equal_lows pool
        closes = [3305, 3303, 3300.1, 3303, 3305, 3303, 3300.3, 3303,
                  3305, 3305, 3305]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=2,
                                   eq_tol_pts=0.5, round_step=0.0)
        eq = [p for p in pools if p.kind == "equal_lows"]
        assert len(eq) == 1
        assert eq[0].side == "sell_side"
        assert eq[0].price == pytest.approx((3300.0 + 3300.2) / 2)

    def test_max_levels_cap(self):
        closes = [3302.0] * 16
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3,
                                   round_step=1.0, max_levels=3)
        assert len(pools) <= 3
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_live_marks.py -v`
Expected: new tests FAIL — `AttributeError: ... 'defended_levels'`

- [ ] **Step 3: Implement** (append to `src/microstructure/live_marks.py`; add `import numpy as np` to the module imports)

```python
# ---------------------------------------------------- where-are-the-orders

@dataclass(frozen=True)
class DefendedLevel:
    """EVIDENCE layer: a price band where absorption keeps recurring —
    someone's resting orders are eating flow there."""
    price: float
    side: str          # "buyers" (selling absorbed) / "sellers" (buying absorbed)
    touches: int
    last_ts: pd.Timestamp


def defended_levels(events: list[FlowEvent],
                    band_pts: float = 1.0) -> list[DefendedLevel]:
    clusters: dict[tuple, dict] = {}
    for e in events:
        if not e.kind.startswith("absorption"):
            continue
        key = (round(e.price / band_pts) * band_pts, e.kind)
        c = clusters.setdefault(key, {"touches": 0, "last_ts": e.ts, "prices": []})
        c["touches"] += 1
        c["last_ts"] = max(c["last_ts"], e.ts)
        c["prices"].append(e.price)
    out = []
    for (_, kind), c in clusters.items():
        side = "buyers" if kind == "absorption_of_selling" else "sellers"
        out.append(DefendedLevel(price=float(np.mean(c["prices"])), side=side,
                                 touches=c["touches"], last_ts=c["last_ts"]))
    return sorted(out, key=lambda d: (-d.touches, d.price))


@dataclass(frozen=True)
class LiquidityPool:
    """HEURISTIC (inferred) layer: where stops/limits statistically cluster —
    un-swept swings, equal extremes, round numbers. Inference, not data."""
    price: float
    side: str          # "buy_side" (above price) / "sell_side" (below price)
    kind: str          # swing_high | swing_low | equal_highs | equal_lows | round


def liquidity_pools(bars: pd.DataFrame, swing_bars: int = 5,
                    eq_tol_pts: float = 0.5, round_step: float = 5.0,
                    max_levels: int = 12) -> list[LiquidityPool]:
    if len(bars) < 2 * swing_bars + 1:
        return []
    high, low, close = bars["high"], bars["low"], bars["close"]
    last = float(close.iloc[-1])

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(swing_bars, len(bars) - swing_bars):
        h = float(high.iloc[i])
        window_h = high.iloc[i - swing_bars:i + swing_bars + 1]
        if h == float(window_h.max()) and float(high.iloc[i + 1:].max()) < h:
            swing_highs.append(h)                     # confirmed AND un-swept
        l = float(low.iloc[i])
        window_l = low.iloc[i - swing_bars:i + swing_bars + 1]
        if l == float(window_l.min()) and float(low.iloc[i + 1:].min()) > l:
            swing_lows.append(l)

    def cluster(levels: list[float], eq_kind: str, solo_kind: str) -> list[tuple]:
        out, group = [], []
        for lvl in sorted(levels):
            if group and lvl - group[-1] > eq_tol_pts:
                out.append((float(np.mean(group)),
                            eq_kind if len(group) >= 2 else solo_kind))
                group = []
            group.append(lvl)
        if group:
            out.append((float(np.mean(group)),
                        eq_kind if len(group) >= 2 else solo_kind))
        return out

    pools: list[LiquidityPool] = []
    for price, kind in cluster(swing_highs, "equal_highs", "swing_high"):
        pools.append(LiquidityPool(price, "buy_side" if price > last else "sell_side",
                                   kind))
    for price, kind in cluster(swing_lows, "equal_lows", "swing_low"):
        pools.append(LiquidityPool(price, "buy_side" if price > last else "sell_side",
                                   kind))
    if round_step > 0:
        base = round(last / round_step) * round_step
        for k in (-2, -1, 0, 1, 2):
            lvl = base + k * round_step
            if abs(lvl - last) < 1e-9:
                continue
            pools.append(LiquidityPool(float(lvl),
                                       "buy_side" if lvl > last else "sell_side",
                                       "round"))
    pools.sort(key=lambda p: abs(p.price - last))
    return pools[:max_levels]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_live_marks.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit** (message via file)

```
feat: defended-level map + inferred liquidity pools

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add src/microstructure/live_marks.py tests/unit/test_live_marks.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 5: Viewer LIVE mode

**Files:**
- Modify: `scripts/orderflow_viewer.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4 exactly as declared, plus existing `build_figure`, `MARK_STYLE`, `_ticks`.
- Produces: `build_figure(df, timeframe, show, p, events=None)` — new optional `events` param: when given, `_detect` is skipped and these events are drawn; when `show` contains `"defended"`/`"pools"`, those layers render from the events/bars. HISTORY behavior with `events=None` is byte-for-byte unchanged.

- [ ] **Step 1: Apply the modifications**

In `scripts/orderflow_viewer.py` make exactly these changes:

1. Extend the module docstring's usage block with one line: `LIVE mode: switch the Mode radio to LIVE while the bot's MT5 terminal runs — marks appear as each candle closes; reads ONLY mt5_status.json (never the bridge command channel).`

2. Add imports after the existing `from src.microstructure import features as ft`:

```python
from src.microstructure import live_feed as lfeed  # noqa: E402
from src.microstructure import live_marks as lmarks  # noqa: E402
import pandas as pd  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
```

3. Add after `MARK_STYLE`:

```python
LIVE_PARAM_KEYS = ("lookback", "band_pts", "flow_pctile", "ratio",
                   "burst_pctile", "spread_pctile", "price_bin")
_LIVE: dict = {"tap": None, "backfill": None, "feed": None, "day": None}


def _ensure_live(symbol: str):
    """Start (once) the tap/backfill/feed trio for today. Idempotent; rolls
    over automatically when the UTC day changes."""
    today = datetime.now(timezone.utc).date()
    if _LIVE["tap"] is None or _LIVE["day"] != today:
        if _LIVE["tap"] is not None:
            _LIVE["tap"].stop()
        tap = lfeed.StatusTap(symbol)
        tap.preload_spill(today)
        tap.start()
        _LIVE.update(
            tap=tap,
            backfill=lfeed.DukaBackfill(symbol, today),
            feed=lmarks.SignalFeed(lfeed.LIVE_DIR / symbol / f"{today}_signals.jsonl"),
            day=today,
        )
    return _LIVE["tap"], _LIVE["backfill"], _LIVE["feed"]


def _feed_color(kind: str) -> str:
    good = ("buy", "bullish", "selling", "low")   # *_of_selling = buyers defending
    return "#2ca02c" if any(g in kind for g in good) else "#d62728"


def _feed_table(entries, limit=50):
    rows = [html.Tr([
        html.Td(e.emitted_at[11:19]),
        html.Td(e.bar_ts[11:16]),
        html.Td(e.kind, style={"color": _feed_color(e.kind)}),
        html.Td(f"{e.price:.2f}"),
        html.Td(f"{e.strength:.2f}"),
    ]) for e in list(entries)[::-1][:limit]]
    header = html.Tr([html.Th(h) for h in
                      ("emitted", "bar", "kind", "price", "strength")])
    return html.Table([header] + rows, style={"fontSize": "12px", "width": "100%"})
```

4. Change `build_figure`'s signature to `def build_figure(df, timeframe, show, p, events=None):` and replace the line `events = _detect(df, bars, delta, show, {**p, "timeframe": timeframe})` with:

```python
    if events is None:
        events = _detect(df, bars, delta, show, {**p, "timeframe": timeframe})
```

Then, immediately after the mark-scatter loop (`for kind, evs in by_kind.items(): ...`), add:

```python
    if "defended" in show:
        for lvl in lmarks.defended_levels(events):
            color = "42,160,44" if lvl.side == "buyers" else "214,39,40"
            fig.add_hline(y=lvl.price, row=1, col=1,
                          line=dict(color=f"rgba({color},{min(0.25 + 0.15 * lvl.touches, 0.9)})",
                                    width=2 + lvl.touches),
                          annotation_text=f"defended x{lvl.touches}",
                          annotation_font_size=9)
    if "pools" in show:
        for pool in lmarks.liquidity_pools(bars):
            fig.add_hline(y=pool.price, row=1, col=1,
                          line=dict(color="rgba(128,0,128,0.55)", width=1, dash="dash"),
                          annotation_text=f"{pool.kind} ({pool.side}, inferred)",
                          annotation_font_size=8)
```

5. In `make_app`: add to the layout controls, directly under the `H3` title:

```python
        dcc.RadioItems(["HISTORY", "LIVE"], "HISTORY", id="mode", inline=True),
        html.Div(id="live-status", style={"fontSize": "12px", "margin": "4px 0"}),
```

Extend the marks `dcc.Checklist` options list with `"defended", "pools"` (defaults unchanged). Add below the controls column (inside the sidebar Div, after the last slider):

```python
        html.Div(id="feed", style={"maxHeight": "320px", "overflowY": "auto",
                                   "marginTop": "8px"}),
        dcc.Interval(id="live-interval", interval=20_000, disabled=True),
```

6. Add a small mode callback inside `make_app`:

```python
    @app.callback(Output("live-interval", "disabled"), Output("dates", "disabled"),
                  Input("mode", "value"))
    def toggle_mode(mode):
        live = mode == "LIVE"
        return (not live), live
```

7. Change the main `update` callback: add `Input("mode", "value")` and `Input("live-interval", "n_intervals")` (after the existing inputs), add `Output("feed", "children")` and `Output("live-status", "children")` to the outputs, and replace the body with:

```python
    def update(start, end, timeframe, show, lookback, band_pts, flow_pctile,
               ratio, burst_pctile, spread_pctile, price_bin, mode, _n):
        params = dict(lookback=lookback, band_pts=band_pts, flow_pctile=flow_pctile,
                      ratio=ratio, burst_pctile=burst_pctile,
                      spread_pctile=spread_pctile, price_bin=price_bin)
        show = show or []
        if mode == "LIVE":
            tap, backfill, feed = _ensure_live(symbol)
            now = pd.Timestamp.now(tz="UTC")
            df = lfeed.stitch_day(backfill.refresh(now.to_pydatetime()),
                                  tap.rows_df())
            stale = tap.staleness_s()
            badge_style = {"color": "#d62728" if stale > 15 else "#2ca02c"}
            hours = backfill.published_hours()
            status = html.Span([
                html.B("LIVE ", style=badge_style),
                f"tap {'∞' if stale == float('inf') else f'{stale:.0f}s'} | "
                f"backfill→{(f'{hours[-1]:02d}h' if hours else '—')} | "
                f"{len(df):,} ticks | feed {len(feed.entries)}",
            ], style=badge_style if stale > 15 else None)
            if df.empty:
                return (go.Figure(layout=dict(
                    title="LIVE: no data yet — is MT5 running? (backfill lags 1-2h)")),
                    _feed_table(feed.entries), status)
            events = lmarks.closed_candle_events(df, timeframe, params, now)
            feed.ingest(events, now)
            fig = build_figure(df, timeframe, show, params, events=events)
            return fig, _feed_table(feed.entries), status
        df = _ticks(symbol, start[:10], end[:10])
        return (build_figure(df, timeframe, show, params),
                "(feed active in LIVE mode)", "")
```

- [ ] **Step 2: Headless verification — HISTORY path unchanged + LIVE layers render**

```bash
./venv/bin/python -c "
import pandas as pd
from datetime import date
from src.microstructure import features as ft
from src.microstructure.features import FlowEvent
from scripts.orderflow_viewer import build_figure
df = ft.load_ticks('XAUUSD', date(2026, 7, 7), date(2026, 7, 9))
p = dict(lookback=20, band_pts=0.5, flow_pctile=90, ratio=3.0,
         burst_pctile=95, spread_pctile=95, price_bin=0.5)
f1 = build_figure(df, '5min', ['divergence', 'sweep'], p)          # HISTORY path
ev = [FlowEvent(df.index[1000], float(df['mid'].iloc[1000]), 9.9,
                'absorption_of_selling')]
f2 = build_figure(df, '5min', ['defended', 'pools'], p, events=ev)  # LIVE layers
n_hlines = len([s for s in f2.layout.shapes or []])
print('history traces', len(f1.data), '| live shapes', n_hlines)
assert len(f1.data) >= 3 and n_hlines >= 2
print('OK')"
```

Expected: trace/shape counts printed, `OK`.

- [ ] **Step 3: Headless server smoke with a fake status file**

The tap must run without a real MT5. Start the server, confirm Dash HTML, stop:

```bash
./venv/bin/python scripts/orderflow_viewer.py --port 8056 &
sleep 6
curl -s http://127.0.0.1:8056 | grep -c dash
kill %1
```

Expected: a count ≥ 1 (Dash HTML served). (LIVE mode itself needs the real EA status file — user-verified later; a missing status file must NOT crash the server: the tap simply records nothing.)

- [ ] **Step 4: Run the full unit suite for regressions**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass, zero failures (no count regression vs the run at task start).

- [ ] **Step 5: Commit** (message via file)

```
feat: viewer LIVE mode — closed-candle marks, signal feed, level layers

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add scripts/orderflow_viewer.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 6: Broker DOM probe (EA + checker)

**Files:**
- Create: `mt5_bridge/EA_DOMProbe.mq5`
- Create: `scripts/check_dom_probe.py`
- Test: `tests/unit/test_check_dom_probe.py`

**Interfaces:**
- Produces: `classify_snapshots(snapshots: list[dict]) -> str` in `scripts/check_dom_probe.py`, returning exactly one of `"NO BOOK"`, `"TOP-OF-BOOK ONLY"`, `"REAL DEPTH"`. A snapshot is the parsed probe JSON: `{"ts": str, "symbol": str, "levels": [{"type": int, "price": float, "volume": float}, ...]}`.

- [ ] **Step 1: Write the EA**

Create `mt5_bridge/EA_DOMProbe.mq5`:

```mql5
//+------------------------------------------------------------------+
//| EA_DOMProbe.mq5 — READ-ONLY depth-of-market probe.               |
//| Writes book snapshots to its own file in Common Files. Sends NO  |
//| commands — cannot interact with EA_FileBridge's command channel. |
//| Attach to the XAUUSDs chart alongside (not instead of) the bot EA.|
//+------------------------------------------------------------------+
#property strict
input string OutFile = "mt5_dom_probe.json";

int OnInit()
{
   if(!MarketBookAdd(_Symbol))
      Print("DOMProbe: MarketBookAdd failed for ", _Symbol,
            " — broker likely publishes no book");
   EventSetTimer(5); // heartbeat write even when no book events arrive
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   MarketBookRelease(_Symbol);
   EventKillTimer();
}

void OnTimer()   { WriteBook(); }
void OnBookEvent(const string &symbol)
{
   if(symbol == _Symbol) WriteBook();
}

void WriteBook()
{
   MqlBookInfo book[];
   bool got = MarketBookGet(_Symbol, book);
   string json = "{\"ts\":\"" + TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS) +
                 "\",\"symbol\":\"" + _Symbol + "\",\"levels\":[";
   if(got)
   {
      for(int i = 0; i < ArraySize(book); i++)
      {
         if(i > 0) json += ",";
         json += "{\"type\":" + IntegerToString(book[i].type) +
                 ",\"price\":" + DoubleToString(book[i].price, _Digits) +
                 ",\"volume\":" + DoubleToString((double)book[i].volume, 2) + "}";
      }
   }
   json += "]}";
   int h = FileOpen(OutFile, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(h != INVALID_HANDLE)
   {
      FileWriteString(h, json);
      FileClose(h);
   }
}
```

- [ ] **Step 2: Write the failing classifier tests**

Create `tests/unit/test_check_dom_probe.py`:

```python
"""Unit tests for the DOM-probe verdict classifier — no MT5."""
from scripts.check_dom_probe import classify_snapshots


def _snap(levels):
    return {"ts": "2026.07.16 10:00:00", "symbol": "XAUUSDs", "levels": levels}


def test_no_book_when_levels_always_empty():
    assert classify_snapshots([_snap([]) for _ in range(10)]) == "NO BOOK"


def test_no_book_when_no_snapshots():
    assert classify_snapshots([]) == "NO BOOK"


def test_top_of_book_when_two_static_levels():
    lv = [{"type": 1, "price": 3300.2, "volume": 1.0},
          {"type": 2, "price": 3300.0, "volume": 1.0}]
    assert classify_snapshots([_snap(lv) for _ in range(10)]) == "TOP-OF-BOOK ONLY"


def test_real_depth_when_many_levels_changing():
    snaps = []
    for i in range(10):
        snaps.append(_snap([
            {"type": 1, "price": 3300.2 + 0.1 * j, "volume": 1.0 + i + j}
            for j in range(5)
        ] + [
            {"type": 2, "price": 3300.0 - 0.1 * j, "volume": 2.0 + i}
            for j in range(5)
        ]))
    assert classify_snapshots(snaps) == "REAL DEPTH"
```

- [ ] **Step 3: Run to verify failure**

Run: `./venv/bin/pytest tests/unit/test_check_dom_probe.py -v`
Expected: ERROR — `ModuleNotFoundError: ... 'scripts.check_dom_probe'`

- [ ] **Step 4: Write the checker**

Create `scripts/check_dom_probe.py`:

```python
#!/usr/bin/env python3
"""
Broker DOM probe verdict — reads mt5_dom_probe.json (written by
mt5_bridge/EA_DOMProbe.mq5) for ~60 s and prints one verdict:

  NO BOOK            broker publishes nothing (or EA not attached/heartbeat dead)
  TOP-OF-BOOK ONLY   <=2 levels or volumes never change (synthetic quote echo)
  REAL DEPTH         >2 levels with changing volumes -> a real book exists

Decision gate (spec 2026-07-16): only REAL DEPTH justifies designing a DOM
heatmap layer; the other verdicts close the resting-order question.

Usage:
    python scripts/check_dom_probe.py [--seconds 60]
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mt5_bridge"))


def classify_snapshots(snapshots: list[dict]) -> str:
    """Pure verdict logic over parsed probe snapshots."""
    with_levels = [s for s in snapshots if s.get("levels")]
    if not with_levels:
        return "NO BOOK"
    max_levels = max(len(s["levels"]) for s in with_levels)
    volume_sets = {tuple(round(l["volume"], 4) for l in s["levels"])
                   for s in with_levels}
    if max_levels <= 2 or len(volume_sets) <= 1:
        return "TOP-OF-BOOK ONLY"
    return "REAL DEPTH"


def main() -> int:
    p = argparse.ArgumentParser(description="Classify the broker's DOM feed")
    p.add_argument("--seconds", type=int, default=60)
    args = p.parse_args()

    from mt5_file_client import MT5FileClient
    probe = MT5FileClient().data_dir / "mt5_dom_probe.json"
    print(f"Watching {probe} for {args.seconds}s "
          f"(EA_DOMProbe must be attached to the XAUUSDs chart)…")

    snapshots, last_mtime = [], 0.0
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        try:
            mtime = probe.stat().st_mtime
        except FileNotFoundError:
            time.sleep(1)
            continue
        if mtime > last_mtime:
            last_mtime = mtime
            try:
                snapshots.append(json.loads(probe.read_text()))
            except (json.JSONDecodeError, OSError):
                pass  # mid-write race — next heartbeat wins
        time.sleep(1)

    if not snapshots:
        print("VERDICT: NO BOOK (probe file never appeared/updated — "
              "is EA_DOMProbe compiled+attached?)")
        return 1
    verdict = classify_snapshots(snapshots)
    n = max((len(s.get("levels", [])) for s in snapshots), default=0)
    print(f"VERDICT: {verdict} ({len(snapshots)} snapshots, max {n} levels)")
    if verdict == "REAL DEPTH":
        print("→ a real book exists — a DOM heatmap layer is worth designing.")
    else:
        print("→ no usable resting-order data; the DOM question is closed "
              "(spec decision gate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./venv/bin/pytest tests/unit/test_check_dom_probe.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit** (message via file)

```
feat: read-only broker DOM probe EA + verdict checker

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

```bash
git add mt5_bridge/EA_DOMProbe.mq5 scripts/check_dom_probe.py tests/unit/test_check_dom_probe.py
git commit -F <scratch-msg-file> && git log -1 --format=%B
```

---

### Task 7: Final verification

- [ ] **Step 1: Full unit suite**

Run: `./venv/bin/pytest tests/unit -q`
Expected: all pass, zero failures.

- [ ] **Step 2: Live-system isolation check**

```bash
git log --stat --oneline <base>..HEAD | grep -E "^\s+\S+\s+\|" | awk '{print $1}' | sort -u
```

Expected: ONLY `.gitignore`, `src/microstructure/live_feed.py`, `src/microstructure/live_marks.py`, `scripts/orderflow_viewer.py`, `scripts/check_dom_probe.py`, `mt5_bridge/EA_DOMProbe.mq5`, `tests/unit/test_live_feed.py`, `tests/unit/test_live_marks.py`, `tests/unit/test_check_dom_probe.py` — nothing under `config/`, `src/strategies/`, `src/risk/`, `src/execution/`, `src/data/`, and NOT `mt5_bridge/EA_FileBridge.mq5` or `mt5_bridge/mt5_file_client.py`.

- [ ] **Step 3: User handoff notes (report, no code)**

The user's live verification steps, to include in the final summary:
1. With the bot's MT5 running: `./venv/bin/python scripts/orderflow_viewer.py`, switch Mode → LIVE. Expect the green tap badge within ~30 s, candles growing, marks appearing on candle closes, feed rows accumulating; sweeps/withdrawals only appear on backfilled hours.
2. DOM probe: compile `mt5_bridge/EA_DOMProbe.mq5` in MetaEditor, attach to the `XAUUSDs` chart (keep EA_FileBridge running), then `./venv/bin/python scripts/check_dom_probe.py`. Report the verdict back — only `REAL DEPTH` reopens the DOM-layer design.
