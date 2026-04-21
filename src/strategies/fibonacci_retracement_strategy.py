"""
Fibonacci Retracement Golden Zone Strategy — trades pullbacks into the 50%-61.8% zone.

Concept (evolvedaytrading):
1. Find A Swing — identify swing low → swing high (uptrend) or vice versa
2. Apply Fibonacci — compute retracement levels from the swing pair
3. Wait for Retracement — price pulls back into the Golden Zone (50%–61.8%)
4. Plan Entry — enter on rejection candle from the Golden Zone

The "Golden Zone" (50%–61.8%) is where institutional/smart money watches
for pullback entries. The 61.8% level (Golden Ratio) is considered the
most important Fibonacci retracement level.

Filter stack (6 core filters):
1. Valid swing pair detected (swing low/high with minimum ATR-scaled size)
2. Price enters the Golden Zone (between 50% and 61.8% retracement)
3. Rejection candle quality (wick toward zone boundary confirms bounce)
4. ADX > threshold (trend still active — avoid ranging markets)
5. RSI not extreme (avoid chasing exhausted moves)
6. EMA trend alignment (only trade in direction of the trend)

Design: all detection logic is in pure functions that read inputs and
return values. The only mutable state is _active_swing and
_bars_since_signal, both visible at the on_bar() call site. (Carmack rule)
"""

from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


# ── Fibonacci level constants ────────────────────────────────────────────────
# Standard retracement levels drawn from swing low (1.0) to swing high (0.0)
FIB_LEVELS = {
    "0.0": 0.0,
    "0.236": 0.236,
    "0.382": 0.382,
    "0.5": 0.5,
    "0.618": 0.618,
    "0.786": 0.786,
    "1.0": 1.0,
}


def _find_swing_points(
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int,
) -> List[Dict[str, Any]]:
    """Detect swing highs and swing lows using N-bar pivot logic.

    A swing high is a bar whose high is the highest of the surrounding
    2*lookback+1 bars. A swing low is a bar whose low is the lowest.

    Args:
        highs: Array of high prices.
        lows: Array of low prices.
        lookback: Number of bars on each side to confirm a pivot.

    Returns:
        List of dicts with 'type' ('high'|'low'), 'price', and 'index'.
        Sorted by index ascending.
    """
    n = len(highs)
    swings: List[Dict[str, Any]] = []

    for i in range(lookback, n - lookback):
        # Swing high: bar's high >= all neighbours within lookback window
        window_highs = highs[i - lookback: i + lookback + 1]
        if highs[i] == np.max(window_highs):
            swings.append({"type": "high", "price": float(highs[i]), "index": i})

        # Swing low: bar's low <= all neighbours within lookback window
        window_lows = lows[i - lookback: i + lookback + 1]
        if lows[i] == np.min(window_lows):
            swings.append({"type": "low", "price": float(lows[i]), "index": i})

    return swings


def _find_latest_swing_pair(
    swings: List[Dict[str, Any]],
    min_swing_size: float,
    total_bars: int,
    max_age_bars: int,
) -> Optional[Dict[str, Any]]:
    """Find the most recent valid swing low→high (bullish) or high→low (bearish) pair.

    Scans swings in reverse order to find the freshest pair where:
    - The two points are of opposite type (high/low)
    - The swing size exceeds the minimum ATR-scaled threshold
    - Neither point is older than max_age_bars from the current bar

    Args:
        swings: List from _find_swing_points, sorted by index.
        min_swing_size: Minimum price distance between swing high and low.
        total_bars: Total number of bars in the DataFrame (for age check).
        max_age_bars: Maximum age of swing points in bars.

    Returns:
        Dict with 'direction' ('bullish'|'bearish'), 'swing_high', 'swing_low',
        'swing_high_index', 'swing_low_index'. None if no valid pair found.
    """
    if len(swings) < 2:
        return None

    # Walk backwards through swings to find the most recent pair
    for i in range(len(swings) - 1, 0, -1):
        recent = swings[i]
        prev = swings[i - 1]

        # Both points must be within max_age_bars of the current bar
        recent_age = total_bars - 1 - recent["index"]
        prev_age = total_bars - 1 - prev["index"]
        if recent_age > max_age_bars or prev_age > max_age_bars:
            continue

        # Must be opposite types
        if recent["type"] == prev["type"]:
            continue

        # Determine direction
        if prev["type"] == "low" and recent["type"] == "high":
            # Bullish swing: low → high (uptrend, look for pullback to buy)
            swing_low = prev["price"]
            swing_high = recent["price"]
            swing_size = swing_high - swing_low
            if swing_size >= min_swing_size:
                return {
                    "direction": "bullish",
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                    "swing_high_index": recent["index"],
                    "swing_low_index": prev["index"],
                }

        elif prev["type"] == "high" and recent["type"] == "low":
            # Bearish swing: high → low (downtrend, look for pullback to sell)
            swing_high = prev["price"]
            swing_low = recent["price"]
            swing_size = swing_high - swing_low
            if swing_size >= min_swing_size:
                return {
                    "direction": "bearish",
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                    "swing_high_index": prev["index"],
                    "swing_low_index": recent["index"],
                }

    return None


