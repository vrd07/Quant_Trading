#!/usr/bin/env python3
"""
Close ALL open positions in MT5 via File Bridge.
Use this to unclog the system when "Max positions" is reached.
"""

import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mt5_bridge.mt5_file_client import MT5FileClient

def main():
    print("=" * 60)
    print("MT5 EMERGENCY POSITION CLOSER")
    print("=" * 60)
    
    # 1. Initialize Client
    try:
        client = MT5FileClient()
        print(f"Connected to Bridge at: {client.data_dir}")
    except Exception as e:
        print(f"Failed to initialize client: {e}")
        return

    # 2. Get Positions
    print("\nFetching open positions...")
    try:
        response = client.get_positions()
        positions = response.get('positions', [])
        count = len(positions)
        
        if count == 0:
            print("✓ No open positions found. System is clean.")
            return

        print(f"⚠ Found {count} open positions!")
        
        # 3. Confirm Deletion
        confirm = input(f"Are you sure you want to CLOSE ALL {count} POSITIONS? (type 'YES'): ")
        if confirm != 'YES':
            print("Operation cancelled.")
            return

        # 4. Close Loop
        print("\nStarting close sequence...")
        success_count = 0
        fail_count = 0
        
        for i, pos in enumerate(positions, 1):
            ticket = pos['ticket']
            symbol = pos['symbol']
            lot = pos['volume']
            profit = pos['profit']
            
            print(f"[{i}/{count}] Closing {symbol} Ticket {ticket} ({lot} lots, PnL: {profit})... ", end='', flush=True)
            
            try:
                res = client.close_position(ticket)
                if res.get('status') == 'closed':
                    print("✓ DONE")
                    success_count += 1
                else:
                    print(f"✗ FAILED: {res.get('error', 'Unknown')}")
                    fail_count += 1
            except Exception as e:
                print(f"✗ ERROR: {e}")
                fail_count += 1
            
            # Small sleep to prevent file lockout race conditions
            time.sleep(0.05)

        print("-" * 60)
        print(f"Summary: {success_count} Closed, {fail_count} Failed")
        
        # 5. Verify final state
        end_resp = client.get_positions()
        remaining = len(end_resp.get('positions', []))
        if remaining == 0:
            print("\n✓ SUCCESS: All positions cleared.")
        else:
            print(f"\n⚠ WARNING: {remaining} positions still remain. Run script again.")

    except Exception as e:
        print(f"\nCritical Error: {e}")

if __name__ == "__main__":
    main()
