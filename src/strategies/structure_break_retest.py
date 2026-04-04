"""
Structure Break + Retest (SBR) Strategy — trades confirmed retests of broken structure.

Unlike donchian_breakout which trades the initial break, SBR waits for price to
return to the broken level and confirm rejection before entering. This provides:
- Tighter stop loss (invalidation is the broken level itself)
- Higher probability (market has already shown direction)
- Better risk:reward (tight SL + generous TP)

Pattern lifecycle:
1. Price breaks a Donchian channel boundary (structure break)
2. Price returns to the broken level within a configurable window
3. The retest bar shows rejection (wick > body towards the broken level)
4. Confirmation filters pass (ADX trending, RSI not extreme)
5. Pure signal emitted → RiskProcessor attaches SL/TP

Filter stack (5 core filters):
1. Structure break detected (Donchian channel breach, previous bar's boundary)
2. Retest within tolerance (price returns to broken level ± ATR fraction)
3. Rejection candle quality (wick/body ratio confirms rejection)
4. ADX > threshold (trend still active after break)
5. RSI not extreme (avoid chasing exhausted moves)

Design: all detection logic is in pure functions that read inputs and return
values. The only mutable state is _pending_break and _bars_since_signal,
both visible at the on_bar() call site. (Carmack rule)
"""

from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


def _find_structure_break(
    bars: pd.DataFrame,
    donchian_period: int,
) -> Optional[Dict[str, Any]]:
    """Detect if a structure break occurred on the current bar.

    A structure break is a close beyond the previous bar's Donchian channel
    boundary. Uses iloc[-2] channel to avoid lookahead bias.

    Args:
        bars: OHLCV DataFrame, minimum donchian_period + 2 rows.
        donchian_period: Lookback for Donchian channel calculation.

    Returns:
        Dict with 'direction' ('bullish'|'bearish'), 'broken_level' (float),
        and 'break_bar_index' (int) if a break occurred. None otherwise.
    """
    upper, _, lower = Indicators.donchian_channel(bars, period=donchian_period)

    current_close = float(bars["close"].iloc[-1])
    prev_upper = float(upper.iloc[-2])
    prev_lower = float(lower.iloc[-2])

    if pd.isna(prev_upper) or pd.isna(prev_lower):
        return None

    if current_close > prev_upper:
        return {
            "direction": "bullish",
            "broken_level": prev_upper,
            "break_bar_index": len(bars) - 1,
        }

    if current_close < prev_lower:
        return {
            "direction": "bearish",
            "broken_level": prev_lower,
            "break_bar_index": len(bars) - 1,
        }

    return None


def _check_retest(
    current_close: float,
    current_low: float,
    current_high: float,
    broken_level: float,
    direction: str,
    tolerance: float,
) -> bool:
    """Check if the current bar retests the broken level within tolerance.

    For a bullish break, price must pull back DOWN to the broken level
    (now support). For bearish, price must pull back UP to the broken level
    (now resistance).

    Args:
        current_close: Current bar close price.
        current_low: Current bar low price.
        current_high: Current bar high price.
        broken_level: The level that was broken.
        direction: 'bullish' or 'bearish'.
        tolerance: ATR-scaled distance threshold.

    Returns:
        True if the bar touches or comes within tolerance of the broken level.
    """
    if direction == "bullish":
        # Pullback into broken resistance (now support)
        # Low must reach within tolerance of the broken level
        distance = current_low - broken_level
        return -tolerance <= distance <= tolerance
    else:
        # Pullback into broken support (now resistance)
        # High must reach within tolerance of the broken level
        distance = broken_level - current_high
        return -tolerance <= distance <= tolerance


def _calculate_rejection_strength(
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
    direction: str,
) -> float:
    """Calculate how strongly the retest bar rejected the broken level.

    For a bullish retest (support), we want a long lower wick and close
    above open (buyers stepped in). For bearish (resistance), long upper
    wick and close below open.

    Args:
        bar_open: Bar open price.
        bar_high: Bar high price.
        bar_low: Bar low price.
        bar_close: Bar close price.
        direction: 'bullish' or 'bearish'.

    Returns:
        Rejection ratio 0.0–1.0. Higher = stronger rejection.
        0.0 if the bar has zero range (doji with no wick).
    """
    bar_range = bar_high - bar_low
    if bar_range <= 0:
        return 0.0

    body = abs(bar_close - bar_open)

    if direction == "bullish":
        # Lower wick = distance from low to min(open, close)
        rejection_wick = min(bar_open, bar_close) - bar_low
    else:
        # Upper wick = distance from max(open, close) to high
        rejection_wick = bar_high - max(bar_open, bar_close)

    # Ratio of rejection wick to total range
    return rejection_wick / bar_range


