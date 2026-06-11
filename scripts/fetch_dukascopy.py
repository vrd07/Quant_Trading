#!/usr/bin/env python3
"""
Fetch free historical 1m candles from Dukascopy's datafeed and emit the
canonical {SYMBOL}_5m_real.csv used by run_backtest.py / regime tooling.

Why: yfinance caps 5m intraday history at ~60 days, so any backtest longer
than two months on a new symbol needs another source. Dukascopy serves
per-day BID_candles_min_1.bi5 files (LZMA, 24-byte big-endian records:
time-offset sec, open, close, low, high in integer points, volume float32)
back many years, no API key.

Usage:
    python scripts/fetch_dukascopy.py --symbol GBPJPY --start 2026-01-01 --end 2026-06-11
    python scripts/fetch_dukascopy.py --symbol GBPJPY --start 2026-01-01 --end 2026-06-11 --point 0.001

Merges into data/historical/{SYMBOL}_5m_real.csv via the same merge-safe
helper the weekly refresh job uses (concat + dedupe, never truncates).
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

from scripts.refresh_historical_data import merge_into_csv, _target_csv  # noqa: E402

BASE = "https://datafeed.dukascopy.com/datafeed"
RECORD = struct.Struct(">IIIIIf")  # offset_sec, open, close, low, high, volume

# Decimal places of one integer "point" per instrument family.
# JPY-quoted FX = 0.001, most other FX = 0.00001, XAU = 0.001.
DEFAULT_POINTS = {
    "GBPJPY": 0.001, "USDJPY": 0.001, "EURJPY": 0.001, "CHFJPY": 0.001,
    "EURUSD": 0.00001, "GBPUSD": 0.00001, "AUDUSD": 0.00001,
    "XAUUSD": 0.001,
}


def fetch_day(symbol: str, day: date, point: float, side: str = "BID",
              retries: int = 4) -> pd.DataFrame | None:
    # Dukascopy months are 0-indexed in the URL path.
    url = f"{BASE}/{symbol}/{day.year}/{day.month - 1:02d}/{day.day:02d}/{side}_candles_min_1.bi5"
    raw = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # day not published (today / far future)
            if attempt == retries - 1:
                print(f"  ⚠️ {day} fetch failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == retries - 1:
                print(f"  ⚠️ {day} fetch failed: {e}")
                return None
            time.sleep(2 * (attempt + 1))
    if not raw:
        return None  # weekend / holiday — server returns empty body
    data = lzma.decompress(raw)
    base_ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    rows = []
    for (off, o, c, lo, hi, vol) in RECORD.iter_unpack(data):
        if o == 0:
            continue
        rows.append((base_ts + timedelta(seconds=off),
                     o * point, hi * point, lo * point, c * point, vol))
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch Dukascopy 1m candles → canonical 5m CSV")
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--point", type=float, default=None,
                   help="Price units per integer point (default: known-symbol table)")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    point = args.point or DEFAULT_POINTS.get(args.symbol)
    if point is None:
        p.error(f"--point required for unknown symbol {args.symbol!r}")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    # Skip Saturdays outright (FX closed); Sundays open ~21:00 UTC, keep them.
    days = [d for d in days if d.weekday() != 5]

    print(f"Fetching {args.symbol} 1m candles {start} → {end} ({len(days)} days, point={point})")
    frames = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for df in ex.map(lambda d: fetch_day(args.symbol, d, point), days):
            if df is not None:
                frames.append(df)

    if not frames:
        print("❌ no data fetched")
        return 1

    m1 = pd.concat(frames).set_index("timestamp").sort_index()
    m1 = m1[~m1.index.duplicated(keep="last")]
    # Dukascopy pads closed-market hours with flat zero-volume candles that
    # repeat the last close — they collapse ATR and blow up z-scores. Drop them.
    flat = (m1.open == m1.close) & (m1.high == m1.low) & (m1.volume == 0)
    m1 = m1[~flat]
    m5 = m1.resample("5min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"]).reset_index()

    target = _target_csv(args.symbol)
    summary = merge_into_csv(m5, target, backup_suffix=None if not target.exists()
                             else f"pre_dukascopy_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}")
    print(f"✅ {args.symbol}: {len(m1):,} × 1m → {summary['rows_after']:,} × 5m bars in {target.name}")
    print(f"   range: {summary['first_ts']} → {summary['last_ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
