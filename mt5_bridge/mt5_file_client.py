#!/usr/bin/env python3
"""
File-Based MT5 Bridge Client

Communicates with MT5 via shared JSON files.
No sockets, no libraries - 100% compatible with Wine/Mac.

Usage:
    from mt5_file_client import MT5FileClient
    
    client = MT5FileClient()
    account = client.get_account_info()
    print(f"Balance: ${account['balance']}")
"""

import json
import time
import os
from pathlib import Path
from datetime import datetime

class MT5FileClient:
    """File-based client for MT5 communication."""
    
    def __init__(self, data_dir=None):
        """
        Initialize the file-based MT5 client.
        
        Args:
            data_dir: Directory for communication files. 
                     Defaults to MT5 Common Files folder.
        """
        if data_dir is None:
            # Use MT5 Common Files directory (Wine/Mac path)
            self.data_dir = Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files"
        else:
            self.data_dir = Path(data_dir)
        
        self.command_file = self.data_dir / "mt5_commands.json"
        self.status_file = self.data_dir / "mt5_status.json"
        self.response_file = self.data_dir / "mt5_responses.json"
        
        # Create directory if it doesn't exist
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"📁 MT5 File Bridge Client")
        print(f"   Data Directory: {self.data_dir}")
        print(f"   Command File: {self.command_file.name}")
        print(f"   Status File: {self.status_file.name}")
        print(f"   Response File: {self.response_file.name}")
    
    def _send_command(self, command_dict, timeout=5):
        """
        Send a command and wait for response.
        
        Args:
            command_dict: Dictionary containing command data
            timeout: Maximum seconds to wait for response
            
        Returns:
            dict: Response from MT5
        """
        # Clear old response file
        if self.response_file.exists():
            self.response_file.unlink()
        
        # Small delay to ensure file is deleted
        time.sleep(0.05)
        
        # Add timestamp to make command unique (EA ignores duplicates)
        command_dict['timestamp'] = time.time()
        
        # Write command in UTF-16 format (MT5 expects this)
        with open(self.command_file, 'w', encoding='utf-16') as f:
            json.dump(command_dict, f)
        
        # Wait for response file to be created
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.response_file.exists():
                try:
                    # Small delay to ensure file is fully written
                    time.sleep(0.05)
                    
                    # MT5 writes files in UTF-16 format with BOM
                    with open(self.response_file, 'r', encoding='utf-16') as f:
                        response = json.load(f)
                        return response
                except (json.JSONDecodeError, FileNotFoundError, UnicodeDecodeError):
                    # File not ready yet, wait and retry
                    pass
            time.sleep(0.1)
        
        raise TimeoutError(f"No response from MT5 after {timeout} seconds")
    
    def get_status(self):
        """
        Get current MT5 status (updated every tick).
        
        Returns:
            dict: Current status including bid/ask prices and account info
        """
        if not self.status_file.exists():
            raise FileNotFoundError("Status file not found - is EA running?")
        
        # Retry logic to handle race conditions when MT5 is writing to file
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # MT5 writes files in UTF-16 format with BOM
                with open(self.status_file, 'r', encoding='utf-16') as f:
                    return json.load(f)
            except (json.JSONDecodeError, ValueError) as e:
                if attempt < max_retries - 1:
                    time.sleep(0.05)  # Wait 50ms and retry
                else:
                    raise e
    
    def heartbeat(self):
        """
        Send heartbeat command to check if EA is responding.
        
        Returns:
            dict: Heartbeat response with timestamp and status
        """
        return self._send_command({"command": "HEARTBEAT"})
    
    def get_account_info(self):
        """
        Get account information.
        
        Returns:
            dict: Account balance, equity, margin, etc.
        """
        return self._send_command({"command": "GET_ACCOUNT_INFO"})
    
    def get_positions(self):
        """
        Get current open positions.
        
        Returns:
            dict: List of positions with ticket, symbol, type, volume, etc.
        """
        return self._send_command({"command": "GET_POSITIONS"})
    
    def place_order(self, symbol, order_type, volume):
        """
        Place a market order.
        
        Args:
            symbol: Trading symbol (e.g., "EURUSD")
            order_type: "BUY" or "SELL"
            volume: Lot size (e.g., 0.01)
            
        Returns:
            dict: Order result with ticket number if successful
        """
        return self._send_command({
            "command": "PLACE_ORDER",
            "symbol": symbol,
            "order_type": order_type.upper(),
            "volume": str(volume)
        })
    
    def close_position(self, ticket):
        """
        Close an open position by ticket number.
        
        Args:
            ticket: Position ticket number
            
        Returns:
            dict: Result of close operation
        """
        return self._send_command({
            "command": "CLOSE_POSITION",
            "ticket": str(ticket)
        })


def test_file_bridge():
    """Test the file-based MT5 bridge."""
    print("=" * 60)
    print("MT5 File Bridge Connection Test")
    print("=" * 60)
    
    client = MT5FileClient()
    
    # Test 1: Check status file
    print("\n1. Testing status file...")
    try:
        status = client.get_status()
        print(f"✓ Status: {status.get('status')}")
        print(f"  Symbol: {status.get('symbol')}")
        print(f"  Bid: {status.get('bid')}, Ask: {status.get('ask')}")
        print(f"  Balance: ${status.get('balance')}")
    except FileNotFoundError:
        print("✗ Status file not found - Is EA running?")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    
    # Test 2: Heartbeat
    print("\n2. Testing HEARTBEAT...")
    try:
        response = client.heartbeat()
        print(f"✓ Response: {response}")
    except TimeoutError:
        print("✗ Timeout - EA not responding to commands")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    
    # Test 3: Account Info
    print("\n3. Testing GET_ACCOUNT_INFO...")
    try:
        account = client.get_account_info()
        print(f"✓ Account Info:")
        print(f"   Balance: ${account.get('balance')}")
        print(f"   Equity:  ${account.get('equity')}")
        print(f"   Margin:  ${account.get('margin')}")
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    
    # Test 4: Get Positions
    print("\n4. Testing GET_POSITIONS...")
    try:
        positions = client.get_positions()
        pos_count = len(positions.get('positions', []))
        print(f"✓ Open positions: {pos_count}")
        if pos_count > 0:
            for pos in positions['positions']:
                print(f"   {pos['symbol']}: {pos['type']} {pos['volume']} lots")
    except Exception as e:
        print(f"✗ Error: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✓ All tests passed - File bridge is working!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    test_file_bridge()
