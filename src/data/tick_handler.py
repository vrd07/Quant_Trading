"""
Tick Handler - Manages real-time tick data.
"""

from collections import deque
from typing import Optional, Deque
from datetime import datetime

from ..core.types import Tick


class TickHandler:
    """
    Manages tick data with bounded memory.
    
    Uses deque for O(1) append and automatic size limiting.
    """
    
    def __init__(self, buffer_size: int = 10000):
        self.buffer_size = buffer_size
        self.ticks: Deque[Tick] = deque(maxlen=buffer_size)
        self.ticks_by_symbol: dict[str, Deque[Tick]] = {}
    
    def add_tick(self, tick: Tick) -> None:
        """Add tick to buffer."""
        self.ticks.append(tick)
        
        # Also maintain per-symbol buffer
        symbol = tick.symbol.ticker
        if symbol not in self.ticks_by_symbol:
            self.ticks_by_symbol[symbol] = deque(maxlen=self.buffer_size)
        
        self.ticks_by_symbol[symbol].append(tick)
    
    def get_latest_tick(self, symbol: str) -> Optional[Tick]:
        """Get most recent tick for symbol."""
        if symbol in self.ticks_by_symbol and len(self.ticks_by_symbol[symbol]) > 0:
            return self.ticks_by_symbol[symbol][-1]
        return None
    
    def get_recent_ticks(self, symbol: str, count: int = 100) -> list[Tick]:
        """Get N most recent ticks for symbol."""
        if symbol not in self.ticks_by_symbol:
            return []
        
        ticks = list(self.ticks_by_symbol[symbol])
        return ticks[-count:]
