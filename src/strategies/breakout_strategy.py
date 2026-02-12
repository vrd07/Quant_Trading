"""
Breakout Strategy - Donchian Channel breakouts.

Entry Logic:
- Only trade when regime = TREND
- Buy when price breaks above upper Donchian channel
- Sell when price breaks below lower Donchian channel
- Optional: Require higher timeframe trend alignment

Exit Logic:
- Stop loss: Opposite Donchian boundary
- Take profit: Configurable reward/risk ratio

Parameters:
- donchian_period: Lookback for high/low (default 20)
- confirmation_bars: Bars to confirm breakout (default 0)
- rr_ratio: Reward/risk ratio (default 2.0)
- mtf_confirmation: Use multi-timeframe filter (default False)
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from .multi_timeframe_filter import MultiTimeframeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class BreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout strategy with optional MTF confirmation."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.donchian_period = config.get('donchian_period', 20)
        self.confirmation_bars = config.get('confirmation_bars', 0)
        self.rr_ratio = config.get('rr_ratio', 2.0)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]
        
        # Multi-timeframe confirmation
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None
        
        # Regime filter
        # Regime filter with relaxed parameters for testing
        self.regime_filter = RegimeFilter(
            adx_trend_threshold=15, 
            adx_range_threshold=10,
            adx_period=10,
            use_hurst=False
        )
        
        # State
        self.last_breakout_bar = None
        self._pending_bars_by_tf: Dict[str, pd.DataFrame] = {}
    
    def get_name(self) -> str:
        return "donchian_breakout"
    
    def set_higher_tf_bars(self, bars_by_tf: Dict[str, pd.DataFrame]) -> None:
        """
        Set higher timeframe bars for MTF confirmation.
        
        Args:
            bars_by_tf: Dict mapping timeframe to bars, e.g. {'5m': df, '15m': df}
        """
        self._pending_bars_by_tf = bars_by_tf
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate breakout signal.
        
        Logic:
        1. Check regime (must be TREND)
        2. Calculate Donchian channels
        3. Check for breakout
        4. Confirm breakout (optional)
        5. Generate signal with stop/target
        """
        if not self.is_enabled():
            return None
        
        if len(bars) < self.donchian_period + 2:
            self._log_no_signal("Insufficient data")
            return None
        
        # Check regime - MUST be TREND for breakouts (re-enabled for live trading)
        regime = self.regime_filter.classify(bars)
        
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None
        
        # Calculate Donchian channels
        upper, middle, lower = Indicators.donchian_channel(bars, period=self.donchian_period)
        
        # Current and previous bar
        current_close = bars['close'].iloc[-1]
        current_high = bars['high'].iloc[-1]
        current_low = bars['low'].iloc[-1]
        
        prev_close = bars['close'].iloc[-2]
        
        # Use previous channel values for breakout level (since current channel includes current bar)
        breakout_upper = upper.iloc[-2]
        breakout_lower = lower.iloc[-2]
        
        # Check for bullish breakout
        if current_high > breakout_upper:
            # Breakout above upper channel
            
            # Check MTF confirmation if enabled
            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('BUY', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for BUY")
                    return None
            
            # Calculate stop and target
            stop_loss = breakout_lower
            risk = current_close - stop_loss
            take_profit = current_close + (risk * self.rr_ratio)
            
            mtf_confirmed = self.mtf_confirmation and self._pending_bars_by_tf
            return self._create_signal(
                side=OrderSide.BUY,
                strength=0.85 if mtf_confirmed else 0.8,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'breakout_type': 'upper',
                    'donchian_upper': float(breakout_upper),
                    'donchian_lower': float(breakout_lower),
                    'risk': float(risk),
                    'rr_ratio': self.rr_ratio,
                    'mtf_confirmed': mtf_confirmed
                }
            )
        
        # Check for bearish breakout
        if current_low < breakout_lower:
            # Breakout below lower channel
            
            # Check MTF confirmation if enabled
            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('SELL', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for SELL")
                    return None
            
            stop_loss = breakout_upper
            risk = stop_loss - current_close
            take_profit = current_close - (risk * self.rr_ratio)
            
            mtf_confirmed = self.mtf_confirmation and self._pending_bars_by_tf
            return self._create_signal(
                side=OrderSide.SELL,
                strength=0.85 if mtf_confirmed else 0.8,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'breakout_type': 'lower',
                    'donchian_upper': float(breakout_upper),
                    'donchian_lower': float(breakout_lower),
                    'risk': float(risk),
                    'rr_ratio': self.rr_ratio,
                    'mtf_confirmed': mtf_confirmed
                }
            )
        
        # No breakout
        self._log_no_signal("No breakout detected")
        return None
