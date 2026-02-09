"""
MetaTrader 5 Connector via ZeroMQ Bridge.

This module provides the MT5Connector class that integrates the ZeroMQ bridge
with the algo_trading_system's core types and interfaces.

Usage:
    from connectors.mt5_connector import MT5Connector
    
    connector = MT5Connector()
    await connector.connect()
    
    # Place an order
    order = Order(
        symbol=Symbol(ticker="XAUUSD"),
        side=OrderSide.BUY,
        quantity=Decimal("0.1"),
    )
    result = await connector.place_order(order)
"""

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional
from uuid import UUID

from core.types import Order, Position, Tick, Symbol
from core.constants import OrderSide, OrderStatus, PositionSide
from core.exceptions import (
    MT5ConnectionError,
    OrderRejectedError,
    OrderTimeoutError,
    ConnectionLostError,
    HeartbeatTimeoutError,
)

# Attempt to import the ZeroMQ client
try:
    import sys
    sys.path.insert(0, str(__file__).replace('/algo_trading_system/src/connectors/mt5_connector.py', '/mt5_bridge'))
    from mt5_zmq_client import (
        MT5ZmqClient,
        TickData,
        FillData,
        Position as ZmqPosition,
        AccountInfo as ZmqAccountInfo,
        OrderResult,
        CloseResult,
    )
except ImportError:
    raise ImportError(
        "mt5_zmq_client not found. Ensure mt5_bridge/mt5_zmq_client.py is available."
    )

logger = logging.getLogger(__name__)


