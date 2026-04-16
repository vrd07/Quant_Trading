"""
Descending Channel Breakout (DCB) Strategy — trades structure shifts within
descending channels.

Based on multi-timeframe chart analysis pattern:
1. Detect descending channel via linear regression on highs and lows
2. Identify structure shift: Higher Low (HL) formations within the channel
3. Enter on confirmed breakout above channel resistance (bullish continuation)
   or rejection from resistance (bearish counter-trend, lower R:R)

Pattern lifecycle:
1. Price is in a descending channel (correction phase)
2. Strong rejection from lower boundary → bullish reaction
3. Higher Low (HL) formation detected (structure shift)
4. Price enters compression zone between dynamic resistance and rising support
5. Breakout above upper trendline with quality bar → BUY signal
6. OR: rejection from upper trendline with weak HL structure → SELL signal

Filter stack (7 core filters):
1. Descending channel detected (linear regression slopes both negative)
2. Structure shift confirmed (min_hl_count Higher Lows formed)
3. Breakout / rejection confirmed at channel boundary
4. Bullish candle quality (body > min_body_atr_ratio × ATR)
5. ADX within range (trend active, not overheated)
6. RSI not extreme (avoid chasing exhausted moves)
7. EMA trend alignment (breakout direction matches 50-EMA)

Design: pure functions for channel/swing detection. Mutable state limited to
_swing_lows tracking list and _bars_since_signal cooldown. (Carmack rule)
"""

from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


def _linear_regression_slope(values: np.ndarray) -> float:
    """Calculate the slope of a simple linear regression over the values array.

    Uses the closed-form least-squares formula for speed:
        slope = (n * Σ(x*y) - Σx * Σy) / (n * Σ(x²) - (Σx)²)

    Args:
        values: 1-D array of floats (e.g. highs or lows over lookback).

    Returns:
        Slope as a float.  Negative = descending, positive = ascending.
    """
    n = len(values)
    if n < 3:
        return 0.0
    x = np.arange(n, dtype=np.float64)
    y = values.astype(np.float64)
    sum_x = x.sum()
    sum_y = y.sum()
    sum_xy = (x * y).sum()
    sum_x2 = (x * x).sum()
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


def _linear_regression_value(values: np.ndarray, index: int) -> float:
    """Return the regression line value at a given index.

    Args:
        values: 1-D array used for regression (e.g. last N highs).
        index: Position in the array to evaluate (typically len-1 for current).

    Returns:
        The y-value on the regression line at the given index.
    """
    n = len(values)
    if n < 3:
        return float(values[-1]) if len(values) > 0 else 0.0
    slope = _linear_regression_slope(values)
    x = np.arange(n, dtype=np.float64)
    y = values.astype(np.float64)
    intercept = y.mean() - slope * x.mean()
    return slope * index + intercept


def _find_swing_lows(
    lows: np.ndarray,
    period: int,
) -> List[Tuple[int, float]]:
    """Identify swing lows in the price series.

    A swing low is a bar whose low is the minimum within ±period bars.

    Args:
        lows: Array of low prices.
        period: Number of bars on each side to compare.

    Returns:
        List of (index, low_value) tuples for each detected swing low,
        ordered by index ascending.
    """
    swing_lows = []
    for i in range(period, len(lows) - period):
        window = lows[max(0, i - period): i + period + 1]
        if lows[i] == window.min():
            swing_lows.append((i, float(lows[i])))
    return swing_lows


def _count_higher_lows(swing_lows: List[Tuple[int, float]]) -> int:
    """Count consecutive Higher Lows from the most recent swing lows.

    Scans backwards from the end of the swing low list and counts how many
    successive lows are higher than the previous one.

    Args:
        swing_lows: List of (index, value) tuples, sorted by index.

    Returns:
        Number of consecutive Higher Lows (0 if none, 1+ if pattern forming).
    """
    if len(swing_lows) < 2:
        return 0
    count = 0
    for i in range(len(swing_lows) - 1, 0, -1):
        if swing_lows[i][1] > swing_lows[i - 1][1]:
            count += 1
        else:
            break
    return count


