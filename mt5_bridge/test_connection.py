#!/usr/bin/env python3
"""
Test script for MT5 ZeroMQ Bridge.

Usage:
    python test_connection.py

This script tests:
1. REQ/REP socket (send HEARTBEAT)
2. SUB socket (receive ticks)
3. PULL socket (receive fills - if any)
"""

import zmq
import json
import time
from datetime import datetime

# Configuration
REQ_PORT = 5555
SUB_PORT = 5557
PULL_PORT = 5556
HOST = "localhost"

def test_heartbeat():
    """Test REQ/REP socket with HEARTBEAT command."""
    print("Testing HEARTBEAT...")
    
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect(f"tcp://{HOST}:{REQ_PORT}")
    socket.setsockopt(zmq.RCVTIMEO, 5000)  # 5 second timeout
    
    try:
        # Send command
        command = {"command": "HEARTBEAT"}
        socket.send_string(json.dumps(command))
        print(f"→ Sent: {command}")
        
        # Receive response
        response = socket.recv_string()
        print(f"← Received: {response}")
        
        response_obj = json.loads(response)
        if response_obj.get("status") == "ALIVE":
            print("✓ HEARTBEAT successful")
            return True
        else:
            print("✗ Unexpected response")
            return False
            
    except zmq.error.Again:
        print("✗ Timeout - EA not responding")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        socket.close()
        context.term()

def test_tick_stream(duration=5):
    """Test SUB socket by receiving ticks for N seconds."""
    print(f"\nTesting TICK stream for {duration} seconds...")
    
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(f"tcp://{HOST}:{SUB_PORT}")
    socket.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to all messages
    socket.setsockopt(zmq.RCVTIMEO, 1000)  # 1 second timeout
    
    tick_count = 0
    start_time = time.time()
    
    try:
        while time.time() - start_time < duration:
            try:
                message = socket.recv_string()
                tick = json.loads(message)
                
                if tick.get("type") == "TICK":
                    tick_count += 1
                    print(f"← Tick #{tick_count}: {tick['symbol']} "
                          f"Bid={tick['bid']:.2f} Ask={tick['ask']:.2f}")
            except zmq.error.Again:
                # No message received, continue
                continue
        
        if tick_count > 0:
            print(f"✓ Received {tick_count} ticks")
            return True
        else:
            print("✗ No ticks received")
            return False
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        socket.close()
        context.term()

def test_account_info():
    """Test GET_ACCOUNT_INFO command."""
    print("\nTesting GET_ACCOUNT_INFO...")
    
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.connect(f"tcp://{HOST}:{REQ_PORT}")
    socket.setsockopt(zmq.RCVTIMEO, 5000)
    
    try:
        command = {"command": "GET_ACCOUNT_INFO"}
        socket.send_string(json.dumps(command))
        
        response = socket.recv_string()
        account = json.loads(response)
        
        print(f"← Account Info:")
        print(f"   Balance: ${account.get('balance', 0):.2f}")
        print(f"   Equity:  ${account.get('equity', 0):.2f}")
        print(f"   Margin:  ${account.get('margin', 0):.2f}")
        print("✓ GET_ACCOUNT_INFO successful")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        socket.close()
        context.term()

def main():
    """Run all tests."""
    print("=" * 60)
    print("MT5 ZeroMQ Bridge Connection Test")
    print("=" * 60)
    
    results = {
        "heartbeat": test_heartbeat(),
        "tick_stream": test_tick_stream(duration=5),
        "account_info": test_account_info()
    }
    
    print("\n" + "=" * 60)
    print("Test Results:")
    print("=" * 60)
    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{test_name:20s}: {status}")
    
    all_passed = all(results.values())
    print("=" * 60)
    if all_passed:
        print("✓ All tests passed - MT5 bridge is working!")
    else:
        print("✗ Some tests failed - check EA logs in MT5")
    print("=" * 60)

if __name__ == "__main__":
    main()
