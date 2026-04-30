"""
Continuation Breakout Strategy — Wyckoff-style two-leg breakout.

Pattern lifecycle (the "stair-step" continuation):
1. Accumulation Phase:   tight horizontal range (low height/ATR ratio).
2. Fast Price Move:      impulse bar breaks the range with a strong body.
3. Building Pre-Tension: second tight range forms after the impulse, in the
                         trend direction (above for longs, below for shorts).
4. Trend Continuation:   second breakout fires the entry — same direction as
                         the impulse, confirmed by body and conviction.

This is NOT a retest strategy (see structure_break_retest for that). It is
the second leg of a two-leg breakout where the first leg confirmed direction
and the second leg confirms the trend has resumed after consolidation.

Design (Carmack lens):
- All detection logic lives in pure helper functions that take inputs and
  return values. No hidden state.
- The only mutable state on the class is _bars_since_signal (cooldown).
- The full pattern is reconstructed on every bar from the recent lookback,
  so a missed bar (gap, restart) self-heals.
"""

from typing import Optional, Dict, Any
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


def _find_recent_impulse(
    bars: pd.DataFrame,
    *,
    upper: pd.Series,
    lower: pd.Series,
    atr: pd.Series,
    scan_from: int,
    scan_to: int,
    min_body_atr: float,
) -> Optional[Dict[str, Any]]:
    """Walk backwards in [scan_from, scan_to) for the most recent impulse bar.

    An impulse is a bar that:
      - Breaks the previous bar's Donchian channel (upper for bullish,
        lower for bearish), AND
      - Has a body (|close - open|) >= min_body_atr × ATR at that bar, AND
      - Closes in the breakout direction (close > open for bullish).

    Returns:
        Dict with 'bar_idx', 'direction' ('bullish'|'bearish'),
        'breakout_level' (float), 'atr' (float). None if no impulse found.
    """
    closes = bars["close"].to_numpy()
    opens = bars["open"].to_numpy()
    upper_arr = upper.to_numpy()
    lower_arr = lower.to_numpy()
    atr_arr = atr.to_numpy()

    if scan_to <= scan_from:
        return None

    for i in range(scan_to - 1, scan_from - 1, -1):
        if i - 1 < 0:
            continue
        atr_i = atr_arr[i]
        if not np.isfinite(atr_i) or atr_i <= 0:
            continue
        body = abs(closes[i] - opens[i])
        if body < min_body_atr * atr_i:
            continue
        prev_upper = upper_arr[i - 1]
        prev_lower = lower_arr[i - 1]
        if (
            np.isfinite(prev_upper)
            and closes[i] > prev_upper
            and closes[i] > opens[i]
        ):
            return {
                "bar_idx": i,
                "direction": "bullish",
                "breakout_level": float(prev_upper),
                "atr": float(atr_i),
            }
        if (
            np.isfinite(prev_lower)
            and closes[i] < prev_lower
            and closes[i] < opens[i]
        ):
            return {
                "bar_idx": i,
                "direction": "bearish",
                "breakout_level": float(prev_lower),
                "atr": float(atr_i),
            }
    return None


def _measure_consolidation(
    bars: pd.DataFrame,
    *,
    atr_value: float,
    start_idx: int,
    end_idx: int,
    max_height_atr: float,
) -> Optional[Dict[str, float]]:
    """Measure bars[start_idx:end_idx+1] as a consolidation cluster.

    Returns the high/low/height of the cluster if its total span is
    <= max_height_atr × atr_value. Otherwise None (range too wide to
    qualify as a re-accumulation pocket).
    """
    if end_idx < start_idx or atr_value <= 0:
        return None
    window = bars.iloc[start_idx : end_idx + 1]
    if len(window) == 0:
        return None
    high = float(window["high"].max())
    low = float(window["low"].min())
    height = high - low
    if height > max_height_atr * atr_value:
        return None
    return {
        "high": high,
        "low": low,
        "height": height,
        "height_atr": height / atr_value,
    }


