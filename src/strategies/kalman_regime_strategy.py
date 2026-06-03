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

Signal concept — LONG-ONLY deep-dip mean reversion (data-driven, 2026-06):
    A signal-research pass (information-coefficient + quintile forward-return
    study, validated in- AND out-of-sample on XAU 15m) found the Kalman's only
    robust edge: when price sits FAR BELOW the filtered level it bounces back
    toward it (~65% win, +0.55 ATR net of cost over ~3h). The mirror trades
    have NO edge — price above the level keeps rising (fading it loses) and
    shorts fight gold's structural drift. So this strategy does exactly one
    thing well:

        BUY when (level − close)/ATR ≥ dip_entry_atr,
            inside a higher-TF uptrend (catch dips, not falling knives).

    The Kalman level is the "fair value" anchor; the ATR-scaled deviation is the
    oversold trigger. Velocity is computed for context/optional gating only.

Risk: the strategy emits an entry + ATR. Stop-loss / take-profit are sized
downstream by ``RiskProcessor`` from ``sl_atr_multiplier`` / ``tp_atr_multiplier``
(floored by ``risk.kalman_min_tp_rr``), exactly as the rest of the system expects.
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

        # ── Deep-dip mean-reversion entry (the data-validated edge) ─────────
        # Signal research (2026-06, in- AND out-of-sample) showed the Kalman's
        # only robust edge on XAU 15m is LONG-ONLY deep-dip reversion: when price
        # sits far BELOW the filtered level it bounces (price ABOVE the level
        # keeps rising — fading it loses; shorts have no edge in gold's uptrend).
        # Entry fires when (level − close)/ATR ≥ dip_entry_atr.
        self.dip_entry_atr = float(config.get('dip_entry_atr', 2.0))
        self.dip_strength_scale = float(config.get('dip_strength_scale', 3.0))

        # Optional weak momentum confirmation: skip dips that are still free-
        # falling hard (very negative velocity). None disables the check.
        self.max_falling_vel = config.get('max_falling_vel', None)

        # HTF trend gate: buy dips only inside a higher-TF UPtrend; sell rallies
        # only inside a higher-TF DOWNtrend (catch reversions, not the trend).
        self.require_htf_uptrend = bool(config.get('require_htf_uptrend', True))
        self.htf_resample_to = str(config.get('htf_resample_to', '4h'))
        self.htf_ema_period = int(config.get('htf_ema_period', 50))

        # ── SELL side (symmetric rally-fade) ────────────────────────────────
        # OFF by default: the signal study found rallies above the level tend to
        # CONTINUE up (no reversion) in gold's uptrend, so shorts are structurally
        # weak. Enable to fade extreme rallies (price ≥ rally_entry_atr ABOVE the
        # level); when require_htf_uptrend is set, sells additionally require a
        # higher-TF DOWNtrend so we don't short into the dominant up-drift.
        self.enable_sells = bool(config.get('enable_sells', False))
        self.rally_entry_atr = float(config.get('rally_entry_atr', self.dip_entry_atr))

        # ── Signal gating ───────────────────────────────────────────────────
        self.min_signal_strength = float(config.get('min_signal_strength', 0.50))
        self.min_signal_strength_sell = config.get('min_signal_strength_sell', None)

        self.cooldown_bars = int(config.get('cooldown_bars', 4))
        self._bars_since_signal = self.cooldown_bars  # allow first trade immediately

        self.session_filter_enabled = bool(config.get('session_filter_enabled', False))
        self.allowed_sessions: List = config.get('allowed_sessions', [])

        # Confidence gate for the executor's signal-stacking / confidence-flip.
        self.high_confidence_threshold = float(config.get('high_confidence_threshold', 90.0))

        # Minimum history: ATR warm-up + filter burn-in + HTF resample headroom.
        self.min_bars = self.atr_period + 60
        if self.require_htf_uptrend:
            bars_per_htf = {
                '15min': 1, '15m': 1, '30min': 2, '30m': 2,
                '1h': 4, '1H': 4, '2h': 8, '2H': 8, '4h': 16, '4H': 16,
            }.get(self.htf_resample_to, 16)
            self.min_bars = max(self.min_bars, (self.htf_ema_period + 10) * bars_per_htf)

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

    def _htf_uptrend_ok(self, bars: pd.DataFrame) -> bool:
        """True if the higher-TF close sits above its EMA (bullish HTF) — only then
        do we buy dips, to avoid catching a falling knife in a real downtrend."""
        try:
            htf = bars[['open', 'high', 'low', 'close', 'volume']].resample(
                self.htf_resample_to
            ).agg({'open': 'first', 'high': 'max', 'low': 'min',
                   'close': 'last', 'volume': 'sum'}).dropna()
        except Exception:
            return False
        if len(htf) < self.htf_ema_period + 1:
            self._log_no_signal(f"HTF uptrend gate: insufficient {self.htf_resample_to} bars")
            return False
        htf_close = float(htf['close'].iloc[-1])
        htf_ema = float(htf['close'].ewm(span=self.htf_ema_period, adjust=False).mean().iloc[-1])
        if htf_close <= htf_ema:
            self._log_no_signal(
                f"HTF uptrend gate: {self.htf_resample_to} close {htf_close:.2f} "
                f"<= EMA{self.htf_ema_period} {htf_ema:.2f} — not an uptrend")
            return False
        return True

    def _htf_downtrend_ok(self, bars: pd.DataFrame) -> bool:
        """True if the higher-TF close sits below its EMA (bearish HTF) — only then
        do we fade rallies short, to avoid shorting into gold's up-drift."""
        try:
            htf = bars[['open', 'high', 'low', 'close', 'volume']].resample(
                self.htf_resample_to
            ).agg({'open': 'first', 'high': 'max', 'low': 'min',
                   'close': 'last', 'volume': 'sum'}).dropna()
        except Exception:
            return False
        if len(htf) < self.htf_ema_period + 1:
            self._log_no_signal(f"HTF downtrend gate: insufficient {self.htf_resample_to} bars")
            return False
        htf_close = float(htf['close'].iloc[-1])
        htf_ema = float(htf['close'].ewm(span=self.htf_ema_period, adjust=False).mean().iloc[-1])
        if htf_close >= htf_ema:
            self._log_no_signal(
                f"HTF downtrend gate: {self.htf_resample_to} close {htf_close:.2f} "
                f">= EMA{self.htf_ema_period} {htf_ema:.2f} — not a downtrend")
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

        # Normalized slope (ATR/bar) and level deviation (ATR units, +above/-below)
        vel_norm = velocity / current_atr
        dev_atr = (current_close - level) / current_atr

        # ── Signal: deep-dip mean reversion (long) + optional rally-fade (short) ─
        # Primary edge (in- & out-of-sample): price ≥ dip_entry_atr BELOW the
        # Kalman level bounces back toward it. The mirror short (fade rallies
        # ≥ rally_entry_atr ABOVE the level) is opt-in via enable_sells and is
        # structurally weaker in gold's up-drift, so it is HTF-downtrend gated.
        dip = -dev_atr     # ATR units BELOW the level (long zone)
        rally = dev_atr    # ATR units ABOVE the level (short zone)

        side = None
        magnitude = 0.0
        if dip >= self.dip_entry_atr:
            if self.max_falling_vel is not None and vel_norm < float(self.max_falling_vel):
                self._log_no_signal(f"Dip still free-falling (vel/atr={vel_norm:.3f})")
                return None
            if self.require_htf_uptrend and not self._htf_uptrend_ok(bars):
                return None
            side, magnitude = OrderSide.BUY, dip
        elif self.enable_sells and rally >= self.rally_entry_atr:
            if self.require_htf_uptrend and not self._htf_downtrend_ok(bars):
                return None
            side, magnitude = OrderSide.SELL, rally

        if side is None:
            self._log_no_signal(
                f"No setup (close={current_close:.2f}, level={level:.2f}, "
                f"dev/atr={dev_atr:.2f})")
            return None

        strength = min(magnitude / self.dip_strength_scale, 1.0)
        if strength < self.min_signal_strength:
            self._log_no_signal(f"Strength too low ({strength:.2f} < {self.min_signal_strength})")
            return None

        # ── Emit ────────────────────────────────────────────────────────────
        self._bars_since_signal = 0
        confidence = round(strength * 100.0, 2)
        return self._create_signal(
            side=side,
            strength=strength,
            regime=MarketRegime.RANGE,   # reversion play
            entry_price=current_close,
            metadata={
                'strategy': 'kalman_regime',
                'mode': 'dip_reversion' if side == OrderSide.BUY else 'rally_fade',
                'level': level,
                'velocity': velocity,
                'vel_norm': vel_norm,
                'dev_atr': dev_atr,
                'innov_z': innov_z,
                'atr': current_atr,
                'confidence': confidence,
                'high_confidence_threshold': self.high_confidence_threshold,
            },
        )
