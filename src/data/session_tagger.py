"""
Session Tagger - Identifies trading sessions.
"""

from datetime import datetime
from ..core.constants import TradingSession


class SessionTagger:
    """
    Tags timestamps with trading session.
    
    Based on UTC time:
    - Asian: 01:00-09:00 UTC
    - London: 08:00-16:00 UTC  
    - New York: 13:00-21:00 UTC
    - Overlaps: London+NY overlap
    """
    
    def get_session(self, timestamp: datetime) -> TradingSession:
        """Determine session for timestamp."""
        hour = timestamp.hour
        
        # London + NY overlap (13:00-16:00 UTC)
        if 13 <= hour < 16:
            return TradingSession.OVERLAP
        
        # Asian session (01:00-09:00 UTC)
        if 1 <= hour < 9:
            return TradingSession.ASIAN
        
        # London session (08:00-16:00 UTC)
        if 8 <= hour < 16:
            return TradingSession.LONDON
        
        # New York session (13:00-21:00 UTC)
        if 13 <= hour < 21:
            return TradingSession.NEW_YORK
        
        return TradingSession.OFF_HOURS
    
    def is_trading_hours(self, timestamp: datetime) -> bool:
        """Check if timestamp is during trading hours."""
        session = self.get_session(timestamp)
        return session != TradingSession.OFF_HOURS