def _calculate_fib_levels(
    swing_high: float,
    swing_low: float,
) -> Dict[str, float]:
    """Compute Fibonacci retracement price levels from a swing pair.

    For a bullish swing (low→high), retracement levels measure how far
    price has pulled back from the high toward the low:
    - 0.0 = swing high (no retracement)
    - 0.5 = halfway between high and low
    - 0.618 = Golden Ratio level
    - 1.0 = swing low (full retracement)

    Args:
        swing_high: The swing high price.
        swing_low: The swing low price.

    Returns:
        Dict mapping level name to price. E.g. {"0.5": 85000.0, "0.618": 82000.0}
    """
    swing_range = swing_high - swing_low
    levels = {}
    for name, ratio in FIB_LEVELS.items():
        # Retracement = high - (range × ratio)
        levels[name] = swing_high - (swing_range * ratio)
    return levels


def _check_golden_zone_entry(
    current_low: float,
    current_high: float,
    fib_50: float,
    fib_618: float,
    direction: str,
) -> bool:
    """Check if the current bar's price action touches the Golden Zone.

    For bullish setups: the bar's low must reach into the zone (pullback down).
    For bearish setups: the bar's high must reach into the zone (pullback up).

    Args:
        current_low: Current bar low price.
        current_high: Current bar high price.
        fib_50: Price at the 50% retracement level.
        fib_618: Price at the 61.8% retracement level.
        direction: 'bullish' or 'bearish'.

    Returns:
        True if price entered the Golden Zone on this bar.
    """
    # Zone boundaries (fib_50 is closer to swing high, fib_618 is closer to swing low)
    zone_top = fib_50
    zone_bottom = fib_618

    if direction == "bullish":
        # Pullback DOWN into zone: bar's low must be at or below zone_top
        # and bar's high must be at or above zone_bottom (bar touches zone)
        return current_low <= zone_top and current_high >= zone_bottom
    else:
        # Pullback UP into zone: bar's high must be at or above zone_bottom
        # and bar's low must be at or below zone_top (bar touches zone)
        return current_high >= zone_bottom and current_low <= zone_top


def _calculate_rejection_strength(
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    direction: str,
) -> float:
    """Calculate how strongly the bar rejected from the Golden Zone.

    For a bullish retracement (pullback into zone from above):
    - We want a long lower wick (buyers stepped in at the zone)
    - Close should be above open (bullish bar)

    For a bearish retracement (pullback into zone from below):
    - We want a long upper wick (sellers stepped in at the zone)
    - Close should be below open (bearish bar)

    Args:
        bar_open: Bar open price.
        bar_high: Bar high price.
        bar_low: Bar low price.
        bar_close: Bar close price.
        direction: 'bullish' or 'bearish'.

    Returns:
        Rejection ratio 0.0–1.0. Higher = stronger rejection.
    """
    bar_range = bar_high - bar_low
    if bar_range <= 0:
        return 0.0

    if direction == "bullish":
        # Lower wick = buyers rejecting from below. Want close > open.
        rejection_wick = min(bar_open, bar_close) - bar_low
        # Bonus: bullish candle confirmation (close > open)
        is_confirming = bar_close > bar_open
    else:
        # Upper wick = sellers rejecting from above. Want close < open.
        rejection_wick = bar_high - max(bar_open, bar_close)
        # Bonus: bearish candle confirmation (close < open)
        is_confirming = bar_close < bar_open

    wick_ratio = rejection_wick / bar_range

    # Penalize non-confirming candles (e.g. bearish candle on a bullish setup)
    if not is_confirming:
        wick_ratio *= 0.6

    return min(wick_ratio, 1.0)


