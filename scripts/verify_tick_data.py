import sys
from pathlib import Path
import time
sys.path.append(str(Path(__file__).parent.parent))

from src.connectors.mt5_connector import get_mt5_connector

def verify_ticks():
    output_path = Path("scripts/verify_tick_data_output.txt")
    with open(output_path, "w") as f:
        f.write("Initializing MT5 Connector...\n")
        try:
            connector = get_mt5_connector()
            
            if not connector.connect():
                f.write("Failed to connect to MT5\n")
                return
                
            f.write("Connected. Checking ticks...\n")
            
            symbols = ["XAUUSD", "BTCUSD", "EURUSD"]
            
            for i in range(5):
                f.write(f"\nIteration {i+1}:\n")
                for symbol in symbols:
                    tick = connector.get_current_tick(symbol)
                    if tick:
                        f.write(f"  {symbol}: {tick.bid} / {tick.ask}\n")
                    else:
                        f.write(f"  {symbol}: NO DATA\n")
                
                f.flush()
                time.sleep(1)
                
        except Exception as e:
            f.write(f"Error: {e}\n")
        finally:
            if 'connector' in locals():
                connector.disconnect()

if __name__ == "__main__":
    verify_ticks()
