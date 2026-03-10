"""
Breakout Strategy - Donchian Channel breakouts with enhanced filtering.

Entry Logic (HIGH WIN-RATE version):
- Only trade when regime = TREND
- Buy when price CLOSES above upper Donchian channel (not just wick)
- Sell when price CLOSES below lower Donchian channel
- Require BB squeeze before the breakout (coiled energy)
- Require above-average volume on breakout bar (when available)
- Skip overbought entries (RSI > 75) and oversold exits (RSI < 25)
- Stochastic confirmation: %K < 80 for BUY, %K > 20 for SELL (not already exhausted)
- ADX > adx_min_threshold (trend strong enough)
- VWAP alignment: price above VWAP for longs, below for shorts
- Minimum signal strength gate (0.65)
- Optional: Require higher timeframe trend alignment

Exit Logic:
- Stop loss: ATR-based, capped at opposite Donchian boundary
- Take profit: Configurable reward/risk ratio
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
    """Donchian Channel breakout strategy with volume, RSI, BB squeeze, Stochastic, and ATR-stop filters."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Strategy parameters
        self.donchian_period = config.get('donchian_period', 20)
        self.confirmation_bars = config.get('confirmation_bars', 0)
        self.rr_ratio = config.get('rr_ratio', 2.0)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]

        # ATR-based stop loss
        self.atr_stop_multiplier = config.get('atr_stop_multiplier', 2.0)

        # ADX minimum for trend confirmation
        self.adx_min_threshold = config.get('adx_min_threshold', 25)

        # Volume confirmation
        self.volume_confirmation = config.get('volume_confirmation', True)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.2)

        # RSI overbought/oversold guards
        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)

        # BB squeeze: width must be below its N-bar average before breakout
        self.bb_squeeze_lookback = config.get('bb_squeeze_lookback', 20)

        # Stochastic guard: don't buy when already overbought on Stochastic
        self.stoch_overbought = config.get('stoch_overbought', 80)
        self.stoch_oversold = config.get('stoch_oversold', 20)

        # Minimum signal strength to emit a signal
        self.min_signal_strength = config.get('min_signal_strength', 0.65)

        # Multi-timeframe confirmation
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None

        # Regime filter
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
        self._pending_bars_by_tf = bars_by_tf

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate breakout signal with HIGH WIN-RATE confluence filtering.

        Logic:
        1. Check regime (must be TREND)
        2. Calculate Donchian channels
        3. Check BB squeeze before breakout (coiled energy prerequisite)
        4. Check for breakout (CLOSE beyond channel, not just wick)
        5. Check ADX threshold (trend strong enough)
        6. Check VWAP alignment
        7. Check RSI overbought/oversold guard
        8. Check Stochastic (not already exhausted)
        9. Check volume confirmation
        10. Confirm breakout via MTF (optional)
        11. Gate on minimum signal strength
        12. Generate signal with ATR-based stop/target
        """
        if not self.is_enabled():
            return None

        if len(bars) < self.donchian_period + self.bb_squeeze_lookback + 5:
            self._log_no_signal("Insufficient data")
            return None

        # Check regime - MUST be TREND for breakouts
        regime = self.regime_filter.classify(bars)
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None

        # Calculate Donchian channels
        upper, middle, lower = Indicators.donchian_channel(bars, period=self.donchian_period)

        # Calculate supporting indicators
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        vwap = Indicators.vwap(bars)
        stoch_k, stoch_d = Indicators.stochastic(bars, period=14)
        bb_w = Indicators.bb_width(bars, period=20)

        current_close = bars['close'].iloc[-1]
        current_atr = atr.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_adx = adx.iloc[-1]
        current_vwap = vwap.iloc[-1]
        current_stoch_k = stoch_k.iloc[-1]
        current_bb_width = bb_w.iloc[-1]

        if any(pd.isna([current_atr, current_rsi, current_adx, current_vwap,
                         current_stoch_k, current_bb_width])):
            self._log_no_signal("Indicator calculation failed")
            return None

        # BB squeeze check: BB width at breakout bar must be below its recent average
        # (the market coiled before the breakout, making it a genuine expansion)
        bb_width_avg = bb_w.iloc[-self.bb_squeeze_lookback - 1:-1].mean()
        bb_squeeze_ok = current_bb_width <= bb_width_avg * 1.1  # allow 10% buffer

        if not bb_squeeze_ok:
            self._log_no_signal(
                f"No BB squeeze: width={current_bb_width:.4f} > avg={bb_width_avg:.4f}")
            return None

        # Use previous channel values for breakout level
        breakout_upper = upper.iloc[-2]
        breakout_lower = lower.iloc[-2]

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

        # --- Bullish breakout ---
        if current_close > breakout_upper:

            if current_adx < self.adx_min_threshold:
                self._log_no_signal(f"ADX too low ({current_adx:.1f} < {self.adx_min_threshold})")
                return None

            if current_close < current_vwap:
                self._log_no_signal("Price below VWAP, rejecting LONG breakout")
                return None

            if current_rsi > self.rsi_overbought:
                self._log_no_signal(f"RSI overbought ({current_rsi:.1f} > {self.rsi_overbought})")
                return None

            # Stochastic guard: don't buy when Stochastic already overbought
            if current_stoch_k > self.stoch_overbought:
                self._log_no_signal(
                    f"Stochastic already overbought (%K={current_stoch_k:.1f}), skipping LONG")
                return None

            if not volume_ok:
                self._log_no_signal(f"Volume too low (ratio={volume_ratio:.2f})")
                return None

            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('BUY', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for BUY")
                    return None

            atr_stop = current_close - (self.atr_stop_multiplier * current_atr)
            channel_stop = breakout_lower
            stop_loss = max(atr_stop, channel_stop)

            risk = current_close - stop_loss
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None

            take_profit = current_close + (risk * self.rr_ratio)

            # Signal strength: ADX + BB squeeze depth + MTF bonus
            base_strength = 0.55
            adx_bonus = min(current_adx / 100.0, 0.25)
            # Deeper squeeze (lower relative width) = stronger breakout potential
            squeeze_depth = max(0, (bb_width_avg - current_bb_width) / bb_width_avg)
            squeeze_bonus = min(squeeze_depth * 0.15, 0.10)
            mtf_bonus = 0.05 if (self.mtf_confirmation and self._pending_bars_by_tf) else 0.0
            strength = min(base_strength + adx_bonus + squeeze_bonus + mtf_bonus, 1.0)

            # Gate on minimum strength
            if strength < self.min_signal_strength:
                self._log_no_signal(
                    f"Signal strength too low ({strength:.2f} < {self.min_signal_strength})")
                return None

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
                    'vwap': float(current_vwap),
                    'stoch_k': float(current_stoch_k),
                    'bb_width': float(current_bb_width),
                    'bb_width_avg': float(bb_width_avg),
                    'volume_ratio': float(volume_ratio),
                    'mtf_confirmed': bool(self.mtf_confirmation and self._pending_bars_by_tf)
                }
            )

        # --- Bearish breakout ---
        if current_close < breakout_lower:

            if current_adx < self.adx_min_threshold:
                self._log_no_signal(
                    f"ADX too low for bearish breakout ({current_adx:.1f} < {self.adx_min_threshold})")
                return None

            if current_close > current_vwap:
                self._log_no_signal("Price above VWAP, rejecting SHORT breakout")
                return None

            if current_rsi < self.rsi_oversold:
                self._log_no_signal(f"RSI oversold ({current_rsi:.1f} < {self.rsi_oversold})")
                return None

            # Stochastic guard: don't short when Stochastic already oversold
            if current_stoch_k < self.stoch_oversold:
                self._log_no_signal(
                    f"Stochastic already oversold (%K={current_stoch_k:.1f}), skipping SHORT")
                return None

            if not volume_ok:
                self._log_no_signal(f"Volume too low (ratio={volume_ratio:.2f})")
                return None

            if self.mtf_confirmation and self.mtf_filter:
                if not self.mtf_filter.confirm_signal('SELL', self._pending_bars_by_tf):
                    self._log_no_signal("MTF confirmation failed for SELL")
                    return None

            atr_stop = current_close + (self.atr_stop_multiplier * current_atr)
            channel_stop = breakout_upper
            stop_loss = min(atr_stop, channel_stop)

            risk = stop_loss - current_close
            if risk <= 0:
                self._log_no_signal("Invalid risk calculation (risk <= 0)")
                return None

            take_profit = current_close - (risk * self.rr_ratio)

            base_strength = 0.55
            adx_bonus = min(current_adx / 100.0, 0.25)
            squeeze_depth = max(0, (bb_width_avg - current_bb_width) / bb_width_avg)
            squeeze_bonus = min(squeeze_depth * 0.15, 0.10)
            mtf_bonus = 0.05 if (self.mtf_confirmation and self._pending_bars_by_tf) else 0.0
            strength = min(base_strength + adx_bonus + squeeze_bonus + mtf_bonus, 1.0)

            if strength < self.min_signal_strength:
                self._log_no_signal(
                    f"Signal strength too low ({strength:.2f} < {self.min_signal_strength})")
                return None

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
                    'vwap': float(current_vwap),
                    'stoch_k': float(current_stoch_k),
                    'bb_width': float(current_bb_width),
                    'bb_width_avg': float(bb_width_avg),
                    'volume_ratio': float(volume_ratio),
                    'mtf_confirmed': bool(self.mtf_confirmation and self._pending_bars_by_tf)
                }
            )

        self._log_no_signal("No breakout detected")
        return None
