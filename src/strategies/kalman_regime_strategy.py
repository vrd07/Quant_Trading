"""
Kalman Regime Strategy v3 — local-linear-trend Kalman filter on XAUUSD 15m.

Complete re-development (2026-06). The previous version stacked a scalar
price-smoother, a realized-vol regime flag, an OU z-score and a pile of
EMA/MACD/Stochastic confirmation gates. Research on Kalman-filter trading
(local-level / constant-velocity state-space models, ATR-adaptive process
noise, velocity-as-trend, innovation-as-deviation) points to a much simpler,
more principled core:

    A two-state Kalman filter that tracks BOTH the smoothed price level and its
    per-bar velocity (slope). One filter gives us everything:
      • velocity sign + magnitude  → the trend signal (low lag)
      • price − level (in ATR units) → the mean-reversion deviation
      • standardized innovation      → how surprising the latest close is

The filter's process/measurement noise are scaled by ATR², making it
scale-invariant across gold's 2,700→4,600 range and more responsive when
volatility rises. The Q/R ratio (process_scale / measurement_scale) is the
single responsiveness knob.

Regime handling:
    If the ML regime classifier has injected a regime (``self.ml_regime``) we
    honour it. Otherwise we classify rule-based from the normalized velocity:
    a strong slope ⇒ TREND, a flat slope ⇒ RANGE.

Signal logic (15m bars):
    TREND mode  — trade WITH the Kalman velocity:
        BUY  if vel/ATR > +trend_vel_atr  AND close > level  AND ADX ≥ adx_min
        SELL if vel/ATR < −trend_vel_atr  AND close < level  AND ADX ≥ adx_min
    RANGE mode  — fade deviation from the Kalman level:
        BUY  if (close−level)/ATR < −range_entry_atr  AND slope ~flat
        SELL if (close−level)/ATR > +range_entry_atr  AND slope ~flat

Risk: the strategy only emits an entry + ATR. Stop-loss / take-profit are sized
downstream by ``RiskProcessor`` from ``sl_atr_multiplier`` / ``tp_atr_multiplier``
(floored by ``risk.kalman_min_tp_rr``), exactly as the rest of the system expects.

Gold has a structural bullish drift, so the proven directional gates are kept as
optional config: ``long_only``, an HTF SELL filter, a session filter and a
per-side strength floor.
"""

