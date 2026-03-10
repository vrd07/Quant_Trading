"""Drawdown tracking for risk management."""

from decimal import Decimal


class DrawdownTracker:
    """
    Tracks and calculates drawdown from equity high water mark.
    """
    
    def __init__(self, max_drawdown_pct: Decimal = Decimal("0.10")):
        """
        Initialize drawdown tracker.
        
        Args:
            max_drawdown_pct: Maximum allowed drawdown percentage
        """
        self.max_drawdown_pct = max_drawdown_pct
    
    def calculate_drawdown(
        self,
        equity_high_water_mark: Decimal,
        current_equity: Decimal
    ) -> Decimal:
        """
        Calculate current drawdown as percentage.
        
        Args:
            equity_high_water_mark: Peak equity value
            current_equity: Current equity value
        
        Returns:
            Drawdown as decimal (0.10 = 10% drawdown)
        """
        if equity_high_water_mark <= 0:
            return Decimal("0")
        
        if current_equity >= equity_high_water_mark:
            return Decimal("0")
        
        drawdown = (equity_high_water_mark - current_equity) / equity_high_water_mark
        return drawdown
    
    def is_limit_exceeded(
        self,
        equity_high_water_mark: Decimal,
        current_equity: Decimal
    ) -> bool:
        """
        Check if drawdown limit is exceeded.
        
        Args:
            equity_high_water_mark: Peak equity value
            current_equity: Current equity value
        
        Returns:
            True if drawdown exceeds limit
        """
        current_drawdown = self.calculate_drawdown(
            equity_high_water_mark,
            current_equity
        )
        return current_drawdown >= self.max_drawdown_pct
