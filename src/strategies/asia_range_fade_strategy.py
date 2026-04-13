"""
Asia Range Fade Strategy — XAUUSD late-Asia / London-lunch mean reversion.

Motivation
----------
The existing strategy lineup has almost no activity during UTC 09–14
(14:30–19:30 IST). That window sits between Tokyo's close and the NY
open — XAUUSD tends to chop in a low-volatility range. This strategy
fades the extremes of that range when volatility is compressed and
oscillators are stretched.

Entry logic (LONG mirror for SHORT)
-----------------------------------
1. Clock gate:  current bar's UTC hour in session_hours.
2. Range regime gate (all must hold):
   - ADX(14)          <  adx_max
   - BB width (/close) below bb_width_percentile cutoff over a rolling
     look-back window (true volatility compression, not noise-level).
3. Extreme touch:
   - close <= rolling_low(lookback_bars)    (long)
   - close >= rolling_high(lookback_bars)   (short)
4. Oscillator confirmation:
   - RSI(14) <= rsi_oversold                (long)
   - RSI(14) >= rsi_overbought              (short)
5. Bollinger confirmation:
   - close <= lower_band (long)  /  close >= upper_band (short)
6. Cooldown (bars) between entries to avoid stacking into one move.

Exit (advisory — RiskEngine enforces)
-------------------------------------
- Stop loss:   atr_stop_multiplier × ATR beyond the touched extreme.
- Take profit: rr_ratio × SL distance (target ≈ session VWAP).

This strategy is deliberately quiet. Expect 0–3 signals per day on
XAUUSD 5m. It is meant to *fill* the dead IST window, not replace
any existing strategy.
"""

