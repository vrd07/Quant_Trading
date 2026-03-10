#!/usr/bin/env python3
"""
Quick test script for technical indicators using historical MT5 data.

This script:
1. Connects to MT5
2. Fetches historical bars (faster than waiting for live data)
3. Calculates indicators
4. Displays results
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.connectors.mt5_connector import MT5Connector
from src.data.indicators import Indicators, calculate_indicators
from src.core.types import Symbol, Bar
from decimal import Decimal
import pandas as pd
from datetime import datetime, timezone


def main():
    print("=" * 60)
    print("Technical Indicators Test with Historical MT5 Data")
    print("=" * 60)
    
    # Setup
    print("\n1. Connecting to MT5...")
    connector = MT5Connector()
    connector.connect()
    print("✓ Connected")
    
    # Get historical data
    print("\n2. Fetching historical data...")
    symbol = Symbol(ticker="EURUSD", pip_value=Decimal("0.0001"))
    
    # Get last 100 1-minute bars from MT5
    response = connector.send_command({
        "command": "GET_HISTORICAL_BARS",
        "symbol": symbol.ticker,
        "timeframe": "1m",
        "count": 100
    })
    
    if "error" in response:
        print(f"❌ Error: {response['error']}")
        connector.disconnect()
        return
    
    bars_data = response.get("bars", [])
    print(f"✓ Retrieved {len(bars_data)} historical bars")
    
    if len(bars_data) < 50:
        print("\n⚠️  Not enough bars for indicator calculation")
        print(f"   Need at least 50 bars, have {len(bars_data)}")
        connector.disconnect()
        return
    
    # Convert to DataFrame
    df_data = {
        'timestamp': [datetime.fromisoformat(b['timestamp']).replace(tzinfo=timezone.utc) for b in bars_data],
        'open': [float(b['open']) for b in bars_data],
        'high': [float(b['high']) for b in bars_data],
        'low': [float(b['low']) for b in bars_data],
        'close': [float(b['close']) for b in bars_data],
        'volume': [float(b.get('volume', 0)) for b in bars_data]
    }
    bars_df = pd.DataFrame(df_data)
    
    # Calculate indicators
    print("\n3. Calculating indicators...")
    
    # Individual indicators
    atr = Indicators.atr(bars_df, period=14)
    adx = Indicators.adx(bars_df, period=14)
    upper, middle, lower = Indicators.donchian_channel(bars_df, period=20)
    vwap = Indicators.vwap(bars_df)
    zscore = Indicators.zscore(bars_df, period=20)
    rsi = Indicators.rsi(bars_df, period=14)
    macd_line, signal_line, histogram = Indicators.macd(bars_df)
    bb_upper, bb_middle, bb_lower = Indicators.bollinger_bands(bars_df, 20, 2.0)
    
    print("✓ Indicators calculated")
    
    # Display results
    print("\n" + "=" * 60)
    print(f"{symbol.ticker} Indicator Values (Latest Bar)")
    print("=" * 60)
    
    latest_idx = -1
    
    print(f"\nPrice Action:")
    print(f"  Timestamp: {bars_df['timestamp'].iloc[latest_idx]}")
    print(f"  Close:     {bars_df['close'].iloc[latest_idx]:.5f}")
    print(f"  High:      {bars_df['high'].iloc[latest_idx]:.5f}")
    print(f"  Low:       {bars_df['low'].iloc[latest_idx]:.5f}")
    print(f"  Volume:    {bars_df['volume'].iloc[latest_idx]:.0f}")
    
    print(f"\nVolatility Indicators:")
    if not atr.isna().iloc[latest_idx]:
        print(f"  ATR(14):   {atr.iloc[latest_idx]:.5f}")
    else:
        print(f"  ATR(14):   [insufficient data]")
    
    print(f"\nTrend Indicators:")
    if not adx.isna().iloc[latest_idx]:
        adx_val = adx.iloc[latest_idx]
        print(f"  ADX(14):   {adx_val:.2f}", end="")
        if adx_val > 25:
            print(" → Strong trend 📈")
        elif adx_val < 20:
            print(" → Weak trend / ranging ↔️")
        else:
            print(" → Moderate")
    else:
        print(f"  ADX(14):   [insufficient data]")
    
    print(f"\nBreakout Levels (Donchian Channel):")
    if not upper.isna().iloc[latest_idx]:
        print(f"  Upper:     {upper.iloc[latest_idx]:.5f}")
        print(f"  Middle:    {middle.iloc[latest_idx]:.5f}")
        print(f"  Lower:     {lower.iloc[latest_idx]:.5f}")
    else:
        print(f"  Donchian:  [insufficient data]")
    
    print(f"\nBollinger Bands:")
    if not bb_upper.isna().iloc[latest_idx]:
        print(f"  Upper:     {bb_upper.iloc[latest_idx]:.5f}")
        print(f"  Middle:    {bb_middle.iloc[latest_idx]:.5f}")
        print(f"  Lower:     {bb_lower.iloc[latest_idx]:.5f}")
    
    print(f"\nMean Reversion Indicators:")
    if not vwap.isna().iloc[latest_idx]:
        price = bars_df['close'].iloc[latest_idx]
        vwap_val = vwap.iloc[latest_idx]
        distance = ((price - vwap_val) / vwap_val) * 100
        print(f"  VWAP:      {vwap_val:.5f}")
        print(f"  Distance:  {distance:+.2f}%", end="")
        if abs(distance) > 0.5:
            print(" → Price far from VWAP")
        else:
            print(" → Price near VWAP")
    
    if not zscore.isna().iloc[latest_idx]:
        z = zscore.iloc[latest_idx]
        print(f"  Z-Score:   {z:+.2f}", end="")
        if z > 2:
            print(" → Overbought 🔴")
        elif z < -2:
            print(" → Oversold 🟢")
        else:
            print(" → Neutral ⚪")
    
    print(f"\nMomentum:")
    if not rsi.isna().iloc[latest_idx]:
        rsi_val = rsi.iloc[latest_idx]
        print(f"  RSI(14):   {rsi_val:.2f}", end="")
        if rsi_val > 70:
            print(" → Overbought 🔴")
        elif rsi_val < 30:
            print(" → Oversold 🟢")
        else:
            print(" → Neutral ⚪")
    
    if not macd_line.isna().iloc[latest_idx]:
        print(f"  MACD:      {macd_line.iloc[latest_idx]:.5f}")
        print(f"  Signal:    {signal_line.iloc[latest_idx]:.5f}")
        print(f"  Histogram: {histogram.iloc[latest_idx]:.5f}", end="")
        if histogram.iloc[latest_idx] > 0:
            print(" → Bullish 🟢")
        else:
            print(" → Bearish 🔴")
    
    # Full indicator DataFrame
    print("\n\n4. Calculating comprehensive indicators...")
    full_indicators = calculate_indicators(bars_df)
    print("✓ Complete")
    
    print(f"\nTotal columns in result: {len(full_indicators.columns)}")
    indicator_cols = [col for col in full_indicators.columns 
                     if col not in ['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    print(f"Indicator columns ({len(indicator_cols)}):", indicator_cols)
    
    # Create data directory if needed
    Path("data").mkdir(exist_ok=True)
    
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
