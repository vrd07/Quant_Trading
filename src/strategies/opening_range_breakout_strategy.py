"""
Opening Range Breakout (ORB) — session-anchored 15m breakout for XAUUSD.

Why this exists (research finding 2026-06-08):
    The old Donchian `BreakoutStrategy` only had edge in 4 specific UTC hours
    (04, 07-08, 22). Widening it to "trade daily" collapsed PF from ~1.6 to
    ~1.05 — noise with a commission bill. The edge lived in *when*, not *what*.
    ORB makes that explicit: it anchors to a session open, builds the opening
    range, and trades the break of it. It fires ~once per session by
    construction, so daily frequency falls out naturally instead of being
    forced by loosening filters.

Model (stateless — the opening range is recomputed from bar timestamps every
call, never stored on the instance, per the repo's stateless-strategy rule):

    1. Pick the active session for the current bar (London 07:00, NY 13:00 UTC
       by default). Each session opens an `or_minutes` window.
    2. The opening range = [min(low), max(high)] of the bars inside that window.
    3. After the window closes, for `entry_window_minutes`, the FIRST 15m bar
       that *closes* beyond the range high (long) / low (short) fires once.
       Statelessness is preserved by scanning the session's post-OR bars: if an
       earlier bar already broke that side, we don't re-fire.
    4. Stop = opposite range boundary ± `sl_buffer_atr`×ATR. Target = R-multiple
       of that risk (`rr_ratio`). SL/TP are set explicitly on the Signal.

Quality gates (kept deliberately small — added only if research justifies):
    - OR height must be within [min_or_atr, max_or_atr]×ATR. Too tight = noise,
      too wide = the move already happened.
    - Optional 1H-EMA trend alignment (only break with the higher-TF trend).
    - Optional close-conviction: breakout bar closes in the far N% of its range.
"""

