"""
Order Manager - Track order lifecycle and state.

Maintains in-memory registry of all orders.
Provides fast lookups and status queries.
"""

from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime, timezone

from ..core.types import Order
from ..core.constants import OrderStatus


class OrderManager:
    """
    Manage order tracking and state.
    
    Keeps registry of all orders for the session.
    """
    
    def __init__(self):
        self.orders: Dict[UUID, Order] = {}
        # O(1) prefix lookup: first 8 chars of UUID → order_id
        self._prefix_index: Dict[str, UUID] = {}

        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def add_order(self, order: Order) -> None:
        """
        Add order to registry.
        
        Args:
            order: Order to track
        """
        if order.order_id in self.orders:
            self.logger.warning(
                "Order already exists in registry",
                order_id=str(order.order_id)
            )
            return
        
        self.orders[order.order_id] = order
        self._prefix_index[str(order.order_id)[:8]] = order.order_id

        self.logger.debug(
            "Order added to registry",
            order_id=str(order.order_id),
            total_orders=len(self.orders)
        )
    
    def get_order(self, order_id: UUID) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)
    
    def update_order(self, order: Order) -> None:
        """Update order in registry."""
        if order.order_id not in self.orders:
            self.logger.warning(
                "Cannot update unknown order",
                order_id=str(order.order_id)
            )
            return
        
        self.orders[order.order_id] = order
    
    def get_order_by_prefix(self, prefix: str) -> Optional[Order]:
        """O(1) lookup by first 8 chars of UUID (fallback to linear scan for other lengths)."""
        order_id = self._prefix_index.get(prefix)
        if order_id:
            return self.orders.get(order_id)
        # Fallback for non-8-char prefixes
        for order in self.orders.values():
            if str(order.order_id).startswith(prefix):
                return order
        return None

    def get_active_orders(self) -> List[Order]:
        """Get all non-terminal orders."""
        return [
            order for order in self.orders.values()
            if order.is_active()
        ]
    
    def get_orders_by_status(self, status: OrderStatus) -> List[Order]:
        """Get orders with specific status."""
        return [
            order for order in self.orders.values()
            if order.status == status
        ]
    
    def get_orders_by_symbol(self, symbol: str) -> List[Order]:
        """Get orders for specific symbol."""
        return [
            order for order in self.orders.values()
            if order.symbol and order.symbol.ticker == symbol
        ]
    
    def get_order_count(self) -> int:
        """Get total number of orders."""
        return len(self.orders)
    
    def get_statistics(self) -> Dict:
        """
        Get order statistics — single pass, O(n).

        Returns:
            Dict with order counts by status
        """
        by_status: Dict[str, int] = {}
        active_count = 0
        for order in self.orders.values():
            key = order.status.value
            by_status[key] = by_status.get(key, 0) + 1
            if order.is_active():
                active_count += 1

        return {
            'total': len(self.orders),
            'active': active_count,
            'by_status': by_status,
        }
