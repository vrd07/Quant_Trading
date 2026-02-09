"""
Data Validator - Validates ticks and bars.

Detects:
- Stale ticks
- Invalid OHLC
- Price spikes
- Missing data
"""

from typing import Optional
from datetime import datetime, timezone
from decimal import Decimal
import statistics

from ..core.types import Tick, Bar
from ..core.constants import MAX_TICK_AGE_SECONDS, SPIKE_THRESHOLD_STD
from ..core.exceptions import DataValidationError


class DataValidator:
    """Validates market data quality."""
    
    def __init__(self):
        self.price_history: dict[str, list[Decimal]] = {}
        self.history_size = 100
    
    def validate_tick(
        self,
        tick: Tick,
        last_tick_time: Optional[datetime] = None
    ) -> bool:
        """
        Validate tick data.
        
        Returns:
            True if valid, False if should be discarded
        """
        # Check for stale tick
        if last_tick_time:
            age = (tick.timestamp - last_tick_time).total_seconds()
            if age < 0:
                # Tick from the past
                return False
        
        # Check tick freshness
        now = datetime.now(timezone.utc)
        tick_age = (now - tick.timestamp).total_seconds()
        
        if tick_age > MAX_TICK_AGE_SECONDS:
            return False  # Too old
        
        # Validate prices
        if tick.bid <= 0 or tick.ask <= 0:
            return False
        
        if tick.ask < tick.bid:
            return False  # Invalid spread
        
        # Check for price spike
        if self._is_spike(tick):
            return False
        
        # Update price history
        self._update_price_history(tick)
        
        return True
    
    def validate_bar(self, bar: Bar) -> bool:
        """
        Validate bar OHLC integrity.
        
        Bar.__post_init__ already does basic validation,
        but we add extra checks here.
        """
        try:
            # High must be highest price
            if bar.high < max(bar.open, bar.close):
                return False
            
            # Low must be lowest price
            if bar.low > min(bar.open, bar.close):
                return False
            
            # All prices must be positive
            if any(p <= 0 for p in [bar.open, bar.high, bar.low, bar.close]):
                return False
            
            # Volume should be non-negative
            if bar.volume < 0:
                return False
            
            return True
            
        except Exception:
            return False
    
    def _is_spike(self, tick: Tick) -> bool:
        """
        Detect price spikes using standard deviation.
        
        If price moves > N standard deviations, it's likely bad data.
        """
        symbol = tick.symbol.ticker
        
        if symbol not in self.price_history or len(self.price_history[symbol]) < 20:
            return False  # Not enough history
        
        prices = [float(p) for p in self.price_history[symbol]]
        mean = statistics.mean(prices)
        std = statistics.stdev(prices)
        
        if std == 0:
            return False
        
        # Check how many std devs away
        z_score = abs((float(tick.mid) - mean) / std)
        
        return z_score > SPIKE_THRESHOLD_STD
    
    def _update_price_history(self, tick: Tick) -> None:
        """Update price history for spike detection."""
        symbol = tick.symbol.ticker
        
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        
        self.price_history[symbol].append(tick.mid)
        
        # Keep only recent history
        if len(self.price_history[symbol]) > self.history_size:
            self.price_history[symbol] = self.price_history[symbol][-self.history_size:]
