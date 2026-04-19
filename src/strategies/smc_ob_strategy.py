"""
SMC Order Block (OB) Strategy — Pure SMC/ICT trade logic for XAUUSD.
Trades BOTH BUY and SELL setups as mirror-image 5-phase state machines.

BUY Setup:
  1. Bearish impulse candle opens from a swing-HIGH area (new sellers active).
  2. Last bearish candle before impulse = Demand OB zone {high, low}.
  3. Price returns to the OB zone from above.
  4. Liquidity sweep: wick BELOW OB low + close BACK ABOVE it (buy-stop grab).
  5. First bullish candle after sweep (buyers absorbing the liquidity).
  6. Next candle breaks ABOVE first buy candle's HIGH -> ENTRY.
     SL = first buy candle's low | TP = OB high + liquidity_premium_mult * ATR.

SELL Setup (mirror):
  1. Bullish impulse candle opens from a swing-LOW area (new buyers active).
  2. Last bullish candle before impulse = Supply OB zone {high, low}.
  3. Price returns to the OB zone from below.
  4. Liquidity sweep: wick ABOVE OB high + close BACK BELOW it (sell-stop grab).
  5. First bearish candle after sweep (sellers absorbing the liquidity).
  6. Next candle breaks BELOW first sell candle's LOW -> ENTRY.
     SL = first sell candle's high | TP = OB low - liquidity_premium_mult * ATR.

State Machine (shared, direction-tagged):
  IDLE            -> OB_FORMED      (impulse from swing extreme)
  OB_FORMED       -> OB_TOUCHED     (price re-enters OB zone)
  OB_TOUCHED      -> SWEEP_CONFIRMED (wick past OB boundary + close-back inside)
  SWEEP_CONFIRMED -> WAITING_ENTRY  (first candle in reversal direction)
  WAITING_ENTRY   -> IDLE + signal  (next candle breaks first candle's trigger level)

  Any state -> IDLE on:
    - OB zone age exceeds ob_max_age_bars
    - Price closes THROUGH the OB in the wrong direction (zone consumed)
    - Pre-entry trigger level breached (setup invalidated)

Design (codinglegits):
  - Carmack: all detection is in pure functions. Mutable state (_state, _direction,
    _ob_zone, etc.) only mutated in on_bar() and visible at the call site.
  - geohot: one state machine, one direction flag. No duplication.
  - Jeff Dean: every signal carries full metadata for post-trade attribution.
"""

from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

IDLE             = "IDLE"
OB_FORMED        = "OB_FORMED"
OB_TOUCHED       = "OB_TOUCHED"
SWEEP_CONFIRMED  = "SWEEP_CONFIRMED"
WAITING_ENTRY    = "WAITING_ENTRY"

DIR_BUY  = "BUY"
DIR_SELL = "SELL"


# ---------------------------------------------------------------------------
# Pure detection helpers — BUY side
# ---------------------------------------------------------------------------

def _is_near_swing_high(bars: pd.DataFrame, impulse_iloc: int, lookback: int) -> bool:
    """Return True when a bearish impulse opens from a swing-high area.

    Checks that the impulse bar's OPEN sits in the upper 30% of the recent
    `lookback`-bar price range — sellers stepped in at a local top.

    Args:
        bars: OHLCV DataFrame.
        impulse_iloc: iloc index of the bearish impulse candle.
        lookback: Number of bars to look back for the local range.

    Returns:
        True when the impulse opened in the top 30% of the recent range.
    """
    start = max(0, impulse_iloc - lookback)
    window_highs = bars["high"].iloc[start:impulse_iloc + 1]
    window_lows  = bars["low"].iloc[start:impulse_iloc + 1]

    range_high = float(window_highs.max())
    range_low  = float(window_lows.min())
    range_size = range_high - range_low

    if range_size <= 0:
        return False

    impulse_open = float(bars["open"].iloc[impulse_iloc])
    return (impulse_open - range_low) / range_size >= 0.70


def _is_bearish_impulse(
    bar_open: float, bar_close: float, atr: float, min_mult: float
) -> bool:
    """Return True when the bar body is a bearish impulse >= min_mult * ATR."""
    if atr <= 0:
        return False
    return (bar_open - bar_close) >= min_mult * atr


