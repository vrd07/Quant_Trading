
import sys
import os

# Add project root to path
sys.path.insert(0, os.getcwd())

def log(msg, f):
    print(msg)
    f.write(msg + "\n")

with open("syntax_result.txt", "w") as f:
    log("Checking imports...", f)

    try:
        log("1. Importing Indicators...", f)
        from src.data.indicators import Indicators
        log("   OK", f)
    except Exception as e:
        log(f"   FAIL: {e}", f)
        import traceback
        traceback.print_exc(file=f)

    try:
        log("2. Importing RegimeFilter...", f)
        from src.strategies.regime_filter import RegimeFilter
        log("   OK", f)
    except Exception as e:
        log(f"   FAIL: {e}", f)
        import traceback
        traceback.print_exc(file=f)

    try:
        log("3. Importing MeanReversionStrategy...", f)
        from src.strategies.mean_reversion_strategy import MeanReversionStrategy
        log("   OK", f)
    except Exception as e:
        log(f"   FAIL: {e}", f)
        import traceback
        traceback.print_exc(file=f)

    try:
        log("4. Importing StrategyManager...", f)
        from src.strategies.strategy_manager import StrategyManager
        log("   OK", f)
    except Exception as e:
        log(f"   FAIL: {e}", f)
        import traceback
        traceback.print_exc(file=f)

    log("Done.", f)
