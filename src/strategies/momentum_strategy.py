"""
Momentum Strategy - RSI + MACD confluence for quick intraday trades.

Entry Logic:
- Buy: RSI > 50 AND MACD histogram turning positive AND Price > EMA(20)
- Sell: RSI < 50 AND MACD histogram turning negative AND Price < EMA(20)

Exit Logic:
- Trailing stop based on ATR
- Quick profit targets (1:1 or 1.5:1 R:R)

Best for:
- Trending intraday moves
- Momentum continuation
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MomentumStrategy(BaseStrategy):
    """RSI + MACD confluence momentum strategy for intraday scalping."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.rsi_period = config.get('rsi_period', 14)
        self.ema_period = config.get('ema_period', 20)
        self.rr_ratio = config.get('rr_ratio', 1.5)
        self.atr_stop_multiplier = config.get('atr_stop_multiplier', 1.5)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]
        
        # RSI thresholds
        self.rsi_bull_threshold = config.get('rsi_bull_threshold', 50)
        self.rsi_bear_threshold = config.get('rsi_bear_threshold', 50)
        
        # MACD settings
        self.macd_fast = config.get('macd_fast', 12)
        self.macd_slow = config.get('macd_slow', 26)
        self.macd_signal = config.get('macd_signal', 9)
        
        # Regime filter
        self.regime_filter = RegimeFilter()
    
    def get_name(self) -> str:
        return "momentum_scalp"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate momentum signal based on RSI + MACD + EMA confluence.
        
        Logic:
        1. Check regime (prefer TREND)
        2. Check RSI direction (above/below 50)
        3. Check MACD histogram direction (turning positive/negative)
        4. Confirm with price vs EMA
        5. Generate signal with tight stops
        """
        if not self.is_enabled():
            return None
        
        min_bars = max(self.macd_slow + self.macd_signal + 5, self.rsi_period + 5)
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None
        
        # Check regime
        regime = self.regime_filter.classify(bars)
        
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None
        
        # Calculate indicators
        rsi = Indicators.rsi(bars, period=self.rsi_period)
        ema = Indicators.ema(bars, period=self.ema_period)
        macd_line, signal_line, histogram = Indicators.macd(
            bars, 
            fast_period=self.macd_fast,
            slow_period=self.macd_slow,
            signal_period=self.macd_signal
        )
        atr = Indicators.atr(bars, period=14)
        
        current_close = bars['close'].iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_ema = ema.iloc[-1]
        current_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]
        current_atr = atr.iloc[-1]
        
        if any(pd.isna([current_rsi, current_ema, current_histogram, prev_histogram, current_atr])):
            self._log_no_signal("Indicator calculation failed")
            return None
        
        # Check for bullish momentum confluence
        rsi_bullish = current_rsi > self.rsi_bull_threshold
        macd_turning_positive = current_histogram > 0 and prev_histogram <= 0
        macd_bullish = current_histogram > prev_histogram  # Histogram increasing
        price_above_ema = current_close > current_ema
        
        if rsi_bullish and (macd_turning_positive or macd_bullish) and price_above_ema:
            stop_loss = current_close - (self.atr_stop_multiplier * current_atr)
            risk = current_close - stop_loss
            take_profit = current_close + (risk * self.rr_ratio)
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(abs(current_rsi - 50) / 30, 1.0),  # Stronger signal further from 50
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'atr': float(current_atr),
                    'macd_turning': macd_turning_positive,
                    'entry_reason': 'bullish_momentum'
                }
            )
        
        # Check for bearish momentum confluence
        rsi_bearish = current_rsi < self.rsi_bear_threshold
        macd_turning_negative = current_histogram < 0 and prev_histogram >= 0
        macd_bearish = current_histogram < prev_histogram  # Histogram decreasing
        price_below_ema = current_close < current_ema
        
        if rsi_bearish and (macd_turning_negative or macd_bearish) and price_below_ema:
            stop_loss = current_close + (self.atr_stop_multiplier * current_atr)
            risk = stop_loss - current_close
            take_profit = current_close - (risk * self.rr_ratio)
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(abs(50 - current_rsi) / 30, 1.0),
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'atr': float(current_atr),
                    'macd_turning': macd_turning_negative,
                    'entry_reason': 'bearish_momentum'
                }
            )
        
        # No confluence
        self._log_no_signal("No momentum confluence detected")
        return None
