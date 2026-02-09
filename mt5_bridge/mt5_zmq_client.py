"""
MT5 ZeroMQ Client - Python Bridge for MetaTrader 5

This module provides a Python client to communicate with the EA_ZeroMQ_Bridge.mq5
Expert Advisor running in MetaTrader 5.

Usage:
    from mt5_zmq_client import MT5ZmqClient
    
    client = MT5ZmqClient()
    client.connect()
    
    # Check connection
    print(client.heartbeat())
    
    # Place an order
    result = client.place_order(
        symbol="XAUUSD",
        side="BUY",
        quantity=0.1,
        stop_loss=1990.0,
        take_profit=2010.0
    )
"""

import json
import logging
import threading
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

import zmq

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type enumeration."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass
class Position:
    """Represents an open trading position."""
    position_id: str
    ticket: str
    symbol: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: float
    take_profit: float
    swap: float = 0.0
    magic: str = ""
    comment: str = ""
    open_time: str = ""


@dataclass
class AccountInfo:
    """Represents account information."""
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    profit: float = 0.0
    credit: float = 0.0
    currency: str = "USD"
    leverage: int = 100
    trade_allowed: bool = True
    trade_expert: bool = True
    account_id: str = ""
    server: str = ""
    company: str = ""
    account_type: str = "DEMO"


@dataclass
class OrderResult:
    """Represents the result of an order placement."""
    order_id: str
    deal_id: str = ""
    status: str = ""
    filled_price: float = 0.0
    filled_volume: float = 0.0
    retcode: int = 0
    error: str = ""
    
    @property
    def is_success(self) -> bool:
        return self.status == "ACCEPTED" and not self.error


@dataclass
class CloseResult:
    """Represents the result of closing a position."""
    status: str
    position_id: str = ""
    realized_pnl: float = 0.0
    close_price: float = 0.0
    order_id: str = ""
    error: str = ""
    
    @property
    def is_success(self) -> bool:
        return self.status == "CLOSED" and not self.error


@dataclass
class TickData:
    """Represents tick data from MT5."""
    type: str
    symbol: str
    timestamp: str
    bid: float
    ask: float
    last: float
    volume: float
    volume_real: float = 0.0
    flags: int = 0


@dataclass 
class FillData:
    """Represents fill confirmation from MT5."""
    type: str
    order_id: str
    position_id: str
    symbol: str
    side: str
    filled_quantity: float
    filled_price: float
    commission: float
    timestamp: str