class DescendingChannelBreakoutStrategy(BaseStrategy):
    """Trades structure shifts within descending channels."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Channel detection
        self.channel_lookback = config.get("channel_lookback", 100)
        self.swing_period = config.get("swing_period", 10)
        self.min_hl_count = config.get("min_hl_count", 2)
        self.channel_slope_max = config.get("channel_slope_max", -0.001)
        self.only_in_regime = MarketRegime[config.get("only_in_regime", "TREND")]

        # Breakout/rejection thresholds
        self.breakout_atr_buffer = config.get("breakout_atr_buffer", 0.3)
        self.demand_zone_atr_mult = config.get("demand_zone_atr_mult", 1.5)
        self.rejection_min_ratio = config.get("rejection_min_ratio", 0.50)
        self.min_body_atr_ratio = config.get("min_body_atr_ratio", 0.30)

        # Confirmation filters
        self.adx_min_threshold = config.get("adx_min_threshold", 20)
        self.adx_max_threshold = config.get("adx_max_threshold", 55)
        self.rsi_overbought = config.get("rsi_overbought", 75)
        self.rsi_oversold = config.get("rsi_oversold", 25)
        self.ema_trend_period = config.get("ema_trend_period", 50)

        # Risk/execution
        self.long_only = config.get("long_only", False)
        self.cooldown_bars = config.get("cooldown_bars", 8)
        self._bars_since_signal = self.cooldown_bars  # Allow first trade immediately

        # Session filter
        self.session_hours = config.get("session_hours", None)

        # Strength gate
        self.min_strength = config.get("min_strength", 0.60)

    def get_name(self) -> str:
        return "descending_channel_breakout"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        # O(1) slice — process last 400 bars regardless of history depth
        bars = bars.tail(400)

        min_bars = self.channel_lookback + self.swing_period + 10
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
            self.ml_regime if self.ml_regime is not None else MarketRegime.TREND
        )

        # ── Indicators ───────────────────────────────────────────────────
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        ema_trend = Indicators.ema(bars, period=self.ema_trend_period)

        current_close = float(bars["close"].iloc[-1])
        current_open = float(bars["open"].iloc[-1])
        current_high = float(bars["high"].iloc[-1])
        current_low = float(bars["low"].iloc[-1])
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

        # ── Phase 1: Channel Detection ───────────────────────────────────
        lookback_bars = bars.tail(self.channel_lookback)
        highs = lookback_bars["high"].values
        lows = lookback_bars["low"].values

        slope_high = _linear_regression_slope(highs)
        slope_low = _linear_regression_slope(lows)

        # Both slopes must be negative (descending channel)
        is_descending = (
            slope_high < self.channel_slope_max
            and slope_low < self.channel_slope_max
        )

        if not is_descending:
            self._log_no_signal(
                f"No descending channel: slope_high={slope_high:.6f}, "
                f"slope_low={slope_low:.6f}"
            )
            return None

        # Calculate channel boundaries at the current bar
        channel_upper = _linear_regression_value(highs, len(highs) - 1)
        channel_lower = _linear_regression_value(lows, len(lows) - 1)

        # ── Phase 2: Structure Shift Detection ───────────────────────────
        swing_lows = _find_swing_lows(lows, self.swing_period)
        hl_count = _count_higher_lows(swing_lows)

        has_structure_shift = hl_count >= self.min_hl_count

        if not has_structure_shift:
            self._log_no_signal(
                f"No structure shift: {hl_count} HL(s) < {self.min_hl_count} required"
            )
            return None

        # ── Phase 3: Entry Logic ─────────────────────────────────────────
        breakout_threshold = channel_upper + self.breakout_atr_buffer * current_atr
        bar_body = abs(current_close - current_open)
        min_body = current_atr * self.min_body_atr_ratio

        # Bullish breakout: price closes above descending trendline
        is_bullish_breakout = (
            current_close > breakout_threshold
            and current_close > current_open  # Bullish candle
            and bar_body >= min_body           # Quality bar
        )

        # Bearish rejection: price rejects from upper trendline
        rejection_zone_upper = channel_upper + self.breakout_atr_buffer * current_atr
        is_near_resistance = current_high >= channel_upper - 0.2 * current_atr
        upper_wick = current_high - max(current_open, current_close)
        bar_range = current_high - current_low
        rejection_ratio = (upper_wick / bar_range) if bar_range > 0 else 0.0

        is_bearish_rejection = (
            is_near_resistance
            and not is_bullish_breakout
            and current_close < current_open   # Bearish candle
            and rejection_ratio >= self.rejection_min_ratio
            and current_close < channel_upper  # Closed below resistance
        )

        if not is_bullish_breakout and not is_bearish_rejection:
            # Check if in compression zone — log informatively
            if current_close > channel_lower and current_close < channel_upper:
                self._log_no_signal(
                    f"Compression zone — Wait: price between "
                    f"channel [{channel_lower:.2f}, {channel_upper:.2f}]"
                )
            else:
                self._log_no_signal("No breakout or rejection at channel boundary")
            return None

        # ── Phase 4: Confirmation Filters ────────────────────────────────
        # ADX must be in range
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
        if is_bullish_breakout and current_rsi > self.rsi_overbought:
            self._log_no_signal(f"RSI overbought: {current_rsi:.1f}")
            return None

        if is_bearish_rejection and current_rsi < self.rsi_oversold:
            self._log_no_signal(f"RSI oversold: {current_rsi:.1f}")
            return None

        # EMA trend alignment
        if is_bullish_breakout and current_close < current_ema:
            self._log_no_signal(
                f"EMA bearish: close {current_close:.2f} < EMA {current_ema:.2f}"
            )
            return None

        if is_bearish_rejection and current_close > current_ema:
            self._log_no_signal(
                f"EMA bullish: close {current_close:.2f} > EMA {current_ema:.2f}"
            )
            return None

        # ── Phase 5: Direction Gate ──────────────────────────────────────
        if is_bullish_breakout:
            side = OrderSide.BUY
        else:
            if self.long_only:
                self._log_no_signal("Long-only mode: rejecting SELL")
                return None
            side = OrderSide.SELL

        # ── Phase 6: Calculate Signal Strength ───────────────────────────
        # Components: ADX momentum, HL count quality, rejection/breakout quality
        adx_norm = min(
            (current_adx - self.adx_min_threshold) / 50.0, 1.0
        )
        hl_norm = min(hl_count / 4.0, 1.0)  # 4 HLs = max quality

        if is_bullish_breakout:
            # Breakout strength: how far above trendline + body quality
            breakout_excess = (current_close - channel_upper) / current_atr
            breakout_norm = min(breakout_excess / 2.0, 1.0)
            strength = min(
                0.35 + adx_norm * 0.15 + hl_norm * 0.25 + breakout_norm * 0.20,
                1.0,
            )
        else:
            # Rejection strength: wick quality + ADX
            rejection_norm = min(rejection_ratio / 0.80, 1.0)
            strength = min(
                0.30 + adx_norm * 0.15 + hl_norm * 0.20 + rejection_norm * 0.25,
                1.0,
            )

        # Strength gate
        if self.min_strength > 0 and strength < self.min_strength:
            self._log_no_signal(
                f"Strength too low: {strength:.2f} < {self.min_strength}"
            )
            return None

        # ── Phase 7: Emit Pure Signal ────────────────────────────────────
        self._bars_since_signal = 0

        entry_type = "bullish_breakout" if is_bullish_breakout else "bearish_rejection"
        self.logger.info(
            f"DCB signal: {entry_type}",
            side=side.value,
            strength=f"{strength:.2f}",
            hl_count=hl_count,
            channel_upper=f"{channel_upper:.2f}",
            channel_lower=f"{channel_lower:.2f}",
            slope_high=f"{slope_high:.6f}",
        )

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                "atr": current_atr,
                "channel_upper": round(channel_upper, 2),
                "channel_lower": round(channel_lower, 2),
                "channel_slope_high": round(slope_high, 6),
                "channel_slope_low": round(slope_low, 6),
                "hl_count": hl_count,
                "entry_type": entry_type,
                "adx": current_adx,
                "rsi": current_rsi,
                "rejection_ratio": round(rejection_ratio, 3),
            },
        )
