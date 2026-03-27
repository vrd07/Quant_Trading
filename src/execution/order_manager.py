"""
Order Manager - Track order lifecycle and state.

Maintains in-memory registry of all orders with O(1) lookups
by ID, status, and symbol via secondary indices.

Torvalds lens: the data structures drive the design. Secondary
indices eliminate all linear scans from the hot path.
"""

from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime, timezone
from collections import defaultdict

from ..core.types import Order
from ..core.constants import OrderStatus


class OrderManager:
    """
    Manage order tracking and state.

    Keeps registry of all orders for the session with O(1) lookups.
    """

    def __init__(self):
        self.orders: Dict[UUID, Order] = {}
        # Secondary indices for O(1) lookups
        self._by_status: Dict[OrderStatus, Dict[UUID, Order]] = defaultdict(dict)
        self._by_symbol: Dict[str, Dict[UUID, Order]] = defaultdict(dict)

        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)

    def add_order(self, order: Order) -> None:
        """
        Add order to registry. O(1).

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
        self._index_order(order)

        self.logger.debug(
            "Order added to registry",
            order_id=str(order.order_id),
            total_orders=len(self.orders)
        )

    def get_order(self, order_id: UUID) -> Optional[Order]:
        """Get order by ID. O(1)."""
        return self.orders.get(order_id)

    def update_order(self, order: Order) -> None:
        """Update order in registry. O(1) with index maintenance."""
        old_order = self.orders.get(order.order_id)
        if old_order is None:
            self.logger.warning(
                "Cannot update unknown order",
                order_id=str(order.order_id)
            )
            return

        # Remove from old indices before updating
        self._deindex_order(old_order)
        self.orders[order.order_id] = order
        self._index_order(order)

    def get_active_orders(self) -> List[Order]:
        """Get all non-terminal orders. O(k) where k = active orders."""
        return [
            order for order in self.orders.values()
            if order.is_active()
        ]

    def get_orders_by_status(self, status: OrderStatus) -> List[Order]:
        """Get orders with specific status. O(1) lookup + O(k) list."""
        bucket = self._by_status.get(status)
        if not bucket:
            return []
        return list(bucket.values())

    def get_orders_by_symbol(self, symbol: str) -> List[Order]:
        """Get orders for specific symbol. O(1) lookup + O(k) list."""
        bucket = self._by_symbol.get(symbol)
        if not bucket:
            return []
        return list(bucket.values())

    def get_order_count(self) -> int:
        """Get total number of orders. O(1)."""
        return len(self.orders)

    def get_statistics(self) -> Dict:
        """
        Get order statistics. O(1) per status via index sizes.

        Returns:
            Dict with order counts by status
        """
        active_count = sum(
            1 for order in self.orders.values() if order.is_active()
        ) if self.orders else 0

        by_status = {
            status.value: len(bucket)
            for status, bucket in self._by_status.items()
            if bucket
        }

        return {
            'total': len(self.orders),
            'active': active_count,
            'by_status': by_status,
        }

    # --- Index maintenance (Carmack: make mutations visible) ----------------

    def _index_order(self, order: Order) -> None:
        """Add order to secondary indices. O(1)."""
        self._by_status[order.status][order.order_id] = order
        ticker = order.symbol.ticker if order.symbol else "__NONE__"
        self._by_symbol[ticker][order.order_id] = order

    def _deindex_order(self, order: Order) -> None:
        """Remove order from secondary indices. O(1)."""
        status_bucket = self._by_status.get(order.status)
        if status_bucket:
            status_bucket.pop(order.order_id, None)
            if not status_bucket:
                del self._by_status[order.status]

        ticker = order.symbol.ticker if order.symbol else "__NONE__"
        symbol_bucket = self._by_symbol.get(ticker)
        if symbol_bucket:
            symbol_bucket.pop(order.order_id, None)
            if not symbol_bucket:
                del self._by_symbol[ticker]
