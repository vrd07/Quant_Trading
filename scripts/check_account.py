from pathlib import Path
import sys
from decimal import Decimal

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.connectors.mt5_connector import MT5Connector

def check():
    print("Connecting to MT5...")
    try:
        connector = MT5Connector()
        connector.connect()
        acc = connector.get_account_info()
        print(f"--- MT5 ACCOUNT INFO ---")
        print(f"Balance: {acc['balance']}")
        print(f"Equity:  {acc['equity']}")
        print(f"Server:  (Check MT5 GUI for server name)")
        
        pos = connector.get_positions()
        print(f"Open Positions: {len(pos)}")
        for pid, p in pos.items():
            print(f"  - {p.symbol.ticker} {p.side.value} {p.quantity}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check()
