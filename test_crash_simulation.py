# test_crash_simulation.py
"""
Manual crash simulation test.

Usage:
1. Start system, create positions, then kill it
2. On restart, run this script to verify recovery works

python test_crash_simulation.py
"""

from src.state.state_manager import StateManager
from src.connectors.mt5_connector import MT5Connector

def main():
    print("Starting crash recovery simulation...")
    print("-" * 40)
    
    # Connect to MT5
    connector = MT5Connector()
    connector.connect()
    print("✓ Connected to MT5")
    
    # Create state manager
    manager = StateManager()
    
    # Get MT5 state
    mt5_pos = connector.get_positions()
    mt5_account = connector.get_account_info()
    
    print(f"✓ Found {len(mt5_pos)} positions in MT5")
    
    # Recover
    state = manager.restore_from_crash(mt5_pos, mt5_account)
    
    print("-" * 40)
    print("Recovery Results:")
    print(f"✓ Recovered {len(state.positions)} positions")
    print(f"✓ Account: ${state.account_balance}")
    print(f"✓ Equity: ${state.account_equity}")
    print(f"✓ Daily P&L: ${state.daily_pnl}")
    print(f"✓ Kill switch: {state.kill_switch_active}")
    print(f"✓ State age: {state.metadata.get('saved_state_age_seconds', 'N/A')}s")
    print("-" * 40)
    print("Crash recovery complete!")


if __name__ == "__main__":
    main()
