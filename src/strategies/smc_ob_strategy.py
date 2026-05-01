"""
SMC Order Block (OB) Strategy — Pure SMC/ICT trade logic for XAUUSD.
Trades BOTH BUY and SELL setups as mirror-image 5-phase state machines.

PDF-aligned filters (Advanced SMC & Price Action Course — Taufiq Sayyedd):
  - Module 5/6: Rejection-wick quality on sweep candle (min_rejection_wick_ratio).
  - Module 2:   Equal-Highs/Lows liquidity-target detection near OB zone.
  - Module 6:   Strong-close confirmation on the entry break candle.
  - Module 5:   Killzone session hours (config-only — session_hours already supported).

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

from typing import Optional, Dict, Any, List
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


def _has_rejection_wick(
    bar_high: float,
    bar_low: float,
    bar_open: float,
    bar_close: float,
    direction: str,
    min_ratio: float,
) -> bool:
    """Return True when the sweep candle shows a quality rejection wick.

    Module 5/6: "Strong momentum candle which sweeps HTF highs/lows" +
    "candle should close at highs or lows".

    For BUY sweeps the rejection wick is the LOWER wick; its length must be
    >= min_ratio × total candle range.  Mirror for SELL.

    Args:
        bar_high, bar_low, bar_open, bar_close: OHLC of the sweep candle.
        direction: DIR_BUY or DIR_SELL.
        min_ratio: Minimum wick-to-range ratio (0.0–1.0).

    Returns:
        True when the wick on the sweep side meets the quality threshold.
    """
    candle_range = bar_high - bar_low
    if candle_range <= 0:
        return False

    body_low = min(bar_open, bar_close)
    body_high = max(bar_open, bar_close)

    if direction == DIR_BUY:
        # Lower wick = distance from bar_low to body_low
        wick = body_low - bar_low
    else:
        # Upper wick = distance from body_high to bar_high
        wick = bar_high - body_high

    return (wick / candle_range) >= min_ratio


def _find_equal_pivots(
    bars: pd.DataFrame,
    direction: str,
    atr: float,
    epsilon_mult: float,
    min_pivots: int,
    lookback: int,
    ob_level: float,
    proximity_mult: float,
) -> Optional[Dict[str, Any]]:
    """Detect equal-highs or equal-lows clusters near the OB boundary.

    Module 2: "Above equal highs / Below equal lows / Tight consolidation
    zones". Liquidity sits at clusters of equal swing pivots — that's the
    magnet smart money targets.

    For BUY setups: find >= min_pivots swing-LOW pivots within
    epsilon_mult * ATR of each other, near the OB low (within
    proximity_mult * ATR).  Mirror for SELL.

    Args:
        bars: OHLCV DataFrame.
        direction: DIR_BUY or DIR_SELL.
        atr: Current ATR value.
        epsilon_mult: Max spread between pivots as ATR multiple.
        min_pivots: Minimum number of equal pivots to qualify.
        lookback: Number of bars to scan for pivots.
        ob_level: The OB boundary (low for BUY, high for SELL).
        proximity_mult: Max distance from ob_level as ATR multiple.

    Returns:
        {'count': int, 'level': float, 'spread': float} or None.
    """
    n = len(bars)
    if n < lookback + 4 or atr <= 0:
        return None

    epsilon = epsilon_mult * atr
    proximity = proximity_mult * atr
    start = max(0, n - lookback)

    hi = bars["high"].values
    lo = bars["low"].values

    # Collect simple 3-bar swing pivots in the scan window
    pivots: list[float] = []
    for i in range(start + 1, n - 1):
        if direction == DIR_BUY:
            # Swing low: low[i] < low[i-1] and low[i] < low[i+1]
            if lo[i] < lo[i - 1] and lo[i] < lo[i + 1]:
                if abs(lo[i] - ob_level) <= proximity:
                    pivots.append(float(lo[i]))
        else:
            # Swing high: high[i] > high[i-1] and high[i] > high[i+1]
            if hi[i] > hi[i - 1] and hi[i] > hi[i + 1]:
                if abs(hi[i] - ob_level) <= proximity:
                    pivots.append(float(hi[i]))

    if len(pivots) < min_pivots:
        return None

    # Cluster: sort and find the largest group within epsilon
    pivots.sort()
    best_count = 0
    best_level = 0.0
    best_spread = 0.0

    for i in range(len(pivots)):
        cluster = [pivots[i]]
        for j in range(i + 1, len(pivots)):
            if pivots[j] - pivots[i] <= epsilon:
                cluster.append(pivots[j])
            else:
                break
        if len(cluster) > best_count:
            best_count = len(cluster)
            best_level = sum(cluster) / len(cluster)
            best_spread = cluster[-1] - cluster[0]

    if best_count >= min_pivots:
        return {
            "count": best_count,
            "level": round(best_level, 2),
            "spread": round(best_spread, 4),
        }
    return None


def _is_strong_close(
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    direction: str,
    threshold: float,
) -> bool:
    """Return True when the entry-break candle closes in the strong zone.

    Module 6: "candle should close at highs or lows".
    BUY: close must be in the top `threshold` fraction of the candle range.
    SELL: close must be in the bottom `threshold` fraction.

    Args:
        bar_open, bar_high, bar_low, bar_close: OHLC of entry candle.
        direction: DIR_BUY or DIR_SELL.
        threshold: Fraction of range (e.g. 0.35 = top/bottom 35%).

    Returns:
        True when the close sits in the strong zone.
    """
    candle_range = bar_high - bar_low
    if candle_range <= 0:
        return True  # Doji — pass through, other filters gate quality

    position = (bar_close - bar_low) / candle_range  # 0.0 = low, 1.0 = high

    if direction == DIR_BUY:
        return position >= (1.0 - threshold)
    else:
        return position <= threshold



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
# BOS / CHoCH confluence helpers
# (adapted from joshyattridge/smart-money-concepts — MIT licensed)
#
# BOS (Break of Structure)     = trend continuation: HH-HL-HH-HL pattern with
#                                the prior swing high/low taken out.
# CHoCH (Change of Character)  = trend reversal: the counter-trend swing made
#                                an extreme in the OLD direction, then price
#                                broke the prior pivot in the NEW direction.
#
# Encoding: swing pattern over the last four pivots [hl_-4, hl_-3, hl_-2, hl_-1].
# For bullish events we require pattern [-1, 1, -1, 1] (low, high, low, high).
# For bearish events the mirror pattern [1, -1, 1, -1].
# The level that must break is the pivot at index -3.
# ---------------------------------------------------------------------------

def _compute_swing_highs_lows(bars: pd.DataFrame, swing_length: int) -> pd.DataFrame:
    """Return a DataFrame with HighLow (+1 high, -1 low, NaN otherwise) and Level.

    A swing high at bar i is the maximum high over the window [i-swing_length,
    i+swing_length]. Consecutive same-type swings are coalesced so only the
    extreme one survives.
    """
    sl2 = swing_length * 2
    hi = bars["high"]
    lo = bars["low"]
    hl = np.where(
        hi == hi.shift(-(sl2 // 2)).rolling(sl2).max(),
        1,
        np.where(lo == lo.shift(-(sl2 // 2)).rolling(sl2).min(), -1, np.nan),
    )

    while True:
        positions = np.where(~np.isnan(hl))[0]
        if len(positions) < 2:
            break
        cur  = hl[positions[:-1]]
        nxt  = hl[positions[1:]]
        h_cur = hi.iloc[positions[:-1]].values
        l_cur = lo.iloc[positions[:-1]].values
        h_nxt = hi.iloc[positions[1:]].values
        l_nxt = lo.iloc[positions[1:]].values
        drop = np.zeros(len(positions), dtype=bool)
        cons_h = (cur == 1) & (nxt == 1)
        drop[:-1] |= cons_h & (h_cur <  h_nxt)
        drop[1:]  |= cons_h & (h_cur >= h_nxt)
        cons_l = (cur == -1) & (nxt == -1)
        drop[:-1] |= cons_l & (l_cur >  l_nxt)
        drop[1:]  |= cons_l & (l_cur <= l_nxt)
        if not drop.any():
            break
        hl[positions[drop]] = np.nan

    level = np.where(
        ~np.isnan(hl),
        np.where(hl == 1, hi.values, lo.values),
        np.nan,
    )
    return pd.DataFrame({"HighLow": hl, "Level": level})


def _compute_bos_choch(
    bars: pd.DataFrame, shl: pd.DataFrame, close_break: bool = True
) -> pd.DataFrame:
    """Return BOS/CHOCH flags, level, and break index per bar.

    Follows the joshyattridge library semantics. An event is only reported
    after it has been *confirmed* by a future close (or wick, if close_break
    is False) breaking through the pivot level.
    """
    n = len(bars)
    bos   = np.zeros(n, dtype=np.int32)
    choch = np.zeros(n, dtype=np.int32)
    level = np.zeros(n, dtype=np.float64)

    hl_col = shl["HighLow"].values
    lv_col = shl["Level"].values

    hl_order: List[float] = []
    lv_order: List[float] = []
    positions: List[int]  = []

    for i in range(n):
        if np.isnan(hl_col[i]):
            continue
        hl_order.append(hl_col[i])
        lv_order.append(lv_col[i])
        positions.append(i)
        if len(hl_order) >= 4:
            last_pos = positions[-2]
            ho = hl_order[-4:]
            lo = lv_order[-4:]
            # bullish BOS: -1,1,-1,1 and L1 < L2 < H1 < H2
            if ho == [-1, 1, -1, 1] and lo[0] < lo[2] < lo[1] < lo[3]:
                bos[last_pos]   = 1
                level[last_pos] = lo[1]
            # bearish BOS: 1,-1,1,-1 and H1 > H2 > L1 > L2
            elif ho == [1, -1, 1, -1] and lo[0] > lo[2] > lo[1] > lo[3]:
                bos[last_pos]   = -1
                level[last_pos] = lo[1]
            # bullish CHoCH: -1,1,-1,1 and H2 > H1 > L1 > L2  (lower-low then break above prior high)
            elif ho == [-1, 1, -1, 1] and lo[3] > lo[1] > lo[0] > lo[2]:
                choch[last_pos] = 1
                level[last_pos] = lo[1]
            # bearish CHoCH: 1,-1,1,-1 and L2 < L1 < H1 < H2  (higher-high then break below prior low)
            elif ho == [1, -1, 1, -1] and lo[3] < lo[1] < lo[0] < lo[2]:
                choch[last_pos] = -1
                level[last_pos] = lo[1]

    broken = np.zeros(n, dtype=np.int32)
    events = np.where((bos != 0) | (choch != 0))[0]
    close_v = bars["close"].values
    high_v  = bars["high"].values
    low_v   = bars["low"].values
    for i in events:
        direction = bos[i] if bos[i] != 0 else choch[i]
        if direction == 1:
            future = close_v[i + 2:] if close_break else high_v[i + 2:]
            mask = future > level[i]
        else:
            future = close_v[i + 2:] if close_break else low_v[i + 2:]
            mask = future < level[i]
        if mask.size and mask.any():
            j = int(np.argmax(mask)) + i + 2
            broken[i] = j

    unbroken = ((bos != 0) | (choch != 0)) & (broken == 0)
    bos[unbroken]   = 0
    choch[unbroken] = 0
    level[unbroken] = 0

    return pd.DataFrame({
        "BOS":         bos,
        "CHOCH":       choch,
        "Level":       level,
        "BrokenIndex": broken,
    })


def _find_recent_structure_shift(
    bars: pd.DataFrame,
    direction: str,
    swing_length: int,
    max_age_bars: int,
    accept_bos: bool,
) -> Optional[Dict[str, Any]]:
    """Return the most recent confirmed BOS/CHoCH matching the trade direction.

    Args:
        bars: OHLCV DataFrame.
        direction: DIR_BUY -> bullish shifts (+1); DIR_SELL -> bearish (-1).
        swing_length: Swing-detection window (half-width).
        max_age_bars: Shift must have been *confirmed* within this many bars.
        accept_bos: If False, only CHoCH qualifies (stricter reversal signal).

    Returns:
        {'type': 'choch'|'bos', 'level': float, 'confirmed_age': int,
         'pivot_age': int} or None.
    """
    n = len(bars)
    if n < 4 * swing_length + 4:
        return None

    # Restrict to recent window for performance (swing detection is O(n))
    window_size = max(200, max_age_bars + 4 * swing_length + 10)
    if n > window_size:
        bars = bars.iloc[-window_size:].reset_index(drop=True)
    else:
        bars = bars.reset_index(drop=True)

    m = len(bars)
    shl = _compute_swing_highs_lows(bars, swing_length)
    bc  = _compute_bos_choch(bars, shl)

    want = 1 if direction == DIR_BUY else -1
    bos_v   = bc["BOS"].values
    choch_v = bc["CHOCH"].values
    level_v = bc["Level"].values
    brk_v   = bc["BrokenIndex"].values

    # Walk backward over pivots; take the most recent confirmed match.
    for i in range(m - 1, -1, -1):
        is_choch = choch_v[i] == want
        is_bos   = bos_v[i]   == want and accept_bos
        if not (is_choch or is_bos):
            continue
        broken_at = int(brk_v[i])
        if broken_at == 0:
            continue  # not confirmed
        confirmed_age = (m - 1) - broken_at
        if confirmed_age > max_age_bars:
            continue
        return {
            "type":          "choch" if is_choch else "bos",
            "level":         float(level_v[i]),
            "confirmed_age": confirmed_age,
            "pivot_age":     (m - 1) - i,
        }
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

        # PDF Module 5/6: Rejection-wick quality on the sweep candle.
        # Wick on sweep side must be >= this fraction of the candle range.
        self.min_rejection_wick_ratio: float = config.get("min_rejection_wick_ratio", 0.40)

        # PDF Module 2: Equal-highs/lows liquidity-target requirement.
        # When enabled, the sweep wick must take out a cluster of equal pivots.
        self.require_equal_pivots: bool         = config.get("require_equal_pivots", False)
        self.equal_pivot_epsilon_atr: float     = config.get("equal_pivot_epsilon_atr", 0.15)
        self.equal_pivot_min_count: int         = config.get("equal_pivot_min_count", 2)
        self.equal_pivot_lookback: int          = config.get("equal_pivot_lookback", 60)
        self.equal_pivot_proximity_atr: float   = config.get("equal_pivot_proximity_atr", 1.5)

        # PDF Module 6: Strong-close on the entry break candle.
        # Close must sit in top/bottom `strong_close_threshold` of the range.
        self.require_strong_close: bool      = config.get("require_strong_close", True)
        self.strong_close_threshold: float   = config.get("strong_close_threshold", 0.40)

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

        # BOS/CHoCH (structure-shift) confluence filter — opt-in ICT gate.
        # When enabled, entry requires a recent confirmed market-structure shift
        # in the trade direction. CHoCH is a reversal break (stricter); BOS is a
        # continuation break. `bos_choch_accept_bos=false` uses CHoCH only.
        self.require_bos_choch_confluence: bool = config.get("require_bos_choch_confluence", False)
        self.bos_choch_swing_length: int        = config.get("bos_choch_swing_length", 10)
        self.bos_choch_max_age_bars: int        = config.get("bos_choch_max_age_bars", 60)
        self.bos_choch_accept_bos: bool         = config.get("bos_choch_accept_bos", True)

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
        self._equal_pivots_meta: Optional[Dict[str, Any]] = None

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
        self._equal_pivots_meta = None
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
                    # PDF Module 5/6: Rejection-wick quality gate
                    if not _has_rejection_wick(
                        current_high, current_low, current_open, current_close,
                        DIR_BUY, self.min_rejection_wick_ratio,
                    ):
                        self._log_no_signal("BUY sweep: rejection wick too small")
                        return None

                    # PDF Module 2: Equal-lows liquidity target
                    if self.require_equal_pivots:
                        eq = _find_equal_pivots(
                            bars, DIR_BUY, current_atr,
                            self.equal_pivot_epsilon_atr,
                            self.equal_pivot_min_count,
                            self.equal_pivot_lookback,
                            self._ob_zone["low"],
                            self.equal_pivot_proximity_atr,
                        )
                        if eq is None:
                            self._log_no_signal("BUY sweep: no equal-lows cluster near OB")
                            return None
                        self._equal_pivots_meta = eq

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
                    # PDF Module 5/6: Rejection-wick quality gate
                    if not _has_rejection_wick(
                        current_high, current_low, current_open, current_close,
                        DIR_SELL, self.min_rejection_wick_ratio,
                    ):
                        self._log_no_signal("SELL sweep: rejection wick too small")
                        return None

                    # PDF Module 2: Equal-highs liquidity target
                    if self.require_equal_pivots:
                        eq = _find_equal_pivots(
                            bars, DIR_SELL, current_atr,
                            self.equal_pivot_epsilon_atr,
                            self.equal_pivot_min_count,
                            self.equal_pivot_lookback,
                            self._ob_zone["high"],
                            self.equal_pivot_proximity_atr,
                        )
                        if eq is None:
                            self._log_no_signal("SELL sweep: no equal-highs cluster near OB")
                            return None
                        self._equal_pivots_meta = eq

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
                    # PDF Module 6: Strong-close confirmation
                    if self.require_strong_close and not _is_strong_close(
                        current_open, current_high, current_low, current_close,
                        DIR_BUY, self.strong_close_threshold,
                    ):
                        self._log_no_signal("BUY: entry candle close not in top zone")
                        return None

                    fvg_meta = self._check_fvg_confluence(bars, current_atr)
                    if self.require_fvg_confluence and fvg_meta is None:
                        self._log_no_signal("BUY: no unmitigated FVG near OB — skipping entry")
                        return None
                    structure_meta = self._check_structure_confluence(bars)
                    if self.require_bos_choch_confluence and structure_meta is None:
                        self._log_no_signal("BUY: no bullish BOS/CHoCH recently — skipping entry")
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
                        structure_meta=structure_meta,
                        equal_pivots_meta=getattr(self, '_equal_pivots_meta', None),
                    )

            else:  # DIR_SELL
                assert self._trigger_low is not None

                # Guard: closed above SL candidate
                if current_close > self._sl_level:
                    self._reset("SELL: SL candidate breached")
                    self._log_no_signal("SL breached before entry")
                    return None

                if current_low < self._trigger_low:
                    # PDF Module 6: Strong-close confirmation
                    if self.require_strong_close and not _is_strong_close(
                        current_open, current_high, current_low, current_close,
                        DIR_SELL, self.strong_close_threshold,
                    ):
                        self._log_no_signal("SELL: entry candle close not in bottom zone")
                        return None

                    fvg_meta = self._check_fvg_confluence(bars, current_atr)
                    if self.require_fvg_confluence and fvg_meta is None:
                        self._log_no_signal("SELL: no unmitigated FVG near OB — skipping entry")
                        return None
                    structure_meta = self._check_structure_confluence(bars)
                    if self.require_bos_choch_confluence and structure_meta is None:
                        self._log_no_signal("SELL: no bearish BOS/CHoCH recently — skipping entry")
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
                        structure_meta=structure_meta,
                        equal_pivots_meta=getattr(self, '_equal_pivots_meta', None),
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

    def _check_structure_confluence(
        self, bars: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """Return most recent confirmed BOS/CHoCH in trade direction, or None."""
        assert self._direction is not None
        return _find_recent_structure_shift(
            bars,
            self._direction,
            self.bos_choch_swing_length,
            self.bos_choch_max_age_bars,
            self.bos_choch_accept_bos,
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
        structure_meta: Optional[Dict[str, Any]] = None,
        equal_pivots_meta: Optional[Dict[str, Any]] = None,
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

        # Strict 1:2 RR — TP is always reward = 2 × risk regardless of OB
        # geometry. The OB-derived TP at the call site is overwritten so the
        # strategy never ships a sub-2R trade.
        reward = 2.0 * risk
        if side == OrderSide.BUY:
            take_profit = entry_price + reward
        else:
            take_profit = entry_price - reward

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
        if structure_meta is not None:
            metadata["structure_type"]  = structure_meta["type"]
            metadata["structure_level"] = round(structure_meta["level"], 2)
            metadata["structure_confirmed_age"] = int(structure_meta["confirmed_age"])
            metadata["structure_pivot_age"]     = int(structure_meta["pivot_age"])
        if equal_pivots_meta is not None:
            metadata["equal_pivot_count"]  = int(equal_pivots_meta["count"])
            metadata["equal_pivot_level"]  = equal_pivots_meta["level"]
            metadata["equal_pivot_spread"] = equal_pivots_meta["spread"]

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata=metadata,
        )
