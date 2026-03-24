"""
Momentum Strategy - RSI + MACD confluence with enhanced high-win-rate filtering.

Entry Logic (HIGH WIN-RATE version):
- Only in TREND regime
- EMA stack alignment: EMA9 > EMA21 > EMA50 for BUY (price in full bullish stack)
- RSI > 50 (not overbought < 75) AND RSI slope rising (momentum building, not fading)
- MACD histogram > 0 AND accelerating (momentum building)
- Price > EMA20
- ADX >= 25 (strong trend)
- Volume confirmation (when available)
- Minimum signal strength gate (0.65)

Exit Logic:
- ATR-based stop loss (configurable multiplier, default 2.0)
- Take profit at configurable R:R ratio (default 1.5)
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MomentumStrategy(BaseStrategy):
    """RSI + MACD + EMA stack confluence momentum strategy."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Strategy parameters
        self.rsi_period = config.get('rsi_period', 14)
        self.ema_period = config.get('ema_period', 20)
        # Risk parameters removed (handled by RiskProcessor)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]

        # RSI thresholds
        self.rsi_bull_threshold = config.get('rsi_bull_threshold', 50)
        self.rsi_bear_threshold = config.get('rsi_bear_threshold', 50)
        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)

        # ADX minimum for momentum confirmation (raised to 25 for quality)
        self.adx_min_threshold = config.get('adx_min_threshold', 25)

        # MACD settings
        self.macd_fast = config.get('macd_fast', 12)
        self.macd_slow = config.get('macd_slow', 26)
        self.macd_signal = config.get('macd_signal', 9)

        # Volume confirmation
        self.volume_confirmation = config.get('volume_confirmation', True)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.0)

        # RSI slope: require RSI rising/falling over N bars
        self.rsi_slope_bars = config.get('rsi_slope_bars', 3)

        # EMA stack periods for full trend alignment confirmation
        self.ema_fast = config.get('ema_fast', 9)
        self.ema_mid = config.get('ema_mid', 21)
        self.ema_slow = config.get('ema_slow', 50)

        # Minimum signal strength to emit a signal
        self.min_signal_strength = config.get('min_signal_strength', 0.65)

        # ML Meta-labeling Filter (Optional)
        self.ml_dynamic_exhaustion = config.get('ml_dynamic_exhaustion', False)

        # Regime filter
        self.regime_filter = RegimeFilter()

    def get_name(self) -> str:
        return "momentum_scalp"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate momentum signal with HIGH WIN-RATE confluence.

        Logic:
        1. Check regime (prefer TREND)
        2. Check ADX minimum threshold (>= 25)
        3. Check EMA stack alignment (9 > 21 > 50 for BUY)
        4. Check RSI direction + slope (rising for BUY, not overbought)
        5. Check MACD histogram side AND acceleration
        6. Check volume confirmation
        7. Gate on minimum signal strength
        8. Generate signal with ATR-based stops
        """
        if not self.is_enabled():
            return None

        min_bars = max(self.macd_slow + self.macd_signal + 5,
                       self.rsi_period + 5,
                       self.ema_slow + 5)
        if len(bars) < min_bars:
            if getattr(self, '_momentum_logged_warmup', False) is False:
                self._log_no_signal("Insufficient data")
                self._momentum_logged_warmup = True
            return None
        self._momentum_logged_warmup = False

        # --- LATENCY FIX (Jeff Dean / Jonathan Blow) ---
        # Recalculating indicators on 2000+ bars every minute is O(N).
        # We only need the trailing window to warm up EMAs (400 bars is plenty).
        bars = bars.tail(400)

        # Check regime
        regime = self.regime_filter.classify(bars)
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None

        # Calculate indicators
        rsi = Indicators.rsi(bars, period=self.rsi_period)
        ema = Indicators.ema(bars, period=self.ema_period)
        ema_fast = Indicators.ema(bars, period=self.ema_fast)
        ema_mid = Indicators.ema(bars, period=self.ema_mid)
        ema_slow = Indicators.ema(bars, period=self.ema_slow)
        macd_line, signal_line, histogram = Indicators.macd(
            bars,
            fast_period=self.macd_fast,
            slow_period=self.macd_slow,
            signal_period=self.macd_signal
        )
        atr = Indicators.atr(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        rsi_slope = Indicators.rsi_slope(bars, rsi_period=self.rsi_period,
                                          slope_bars=self.rsi_slope_bars)

        current_close = bars['close'].iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_ema = ema.iloc[-1]
        current_ema_fast = ema_fast.iloc[-1]
        current_ema_mid = ema_mid.iloc[-1]
        current_ema_slow = ema_slow.iloc[-1]
        current_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]
        current_atr = atr.iloc[-1]
        current_adx = adx.iloc[-1]
        current_rsi_slope = rsi_slope.iloc[-1]

        if any(pd.isna([current_rsi, current_ema, current_histogram, prev_histogram,
                         current_atr, current_adx, current_ema_fast, current_ema_mid,
                         current_ema_slow, current_rsi_slope])):
            self._log_no_signal("Indicator calculation failed")
            return None

        # ADX minimum threshold
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(f"ADX too low ({current_adx:.1f} < {self.adx_min_threshold})")
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
                volume_ok = True  # no volume data — skip check

        # --- Bullish momentum confluence ---
        # EMA stack: fast > mid > slow (full bullish alignment)
        ema_stack_bullish = (current_ema_fast > current_ema_mid > current_ema_slow)
        rsi_bullish = current_rsi > self.rsi_bull_threshold
        rsi_not_overbought = current_rsi < self.rsi_overbought
        rsi_rising = current_rsi_slope > 0   # RSI slope must be positive
        macd_positive = current_histogram > 0
        macd_accelerating = abs(current_histogram) > abs(prev_histogram)
        price_above_ema = current_close > current_ema

        if (ema_stack_bullish and rsi_bullish and rsi_not_overbought and
                rsi_rising and macd_positive and macd_accelerating and
                price_above_ema and volume_ok):

            rsi_strength = min(abs(current_rsi - 50) / 30, 0.4)
            adx_strength = min(current_adx / 100.0, 0.35)
            slope_bonus = 0.1 if current_rsi_slope > 2 else 0.05
            stack_bonus = 0.1 if ema_stack_bullish else 0.0
            strength = min(rsi_strength + adx_strength + slope_bonus + stack_bonus, 1.0)

            if strength < self.min_signal_strength:
                self._log_no_signal(
                    f"Signal strength too low ({strength:.2f} < {self.min_signal_strength})")
                return None

            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'rsi_slope': float(current_rsi_slope),
                    'adx': float(current_adx),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'ema_fast': float(current_ema_fast),
                    'ema_mid': float(current_ema_mid),
                    'ema_slow': float(current_ema_slow),
                    'ema_stack': ema_stack_bullish,
                    'atr': float(current_atr),
                    'volume_ratio': float(volume_ratio),
                    'entry_reason': 'bullish_momentum'
                }
            )

        # --- Bearish momentum confluence ---
        # EMA stack: fast < mid < slow (full bearish alignment)
        ema_stack_bearish = (current_ema_fast < current_ema_mid < current_ema_slow)
        rsi_bearish = current_rsi < self.rsi_bear_threshold
        rsi_not_oversold = current_rsi > self.rsi_oversold
        rsi_falling = current_rsi_slope < 0   # RSI slope must be negative
        macd_negative = current_histogram < 0
        macd_deepening = abs(current_histogram) > abs(prev_histogram)
        price_below_ema = current_close < current_ema

        if (ema_stack_bearish and rsi_bearish and rsi_not_oversold and
                rsi_falling and macd_negative and macd_deepening and
                price_below_ema and volume_ok):

            rsi_strength = min(abs(50 - current_rsi) / 30, 0.4)
            adx_strength = min(current_adx / 100.0, 0.35)
            slope_bonus = 0.1 if current_rsi_slope < -2 else 0.05
            stack_bonus = 0.1 if ema_stack_bearish else 0.0
            strength = min(rsi_strength + adx_strength + slope_bonus + stack_bonus, 1.0)

            if strength < self.min_signal_strength:
                self._log_no_signal(
                    f"Signal strength too low ({strength:.2f} < {self.min_signal_strength})")
                return None

            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'momentum_scalp',
                    'rsi': float(current_rsi),
                    'rsi_slope': float(current_rsi_slope),
                    'adx': float(current_adx),
                    'macd_histogram': float(current_histogram),
                    'ema': float(current_ema),
                    'ema_fast': float(current_ema_fast),
                    'ema_mid': float(current_ema_mid),
                    'ema_slow': float(current_ema_slow),
                    'ema_stack': ema_stack_bearish,
                    'atr': float(current_atr),
                    'volume_ratio': float(volume_ratio),
                    'entry_reason': 'bearish_momentum'
                }
            )

        # No confluence
        self._log_no_signal("No momentum confluence detected")
        return None
