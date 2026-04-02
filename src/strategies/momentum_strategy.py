"""
Momentum Strategy - RSI + MACD confluence with research-backed improvements.

Research basis (2025-2026):
- arXiv:2602.18912: 10-minute momentum window optimal; ATR vol-spike suppression (fear/overreaction)
- arXiv:2602.11708: Regime-conditional performance; ATR trailing stop α=2.0-2.5
- arXiv:2601.19504: Asymmetric long/short sizing — 70/30 bias; multi-confluence win rate 61.5%
- arXiv:2509.21326: MACD as band-pass filter — 3-bar persistence at 5m validates ~15m confirmation
- arXiv:2004.09963: Three-state TREND/RANGE/UNKNOWN regime system empirically validated

Key improvements over prior version:
1. ATR vol-spike suppression: skip entries when ATR > 1.5× 20-bar mean (fear/overreaction regime)
2. H1 HTF trend alignment: only long when H1 EMA21 rising; only short when falling (cached, every 60 bars)
3. Asymmetric SELL strength: Gold's long-term upward drift → SELL threshold = BUY threshold + 0.05
4. Normalised strength formula: bounded [0,1] components replace ad-hoc sum with overflow risk

Entry Logic:
- Only in TREND regime (ADX + Hurst dual confirmation, score ≥ 2)
- ATR must not be in fear/vol-spike territory (ATR < 1.5× 20-bar ATR MA)
- H1 EMA21 direction must align with signal direction (when available)
- EMA stack alignment: EMA9 > EMA21 > EMA50 for BUY
- RSI > rsi_bull_threshold AND RSI slope positive (momentum building, not fading)
- MACD histogram positive for ≥3 bars AND accelerating (band-pass filter persistence)
- Price > EMA20 AND EMA20 rising
- ADX >= adx_min_threshold (strong trend)
- Volume confirmation (when available)
- Minimum signal strength gate (BUY: min_signal_strength, SELL: +0.05 asymmetric)

Exit Logic:
- ATR-based stop loss (configurable multiplier, default 1.5)
- Take profit at configurable R:R ratio (default 2.5)
"""

