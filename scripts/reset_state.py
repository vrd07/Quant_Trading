#!/usr/bin/env python3
"""
Force reset of system state.
Run this ONLY when the main trading script is STOPPED.
"""

import os
from pathlib import Path
import sys

# Define state file path
PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / "data/state/system_state.json"
BACKUP_DIR = PROJECT_ROOT / "data/state/backups"

def main():
    print("=" * 60)
    print("SYSTEM STATE RESET TOOL")
    print("=" * 60)
    
    # 1. Check if main script is running (naive check)
    # We can't easily check cross-platform PIDs reliably without psutil, 
    # but we can warn the user.
    print("WARNING: Ensure execute main.py is STOPPED before proceeding.")
    response = input("Is the main trading script stopped? (y/n): ")
    if response.lower() != 'y':
        print("Please stop the main script first.")
        sys.exit(1)

    if not STATE_FILE.exists():
        print(f"\nState file not found: {STATE_FILE}")
        print("Nothing to reset.")
        return

    # 2. Create backup just in case
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True)
        
    backup_path = BACKUP_DIR / f"system_state_backup_forced.json"
    try:
        with open(STATE_FILE, 'r') as src, open(backup_path, 'w') as dst:
            dst.write(src.read())
        print(f"\nBackup created at: {backup_path}")
    except Exception as e:
        print(f"Warning: Could not create backup: {e}")

    # 3. Delete the file
    try:
        os.remove(STATE_FILE)
        print(f"✓ Deleted: {STATE_FILE}")
        print("\nSUCCESS: System state has been cleared.")
        print("You can now restart the main trading script.")
        print("It will fetch the TRUE positions from MT5 fresh.")
    except Exception as e:
        print(f"Error deleting file: {e}")

if __name__ == "__main__":
    main()
