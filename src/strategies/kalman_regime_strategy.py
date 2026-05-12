"""
Kalman Regime-Switching Strategy v2 — Optimized for $300/day target.

Combines Kalman filter trend, realized-volatility regime detection,
and Ornstein-Uhlenbeck z-scored mean reversion with aggressive
session-based filtering and multi-indicator confirmation.

v2 Changes (data-driven from 1282-trade backtest analysis):
  - Session filter: only trade profitable hours (backtest showed hours 20-21
    averaging $19-36/trade vs hours 11-14 losing -$2 to -$7/trade)
  - EMA trend confirmation: require EMA9 > EMA21 alignment for trend mode
  - MACD momentum: histogram must confirm direction
  - Kalman acceleration: 2nd derivative check ensures trend is strengthening
  - Tighter SL (1.2 ATR) with wider TP (6.0 ATR) for better risk/reward
  - Reduced cooldown for more trade frequency during good sessions
  - Allow shorts with stronger confirmation (gold trends up but big drops are tradeable)

Signal Logic:

Trend Mode  (RV > MA(RV)):
    Long  if Close > Kalman AND EMA9 > EMA21 AND MACD hist > 0 AND Kalman accelerating up
    Short if Close < Kalman AND EMA9 < EMA21 AND MACD hist < 0 AND Kalman accelerating down
    Confirmation: ADX > threshold, session filter

Range Mode  (RV ≤ MA(RV)):
    Long  if OU Z-score < -entry_threshold AND RSI < 40 AND Stoch < 25
    Short if OU Z-score > +entry_threshold AND RSI > 60 AND Stoch > 75

Risk Management:
    Stop Loss  = sl_atr_multiplier × ATR(14)
    Take Profit = tp_atr_multiplier × ATR(14)
"""

