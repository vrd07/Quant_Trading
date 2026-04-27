#!/usr/bin/env python3
"""
Download real XAUUSD historical 5m data using yfinance.

yfinance limits:
  - 5m data: max 60-day window per request
  - We stitch multiple 60-day chunks to cover Jan 2025 → today

Ticker used: GC=F (COMEX Gold Futures — prices track XAUUSD spot within ~$5)
Output:      data/historical/XAUUSD_5m_real.csv

Usage:
    python scripts/download_historical_data.py
    python scripts/download_historical_data.py --start 2025-01-01 --end 2026-03-28
    python scripts/download_historical_data.py --symbol EURUSD=X --out data/historical/EURUSD_5m_real.csv
"""

import sys
import argparse
import time
from pathlib import Path
from datetime import date, timedelta, datetime

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Ticker map ───────────────────────────────────────────────────────────────
# yfinance symbol → output filename
TICKER_MAP = {
    "XAUUSD": "GC=F",       # Gold Futures (COMEX) — closest to XAUUSD spot
    "EURUSD": "EURUSD=X",   # EUR/USD spot
    "BTCUSD": "BTC-USD",    # Bitcoin / USD spot
    "ETHUSD": "ETH-USD",    # Ethereum / USD spot
    "US30":   "YM=F",       # Dow Jones Futures
    "USOIL":  "CL=F",       # WTI Crude Futures
}


def download_chunk(ticker_sym: str, start: date, end: date, interval: str = "5m") -> pd.DataFrame:
    """Download one chunk of data from yfinance."""
    import yfinance as yf
    df = yf.download(
        ticker_sym,
        start=start.isoformat(),
        end=end.isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
    )
    if df.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns if present (yfinance ≥ 0.2 returns MultiIndex)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index.name = "timestamp"
    df.index = pd.to_datetime(df.index)

    # Drop rows with NaN OHLC
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def download_all(
    ticker_sym: str,
    start: date,
    end: date,
    interval: str = "5m",
    chunk_days: int = 58,   # 58 < 60-day limit with safety margin
) -> pd.DataFrame:
    """Stitch multiple chunks to cover an arbitrary date range."""
    frames = []
    cursor = start
    total_days = (end - start).days
    fetched_days = 0

    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        print(f"  Downloading {cursor} → {chunk_end} ...", end=" ", flush=True)
        try:
            chunk = download_chunk(ticker_sym, cursor, chunk_end, interval)
            if not chunk.empty:
                frames.append(chunk)
                print(f"{len(chunk)} bars")
            else:
                print("empty")
        except Exception as e:
            print(f"ERROR: {e}")
        fetched_days += (chunk_end - cursor).days
        cursor = chunk_end
        # Be polite to Yahoo servers
        if cursor < end:
            time.sleep(1.5)

    if not frames:
        print("No data downloaded.")
        return pd.DataFrame()

    combined = pd.concat(frames)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return combined


def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data via yfinance")
    parser.add_argument("--symbol", default="XAUUSD",
                        choices=list(TICKER_MAP.keys()),
                        help="Symbol to download (default: XAUUSD)")
    parser.add_argument("--start", default="2025-01-01",
                        help="Start date YYYY-MM-DD (default: 2025-01-01)")
    parser.add_argument("--end", default=date.today().isoformat(),
                        help=f"End date YYYY-MM-DD (default: today {date.today()})")
    parser.add_argument("--interval", default="5m",
                        help="Bar interval: 1m, 5m, 15m, 1h, 1d (default: 5m)")
    parser.add_argument("--out", default=None,
                        help="Output CSV path (auto-derived from symbol if omitted)")
    args = parser.parse_args()

    ticker_sym = TICKER_MAP[args.symbol]
    start_date = date.fromisoformat(args.start)
    end_date   = date.fromisoformat(args.end)

    out_path = Path(args.out) if args.out else (
        PROJECT_ROOT / "data" / "historical" / f"{args.symbol}_{args.interval}_real.csv"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"Downloading {args.symbol} ({ticker_sym})")
    print(f"Interval : {args.interval}")
    print(f"Range    : {start_date} → {end_date}  ({(end_date - start_date).days} days)")
    print(f"Output   : {out_path}")
    print("=" * 60)

    # yfinance 5m/1m data has a hard lookback limit regardless of start date.
    # Warn the user if they're asking for more than ~60 days of sub-hourly data.
    if args.interval in ("1m", "2m", "5m", "15m", "30m"):
        max_days = 60 if args.interval != "1m" else 7
        actual_days = (end_date - start_date).days
        if actual_days > max_days:
            print(f"\n⚠️  yfinance limits {args.interval} data to the last {max_days} days.")
            print(f"   Requested {actual_days} days — older bars will be empty/skipped.")
            print(f"   For full history, export directly from MT5 (see docs below).\n")

    df = download_all(ticker_sym, start_date, end_date, interval=args.interval)

    if df.empty:
        print("\n❌ No data returned. Possible reasons:")
        print("   1. No internet connection")
        print("   2. Yahoo Finance rate-limit — wait 60s and retry")
        print("   3. Requested period exceeds yfinance limit for this interval")
        sys.exit(1)

    # Reset index so timestamp becomes a column (matches backtest engine format)
    df = df.reset_index()

    df.to_csv(out_path, index=False)
    print(f"\n✅ Saved {len(df):,} bars to {out_path}")
    print(f"   Date range: {df['timestamp'].min()} → {df['timestamp'].max()}")
    print(f"   Price range: {df['close'].min():.2f} → {df['close'].max():.2f}")

    print("\n" + "=" * 60)
    print("MT5 EXPORT (for full history beyond yfinance limits):")
    print("=" * 60)
    print("1. Open MetaTrader 5")
    print("2. Tools → History Center → XAUUSD → M5 → Download")
    print("3. Right-click → Export Bars")
    print("4. Save as CSV with columns: Date, Time, Open, High, Low, Close, Volume")
    print("5. Run: python scripts/convert_mt5_export.py --input your_file.csv")


if __name__ == "__main__":
    main()
