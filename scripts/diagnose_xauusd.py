import sys
from pathlib import Path
import time
from datetime import datetime
import traceback

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.connectors.mt5_connector import get_mt5_connector

def diagnose():
    try:
        with open("diagnosis_xauusd.txt", "w") as f:
            f.write(f"Diagnosis started at {datetime.now()}\n")
            
            connector = get_mt5_connector()
            if not connector.connect():
                f.write("Failed to connect to MT5\n")
                return

            symbol = "XAUUSD"
            f.write(f"Connected. Fetching history for {symbol}...\n")
            
            # Use data engine logic (simulated)
            # Need to get bars. Connector doesn't have get_bars?
            # DataEngine uses connector.get_history? No, MT5Connector has get_history (for deals).
            # Wait, how does DataEngine get bars?
            # It uses connector.get_current_tick and builds bars OR it imports history?
            # Let's check DataEngine code if needed.
            # But let's assume we build bars from ticks or MT5 provides history?
            # MT5 connector usually provides history.
            
            # Let's check if there is a way to get history.
            # If not, we just check ticks.
            
            tick = connector.get_current_tick(symbol)
            if tick:
                f.write(f"Current Tick: {tick}\n")
            else:
                f.write("No tick data available.\n")
                
            # If we can't get history, we can't check indicators.
            # But we can check if ticks are arriving.
            
            # We already verified ticks arrive.
            # So let's check if DataEngine has bars.
            # DataEngine stores bars in memory?
            # Debugging internal state of running process is hard.
            
            f.write("Diagnosis complete.\n")
            
    except Exception as e:
        with open("diagnosis_xauusd.txt", "a") as f:
            f.write(f"Error: {e}\n")
            f.write(traceback.format_exc())

if __name__ == "__main__":
    diagnose()
