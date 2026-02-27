"""
Breakout Strategy - Donchian Channel breakouts with enhanced filtering.

Entry Logic:
- Only trade when regime = TREND
- Buy when price CLOSES above upper Donchian channel (not just wick)
- Sell when price CLOSES below lower Donchian channel
- Require above-average volume on breakout bar
- Skip overbought entries (RSI > 75) and oversold exits (RSI < 25)
- Optional: Require higher timeframe trend alignment

Exit Logic:
- Stop loss: ATR-based (2× ATR), capped at opposite Donchian boundary
- Take profit: Configurable reward/risk ratio

Parameters:
- donchian_period: Lookback for high/low (default 20)
- confirmation_bars: Bars to confirm breakout (default 0)
- rr_ratio: Reward/risk ratio (default 2.0)
- atr_stop_multiplier: ATR multiplier for stop loss (default 2.0)
- volume_confirmation: Require above-average volume (default True)
- volume_ratio_min: Minimum volume/avg volume ratio (default 1.2)
- rsi_overbought: RSI level to reject buys (default 75)
- rsi_oversold: RSI level to reject sells (default 25)
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
    """Donchian Channel breakout strategy with volume, RSI, and ATR-stop filters."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.donchian_period = config.get('donchian_period', 20)
        self.confirmation_bars = config.get('confirmation_bars', 0)
        self.rr_ratio = config.get('rr_ratio', 2.0)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]
        
        # ATR-based stop loss (replaces opposite-channel stop)
        self.atr_stop_multiplier = config.get('atr_stop_multiplier', 2.0)
        
        # Volume confirmation
        self.volume_confirmation = config.get('volume_confirmation', True)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.2)
        
        # RSI overbought/oversold guards
        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)
        
        # Multi-timeframe confirmation
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None
        
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
        Generate breakout signal with enhanced filtering.
        
        Logic:
        1. Check regime (must be TREND)
        2. Calculate Donchian channels
        3. Check for breakout (CLOSE beyond channel, not just wick)
        4. Check volume confirmation
        5. Check RSI overbought/oversold guard
        6. Confirm breakout via MTF (optional)
        7. Generate signal with ATR-based stop/target
        """
        if not self.is_enabled():
            return None
        
        if len(bars) < self.donchian_period + 2:
            self._log_no_signal("Insufficient data")
            return None
        
        # Check regime - MUST be TREND for breakouts
        regime = self.regime_filter.classify(bars)
        
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None
        
        # Calculate Donchian channels
        upper, middle, lower = Indicators.donchian_channel(bars, period=self.donchian_period)
        
        # Calculate additional indicators for filtering
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        
        current_close = bars['close'].iloc[-1]
        current_high = bars['high'].iloc[-1]
        current_low = bars['low'].iloc[-1]
        current_atr = atr.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_adx = adx.iloc[-1]
        
        if any(pd.isna([current_atr, current_rsi, current_adx])):
            self._log_no_signal("Indicator calculation failed")
            return None
        
        # Use previous channel values for breakout level (current channel includes current bar)
        breakout_upper = upper.iloc[-2]
        breakout_lower = lower.iloc[-2]
        
        # Volume confirmation: breakout bar must have above-average volume
        volume_ok = True
        volume_ratio = 0.0
        if self.volume_confirmation and 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].iloc[-21:-1].mean()  # 20-bar avg excluding current
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                volume_ok = volume_ratio >= self.volume_ratio_min
            else:
                volume_ok = False
        
        # --- Check for bullish breakout ---
        # KEY CHANGE: Require CLOSE above channel (not just wick/high)
        if current_close > breakout_upper:
            
            # RSI overbought guard: don't buy into exhausted moves
            if current_rsi > self.rsi_overbought:
                self._log_no_signal(f"RSI overbought ({current_rsi:.1f} > {self.rsi_overbought})")
                return None
            
            # Volume confirmation
            if not volume_ok:
                self._log_no_signal(f"Volume too low for breakout (ratio={volume_ratio:.2f})")
                return None
            
            # Check MTF confirmation if enabled
            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('BUY', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for BUY")
                    return None
            
            # ATR-based stop loss (tighter than opposite channel boundary)
            atr_stop = current_close - (self.atr_stop_multiplier * current_atr)
            channel_stop = breakout_lower
            stop_loss = max(atr_stop, channel_stop)  # Use tighter of the two
            
            risk = current_close - stop_loss
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None
            
            take_profit = current_close + (risk * self.rr_ratio)
            
            # ADX-weighted signal strength (stronger trend = stronger signal)
            base_strength = 0.6
            adx_bonus = min(current_adx / 100.0, 0.3)  # Up to 0.3 bonus from ADX
            mtf_bonus = 0.05 if (self.mtf_confirmation and self._pending_bars_by_tf) else 0.0
            strength = min(base_strength + adx_bonus + mtf_bonus, 1.0)
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
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
                    'atr': float(current_atr),
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'volume_ratio': float(volume_ratio),
                    'mtf_confirmed': bool(self.mtf_confirmation and self._pending_bars_by_tf)
                }
            )
        
        # --- Check for bearish breakout ---
        # KEY CHANGE: Require CLOSE below channel (not just wick/low)
        if current_close < breakout_lower:
            
            # RSI oversold guard: don't sell into exhausted moves
            if current_rsi < self.rsi_oversold:
                self._log_no_signal(f"RSI oversold ({current_rsi:.1f} < {self.rsi_oversold})")
                return None
            
            # Volume confirmation
            if not volume_ok:
                self._log_no_signal(f"Volume too low for breakout (ratio={volume_ratio:.2f})")
                return None
            
            # Check MTF confirmation if enabled
            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('SELL', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for SELL")
                    return None
            
            # ATR-based stop loss (tighter than opposite channel boundary)
            atr_stop = current_close + (self.atr_stop_multiplier * current_atr)
            channel_stop = breakout_upper
            stop_loss = min(atr_stop, channel_stop)  # Use tighter of the two
            
            risk = stop_loss - current_close
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None
            
            take_profit = current_close - (risk * self.rr_ratio)
            
            # ADX-weighted signal strength
            base_strength = 0.6
            adx_bonus = min(current_adx / 100.0, 0.3)
            mtf_bonus = 0.05 if (self.mtf_confirmation and self._pending_bars_by_tf) else 0.0
            strength = min(base_strength + adx_bonus + mtf_bonus, 1.0)
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
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
                    'atr': float(current_atr),
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'volume_ratio': float(volume_ratio),
                    'mtf_confirmed': bool(self.mtf_confirmation and self._pending_bars_by_tf)
                }
            )
        
        # No breakout
        self._log_no_signal("No breakout detected")
        return None
