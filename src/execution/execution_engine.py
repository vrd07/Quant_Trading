"""
Execution Engine - Order execution and lifecycle management.

Responsibilities:
1. Convert signals to orders
2. Validate orders via risk engine
3. Send orders to MT5
4. Track order status
5. Handle fills
6. Handle rejections/timeouts
7. Manage order modifications

Order Lifecycle:
PENDING → SENT → ACCEPTED → FILLED
       ↓        ↓         ↓
   REJECTED  TIMEOUT  CANCELLED

Critical Design:
- Never send duplicate orders (idempotency)
- Always have timeout handling
- Log every state transition
- Retry transient failures (max 3 times)
- Fail safely (reject if uncertain)
"""

from typing import Dict, Optional, List
from uuid import UUID
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import time

from ..connectors.mt5_connector import MT5Connector
from ..core.types import Order, Signal, Position, Symbol
from ..core.constants import OrderStatus, OrderSide, OrderType
from ..core.exceptions import (
    OrderRejectedError,
    OrderTimeoutError,
    OrderError
)
from ..risk.risk_engine import RiskEngine

from .order_manager import OrderManager
from .fill_handler import FillHandler


class ExecutionEngine:
    """
    Central order execution engine.
    
    Manages the complete order lifecycle from signal to fill.
    """
    
    def __init__(
        self,
        connector: MT5Connector,
        risk_engine: RiskEngine,
        order_timeout_seconds: int = 30
    ):
        """
        Initialize execution engine.
        
        Args:
            connector: MT5 connector
            risk_engine: Risk engine for validation
            order_timeout_seconds: Max seconds to wait for order response
        """
        self.connector = connector
        self.risk_engine = risk_engine
        self.order_timeout_seconds = order_timeout_seconds
        
        # Sub-components
        self.order_manager = OrderManager()
        self.fill_handler = FillHandler()
        
        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def submit_signal(
        self,
        signal: Signal,
        account_balance: Decimal,
        account_equity: Decimal,
        current_positions: Dict[str, Position],
        daily_pnl: Decimal
    ) -> Optional[Order]:
        """
        Submit a trading signal for execution.
        
        This is the main entry point from strategies.
        
        Process:
        1. Check for duplicate signal (idempotency)
        2. Convert signal to order
        3. Calculate position size
        4. Validate via risk engine
        5. Send to MT5
        6. Track order
        
        Args:
            signal: Trading signal from strategy
            account_balance: Current account balance
            account_equity: Current account equity
            current_positions: Open positions
            daily_pnl: Daily P&L
        
        Returns:
            Order object if submitted, None if rejected
        """
        try:
            signal_id = str(signal.signal_id)
            
            # 1. Calculate position size
            if not signal.entry_price or not signal.stop_loss:
                self.logger.error(
                    "Signal missing entry price or stop loss",
                    signal_id=signal_id,
                    strategy=signal.strategy_name
                )
                return None
            
            position_size = self.risk_engine.calculate_position_size(
                symbol=signal.symbol,
                account_balance=account_balance,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                side=signal.side
            )
            
            if position_size <= 0:
                self.logger.warning(
                    "Position size calculated as zero",
                    signal_id=signal_id
                )
                return None
            
            # 2. Create order from signal
            order = Order(
                symbol=signal.symbol,
                side=signal.side,
                order_type=OrderType.MARKET,  # Always use market orders for now
                quantity=position_size,
                price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                status=OrderStatus.PENDING,
                metadata={
                    'signal_id': signal_id,
                    'strategy': signal.strategy_name,
                    'regime': signal.regime.value,
                    'signal_strength': signal.strength
                }
            )
            
            self.logger.info(
                "Order created from signal",
                order_id=str(order.order_id),
                strategy=signal.strategy_name,
                symbol=signal.symbol.ticker if signal.symbol else None,
                side=signal.side.value if signal.side else None,
                quantity=float(position_size)
            )
            
            # 3. Validate with risk engine
            is_valid, reason = self.risk_engine.validate_order(
                order=order,
                account_balance=account_balance,
                account_equity=account_equity,
                current_positions=current_positions,
                daily_pnl=daily_pnl
            )
            
            if not is_valid:
                self.logger.warning(
                    "Order rejected by risk engine",
                    order_id=str(order.order_id),
                    reason=reason
                )
                order.status = OrderStatus.REJECTED
                order.metadata['rejection_reason'] = reason
                return order
            
            # 4. Submit to MT5
            submitted_order = self._submit_order(order)
            
            return submitted_order
            

        except Exception as e:
            self.logger.error(
                "Error submitting signal",
                signal_id=str(signal.signal_id) if signal else None,
                error=str(e),
                exc_info=True
            )
            return None
    
    def _submit_order(self, order: Order, retry_count: int = 0) -> Order:
        """
        Submit order to MT5 with retry logic.
        
        Args:
            order: Order to submit
            retry_count: Current retry attempt
        
        Returns:
            Order with updated status
        
        Raises:
            OrderRejectedError: If MT5 rejects order
            OrderTimeoutError: If no response after retries
        """
        max_retries = 3
        
        try:
            # Track order (idempotency - this will fail if duplicate)
            if retry_count == 0:
                self.order_manager.add_order(order)
            
            # Update status to SENT
            order.status = OrderStatus.SENT
            order.sent_at = datetime.now(timezone.utc)
            self.order_manager.update_order(order)
            
            self.logger.info(
                "Sending order to MT5",
                order_id=str(order.order_id),
                symbol=order.symbol.ticker if order.symbol else None,
                side=order.side.value if order.side else None,
                quantity=float(order.quantity),
                attempt=retry_count + 1
            )
            
            # Place order via connector
            result = self.connector.place_order(
                symbol=order.symbol.ticker if order.symbol else "",
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                price=order.price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                comment=f"Order-{str(order.order_id)[:8]}"
            )
            
            # Update order with MT5 response
            order.status = OrderStatus.ACCEPTED
            order.metadata['mt5_order_id'] = result.metadata.get('mt5_order_id')
            order.metadata['mt5_ticket'] = result.metadata.get('mt5_ticket')
            self.order_manager.update_order(order)
            
            self.logger.info(
                "Order accepted by MT5",
                order_id=str(order.order_id),
                mt5_ticket=result.metadata.get('mt5_ticket')
            )
            
            return order
            
        except OrderRejectedError as e:
            # MT5 rejected - don't retry
            order.status = OrderStatus.REJECTED
            order.metadata['rejection_reason'] = str(e)
            self.order_manager.update_order(order)
            
            self.logger.error(
                "Order rejected by MT5",
                order_id=str(order.order_id),
                reason=str(e)
            )
            
            raise
            
        except (OrderTimeoutError, ConnectionError) as e:
            # Transient error - retry with exponential backoff
            if retry_count < max_retries:
                backoff_time = 2 ** retry_count  # 1, 2, 4 seconds
                
                self.logger.warning(
                    "Order submission failed, retrying",
                    order_id=str(order.order_id),
                    attempt=retry_count + 1,
                    max_retries=max_retries,
                    backoff_seconds=backoff_time,
                    error=str(e)
                )
                
                time.sleep(backoff_time)
                return self._submit_order(order, retry_count + 1)
            else:
                # Max retries exceeded - fail safe
                order.status = OrderStatus.REJECTED
                order.metadata['rejection_reason'] = f"Timeout after {max_retries} retries: {str(e)}"
                self.order_manager.update_order(order)
                
                self.logger.error(
                    "Order submission failed after retries",
                    order_id=str(order.order_id),
                    retries=max_retries,
                    error=str(e)
                )
                
                raise OrderTimeoutError(
                    f"Order timeout after {max_retries} retries",
                    order_id=str(order.order_id)
                )
                

            
        except Exception as e:
            # Unexpected error - fail safe (reject order)
            order.status = OrderStatus.REJECTED
            order.metadata['rejection_reason'] = f"Unexpected error: {str(e)}"
            self.order_manager.update_order(order)
            
            self.logger.error(
                "Unexpected error submitting order",
                order_id=str(order.order_id),
                error=str(e),
                exc_info=True
            )
            
            raise OrderError(
                f"Order submission failed: {str(e)}",
                order_id=str(order.order_id)
            )
    
    def handle_fill(self, fill_data: Dict) -> Optional[Position]:
        """
        Handle order fill notification from MT5.
        
        Args:
            fill_data: Fill data from MT5 (from PUSH socket)
                Expected fields:
                - order_id: str (our order ID from comment)
                - filled_price: float
                - filled_quantity: float
                - mt5_ticket: int
        
        Returns:
            Position object created from fill
        """
        try:
            order_id_str = fill_data.get('order_id')
            
            if not order_id_str:
                # Try to extract from comment
                comment = fill_data.get('comment', '')
                if comment.startswith('Order-'):
                    order_id_str = comment.replace('Order-', '')
                else:
                    self.logger.error(
                        "Fill data missing order_id",
                        fill_data=fill_data
                    )
                    return None
            
            # Find order
            try:
                order_id = UUID(order_id_str)
            except ValueError:
                # Partial UUID from comment
                order = self._find_order_by_prefix(order_id_str)
                if not order:
                    self.logger.warning(
                        "Received fill for unknown order",
                        order_id=order_id_str
                    )
                    return None
            else:
                order = self.order_manager.get_order(order_id)
            
            if not order:
                self.logger.warning(
                    "Received fill for unknown order",
                    order_id=order_id_str
                )
                return None
            
            # Process fill
            position = self.fill_handler.process_fill(order, fill_data)
            
            if not position:
                self.logger.error(
                    "Failed to create position from fill",
                    order_id=str(order.order_id)
                )
                return None
            
            # Update order status
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.now(timezone.utc)
            order.filled_price = Decimal(str(fill_data.get('filled_price', 0)))
            order.filled_quantity = Decimal(str(fill_data.get('filled_quantity', 0)))
            
            # Calculate slippage
            if order.price:
                order.slippage = order.calculate_slippage(order.price)
                
                # Log if slippage is significant (> 5 pips)
                if order.symbol and abs(order.slippage) > order.symbol.pip_value * 5:
                    self.logger.warning(
                        "Significant slippage detected",
                        order_id=str(order.order_id),
                        expected_price=float(order.price),
                        filled_price=float(order.filled_price),
                        slippage=float(order.slippage),
                        slippage_pips=float(order.slippage / order.symbol.pip_value)
                    )
            
            self.order_manager.update_order(order)
            
            self.logger.info(
                "Order filled",
                order_id=str(order.order_id),
                filled_price=float(order.filled_price),
                filled_quantity=float(order.filled_quantity),
                position_id=str(position.position_id)
            )
            
            return position
            
        except Exception as e:
            self.logger.error(
                "Error handling fill",
                fill_data=fill_data,
                error=str(e),
                exc_info=True
            )
            return None
    
    def _find_order_by_prefix(self, prefix: str) -> Optional[Order]:
        """
        Find order by ID prefix (from truncated comment).
        
        Args:
            prefix: First 8 chars of order UUID
        
        Returns:
            Matching order or None
        """
        for order in self.order_manager.get_active_orders():
            if str(order.order_id).startswith(prefix):
                return order
        return None
    
    def cancel_order(self, order_id: UUID) -> bool:
        """
        Cancel a pending order.
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            True if cancelled successfully
        """
        order = self.order_manager.get_order(order_id)
        
        if not order:
            self.logger.warning(
                "Cannot cancel unknown order",
                order_id=str(order_id)
            )
            return False
        
        if order.is_terminal():
            self.logger.warning(
                "Cannot cancel terminal order",
                order_id=str(order_id),
                status=order.status.value
            )
            return False
        
        try:
            # TODO: Cancel via MT5 connector when API supports it
            # For now, just update status locally
            
            old_status = order.status
            order.status = OrderStatus.CANCELLED
            order.metadata['cancelled_at'] = datetime.now(timezone.utc).isoformat()
            order.metadata['cancelled_from'] = old_status.value
            self.order_manager.update_order(order)
            
            self.logger.info(
                "Order cancelled",
                order_id=str(order_id),
                previous_status=old_status.value
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error cancelling order",
                order_id=str(order_id),
                error=str(e),
                exc_info=True
            )
            return False
    
    def check_order_timeouts(self) -> List[Order]:
        """
        Check for orders that have timed out.
        
        Should be called periodically (e.g., every second).
        
        Returns:
            List of orders that were timed out
        """
        now = datetime.now(timezone.utc)
        timeout_delta = timedelta(seconds=self.order_timeout_seconds)
        timed_out = []
        
        for order in self.order_manager.get_active_orders():
            if order.status == OrderStatus.SENT and order.sent_at:
                if now - order.sent_at > timeout_delta:
                    self.logger.warning(
                        "Order timed out",
                        order_id=str(order.order_id),
                        sent_at=order.sent_at.isoformat(),
                        timeout_seconds=self.order_timeout_seconds
                    )
                    
                    order.status = OrderStatus.REJECTED
                    order.metadata['rejection_reason'] = 'Order timeout'
                    order.metadata['timed_out_at'] = now.isoformat()
                    self.order_manager.update_order(order)
                    
                    timed_out.append(order)
        
        return timed_out
    
    def get_active_orders(self) -> List[Order]:
        """Get all active (non-terminal) orders."""
        return self.order_manager.get_active_orders()
    
    def get_order(self, order_id: UUID) -> Optional[Order]:
        """Get order by ID."""
        return self.order_manager.get_order(order_id)
    
    def get_order_statistics(self) -> Dict:
        """
        Get order execution statistics.
        
        Returns:
            Dict with order counts and metrics
        """
        stats = self.order_manager.get_statistics()
        
        # Add fill quality metrics
        filled_orders = self.order_manager.get_orders_by_status(OrderStatus.FILLED)
        
        if filled_orders:
            total_slippage = sum(
                order.slippage for order in filled_orders
                if order.slippage is not None
            )
            avg_slippage = total_slippage / len(filled_orders)
            
            stats['avg_slippage'] = float(avg_slippage)
            stats['total_slippage'] = float(total_slippage)
        else:
            stats['avg_slippage'] = 0
            stats['total_slippage'] = 0
        
        return stats
    