from typing import Optional
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Session opening-range breakout, 15m, XAUUSD."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Session opens as [hour, minute] in UTC. London + NY cover gold's
        # two liquid expansions; each is a fresh opening range.
        self.sessions = config.get('sessions', [[7, 0], [13, 0]])

        self.or_minutes = config.get('or_minutes', 30)              # range window
        self.entry_window_minutes = config.get('entry_window_minutes', 180)
        self.bar_minutes = config.get('bar_minutes', 15)

        # Stop / target
        self.sl_buffer_atr = config.get('sl_buffer_atr', 0.10)      # buffer beyond OR
        self.rr_ratio = config.get('rr_ratio', 2.0)

        # OR-height sanity band (in ATR units)
        self.min_or_atr = config.get('min_or_atr', 0.5)
        self.max_or_atr = config.get('max_or_atr', 4.0)

        # Optional filters
        self.htf_trend_enabled = config.get('htf_trend_enabled', False)
        self.htf_ema_period = config.get('htf_ema_period', 50)
        self.conviction_enabled = config.get('conviction_enabled', False)
        self.close_position_pct = config.get('close_position_pct', 0.5)

        self.atr_period = config.get('atr_period', 14)

        # Research flag: fade the break back into the range instead of following
        # it. Gold session breaks fail often (see research 2026-06-08); this lets
        # the harness measure the inverted edge without a second class.
        self.fade_mode = config.get('fade_mode', False)
        self.fade_target_atr = config.get('fade_target_atr', 1.0)

    def get_name(self) -> str:
        return "opening_range_breakout"

    # ------------------------------------------------------------------ helpers
    def _active_session_open(self, ts: pd.Timestamp):
        """Return the session-open Timestamp whose entry window contains ts,
        or None if ts is outside every session's [open, entry-window-end)."""
        day = ts.normalize()
        total_minutes = self.or_minutes + self.entry_window_minutes
        for hh, mm in self.sessions:
            open_ts = day + pd.Timedelta(hours=hh, minutes=mm)
            if open_ts <= ts < open_ts + pd.Timedelta(minutes=total_minutes):
                return open_ts
        return None

    def _opening_range(self, bars: pd.DataFrame, open_ts: pd.Timestamp):
        """[low, high] of bars inside [open_ts, open_ts + or_minutes)."""
        end = open_ts + pd.Timedelta(minutes=self.or_minutes)
        window = bars[(bars.index >= open_ts) & (bars.index < end)]
        if len(window) == 0:
            return None
        return float(window['low'].min()), float(window['high'].max())

    def _already_broke(self, bars: pd.DataFrame, open_ts: pd.Timestamp,
                       current_ts: pd.Timestamp, or_low: float, or_high: float) -> bool:
        """Stateless 'one trade per session': did any post-OR bar BEFORE the
        current bar already close beyond either boundary?"""
        or_end = open_ts + pd.Timedelta(minutes=self.or_minutes)
        prior = bars[(bars.index >= or_end) & (bars.index < current_ts)]
        if len(prior) == 0:
            return False
        closes = prior['close']
        return bool((closes > or_high).any() or (closes < or_low).any())

    def _htf_aligned(self, bars: pd.DataFrame, side: OrderSide) -> bool:
        if not self.htf_trend_enabled:
            return True
        try:
            o = {'open': 'first', 'high': 'max', 'low': 'min',
                 'close': 'last', 'volume': 'sum'}
            h1 = bars.resample('1h').agg(o).dropna()
            if len(h1) < self.htf_ema_period + 2:
                return True
            ema = Indicators.ema(h1, period=self.htf_ema_period).iloc[-1]
            close = h1['close'].iloc[-1]
            if pd.isna(ema):
                return True
            return close > ema if side == OrderSide.BUY else close < ema
        except Exception:
            return True

    def _has_conviction(self, bar, side: OrderSide) -> bool:
        if not self.conviction_enabled:
            return True
        rng = float(bar['high']) - float(bar['low'])
        if rng <= 0:
            return False
        pos = (float(bar['close']) - float(bar['low'])) / rng
        return pos >= (1.0 - self.close_position_pct) if side == OrderSide.BUY \
            else pos <= self.close_position_pct

    # --------------------------------------------------------------------- main
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        if not isinstance(bars.index, pd.DatetimeIndex):
            self._log_no_signal("Non-datetime index")
            return None

        if len(bars) < self.atr_period + 5:
            self._log_no_signal("Insufficient data")
            return None

        current_ts = bars.index[-1]

        open_ts = self._active_session_open(current_ts)
        if open_ts is None:
            self._log_no_signal("Outside session entry window")
            return None

        # Don't trade until the opening range has fully formed.
        or_end = open_ts + pd.Timedelta(minutes=self.or_minutes)
        if current_ts < or_end:
            self._log_no_signal("Opening range still forming")
            return None

        rng = self._opening_range(bars, open_ts)
        if rng is None:
            self._log_no_signal("No bars in opening range")
            return None
        or_low, or_high = rng

        atr = Indicators.atr(bars, period=self.atr_period).iloc[-1]
        if pd.isna(atr) or atr <= 0:
            self._log_no_signal("ATR unavailable")
            return None

        # OR-height sanity band
        or_height = or_high - or_low
        if or_height < self.min_or_atr * atr:
            self._log_no_signal(f"OR too tight ({or_height:.2f} < {self.min_or_atr}xATR)")
            return None
        if or_height > self.max_or_atr * atr:
            self._log_no_signal(f"OR too wide ({or_height:.2f} > {self.max_or_atr}xATR)")
            return None

        # One trade per session (stateless dedup)
        if self._already_broke(bars, open_ts, current_ts, or_low, or_high):
            self._log_no_signal("Session already broke")
            return None

        bar = bars.iloc[-1]
        close = float(bar['close'])
        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.TREND
        buffer = self.sl_buffer_atr * float(atr)

        side = None
        if close > or_high:
            side = OrderSide.BUY
        elif close < or_low:
            side = OrderSide.SELL
        if side is None:
            self._log_no_signal("No range break")
            return None

        if not self._has_conviction(bar, side):
            self._log_no_signal("Weak close conviction")
            return None
        if not self._htf_aligned(bars, side):
            self._log_no_signal("HTF trend not aligned")
            return None

        if self.fade_mode:
            # Fade: a break above the range is a SELL back toward the range,
            # stop just beyond the break extreme, target = fade_target_atr×ATR.
            side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY

        if side == OrderSide.BUY:
            if self.fade_mode:
                stop_loss = float(bar['low']) - buffer
                take_profit = close + self.fade_target_atr * float(atr)
            else:
                stop_loss = or_low - buffer
                take_profit = close + self.rr_ratio * (close - stop_loss)
            risk = close - stop_loss
        else:
            if self.fade_mode:
                stop_loss = float(bar['high']) + buffer
                take_profit = close - self.fade_target_atr * float(atr)
            else:
                stop_loss = or_high + buffer
                take_profit = close - self.rr_ratio * (stop_loss - close)
            risk = stop_loss - close

        if risk <= 0:
            self._log_no_signal("Non-positive risk distance")
            return None

        # Strength: how decisively price cleared the range, capped.
        clearance = (close - or_high) if side == OrderSide.BUY else (or_low - close)
        strength = min(0.60 + min(clearance / float(atr), 1.0) * 0.30, 0.95)

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                'session_open': str(open_ts),
                'or_high': or_high,
                'or_low': or_low,
                'or_height': or_height,
                'atr': float(atr),
                'rr_ratio': self.rr_ratio,
            }
        )
