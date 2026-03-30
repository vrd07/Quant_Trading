"""
Breakout Strategy - Donchian Channel breakouts for intraday 5m trading.

Streamlined filter stack (6 core filters, not 16):
1. Donchian channel breach (previous bar's upper/lower)
2. ADX > threshold and rising (trend strength)
3. Bar body >= min_body_atr_ratio × ATR (quality bar)
4. VWAP alignment (price above for longs, below for shorts)
5. RSI not extreme (avoid chasing)
6. ATR not spiking (fear regime suppression)

Removed filters that killed all signals on 5m gold:
- Hurst-based regime gate (oscillates ~0.5 on 5m, blocks everything)
- BB squeeze prerequisite (too restrictive combined with other filters)
- Stochastic overbought/oversold (redundant with RSI)
- Volume ratio 1.35x (MT5 tick volume unreliable on 5m)
- MACD histogram alignment (redundant with ADX)
- H1 EMA21 HTF trend (expensive resample, marginal edge)
- Close position in bar range (covered by body size filter)
- Minimum breakout distance (covered by Donchian + body filters)
- Signal strength gate (was rejecting valid setups)

Exit Logic:
- Stop loss: ATR-based
- Take profit: Configurable reward/risk ratio
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators

# RegimeFilter removed — ADX check inside signal logic is sufficient for 5m


class BreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout strategy — streamlined for intraday 5m."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.donchian_period = config.get('donchian_period', 20)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]

        self.adx_min_threshold = config.get('adx_min_threshold', 20)
        self.min_body_atr_ratio = config.get('min_body_atr_ratio', 0.30)

        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)

        # ATR vol-spike suppression
        self.atr_spike_mult = config.get('atr_spike_mult', 1.5)
        self.atr_ma_period = config.get('atr_ma_period', 20)

        # Volume confirmation (optional, off by default for 5m tick volume)
        self.volume_confirmation = config.get('volume_confirmation', False)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.2)

        self.last_breakout_bar = None

    def get_name(self) -> str:
        return "donchian_breakout"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        min_bars = self.donchian_period + self.atr_ma_period + 5
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        # No separate regime filter — ADX > threshold inside the signal logic
        # already confirms trend. The RegimeFilter's scoring system is too strict
        # on 5m data (UNKNOWN 100% of the time).
        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.TREND

        # Indicators
        upper, middle, lower = Indicators.donchian_channel(bars, period=self.donchian_period)
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        vwap = Indicators.vwap(bars)

        current_close = bars['close'].iloc[-1]
        current_open = float(bars['open'].iloc[-1])
        current_atr = atr.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_adx = adx.iloc[-1]
        prev_adx = adx.iloc[-2]
        current_vwap = vwap.iloc[-1]

        if any(pd.isna([current_atr, current_rsi, current_adx, prev_adx, current_vwap])):
            self._log_no_signal("Indicator calculation failed")
            return None

        # ── Filter 1: ATR vol-spike suppression ─────────────────────────────
        atr_ma = atr.rolling(window=self.atr_ma_period).mean().iloc[-1]
        if not pd.isna(atr_ma) and atr_ma > 0:
            if float(current_atr) > self.atr_spike_mult * float(atr_ma):
                self._log_no_signal(
                    f"ATR spike: {current_atr:.2f} > {self.atr_spike_mult}× MA={atr_ma:.2f}")
                return None

        # ── Filter 2: ADX rising ─────────────────────────────────────────────
        if current_adx <= prev_adx:
            self._log_no_signal(f"ADX not rising ({current_adx:.1f} <= {prev_adx:.1f})")
            return None

        # Bar body for quality check
        bar_body = abs(current_close - current_open)
        min_body = float(current_atr) * self.min_body_atr_ratio

        # Use previous bar's channel values (no lookahead)
        breakout_upper = upper.iloc[-2]
        breakout_lower = lower.iloc[-2]

        # Volume (optional)
        volume_ratio = 0.0
        if self.volume_confirmation and 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].iloc[-21:-1].mean()
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                if volume_ratio < self.volume_ratio_min:
                    self._log_no_signal(f"Volume too low (ratio={volume_ratio:.2f})")
                    return None

        # ── Bullish breakout ─────────────────────────────────────────────────
        if current_close > breakout_upper:

            # Filter 3: Body size
            if bar_body < min_body:
                self._log_no_signal(f"Bullish: body too small ({bar_body:.2f} < {min_body:.2f})")
                return None

            # Filter 4: ADX threshold
            if current_adx < self.adx_min_threshold:
                self._log_no_signal(f"ADX too low ({current_adx:.1f} < {self.adx_min_threshold})")
                return None

            # Filter 5: VWAP alignment
            if current_close < current_vwap:
                self._log_no_signal("Price below VWAP, rejecting LONG")
                return None

            # Filter 6: RSI not overbought
            if current_rsi > self.rsi_overbought:
                self._log_no_signal(f"RSI overbought ({current_rsi:.1f})")
                return None

            # Strength: simple ADX-normalized
            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            strength = min(0.55 + adx_norm * 0.35, 1.0)

            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'breakout_type': 'upper',
                    'donchian_upper': float(breakout_upper),
                    'donchian_lower': float(breakout_lower),
                    'atr': float(current_atr),
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'vwap': float(current_vwap),
                    'volume_ratio': float(volume_ratio),
                }
            )

        # ── Bearish breakout ─────────────────────────────────────────────────
        if current_close < breakout_lower:

            if bar_body < min_body:
                self._log_no_signal(f"Bearish: body too small ({bar_body:.2f} < {min_body:.2f})")
                return None

            if current_adx < self.adx_min_threshold:
                self._log_no_signal(f"ADX too low ({current_adx:.1f})")
                return None

            if current_close > current_vwap:
                self._log_no_signal("Price above VWAP, rejecting SHORT")
                return None

            if current_rsi < self.rsi_oversold:
                self._log_no_signal(f"RSI oversold ({current_rsi:.1f})")
                return None

            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            strength = min(0.55 + adx_norm * 0.35, 1.0)

            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'breakout_type': 'lower',
                    'donchian_upper': float(breakout_upper),
                    'donchian_lower': float(breakout_lower),
                    'atr': float(current_atr),
                    'rsi': float(current_rsi),
                    'adx': float(current_adx),
                    'vwap': float(current_vwap),
                    'volume_ratio': float(volume_ratio),
                }
            )

        self._log_no_signal("No breakout detected")
        return None
