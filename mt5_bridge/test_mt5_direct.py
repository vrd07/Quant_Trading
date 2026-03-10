#!/usr/bin/env python3
"""
Test script for MT5 Direct Connection (no ZeroMQ needed).

This uses the official MetaTrader5 Python package to connect directly to MT5.

Requirements:
    pip install MetaTrader5

Usage:
    python test_mt5_direct.py
"""

import MetaTrader5 as mt5
from datetime import datetime
import time

def test_connection():
    """Test basic connection to MT5."""
    print("=" * 60)
    print("MT5 Direct Connection Test")
    print("=" * 60)
    
    # Initialize MT5 connection
    print("\n1. Testing MT5 initialization...")
    if not mt5.initialize():
        print(f"✗ Initialize failed: {mt5.last_error()}")
        return False
    
    print("✓ MT5 initialized successfully")
    return True

def test_account_info():
    """Get and display account information."""
    print("\n2. Testing account info...")
    
    account_info = mt5.account_info()
    if account_info is None:
        print(f"✗ Failed to get account info: {mt5.last_error()}")
        return False
    
    print("✓ Account Info:")
    print(f"   Login:   {account_info.login}")
    print(f"   Server:  {account_info.server}")
    print(f"   Balance: ${account_info.balance:.2f}")
    print(f"   Equity:  ${account_info.equity:.2f}")
    print(f"   Margin:  ${account_info.margin:.2f}")
    print(f"   Profit:  ${account_info.profit:.2f}")
    
    return True

def test_symbol_info():
    """Get symbol information."""
    print("\n3. Testing symbol info (EURUSD)...")
    
    # Select symbol
    symbol = "EURUSD"
    if not mt5.symbol_select(symbol, True):
        print(f"✗ Failed to select {symbol}")
        return False
    
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        print(f"✗ Failed to get symbol info: {mt5.last_error()}")
        return False
    
    print(f"✓ Symbol Info ({symbol}):")
    print(f"   Bid:          {symbol_info.bid:.5f}")
    print(f"   Ask:          {symbol_info.ask:.5f}")
    print(f"   Spread:       {symbol_info.spread} points")
    print(f"   Digits:       {symbol_info.digits}")
    print(f"   Point:        {symbol_info.point}")
    print(f"   Trade Mode:   {symbol_info.trade_mode}")
    
    return True

def test_tick_stream(duration=5):
    """Monitor live ticks for a few seconds."""
    print(f"\n4. Testing tick stream for {duration} seconds...")
    
    symbol = "EURUSD"
    tick_count = 0
    start_time = time.time()
    last_tick = None
    
    while time.time() - start_time < duration:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            continue
        
        # Only print if tick changed
        if last_tick is None or tick.bid != last_tick.bid or tick.ask != last_tick.ask:
            tick_count += 1
            timestamp = datetime.fromtimestamp(tick.time)
            print(f"← Tick #{tick_count}: {symbol} "
                  f"Bid={tick.bid:.5f} Ask={tick.ask:.5f} "
                  f"Time={timestamp.strftime('%H:%M:%S')}")
            last_tick = tick
        
        time.sleep(0.1)  # Check every 100ms
    
    if tick_count > 0:
        print(f"✓ Received {tick_count} ticks")
        return True
    else:
        print("✗ No ticks received")
        return False

def test_positions():
    """Get current open positions."""
    print("\n5. Testing positions...")
    
    positions = mt5.positions_get()
    if positions is None:
        print(f"✗ Failed to get positions: {mt5.last_error()}")
        return False
    
    if len(positions) == 0:
        print("✓ No open positions (expected for new account)")
    else:
        print(f"✓ Found {len(positions)} open position(s):")
        for pos in positions:
            print(f"   {pos.symbol}: {pos.type} {pos.volume} lots @ {pos.price_open}")
    
    return True

def test_history():
    """Get recent trade history."""
    print("\n6. Testing trade history...")
    
    # Get history for last 7 days
    from_date = datetime.now().timestamp() - (7 * 24 * 60 * 60)
    to_date = datetime.now().timestamp()
    
    deals = mt5.history_deals_get(int(from_date), int(to_date))
    
    if deals is None:
        print(f"⚠ No trade history (normal for new account)")
        return True
    
    print(f"✓ Found {len(deals)} historical deal(s)")
    return True

def main():
    """Run all tests."""
    results = {
        "connection": False,
        "account_info": False,
        "symbol_info": False,
        "tick_stream": False,
        "positions": False,
        "history": False,
    }
    
    try:
        # Test connection
        if not test_connection():
            print("\n✗ Cannot proceed - MT5 not initialized")
            return
        
        # Run all other tests
        results["connection"] = True
        results["account_info"] = test_account_info()
        results["symbol_info"] = test_symbol_info()
        results["tick_stream"] = test_tick_stream(duration=5)
        results["positions"] = test_positions()
        results["history"] = test_history()
        
    finally:
        # Always shutdown MT5 connection
        mt5.shutdown()
        print("\n" + "=" * 60)
        print("Test Results:")
        print("=" * 60)
        
        for test_name, result in results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            print(f"{test_name:20s}: {status}")
        
        all_passed = all(results.values())
        print("=" * 60)
        if all_passed:
            print("✓ All tests passed - MT5 direct connection is working!")
        else:
            print("✗ Some tests failed - check the output above")
        print("=" * 60)

if __name__ == "__main__":
    main()
