import json
import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

def clean_state():
    state_file = PROJECT_ROOT / "data/state/system_state.json"
    
    if not state_file.exists():
        print(f"State file not found at {state_file}")
        return

    print(f"Backing up and cleaning {state_file}...")
    
    # Backup
    backup_file = state_file.with_suffix(".json.bak")
    with open(state_file, 'r') as f:
        state = json.load(f)
        
    with open(backup_file, 'w') as f:
        json.dump(state, f, indent=2)
    
    print(f"Backup created at {backup_file}")
    
    # Clean positions and orders
    old_pos_count = len(state.get('positions', {}))
    old_order_count = len(state.get('open_orders', {}))
    
    state['positions'] = {}
    state['open_orders'] = {}
    state['daily_pnl'] = "0"
    state['total_pnl'] = "0"
    state['daily_trades_count'] = 0
    state['consecutive_losses'] = 0
    state['kill_switch_active'] = False
    state['circuit_breaker_active'] = False
    
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)
    
    print(f"Cleaned {old_pos_count} phantom positions and {old_order_count} orders.")
    print("System will now reload actual positions from MT5 on next restart.")

if __name__ == "__main__":
    clean_state()
