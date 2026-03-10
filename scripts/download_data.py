#!/usr/bin/env python3
"""
Download Historical Data from MT5.

Downloads OHLCV data and saves to CSV for backtesting.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from connectors.mt5_connector import MT5Connector


def download_data(
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    output_file: str
):
    """
    Download historical data from MT5.
    
    Args:
        symbol: Symbol ticker
        timeframe: Timeframe (1m, 5m, 15m, 1h, etc.)
        start_date: Start date
        end_date: End date
        output_file: Output CSV file path
    """
    print(f"Downloading {symbol} {timeframe} data...")
    print(f"Date range: {start_date.date()} to {end_date.date()}")
    
    # Connect to MT5
    connector = MT5Connector()
    connector.connect()
    
    # For file-based MT5 bridge, we'd need to implement historical data fetching
    # This is a placeholder - actual implementation depends on your MT5 bridge capabilities
    
    print("⚠️  Historical data download via file bridge not yet implemented")
    print("\nManual download instructions:")
    print("1. Open MT5")
    print(f"2. Open {symbol} chart")
    print(f"3. Set timeframe to {timeframe}")
    print("4. Right-click → 'Save as' → Choose CSV")
    print(f"5. Save to: {output_file}")
    print("\nOr use MT5 Python library (MetaTrader5 package) if available")
    
    connector.disconnect()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Download historical data from MT5")
    parser.add_argument('--symbol', required=True, help='Symbol (e.g., XAUUSD)')
    parser.add_argument('--timeframe', default='5m', help='Timeframe (default: 5m)')
    parser.add_argument('--days', type=int, default=365, help='Number of days of history')
    parser.add_argument('--output', help='Output file (default: data/historical/{symbol}_{timeframe}.csv)')
    
    args = parser.parse_args()
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)
    
    # Default output path
    if not args.output:
        output_dir = Path("data/historical")
        output_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(output_dir / f"{args.symbol}_{args.timeframe}.csv")
    
    # Download
    download_data(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=start_date,
        end_date=end_date,
        output_file=args.output
    )


if __name__ == "__main__":
    main()