class StructureBreakRetestStrategy(BaseStrategy):
    """Trades confirmed retests of broken Donchian channel structure."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.lookback_period = config.get("lookback_period", 20)
        self.retest_tolerance_atr = config.get("retest_tolerance_atr", 0.5)
        self.min_rejection_ratio = config.get("min_rejection_ratio", 0.6)
        self.retest_window_bars = config.get("retest_window_bars", 30)
        self.only_in_regime = MarketRegime[config.get("only_in_regime", "TREND")]

        self.adx_min_threshold = config.get("adx_min_threshold", 20)
        self.rsi_overbought = config.get("rsi_overbought", 75)
        self.rsi_oversold = config.get("rsi_oversold", 25)
        self.long_only = config.get("long_only", False)

        self.cooldown_bars = config.get("cooldown_bars", 10)
        self._bars_since_signal = self.cooldown_bars  # Allow first trade immediately

        # Pending break state — visible and mutated only in on_bar()
        # None when no break is being tracked
        self._pending_break: Optional[Dict[str, Any]] = None
        self._bars_since_break = 0

    def get_name(self) -> str:
        return "structure_break_retest"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        # O(1) slice — only process last 400 bars regardless of history depth
        bars = bars.tail(400)

        min_bars = self.lookback_period + 20 + 5
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

        # ── Regime ───────────────────────────────────────────────────────
        regime = (
            self.ml_regime if self.ml_regime is not None else MarketRegime.TREND
        )

        # ── Indicators ───────────────────────────────────────────────────
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)

        current_close = float(bars["close"].iloc[-1])
        current_open = float(bars["open"].iloc[-1])
        current_high = float(bars["high"].iloc[-1])
        current_low = float(bars["low"].iloc[-1])
        current_atr = float(atr.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_adx = float(adx.iloc[-1])

        if any(
            np.isnan(v) for v in [current_atr, current_rsi, current_adx]
        ):
            self._log_no_signal("Indicator calculation failed")
            return None

        # ── Phase 1: Detect new structure breaks ─────────────────────────
        new_break = _find_structure_break(bars, self.lookback_period)
        if new_break is not None:
            # A new break replaces any pending break (most recent wins)
            self._pending_break = new_break
            self._bars_since_break = 0
            self.logger.info(
                f"Structure break detected",
                direction=new_break["direction"],
                broken_level=f"{new_break['broken_level']:.2f}",
            )

        # ── Phase 2: Check for retest of pending break ───────────────────
        if self._pending_break is None:
            self._log_no_signal("No pending structure break")
            return None

        self._bars_since_break += 1

        # Expire stale breaks — geohot rule: don't hold state forever
        if self._bars_since_break > self.retest_window_bars:
            self.logger.info(
                f"Pending break expired after {self._bars_since_break} bars"
            )
            self._pending_break = None
            return None

        direction = self._pending_break["direction"]
        broken_level = self._pending_break["broken_level"]
        tolerance = self.retest_tolerance_atr * current_atr

        # Skip same bar as break (need at least 1 bar separation)
        if self._bars_since_break < 2:
            self._log_no_signal("Waiting for retest (break too recent)")
            return None

        # Check if price retests the broken level
        is_retest = _check_retest(
            current_close, current_low, current_high,
            broken_level, direction, tolerance,
        )

        if not is_retest:
            self._log_no_signal(
                f"No retest: price not near broken level "
                f"{broken_level:.2f} ± {tolerance:.2f}"
            )
            return None

        # ── Phase 3: Confirm rejection quality ───────────────────────────
        rejection = _calculate_rejection_strength(
            current_open, current_high, current_low, current_close, direction,
        )

        if rejection < self.min_rejection_ratio:
            self._log_no_signal(
                f"Weak rejection: {rejection:.2f} < {self.min_rejection_ratio}"
            )
            return None

        # ── Phase 4: Confirmation filters ────────────────────────────────
        # Filter: ADX must confirm trend is still active
        if current_adx < self.adx_min_threshold:
            self._log_no_signal(
                f"ADX too low: {current_adx:.1f} < {self.adx_min_threshold}"
            )
            return None

        # Filter: RSI not extreme (avoid chasing)
        if direction == "bullish" and current_rsi > self.rsi_overbought:
            self._log_no_signal(f"RSI overbought: {current_rsi:.1f}")
            return None

        if direction == "bearish" and current_rsi < self.rsi_oversold:
            self._log_no_signal(f"RSI oversold: {current_rsi:.1f}")
            return None

        # ── Phase 5: Direction gate ──────────────────────────────────────
        if direction == "bullish":
            side = OrderSide.BUY
        else:
            if self.long_only:
                self._log_no_signal("Long-only mode: rejecting SELL")
                return None
            side = OrderSide.SELL

        # ── Phase 6: Emit pure signal ────────────────────────────────────
        # Strength: ADX-normalized + rejection quality bonus
        adx_norm = min(
            (current_adx - self.adx_min_threshold) / 50.0, 1.0
        )
        strength = min(0.50 + adx_norm * 0.25 + rejection * 0.20, 1.0)

        # Clear pending break — it has been consumed
        self._pending_break = None
        self._bars_since_signal = 0

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                "atr": current_atr,
                "broken_level": broken_level,
                "rejection_ratio": round(rejection, 3),
                "retest_bar_count": self._bars_since_break,
                "adx": current_adx,
                "rsi": current_rsi,
            },
        )
