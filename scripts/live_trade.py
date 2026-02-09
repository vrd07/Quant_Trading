#!/usr/bin/env python3
"""
Live Trading Script - Run on REAL account.

⚠️  WARNING: THIS TRADES REAL MONEY ⚠️

Only run this if you:
1. Have thoroughly tested in paper trading
2. Understand the risks
3. Can afford to lose the capital
4. Have reviewed all configuration
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import TradingSystem


def main():
    print("=" * 60)
    print("⚠️  LIVE TRADING MODE - REAL MONEY ⚠️")
    print("=" * 60)
    
    # Confirmation
    response = input("Are you ABSOLUTELY SURE you want to trade live? (type 'YES' to confirm): ")
    
    if response != "YES":
        print("Live trading cancelled")
        return
    
    print("Starting live trading...")
    print("Using config/config_live.yaml")
    print("=" * 60)
    
    # Create system with live config
    system = TradingSystem(config_file="config/config_live.yaml")
    
    # Run
    system.run()


if __name__ == "__main__":
    main()
