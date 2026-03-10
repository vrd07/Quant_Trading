"""
Fill Handler - Process order fills from MT5.

Converts filled orders into position objects.
Calculates realized P&L for closed positions.
"""

from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

from ..core.types import Order, Position
from ..core.constants import PositionSide, OrderSide


class FillHandler:
    """Process order fills and create positions."""
    
    def __init__(self):
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def process_fill(self, order: Order, fill_data: Dict) -> Optional[Position]:
        """
        Process order fill and create position.
        
        Args:
            order: Filled order
            fill_data: Fill data from MT5
        
        Returns:
            Position object created from fill
        """
        try:
            filled_price = Decimal(str(fill_data.get('filled_price', 0)))
            filled_quantity = Decimal(str(fill_data.get('filled_quantity', 0)))
            commission = Decimal(str(fill_data.get('commission', 0)))
            
            # Determine position side
            if order.side == OrderSide.BUY:
                position_side = PositionSide.LONG
            elif order.side == OrderSide.SELL:
                position_side = PositionSide.SHORT
            else:
                self.logger.error("Invalid order side", side=order.side)
                return None
            
            # Create position
            position = Position(
                position_id=uuid4(),
                symbol=order.symbol,
                side=position_side,
                quantity=filled_quantity,
                entry_price=filled_price,
                current_price=filled_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                opened_at=datetime.now(timezone.utc),
                metadata={
                    'order_id': str(order.order_id),
                    'strategy': order.metadata.get('strategy'),
                    'mt5_ticket': fill_data.get('ticket'),
                    'commission': float(commission)
                }
            )
            
            self.logger.info(
                "Position created from fill",
                position_id=str(position.position_id),
                order_id=str(order.order_id),
                symbol=order.symbol.ticker,
                side=position_side.value,
                quantity=float(filled_quantity),
                entry_price=float(filled_price)
            )
            
            return position
            
        except Exception as e:
            self.logger.error(
                "Error processing fill",
                order_id=str(order.order_id) if order else None,
                error=str(e),
                exc_info=True
            )
            return None
    
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
            Realized P&L in account currency
        """
        price_diff = exit_price - position.entry_price
        
        if position.side == PositionSide.LONG:
            pnl = price_diff * position.quantity * position.symbol.value_per_lot
        elif position.side == PositionSide.SHORT:
            pnl = -price_diff * position.quantity * position.symbol.value_per_lot
        else:
            pnl = Decimal("0")
        
        # Subtract commission if present
        commission = Decimal(str(position.metadata.get('commission', 0)))
        pnl -= commission
        
        self.logger.info(
            "Realized P&L calculated",
            position_id=str(position.position_id),
            entry_price=float(position.entry_price),
            exit_price=float(exit_price),
            pnl=float(pnl)
        )
        
        return pnl