class FibonacciRetracementStrategy(BaseStrategy):
    """Trades pullbacks into the Fibonacci Golden Zone (50%–61.8%)."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Swing detection
        self.swing_lookback = config.get("swing_lookback", 5)
        self.min_swing_atr_mult = config.get("min_swing_atr_mult", 2.0)
        self.swing_max_age_bars = config.get("swing_max_age_bars", 100)

        # Golden Zone boundaries
        self.golden_zone_upper = config.get("golden_zone_upper", 0.618)
        self.golden_zone_lower = config.get("golden_zone_lower", 0.50)

        # Rejection quality
        self.min_rejection_ratio = config.get("min_rejection_ratio", 0.50)

        # Confirmation filters
        self.adx_min_threshold = config.get("adx_min_threshold", 20)
        self.adx_max_threshold = config.get("adx_max_threshold", 55)
        self.rsi_overbought = config.get("rsi_overbought", 75)
        self.rsi_oversold = config.get("rsi_oversold", 25)
        self.ema_trend_period = config.get("ema_trend_period", 50)
        self.only_in_regime = MarketRegime[config.get("only_in_regime", "TREND")]

        # Direction and cooldown
        self.long_only = config.get("long_only", False)
        self.cooldown_bars = config.get("cooldown_bars", 8)
        self.min_strength = config.get("min_strength", 0.0)
        self.session_hours = config.get("session_hours", None)

        # Cooldown state — Carmack: visible at on_bar() call site
        self._bars_since_signal = self.cooldown_bars  # Allow first trade immediately

    def get_name(self) -> str:
        return "fibonacci_retracement"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        # O(1) slice — Jeff Dean rule
        bars = bars.tail(400)

        min_bars = self.swing_lookback * 2 + 50
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        # ── Cooldown gate ────────────────────────────────────────────────
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(
                f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars"
            )
            return None

        # ── Session filter ───────────────────────────────────────────────
        if self.session_hours is not None:
            bar_hour = self._get_bar_hour(bars)
            if bar_hour is not None and bar_hour not in self.session_hours:
                self._log_no_signal(f"Outside session hours: {bar_hour}")
                return None

        # ── Regime ───────────────────────────────────────────────────────
        regime = (
            self.ml_regime if self.ml_regime is not None else self.only_in_regime
        )

        # ── Indicators ───────────────────────────────────────────────────
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        ema_trend = Indicators.ema(bars, period=self.ema_trend_period)

        current_open = float(bars["open"].iloc[-1])
        current_high = float(bars["high"].iloc[-1])
        current_low = float(bars["low"].iloc[-1])
        current_close = float(bars["close"].iloc[-1])
        current_atr = float(atr.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_adx = float(adx.iloc[-1])
        current_ema = float(ema_trend.iloc[-1])

        if any(
            np.isnan(v)
            for v in [current_atr, current_rsi, current_adx, current_ema]
        ):
            self._log_no_signal("Indicator calculation failed")
            return None

        # ── Phase 1: Find swing points ───────────────────────────────────
        highs = bars["high"].values.astype(float)
        lows = bars["low"].values.astype(float)

        swings = _find_swing_points(highs, lows, self.swing_lookback)

        min_swing_size = self.min_swing_atr_mult * current_atr
        swing_pair = _find_latest_swing_pair(
            swings,
            min_swing_size=min_swing_size,
            total_bars=len(bars),
            max_age_bars=self.swing_max_age_bars,
        )

        if swing_pair is None:
            self._log_no_signal("No valid swing pair found")
            return None

        direction = swing_pair["direction"]
        swing_high = swing_pair["swing_high"]
        swing_low = swing_pair["swing_low"]

        # ── Phase 2: Calculate Fibonacci levels ──────────────────────────
        fib_levels = _calculate_fib_levels(swing_high, swing_low)
        fib_50 = fib_levels["0.5"]
        fib_618 = fib_levels["0.618"]

        # ── Phase 3: Check Golden Zone entry ─────────────────────────────
        in_golden_zone = _check_golden_zone_entry(
            current_low, current_high, fib_50, fib_618, direction,
        )

        if not in_golden_zone:
            self._log_no_signal(
                f"Not in Golden Zone: price {current_close:.2f}, "
                f"zone [{fib_618:.2f} – {fib_50:.2f}]"
            )
            return None

        # ── Phase 4: Rejection candle confirmation ───────────────────────
        rejection = _calculate_rejection_strength(
            current_open, current_high, current_low, current_close, direction,
        )

        if rejection < self.min_rejection_ratio:
            self._log_no_signal(
                f"Weak rejection: {rejection:.2f} < {self.min_rejection_ratio}"
            )
            return None

        # ── Phase 5: Confirmation filters ────────────────────────────────
        # ADX must confirm trend is active
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(
                f"ADX too low: {current_adx:.1f} < {self.adx_min_threshold}"
            )
            return None

        if current_adx > self.adx_max_threshold:
            self._log_no_signal(
                f"ADX too high: {current_adx:.1f} > {self.adx_max_threshold}"
            )
            return None

        # RSI not extreme
        if direction == "bullish" and current_rsi > self.rsi_overbought:
            self._log_no_signal(f"RSI overbought: {current_rsi:.1f}")
            return None

        if direction == "bearish" and current_rsi < self.rsi_oversold:
            self._log_no_signal(f"RSI oversold: {current_rsi:.1f}")
            return None

        # EMA trend alignment — only trade in direction of the prevailing trend
        if direction == "bullish" and current_close < current_ema:
            self._log_no_signal(
                f"EMA trend bearish: close {current_close:.2f} < EMA {current_ema:.2f}"
            )
            return None
        if direction == "bearish" and current_close > current_ema:
            self._log_no_signal(
                f"EMA trend bullish: close {current_close:.2f} > EMA {current_ema:.2f}"
            )
            return None

        # ── Phase 6: Direction gate ──────────────────────────────────────
        if direction == "bullish":
            side = OrderSide.BUY
        else:
            if self.long_only:
                self._log_no_signal("Long-only mode: rejecting SELL")
                return None
            side = OrderSide.SELL

        # ── Phase 7: Compute signal strength ─────────────────────────────
        # Strength formula: weighted combination of rejection quality,
        # ADX momentum, and proximity to the 61.8 level (deeper = stronger)
        swing_range = swing_high - swing_low
        if swing_range > 0:
            # How deep into the golden zone: 0.0 at fib_50, 1.0 at fib_618
            if direction == "bullish":
                depth = (fib_50 - current_close) / (fib_50 - fib_618)
            else:
                depth = (current_close - fib_50) / (fib_618 - fib_50)
            depth = max(0.0, min(1.0, depth))
        else:
            depth = 0.0

        adx_norm = min(
            (current_adx - self.adx_min_threshold) / 50.0, 1.0
        )
        strength = min(
            0.35 + rejection * 0.30 + depth * 0.20 + adx_norm * 0.15,
            1.0,
        )

        # Strength gate
        if self.min_strength > 0 and strength < self.min_strength:
            self._log_no_signal(
                f"Strength too low: {strength:.2f} < {self.min_strength}"
            )
            return None

        # ── Emit pure signal ─────────────────────────────────────────────
        self._bars_since_signal = 0

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                "atr": current_atr,
                "swing_high": swing_high,
                "swing_low": swing_low,
                "fib_50": fib_50,
                "fib_618": fib_618,
                "rejection_ratio": round(rejection, 3),
                "golden_zone_depth": round(depth, 3),
                "adx": current_adx,
                "rsi": current_rsi,
            },
        )
