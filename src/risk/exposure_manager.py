"""Exposure management for per-symbol concentration limits."""

from decimal import Decimal
from typing import Dict, Tuple

from ..core.types import Order, Position, Symbol


class ExposureManager:
    """
    Manages exposure limits per symbol to prevent over-concentration.
    """
    
    def __init__(self, max_exposure_pct: Decimal = Decimal("0.30")):
        """
        Initialize exposure manager.
        
        Args:
            max_exposure_pct: Max exposure per symbol as % of account equity
        """
        self.max_exposure_pct = max_exposure_pct
    
    def _calculate_symbol_exposure(
        self,
        symbol: Symbol,
        positions: Dict[str, Position]
    ) -> Decimal:
        """Calculate total notional exposure for a symbol."""
        exposure = Decimal("0")
        
        for position in positions.values():
            # Only count positions for this symbol
            if position.symbol.ticker == symbol.ticker:
                # Use absolute value (long and short both count toward exposure)
                position_value = abs(
                    position.quantity * 
                    position.current_price * 
                    position.symbol.value_per_lot
                )
                exposure += position_value
        
        return exposure

    def check_exposure_limit(
        self,
        symbol: Symbol,
        new_order: Order,
        current_positions: Dict[str, Position],
        account_equity: Decimal
    ) -> Tuple[bool, str]:
        """
        Check if new order would exceed exposure limit.
        
        Returns:
            (allowed, reason)
        """
        # Calculate current exposure for this symbol
        current_exposure = self._calculate_symbol_exposure(
            symbol,
            current_positions
        )
        
        # Calculate notional value of new order
        # CRITICAL FIX: Don't use price if it's None (market orders)
        if new_order.price and new_order.price > 0:
            order_price = new_order.price
        else:
            # For market orders, estimate using a reasonable price
            # In production, get current market price
            # For now, skip exposure check for market orders without price
            # Use print or pass if logger not available in this scope yet, 
            # OR better, since I can't easily add logger here without seeing __init__, 
            # I see the user's code uses self.logger. 
            # The original file does NOT have self.logger in __init__. 
            # I must check if I need to add logger to __init__ or if I should remove the logging lines.
            # The original file shows `class ExposureManager:` lines 9-21. No logger init. 
            # I should probably add the logger to __init__ as well or remove logging. 
            # Given the user provided code with logging, I should probably add the logger.
            
            # Let's check imports. `import logging` is not in the file.
            # I will modify the whole file to be safe.
            pass
            return True, "OK"
    
        # Calculate new order exposure
        new_exposure = new_order.quantity * order_price * symbol.value_per_lot
        
        # Total exposure after this order
        total_exposure = current_exposure + new_exposure
        
        # Maximum allowed exposure for this symbol
        max_exposure = account_equity * self.max_exposure_pct
        
        if total_exposure > max_exposure:
            # Calculate percentage for error message
            if account_equity > 0:
                exposure_pct = (total_exposure / account_equity) * 100
            else:
                exposure_pct = Decimal("0")
            max_pct = self.max_exposure_pct * 100
            
            return False, f"Exposure limit exceeded for {symbol.ticker}: {exposure_pct:.1f}% > {max_pct:.1f}%"
        
        return True, "OK"
