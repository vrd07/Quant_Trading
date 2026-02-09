#!/usr/bin/env python3
"""
Generate realistic Crypto (BTCUSD) data for backtesting.

Features:
- 24/7 trading (no sessions)
- Higher volatility than Forex
- "Pump and Dump" patterns
- Trend following and mean reversion regimes
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

def generate_crypto_data(symbol: str, days: int = 60, timeframe_minutes: int = 5, base_price: float = 95000.0) -> pd.DataFrame:
    """
    Generate realistic crypto data.
    """
    np.random.seed(42)  # Reproducible

    # Calculate number of bars
    bars_per_day = int(24 * 60 / timeframe_minutes)
    total_bars = days * bars_per_day

    # Start time
    start_time = datetime.now() - timedelta(days=days)

    # Generate timestamps
    timestamps = pd.date_range(
        start=start_time,
        periods=total_bars,
        freq=f'{timeframe_minutes}min'
    )

    # Volatility parameters (Crypto is volatile!)
    # Daily BTC volatility is typically 3-5%
    daily_volatility = 0.04 
    bar_volatility = daily_volatility / np.sqrt(bars_per_day)

    # Initialize arrays
    returns = np.zeros(total_bars)
    trend = 0
    momentum = 0
    
    # Regime switching (0=Ranging, 1=Trending Up, 2=Trending Down, 3=High Volatility)
    regime = 0
    regime_duration = 0

    print(f"Generating {total_bars} bars for {symbol} starting at ${base_price:,.2f}...")

    for i in range(total_bars):
        # Update regime
        if regime_duration <= 0:
            # Pick new regime
            rand = np.random.random()
            if rand < 0.1:
                regime = 0  # Ranging (10%)
                regime_duration = np.random.randint(50, 100)
            elif rand < 0.55:
                regime = 1  # Trending Up (45%)
                regime_duration = np.random.randint(200, 800)
            elif rand < 0.9:
                regime = 2  # Trending Down (35%)
                regime_duration = np.random.randint(200, 600)
            else:
                regime = 3  # High Volatility / Pump & Dump (10%)
                regime_duration = np.random.randint(50, 150)
        
        regime_duration -= 1

        # Calculate return based on regime
        noise = np.random.normal(0, bar_volatility)
        
        if regime == 0: # Ranging
            # Mean reversion to local mean
            current_return = noise * 0.8
            momentum = momentum * 0.8  # Decay momentum
            
        elif regime == 1: # Trending Up
            # Positive drift
            current_return = noise + (bar_volatility * 0.2)
            momentum = momentum * 0.95 + 0.05  # Build positive momentum
            
        elif regime == 2: # Trending Down
            # Negative drift
            current_return = noise - (bar_volatility * 0.2)
            momentum = momentum * 0.95 - 0.05  # Build negative momentum
            
        elif regime == 3: # High Volatility
            # Huge moves
            current_return = noise * 3.0
            momentum = momentum * 0.9 # Fast decay

        # Apply return
        returns[i] = current_return

    # Calculate prices
    price_multipliers = np.exp(np.cumsum(returns))
    close_prices = base_price * price_multipliers

    # Generate OHLC
    data = []
    for i in range(total_bars):
        close = close_prices[i]
        
        # Crypto has wider spreads and ranges
        bar_range_pct = np.random.uniform(0.001, 0.005) # 0.1% to 0.5% per bar
        if regime == 3:
            bar_range_pct *= 3 # Much larger candles in high vol
            
        bar_range = close * bar_range_pct
        
        # Random wicks
        upper_wick = bar_range * np.random.uniform(0.1, 0.4)
        lower_wick = bar_range * np.random.uniform(0.1, 0.4)
        body = bar_range - upper_wick - lower_wick
        
        # Bullish or Bearish based on return sign (mostly)
        is_bullish = returns[i] > 0
        if regime == 0 and np.random.random() > 0.8: 
            is_bullish = not is_bullish # Random chop in ranging
            
        if is_bullish:
            open_price = close - body
            high = close + upper_wick
            low = open_price - lower_wick
        else:
            open_price = close + body
            high = open_price + upper_wick
            low = close - lower_wick
            
        # Crypto Volume (24/7 but higher during US hours)
        hour = timestamps[i].hour
        base_volume = 100.0 # BTC
        
        # Volume profile
        if 13 <= hour <= 21: # US Session
            vol_mult = 1.5
        elif 0 <= hour <= 8: # Asian Session
            vol_mult = 1.2
        else:
            vol_mult = 1.0
            
        if regime == 3:
            vol_mult *= 4.0 # Huge volume in spikes
            
        volume = base_volume * vol_mult * np.random.uniform(0.8, 1.2)

        data.append({
            'timestamp': timestamps[i],
            'open': round(open_price, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': round(volume, 4)
        })

    df = pd.DataFrame(data)
    return df

def main():
    parser = argparse.ArgumentParser(description="Generate realistic Crypto data")
    parser.add_argument('--symbol', default='BTCUSD', help='Symbol name')
    parser.add_argument('--days', type=int, default=60, help='Days of data')
    parser.add_argument('--price', type=float, default=95000.0, help='Start price')
    parser.add_argument('--timeframe', default='5m', help='Timeframe')
    parser.add_argument('--output', help='Output file')

    args = parser.parse_args()

    # Parse timeframe
    tf_minutes = 5
    if args.timeframe.endswith('m'):
        tf_minutes = int(args.timeframe[:-1])
    elif args.timeframe.endswith('h'):
        tf_minutes = int(args.timeframe[:-1]) * 60

    df = generate_crypto_data(
        symbol=args.symbol,
        days=args.days,
        timeframe_minutes=tf_minutes,
        base_price=args.price
    )

    # Output path
    if not args.output:
        output_dir = Path("data/historical")
        output_dir.mkdir(parents=True, exist_ok=True)
        args.output = str(output_dir / f"{args.symbol}_{args.timeframe}_real.csv")

    df.to_csv(args.output, index=False)

    print(f"✓ Saved to: {args.output}")
    print(f"  Bars: {len(df)}")
    print(f"  Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"  Price: ${df['low'].min():,.2f} to ${df['high'].max():,.2f}")

if __name__ == "__main__":
    main()
