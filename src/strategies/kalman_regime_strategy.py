"""
Kalman Regime-Switching Strategy.

Combines Kalman filter trend, realized-volatility regime detection,
and Ornstein-Uhlenbeck z-scored mean reversion into a single strategy.

Signal Logic (from Instruct.md):

Trend Mode  (RV > MA(RV)):
    Long   if Close > Kalman  (price above adaptive trend)
    Short  if Close < Kalman  (price below adaptive trend)
    Confirmation: light ADX > 15 to avoid flat/choppy markets

Range Mode  (RV ≤ MA(RV)):
    Long   if OU Z-score < -entry_threshold  (oversold — confirmed by RSI < 45)
    Short  if OU Z-score > +entry_threshold  (overbought — confirmed by RSI > 55)

Risk Management:
    Stop Loss  = sl_atr_multiplier × ATR(14)   (default 2.5)
    Take Profit = tp_atr_multiplier × ATR(14)  (default 2.0)

Optional:
    - News filter blackout (ForexFactory)
    - Multi-timeframe confirmation
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class KalmanRegimeStrategy(BaseStrategy):
    """
    Regime-switching strategy using Kalman filter + RV regime + OU z-score.

    Adapts between trend-following and mean-reversion based on
    realized volatility regime classification.

    Trend mode: Follow the Kalman adaptive trend (Close > Kalman → BUY).
    Range mode: Mean-revert on OU z-score extremes.
    """

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Kalman parameters
        self.kalman_q = config.get('kalman_q', 1e-5)
        self.kalman_r = config.get('kalman_r', 0.01)

        # Realized volatility regime
        self.rv_window = config.get('rv_window', 20)
        self.rv_ma_window = config.get('rv_ma_window', 100)

        # OU z-score thresholds (range mode) — raised to 2.8 to require genuine extremes
        self.zscore_window = config.get('zscore_window', 20)
        self.entry_threshold = config.get('entry_threshold', 2.8)

        # ATR-based risk management delegates to RiskProcessor
        self.atr_period = config.get('atr_period', 14)

        # ADX gate raised to 25 — eliminates borderline trend signals (ADX 22-25 are noisy)
        self.trend_adx_min = config.get('trend_adx_min', 25)

        # Require N consecutive bars on the correct side of Kalman before entry
        self.kalman_confirm_bars = config.get('kalman_confirm_bars', 2)

        # Minimum signal strength to emit a signal
        self.min_signal_strength = config.get('min_signal_strength', 0.50)

        # News filter (optional)
        self.news_filter_enabled = config.get('news_filter', False)
        self._news_events = None

        # Minimum data required
        self.min_bars = max(self.rv_ma_window, 100) + self.rv_window + 10

    def get_name(self) -> str:
        return "kalman_regime"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """Generate regime-switching signal per Instruct.md specification."""
        if not self.is_enabled():
            return None

        if len(bars) < self.min_bars:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {self.min_bars}")
            return None

        close = bars['close']
        current_close = float(close.iloc[-1])

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

        # ── 6. Signal generation ────────────────────────────────────────────
        side = None
        strength = 0.0

        if is_trend:
            # ── TREND MODE ───────────────────────────────────────────────
            # Core rule: Close > Kalman → BUY, Close < Kalman → SELL
            # ADX gate: avoid flat/dead markets (raised to 25)
            if current_adx < self.trend_adx_min:
                self._log_no_signal(
                    f"TREND mode: ADX too low ({current_adx:.1f} < {self.trend_adx_min})"
                )
                return None

            # Multi-bar Kalman confirmation: require N consecutive bars on correct side
            # This avoids triggering on a single noisy crossover bar
            close_series = bars['close']
            kalman_full = Indicators.kalman_filter(close_series, q=self.kalman_q, r=self.kalman_r)
            confirm_n = self.kalman_confirm_bars
            recent_closes = close_series.iloc[-(confirm_n + 1):-1]   # last N bars (exc. current)
            recent_kalman = kalman_full.iloc[-(confirm_n + 1):-1]

            price_above_kalman = current_close > current_kalman
            price_below_kalman = current_close < current_kalman

            # Kalman slope gate: filter signals where trend is flattening
            # Slope = difference of last 3 Kalman values
            kalman_slope = float(kalman_full.iloc[-1] - kalman_full.iloc[-3])

            if price_above_kalman:
                # All recent bars must also have been above Kalman
                if not (recent_closes > recent_kalman).all():
                    self._log_no_signal(
                        f"TREND BUY: not {confirm_n} consecutive bars above Kalman")
                    return None
                # Require Kalman sloping upward (trend not flattening)
                if kalman_slope <= 0:
                    self._log_no_signal(
                        f"TREND BUY: Kalman slope flat/down ({kalman_slope:.4f}), skipping")
                    return None
                side = OrderSide.BUY
                strength = min(abs(current_close - current_kalman) / current_atr, 1.0)
            elif price_below_kalman:
                if not (recent_closes < recent_kalman).all():
                    self._log_no_signal(
                        f"TREND SELL: not {confirm_n} consecutive bars below Kalman")
                    return None
                # Require Kalman sloping downward
                if kalman_slope >= 0:
                    self._log_no_signal(
                        f"TREND SELL: Kalman slope flat/up ({kalman_slope:.4f}), skipping")
                    return None
                side = OrderSide.SELL
                strength = min(abs(current_close - current_kalman) / current_atr, 1.0)

        else:
            # ── RANGE MODE (OU mean-reversion) ───────────────────────────
            # Core rule: Z < -threshold → BUY (oversold), Z > +threshold → SELL (overbought)
            # RSI confirmation tightened: < 42 (BUY) and > 58 (SELL) for genuine extremes
            if current_z < -self.entry_threshold and current_rsi < 42.0:
                side = OrderSide.BUY
                strength = min(abs(current_z) / (self.entry_threshold * 1.5), 1.0)
            elif current_z > self.entry_threshold and current_rsi > 58.0:
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

        # Minimum signal strength gate
        if strength < self.min_signal_strength:
            self._log_no_signal(
                f"Kalman signal strength too low ({strength:.2f} < {self.min_signal_strength})")
            return None

        # ── 7. Delegation ───────────────────────────────────────────────────
        # SL/TP is managed by RiskProcessor based on metadata parameters

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
                'atr': current_atr
            }
        )