def _is_liquidity_sweep_buy(bar_low: float, bar_close: float, ob_low: float) -> bool:
    """Return True when price wicks below OB low but closes above it (buy sweep).

    The wick grabs buy-side stops below the OB; the close-back-above signals
    institutional absorption before the move up.
    """
    return bar_low < ob_low and bar_close > ob_low


# ---------------------------------------------------------------------------
# Pure detection helpers — SELL side (exact mirror)
# ---------------------------------------------------------------------------

def _is_near_swing_low(bars: pd.DataFrame, impulse_iloc: int, lookback: int) -> bool:
    """Return True when a bullish impulse opens from a swing-low area.

    Checks that the impulse bar's OPEN sits in the lower 30% of the recent
    `lookback`-bar price range — buyers stepped in at a local bottom.

    Args:
        bars: OHLCV DataFrame.
        impulse_iloc: iloc index of the bullish impulse candle.
        lookback: Number of bars to look back for the local range.

    Returns:
        True when the impulse opened in the bottom 30% of the recent range.
    """
    start = max(0, impulse_iloc - lookback)
    window_highs = bars["high"].iloc[start:impulse_iloc + 1]
    window_lows  = bars["low"].iloc[start:impulse_iloc + 1]

    range_high = float(window_highs.max())
    range_low  = float(window_lows.min())
    range_size = range_high - range_low

    if range_size <= 0:
        return False

    impulse_open = float(bars["open"].iloc[impulse_iloc])
    return (impulse_open - range_low) / range_size <= 0.30


def _is_bullish_impulse(
    bar_open: float, bar_close: float, atr: float, min_mult: float
) -> bool:
    """Return True when the bar body is a bullish impulse >= min_mult * ATR."""
    if atr <= 0:
        return False
    return (bar_close - bar_open) >= min_mult * atr


def _is_liquidity_sweep_sell(bar_high: float, bar_close: float, ob_high: float) -> bool:
    """Return True when price wicks above OB high but closes below it (sell sweep).

    The wick grabs sell-side stops above the OB; the close-back-below signals
    institutional absorption before the move down.
    """
    return bar_high > ob_high and bar_close < ob_high


# ---------------------------------------------------------------------------
# Shared pure helpers (work for both directions)
# ---------------------------------------------------------------------------

def _build_ob_zone(bars: pd.DataFrame, impulse_iloc: int) -> Optional[Dict[str, float]]:
    """Build the Order Block zone from the candle immediately BEFORE the impulse.

    The OB body is the candle that preceded the impulse move. We use open/close
    (body) to define high/low rather than the full wick range.

    Args:
        bars: OHLCV DataFrame.
        impulse_iloc: iloc index of the impulse candle.

    Returns:
        Dict {'high': float, 'low': float} or None if insufficient data.
    """
    ob_iloc = impulse_iloc - 1
    if ob_iloc < 0:
        return None

    ob_open  = float(bars["open"].iloc[ob_iloc])
    ob_close = float(bars["close"].iloc[ob_iloc])

    ob_high = max(ob_open, ob_close)
    ob_low  = min(ob_open, ob_close)

    if ob_high <= ob_low:
        return None

    return {"high": ob_high, "low": ob_low}


def _price_in_zone(
    bar_low: float, bar_high: float,
    ob_high: float, ob_low: float,
    tolerance: float,
) -> bool:
    """Return True when the bar overlaps the OB zone within ATR-scaled tolerance."""
    return (bar_low - tolerance) <= ob_high and (bar_high + tolerance) >= ob_low


# ---------------------------------------------------------------------------
# FVG confluence helper
# (adapted from joshyattridge/smart-money-concepts `fvg` — MIT licensed)
#
# An FVG at bar i is defined by a 3-bar pattern:
#   Bullish: bar[i-1].high < bar[i+1].low  AND  bar[i] is bullish (close > open)
#            Top = bar[i+1].low    Bottom = bar[i-1].high
#   Bearish: bar[i-1].low  > bar[i+1].high AND  bar[i] is bearish (close < open)
#            Top = bar[i-1].low    Bottom = bar[i+1].high
#
# Mitigation: a bullish FVG is mitigated once a later bar's low <= Top;
#             a bearish FVG is mitigated once a later bar's high >= Bottom.
# ---------------------------------------------------------------------------