from typing import Optional, Dict, List
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class KalmanRegimeStrategy(BaseStrategy):
    """
    Regime-switching strategy v2 — Kalman filter + RV regime + OU z-score
    with session filter, EMA/MACD confirmation, and Kalman acceleration.
    """

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Kalman parameters
        self.kalman_q = config.get('kalman_q', 1e-5)
        self.kalman_r = config.get('kalman_r', 0.01)

        # Realized volatility regime
        self.rv_window = config.get('rv_window', 20)
        self.rv_ma_window = config.get('rv_ma_window', 100)

        # OU z-score thresholds (range mode)
        self.zscore_window = config.get('zscore_window', 20)
        self.entry_threshold = config.get('entry_threshold', 2.5)

        # ATR-based risk management
        self.atr_period = config.get('atr_period', 14)

        # ADX gate for trend mode
        self.trend_adx_min = config.get('trend_adx_min', 22)

        # Kalman confirmation bars
        self.kalman_confirm_bars = config.get('kalman_confirm_bars', 2)

        # Minimum signal strength
        self.min_signal_strength = config.get('min_signal_strength', 0.50)

        # Trade cooldown
        self.cooldown_bars = config.get('cooldown_bars', 8)
        self._bars_since_signal = self.cooldown_bars  # Allow first trade immediately

        # Long-only mode
        self.long_only = config.get('long_only', False)

        # News filter
        self.news_filter_enabled = config.get('news_filter', False)
        self._news_events = None

        # ── v2 Filters ─────────────────────────────────────────────────────
        # Session filter: only trade during profitable hours
        self.session_filter_enabled = config.get('session_filter_enabled', False)
        self.allowed_sessions: List = config.get('allowed_sessions', [])

        # EMA trend confirmation
        self.ema_confirm_enabled = config.get('ema_confirm_enabled', False)
        self.ema_fast_period = config.get('ema_fast_period', 9)
        self.ema_slow_period = config.get('ema_slow_period', 21)

        # MACD momentum confirmation
        self.macd_confirmation = config.get('macd_confirmation', False)
        self.macd_fast = config.get('macd_fast', 12)
        self.macd_slow = config.get('macd_slow', 26)
        self.macd_signal_period = config.get('macd_signal', 9)

        # Kalman acceleration (2nd derivative) — require trend to be strengthening
        self.kalman_accel_enabled = config.get('kalman_accel_enabled', False)
        self.kalman_accel_bars = config.get('kalman_accel_bars', 5)

        # Range mode: Stochastic confirmation
        self.stoch_confirm_enabled = config.get('stoch_confirm_enabled', False)
        self.stoch_oversold = config.get('stoch_oversold', 25)
        self.stoch_overbought = config.get('stoch_overbought', 75)

        # RSI thresholds for range mode
        self.range_rsi_buy = config.get('range_rsi_buy', 42.0)
        self.range_rsi_sell = config.get('range_rsi_sell', 58.0)

        # Sell-side: require higher signal strength for shorts (gold upward bias)
        self.min_signal_strength_sell = config.get('min_signal_strength_sell', None)

        # HTF directional gate for SELL only.
        # Gold has a structural bullish drift; even strong same-TF SELL signals get
        # run over by the higher-TF trend. When enabled, SELL signals require the
        # higher-TF (resample target) close to sit BELOW EMA(htf_sell_ema_period).
        # BUY-side is unaffected — both directions stay live, but SELL must align
        # with a confirmed bearish HTF trend.
        self.htf_sell_filter_enabled = config.get('htf_sell_filter_enabled', False)
        self.htf_sell_resample_to = str(config.get('htf_sell_resample_to', '1h'))
        self.htf_sell_ema_period = int(config.get('htf_sell_ema_period', 50))

        # Confidence threshold (0-100). Signals with confidence >= threshold may stack
        # up to risk.max_positions; below the threshold the executor allows only one
        # concurrent kalman_regime position. Confidence is derived from signal strength.
        self.high_confidence_threshold = float(config.get('high_confidence_threshold', 90.0))

        # Minimum data required
        self.min_bars = max(self.rv_ma_window, 100) + self.rv_window + 10

        # Bump min_bars when HTF SELL filter is on so the resample has enough history.
        if self.htf_sell_filter_enabled:
            bars_per_htf = {
                '15min': 1, '15m': 1, '30min': 2, '30m': 2,
                '1h': 4, '1H': 4, '2h': 8, '2H': 8, '4h': 16, '4H': 16,
            }.get(self.htf_sell_resample_to, 4)
            self.min_bars = max(self.min_bars, (self.htf_sell_ema_period + 10) * bars_per_htf)

    def get_name(self) -> str:
        return "kalman_regime"

    def _check_session(self, bars: pd.DataFrame) -> bool:
        """Check if current bar is in an allowed trading session."""
        if not self.session_filter_enabled or not self.allowed_sessions:
            return True

        current_hour = self._get_bar_hour(bars)
        if current_hour is None:
            return True  # Cannot determine session — don't block trading

        for session in self.allowed_sessions:
            if isinstance(session, list) and len(session) == 2:
                start_h, end_h = session
                if start_h <= end_h:
                    if start_h <= current_hour <= end_h:
                        return True
                else:  # wraps midnight
                    if current_hour >= start_h or current_hour <= end_h:
                        return True
            elif isinstance(session, (int, float)):
                if current_hour == int(session):
                    return True

        return False

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """Generate regime-switching signal with v2 filters."""
        if not self.is_enabled():
            return None

        if len(bars) < self.min_bars:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {self.min_bars}")
            return None

        close = bars['close']
        current_close = float(close.iloc[-1])

        # ── 0. Cooldown check ──────────────────────────────────────────────
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars")
            return None

        # ── 0b. Session filter ─────────────────────────────────────────────
        if not self._check_session(bars):
            self._log_no_signal("Outside allowed session hours")
            return None

        # ── 1. Kalman filter trend ──────────────────────────────────────────
        kalman = Indicators.kalman_filter(close, q=self.kalman_q, r=self.kalman_r)
        current_kalman = float(kalman.iloc[-1])

        # ── 2. Realized volatility regime ──────────────────────────────────
        regime_series = Indicators.rv_regime(
            close, rv_window=self.rv_window, rv_ma_window=self.rv_ma_window
        )
        current_regime_val = int(regime_series.iloc[-1]) if not pd.isna(regime_series.iloc[-1]) else -1

        if current_regime_val == -1:
            self._log_no_signal("Regime classification unavailable (NaN)")
            return None

        is_trend = current_regime_val == 1
        regime = MarketRegime.TREND if is_trend else MarketRegime.RANGE

        # ── 3. OU z-score (for range mode) ─────────────────────────────────
        zscore = Indicators.ou_zscore(close, kalman, window=self.zscore_window)
        current_z = float(zscore.iloc[-1]) if not pd.isna(zscore.iloc[-1]) else 0.0

        # ── 4. Supporting indicators ────────────────────────────────────────
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0

        # ── 5. ATR for stop/take-profit ─────────────────────────────────────
        atr = Indicators.atr(bars, period=self.atr_period)
        current_atr = float(atr.iloc[-1])
        if current_atr <= 0 or pd.isna(current_atr):
            self._log_no_signal("ATR unavailable")
            return None

        # ── 5b. EMA trend confirmation ──────────────────────────────────────
        ema_fast_val = None
        ema_slow_val = None
        if self.ema_confirm_enabled:
            ema_fast = Indicators.ema(bars, period=self.ema_fast_period)
            ema_slow = Indicators.ema(bars, period=self.ema_slow_period)
            ema_fast_val = float(ema_fast.iloc[-1])
            ema_slow_val = float(ema_slow.iloc[-1])

        # ── 5c. MACD momentum ──────────────────────────────────────────────
        macd_hist_val = None
        if self.macd_confirmation:
            macd_line, signal_line, hist = Indicators.macd(
                bars, fast_period=self.macd_fast, slow_period=self.macd_slow, signal_period=self.macd_signal_period
            )
            macd_hist_val = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0

        # ── 5d. Stochastic (range mode) ────────────────────────────────────
        stoch_k_val = None
        if self.stoch_confirm_enabled:
            stoch_k, stoch_d = Indicators.stochastic(bars, period=14)
            stoch_k_val = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0

        # ── 6. Signal generation ────────────────────────────────────────────
        side = None
        strength = 0.0

        if is_trend:
            # ── TREND MODE ───────────────────────────────────────────────
            if current_adx < self.trend_adx_min:
                self._log_no_signal(
                    f"TREND mode: ADX too low ({current_adx:.1f} < {self.trend_adx_min})"
                )
                return None

            # Multi-bar Kalman confirmation
            close_series = bars['close']
            confirm_n = self.kalman_confirm_bars
            recent_closes = close_series.iloc[-(confirm_n + 1):-1]
            recent_kalman = kalman.iloc[-(confirm_n + 1):-1]

            price_above_kalman = current_close > current_kalman
            price_below_kalman = current_close < current_kalman

            # Kalman slope (1st derivative)
            kalman_slope = float(kalman.iloc[-1] - kalman.iloc[-3])

            # Kalman acceleration (2nd derivative) — trend strengthening
            kalman_accel_ok = True
            if self.kalman_accel_enabled and len(kalman) >= self.kalman_accel_bars + 2:
                slope_now = float(kalman.iloc[-1] - kalman.iloc[-2])
                slope_prev = float(kalman.iloc[-self.kalman_accel_bars] - kalman.iloc[-self.kalman_accel_bars - 1])
                kalman_accel = slope_now - slope_prev
                # For BUY: acceleration should be positive (trend strengthening)
                # For SELL: acceleration should be negative
                if price_above_kalman and kalman_accel < 0:
                    kalman_accel_ok = False
                elif price_below_kalman and kalman_accel > 0:
                    kalman_accel_ok = False

            if price_above_kalman:
                if not (recent_closes > recent_kalman).all():
                    self._log_no_signal(
                        f"TREND BUY: not {confirm_n} consecutive bars above Kalman")
                    return None
                if kalman_slope <= 0:
                    self._log_no_signal(
                        f"TREND BUY: Kalman slope flat/down ({kalman_slope:.4f})")
                    return None
                if not kalman_accel_ok:
                    self._log_no_signal("TREND BUY: Kalman acceleration negative (trend weakening)")
                    return None
                # EMA confirmation
                if self.ema_confirm_enabled and ema_fast_val is not None and ema_slow_val is not None:
                    if ema_fast_val <= ema_slow_val:
                        self._log_no_signal(
                            f"TREND BUY: EMA{self.ema_fast_period} ({ema_fast_val:.2f}) <= EMA{self.ema_slow_period} ({ema_slow_val:.2f})")
                        return None
                # MACD confirmation
                if self.macd_confirmation and macd_hist_val is not None:
                    if macd_hist_val <= 0:
                        self._log_no_signal(f"TREND BUY: MACD histogram negative ({macd_hist_val:.4f})")
                        return None

                side = OrderSide.BUY
                # Strength: combine Kalman distance + ADX + RSI momentum
                kalman_dist = min(abs(current_close - current_kalman) / current_atr, 1.0)
                adx_strength = min(current_adx / 50.0, 1.0)
                strength = 0.5 * kalman_dist + 0.3 * adx_strength + 0.2 * (current_rsi / 100.0)

            elif price_below_kalman:
                if not (recent_closes < recent_kalman).all():
                    self._log_no_signal(
                        f"TREND SELL: not {confirm_n} consecutive bars below Kalman")
                    return None
                if kalman_slope >= 0:
                    self._log_no_signal(
                        f"TREND SELL: Kalman slope flat/up ({kalman_slope:.4f})")
                    return None
                if not kalman_accel_ok:
                    self._log_no_signal("TREND SELL: Kalman acceleration positive (trend weakening)")
                    return None
                # EMA confirmation
                if self.ema_confirm_enabled and ema_fast_val is not None and ema_slow_val is not None:
                    if ema_fast_val >= ema_slow_val:
                        self._log_no_signal(
                            f"TREND SELL: EMA{self.ema_fast_period} ({ema_fast_val:.2f}) >= EMA{self.ema_slow_period} ({ema_slow_val:.2f})")
                        return None
                # MACD confirmation
                if self.macd_confirmation and macd_hist_val is not None:
                    if macd_hist_val >= 0:
                        self._log_no_signal(f"TREND SELL: MACD histogram positive ({macd_hist_val:.4f})")
                        return None

                side = OrderSide.SELL
                kalman_dist = min(abs(current_close - current_kalman) / current_atr, 1.0)
                adx_strength = min(current_adx / 50.0, 1.0)
                strength = 0.5 * kalman_dist + 0.3 * adx_strength + 0.2 * ((100 - current_rsi) / 100.0)

        else:
            # ── RANGE MODE (OU mean-reversion) ───────────────────────────
            if current_z < -self.entry_threshold and current_rsi < self.range_rsi_buy:
                # Stochastic confirmation for range mode
                if self.stoch_confirm_enabled and stoch_k_val is not None:
                    if stoch_k_val > self.stoch_oversold:
                        self._log_no_signal(
                            f"RANGE BUY: Stoch K ({stoch_k_val:.1f}) > {self.stoch_oversold}")
                        return None
                side = OrderSide.BUY
                strength = min(abs(current_z) / (self.entry_threshold * 1.5), 1.0)
            elif current_z > self.entry_threshold and current_rsi > self.range_rsi_sell:
                if self.stoch_confirm_enabled and stoch_k_val is not None:
                    if stoch_k_val < self.stoch_overbought:
                        self._log_no_signal(
                            f"RANGE SELL: Stoch K ({stoch_k_val:.1f}) < {self.stoch_overbought}")
                        return None
                side = OrderSide.SELL
                strength = min(abs(current_z) / (self.entry_threshold * 1.5), 1.0)

        if side is None:
            mode_str = "TREND" if is_trend else "RANGE"
            self._log_no_signal(
                f"No signal in {mode_str} mode "
                f"(close={current_close:.2f}, kalman={current_kalman:.2f}, z={current_z:.2f}, "
                f"adx={current_adx:.1f}, rsi={current_rsi:.1f})"
            )
            return None

        # Long-only gate
        if self.long_only and side == OrderSide.SELL:
            self._log_no_signal("Long-only mode: SELL signal suppressed")
            return None

        # HTF directional gate for SELL only — only fire SELL when higher-TF
        # close is below EMA(htf_sell_ema_period). Suppresses shorts that fight
        # a bullish HTF trend (the dominant XAU loss pattern).
        if self.htf_sell_filter_enabled and side == OrderSide.SELL:
            try:
                htf = bars[['open', 'high', 'low', 'close', 'volume']].resample(
                    self.htf_sell_resample_to
                ).agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum',
                }).dropna()
            except Exception:
                htf = None
            if htf is None or len(htf) < self.htf_sell_ema_period + 1:
                self._log_no_signal(
                    f"HTF SELL filter: insufficient {self.htf_sell_resample_to} bars"
                )
                return None
            htf_close = float(htf['close'].iloc[-1])
            htf_ema = float(htf['close'].ewm(span=self.htf_sell_ema_period, adjust=False).mean().iloc[-1])
            if htf_close >= htf_ema:
                self._log_no_signal(
                    f"HTF SELL filter: {self.htf_sell_resample_to} close {htf_close:.2f} "
                    f">= EMA{self.htf_sell_ema_period} {htf_ema:.2f} — bullish HTF"
                )
                return None

        # Minimum signal strength gate (different threshold for SELL if configured)
        min_strength = self.min_signal_strength
        if side == OrderSide.SELL and self.min_signal_strength_sell is not None:
            min_strength = self.min_signal_strength_sell

        if strength < min_strength:
            self._log_no_signal(
                f"Kalman signal strength too low ({strength:.2f} < {min_strength})")
            return None

        # ── 7. Emit signal ──────────────────────────────────────────────────
        self._bars_since_signal = 0  # Reset cooldown

        confidence = round(strength * 100.0, 2)

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                'strategy': 'kalman_regime',
                'mode': 'trend' if is_trend else 'range',
                'kalman': current_kalman,
                'zscore': current_z,
                'adx': current_adx,
                'rsi': current_rsi,
                'atr': current_atr,
                'confidence': confidence,
                'high_confidence_threshold': self.high_confidence_threshold,
            }
        )