from typing import Optional, List
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class KalmanRegimeStrategy(BaseStrategy):
    """Local-linear-trend Kalman strategy (trend-follow + range-fade) for 15m XAUUSD."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # ── Kalman filter (2-state level+velocity) ──────────────────────────
        # process_scale / measurement_scale set the Q/R ratio. Higher
        # process_scale ⇒ more responsive (less lag, more noise).
        self.process_scale = float(config.get('process_scale', 1e-3))
        self.measurement_scale = float(config.get('measurement_scale', 1.0))
        # Window fed to the recursive filter. It converges within ~100 bars, so
        # only the recent tail matters — capping it keeps the per-bar cost flat
        # (the backtest re-runs the filter on every bar) without changing the
        # latest estimate. Must comfortably exceed convergence + ATR warm-up.
        self.kalman_window = int(config.get('kalman_window', 300))

        # ── ATR (drives noise scaling AND stop sizing) ──────────────────────
        self.atr_period = int(config.get('atr_period', 14))

        # ── Regime classification (rule-based fallback when no ML regime) ───
        # |velocity / ATR| above this ⇒ TREND, below ⇒ RANGE.
        self.regime_vel_atr = float(config.get('regime_vel_atr', 0.05))

        # ── TREND-mode entry ────────────────────────────────────────────────
        self.trend_vel_atr = float(config.get('trend_vel_atr', 0.05))
        self.trend_adx_min = float(config.get('trend_adx_min', 20))
        self.vel_strength_scale = float(config.get('vel_strength_scale', 0.20))

        # ── RANGE-mode entry ────────────────────────────────────────────────
        self.range_entry_atr = float(config.get('range_entry_atr', 1.0))
        self.range_strength_scale = float(config.get('range_strength_scale', 2.5))

        # ── Signal gating ───────────────────────────────────────────────────
        self.min_signal_strength = float(config.get('min_signal_strength', 0.50))
        self.min_signal_strength_sell = config.get('min_signal_strength_sell', None)

        self.cooldown_bars = int(config.get('cooldown_bars', 4))
        self._bars_since_signal = self.cooldown_bars  # allow first trade immediately

        # ── Directional / session gates (gold bullish bias) ─────────────────
        self.long_only = bool(config.get('long_only', False))

        self.session_filter_enabled = bool(config.get('session_filter_enabled', False))
        self.allowed_sessions: List = config.get('allowed_sessions', [])

        # HTF SELL filter: only allow shorts when the higher-TF close is below
        # its EMA, i.e. the bigger trend is actually bearish. Suppresses shorts
        # that fight gold's structural uptrend (the dominant XAU loss pattern).
        self.htf_sell_filter_enabled = bool(config.get('htf_sell_filter_enabled', False))
        self.htf_sell_resample_to = str(config.get('htf_sell_resample_to', '1h'))
        self.htf_sell_ema_period = int(config.get('htf_sell_ema_period', 50))

        # Confidence gate for the executor's signal-stacking / confidence-flip.
        self.high_confidence_threshold = float(config.get('high_confidence_threshold', 90.0))

        # Minimum history: ATR warm-up + filter burn-in + HTF resample headroom.
        self.min_bars = self.atr_period + 60
        if self.htf_sell_filter_enabled:
            bars_per_htf = {
                '15min': 1, '15m': 1, '30min': 2, '30m': 2,
                '1h': 4, '1H': 4, '2h': 8, '2H': 8, '4h': 16, '4H': 16,
            }.get(self.htf_sell_resample_to, 4)
            self.min_bars = max(self.min_bars, (self.htf_sell_ema_period + 10) * bars_per_htf)

    def get_name(self) -> str:
        return "kalman_regime"

    # ------------------------------------------------------------------ gates
    def _check_session(self, bars: pd.DataFrame) -> bool:
        """True if the current bar's UTC hour is inside an allowed session."""
        if not self.session_filter_enabled or not self.allowed_sessions:
            return True
        current_hour = self._get_bar_hour(bars)
        if current_hour is None:
            return True
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

    def _htf_sell_ok(self, bars: pd.DataFrame) -> bool:
        """True if a SELL is allowed: higher-TF close sits below its EMA (bearish HTF)."""
        try:
            htf = bars[['open', 'high', 'low', 'close', 'volume']].resample(
                self.htf_sell_resample_to
            ).agg({'open': 'first', 'high': 'max', 'low': 'min',
                   'close': 'last', 'volume': 'sum'}).dropna()
        except Exception:
            return False
        if len(htf) < self.htf_sell_ema_period + 1:
            self._log_no_signal(f"HTF SELL filter: insufficient {self.htf_sell_resample_to} bars")
            return False
        htf_close = float(htf['close'].iloc[-1])
        htf_ema = float(htf['close'].ewm(span=self.htf_sell_ema_period, adjust=False).mean().iloc[-1])
        if htf_close >= htf_ema:
            self._log_no_signal(
                f"HTF SELL filter: {self.htf_sell_resample_to} close {htf_close:.2f} "
                f">= EMA{self.htf_sell_ema_period} {htf_ema:.2f} — bullish HTF")
            return False
        return True

    # ------------------------------------------------------------------- core
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None
        if len(bars) < self.min_bars:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {self.min_bars}")
            return None

        # Cooldown
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars")
            return None

        # Session
        if not self._check_session(bars):
            self._log_no_signal("Outside allowed session hours")
            return None

        close = bars['close']
        current_close = float(close.iloc[-1])

        # ATR (drives both noise scaling and stop sizing)
        atr = Indicators.atr(bars, period=self.atr_period)
        current_atr = float(atr.iloc[-1])
        if current_atr <= 0 or pd.isna(current_atr):
            self._log_no_signal("ATR unavailable")
            return None

        # Two-state Kalman: level + velocity + standardized innovation.
        # Feed only the recent tail — the recursive filter has converged long
        # before then, and we only read the latest value.
        w = self.kalman_window
        kf = Indicators.local_trend_kalman(
            close.iloc[-w:], atr.iloc[-w:],
            process_scale=self.process_scale,
            measurement_scale=self.measurement_scale,
        )
        level = float(kf['level'].iloc[-1])
        velocity = float(kf['velocity'].iloc[-1])
        innov_z = float(kf['innov_z'].iloc[-1])
        if pd.isna(level) or pd.isna(velocity):
            self._log_no_signal("Kalman estimate unavailable (NaN)")
            return None

        # Normalized slope (ATR units per bar) and level deviation (ATR units)
        vel_norm = velocity / current_atr
        dev_atr = (current_close - level) / current_atr

        # ADX for trend confirmation
        adx = Indicators.adx(bars, period=14)
        current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0

        # ── Regime: honour ML override, else classify from slope strength ───
        if self.ml_regime in (MarketRegime.TREND, MarketRegime.RANGE):
            is_trend = self.ml_regime == MarketRegime.TREND
        else:
            is_trend = abs(vel_norm) >= self.regime_vel_atr
        regime = MarketRegime.TREND if is_trend else MarketRegime.RANGE

        # ── Signal generation ───────────────────────────────────────────────
        side = None
        strength = 0.0

        if is_trend:
            if current_adx < self.trend_adx_min:
                self._log_no_signal(f"TREND: ADX too low ({current_adx:.1f} < {self.trend_adx_min})")
                return None
            if vel_norm > self.trend_vel_atr and current_close > level:
                side = OrderSide.BUY
            elif vel_norm < -self.trend_vel_atr and current_close < level:
                side = OrderSide.SELL
            if side is not None:
                vel_str = min(abs(vel_norm) / self.vel_strength_scale, 1.0)
                adx_str = min(current_adx / 50.0, 1.0)
                strength = 0.7 * vel_str + 0.3 * adx_str
        else:
            # RANGE: fade deviation from the Kalman level, only when slope is flat
            if abs(vel_norm) < self.regime_vel_atr:
                if dev_atr < -self.range_entry_atr:
                    side = OrderSide.BUY
                elif dev_atr > self.range_entry_atr:
                    side = OrderSide.SELL
                if side is not None:
                    strength = min(abs(dev_atr) / self.range_strength_scale, 1.0)

        if side is None:
            self._log_no_signal(
                f"No signal in {'TREND' if is_trend else 'RANGE'} "
                f"(close={current_close:.2f}, level={level:.2f}, vel/atr={vel_norm:.3f}, "
                f"dev/atr={dev_atr:.2f}, adx={current_adx:.1f})")
            return None

        # ── Directional gates ───────────────────────────────────────────────
        if self.long_only and side == OrderSide.SELL:
            self._log_no_signal("Long-only mode: SELL suppressed")
            return None
        if self.htf_sell_filter_enabled and side == OrderSide.SELL and not self._htf_sell_ok(bars):
            return None

        # ── Strength floor (per-side) ───────────────────────────────────────
        min_strength = self.min_signal_strength
        if side == OrderSide.SELL and self.min_signal_strength_sell is not None:
            min_strength = float(self.min_signal_strength_sell)
        if strength < min_strength:
            self._log_no_signal(f"Strength too low ({strength:.2f} < {min_strength})")
            return None

        # ── Emit ────────────────────────────────────────────────────────────
        self._bars_since_signal = 0
        confidence = round(strength * 100.0, 2)
        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                'strategy': 'kalman_regime',
                'mode': 'trend' if is_trend else 'range',
                'level': level,
                'velocity': velocity,
                'vel_norm': vel_norm,
                'dev_atr': dev_atr,
                'innov_z': innov_z,
                'adx': current_adx,
                'atr': current_atr,
                'confidence': confidence,
                'high_confidence_threshold': self.high_confidence_threshold,
            },
        )