def _confirm_continuation_breakout(
    *,
    current_open: float,
    current_close: float,
    current_atr: float,
    range_high: float,
    range_low: float,
    direction: str,
    min_body_atr: float,
) -> bool:
    """Current bar must break the consolidation in the trend direction
    with body >= min_body_atr × ATR and close on the breakout side of open.
    """
    body = abs(current_close - current_open)
    if body < min_body_atr * current_atr:
        return False
    if direction == "bullish":
        return current_close > range_high and current_close > current_open
    return current_close < range_low and current_close < current_open


class ContinuationBreakoutStrategy(BaseStrategy):
    """Two-leg breakout: range -> impulse -> re-accumulation -> continuation."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.donchian_period = int(config.get("donchian_period", 20))

        self.lookback_window = int(config.get("lookback_window", 60))
        self.min_consolidation_bars = int(config.get("min_consolidation_bars", 5))
        self.max_consolidation_bars = int(config.get("max_consolidation_bars", 25))
        self.impulse_max_age_bars = int(config.get("impulse_max_age_bars", 40))

        self.impulse_body_atr = float(config.get("impulse_body_atr", 1.2))
        self.continuation_body_atr = float(config.get("continuation_body_atr", 0.5))
        self.consolidation_max_height_atr = float(
            config.get("consolidation_max_height_atr", 2.0)
        )

        self.adx_min_threshold = float(config.get("adx_min_threshold", 18))
        self.rsi_overbought = float(config.get("rsi_overbought", 78))
        self.rsi_oversold = float(config.get("rsi_oversold", 22))
        self.ema_trend_period = int(config.get("ema_trend_period", 50))
        self.use_ema_filter = bool(config.get("use_ema_filter", True))

        session_hours = config.get("session_hours", None)
        self.session_hours = (
            set(session_hours) if session_hours is not None else None
        )

        self.long_only = bool(config.get("long_only", False))

        self.cooldown_bars = int(config.get("cooldown_bars", 6))
        self._bars_since_signal = self.cooldown_bars  # Allow first signal immediately

    def get_name(self) -> str:
        return "continuation_breakout"

    def _is_in_session(self, bars: pd.DataFrame) -> bool:
        if self.session_hours is None:
            return True
        hour = self._get_bar_hour(bars)
        if hour is None:
            return True  # Cannot determine — don't block
        return hour in self.session_hours

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        # Bound work to the lookback we actually need
        keep = max(self.lookback_window + self.donchian_period + 30, 200)
        bars = bars.tail(keep)

        min_bars = self.donchian_period + self.lookback_window + 5
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            self._log_no_signal(
                f"Cooldown: {self._bars_since_signal}/{self.cooldown_bars} bars"
            )
            return None

        if not self._is_in_session(bars):
            self._log_no_signal("Outside allowed session")
            return None

        upper, _, lower = Indicators.donchian_channel(
            bars, period=self.donchian_period
        )
        atr = Indicators.atr(bars, period=14)
        rsi = Indicators.rsi(bars, period=14)
        adx = Indicators.adx(bars, period=14)
        ema_trend = Indicators.ema(bars, period=self.ema_trend_period)

        current_open = float(bars["open"].iloc[-1])
        current_close = float(bars["close"].iloc[-1])
        current_atr = float(atr.iloc[-1])
        current_rsi = float(rsi.iloc[-1])
        current_adx = float(adx.iloc[-1])
        current_ema = float(ema_trend.iloc[-1])

        if any(
            np.isnan(v) for v in [current_atr, current_rsi, current_adx, current_ema]
        ):
            self._log_no_signal("Indicator calculation failed")
            return None

        if current_atr <= 0:
            self._log_no_signal("Non-positive ATR")
            return None

        if current_adx < self.adx_min_threshold:
            self._log_no_signal(
                f"ADX too low: {current_adx:.1f} < {self.adx_min_threshold}"
            )
            return None

        # ── Pattern detection: 3-stage scan ──────────────────────────────
        n = len(bars)
        # Impulse must leave at least min_consolidation_bars between itself
        # and the current (entry) bar. Current bar is iloc -1 == n - 1.
        scan_to = n - 1 - self.min_consolidation_bars
        scan_from = max(self.donchian_period + 1, n - 1 - self.impulse_max_age_bars)

        if scan_to <= scan_from:
            self._log_no_signal("Insufficient lookback for impulse scan")
            return None

        impulse = _find_recent_impulse(
            bars,
            upper=upper,
            lower=lower,
            atr=atr,
            scan_from=scan_from,
            scan_to=scan_to,
            min_body_atr=self.impulse_body_atr,
        )

        if impulse is None:
            self._log_no_signal("No qualifying impulse bar in lookback")
            return None

        cluster_start = impulse["bar_idx"] + 1
        cluster_end = n - 2  # exclude current (entry) bar
        cluster_len = cluster_end - cluster_start + 1
        if cluster_len < self.min_consolidation_bars:
            self._log_no_signal(
                f"Re-accumulation too short: {cluster_len} < "
                f"{self.min_consolidation_bars}"
            )
            return None
        if cluster_len > self.max_consolidation_bars:
            self._log_no_signal(
                f"Re-accumulation too old: {cluster_len} > "
                f"{self.max_consolidation_bars}"
            )
            return None

        cluster = _measure_consolidation(
            bars,
            atr_value=current_atr,
            start_idx=cluster_start,
            end_idx=cluster_end,
            max_height_atr=self.consolidation_max_height_atr,
        )

        if cluster is None:
            self._log_no_signal("Re-accumulation range too wide (no pre-tension)")
            return None

        direction = impulse["direction"]
        # Failed-continuation guard: cluster must sit on the trend side of
        # the impulse breakout level. If price retraced past it, this is
        # a failed move, not a stair-step.
        if direction == "bullish" and cluster["low"] < impulse["breakout_level"]:
            self._log_no_signal(
                "Cluster pulled back below impulse breakout — failed continuation"
            )
            return None
        if direction == "bearish" and cluster["high"] > impulse["breakout_level"]:
            self._log_no_signal(
                "Cluster rallied above impulse breakdown — failed continuation"
            )
            return None

        if not _confirm_continuation_breakout(
            current_open=current_open,
            current_close=current_close,
            current_atr=current_atr,
            range_high=cluster["high"],
            range_low=cluster["low"],
            direction=direction,
            min_body_atr=self.continuation_body_atr,
        ):
            self._log_no_signal("Current bar did not break re-accumulation range")
            return None

        if direction == "bullish":
            if current_rsi > self.rsi_overbought:
                self._log_no_signal(f"RSI overbought: {current_rsi:.1f}")
                return None
            if self.use_ema_filter and current_close < current_ema:
                self._log_no_signal(
                    f"EMA trend bearish: close {current_close:.2f} "
                    f"< EMA {current_ema:.2f}"
                )
                return None
            side = OrderSide.BUY
        else:
            if self.long_only:
                self._log_no_signal("Long-only mode: rejecting SELL")
                return None
            if current_rsi < self.rsi_oversold:
                self._log_no_signal(f"RSI oversold: {current_rsi:.1f}")
                return None
            if self.use_ema_filter and current_close > current_ema:
                self._log_no_signal(
                    f"EMA trend bullish: close {current_close:.2f} "
                    f"> EMA {current_ema:.2f}"
                )
                return None
            side = OrderSide.SELL

        regime = (
            self.ml_regime if self.ml_regime is not None else MarketRegime.TREND
        )

        adx_norm = min((current_adx - self.adx_min_threshold) / 50.0, 1.0)
        tightness_norm = max(
            0.0,
            1.0 - (cluster["height_atr"] / self.consolidation_max_height_atr),
        )
        strength = min(0.45 + adx_norm * 0.30 + tightness_norm * 0.20, 1.0)

        self._bars_since_signal = 0

        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            metadata={
                "atr": current_atr,
                "adx": current_adx,
                "rsi": current_rsi,
                "impulse_bars_back": (n - 1) - impulse["bar_idx"],
                "impulse_breakout_level": impulse["breakout_level"],
                "cluster_high": cluster["high"],
                "cluster_low": cluster["low"],
                "cluster_height_atr": round(cluster["height_atr"], 3),
                "cluster_bars": cluster_len,
                "direction": direction,
            },
        )
