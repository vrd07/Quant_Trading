
import json
import shutil
import os
from datetime import datetime

STATE_FILE = "data/state/system_state.json"
BACKUP_FILE = f"data/state/system_state.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}.json"

def clean_state():
    if not os.path.exists(STATE_FILE):
        print(f"State file {STATE_FILE} not found.")
        return

    # Backup first
    shutil.copy(STATE_FILE, BACKUP_FILE)
    print(f"Backed up state to {BACKUP_FILE}")

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    positions = state.get("positions", {})
    print(f"Current positions: {len(positions)}")

    # Group by (Symbol, Side, Entry Price) to find duplicates
    unique_positions = {}
    duplicates_removed = 0
    
    # We want to keep the OLDEST position (first opened)
    # Sort positions by opened_at
    sorted_positions = sorted(
        positions.items(),
        key=lambda x: x[1].get('opened_at', '')
    )
    
    cleaned_positions = {}
    
    for pid, pos in sorted_positions:
        symbol = pos.get("symbol", {}).get("ticker", "UNKNOWN") if isinstance(pos.get("symbol"), dict) else str(pos.get("symbol"))
        side = pos.get("side")
        entry_price = float(pos.get("entry_price", 0))
        quantity = float(pos.get("quantity", 0))
        
        # Key for uniqueness: Symbol + Side + Approx Entry Price + Quantity
        # Round entry price to 2 decimal places for grouping
        key = (symbol, side, round(entry_price, 2), quantity)
        
        if key not in unique_positions:
            unique_positions[key] = pid
            cleaned_positions[pid] = pos
        else:
            duplicates_removed += 1
            # print(f"Removing duplicate: {symbol} {side} @ {entry_price}")

    print(f"Removed {duplicates_removed} duplicate positions.")
    print(f"Remaining positions: {len(cleaned_positions)}")
    
    state["positions"] = cleaned_positions
    state["metadata"]["cleaned_at"] = datetime.now().isoformat()
    
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        
    print("State cleaned successfully.")

if __name__ == "__main__":
    clean_state()