def _find_unmitigated_fvg_near_ob(
    bars: pd.DataFrame,
    ob_high: float,
    ob_low: float,
    direction: str,
    max_age_bars: int,
    proximity: float,
) -> Optional[Dict[str, float]]:
    """Return the most recent unmitigated FVG in the trade direction near the OB.

    The FVG must overlap the interval [ob_low - proximity, ob_high + proximity]
    so the imbalance sits at (or adjacent to) the Order Block — canonical ICT
    confluence that confirms the zone still has unfilled inefficiency.

    Args:
        bars: OHLCV DataFrame (index doesn't matter; positional).
        ob_high, ob_low: Order Block zone boundaries.
        direction: DIR_BUY -> search bullish FVGs; DIR_SELL -> bearish.
        max_age_bars: Consider FVGs formed within the last max_age_bars only.
        proximity: ATR-scaled tolerance for FVG-to-OB overlap.

    Returns:
        {'top': float, 'bottom': float, 'age': int} for the newest match,
        or None.
    """
    n = len(bars)
    if n < 3:
        return None

    hi = bars["high"].values
    lo = bars["low"].values
    op = bars["open"].values
    cl = bars["close"].values

    zone_top = ob_high + proximity
    zone_bot = ob_low  - proximity

    start = max(1, n - max_age_bars - 1)
    end   = n - 1  # bar[i+1] must exist, so i maxes at n-2

    for i in range(end - 1, start - 1, -1):
        if direction == DIR_BUY:
            if hi[i - 1] < lo[i + 1] and cl[i] > op[i]:
                fvg_top = float(lo[i + 1])
                fvg_bot = float(hi[i - 1])
                if fvg_top >= zone_bot and fvg_bot <= zone_top:
                    remaining = lo[i + 2:]
                    if remaining.size == 0 or (remaining > fvg_top).all():
                        return {"top": fvg_top, "bottom": fvg_bot, "age": n - 1 - i}
        else:  # DIR_SELL
            if lo[i - 1] > hi[i + 1] and cl[i] < op[i]:
                fvg_top = float(lo[i - 1])
                fvg_bot = float(hi[i + 1])
                if fvg_top >= zone_bot and fvg_bot <= zone_top:
                    remaining = hi[i + 2:]
                    if remaining.size == 0 or (remaining < fvg_bot).all():
                        return {"top": fvg_top, "bottom": fvg_bot, "age": n - 1 - i}

    return None


# ---------------------------------------------------------------------------
# Strategy class
# ---------------------------------------------------------------------------

