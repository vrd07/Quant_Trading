#!/usr/bin/env python3
"""
Generate Sample Historical Data for Backtest Testing.

Creates synthetic OHLCV data with realistic characteristics.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path


def generate_trending_data(
    start_date: datetime,
    periods: int,
    base_price: float = 2000.0,
    trend: float = 0.001,
    volatility: float = 0.01
) -> pd.DataFrame:
    """
    Generate trending price data.
    
    Args:
        start_date: Start date
        periods: Number of bars
        base_price: Starting price
        trend: Trend per bar (0.001 = 0.1% per bar)
        volatility: Volatility (std dev)
    
    Returns:
        DataFrame with OHLCV data
    """
    timestamps = [start_date + timedelta(minutes=5*i) for i in range(periods)]
    
    # Generate close prices with trend and noise
    close_prices = [base_price]
    for i in range(1, periods):
        change = trend + np.random.normal(0, volatility)
        new_price = close_prices[-1] * (1 + change)
        close_prices.append(new_price)
    
    # Generate OHLC from close
    data = []
    for i, (ts, close) in enumerate(zip(timestamps, close_prices)):
        if i == 0:
            open_price = close
        else:
            open_price = close_prices[i-1]
        
        # Generate high and low
        bar_range = abs(np.random.normal(0, volatility * close))
        high = max(open_price, close) + bar_range * 0.5
        low = min(open_price, close) - bar_range * 0.5
        
        volume = np.random.randint(1000, 5000)
        
        data.append({
            'timestamp': ts,
            'open': round(open_price, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': volume
        })
    
    return pd.DataFrame(data)


def generate_ranging_data(
    start_date: datetime,
    periods: int,
    base_price: float = 2000.0,
    range_width: float = 50.0,
    volatility: float = 0.005
) -> pd.DataFrame:
    """
    Generate ranging (sideways) price data.
    
    Args:
        start_date: Start date
        periods: Number of bars
        base_price: Center price
        range_width: Range width (e.g., 50 points)
        volatility: Volatility
    
    Returns:
        DataFrame with OHLCV data
    """
    timestamps = [start_date + timedelta(minutes=5*i) for i in range(periods)]
    
    # Generate close prices oscillating around base
    close_prices = []
    for i in range(periods):
        # Sine wave + noise
        sine_component = np.sin(i / 20) * range_width / 2
        noise = np.random.normal(0, volatility * base_price)
        price = base_price + sine_component + noise
        close_prices.append(price)
    
    # Generate OHLC from close
    data = []
    for i, (ts, close) in enumerate(zip(timestamps, close_prices)):
        if i == 0:
            open_price = close
        else:
            open_price = close_prices[i-1]
        
        bar_range = abs(np.random.normal(0, volatility * close))
        high = max(open_price, close) + bar_range * 0.5
        low = min(open_price, close) - bar_range * 0.5
        
        volume = np.random.randint(1000, 5000)
        
        data.append({
            'timestamp': ts,
            'open': round(open_price, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': volume
        })
    
    return pd.DataFrame(data)


def main():
    print("Generating sample historical data...")
    
    output_dir = Path("data/historical")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate trending data (for breakout strategy)
    print("\n1. Generating trending data (XAUUSD)...")
    trending_data = generate_trending_data(
        start_date=datetime(2024, 1, 1),
        periods=10000,  # ~35 days of 5m bars
        base_price=2000.0,
        trend=0.0005,  # Uptrend
        volatility=0.008
    )
    
    trending_file = output_dir / "XAUUSD_5m_trending.csv"
    trending_data.to_csv(trending_file, index=False)
    print(f"✓ Saved to: {trending_file}")
    print(f"  Bars: {len(trending_data)}")
    print(f"  Date range: {trending_data['timestamp'].min()} to {trending_data['timestamp'].max()}")
    print(f"  Price range: ${trending_data['close'].min():.2f} to ${trending_data['close'].max():.2f}")
    
    # Generate ranging data (for mean reversion strategy)
    print("\n2. Generating ranging data (BTCUSD)...")
    ranging_data = generate_ranging_data(
        start_date=datetime(2024, 1, 1),
        periods=10000,
        base_price=45000.0,
        range_width=2000.0,
        volatility=0.01
    )
    
    ranging_file = output_dir / "BTCUSD_5m_ranging.csv"
    ranging_data.to_csv(ranging_file, index=False)
    print(f"✓ Saved to: {ranging_file}")
    print(f"  Bars: {len(ranging_data)}")
    print(f"  Date range: {ranging_data['timestamp'].min()} to {ranging_data['timestamp'].max()}")
    print(f"  Price range: ${ranging_data['close'].min():.2f} to ${ranging_data['close'].max():.2f}")
    
    print("\n✓ Sample data generated successfully!")
    print("\nYou can now run backtests:")
    print(f"  python scripts/run_backtest.py --strategy breakout --symbol XAUUSD --start 2024-01-01")


if __name__ == "__main__":
    main()
