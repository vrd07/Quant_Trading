"""
Unit tests for MT5 ZeroMQ Client.

These tests use mock sockets to test the client logic without requiring
a running MT5 bridge.

Run with:
    pytest tests/test_mt5_zmq_client.py -v
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mt5_zmq_client import (
    MT5ZmqClient,
    Position,
    AccountInfo,
    OrderResult,
    CloseResult,
    TickData,
    FillData,
    OrderSide,
    OrderType,
)


class TestDataClasses:
    """Test dataclass definitions and properties."""
    
    def test_position_creation(self):
        """Test Position dataclass."""
        pos = Position(
            position_id="123456",
            ticket="789",
            symbol="XAUUSD",
            side="LONG",
            quantity=0.1,
            entry_price=2000.0,
            current_price=2005.0,
            unrealized_pnl=50.0,
            stop_loss=1990.0,
            take_profit=2010.0,
        )
        
        assert pos.position_id == "123456"
        assert pos.symbol == "XAUUSD"
        assert pos.side == "LONG"
        assert pos.quantity == 0.1
        assert pos.unrealized_pnl == 50.0
    
    def test_account_info_creation(self):
        """Test AccountInfo dataclass."""
        account = AccountInfo(
            balance=10000.0,
            equity=10050.0,
            margin=200.0,
            free_margin=9800.0,
            margin_level=5025.0,
            currency="USD",
            leverage=100,
        )
        
        assert account.balance == 10000.0
        assert account.equity == 10050.0
        assert account.currency == "USD"
        assert account.leverage == 100
    
    def test_order_result_success(self):
        """Test OrderResult success check."""
        result = OrderResult(
            order_id="123",
            deal_id="456",
            status="ACCEPTED",
            filled_price=2005.5,
            filled_volume=0.1,
            retcode=10009,
        )
        
        assert result.is_success is True
        assert result.order_id == "123"
    
    def test_order_result_failure(self):
        """Test OrderResult failure check."""
        result = OrderResult(
            order_id="",
            error="Insufficient margin",
        )
        
        assert result.is_success is False
        assert result.error == "Insufficient margin"
    
    def test_close_result_success(self):
        """Test CloseResult success check."""
        result = CloseResult(
            status="CLOSED",
            position_id="123",
            realized_pnl=50.0,
            close_price=2005.5,
        )
        
        assert result.is_success is True
        assert result.realized_pnl == 50.0
    
    def test_close_result_failure(self):
        """Test CloseResult failure check."""
        result = CloseResult(
            status="ERROR",
            error="Position not found",
        )
        
        assert result.is_success is False
    
    def test_tick_data_creation(self):
        """Test TickData dataclass."""
        tick = TickData(
            type="TICK",
            symbol="XAUUSD",
            timestamp="2026-01-21T14:30:00.123Z",
            bid=2005.50,
            ask=2005.60,
            last=2005.55,
            volume=1250.0,
        )
        
        assert tick.symbol == "XAUUSD"
        assert tick.bid == 2005.50
        assert tick.ask == 2005.60
    
    def test_fill_data_creation(self):
        """Test FillData dataclass."""
        fill = FillData(
            type="FILL",
            order_id="123",
            position_id="456",
            symbol="XAUUSD",
            side="BUY",
            filled_quantity=0.1,
            filled_price=2005.55,
            commission=0.0,
            timestamp="2026-01-21T14:30:00.456Z",
        )
        
        assert fill.order_id == "123"
        assert fill.side == "BUY"
        assert fill.filled_quantity == 0.1


class TestEnums:
    """Test enumeration classes."""
    
    def test_order_side_values(self):
        """Test OrderSide enum values."""
        assert OrderSide.BUY.value == "BUY"
        assert OrderSide.SELL.value == "SELL"
    
    def test_order_type_values(self):
        """Test OrderType enum values."""
        assert OrderType.MARKET.value == "MARKET"
        assert OrderType.LIMIT.value == "LIMIT"


class TestMT5ZmqClientInitialization:
    """Test MT5ZmqClient initialization (no mocking needed)."""
    
    def test_client_initialization(self):
        """Test client initializes with correct defaults."""
        client = MT5ZmqClient()
        
        assert client.host == "127.0.0.1"
        assert client.rep_port == 5555
        assert client.push_port == 5556
        assert client.pub_port == 5557
        assert client.request_timeout == 5000
    
    def test_client_custom_ports(self):
        """Test client with custom port configuration."""
        client = MT5ZmqClient(
            host="192.168.1.100",
            rep_port=6555,
            push_port=6556,
            pub_port=6557,
            request_timeout=10000,
        )
        
        assert client.host == "192.168.1.100"
        assert client.rep_port == 6555
        assert client.push_port == 6556
        assert client.pub_port == 6557
        assert client.request_timeout == 10000


class TestMT5ZmqClientWithMockedConnection:
    """Test MT5ZmqClient with properly mocked internal state."""
    
    @pytest.fixture
    def mock_client(self):
        """Create a client with mocked internal sockets."""
        client = MT5ZmqClient()
        
        # Mock the internal socket
        client.req_socket = MagicMock()
        client.pull_socket = MagicMock()
        client.sub_socket = MagicMock()
        client.context = MagicMock()
        client._connected = True
        
        return client
    
    def test_heartbeat(self, mock_client):
        """Test heartbeat command."""
        expected_response = {
            "status": "ALIVE",
            "timestamp": "2026-01-21T14:30:00Z",
            "symbol": "XAUUSD",
        }
        mock_client.req_socket.recv_json.return_value = expected_response
        
        result = mock_client.heartbeat()
        
        assert result["status"] == "ALIVE"
        mock_client.req_socket.send_json.assert_called_with({"command": "HEARTBEAT"})
    
    def test_place_order_success(self, mock_client):
        """Test successful order placement."""
        mock_client.req_socket.recv_json.return_value = {
            "order_id": "123456",
            "deal_id": "789012",
            "status": "ACCEPTED",
            "filled_price": 2005.55,
            "filled_volume": 0.1,
            "retcode": 10009,
        }
        
        result = mock_client.place_order(
            symbol="XAUUSD",
            side="BUY",
            quantity=0.1,
            stop_loss=1990.0,
            take_profit=2010.0,
        )
        
        assert result.is_success is True
        assert result.order_id == "123456"
        assert result.filled_price == 2005.55
    
    def test_place_order_failure(self, mock_client):
        """Test order placement failure."""
        mock_client.req_socket.recv_json.return_value = {
            "error": "Insufficient margin",
        }
        
        result = mock_client.place_order(
            symbol="XAUUSD",
            side="BUY",
            quantity=10.0,
        )
        
        assert result.is_success is False
        assert result.error == "Insufficient margin"
    
    def test_close_position_success(self, mock_client):
        """Test successful position close."""
        mock_client.req_socket.recv_json.return_value = {
            "status": "CLOSED",
            "position_id": "123456",
            "realized_pnl": 50.0,
            "close_price": 2005.55,
            "order_id": "789012",
        }
        
        result = mock_client.close_position("123456")
        
        assert result.is_success is True
        assert result.realized_pnl == 50.0
    
    def test_close_position_failure(self, mock_client):
        """Test position close failure."""
        mock_client.req_socket.recv_json.return_value = {
            "error": "Position not found",
        }
        
        result = mock_client.close_position("invalid_id")
        
        assert result.is_success is False
        assert result.error == "Position not found"
    
    def test_get_positions(self, mock_client):
        """Test getting positions."""
        mock_client.req_socket.recv_json.return_value = {
            "positions": [
                {
                    "position_id": "123",
                    "ticket": "456",
                    "symbol": "XAUUSD",
                    "side": "LONG",
                    "quantity": 0.1,
                    "entry_price": 2000.0,
                    "current_price": 2005.0,
                    "unrealized_pnl": 50.0,
                    "stop_loss": 1990.0,
                    "take_profit": 2010.0,
                    "swap": 0.0,
                    "magic": "123456",
                    "comment": "",
                    "open_time": "2026-01-21T10:00:00Z",
                },
            ],
            "count": 1,
        }
        
        positions = mock_client.get_positions()
        
        assert len(positions) == 1
        assert positions[0].symbol == "XAUUSD"
        assert positions[0].side == "LONG"
        assert positions[0].unrealized_pnl == 50.0
    
    def test_get_positions_empty(self, mock_client):
        """Test getting positions when none exist."""
        mock_client.req_socket.recv_json.return_value = {
            "positions": [],
            "count": 0,
        }
        
        positions = mock_client.get_positions()
        
        assert len(positions) == 0
    
    def test_get_account_info(self, mock_client):
        """Test getting account info."""
        mock_client.req_socket.recv_json.return_value = {
            "balance": 10000.0,
            "equity": 10050.0,
            "margin": 200.0,
            "free_margin": 9800.0,
            "margin_level": 5025.0,
            "profit": 50.0,
            "credit": 0.0,
            "currency": "USD",
            "leverage": 100,
            "trade_allowed": True,
            "trade_expert": True,
            "account_id": "12345",
            "server": "Demo-Server",
            "company": "Broker Inc",
            "account_type": "DEMO",
        }
        
        account = mock_client.get_account_info()
        
        assert account is not None
        assert account.balance == 10000.0
        assert account.equity == 10050.0
        assert account.currency == "USD"
        assert account.leverage == 100
    
    def test_modify_position(self, mock_client):
        """Test modifying position SL/TP."""
        mock_client.req_socket.recv_json.return_value = {
            "status": "MODIFIED",
            "position_id": "123456",
            "new_stop_loss": 1995.0,
            "new_take_profit": 2015.0,
        }
        
        result = mock_client.modify_position(
            position_id="123456",
            stop_loss=1995.0,
            take_profit=2015.0,
        )
        
        assert result["status"] == "MODIFIED"
    
    def test_disconnect(self, mock_client):
        """Test disconnection."""
        mock_client.disconnect()
        
        assert mock_client._connected is False
        mock_client.req_socket.close.assert_called_once()
        mock_client.pull_socket.close.assert_called_once()
        mock_client.sub_socket.close.assert_called_once()


class TestMT5ZmqClientNotConnected:
    """Test behavior when not connected."""
    
    def test_send_command_not_connected(self):
        """Test that commands fail when not connected."""
        client = MT5ZmqClient()
        # Don't call connect()
        
        result = client.heartbeat()
        
        assert "error" in result
    
    def test_place_order_not_connected(self):
        """Test place order fails when not connected."""
        client = MT5ZmqClient()
        
        # This should raise or return error
        with pytest.raises(ConnectionError):
            client._send_command("PLACE_ORDER", {})


class TestMT5ZmqClientCallbacks:
    """Test callback registration (no mocking needed for registration)."""
    
    def test_tick_callback_registration(self):
        """Test registering tick callback."""
        client = MT5ZmqClient()
        callback = Mock()
        
        client._tick_callbacks.append(callback)
        
        assert callback in client._tick_callbacks
    
    def test_fill_callback_registration(self):
        """Test registering fill callback."""
        client = MT5ZmqClient()
        callback = Mock()
        
        client._fill_callbacks.append(callback)
        
        assert callback in client._fill_callbacks
    
    def test_multiple_callbacks(self):
        """Test registering multiple callbacks."""
        client = MT5ZmqClient()
        callback1 = Mock()
        callback2 = Mock()
        
        client._tick_callbacks.append(callback1)
        client._tick_callbacks.append(callback2)
        
        assert len(client._tick_callbacks) == 2


class TestOrderResultProperties:
    """Test OrderResult and CloseResult edge cases."""
    
    def test_order_result_with_error_and_status(self):
        """Test OrderResult with both error and status (error takes precedence)."""
        result = OrderResult(
            order_id="123",
            status="ACCEPTED",
            error="Some error",
        )
        
        # Has error, so should not be success
        assert result.is_success is False
    
    def test_order_result_partial_fill(self):
        """Test OrderResult with partial fill data."""
        result = OrderResult(
            order_id="123",
            status="ACCEPTED",
            filled_price=2005.0,
            # filled_volume not set
        )
        
        assert result.is_success is True
        assert result.filled_volume == 0.0  # default
    
    def test_close_result_with_no_realized_pnl(self):
        """Test CloseResult when position closed at breakeven."""
        result = CloseResult(
            status="CLOSED",
            realized_pnl=0.0,
        )
        
        assert result.is_success is True
        assert result.realized_pnl == 0.0


class TestPositionDataClass:
    """Test Position dataclass edge cases."""
    
    def test_position_with_defaults(self):
        """Test Position with only required fields."""
        pos = Position(
            position_id="123",
            ticket="456",
            symbol="EURUSD",
            side="SHORT",
            quantity=0.5,
            entry_price=1.0850,
            current_price=1.0840,
            unrealized_pnl=50.0,
            stop_loss=1.0900,
            take_profit=1.0800,
        )
        
        # Check defaults
        assert pos.swap == 0.0
        assert pos.magic == ""
        assert pos.comment == ""
        assert pos.open_time == ""
    
    def test_position_short(self):
        """Test short position."""
        pos = Position(
            position_id="123",
            ticket="456",
            symbol="EURUSD",
            side="SHORT",
            quantity=1.0,
            entry_price=1.0850,
            current_price=1.0840,
            unrealized_pnl=100.0,
            stop_loss=1.0900,
            take_profit=1.0750,
        )
        
        assert pos.side == "SHORT"
        assert pos.unrealized_pnl == 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
