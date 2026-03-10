"""
P&L Calculator - Calculate position P&L.

Handles both unrealized and realized P&L calculations.
"""

from decimal import Decimal

from ..core.types import Position
from ..core.constants import PositionSide


class PnLCalculator:
    """Calculate position P&L."""
    
    def __init__(self):
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def calculate_unrealized_pnl(
        self,
        position: Position,
        current_price: Decimal
    ) -> Decimal:
        """
        Calculate unrealized P&L for open position.
        
        Formula:
        - Long: (Current - Entry) × Quantity × Multiplier
        - Short: (Entry - Current) × Quantity × Multiplier
        
        Args:
            position: Position to calculate
            current_price: Current market price
        
        Returns:
            Unrealized P&L
        """
        price_diff = current_price - position.entry_price
        
        if position.side == PositionSide.LONG:
            pnl = price_diff * position.quantity * position.symbol.value_per_lot
        elif position.side == PositionSide.SHORT:
            pnl = -price_diff * position.quantity * position.symbol.value_per_lot
        else:
            pnl = Decimal("0")
        
        return pnl
    
    def calculate_realized_pnl(
        self,
        position: Position,
        exit_price: Decimal
    ) -> Decimal:
        """
        Calculate realized P&L for closed position.
        
        Args:
            position: Position being closed
            exit_price: Exit price
        
        Returns:
            Realized P&L (includes commission)
        """
        price_diff = exit_price - position.entry_price
        
        if position.side == PositionSide.LONG:
            pnl = price_diff * position.quantity * position.symbol.value_per_lot
        elif position.side == PositionSide.SHORT:
            pnl = -price_diff * position.quantity * position.symbol.value_per_lot
        else:
            pnl = Decimal("0")
        
        # Subtract commission
        commission = Decimal(str(position.metadata.get('commission', 0)))
        pnl -= commission
        
        self.logger.info(
            "Realized P&L calculated",
            position_id=str(position.position_id),
            entry=float(position.entry_price),
            exit=float(exit_price),
            pnl=float(pnl),
            commission=float(commission)
        )
        
        return pnl
