"""
VWAP + MACD Crossover Strategy — v2 (2026-05-09 structural fixes).

Entry Logic (two-stage gate):
──────────────────────────────
Stage 1 — VWAP touch gate:
  Price must touch or breach a session-anchored VWAP StdDev band (±band_std_mult σ).
  This arms the entry, but does NOT immediately open a position.
  The gate stays armed for up to `macd_arm_window` bars (default 3 = 45 min on 15m).

Stage 2 — MACD crossover trigger (intersection only):
  A MACD crossover must occur while the gate is armed:
  - Blue (MACD line) crosses ABOVE orange (Signal line) → LONG
  - Blue (MACD line) crosses BELOW orange (Signal line) → SHORT

H1 EMA trend gate (v2 addition):
  A higher-timeframe EMA filter prevents trading against the dominant trend:
  - SELL only fires when price < H1 EMA(h1_ema_period) — confirmed bearish HTF
  - BUY  only fires when price > H1 EMA(h1_ema_period) — confirmed bullish HTF
  On 15m bars, H1 EMA(50) ≈ 200 bars of 15m data (configurable via h1_ema_period).
  This was the primary fix for the v1 SELL-into-bull-market problem.

Directional coherence:
  • VWAP touch below lower band   → only a BUY crossover is valid (upward cross)
  • VWAP touch above upper band   → only a SELL crossover is valid (downward cross)

Risk:
  • Stop loss  : stop_atr_mult × ATR from entry (default 1.5)
  • Take profit: 2 × stop distance (1:2 risk-reward ratio, hard-coded)
  • Time stop  : max_hold_minutes (default 60 min); reversion thesis expires.

v2 changes vs v1:
  - H1 EMA(50) trend gate added — kills counter-trend entries
  - macd_arm_window default 5 → 3 (45 min max wait on 15m)
  - band_std_mult default 1.5 → 2.0 (filter genuine extremes only)
  - kill_zones_enabled config flag — set False to trade all sessions in backtest
"""

from __future__ import annotations

from typing import Optional, Tuple
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


# Session anchor hours in UTC — ordered by priority (most recent wins)
_SESSION_ANCHORS_UTC: tuple[int, ...] = (12, 7, 1)  # NY open, London open, Asian open


def _compute_session_vwap(
    bars: pd.DataFrame,
    std_mult: float,
) -> Tuple[
    Optional[pd.Series],
    Optional[pd.Series],
    Optional[pd.Series],
]:
    """
    Compute session-anchored VWAP with ±std_mult StdDev bands.

    Anchors from the most recent session open found in the bar index
    (NY 12:00 → London 07:00 → Asian 01:00 UTC). Falls back to the full
    window if no anchor is found.

    Returns:
        (vwap, upper_band, lower_band) — all None if index is not datetime
        or volume is entirely zero.
    """
    try:
        bar_hours = bars.index.hour
    except AttributeError:
        return None, None, None

    # Find the most recent session anchor
    anchor_pos: Optional[int] = None
    for anchor_hour in _SESSION_ANCHORS_UTC:
        matches = (bar_hours == anchor_hour).nonzero()[0]
        if len(matches):
            anchor_pos = int(matches[-1])
            break

    session_bars = bars.iloc[anchor_pos:] if anchor_pos is not None else bars

    typical = (session_bars['high'] + session_bars['low'] + session_bars['close']) / 3.0

    has_volume = (
        'volume' in session_bars.columns
        and session_bars['volume'].sum() > 0
    )

    if has_volume:
        cum_vol = session_bars['volume'].cumsum()
        vwap_vals = (typical * session_bars['volume']).cumsum() / cum_vol
    else:
        # No volume feed: equal-weight expanding mean (session-anchored)
        vwap_vals = typical.expanding().mean()

    std_window = min(20, len(session_bars))
    rolling_std = typical.rolling(std_window, min_periods=min(5, std_window)).std().fillna(
        typical.std()
    )

    upper = vwap_vals + std_mult * rolling_std
    lower = vwap_vals - std_mult * rolling_std

    return (
        vwap_vals.reindex(bars.index),
        upper.reindex(bars.index),
        lower.reindex(bars.index),
    )


