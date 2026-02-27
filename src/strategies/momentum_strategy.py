"""
Momentum Strategy - RSI + MACD confluence with enhanced filtering.

Entry Logic:
- Buy: RSI in 50-75 AND MACD histogram crossing zero AND accelerating
       AND Price > EMA(20) AND ADX >= 20 AND volume above average
- Sell: RSI in 25-50 AND MACD histogram crossing zero AND accelerating
        AND Price < EMA(20) AND ADX >= 20 AND volume above average

Exit Logic:
- ATR-based stop loss (1.2× ATR default, tighter than before)
- Take profit at configurable R:R ratio (default 2.0)

Best for:
- Trending intraday moves
- Momentum continuation with confirmation
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MomentumStrategy(BaseStrategy):
    """RSI + MACD confluence momentum strategy with volume, ADX, and RSI guards."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.rsi_period = config.get('rsi_period', 14)
        self.ema_period = config.get('ema_period', 20)
        self.rr_ratio = config.get('rr_ratio', 2.0)
        self.atr_stop_multiplier = config.get('atr_stop_multiplier', 1.2)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]
        
        # RSI thresholds (entry zone boundaries)
        self.rsi_bull_threshold = config.get('rsi_bull_threshold', 50)
        self.rsi_bear_threshold = config.get('rsi_bear_threshold', 50)
        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)
        
        # ADX minimum for momentum confirmation
        self.adx_min_threshold = config.get('adx_min_threshold', 20)
        
        # MACD settings
        self.macd_fast = config.get('macd_fast', 12)
        self.macd_slow = config.get('macd_slow', 26)
        self.macd_signal = config.get('macd_signal', 9)
        
        # Volume confirmation
        self.volume_confirmation = config.get('volume_confirmation', True)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.0)
        
        # Regime filter
        self.regime_filter = RegimeFilter()
    
    def get_name(self) -> str:
        return "momentum_scalp"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate momentum signal with enhanced filtering.
        
        Logic:
        1. Check regime (prefer TREND)
        2. Check ADX minimum threshold (trend must be strong enough)
        3. Check RSI direction within bounded range (not overbought/oversold)
        4. Check MACD histogram zero-line crossover AND acceleration
        5. Check volume confirmation
        6. Confirm with price vs EMA
        7. Generate signal with tight ATR-based stops
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
        adx = Indicators.adx(bars, period=14)
        
        current_close = bars['close'].iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_ema = ema.iloc[-1]
        current_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]
        current_atr = atr.iloc[-1]
        current_adx = adx.iloc[-1]
        
        if any(pd.isna([current_rsi, current_ema, current_histogram, prev_histogram, current_atr, current_adx])):
            self._log_no_signal("Indicator calculation failed")
            return None
        
        # ADX minimum threshold: trend must be strong enough for momentum
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(f"ADX too low for momentum ({current_adx:.1f} < {self.adx_min_threshold})")
            return None
        
        # Volume confirmation
        volume_ok = True
        volume_ratio = 0.0
        if self.volume_confirmation and 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].iloc[-21:-1].mean()
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                volume_ok = volume_ratio >= self.volume_ratio_min
            else:
                volume_ok = False
        
        # --- Check for bullish momentum confluence ---
        rsi_bullish = current_rsi > self.rsi_bull_threshold
        rsi_not_overbought = current_rsi < self.rsi_overbought  # Don't buy exhausted moves
        macd_turning_positive = current_histogram > 0 and prev_histogram <= 0  # Zero-line crossover
        macd_accelerating = abs(current_histogram) > abs(prev_histogram)  # Histogram growing
        price_above_ema = current_close > current_ema
        
        if (rsi_bullish and rsi_not_overbought and 
            macd_turning_positive and macd_accelerating and 
            price_above_ema and volume_ok):
            
            stop_loss = current_close - (self.atr_stop_multiplier * current_atr)
            risk = current_close - stop_loss
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None
            take_profit = current_close + (risk * self.rr_ratio)
            
            # Strength based on RSI distance from 50 and ADX
            rsi_strength = min(abs(current_rsi - 50) / 30, 0.5)
            adx_strength = min(current_adx / 100.0, 0.4)
            strength = min(rsi_strength + adx_strength + 0.1, 1.0)
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'atr': float(current_atr),
                    'volume_ratio': float(volume_ratio),
                    'macd_turning': macd_turning_positive,
                    'macd_accelerating': macd_accelerating,
                    'entry_reason': 'bullish_momentum'
                }
            )
        
        # --- Check for bearish momentum confluence ---
        rsi_bearish = current_rsi < self.rsi_bear_threshold
        rsi_not_oversold = current_rsi > self.rsi_oversold  # Don't sell exhausted moves
        macd_turning_negative = current_histogram < 0 and prev_histogram >= 0  # Zero-line crossover
        macd_decelerating = abs(current_histogram) > abs(prev_histogram)  # Histogram growing (in negative dir)
        price_below_ema = current_close < current_ema
        
        if (rsi_bearish and rsi_not_oversold and
            macd_turning_negative and macd_decelerating and
            price_below_ema and volume_ok):
            
            stop_loss = current_close + (self.atr_stop_multiplier * current_atr)
            risk = stop_loss - current_close
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None
            take_profit = current_close - (risk * self.rr_ratio)
            
            rsi_strength = min(abs(50 - current_rsi) / 30, 0.5)
            adx_strength = min(current_adx / 100.0, 0.4)
            strength = min(rsi_strength + adx_strength + 0.1, 1.0)
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'atr': float(current_atr),
                    'volume_ratio': float(volume_ratio),
                    'macd_turning': macd_turning_negative,
                    'macd_accelerating': macd_decelerating,
                    'entry_reason': 'bearish_momentum'
                }
            )
        
        # No confluence
        self._log_no_signal("No momentum confluence detected")
        return None
