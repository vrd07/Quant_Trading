#!/usr/bin/env python3
"""
Quick test with synthetic data to verify indicator calculations and CSV output.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.indicators import Indicators, calculate_indicators


def generate_synthetic_data(num_bars: int = 100) -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data."""
    np.random.seed(42)
    
    # Start with base price
    base_price = 1.0850  # EURUSD-like price
    
    # Generate price walk with trend and noise
    returns = np.random.randn(num_bars) * 0.0002 + 0.00001  # Small uptrend
    prices = base_price * (1 + returns).cumprod()
    
    # Generate OHLC from close prices
    data = {
        'timestamp': [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i) 
                     for i in range(num_bars)],
        'open': prices,
        'high': prices * (1 + abs(np.random.randn(num_bars)) * 0.0001),
        'low': prices * (1 - abs(np.random.randn(num_bars)) * 0.0001),
        'close': prices * (1 + np.random.randn(num_bars) * 0.00005),
        'volume': np.random.randint(800, 1200, num_bars).astype(float)
    }
    
    return pd.DataFrame(data)


def main():
    print("=" * 60)
    print("Technical Indicators Test with Synthetic Data")
    print("=" * 60)
    
    # Generate data
    print("\n1. Generating synthetic OHLCV data...")
    bars_df = generate_synthetic_data(100)
    print(f"✓ Generated {len(bars_df)} bars")
    
    # Calculate indicators
    print("\n2. Calculating all indicators...")
    result = calculate_indicators(bars_df)
    print("✓ Complete")
    
    # Show statistics
    print("\n3. Results:")
    print(f"   Total columns: {len(result.columns)}")
    print(f"   OHLCV columns: {[c for c in result.columns if c in ['timestamp', 'open', 'high', 'low', 'close', 'volume']]}")
    
    indicator_cols = [c for c in result.columns 
                     if c not in ['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    print(f"   Indicator columns ({len(indicator_cols)}):")
    for col in indicator_cols:
        non_nan = result[col].dropna().shape[0]
        print(f"      - {col}: {non_nan} valid values")
    
    # Display latest values
    print("\n4. Latest Indicator Values:")
    print(f"   Close Price: {result['close'].iloc[-1]:.5f}")
    
    if not result['atr_14'].isna().iloc[-1]:
        print(f"   ATR(14):     {result['atr_14'].iloc[-1]:.5f}")
    
    if not result['adx_14'].isna().iloc[-1]:
        adx = result['adx_14'].iloc[-1]
        trend = "Strong trend" if adx > 25 else "Weak/ranging" if adx < 20 else "Moderate"
        print(f"   ADX(14):     {adx:.2f} → {trend}")
    
    if not result['rsi_14'].isna().iloc[-1]:
        rsi = result['rsi_14'].iloc[-1]
        condition = "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral"
        print(f"   RSI(14):     {rsi:.2f} → {condition}")
    
    if not result['zscore_20'].isna().iloc[-1]:
        z = result['zscore_20'].iloc[-1]
        condition = "Overbought" if z > 2 else "Oversold" if z < -2 else "Neutral"
        print(f"   Z-Score(20): {z:+.2f} → {condition}")
    
    if not result['macd'].isna().iloc[-1]:
        macd = result['macd'].iloc[-1]
        signal = result['macd_signal'].iloc[-1]
        trend = "Bullish" if macd > signal else "Bearish"
        print(f"   MACD:        {macd:.5f} → {trend}")
    
    # Export to CSV
    print("\n5. Exporting to CSV...")
    Path("data").mkdir(exist_ok=True)
    output_file = "data/indicators_test.csv"
    result.to_csv(output_file, index=False)
    print(f"✓ Exported to: {output_file}")
    
    print("\n" + "=" * 60)
    print("✓ Test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
