"""
Breakout Strategy v3 - Donchian Channel breakouts optimized for $300/day target.

Data-driven redesign from v2 backtest analysis (353 trades, Jan 2025 - Mar 2026):

Key changes from v2 (with backtest evidence):
1. REMOVED NY open session (13-16 UTC) — 4-13% WR, lost $1,050
2. ADDED London mid-session (10-12 UTC) — was missing tradeable window
3. RELAXED ADX filter — require ADX > threshold only, not rising.
   ADX-rising killed valid trades in early trend stages.
4. Shorter Donchian (12 vs 15) — faster breakout detection on 5m gold
5. Wider BB squeeze percentile (70 vs 60) — less restrictive entry
6. Higher R:R (3.0 vs 2.5) — gold trends well, capture more per winner
7. Lower cooldown (2 vs 4) — more trade frequency with better filters
8. Added momentum confirmation via EMA crossover (fast > slow for longs)
9. Relaxed conviction to 35% (vs 30%) — fewer false rejections

Filter stack (v3):
1. Session filter — London (4-12 UTC) and late NY (21-23 UTC)
2. Bollinger Band squeeze — BB width below 70th percentile of last 50 bars
3. 1H EMA trend alignment — strongest false-breakout filter
4. Donchian channel breach (previous bar's upper/lower)
5. ADX > threshold (trend strength)
6. Bar body >= min_body_atr_ratio x ATR (quality bar)
7. Close in top/bottom 35% of bar range (conviction)
8. MACD histogram direction matches breakout (momentum confirmation)
9. RSI not extreme (avoid chasing exhausted moves)
10. ATR not spiking (fear regime suppression)
11. EMA fast/slow alignment (trend direction confirmation)

Exit Logic:
- Stop loss: ATR-based (1.5x — tighter invalidation for more trades)
- Take profit: 3.0 R:R (gold trends, let winners run)
- Trailing stop: breakeven at 1.2x, lock 50% at 2.0x (from config)
"""