class MT5ZmqClient:
    """
    ZeroMQ client for communicating with MetaTrader 5 via EA_ZeroMQ_Bridge.
    
    This client provides methods for:
    - Sending trading commands (place order, close position, modify order)
    - Querying account and position information
    - Subscribing to real-time tick data
    - Receiving fill confirmations
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        rep_port: int = 5555,
        push_port: int = 5556,
        pub_port: int = 5557,
        request_timeout: int = 5000,  # milliseconds
    ):
        """
        Initialize the MT5 ZeroMQ client.
        
        Args:
            host: MT5 server host address
            rep_port: REP socket port for request/reply
            push_port: PUSH socket port for fill confirmations (we PULL)
            pub_port: PUB socket port for tick data (we SUB)
            request_timeout: Request timeout in milliseconds
        """
        self.host = host
        self.rep_port = rep_port
        self.push_port = push_port
        self.pub_port = pub_port
        self.request_timeout = request_timeout
        
        self.context: Optional[zmq.Context] = None
        self.req_socket: Optional[zmq.Socket] = None
        self.pull_socket: Optional[zmq.Socket] = None
        self.sub_socket: Optional[zmq.Socket] = None
        
        self._connected = False
        self._tick_thread: Optional[threading.Thread] = None
        self._fill_thread: Optional[threading.Thread] = None
        self._running = False
        
        self._tick_callbacks: List[Callable[[TickData], None]] = []
        self._fill_callbacks: List[Callable[[FillData], None]] = []
    
    def connect(self) -> bool:
        """
        Connect to the MT5 ZeroMQ bridge.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.context = zmq.Context()
            
            # REQ socket for sending commands
            self.req_socket = self.context.socket(zmq.REQ)
            self.req_socket.setsockopt(zmq.RCVTIMEO, self.request_timeout)
            self.req_socket.setsockopt(zmq.SNDTIMEO, self.request_timeout)
            self.req_socket.setsockopt(zmq.LINGER, 0)
            self.req_socket.connect(f"tcp://{self.host}:{self.rep_port}")
            
            # PULL socket for receiving fill confirmations
            self.pull_socket = self.context.socket(zmq.PULL)
            self.pull_socket.setsockopt(zmq.RCVTIMEO, 100)
            self.pull_socket.setsockopt(zmq.LINGER, 0)
            self.pull_socket.connect(f"tcp://{self.host}:{self.push_port}")
            
            # SUB socket for receiving tick data
            self.sub_socket = self.context.socket(zmq.SUB)
            self.sub_socket.setsockopt(zmq.RCVTIMEO, 100)
            self.sub_socket.setsockopt(zmq.LINGER, 0)
            self.sub_socket.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to all
            self.sub_socket.connect(f"tcp://{self.host}:{self.pub_port}")
            
            # Test connection with heartbeat
            result = self.heartbeat()
            if result and result.get("status") == "ALIVE":
                self._connected = True
                logger.info(f"Connected to MT5 bridge at {self.host}")
                return True
            else:
                logger.error(f"Heartbeat failed: {result}")
                return False
                
        except zmq.ZMQError as e:
            logger.error(f"ZMQ connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the MT5 ZeroMQ bridge."""
        self._running = False
        
        # Wait for threads to stop
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=1.0)
        if self._fill_thread and self._fill_thread.is_alive():
            self._fill_thread.join(timeout=1.0)
        
        # Close sockets
        if self.req_socket:
            self.req_socket.close()
        if self.pull_socket:
            self.pull_socket.close()
        if self.sub_socket:
            self.sub_socket.close()
        
        # Terminate context
        if self.context:
            self.context.term()
        
        self._connected = False
        logger.info("Disconnected from MT5 bridge")
    
    def _send_command(self, command: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Send a command to the MT5 bridge and receive response.
        
        Args:
            command: Command name (e.g., "HEARTBEAT", "PLACE_ORDER")
            payload: Optional command payload
            
        Returns:
            Response dictionary from MT5
        """
        if not self._connected:
            raise ConnectionError("Not connected to MT5 bridge")
        
        # Build message
        message = {"command": command}
        if payload:
            message.update(payload)
        
        try:
            # Send request
            self.req_socket.send_json(message)
            
            # Receive response
            response = self.req_socket.recv_json()
            return response
            
        except zmq.Again:
            raise TimeoutError(f"Request timed out: {command}")
        except zmq.ZMQError as e:
            raise ConnectionError(f"ZMQ error: {e}")
    
    # ===== Trading Commands =====
    
    def heartbeat(self) -> Dict[str, Any]:
        """
        Send heartbeat to check connection.
        
        Returns:
            Heartbeat response with status and timestamp
        """
        try:
            return self._send_command("HEARTBEAT")
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
            return {"error": str(e)}
    
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> OrderResult:
        """
        Place a trading order.
        
        Args:
            symbol: Trading symbol (e.g., "XAUUSD")
            side: Order side ("BUY" or "SELL")
            quantity: Order quantity (lot size)
            order_type: Order type ("MARKET")
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            OrderResult with order details or error
        """
        payload = {
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "order_type": order_type.upper(),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        
        response = self._send_command("PLACE_ORDER", payload)
        
        if "error" in response:
            return OrderResult(order_id="", error=response["error"])
        
        return OrderResult(
            order_id=response.get("order_id", ""),
            deal_id=response.get("deal_id", ""),
            status=response.get("status", ""),
            filled_price=response.get("filled_price", 0.0),
            filled_volume=response.get("filled_volume", 0.0),
            retcode=response.get("retcode", 0),
        )
    
    def close_position(self, position_id: str) -> CloseResult:
        """
        Close an open position.
        
        Args:
            position_id: Position identifier to close
            
        Returns:
            CloseResult with close details or error
        """
        payload = {"position_id": position_id}
        
        response = self._send_command("CLOSE_POSITION", payload)
        
        if "error" in response:
            return CloseResult(status="ERROR", error=response["error"])
        
        return CloseResult(
            status=response.get("status", ""),
            position_id=response.get("position_id", ""),
            realized_pnl=response.get("realized_pnl", 0.0),
            close_price=response.get("close_price", 0.0),
            order_id=response.get("order_id", ""),
        )
    
    def modify_position(
        self,
        position_id: str,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Modify an existing position's stop loss and take profit.
        
        Args:
            position_id: Position identifier to modify
            stop_loss: New stop loss (0 to keep existing)
            take_profit: New take profit (0 to keep existing)
            
        Returns:
            Response dictionary with modification result
        """
        payload = {
            "position_id": position_id,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
        
        return self._send_command("MODIFY_ORDER", payload)
    
    def get_positions(self) -> List[Position]:
        """
        Get all open positions.
        
        Returns:
            List of Position objects
        """
        response = self._send_command("GET_POSITIONS")
        
        if "error" in response:
            logger.error(f"Error getting positions: {response['error']}")
            return []
        
        positions = []
        for pos_data in response.get("positions", []):
            positions.append(Position(
                position_id=pos_data.get("position_id", ""),
                ticket=pos_data.get("ticket", ""),
                symbol=pos_data.get("symbol", ""),
                side=pos_data.get("side", ""),
                quantity=pos_data.get("quantity", 0.0),
                entry_price=pos_data.get("entry_price", 0.0),
                current_price=pos_data.get("current_price", 0.0),
                unrealized_pnl=pos_data.get("unrealized_pnl", 0.0),
                stop_loss=pos_data.get("stop_loss", 0.0),
                take_profit=pos_data.get("take_profit", 0.0),
                swap=pos_data.get("swap", 0.0),
                magic=pos_data.get("magic", ""),
                comment=pos_data.get("comment", ""),
                open_time=pos_data.get("open_time", ""),
            ))
        
        return positions
    
    def get_account_info(self) -> Optional[AccountInfo]:
        """
        Get account information.
        
        Returns:
            AccountInfo object or None on error
        """
        response = self._send_command("GET_ACCOUNT_INFO")
        
        if "error" in response:
            logger.error(f"Error getting account info: {response['error']}")
            return None
        
        return AccountInfo(
            balance=response.get("balance", 0.0),
            equity=response.get("equity", 0.0),
            margin=response.get("margin", 0.0),
            free_margin=response.get("free_margin", 0.0),
            margin_level=response.get("margin_level", 0.0),
            profit=response.get("profit", 0.0),
            credit=response.get("credit", 0.0),
            currency=response.get("currency", "USD"),
            leverage=response.get("leverage", 100),
            trade_allowed=response.get("trade_allowed", True),
            trade_expert=response.get("trade_expert", True),
            account_id=response.get("account_id", ""),
            server=response.get("server", ""),
            company=response.get("company", ""),
            account_type=response.get("account_type", "DEMO"),
        )
    
    # ===== Streaming Data =====
    
    def subscribe_ticks(self, callback: Callable[[TickData], None]) -> None:
        """
        Subscribe to tick data stream.
        
        Args:
            callback: Function to call with each tick
        """
        self._tick_callbacks.append(callback)
        
        if not self._tick_thread or not self._tick_thread.is_alive():
            self._running = True
            self._tick_thread = threading.Thread(target=self._tick_listener, daemon=True)
            self._tick_thread.start()
    
    def subscribe_fills(self, callback: Callable[[FillData], None]) -> None:
        """
        Subscribe to fill confirmation stream.
        
        Args:
            callback: Function to call with each fill
        """
        self._fill_callbacks.append(callback)
        
        if not self._fill_thread or not self._fill_thread.is_alive():
            self._running = True
            self._fill_thread = threading.Thread(target=self._fill_listener, daemon=True)
            self._fill_thread.start()
    
    def _tick_listener(self) -> None:
        """Background thread to listen for tick data."""
        while self._running:
            try:
                message = self.sub_socket.recv_json(flags=zmq.NOBLOCK)
                
                if message.get("type") == "TICK":
                    tick = TickData(
                        type=message.get("type", ""),
                        symbol=message.get("symbol", ""),
                        timestamp=message.get("timestamp", ""),
                        bid=message.get("bid", 0.0),
                        ask=message.get("ask", 0.0),
                        last=message.get("last", 0.0),
                        volume=message.get("volume", 0.0),
                        volume_real=message.get("volume_real", 0.0),
                        flags=message.get("flags", 0),
                    )
                    
                    for callback in self._tick_callbacks:
                        try:
                            callback(tick)
                        except Exception as e:
                            logger.error(f"Tick callback error: {e}")
                            
            except zmq.Again:
                pass  # No message available
            except Exception as e:
                logger.error(f"Tick listener error: {e}")
    
    def _fill_listener(self) -> None:
        """Background thread to listen for fill confirmations."""
        while self._running:
            try:
                message = self.pull_socket.recv_json(flags=zmq.NOBLOCK)
                
                if message.get("type") == "FILL":
                    fill = FillData(
                        type=message.get("type", ""),
                        order_id=message.get("order_id", ""),
                        position_id=message.get("position_id", ""),
                        symbol=message.get("symbol", ""),
                        side=message.get("side", ""),
                        filled_quantity=message.get("filled_quantity", 0.0),
                        filled_price=message.get("filled_price", 0.0),
                        commission=message.get("commission", 0.0),
                        timestamp=message.get("timestamp", ""),
                    )
                    
                    for callback in self._fill_callbacks:
                        try:
                            callback(fill)
                        except Exception as e:
                            logger.error(f"Fill callback error: {e}")
                            
                elif message.get("type") == "ERROR":
                    logger.error(f"MT5 Error: {message.get('message', 'Unknown error')}")
                    
            except zmq.Again:
                pass  # No message available
            except Exception as e:
                logger.error(f"Fill listener error: {e}")
    
    # ===== Context Manager =====
    
    def __enter__(self) -> "MT5ZmqClient":
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()


# ===== Example Usage =====

if __name__ == "__main__":
    import time
    
    def on_tick(tick: TickData):
        print(f"TICK: {tick.symbol} Bid={tick.bid:.5f} Ask={tick.ask:.5f}")
    
    def on_fill(fill: FillData):
        print(f"FILL: {fill.symbol} {fill.side} {fill.filled_quantity}@{fill.filled_price}")
    
    # Using context manager
    with MT5ZmqClient() as client:
        # Check connection
        heartbeat = client.heartbeat()
        print(f"Heartbeat: {heartbeat}")
        
        # Get account info
        account = client.get_account_info()
        if account:
            print(f"Account Balance: {account.balance} {account.currency}")
            print(f"Account Equity: {account.equity}")
            print(f"Free Margin: {account.free_margin}")
        
        # Get open positions
        positions = client.get_positions()
        print(f"Open positions: {len(positions)}")
        for pos in positions:
            print(f"  {pos.symbol} {pos.side} {pos.quantity} @ {pos.entry_price}")
        
        # Subscribe to tick data
        client.subscribe_ticks(on_tick)
        client.subscribe_fills(on_fill)
        
        # Example: Place an order (commented out for safety)
        # result = client.place_order(
        #     symbol="XAUUSD",
        #     side="BUY",
        #     quantity=0.1,
        #     stop_loss=1990.0,
        #     take_profit=2010.0
        # )
        # print(f"Order result: {result}")
        
        # Keep running for tick data
        print("\nListening for ticks (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