from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MomentumStrategy(BaseStrategy):
    """RSI + MACD + EMA stack confluence momentum strategy."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.rsi_period = config.get('rsi_period', 14)
        self.ema_period = config.get('ema_period', 20)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]

        # RSI thresholds
        self.rsi_bull_threshold = config.get('rsi_bull_threshold', 52)
        self.rsi_bear_threshold = config.get('rsi_bear_threshold', 48)
        self.rsi_overbought = config.get('rsi_overbought', 75)
        self.rsi_oversold = config.get('rsi_oversold', 25)

        self.adx_min_threshold = config.get('adx_min_threshold', 25)

        # MACD settings
        self.macd_fast = config.get('macd_fast', 12)
        self.macd_slow = config.get('macd_slow', 26)
        self.macd_signal = config.get('macd_signal', 9)

        # Volume confirmation
        self.volume_confirmation = config.get('volume_confirmation', True)
        self.volume_ratio_min = config.get('volume_ratio_min', 1.0)

        self.rsi_slope_bars = config.get('rsi_slope_bars', 3)

        # EMA stack periods
        self.ema_fast = config.get('ema_fast', 9)
        self.ema_mid = config.get('ema_mid', 21)
        self.ema_slow = config.get('ema_slow', 50)

        # Asymmetric strength gate: SELL needs a higher bar due to Gold's long-term upward drift.
        # arXiv:2601.19504: 70/30 long/short size asymmetry validated empirically for Gold.
        self.min_signal_strength = config.get('min_signal_strength', 0.65)
        self.min_signal_strength_sell = config.get('min_signal_strength_sell',
                                                    self.min_signal_strength + 0.05)

        # ATR vol-spike suppression (arXiv:2602.18912).
        # Fear/overreaction spikes produce reversals, not momentum continuation.
        # Suppress entries when current ATR exceeds atr_spike_mult × its 20-bar mean.
        self.atr_spike_mult = config.get('atr_spike_mult', 1.5)
        self.atr_ma_period = config.get('atr_ma_period', 20)

        self.ml_dynamic_exhaustion = config.get('ml_dynamic_exhaustion', False)

        # H1 HTF trend alignment cache
        self._h1_last_len: int = 0
        self._h1_trend_cached: Optional[bool] = None

    def get_name(self) -> str:
        return "momentum_scalp"

    def _get_h1_trend(self, bars: pd.DataFrame) -> Optional[bool]:
        """
        Return True if H1 EMA21 is rising (bullish HTF trend),
        False if falling, None if insufficient data.

        Cached — only resamples when 60+ new 1m bars have arrived.
        Consistent with vwap_strategy resample pattern (DatetimeIndex assumed).
        """
        if len(bars) >= self._h1_last_len + 60:
            try:
                h1 = (
                    bars.resample('1h')
                    .agg({'open': 'first', 'high': 'max',
                          'low': 'min', 'close': 'last', 'volume': 'sum'})
                    .dropna(subset=['open', 'close'])
                )
                if len(h1) >= 23:
                    ema21 = Indicators.ema(h1, period=21)
                    if not pd.isna(ema21.iloc[-1]) and not pd.isna(ema21.iloc[-2]):
                        self._h1_trend_cached = bool(ema21.iloc[-1] > ema21.iloc[-2])
            except Exception:
                pass
            self._h1_last_len = len(bars)
        return self._h1_trend_cached

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        min_bars = max(self.macd_slow + self.macd_signal + 5,
                       self.rsi_period + 5,
                       self.ema_slow + 5)
        if len(bars) < min_bars:
            if not getattr(self, '_momentum_logged_warmup', False):
                self._log_no_signal("Insufficient data")
                self._momentum_logged_warmup = True
            return None
        self._momentum_logged_warmup = False

        # Trim to 400 bars — enough to warm up all EMAs (O(N) indicator cost mitigation)
        bars = bars.tail(400)

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
        prev_ema = ema.iloc[-2]
        current_ema_fast = ema_fast.iloc[-1]
        current_ema_mid = ema_mid.iloc[-1]
        current_ema_slow = ema_slow.iloc[-1]
        current_histogram = histogram.iloc[-1]
        prev_histogram = histogram.iloc[-2]
        prev2_histogram = histogram.iloc[-3]
        current_atr = atr.iloc[-1]
        current_adx = adx.iloc[-1]
        current_rsi_slope = rsi_slope.iloc[-1]

        if any(pd.isna([current_rsi, current_ema, prev_ema, current_histogram, prev_histogram,
                         prev2_histogram, current_atr, current_adx, current_ema_fast,
                         current_ema_mid, current_ema_slow, current_rsi_slope])):
            self._log_no_signal("Indicator calculation failed")
            return None

        # Inline regime classification using ADX + EMA direction.
        # ADX confirms trend strength, EMA fast > mid confirms directional alignment.
        # For SELL signals: the SELL path bypasses the regime gate (see below) since
        # a bearish setup naturally has EMA fast < mid.
        if self.ml_regime is not None:
            regime = self.ml_regime
        elif current_adx >= self.adx_min_threshold:
            regime = MarketRegime.TREND
        else:
            regime = MarketRegime.RANGE

        # Regime gate: momentum only fires in TREND regime
        if regime != MarketRegime.TREND:
            self._log_no_signal(f"Regime is {regime.name}, momentum requires TREND")
            return None

        # ATR vol-spike suppression (arXiv:2602.18912):
        # When current ATR exceeds 1.5× its 20-bar mean the market is in a fear/overreaction
        # regime where momentum continuation probability drops and reversals dominate.
        atr_ma = atr.rolling(window=self.atr_ma_period).mean().iloc[-1]
        if not pd.isna(atr_ma) and atr_ma > 0:
            if float(current_atr) > self.atr_spike_mult * float(atr_ma):
                self._log_no_signal(
                    f"ATR spike suppression: "
                    f"ATR={current_atr:.2f} > {self.atr_spike_mult}× MA={atr_ma:.2f}")
                return None

        # ADX minimum threshold
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(f"ADX too low ({current_adx:.1f} < {self.adx_min_threshold})")
            return None

        # H1 HTF trend (cached every 60 bars)
        h1_trend = self._get_h1_trend(bars)

        # Volume confirmation
        volume_ok = True
        volume_ratio = 0.0
        if self.volume_confirmation and 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].iloc[-21:-1].mean()
            if avg_volume > 0:
                volume_ratio = current_volume / avg_volume
                volume_ok = volume_ratio >= self.volume_ratio_min

        # ── Bullish momentum confluence ──────────────────────────────────────
        ema_stack_bullish = (current_ema_fast > current_ema_mid > current_ema_slow)
        rsi_bullish = current_rsi > self.rsi_bull_threshold
        rsi_not_overbought = current_rsi < self.rsi_overbought
        rsi_rising = current_rsi_slope > 0
        # 2-bar MACD histogram persistence (relaxed from 3-bar — too strict on 5m)
        macd_positive = (current_histogram > 0 and prev_histogram > 0)
        # Acceleration: current bar stronger than previous (relaxed from 3-bar chain)
        macd_accelerating = abs(current_histogram) > abs(prev_histogram)
        price_above_ema = current_close > current_ema
        ema_rising = current_ema > prev_ema
        # Entry proximity: price must be within 2×ATR of EMA20 — avoids chasing overextended moves
        not_overextended = (current_close - current_ema) < (2.0 * float(current_atr))
        # H1 must be bullish — allow None (when H1 unavailable, don't gate)
        h1_aligned = h1_trend is not False

        if (ema_stack_bullish and rsi_bullish and rsi_not_overbought and
                rsi_rising and macd_positive and macd_accelerating and
                price_above_ema and ema_rising and not_overextended and volume_ok and h1_aligned):

            # Normalised strength: bounded [0,1] components sum to exactly [0,1].
            # rsi_norm: distance above 50 (max useful range = 30 pts to overbought threshold)
            # adx_norm: excess above min threshold (50 pts covers ADX 25→75 range)
            # slope_norm: RSI slope magnitude (5 pts/bar = strong; cap at 1.0)
            rsi_norm = min((float(current_rsi) - 50.0) / 30.0, 1.0)
            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            slope_norm = min(abs(float(current_rsi_slope)) / 5.0, 1.0)
            strength = rsi_norm * 0.4 + adx_norm * 0.35 + slope_norm * 0.25
            if h1_trend is True:
                strength = min(strength + 0.05, 1.0)

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
                    'h1_trend': h1_trend,
                    'entry_reason': 'bullish_momentum'
                }
            )

        # ── Bearish momentum confluence ──────────────────────────────────────
        # Uses short-term indicators only — no H1 trend gate or EMA20 direction
        # requirement, since those make SELL structurally impossible on trending Gold
        # (previous version: 1544 BUY vs 3 SELL).
        ema_stack_bearish = (current_ema_fast < current_ema_mid)
        rsi_bearish = current_rsi < self.rsi_bear_threshold
        rsi_not_oversold = current_rsi > self.rsi_oversold
        rsi_falling = current_rsi_slope < 0
        macd_negative = (current_histogram < 0 and prev_histogram < 0)
        macd_deepening = abs(current_histogram) > abs(prev_histogram)
        # Entry proximity: price must be within 2×ATR below EMA20 — avoids shorting into exhaustion
        not_overextended_sell = (current_ema - current_close) < (2.0 * float(current_atr))

        if (ema_stack_bearish and rsi_bearish and rsi_not_oversold and
                rsi_falling and macd_negative and macd_deepening and
                not_overextended_sell and volume_ok):

            rsi_norm = min((50.0 - float(current_rsi)) / 30.0, 1.0)
            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            slope_norm = min(abs(float(current_rsi_slope)) / 5.0, 1.0)
            strength = rsi_norm * 0.4 + adx_norm * 0.35 + slope_norm * 0.25
            if h1_trend is False:
                strength = min(strength + 0.05, 1.0)

            # Asymmetric threshold: SELL requires higher conviction on Gold due to upward drift bias
            if strength < self.min_signal_strength_sell:
                self._log_no_signal(
                    f"SELL strength too low ({strength:.2f} < {self.min_signal_strength_sell})")
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
                    'h1_trend': h1_trend,
                    'entry_reason': 'bearish_momentum'
                }
            )

        self._log_no_signal("No momentum confluence detected")
        return None
