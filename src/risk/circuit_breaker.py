"""Circuit breaker for trading pauses after consecutive losses."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Tuple, Optional


class CircuitBreaker:
    """
    Circuit breaker that pauses trading after consecutive losses.
    
    Unlike the kill switch, this auto-resets after a cooldown period.
    """
    
    def __init__(
        self,
        max_consecutive_losses: int = 3,
        cooldown_minutes: int = 30
    ):
        """
        Initialize circuit breaker.
        
        Args:
            max_consecutive_losses: Number of consecutive losses before tripping
            cooldown_minutes: Minutes to wait before allowing trading again
        """
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_minutes = cooldown_minutes
        
        # State
        self.consecutive_losses = 0
        self._tripped_at: Optional[datetime] = None
    
    def record_trade(self, pnl: Decimal) -> None:
        """
        Record trade result.
        
        Args:
            pnl: Realized P&L from the trade
        """
        if pnl < 0:
            self.consecutive_losses += 1
            
            # Check if we should trip
            if self.consecutive_losses >= self.max_consecutive_losses:
                self._trip()
        else:
            # Win resets counter
            self.consecutive_losses = 0
    
    def _trip(self) -> None:
        """Trip the circuit breaker."""
        self._tripped_at = datetime.now(timezone.utc)
    
    def is_trading_allowed(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed.
        
        Returns:
            (allowed, reason) tuple
        """
        if self._tripped_at is None:
            return True, "OK"
        
        # Check if cooldown has elapsed
        cooldown_end = self._tripped_at + timedelta(minutes=self.cooldown_minutes)
        now = datetime.now(timezone.utc)
        
        if now >= cooldown_end:
            # Cooldown complete - reset
            self._tripped_at = None
            self.consecutive_losses = 0
            return True, "OK"
        
        # Still in cooldown
        remaining = cooldown_end - now
        minutes_left = int(remaining.total_seconds() / 60)
        
        return False, f"Circuit breaker active: {minutes_left} minutes remaining"
    
    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._tripped_at = None
        self.consecutive_losses = 0
    
    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        is_tripped = self._tripped_at is not None
        
        cooldown_remaining = None
        if is_tripped:
            cooldown_end = self._tripped_at + timedelta(minutes=self.cooldown_minutes)
            remaining = cooldown_end - datetime.now(timezone.utc)
            cooldown_remaining = max(0, int(remaining.total_seconds()))
        
        return {
            'tripped': is_tripped,
            'consecutive_losses': self.consecutive_losses,
            'max_consecutive_losses': self.max_consecutive_losses,
            'cooldown_remaining_seconds': cooldown_remaining
        }
