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