class MT5Connector:
    """
    MetaTrader 5 connector using ZeroMQ bridge.
    
    This class provides an async-compatible interface to communicate with
    MetaTrader 5 via the EA_ZeroMQ_Bridge Expert Advisor.
    
    Features:
    - Order placement, modification, and cancellation
    - Position management
    - Real-time tick data streaming
    - Fill confirmation handling
    - Automatic reconnection with heartbeat monitoring
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        rep_port: int = 5555,
        push_port: int = 5556,
        pub_port: int = 5557,
        request_timeout: int = 5000,
        heartbeat_interval: float = 5.0,
        max_reconnect_attempts: int = 3,
    ):
        """
        Initialize the MT5 connector.
        
        Args:
            host: MT5 bridge host address
            rep_port: REP socket port
            push_port: PUSH socket port
            pub_port: PUB socket port
            request_timeout: Request timeout in milliseconds
            heartbeat_interval: Seconds between heartbeat checks
            max_reconnect_attempts: Max reconnection attempts before failing
        """
        self.host = host
        self.rep_port = rep_port
        self.push_port = push_port
        self.pub_port = pub_port
        self.request_timeout = request_timeout
        self.heartbeat_interval = heartbeat_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        
        self._client: Optional[MT5ZmqClient] = None
        self._connected = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._symbol_cache: Dict[str, Symbol] = {}
        
        # Callbacks
        self._tick_callbacks: List[Callable[[Tick], None]] = []
        self._fill_callbacks: List[Callable[[Order], None]] = []
        self._position_callbacks: List[Callable[[Position], None]] = []
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to MT5."""
        return self._connected and self._client is not None
    
    async def connect(self) -> bool:
        """
        Connect to the MT5 ZeroMQ bridge.
        
        Returns:
            True if connection successful
            
        Raises:
            MT5ConnectionError: If connection fails after max attempts
        """
        for attempt in range(1, self.max_reconnect_attempts + 1):
            try:
                logger.info(f"Connecting to MT5 bridge (attempt {attempt}/{self.max_reconnect_attempts})...")
                
                self._client = MT5ZmqClient(
                    host=self.host,
                    rep_port=self.rep_port,
                    push_port=self.push_port,
                    pub_port=self.pub_port,
                    request_timeout=self.request_timeout,
                )
                
                if self._client.connect():
                    self._connected = True
                    
                    # Start heartbeat monitoring
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    
                    # Subscribe to fills
                    self._client.subscribe_fills(self._on_fill)
                    
                    logger.info("Successfully connected to MT5 bridge")
                    return True
                else:
                    logger.warning(f"Connection attempt {attempt} failed")
                    
            except Exception as e:
                logger.error(f"Connection attempt {attempt} error: {e}")
            
            if attempt < self.max_reconnect_attempts:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        raise MT5ConnectionError(
            f"Failed to connect to MT5 bridge after {self.max_reconnect_attempts} attempts"
        )
    
    async def disconnect(self) -> None:
        """Disconnect from the MT5 bridge."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        if self._client:
            self._client.disconnect()
        
        self._connected = False
        logger.info("Disconnected from MT5 bridge")
    
    async def _heartbeat_loop(self) -> None:
        """Background task to monitor connection health."""
        consecutive_failures = 0
        max_failures = 3
        
        while self._connected:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self._client.heartbeat
                )
                
                if result.get("status") == "ALIVE":
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    logger.warning(f"Heartbeat failed: {result}")
                    
            except Exception as e:
                consecutive_failures += 1
                logger.error(f"Heartbeat error: {e}")
            
            if consecutive_failures >= max_failures:
                self._connected = False
                raise HeartbeatTimeoutError(
                    f"Lost connection to MT5 after {max_failures} failed heartbeats"
                )
    
    def _get_or_create_symbol(self, ticker: str) -> Symbol:
        """Get or create a Symbol instance."""
        if ticker not in self._symbol_cache:
            self._symbol_cache[ticker] = Symbol(ticker=ticker)
        return self._symbol_cache[ticker]
    
    def _on_fill(self, fill: FillData) -> None:
        """Handle fill confirmation from MT5."""
        try:
            # Convert to Order
            order = Order(
                order_id=UUID(int=int(fill.order_id)) if fill.order_id.isdigit() else None,
                symbol=self._get_or_create_symbol(fill.symbol),
                side=OrderSide.BUY if fill.side == "BUY" else OrderSide.SELL,
                status=OrderStatus.FILLED,
                filled_price=Decimal(str(fill.filled_price)),
                filled_quantity=Decimal(str(fill.filled_quantity)),
                commission=Decimal(str(fill.commission)),
                filled_at=datetime.fromisoformat(fill.timestamp.replace("Z", "+00:00")),
            )
            
            for callback in self._fill_callbacks:
                try:
                    callback(order)
                except Exception as e:
                    logger.error(f"Fill callback error: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing fill: {e}")
    
    def _on_tick(self, tick_data: TickData) -> None:
        """Handle tick data from MT5."""
        try:
            tick = Tick(
                symbol=self._get_or_create_symbol(tick_data.symbol),
                timestamp=datetime.fromisoformat(tick_data.timestamp.replace("Z", "+00:00")),
                bid=Decimal(str(tick_data.bid)),
                ask=Decimal(str(tick_data.ask)),
                last=Decimal(str(tick_data.last)),
                volume=Decimal(str(tick_data.volume)),
            )
            
            for callback in self._tick_callbacks:
                try:
                    callback(tick)
                except Exception as e:
                    logger.error(f"Tick callback error: {e}")
                    
        except Exception as e:
            logger.error(f"Error processing tick: {e}")
    
    async def place_order(self, order: Order) -> Order:
        """
        Place an order via MT5.
        
        Args:
            order: Order object with symbol, side, quantity, etc.
            
        Returns:
            Updated Order with fill information
            
        Raises:
            OrderRejectedError: If order is rejected by MT5
            OrderTimeoutError: If order times out
        """
        if not self.is_connected:
            raise ConnectionLostError("Not connected to MT5")
        
        if order.symbol is None:
            raise ValueError("Order must have a symbol")
        
        # Update order status
        order.status = OrderStatus.SENT
        order.sent_at = datetime.now(timezone.utc)
        
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.place_order(
                    symbol=order.symbol.ticker,
                    side=order.side.value if order.side else "BUY",
                    quantity=float(order.quantity),
                    order_type=order.order_type.value,
                    stop_loss=float(order.stop_loss) if order.stop_loss else 0.0,
                    take_profit=float(order.take_profit) if order.take_profit else 0.0,
                )
            )
            
            if result.is_success:
                order.status = OrderStatus.FILLED
                order.filled_at = datetime.now(timezone.utc)
                order.filled_price = Decimal(str(result.filled_price))
                order.filled_quantity = Decimal(str(result.filled_volume))
                order.metadata["mt5_order_id"] = result.order_id
                order.metadata["mt5_deal_id"] = result.deal_id
                
                logger.info(f"Order filled: {order.order_id} @ {order.filled_price}")
            else:
                order.status = OrderStatus.REJECTED
                order.metadata["error"] = result.error
                
                raise OrderRejectedError(
                    f"Order rejected: {result.error}",
                    order_id=str(order.order_id),
                    reason=result.error,
                )
            
        except TimeoutError:
            order.status = OrderStatus.EXPIRED
            raise OrderTimeoutError(
                f"Order timed out: {order.order_id}",
                order_id=str(order.order_id),
            )
        
        return order
    
    async def close_position(self, position: Position) -> Position:
        """
        Close an open position.
        
        Args:
            position: Position to close
            
        Returns:
            Updated Position with realized PnL
        """
        if not self.is_connected:
            raise ConnectionLostError("Not connected to MT5")
        
        mt5_position_id = position.metadata.get("mt5_position_id", str(position.position_id))
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.close_position(position_id=mt5_position_id)
        )
        
        if result.is_success:
            position.realized_pnl = Decimal(str(result.realized_pnl))
            position.quantity = Decimal("0")
            position.side = PositionSide.FLAT
            position.metadata["close_price"] = result.close_price
            
            logger.info(f"Position closed: {position.position_id}, PnL: {position.realized_pnl}")
        else:
            raise OrderRejectedError(
                f"Failed to close position: {result.error}",
                order_id=str(position.position_id),
                reason=result.error,
            )
        
        return position
    
    async def modify_position(
        self,
        position: Position,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
    ) -> Position:
        """
        Modify position's stop loss and take profit.
        
        Args:
            position: Position to modify
            stop_loss: New stop loss (None to keep existing)
            take_profit: New take profit (None to keep existing)
            
        Returns:
            Updated Position
        """
        if not self.is_connected:
            raise ConnectionLostError("Not connected to MT5")
        
        mt5_position_id = position.metadata.get("mt5_position_id", str(position.position_id))
        
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.modify_position(
                position_id=mt5_position_id,
                stop_loss=float(stop_loss) if stop_loss else 0.0,
                take_profit=float(take_profit) if take_profit else 0.0,
            )
        )
        
        if result.get("status") == "MODIFIED":
            if stop_loss:
                position.stop_loss = stop_loss
            if take_profit:
                position.take_profit = take_profit
            position.updated_at = datetime.now(timezone.utc)
            
            logger.info(f"Position modified: {position.position_id}")
        else:
            raise OrderRejectedError(
                f"Failed to modify position: {result.get('error', 'Unknown error')}",
                order_id=str(position.position_id),
                reason=result.get("error", "Unknown"),
            )
        
        return position
    
    async def get_positions(self) -> List[Position]:
        """
        Get all open positions from MT5.
        
        Returns:
            List of Position objects
        """
        if not self.is_connected:
            raise ConnectionLostError("Not connected to MT5")
        
        zmq_positions = await asyncio.get_event_loop().run_in_executor(
            None, self._client.get_positions
        )
        
        positions = []
        for zp in zmq_positions:
            position = Position(
                symbol=self._get_or_create_symbol(zp.symbol),
                side=PositionSide.LONG if zp.side == "LONG" else PositionSide.SHORT,
                quantity=Decimal(str(zp.quantity)),
                entry_price=Decimal(str(zp.entry_price)),
                current_price=Decimal(str(zp.current_price)),
                unrealized_pnl=Decimal(str(zp.unrealized_pnl)),
                stop_loss=Decimal(str(zp.stop_loss)) if zp.stop_loss else None,
                take_profit=Decimal(str(zp.take_profit)) if zp.take_profit else None,
            )
            position.metadata["mt5_position_id"] = zp.position_id
            position.metadata["mt5_ticket"] = zp.ticket
            
            positions.append(position)
        
        return positions
    
    async def get_account_info(self) -> Dict:
        """
        Get account information from MT5.
        
        Returns:
            Dictionary with balance, equity, margin, etc.
        """
        if not self.is_connected:
            raise ConnectionLostError("Not connected to MT5")
        
        account = await asyncio.get_event_loop().run_in_executor(
            None, self._client.get_account_info
        )
        
        if account is None:
            return {}
        
        return {
            "balance": Decimal(str(account.balance)),
            "equity": Decimal(str(account.equity)),
            "margin": Decimal(str(account.margin)),
            "free_margin": Decimal(str(account.free_margin)),
            "margin_level": Decimal(str(account.margin_level)),
            "profit": Decimal(str(account.profit)),
            "currency": account.currency,
            "leverage": account.leverage,
            "trade_allowed": account.trade_allowed,
            "account_type": account.account_type,
        }
    
    def subscribe_ticks(self, callback: Callable[[Tick], None]) -> None:
        """
        Subscribe to tick data.
        
        Args:
            callback: Function to call with each tick
        """
        self._tick_callbacks.append(callback)
        
        if self._client:
            self._client.subscribe_ticks(self._on_tick)
    
    def subscribe_fills(self, callback: Callable[[Order], None]) -> None:
        """
        Subscribe to fill confirmations.
        
        Args:
            callback: Function to call with each filled order
        """
        self._fill_callbacks.append(callback)
    
    async def __aenter__(self) -> "MT5Connector":
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