class SMCOrderBlockStrategy(BaseStrategy):
    """Trade SMC Order Block setups — BUY and SELL — with liquidity sweep confirmation.

    Both directions use the same 5-phase state machine. The `_direction` field
    tags which side is active. A fresh impulse on the opposite side will reset
    the machine and start a new cycle only after the previous setup resolves.
    """

    def __init__(self, symbol: Symbol, config: dict) -> None:
        super().__init__(symbol, config)

        # Detection parameters
        self.swing_lookback: int         = config.get("swing_lookback", 5)
        self.min_impulse_atr_mult: float = config.get("min_impulse_atr_mult", 1.2)
        self.ob_max_age_bars: int        = config.get("ob_max_age_bars", 100)
        self.ob_touch_tolerance_atr: float = config.get("ob_touch_tolerance_atr", 1.0)

        # SL / TP parameters
        self.liquidity_premium_mult: float     = config.get("liquidity_premium_mult", 15.0)
        self.min_liquidity_premium_mult: float = config.get("min_liquidity_premium_mult", 8.0)

        # Filters
        self.adx_min_threshold: float        = config.get("adx_min_threshold", 15.0)
        self.long_only: bool                 = config.get("long_only", False)
        self.session_hours: Optional[list]   = config.get("session_hours", None)
        self.cooldown_bars: int              = config.get("cooldown_bars", 10)
        self._bars_since_signal: int         = self.cooldown_bars

        # EMA trend filter: only BUY when price > EMA, only SELL when price < EMA.
        # Prevents shorting into a bull run and buying into a bear trend.
        self.use_ema_trend_filter: bool = config.get("use_ema_trend_filter", True)
        self.ema_trend_period: int      = config.get("ema_trend_period", 50)

        # SL size quality gate: reject setups where risk is too small (noise)
        # or too large (sloppy OB — stop is too wide to be a clean setup).
        # Both are expressed as ATR multiples.
        self.min_sl_atr: float = config.get("min_sl_atr", 0.1)   # SL must be >= 0.1 ATR
        self.max_sl_atr: float = config.get("max_sl_atr", 3.0)   # SL must be <= 3.0 ATR

        # FVG (Fair Value Gap) confluence filter — opt-in ICT imbalance check.
        # When enabled, the entry break must coincide with an unmitigated FVG
        # of matching direction sitting within fvg_ob_proximity_atr * ATR of
        # the OB zone.
        self.require_fvg_confluence: bool  = config.get("require_fvg_confluence", False)
        self.fvg_ob_proximity_atr: float   = config.get("fvg_ob_proximity_atr", 2.0)
        self.fvg_max_age_bars: int         = config.get("fvg_max_age_bars", 50)

        # ── Mutable state (Carmack: explicit, only mutated in on_bar) ──────
        self._state: str                         = IDLE
        self._direction: Optional[str]           = None   # DIR_BUY | DIR_SELL
        self._ob_zone: Optional[Dict[str, float]] = None  # {high, low}
        self._ob_age: int                        = 0
        self._trigger_high: Optional[float]      = None   # first buy candle HIGH (buy) / first sell candle LOW (sell)
        self._trigger_low: Optional[float]       = None
        self._sl_level: Optional[float]          = None   # SL reference
        self._sweep_close: Optional[float]        = None

    def get_name(self) -> str:
        return "smc_ob"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self, reason: str = "") -> None:
        """Return to IDLE, wipe all phase state."""
        if self._state != IDLE:
            self.logger.debug(f"SMC OB reset: {reason}")
        self._state     = IDLE
        self._direction = None
        self._ob_zone   = None
        self._ob_age    = 0
        self._trigger_high = None
        self._trigger_low  = None
        self._sl_level     = None
        self._sweep_close  = None

    def _start_cycle(self, direction: str, ob_zone: Dict[str, float]) -> None:
        """Transition to OB_FORMED with a fresh zone."""
        self._state     = OB_FORMED
        self._direction = direction
        self._ob_zone   = ob_zone
        self._ob_age    = 0
        self._trigger_high = None
        self._trigger_low  = None
        self._sl_level     = None
        self._sweep_close  = None
        self.logger.info(
            "SMC OB zone formed",
            direction=direction,
            ob_high=f"{ob_zone['high']:.2f}",
            ob_low =f"{ob_zone['low']:.2f}",
        )

    def _in_session(self, bars: pd.DataFrame) -> bool:
        if self.session_hours is None:
            return True
        hour = self._get_bar_hour(bars)
        if hour is None:
            return True
        return hour in self.session_hours

    # ------------------------------------------------------------------
    # Main signal loop
    # ------------------------------------------------------------------

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        bars = bars.tail(500)

        min_bars = self.swing_lookback + 30
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        # ── Cooldown gate ─────────────────────────────────────────────
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(
                f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars"
            )
            return None

        # ── Session filter ────────────────────────────────────────────
        if not self._in_session(bars):
            self._log_no_signal("Outside session hours")
            return None

        # ── Indicators ────────────────────────────────────────────────
        atr = Indicators.atr(bars, period=14)
        adx = Indicators.adx(bars, period=14)

        current_open  = float(bars["open"].iloc[-1])
        current_high  = float(bars["high"].iloc[-1])
        current_low   = float(bars["low"].iloc[-1])
        current_close = float(bars["close"].iloc[-1])
        current_atr   = float(atr.iloc[-1])
        current_adx   = float(adx.iloc[-1])

        if np.isnan(current_atr) or np.isnan(current_adx) or current_atr <= 0:
            self._log_no_signal("Indicator calculation failed")
            return None

        if current_adx < self.adx_min_threshold:
            self._log_no_signal(f"ADX too low: {current_adx:.1f}")
            return None

        regime    = self.ml_regime if self.ml_regime is not None else MarketRegime.TREND
        tolerance = self.ob_touch_tolerance_atr * current_atr

        # ── EMA trend direction (computed once, used to gate both sides) ───
        ema_trend = Indicators.ema(bars, period=self.ema_trend_period)
        current_ema = float(ema_trend.iloc[-1])
        price_above_ema = current_close > current_ema
        price_below_ema = current_close < current_ema

        # ==============================================================
        # PHASE 0 -> 1: Detect impulse on bar[-2], start new cycle
        # bar[-2] is used so bar[-1] can immediately begin the retest.
        # A fresh impulse overrides any in-progress cycle for cleanliness.
        # ==============================================================
        if len(bars) >= self.swing_lookback + 2:
            impulse_iloc = len(bars) - 2
            prev_open  = float(bars["open"].iloc[-2])
            prev_close = float(bars["close"].iloc[-2])
            prev_atr   = float(atr.iloc[-2])
            if np.isnan(prev_atr) or prev_atr <= 0:
                prev_atr = current_atr

            # ── BUY setup: bearish impulse from swing high ─────────────
            if (
                _is_near_swing_high(bars, impulse_iloc, self.swing_lookback)
                and _is_bearish_impulse(prev_open, prev_close, prev_atr, self.min_impulse_atr_mult)
                and (not self.use_ema_trend_filter or price_above_ema)  # only BUY above EMA
            ):
                ob = _build_ob_zone(bars, impulse_iloc)
                if ob is not None:
                    self._start_cycle(DIR_BUY, ob)

            # ── SELL setup: bullish impulse from swing low ──────────────
            elif (
                not self.long_only
                and _is_near_swing_low(bars, impulse_iloc, self.swing_lookback)
                and _is_bullish_impulse(prev_open, prev_close, prev_atr, self.min_impulse_atr_mult)
                and (not self.use_ema_trend_filter or price_below_ema)  # only SELL below EMA
            ):
                ob = _build_ob_zone(bars, impulse_iloc)
                if ob is not None:
                    self._start_cycle(DIR_SELL, ob)

        # ── Age guard ─────────────────────────────────────────────────
        if self._state != IDLE:
            self._ob_age += 1
            if self._ob_age > self.ob_max_age_bars:
                self._reset(f"OB expired after {self._ob_age} bars")
                self._log_no_signal("OB zone expired")
                return None

        # ==============================================================
        # PHASE 1 -> 2: Price touches the OB zone
        # ==============================================================
        if self._state == OB_FORMED:
            assert self._ob_zone is not None
            if _price_in_zone(
                current_low, current_high,
                self._ob_zone["high"], self._ob_zone["low"],
                tolerance,
            ):
                self._state = OB_TOUCHED
                self.logger.info(
                    "SMC OB touched",
                    direction=self._direction,
                    ob_high=f"{self._ob_zone['high']:.2f}",
                    ob_low =f"{self._ob_zone['low']:.2f}",
                    price  =f"{current_close:.2f}",
                )

        # ==============================================================
        # PHASE 2 -> 3: Liquidity sweep confirmation
        # ==============================================================
        if self._state == OB_TOUCHED:
            assert self._ob_zone is not None

            if self._direction == DIR_BUY:
                # Guard: closed below OB low — zone consumed
                if current_close < self._ob_zone["low"] - tolerance:
                    self._reset("BUY: closed below OB low")
                    self._log_no_signal("OB zone broken to downside")
                    return None

                if _is_liquidity_sweep_buy(current_low, current_close, self._ob_zone["low"]):
                    self._state       = SWEEP_CONFIRMED
                    self._sweep_close = current_close
                    self.logger.info(
                        "SMC BUY sweep confirmed",
                        wick_low =f"{current_low:.2f}",
                        close    =f"{current_close:.2f}",
                        ob_low   =f"{self._ob_zone['low']:.2f}",
                    )

            else:  # DIR_SELL
                # Guard: closed above OB high — zone consumed
                if current_close > self._ob_zone["high"] + tolerance:
                    self._reset("SELL: closed above OB high")
                    self._log_no_signal("OB zone broken to upside")
                    return None

                if _is_liquidity_sweep_sell(current_high, current_close, self._ob_zone["high"]):
                    self._state       = SWEEP_CONFIRMED
                    self._sweep_close = current_close
                    self.logger.info(
                        "SMC SELL sweep confirmed",
                        wick_high=f"{current_high:.2f}",
                        close    =f"{current_close:.2f}",
                        ob_high  =f"{self._ob_zone['high']:.2f}",
                    )

        # ==============================================================
        # PHASE 3 -> 4: First reversal candle after sweep
        # ==============================================================
        if self._state == SWEEP_CONFIRMED:
            assert self._ob_zone is not None

            if self._direction == DIR_BUY:
                # Guard: closed below OB low again
                if current_close < self._ob_zone["low"]:
                    self._reset("BUY: closed below OB low post-sweep")
                    self._log_no_signal("Setup invalidated post-sweep")
                    return None

                if current_close > current_open:  # bullish candle
                    self._state        = WAITING_ENTRY
                    self._trigger_high = current_high  # break above this -> entry
                    self._sl_level     = current_low   # SL below this
                    self.logger.info(
                        "SMC BUY: first buy candle",
                        trigger_high=f"{current_high:.2f}",
                        sl=f"{current_low:.2f}",
                    )

            else:  # DIR_SELL
                # Guard: closed above OB high again
                if current_close > self._ob_zone["high"]:
                    self._reset("SELL: closed above OB high post-sweep")
                    self._log_no_signal("Setup invalidated post-sweep")
                    return None

                if current_close < current_open:  # bearish candle
                    self._state       = WAITING_ENTRY
                    self._trigger_low = current_low   # break below this -> entry
                    self._sl_level    = current_high  # SL above this
                    self.logger.info(
                        "SMC SELL: first sell candle",
                        trigger_low=f"{current_low:.2f}",
                        sl=f"{current_high:.2f}",
                    )

        # ==============================================================
        # PHASE 4 -> SIGNAL: Break of first-candle trigger level
        # ==============================================================
        if self._state == WAITING_ENTRY:
            assert self._ob_zone is not None
            assert self._sl_level is not None

            if self._direction == DIR_BUY:
                assert self._trigger_high is not None

                # Guard: closed below SL candidate
                if current_close < self._sl_level:
                    self._reset("BUY: SL candidate breached")
                    self._log_no_signal("SL breached before entry")
                    return None

                if current_high > self._trigger_high:
                    fvg_meta = self._check_fvg_confluence(bars, current_atr)
                    if self.require_fvg_confluence and fvg_meta is None:
                        self._log_no_signal("BUY: no unmitigated FVG near OB — skipping entry")
                        return None
                    return self._emit_signal(
                        side=OrderSide.BUY,
                        entry_price=current_close,
                        stop_loss=self._sl_level,
                        take_profit=self._ob_zone["high"] + self.liquidity_premium_mult * current_atr,
                        current_atr=current_atr,
                        current_adx=current_adx,
                        adx=adx,
                        regime=regime,
                        sweep_low=current_low,
                        fvg_meta=fvg_meta,
                    )

            else:  # DIR_SELL
                assert self._trigger_low is not None

                # Guard: closed above SL candidate
                if current_close > self._sl_level:
                    self._reset("SELL: SL candidate breached")
                    self._log_no_signal("SL breached before entry")
                    return None

                if current_low < self._trigger_low:
                    fvg_meta = self._check_fvg_confluence(bars, current_atr)
                    if self.require_fvg_confluence and fvg_meta is None:
                        self._log_no_signal("SELL: no unmitigated FVG near OB — skipping entry")
                        return None
                    return self._emit_signal(
                        side=OrderSide.SELL,
                        entry_price=current_close,
                        stop_loss=self._sl_level,
                        take_profit=self._ob_zone["low"] - self.liquidity_premium_mult * current_atr,
                        current_atr=current_atr,
                        current_adx=current_adx,
                        adx=adx,
                        regime=regime,
                        sweep_high=current_high,
                        fvg_meta=fvg_meta,
                    )

        self._log_no_signal(
            f"State: {self._state} | Dir: {self._direction} | OB age: {self._ob_age}"
        )
        return None

    # ------------------------------------------------------------------
    # Signal emission helper (shared for both directions)
    # ------------------------------------------------------------------

    def _check_fvg_confluence(
        self, bars: pd.DataFrame, current_atr: float
    ) -> Optional[Dict[str, float]]:
        """Return nearest unmitigated FVG near the OB, or None."""
        assert self._ob_zone is not None
        assert self._direction is not None
        proximity = self.fvg_ob_proximity_atr * current_atr
        return _find_unmitigated_fvg_near_ob(
            bars,
            self._ob_zone["high"],
            self._ob_zone["low"],
            self._direction,
            self.fvg_max_age_bars,
            proximity,
        )

    def _emit_signal(
        self,
        side: OrderSide,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        current_atr: float,
        current_adx: float,
        adx: pd.Series,
        regime: MarketRegime,
        sweep_low: float = 0.0,
        sweep_high: float = 0.0,
        fvg_meta: Optional[Dict[str, float]] = None,
    ) -> Optional[Signal]:
        """Validate, compute strength, reset state, and emit the signal."""
        assert self._ob_zone is not None

        # Validate RR
        if side == OrderSide.BUY:
            risk   = entry_price - stop_loss
            reward = take_profit - entry_price
        else:
            risk   = stop_loss - entry_price
            reward = entry_price - take_profit

        if risk <= 0:
            self._reset("Risk <= 0 — entry inside SL")
            self._log_no_signal("Invalid SL placement")
            return None

        # SL quality gate: reject noise trades and sloppy wide-stop entries
        sl_in_atr = risk / (current_atr + 1e-9)
        if sl_in_atr < self.min_sl_atr:
            self._reset(f"SL too small: {sl_in_atr:.2f} ATR < {self.min_sl_atr}")
            self._log_no_signal(f"SL too small ({sl_in_atr:.2f} ATR) — noise entry rejected")
            return None
        if sl_in_atr > self.max_sl_atr:
            self._reset(f"SL too large: {sl_in_atr:.2f} ATR > {self.max_sl_atr}")
            self._log_no_signal(f"SL too wide ({sl_in_atr:.2f} ATR) — sloppy OB rejected")
            return None

        # Enforce minimum TP floor
        min_tp_distance = self.min_liquidity_premium_mult * current_atr
        if reward < min_tp_distance:
            if side == OrderSide.BUY:
                take_profit = self._ob_zone["high"] + min_tp_distance
            else:
                take_profit = self._ob_zone["low"] - min_tp_distance
            reward = min_tp_distance

        rr_ratio = reward / risk

        # Signal strength
        prev_adx   = float(adx.iloc[-2]) if not np.isnan(adx.iloc[-2]) else current_adx
        adx_rising = current_adx > prev_adx
        adx_norm   = min((current_adx - self.adx_min_threshold) / 40.0, 1.0)
        adx_bonus  = 0.10 if adx_rising else 0.0

        # Sweep quality: depth of the wick past OB boundary relative to ATR
        if side == OrderSide.BUY:
            sweep_depth = max(0.0, self._ob_zone["low"] - sweep_low)
        else:
            sweep_depth = max(0.0, sweep_high - self._ob_zone["high"])
        sweep_ratio = min(sweep_depth / (current_atr + 1e-9), 1.0)

        strength = min(0.40 + sweep_ratio * 0.35 + adx_norm * 0.15 + adx_bonus, 1.0)

        # Snapshot before reset
        ob_high      = self._ob_zone["high"]
        ob_low       = self._ob_zone["low"]
        ob_age       = self._ob_age
        sweep_close  = self._sweep_close
        direction    = self._direction
        trigger_high = self._trigger_high
        trigger_low  = self._trigger_low
        sl_level     = self._sl_level

        self._reset("Signal emitted")
        self._bars_since_signal = 0

        self.logger.info(
            "SMC OB signal emitted",
            direction=direction,
            side=side.value,
            entry=f"{entry_price:.2f}",
            sl=f"{stop_loss:.2f}",
            tp=f"{take_profit:.2f}",
            rr=f"{rr_ratio:.2f}",
        )

        metadata = {
            "ob_direction": direction,
            "ob_high": round(ob_high, 2),
            "ob_low":  round(ob_low, 2),
            "ob_age_bars": ob_age,
            "sweep_close": round(sweep_close, 2) if sweep_close else None,
            "sweep_ratio": round(sweep_ratio, 3),
            "trigger_high": round(trigger_high, 2) if trigger_high else None,
            "trigger_low":  round(trigger_low, 2) if trigger_low else None,
            "sl_level": round(sl_level, 2) if sl_level else None,
            "rr_ratio": round(rr_ratio, 2),
            "atr": round(current_atr, 4),
            "adx": round(current_adx, 1),
            "adx_rising": adx_rising,
        }
        if fvg_meta is not None:
            metadata["fvg_top"]    = round(fvg_meta["top"], 2)
            metadata["fvg_bottom"] = round(fvg_meta["bottom"], 2)
            metadata["fvg_age"]    = int(fvg_meta["age"])

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )
