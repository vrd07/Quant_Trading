#!/usr/bin/env python3
"""
Paper Trading Script - Run on demo account.

This is for testing strategies with real market data
but without risking real money.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import TradingSystem


def main():
    print("=" * 60)
    print("PAPER TRADING MODE")
    print("=" * 60)
    print("Running on DEMO account")
    print("Using config/config_paper.yaml")
    print("=" * 60)
    
    # Create system with paper config
    system = TradingSystem(config_file="config/config_paper.yaml")
    
    # Run
    system.run()


if __name__ == "__main__":
    main()
