
import pandas as pd
import numpy as np
from src.data.indicators import Indicators
from src.strategies.regime_filter import RegimeFilter
from src.core.constants import MarketRegime

def log(msg, file):
    print(msg)
    file.write(msg + "\n")

def generate_data(length=500):
    # Generating a sine wave to simulate mean reversion
    x = np.linspace(0, length, length)
    y = np.sin(x * 2 * np.pi / 50) + np.random.normal(0, 0.1, length) # Period 50
    
    df = pd.DataFrame({
        'timestamp': pd.date_range(start='2024-01-01', periods=length, freq='1min'),
        'open': y,
        'high': y + 0.1,
        'low': y - 0.1,
        'close': y,
        'volume': np.random.randint(100, 1000, length)
    })
    return df

def test_strategy_logic():
    with open("strategy_verification.log", "w") as f:
        log("Generating synthetic data (Sine Wave, Period=50)...", f)
        df = generate_data()
        
        # Test Indicators
        log("\n--- Testing Indicators ---", f)
        hl = Indicators.half_life(df, period=100).iloc[-1]
        log(f"Calculated Half-Life: {hl:.2f} (Expected approx 10-20 for sine wave)", f)
        
        vwap_z = Indicators.zscore_vwap(df, period=20).iloc[-1]
        log(f"VWAP Z-Score: {vwap_z:.2f}", f)
        
        # Test Regime Filter
        log("\n--- Testing Regime Filter ---", f)
        rf = RegimeFilter()
        regime = rf.classify(df)
        log(f"Classified Regime: {regime.value}", f)
        
        # Test Logic used in Strategy
        log("\n--- Testing Strategy Logic ---", f)
        lookback = int(hl * 1.0)
        lookback = max(10, min(100, lookback))
        log(f"Dynamic Lookback (HL * 1.0): {lookback}", f)
        
        if lookback > 0:
            z_dynamic = Indicators.zscore_vwap(df, period=lookback).iloc[-1]
            log(f"Dynamic Z-Score (L={lookback}): {z_dynamic:.2f}", f)
        
        log("\nVerification Complete", f)

if __name__ == "__main__":
    test_strategy_logic()
