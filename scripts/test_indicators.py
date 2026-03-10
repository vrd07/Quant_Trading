#!/usr/bin/env python3
"""
Test script for technical indicators with real MT5 data.

This script:
1. Connects to MT5
2. Collects tick data
3. Builds bars
4. Calculates indicators
5. Displays results
"""

import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.connectors.mt5_connector import MT5Connector
from src.data.data_engine import DataEngine
from src.data.indicators import Indicators, calculate_indicators
from src.core.types import Symbol
from decimal import Decimal


def main():
    print("=" * 60)
    print("Technical Indicators Test with Real MT5 Data")
    print("=" * 60)
    
    # Setup
    print("\n1. Connecting to MT5...")
    connector = MT5Connector()
    connector.connect()
    print("✓ Connected")
    
    # Create data engine
    symbols = [
        Symbol(ticker="EURUSD", pip_value=Decimal("0.0001")),
        Symbol(ticker="XAUUSD", pip_value=Decimal("0.01"))
    ]
    
    print("\n2. Initializing data engine...")
    engine = DataEngine(
        connector=connector,
        symbols=symbols,
        timeframes=["1m", "5m"]
    )
    print("✓ Data engine ready")
    
    # Collect data
    print("\n3. Collecting data for 3 minutes...")
    for i in range(180):  # 3 minutes
        engine.update_from_connector()
        time.sleep(1)
        
        if i % 30 == 0:
            print(f"   {i} seconds elapsed...")
    
    print("✓ Data collection complete")
    
    # Get bars
    print("\n4. Retrieving bars...")
    bars_1m = engine.get_bars("EURUSD", "1m")
    print(f"✓ Retrieved {len(bars_1m)} bars")
    
    if len(bars_1m) < 20:
        print("\n⚠️  Not enough bars for indicator calculation")
        print("   Need at least 20 bars, have", len(bars_1m))
        return
    
    # Calculate indicators
    print("\n5. Calculating indicators...")
    
    # Individual indicators
    atr = Indicators.atr(bars_1m, period=14)
    adx = Indicators.adx(bars_1m, period=14)
    upper, middle, lower = Indicators.donchian_channel(bars_1m, period=20)
    vwap = Indicators.vwap(bars_1m)
    zscore = Indicators.zscore(bars_1m, period=20)
    rsi = Indicators.rsi(bars_1m, period=14)
    
    print("✓ Indicators calculated")
    
    # Display results
    print("\n" + "=" * 60)
    print("EURUSD Indicator Values (Latest Bar)")
    print("=" * 60)
    
    latest_idx = -1
    
    print(f"\nPrice Action:")
    print(f"  Close:  {bars_1m['close'].iloc[latest_idx]:.5f}")
    print(f"  High:   {bars_1m['high'].iloc[latest_idx]:.5f}")
    print(f"  Low:    {bars_1m['low'].iloc[latest_idx]:.5f}")
    
    print(f"\nVolatility Indicators:")
    if not atr.isna().iloc[latest_idx]:
        print(f"  ATR(14):  {atr.iloc[latest_idx]:.5f}")
    else:
        print(f"  ATR(14):  [insufficient data]")
    
    print(f"\nTrend Indicators:")
    if not adx.isna().iloc[latest_idx]:
        adx_val = adx.iloc[latest_idx]
        print(f"  ADX(14):  {adx_val:.2f}", end="")
        if adx_val > 25:
            print(" → Strong trend")
        elif adx_val < 20:
            print(" → Weak trend / ranging")
        else:
            print(" → Moderate")
    else:
        print(f"  ADX(14):  [insufficient data]")
    
    print(f"\nBreakout Levels:")
    if not upper.isna().iloc[latest_idx]:
        print(f"  Donchian Upper: {upper.iloc[latest_idx]:.5f}")
        print(f"  Donchian Lower: {lower.iloc[latest_idx]:.5f}")
    else:
        print(f"  Donchian Channel: [insufficient data]")
    
    print(f"\nMean Reversion Indicators:")
    if not vwap.isna().iloc[latest_idx]:
        price = bars_1m['close'].iloc[latest_idx]
        vwap_val = vwap.iloc[latest_idx]
        distance = ((price - vwap_val) / vwap_val) * 100
        print(f"  VWAP:     {vwap_val:.5f}")
        print(f"  Distance: {distance:+.2f}%")
    
    if not zscore.isna().iloc[latest_idx]:
        z = zscore.iloc[latest_idx]
        print(f"  Z-Score:  {z:+.2f}", end="")
        if z > 2:
            print(" → Overbought")
        elif z < -2:
            print(" → Oversold")
        else:
            print(" → Neutral")
    
    print(f"\nMomentum:")
    if not rsi.isna().iloc[latest_idx]:
        rsi_val = rsi.iloc[latest_idx]
        print(f"  RSI(14):  {rsi_val:.2f}", end="")
        if rsi_val > 70:
            print(" → Overbought")
        elif rsi_val < 30:
            print(" → Oversold")
        else:
            print(" → Neutral")
    
    # Full indicator DataFrame
    print("\n\n6. Calculating comprehensive indicators...")
    full_indicators = calculate_indicators(bars_1m)
    print("✓ Complete")
    
    print(f"\nTotal columns in result: {len(full_indicators.columns)}")
    print("Indicator columns:", [col for col in full_indicators.columns 
                                  if col not in ['timestamp', 'open', 'high', 'low', 'close', 'volume']])
    
    # Export
    output_file = "data/indicators_test.csv"
    full_indicators.to_csv(output_file, index=False)
    print(f"\n✓ Results exported to: {output_file}")
    
    print("\n" + "=" * 60)
    print("✓ All tests complete!")
    print("=" * 60)
    
    connector.disconnect()


if __name__ == "__main__":
    main()