from typing import Optional
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class AsiaRangeFadeStrategy(BaseStrategy):
    """Range fade during UTC 09–14 (low-vol XAUUSD window)."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.session_hours = set(config.get('session_hours', [9, 10, 11, 12, 13]))
        self.lookback_bars = int(config.get('lookback_bars', 20))

        self.adx_max = float(config.get('adx_max', 22.0))
        self.bb_period = int(config.get('bb_period', 20))
        self.bb_width_window = int(config.get('bb_width_window', 200))
        self.bb_width_percentile = float(config.get('bb_width_percentile', 0.35))

        self.rsi_period = int(config.get('rsi_period', 14))
        self.rsi_oversold = float(config.get('rsi_oversold', 30.0))
        self.rsi_overbought = float(config.get('rsi_overbought', 70.0))

        self.atr_period = int(config.get('atr_period', 14))
        self.atr_stop_multiplier = float(config.get('atr_stop_multiplier', 1.5))
        self.rr_ratio = float(config.get('rr_ratio', 1.2))

        self.cooldown_bars = int(config.get('cooldown_bars', 12))
        self.long_only = bool(config.get('long_only', False))

        # Block entries entirely when ML regime says TREND
        # (rule-based entry would still be safe, but we defer to ML when present)
        self.respect_ml_trend = bool(config.get('respect_ml_trend', True))

        # Track cooldown by the last signal's bar timestamp (not by positional
        # index, because in live/backtest the bar window is rolling-capped and
        # positional indices don't strictly increase across calls).
        self._last_signal_ts = None

    def get_name(self) -> str:
        return "asia_range_fade"

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        min_required = max(self.lookback_bars, self.bb_width_window, 100) + 5
        if len(bars) < min_required:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {min_required}")
            return None

        # ── 1. Session clock gate ─────────────────────────────────────
        hour = self._get_bar_hour(bars)
        if hour is None:
            self._log_no_signal("Bar index lacks timestamp — cannot verify session")
            return None
        if hour not in self.session_hours:
            self._log_no_signal(f"Outside session hours (UTC hour={hour})")
            return None

        # ── 2. ML regime guard (optional) ─────────────────────────────
        if self.respect_ml_trend and self.ml_regime == MarketRegime.TREND:
            self._log_no_signal("ML regime=TREND — skipping range fade")
            return None

        # ── 3. Cooldown (timestamp-based) ─────────────────────────────
        current_ts = bars.index[-1]
        if self._last_signal_ts is not None:
            try:
                bars_since = int((current_ts - self._last_signal_ts).total_seconds() // 300)
            except Exception:
                bars_since = self.cooldown_bars  # fallback — don't block forever
            if bars_since < self.cooldown_bars:
                self._log_no_signal(f"Cooldown: {self.cooldown_bars - bars_since} bars left")
                return None

        # ── 4. Volatility compression gate ────────────────────────────
        adx = Indicators.adx(bars, period=14)
        current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 25.0
        if current_adx >= self.adx_max:
            self._log_no_signal(f"ADX too high ({current_adx:.1f} >= {self.adx_max})")
            return None

        bb_upper, bb_mid, bb_lower = Indicators.bollinger_bands(
            bars, period=self.bb_period, num_std=2.0
        )
        # BB width normalised by middle band — scale-free volatility proxy
        bb_width = (bb_upper - bb_lower) / bb_mid.replace(0, pd.NA)
        bb_width = bb_width.astype(float)
        recent_width = bb_width.iloc[-self.bb_width_window:]
        if recent_width.isna().all():
            self._log_no_signal("BB width calc failed")
            return None
        width_threshold = float(recent_width.quantile(self.bb_width_percentile))
        current_width = float(bb_width.iloc[-1])
        if pd.isna(current_width) or current_width > width_threshold:
            self._log_no_signal(
                f"Volatility not compressed (width {current_width:.5f} > p{int(self.bb_width_percentile*100)} "
                f"{width_threshold:.5f})"
            )
            return None

        # ── 5. Level touch + oscillator ───────────────────────────────
        current_close = float(bars['close'].iloc[-1])
        rolling_high = float(bars['high'].iloc[-(self.lookback_bars + 1):-1].max())
        rolling_low = float(bars['low'].iloc[-(self.lookback_bars + 1):-1].min())

        rsi = Indicators.rsi(bars, period=self.rsi_period)
        current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        atr = Indicators.atr(bars, period=self.atr_period)
        current_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else current_close * 0.002
        if current_atr <= 0:
            self._log_no_signal("ATR invalid")
            return None

        current_bb_lower = float(bb_lower.iloc[-1])
        current_bb_upper = float(bb_upper.iloc[-1])

        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.RANGE

        # ── LONG fade ─────────────────────────────────────────────────
        # Entry = BB-lower touch + RSI oversold (BB already encodes local extreme).
        # Rolling-low is advisory for stop placement only.
        if current_close <= current_bb_lower:
            if current_rsi > self.rsi_oversold:
                self._log_no_signal(
                    f"Long touch but RSI not oversold ({current_rsi:.1f} > {self.rsi_oversold})"
                )
                return None

            entry = current_close
            stop = rolling_low - self.atr_stop_multiplier * current_atr
            target = entry + self.rr_ratio * (entry - stop)
            self._last_signal_ts = current_ts

            strength = min(1.0, (self.rsi_oversold - current_rsi) / self.rsi_oversold + 0.5)
            return self._create_signal(
                side=OrderSide.BUY,
                strength=float(max(0.5, min(1.0, strength))),
                regime=regime,
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                metadata={
                    'rolling_low': rolling_low,
                    'rolling_high': rolling_high,
                    'rsi': current_rsi,
                    'adx': current_adx,
                    'bb_width': current_width,
                    'bb_width_threshold': width_threshold,
                    'atr': current_atr,
                    'session_hour_utc': hour,
                }
            )

        # ── SHORT fade ────────────────────────────────────────────────
        if not self.long_only and current_close >= current_bb_upper:
            if current_rsi < self.rsi_overbought:
                self._log_no_signal(
                    f"Short touch but RSI not overbought ({current_rsi:.1f} < {self.rsi_overbought})"
                )
                return None

            entry = current_close
            stop = rolling_high + self.atr_stop_multiplier * current_atr
            target = entry - self.rr_ratio * (stop - entry)
            self._last_signal_ts = current_ts

            strength = min(1.0, (current_rsi - self.rsi_overbought) / (100 - self.rsi_overbought) + 0.5)
            return self._create_signal(
                side=OrderSide.SELL,
                strength=float(max(0.5, min(1.0, strength))),
                regime=regime,
                entry_price=entry,
                stop_loss=stop,
                take_profit=target,
                metadata={
                    'rolling_low': rolling_low,
                    'rolling_high': rolling_high,
                    'rsi': current_rsi,
                    'adx': current_adx,
                    'bb_width': current_width,
                    'bb_width_threshold': width_threshold,
                    'atr': current_atr,
                    'session_hour_utc': hour,
                }
            )

        self._log_no_signal(
            f"No fade setup (close={current_close:.2f} low={rolling_low:.2f} high={rolling_high:.2f} "
            f"rsi={current_rsi:.1f} adx={current_adx:.1f})"
        )
        return None
