"""
Position Tracker - Maintain registry of open positions.

Fast in-memory storage for active positions.
"""

from typing import Dict, List, Optional
from uuid import UUID

from ..core.types import Position


class PositionTracker:
    """Track open positions in memory."""
    
    def __init__(self):
        self.positions: Dict[UUID, Position] = {}
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def add_position(self, position: Position) -> None:
        """Add position to tracker."""
        if position.position_id in self.positions:
            self.logger.warning(
                "Position already exists",
                position_id=str(position.position_id)
            )
            return
        
        self.positions[position.position_id] = position
    
    def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position by ID."""
        return self.positions.get(position_id)
    
    def remove_position(self, position_id: UUID) -> None:
        """Remove position from tracker."""
        if position_id in self.positions:
            del self.positions[position_id]
    
    def get_all_positions(self) -> List[Position]:
        """Get all positions."""
        return list(self.positions.values())
    
    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get positions for specific symbol."""
        return [
            pos for pos in self.positions.values()
            if pos.symbol and pos.symbol.ticker == symbol
        ]
    
    def get_position_count(self) -> int:
        """Get number of open positions."""
        return len(self.positions)
