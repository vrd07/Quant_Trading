#!/usr/bin/env python3
"""
Merge-safe refresh of historical 5m OHLCV CSVs used by the regime classifier.

Unlike scripts/download_historical_data.py which OVERWRITES the target CSV
(yfinance only serves ~60 days of 5m, so a naive overwrite truncates the
file), this wrapper downloads the last N days and CONCATENATES + DEDUPES
with the existing CSV, preserving all older bars.

Designed for the weekly launchd job com.quanttrading.data-refresh.
Also doubles as a one-shot restore helper via --restore-from <path-to-MT5-export>.

Usage:
    python scripts/refresh_historical_data.py                       # XAUUSD, 58d
    python scripts/refresh_historical_data.py --symbols XAUUSD BTCUSD
    python scripts/refresh_historical_data.py --restore-from data/historical/XAUUSD_M5.csv
"""

import argparse
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.download_historical_data import TICKER_MAP, download_all  # noqa: E402
from scripts.convert_mt5_export import parse_mt5_csv  # noqa: E402


def _target_csv(symbol: str) -> Path:
    return PROJECT_ROOT / "data" / "historical" / f"{symbol}_5m_real.csv"


def _load_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        raise ValueError(f"{path} missing 'timestamp' column (got {list(df.columns)})")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce timestamp to tz-aware UTC and keep canonical OHLCV columns."""
    df = df.copy()
    # parse_mt5_csv emits two 'volume' columns when an MT5 export has both
    # <TICKVOL> and <VOL>. Keep the first occurrence so the canonical
    # 6-column selection below doesn't reindex with a duplicate axis.
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    if "timestamp" not in df.columns:
        df = df.reset_index().rename(columns={df.index.name or "index": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["timestamp", "open", "high", "low", "close", "volume"]]


def merge_into_csv(new_df: pd.DataFrame, target: Path, *, backup_suffix: str | None) -> dict:
    """Concat new_df with existing CSV at target, dedupe on timestamp, write atomically.

    Returns a small summary dict for logging.
    """
    new_df = _normalise(new_df)
    existing = _load_existing(target)

    before_rows = len(existing)
    merged = pd.concat([existing, new_df], ignore_index=True)
    # keep='last' so a re-download with corrected data wins over the older row
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    after_rows = len(merged)

    if before_rows and backup_suffix:
        backup_path = target.with_name(f"{target.name}.bak_{backup_suffix}")
        shutil.copy2(target, backup_path)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    merged.to_csv(tmp, index=False)
    tmp.replace(target)

    return {
        "rows_before": before_rows,
        "rows_after": after_rows,
        "rows_added": after_rows - before_rows,
        "first_ts": str(merged["timestamp"].iloc[0]) if after_rows else None,
        "last_ts": str(merged["timestamp"].iloc[-1]) if after_rows else None,
    }


def refresh_symbol(symbol: str, lookback_days: int = 58) -> dict:
    if symbol not in TICKER_MAP:
        raise ValueError(f"Unknown symbol {symbol!r} — supported: {sorted(TICKER_MAP)}")
    target = _target_csv(symbol)
    end_d = date.today() + timedelta(days=1)  # include today's partial bars
    start_d = end_d - timedelta(days=lookback_days)

    print(f"\n=== {symbol} ({TICKER_MAP[symbol]}) ===")
    print(f"Fetching {start_d} → {end_d} ({lookback_days}d) into {target.name}")
    fetched = download_all(TICKER_MAP[symbol], start_d, end_d, interval="5m")
    if fetched.empty:
        print(f"⚠️  {symbol}: yfinance returned 0 bars — leaving CSV untouched")
        return {"symbol": symbol, "skipped": True}

    fetched = fetched.reset_index()
    summary = merge_into_csv(
        fetched, target,
        backup_suffix=f"weekly_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}",
    )
    print(
        f"✅ {symbol}: {summary['rows_before']:,} → {summary['rows_after']:,} bars "
        f"(+{summary['rows_added']:,})  range: {summary['first_ts']} → {summary['last_ts']}"
    )
    return {"symbol": symbol, **summary}


def restore_from_mt5(symbol: str, mt5_path: Path) -> dict:
    target = _target_csv(symbol)
    print(f"\n=== RESTORE {symbol} ===")
    print(f"Source : {mt5_path}")
    print(f"Target : {target}")
    if not mt5_path.exists():
        raise FileNotFoundError(mt5_path)
    parsed = parse_mt5_csv(mt5_path)
    summary = merge_into_csv(
        parsed, target,
        backup_suffix=f"pre_restore_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}",
    )
    print(
        f"✅ {symbol}: {summary['rows_before']:,} → {summary['rows_after']:,} bars "
        f"(+{summary['rows_added']:,})  range: {summary['first_ts']} → {summary['last_ts']}"
    )
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Merge-safe refresh of historical 5m CSVs")
    p.add_argument("--symbols", nargs="+", default=["XAUUSD"],
                   help="Symbols to refresh (default: XAUUSD)")
    p.add_argument("--lookback-days", type=int, default=58,
                   help="Days of recent data to pull from yfinance (default 58, max ~60)")
    p.add_argument("--restore-from", type=Path, default=None,
                   help="Path to an MT5 History Center export. Merges it into the "
                        "first --symbols target and exits (no yfinance call).")
    args = p.parse_args()

    print("=" * 60)
    print(f"refresh_historical_data — {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    if args.restore_from:
        restore_from_mt5(args.symbols[0], args.restore_from)
        return 0

    failures = []
    for sym in args.symbols:
        try:
            refresh_symbol(sym, lookback_days=args.lookback_days)
        except Exception as e:
            print(f"❌ {sym}: {e}")
            failures.append(sym)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
