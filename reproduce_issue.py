import pandas as pd
import numpy as np
import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

with open("verification_result.txt", "w") as f:
    # Redirect stdout and stderr to file
    sys.stdout = f
    sys.stderr = f
    
    try:
        from src.strategies.regime_filter import RegimeFilter
        from src.core.constants import MarketRegime
        
        # Create dummy data
        np.random.seed(42)
        n_rows = 150
        data = {
            'high': np.random.rand(n_rows) * 10 + 100,
            'low': np.random.rand(n_rows) * 10 + 90,
            'close': np.random.rand(n_rows) * 10 + 95,
            'open': np.random.rand(n_rows) * 10 + 95,
            'volume': np.random.rand(n_rows) * 1000
        }
        df = pd.DataFrame(data)

        # Ensure reasonable OHLC
        df['high'] = df[['open', 'close', 'high', 'low']].max(axis=1)
        df['low'] = df[['open', 'close', 'high', 'low']].min(axis=1)

        print("Initializing RegimeFilter...")
        rf = RegimeFilter()

        print("Classifying...")
        regime = rf.classify(df)
        print(f"Successfully classified regime: {regime}")
        
        metrics = rf.get_regime_metrics(df)
        print(f"Metrics collected successfully: {list(metrics.keys())}")
        
        print("VERIFICATION_SUCCESS")
        
    except Exception as e:
        print(f"Caught Exception: {e}")
        import traceback
        traceback.print_exc()
