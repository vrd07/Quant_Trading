import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from src.connectors.mt5_connector import get_mt5_connector

def verify_ticks():
    print("Initializing MT5 Connector...")
    connector = get_mt5_connector()
    
    try:
        if not connector.connect():
            print("Failed to connect to MT5")
            return
            
        print("Connected. Checking ticks...")
        
        symbols = ["XAUUSD", "BTCUSD", "EURUSD"]
        
        for i in range(5):
            print(f"\nIteration {i+1}:")
            for symbol in symbols:
                tick = connector.get_current_tick(symbol)
                if tick:
                    print(f"  {symbol}: {tick.bid} / {tick.ask}")
                else:
                    print(f"  {symbol}: NO DATA")
            
            time.sleep(1)
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        connector.disconnect()

if __name__ == "__main__":
    verify_ticks()
