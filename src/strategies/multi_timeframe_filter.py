"""
Multi-Timeframe Confirmation Filter.

Uses higher timeframe trend alignment to confirm lower timeframe signals.

Logic:
- Calculate EMA(20) and EMA(50) on 5m and 15m timeframes
- If 5m and 15m both show EMA20 > EMA50 → BULLISH bias
- If 5m and 15m both show EMA20 < EMA50 → BEARISH bias
- Otherwise → NEUTRAL

Usage:
- Breakout BUY signals require BULLISH or NEUTRAL bias
- Breakout SELL signals require BEARISH or NEUTRAL bias
- Mean reversion can work in any bias (since it's counter-trend)
"""

from enum import Enum
from typing import Dict, Optional
import pandas as pd

from ..data.indicators import Indicators


class MTFBias(Enum):
    """Multi-timeframe bias classification."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class MultiTimeframeFilter:
    """
    Multi-timeframe confirmation filter.
    
    Analyzes higher timeframe trends to filter lower timeframe signals.
    This reduces false breakouts by ensuring alignment with the larger trend.
    """
    
    def __init__(
        self,
        fast_ema_period: int = 20,
        slow_ema_period: int = 50,
        required_alignment: int = 2  # Number of timeframes that must agree
    ):
        """
        Initialize MTF filter.
        
        Args:
            fast_ema_period: Fast EMA period for trend detection
            slow_ema_period: Slow EMA period for trend detection
            required_alignment: How many timeframes must agree for bias
        """
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.required_alignment = required_alignment
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def get_timeframe_bias(self, bars: pd.DataFrame) -> Optional[MTFBias]:
        """
        Get trend bias for a single timeframe.
        
        Args:
            bars: OHLCV DataFrame for one timeframe
            
        Returns:
            MTFBias or None if insufficient data
        """
        min_required = self.slow_ema_period + 5
        
        if bars is None or len(bars) < min_required:
            return None
        
        # Calculate EMAs
        fast_ema = Indicators.ema(bars, period=self.fast_ema_period)
        slow_ema = Indicators.ema(bars, period=self.slow_ema_period)
        
        current_fast = fast_ema.iloc[-1]
        current_slow = slow_ema.iloc[-1]
        
        if pd.isna(current_fast) or pd.isna(current_slow):
            return None
        
        if current_fast > current_slow:
            return MTFBias.BULLISH
        elif current_fast < current_slow:
            return MTFBias.BEARISH
        else:
            return MTFBias.NEUTRAL
    
    def get_overall_bias(
        self,
        bars_by_timeframe: Dict[str, pd.DataFrame]
    ) -> MTFBias:
        """
        Get overall bias from multiple timeframes.
        
        Args:
            bars_by_timeframe: Dict mapping timeframe names to bar DataFrames
                               e.g., {'5m': df_5m, '15m': df_15m}
        
        Returns:
            MTFBias representing the overall trend alignment
        """
        bullish_count = 0
        bearish_count = 0
        
        for tf_name, bars in bars_by_timeframe.items():
            bias = self.get_timeframe_bias(bars)
            
            if bias == MTFBias.BULLISH:
                bullish_count += 1
            elif bias == MTFBias.BEARISH:
                bearish_count += 1
            
            self.logger.debug(
                f"Timeframe bias",
                timeframe=tf_name,
                bias=bias.value if bias else "unknown"
            )
        
        # Determine overall bias
        if bullish_count >= self.required_alignment:
            overall = MTFBias.BULLISH
        elif bearish_count >= self.required_alignment:
            overall = MTFBias.BEARISH
        else:
            overall = MTFBias.NEUTRAL
        
        self.logger.debug(
            f"Overall MTF bias",
            bullish_count=bullish_count,
            bearish_count=bearish_count,
            overall=overall.value
        )
        
        return overall
    
    def confirm_signal(
        self,
        signal_side: str,
        bars_by_timeframe: Dict[str, pd.DataFrame],
        allow_neutral: bool = True
    ) -> bool:
        """
        Confirm if a signal aligns with higher timeframe bias.
        
        Args:
            signal_side: 'BUY' or 'SELL'
            bars_by_timeframe: Higher timeframe bars
            allow_neutral: If True, NEUTRAL bias allows both directions
            
        Returns:
            True if signal is confirmed, False otherwise
        """
        bias = self.get_overall_bias(bars_by_timeframe)
        
        if signal_side.upper() == 'BUY':
            confirmed = bias == MTFBias.BULLISH or (allow_neutral and bias == MTFBias.NEUTRAL)
        elif signal_side.upper() == 'SELL':
            confirmed = bias == MTFBias.BEARISH or (allow_neutral and bias == MTFBias.NEUTRAL)
        else:
            confirmed = False
        
        self.logger.info(
            f"MTF confirmation",
            signal_side=signal_side,
            bias=bias.value,
            confirmed=confirmed
        )
        
        return confirmed
