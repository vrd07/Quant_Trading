"""
Test script for DataEngine - validates the complete data pipeline.

This script:
1. Connects to MT5
2. Creates a DataEngine with multiple symbols and timeframes
3. Collects ticks for 2 minutes
4. Displays data status and sample bars
"""

from src.data.data_engine import DataEngine
from src.connectors.mt5_connector import MT5Connector
from src.core.types import Symbol
from decimal import Decimal
import time


def main():
    # Setup connector
    print("Initializing MT5 connector...")
    connector = MT5Connector()
    connector.connect()
    
    # Define symbols to track
    symbols = [
        Symbol(ticker="EURUSD", pip_value=Decimal("0.0001")),
        Symbol(ticker="XAUUSD", pip_value=Decimal("0.01"))
    ]
    
    # Create data engine
    print("Creating DataEngine...")
    engine = DataEngine(
        connector=connector,
        symbols=symbols,
        timeframes=["1m", "5m", "15m"]
    )
    
    # Collect data for 2 minutes
    print("\nCollecting ticks for 2 minutes...")
    for i in range(120):  # 2 minutes
        updated = engine.update_from_connector()
        time.sleep(1)
        
        if i % 10 == 0:
            print(f"  {i} seconds elapsed... (updated {updated} symbols)")
    
    # Check results
    print("\n" + "="*60)
    print("Data Status Summary:")
    print("="*60)
    
    status = engine.get_data_status()
    for symbol, tfs in status.items():
        print(f"\n{symbol}:")
        for tf, info in tfs.items():
            stale_indicator = "⚠️  STALE" if info['stale'] else "✅"
            print(f"  {tf:4s}: {info['bars']:4d} bars | Latest: {info['latest']} {stale_indicator}")
    
    # Get sample bars
    print("\n" + "="*60)
    print("Sample Data - Last 10 EURUSD 1m bars:")
    print("="*60)
    
    try:
        bars_1m = engine.get_bars("EURUSD", "1m", count=10)
        print(bars_1m.to_string(index=False))
    except Exception as e:
        print(f"Error retrieving bars: {e}")
    
    # Get latest tick
    print("\n" + "="*60)
    print("Latest Ticks:")
    print("="*60)
    
    for symbol in symbols:
        tick = engine.get_latest_tick(symbol.ticker)
        if tick:
            print(f"{symbol.ticker}: bid={tick.bid}, ask={tick.ask}, mid={tick.mid} @ {tick.timestamp}")
        else:
            print(f"{symbol.ticker}: No tick data")
    
    # Disconnect
    print("\nDisconnecting...")
    connector.disconnect()
    print("✅ Test complete!")


if __name__ == "__main__":
    main()