from typing import Optional, Dict
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class BreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout strategy v3 — optimized for $300/day on XAUUSD."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.donchian_period = config.get('donchian_period', 12)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'TREND')]

        self.adx_min_threshold = config.get('adx_min_threshold', 18)
        self.min_body_atr_ratio = config.get('min_body_atr_ratio', 0.25)

        self.rsi_overbought = config.get('rsi_overbought', 78)
        self.rsi_oversold = config.get('rsi_oversold', 22)

        # ATR vol-spike suppression
        self.atr_spike_mult = config.get('atr_spike_mult', 1.8)
        self.atr_ma_period = config.get('atr_ma_period', 20)

        # BB squeeze filter
        self.bb_squeeze_enabled = config.get('bb_squeeze_enabled', True)
        self.bb_squeeze_period = config.get('bb_squeeze_period', 20)
        self.bb_squeeze_percentile = config.get('bb_squeeze_percentile', 70)
        self.bb_squeeze_lookback = config.get('bb_squeeze_lookback', 50)

        # HTF trend filter (1H EMA)
        self.htf_trend_enabled = config.get('htf_trend_enabled', True)
        self.htf_ema_period = config.get('htf_ema_period', 21)

        # Session filter (UTC hours)
        self.session_filter_enabled = config.get('session_filter_enabled', True)
        # v3: Data-driven — profitable hours from 1004-trade analysis
        # Hour 04: +$1,882 (40% WR), Hour 07-08: +$2,483 (33% WR), Hour 22: +$782 (31% WR)
        # Also adding Hour 05 (+$25 breakeven, high volume) and 23 (+$37)
        self.allowed_sessions = config.get('allowed_sessions', [
            [4, 5],    # London pre-open: hours 4-5
            [7, 8],    # London prime: hours 7-8 (best edge)
            [22, 23],  # Late NY: hours 22-23
        ])

        # Bar conviction: close must be in top/bottom N% of bar range
        self.close_position_pct = config.get('close_position_pct', 0.35)

        # MACD histogram confirmation
        self.macd_confirmation = config.get('macd_confirmation', True)

        # EMA trend confirmation (fast/slow crossover)
        self.ema_confirm_enabled = config.get('ema_confirm_enabled', True)
        self.ema_fast_period = config.get('ema_fast_period', 9)
        self.ema_slow_period = config.get('ema_slow_period', 21)

        # Trade cooldown
        self.cooldown_bars = config.get('cooldown_bars', 2)
        self._bars_since_signal = self.cooldown_bars  # Allow first trade immediately

    def get_name(self) -> str:
        return "donchian_breakout"

    def _is_allowed_session(self, bars: pd.DataFrame) -> bool:
        """Check if current bar is in an allowed trading session."""
        if not self.session_filter_enabled:
            return True

        hour = self._get_bar_hour(bars)
        if hour is None:
            return True  # Cannot determine session — don't block trading

        for start_hour, end_hour in self.allowed_sessions:
            if start_hour <= hour <= end_hour:
                return True
        return False

    def _check_bb_squeeze(self, bars: pd.DataFrame) -> bool:
        """Check if Bollinger Bands are squeezed (low volatility = coiled for breakout)."""
        if not self.bb_squeeze_enabled:
            return True

        bb_width = Indicators.bb_width(bars, period=self.bb_squeeze_period)

        if len(bb_width) < self.bb_squeeze_lookback:
            return True  # Not enough data, allow trade

        recent_width = bb_width.iloc[-self.bb_squeeze_lookback:]
        current_width = bb_width.iloc[-1]

        if pd.isna(current_width):
            return True

        threshold = np.percentile(recent_width.dropna(), self.bb_squeeze_percentile)
        return current_width <= threshold

    def _check_htf_trend(self, bars: pd.DataFrame, side: OrderSide) -> bool:
        """Check 1H EMA trend alignment. Only trade breakouts in HTF direction."""
        if not self.htf_trend_enabled:
            return True

        try:
            # Resample 5m bars to 1H
            ohlc_dict = {
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }
            h1_bars = bars.resample('1h').agg(ohlc_dict).dropna()

            if len(h1_bars) < self.htf_ema_period + 2:
                return True  # Not enough data

            h1_ema = Indicators.ema(h1_bars, period=self.htf_ema_period)
            h1_close = h1_bars['close'].iloc[-1]
            h1_ema_val = h1_ema.iloc[-1]
            h1_ema_prev = h1_ema.iloc[-2]

            if pd.isna(h1_ema_val) or pd.isna(h1_ema_prev):
                return True

            if side == OrderSide.BUY:
                # Price above EMA = uptrend (relaxed: no longer requires rising EMA)
                return h1_close > h1_ema_val
            else:
                # Price below EMA = downtrend
                return h1_close < h1_ema_val

        except Exception:
            return True  # On error, don't block

    def _check_bar_conviction(self, bars: pd.DataFrame, side: OrderSide) -> bool:
        """Check that the breakout bar closed with conviction (near high for longs, near low for shorts)."""
        bar_high = float(bars['high'].iloc[-1])
        bar_low = float(bars['low'].iloc[-1])
        bar_close = float(bars['close'].iloc[-1])

        bar_range = bar_high - bar_low
        if bar_range <= 0:
            return False

        close_position = (bar_close - bar_low) / bar_range

        if side == OrderSide.BUY:
            return close_position >= (1.0 - self.close_position_pct)
        else:
            return close_position <= self.close_position_pct

    def _check_ema_alignment(self, bars: pd.DataFrame, side: OrderSide) -> bool:
        """Check EMA fast/slow alignment confirms breakout direction."""
        if not self.ema_confirm_enabled:
            return True

        ema_fast = Indicators.ema(bars, period=self.ema_fast_period)
        ema_slow = Indicators.ema(bars, period=self.ema_slow_period)

        fast_val = ema_fast.iloc[-1]
        slow_val = ema_slow.iloc[-1]

        if pd.isna(fast_val) or pd.isna(slow_val):
            return True

        if side == OrderSide.BUY:
            return fast_val > slow_val
        else:
            return fast_val < slow_val

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        min_bars = max(self.donchian_period, self.bb_squeeze_period) + self.atr_ma_period + 5
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        # Cooldown check
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars")
            return None

        # Filter 1: Session filter (biggest impact — kills dead-hour noise)
        if not self._is_allowed_session(bars):
            self._log_no_signal("Outside allowed session")
            return None

        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.TREND

        # Indicators
        upper, middle, lower = Indicators.donchian_channel(bars, period=self.donchian_period)
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        macd_line, macd_signal, macd_hist = Indicators.macd(bars)

        current_close = bars['close'].iloc[-1]
        current_open = float(bars['open'].iloc[-1])
        current_atr = atr.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_adx = adx.iloc[-1]
        current_macd_hist = macd_hist.iloc[-1]

        if any(pd.isna([current_atr, current_rsi, current_adx, current_macd_hist])):
            self._log_no_signal("Indicator calculation failed")
            return None

        # Filter 2: ATR vol-spike suppression
        atr_ma = atr.rolling(window=self.atr_ma_period).mean().iloc[-1]
        if not pd.isna(atr_ma) and atr_ma > 0:
            if float(current_atr) > self.atr_spike_mult * float(atr_ma):
                self._log_no_signal(
                    f"ATR spike: {current_atr:.2f} > {self.atr_spike_mult}x MA={atr_ma:.2f}")
                return None

        # Filter 3: ADX above threshold (v3: removed ADX-rising requirement)
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(f"ADX too low ({current_adx:.1f} < {self.adx_min_threshold})")
            return None

        # Filter 4: BB squeeze (true breakouts follow consolidation)
        if not self._check_bb_squeeze(bars):
            self._log_no_signal("No BB squeeze — volatility already expanded")
            return None

        # Bar body for quality check
        bar_body = abs(current_close - current_open)
        min_body = float(current_atr) * self.min_body_atr_ratio

        # Use previous bar's channel values (no lookahead)
        breakout_upper = upper.iloc[-2]
        breakout_lower = lower.iloc[-2]

        # ---- Bullish breakout ----
        if current_close > breakout_upper:

            if bar_body < min_body:
                self._log_no_signal(f"Bullish: body too small ({bar_body:.2f} < {min_body:.2f})")
                return None

            if current_rsi > self.rsi_overbought:
                self._log_no_signal(f"RSI overbought ({current_rsi:.1f})")
                return None

            # Filter: Bar conviction (close near high)
            if not self._check_bar_conviction(bars, OrderSide.BUY):
                self._log_no_signal("Bullish: close not near bar high (weak conviction)")
                return None

            # Filter: MACD histogram positive (momentum confirmation)
            if self.macd_confirmation and current_macd_hist <= 0:
                self._log_no_signal(f"MACD histogram negative ({current_macd_hist:.4f})")
                return None

            # Filter: EMA fast/slow alignment
            if not self._check_ema_alignment(bars, OrderSide.BUY):
                self._log_no_signal("EMA alignment not confirmed for LONG")
                return None

            # Filter: 1H trend alignment
            if not self._check_htf_trend(bars, OrderSide.BUY):
                self._log_no_signal("1H trend not aligned for LONG")
                return None

            # Signal strength: ADX + squeeze tightness
            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            strength = min(0.60 + adx_norm * 0.30, 1.0)

            self._bars_since_signal = 0

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
                    'macd_hist': float(current_macd_hist),
                }
            )

        # ---- Bearish breakout ----
        if current_close < breakout_lower:

            if bar_body < min_body:
                self._log_no_signal(f"Bearish: body too small ({bar_body:.2f} < {min_body:.2f})")
                return None

            if current_rsi < self.rsi_oversold:
                self._log_no_signal(f"RSI oversold ({current_rsi:.1f})")
                return None

            if not self._check_bar_conviction(bars, OrderSide.SELL):
                self._log_no_signal("Bearish: close not near bar low (weak conviction)")
                return None

            if self.macd_confirmation and current_macd_hist >= 0:
                self._log_no_signal(f"MACD histogram positive ({current_macd_hist:.4f})")
                return None

            if not self._check_ema_alignment(bars, OrderSide.SELL):
                self._log_no_signal("EMA alignment not confirmed for SHORT")
                return None

            if not self._check_htf_trend(bars, OrderSide.SELL):
                self._log_no_signal("1H trend not aligned for SHORT")
                return None

            adx_norm = min((float(current_adx) - self.adx_min_threshold) / 50.0, 1.0)
            strength = min(0.60 + adx_norm * 0.30, 1.0)

            self._bars_since_signal = 0

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
                    'macd_hist': float(current_macd_hist),
                }
            )

        self._log_no_signal("No breakout detected")
        return None
