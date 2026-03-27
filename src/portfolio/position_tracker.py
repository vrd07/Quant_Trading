"""
Position Tracker - Maintain registry of open positions.

Fast in-memory storage for active positions with O(1) lookups.

Torvalds lens: secondary index by symbol eliminates linear scans.
The data structure choice determines performance, not clever code.
"""

from typing import Dict, List, Optional
from uuid import UUID
from collections import defaultdict

from ..core.types import Position


class PositionTracker:
    """Track open positions in memory with O(1) lookups by ID and symbol."""

    def __init__(self):
        self.positions: Dict[UUID, Position] = {}
        # Secondary index: symbol -> {position_id -> Position}
        self._by_symbol: Dict[str, Dict[UUID, Position]] = defaultdict(dict)

        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)

    def add_position(self, position: Position) -> None:
        """Add position to tracker. O(1)."""
        if position.position_id in self.positions:
            self.logger.warning(
                "Position already exists",
                position_id=str(position.position_id)
            )
            return

        self.positions[position.position_id] = position

        # Maintain symbol index
        ticker = position.symbol.ticker if position.symbol else "__NONE__"
        self._by_symbol[ticker][position.position_id] = position

    def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position by ID. O(1)."""
        return self.positions.get(position_id)

    def remove_position(self, position_id: UUID) -> None:
        """Remove position from tracker. O(1)."""
        position = self.positions.pop(position_id, None)
        if position:
            ticker = position.symbol.ticker if position.symbol else "__NONE__"
            self._by_symbol[ticker].pop(position_id, None)
            # Clean up empty symbol buckets
            if not self._by_symbol[ticker]:
                del self._by_symbol[ticker]

    def get_all_positions(self) -> List[Position]:
        """Get all positions. O(n) but unavoidable for list creation."""
        return list(self.positions.values())

    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get positions for specific symbol. O(1) lookup + O(k) list creation."""
        bucket = self._by_symbol.get(symbol)
        if not bucket:
            return []
        return list(bucket.values())

    def get_position_count(self) -> int:
        """Get number of open positions. O(1)."""
        return len(self.positions)

    def get_symbol_count(self, symbol: str) -> int:
        """Get number of positions for a specific symbol. O(1)."""
        return len(self._by_symbol.get(symbol, {}))
