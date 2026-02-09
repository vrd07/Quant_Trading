
import sys
import traceback
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

output_file = Path("debug_result.txt")

with open(output_file, "w") as f:
    try:
        f.write("Starting debug script...\n")
        f.write(f"Python executable: {sys.executable}\n")
        f.write(f"Path: {sys.path}\n")
        
        f.write("Importing MeanReversionStrategy...\n")
        from src.strategies.mean_reversion_strategy import MeanReversionStrategy
        f.write("Import successful.\n")
        
        from src.core.types import Symbol
        from decimal import Decimal
        
        symbol = Symbol("BTCUSD", Decimal("0.01"), Decimal("0.01"), Decimal("10"), Decimal("0.01"), Decimal("1"))
        config = {
            'zscore_lookback': 20,
            'entry_threshold': 2.0,
            'exit_threshold': 0.5,
            'stop_threshold': 3.0,
            'rr_ratio': 1.5,
            'only_in_regime': 'RANGE'
        }
        
        f.write("Instantiating...\n")
        strategy = MeanReversionStrategy(symbol, config)
        f.write("Instantiation successful.\n")
        
    except Exception as e:
        f.write("\nERROR:\n")
        traceback.print_exc(file=f)
