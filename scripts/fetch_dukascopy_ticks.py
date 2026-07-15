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