def _macd_crossover_direction(
    macd_line: pd.Series,
    signal_line: pd.Series,
) -> int:
    """
    Detect whether a MACD crossover occurred on the most recent completed bar.

    Compares current bar (iloc[-1]) to previous bar (iloc[-2]).

    Returns:
        +1 if blue (MACD) crossed ABOVE orange (Signal) — bullish intersection
        -1 if blue (MACD) crossed BELOW orange (Signal) — bearish intersection
         0 if no crossover (or insufficient data)
    """
    if len(macd_line) < 2 or len(signal_line) < 2:
        return 0

    macd_curr   = float(macd_line.iloc[-1])
    macd_prev   = float(macd_line.iloc[-2])
    signal_curr = float(signal_line.iloc[-1])
    signal_prev = float(signal_line.iloc[-2])

    if any(pd.isna(v) for v in (macd_curr, macd_prev, signal_curr, signal_prev)):
        return 0

    bullish_cross = macd_prev <= signal_prev and macd_curr > signal_curr
    bearish_cross = macd_prev >= signal_prev and macd_curr < signal_curr

    if bullish_cross:
        return 1
    if bearish_cross:
        return -1
    return 0


class VWAPStrategy(BaseStrategy):
    """
    VWAP band touch + MACD crossover entry strategy.

    Entry is a two-stage gate:
    1. Price touches a session-anchored VWAP StdDev band (arms the entry).
    2. A MACD crossover (blue/MACD line vs orange/Signal line) fires while
       the gate is armed and in the correct direction.

    Risk management uses a fixed 1:2 risk-reward ratio:
    - Stop loss  = stop_atr_mult × ATR
    - Take profit = 2 × stop distance
    """

    def __init__(self, symbol: Symbol, config: dict) -> None:
        super().__init__(symbol, config)

        # VWAP band parameters
        self.band_std_mult    = config.get('band_std_mult', 2.0)   # v2: 1.5→2.0 (genuine extremes)
        self.atr_period       = config.get('atr_period', 14)

        # MACD parameters (mirrors TradingView defaults)
        self.macd_fast        = config.get('macd_fast', 12)
        self.macd_slow        = config.get('macd_slow', 26)
        self.macd_signal      = config.get('macd_signal', 9)

        # How many bars the VWAP-touch gate stays armed waiting for a crossover
        # v2: tightened from 5 → 3 (45 min max wait on 15m; 5 bars = stale entry)
        self.macd_arm_window  = config.get('macd_arm_window', 3)

        # Risk management
        self.stop_atr_mult    = config.get('stop_atr_mult', 1.5)
        self.risk_reward      = 2.0  # Hard-coded 1:2 per spec
        self.max_hold_minutes = config.get('max_hold_minutes', 60)

        # Session / time filters
        self.allowed_hours       = config.get('allowed_hours', None)
        # v2: kill-zone guard is now configurable; disable for all-session backtest
        self.kill_zones_enabled  = config.get('kill_zones_enabled', True)

        # v2: H1 EMA trend gate — prevents trading against the dominant HTF trend.
        # On 15m bars, 1 H1 candle = 4 × 15m bars, so H1 EMA(50) ≈ 200 × 15m bars.
        # Override `h1_ema_bars` for other timeframes (e.g. 5m → 600 bars).
        self.h1_ema_bars      = config.get('h1_ema_bars', 200)
        self.htf_trend_gate   = config.get('htf_trend_gate', True)

        # Regime (kept for downstream compatibility — regime not used as hard gate)
        self.regime_filter    = RegimeFilter()

        # Arm state: tracks whether a VWAP touch is pending a MACD crossover
        # 'long'  → price touched lower band; waiting for bullish MACD cross
        # 'short' → price touched upper band; waiting for bearish MACD cross
        # None    → no armed gate
        self._armed_direction: Optional[str] = None
        self._armed_bars_ago: int = 0

    def get_name(self) -> str:
        return "vwap_macd_crossover"

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _reset_arm(self) -> None:
        """Disarms the VWAP-touch gate."""
        self._armed_direction = None
        self._armed_bars_ago  = 0

    def _tick_arm(self) -> None:
        """Increment the arm age counter by one bar."""
        self._armed_bars_ago += 1

    def _arm_expired(self) -> bool:
        """Returns True if the gate has been armed too long without a crossover."""
        return self._armed_bars_ago >= self.macd_arm_window

    def _get_h1_ema(self, bars: pd.DataFrame) -> Optional[float]:
        """
        Compute the H1 EMA of close prices using 15m bars.

        Uses an exponential moving average over `h1_ema_bars` bars of the
        15m close series — equivalent to an EMA(50) on the H1 chart when
        h1_ema_bars=200 (4 × 15m bars per H1 candle × 50 H1 periods).

        Returns None if insufficient bars are available.
        """
        if len(bars) < self.h1_ema_bars:
            return None
        ema = bars['close'].ewm(span=self.h1_ema_bars, adjust=False).mean()
        val = float(ema.iloc[-1])
        return val if not pd.isna(val) else None

    # ── Main signal ─────────────────────────────────────────────────────────

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Evaluate VWAP + MACD crossover signal on each new bar.

        Two-stage process:
        1. Detect VWAP band touch → arm gate in the appropriate direction.
        2. On each armed bar, check for a MACD crossover in the matching
           direction → emit signal with 1:2 RR metadata.
        """
        if not self.is_enabled():
            return None

        min_bars = max(self.atr_period + 5, self.macd_slow + self.macd_signal + 5, 35)
        if len(bars) < min_bars:
            if not getattr(self, '_logged_warmup', False):
                self._log_no_signal("Insufficient bars for warmup")
                self._logged_warmup = True
            return None
        self._logged_warmup = False

        bars = bars.tail(800)

        # ── Session / kill-zone time guard ───────────────────────────────
        bar_hour = self._get_bar_hour(bars)

        # London open (07–10 UTC) and NY open (12–15 UTC): institutional flow dominates.
        # Disabled when kill_zones_enabled=False (e.g. all-sessions backtest mode).
        if self.kill_zones_enabled:
            if bar_hour is not None and any(s <= bar_hour < e for s, e in ((7, 10), (12, 15))):
                self._log_no_signal(f"Kill zone (hour={bar_hour} UTC)")
                self._reset_arm()
                return None

        if (
            self.allowed_hours is not None
            and bar_hour is not None
            and bar_hour not in self.allowed_hours
        ):
            self._log_no_signal(f"Outside allowed_hours (hour={bar_hour})")
            self._reset_arm()
            return None

        # ── Regime label (informational — not a hard gate) ───────────────
        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.RANGE

        # ── Indicators ───────────────────────────────────────────────────
        vwap, upper_band, lower_band = _compute_session_vwap(
            bars, self.band_std_mult
        )
        if vwap is None or pd.isna(vwap.iloc[-1]):
            self._log_no_signal("Session VWAP unavailable")
            return None

        atr = Indicators.atr(bars, period=self.atr_period)
        macd_line, signal_line, _ = Indicators.macd(
            bars,
            fast_period=self.macd_fast,
            slow_period=self.macd_slow,
            signal_period=self.macd_signal,
        )

        current_close  = float(bars['close'].iloc[-1])
        current_vwap   = float(vwap.iloc[-1])
        current_upper  = float(upper_band.iloc[-1])
        current_lower  = float(lower_band.iloc[-1])
        current_atr    = float(atr.iloc[-1])

        if any(
            pd.isna(v) for v in (
                current_vwap, current_upper, current_lower, current_atr,
                macd_line.iloc[-1], signal_line.iloc[-1],
            )
        ):
            self._log_no_signal("Indicator NaN")
            return None

        # ── Stage 1: VWAP band touch → arm the gate ──────────────────────
        touched_lower = current_close <= current_lower
        touched_upper = current_close >= current_upper

        if touched_lower and self._armed_direction != 'long':
            # Lower band touch arms for a BUY (we need a bullish MACD cross)
            self._armed_direction = 'long'
            self._armed_bars_ago  = 0
            self._log_no_signal(
                f"VWAP lower-band touch at {current_close:.5f} "
                f"(band={current_lower:.5f}) — armed for BUY crossover"
            )

        elif touched_upper and self._armed_direction != 'short':
            # Upper band touch arms for a SELL (we need a bearish MACD cross)
            self._armed_direction = 'short'
            self._armed_bars_ago  = 0
            self._log_no_signal(
                f"VWAP upper-band touch at {current_close:.5f} "
                f"(band={current_upper:.5f}) — armed for SELL crossover"
            )

        # If price has moved back well inside the bands, reset the arm
        band_width    = max(current_upper - current_lower, 1e-6)
        inner_margin  = 0.2 * band_width  # reset if price is 20% inside band
        price_inside  = current_lower + inner_margin < current_close < current_upper - inner_margin
        if price_inside and self._armed_direction is not None:
            self._reset_arm()
            self._log_no_signal("Price back inside bands — gate reset")
            return None

        # Nothing armed yet
        if self._armed_direction is None:
            self._log_no_signal(
                f"Close {current_close:.5f} within bands "
                f"[{current_lower:.5f}–{current_upper:.5f}]"
            )
            return None

        # ── v2: H1 EMA(50) trend gate ─────────────────────────────────────
        # Prevents trading against the dominant HTF trend — the primary fix
        # for the v1 SELL-into-bull-market problem (43 losing SELL trades).
        if self.htf_trend_gate:
            h1_ema = self._get_h1_ema(bars)
            if h1_ema is not None:
                if self._armed_direction == 'short' and current_close > h1_ema:
                    self._log_no_signal(
                        f"H1 EMA({self.h1_ema_bars}) bullish ({h1_ema:.2f}) — "
                        f"no SELL against trend"
                    )
                    self._reset_arm()
                    return None
                if self._armed_direction == 'long' and current_close < h1_ema:
                    self._log_no_signal(
                        f"H1 EMA({self.h1_ema_bars}) bearish ({h1_ema:.2f}) — "
                        f"no BUY against trend"
                    )
                    self._reset_arm()
                    return None

        # ── Stage 2: Wait for MACD crossover ─────────────────────────────
        self._tick_arm()
        if self._arm_expired():
            self._log_no_signal(
                f"MACD crossover not seen within {self.macd_arm_window} bars — gate expired"
            )
            self._reset_arm()
            return None

        cross = _macd_crossover_direction(macd_line, signal_line)

        if cross == 0:
            self._log_no_signal(
                f"Waiting for MACD crossover (armed={self._armed_direction}, "
                f"age={self._armed_bars_ago}/{self.macd_arm_window})"
            )
            return None

        # ── Directional coherence check ───────────────────────────────────
        if self._armed_direction == 'long' and cross != 1:
            self._log_no_signal(
                "Bearish MACD cross while armed LONG — discarding"
            )
            self._reset_arm()
            return None

        if self._armed_direction == 'short' and cross != -1:
            self._log_no_signal(
                "Bullish MACD cross while armed SHORT — discarding"
            )
            self._reset_arm()
            return None

        # ── Signal construction (1:2 RR) ─────────────────────────────────
        stop_distance = self.stop_atr_mult * current_atr
        tp_distance   = self.risk_reward * stop_distance

        if self._armed_direction == 'long':
            side       = OrderSide.BUY
            stop_price = current_close - stop_distance
            tp_price   = current_close + tp_distance
            entry_reason = 'vwap_lower_band_touch_macd_bullish_cross'
        else:
            side       = OrderSide.SELL
            stop_price = current_close + stop_distance
            tp_price   = current_close - tp_distance
            entry_reason = 'vwap_upper_band_touch_macd_bearish_cross'

        deviation_pct = (current_close - current_vwap) / current_vwap * 100
        macd_curr     = float(macd_line.iloc[-1])
        signal_curr   = float(signal_line.iloc[-1])

        self._reset_arm()

        return self._create_signal(
            side=side,
            strength=0.75,          # Moderate confidence — composite confirmation
            regime=regime,
            entry_price=float(current_close),
            metadata={
                'strategy':          'vwap_macd_crossover',
                'entry_reason':      entry_reason,
                'vwap':              current_vwap,
                'vwap_upper_band':   current_upper,
                'vwap_lower_band':   current_lower,
                'deviation_pct':     float(deviation_pct),
                'macd_line':         macd_curr,
                'signal_line':       signal_curr,
                'macd_histogram':    macd_curr - signal_curr,
                'atr':               current_atr,
                'stop_price':        float(stop_price),
                'take_profit_price': float(tp_price),
                'risk_reward':       self.risk_reward,
                'max_hold_minutes':  self.max_hold_minutes,
            },
        )
