
import pandas as pd
import numpy as np
from src.data.indicators import Indicators

def generate_sine_wave_data(length=500, period=50):
    x = np.linspace(0, length, length)
    y = np.sin(x * 2 * np.pi / period) + np.random.normal(0, 0.1, length)
    
    df = pd.DataFrame({
        'timestamp': pd.date_range(start='2024-01-01', periods=length, freq='1min'),
        'open': y,
        'high': y + 0.1,
        'low': y - 0.1,
        'close': y,
        'volume': np.random.randint(100, 1000, length)
    })
    return df

def log(msg, file):
    print(msg)
    file.write(msg + "\n")

def test_indicators():
    with open("verification_output.txt", "w") as f:
        log("Generating synthetic data...", f)
        df = generate_sine_wave_data()
        
        log("\nTesting Half-Life...", f)
        try:
            hl = Indicators.half_life(df)
            log(f"Half-Life (last): {hl.iloc[-1]:.2f}", f)
            log("Half-Life calculation successful.", f)
        except Exception as e:
            log(f"Half-Life failed: {e}", f)

        log("\nTesting VWAP Z-Score...", f)
        try:
            z_vwap = Indicators.zscore_vwap(df)
            log(f"VWAP Z-Score (last): {z_vwap.iloc[-1]:.2f}", f)
            log("VWAP Z-Score calculation successful.", f)
        except Exception as e:
            log(f"VWAP Z-Score failed: {e}", f)

if __name__ == "__main__":
    test_indicators()
