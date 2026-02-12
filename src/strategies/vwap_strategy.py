"""
VWAP Strategy - Intraday mean reversion around VWAP.

Entry Logic:
- Buy when price drops below VWAP - (ATR × multiplier)
- Sell when price rises above VWAP + (ATR × multiplier)

Exit Logic:
- Take profit: Return to VWAP
- Stop loss: 2 × ATR from entry

Best for:
- Intraday trading during ranging periods
- High-volume sessions (London, New York overlap)
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class VWAPStrategy(BaseStrategy):
    """VWAP deviation mean reversion strategy for intraday trading."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.atr_multiplier = config.get('atr_multiplier', 1.5)
        self.stop_atr_multiplier = config.get('stop_atr_multiplier', 2.0)
        self.atr_period = config.get('atr_period', 14)
        self.min_volume_ratio = config.get('min_volume_ratio', 1.0)  # Vs avg volume
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]
        
        # Regime filter
        self.regime_filter = RegimeFilter()
        
        # Track VWAP from session start
        self._session_start_idx = 0
    
    def get_name(self) -> str:
        return "vwap_deviation"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate VWAP deviation signal.
        
        Logic:
        1. Check regime (prefer RANGE)
        2. Calculate VWAP and deviation bands
        3. Check for oversold/overbought conditions
        4. Generate signal with tight stop/target
        """
        if not self.is_enabled():
            return None
        
        min_bars = max(self.atr_period + 5, 20)
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None
        
        # Check regime - restrict to RANGE only for live safety
        # In trends, price stays above/below VWAP causing repeated stop-outs
        regime = self.regime_filter.classify(bars)
        
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None
        
        # Calculate VWAP and deviation bands
        vwap, upper_band, lower_band = Indicators.vwap_deviation(
            bars, 
            atr_multiplier=self.atr_multiplier
        )
        
        atr = Indicators.atr(bars, period=self.atr_period)
        
        current_close = bars['close'].iloc[-1]
        current_vwap = vwap.iloc[-1]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        current_atr = atr.iloc[-1]
        
        if pd.isna(current_vwap) or pd.isna(current_atr):
            self._log_no_signal("VWAP or ATR calculation failed")
            return None
        
        # Check volume (optional filter)
        if 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].rolling(20).mean().iloc[-1]
            if current_volume < avg_volume * self.min_volume_ratio:
                self._log_no_signal("Volume too low")
                return None
        
        # Oversold - Buy signal (price below lower band)
        if current_close < current_lower:
            stop_loss = current_close - (self.stop_atr_multiplier * current_atr)
            take_profit = current_vwap  # Target VWAP
            
            deviation_pct = (current_vwap - current_close) / current_vwap * 100
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(deviation_pct / 2, 1.0),  # Higher deviation = stronger signal
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'atr': float(current_atr),
                    'entry_reason': 'oversold_below_vwap'
                }
            )
        
        # Overbought - Sell signal (price above upper band)
        if current_close > current_upper:
            stop_loss = current_close + (self.stop_atr_multiplier * current_atr)
            take_profit = current_vwap  # Target VWAP
            
            deviation_pct = (current_close - current_vwap) / current_vwap * 100
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(deviation_pct / 2, 1.0),
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'atr': float(current_atr),
                    'entry_reason': 'overbought_above_vwap'
                }
            )
        
        # Price within bands - no signal
        self._log_no_signal(f"Price {current_close:.2f} within VWAP bands")
        return None
