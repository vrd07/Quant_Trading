"""
Unit tests for ContinuationBreakoutStrategy (Wyckoff stair-step pattern).

Tests cover:
- Pure helpers: impulse detection, consolidation measurement, breakout confirmation
- Full strategy pipeline on synthetic 4-phase bars
- Failed-continuation guard (cluster retraces below breakout level)
- Cooldown gating
- Long-only mode
"""

import numpy as np
import pandas as pd
import pytest

from src.core.types import Symbol
from src.core.constants import OrderSide
from src.strategies.continuation_breakout_strategy import (
    ContinuationBreakoutStrategy,
    _find_recent_impulse,
    _measure_consolidation,
    _confirm_continuation_breakout,
)
from src.data.indicators import Indicators


@pytest.fixture
def symbol():
    return Symbol(
        ticker="XAUUSD",
        pip_value=0.01,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01,
        value_per_lot=100,
    )


@pytest.fixture
def base_config():
    """Permissive config — filters off so pattern detection is what we test."""
    return {
        "enabled": True,
        "donchian_period": 20,
        "lookback_window": 60,
        "min_consolidation_bars": 5,
        "max_consolidation_bars": 25,
        "impulse_max_age_bars": 40,
        "impulse_body_atr": 1.0,
        "continuation_body_atr": 0.4,
        "consolidation_max_height_atr": 2.0,
        "adx_min_threshold": 0,        # accept any ADX
        "rsi_overbought": 100,         # disable RSI filter
        "rsi_oversold": 0,
        "use_ema_filter": False,       # disable trend filter for unit isolation
        "long_only": False,
        "cooldown_bars": 0,            # no cooldown
        "session_hours": None,
    }


def _make_continuation_bars(
    *,
    direction: str = "bullish",
    range1_bars: int = 80,
    impulse_size_atr: float = 2.0,
    cluster_bars: int = 8,
    cluster_drift_atr: float = 0.3,
    breakout_size_atr: float = 1.0,
    base_price: float = 2000.0,
    bar_atr: float = 1.0,
    cluster_offset_atr: float = 1.5,
    seed: int = 42,
):
    """Build a 4-phase synthetic XAUUSD-like dataset.

    Layout for bullish (bearish is mirrored):
      Phase 1: range1_bars sideways bars at base_price ± 0.5 × bar_atr
      Phase 2: 1 impulse bar that closes impulse_size_atr × bar_atr above
               the range high (the "fast move")
      Phase 3: cluster_bars sideways bars ~cluster_offset_atr × bar_atr
               above the impulse breakout level. Cluster span ≈
               cluster_drift_atr × bar_atr (tight by construction).
      Phase 4: 1 entry bar that closes breakout_size_atr × bar_atr above
               the cluster high.
    """
    rng = np.random.default_rng(seed)
    closes, opens, highs, lows = [], [], [], []

    sign = 1.0 if direction == "bullish" else -1.0

    # Phase 1: range
    for _ in range(range1_bars):
        c = base_price + rng.normal(0, 0.2) * bar_atr
        o = c - 0.05 * bar_atr
        h = c + 0.5 * bar_atr
        l = c - 0.5 * bar_atr
        closes.append(c); opens.append(o); highs.append(h); lows.append(l)

    range_high = max(highs[-range1_bars:])
    range_low = min(lows[-range1_bars:])
    breakout_level = range_high if direction == "bullish" else range_low

    # Phase 2: impulse bar (large body, breaks the range)
    impulse_close = breakout_level + sign * impulse_size_atr * bar_atr
    impulse_open = breakout_level - sign * 0.1 * bar_atr  # body straddles the break
    impulse_high = max(impulse_close, impulse_open) + 0.1 * bar_atr
    impulse_low = min(impulse_close, impulse_open) - 0.1 * bar_atr
    closes.append(impulse_close)
    opens.append(impulse_open)
    highs.append(impulse_high)
    lows.append(impulse_low)

    # Phase 3: re-accumulation cluster — tight range parked above/below impulse
    cluster_center = breakout_level + sign * cluster_offset_atr * bar_atr
    half_drift = cluster_drift_atr * bar_atr / 2.0
    for _ in range(cluster_bars):
        c = cluster_center + rng.normal(0, 0.05) * bar_atr
        o = c - 0.02 * bar_atr
        h = c + half_drift
        l = c - half_drift
        closes.append(c); opens.append(o); highs.append(h); lows.append(l)

    cluster_high = max(highs[-cluster_bars:])
    cluster_low = min(lows[-cluster_bars:])

    # Phase 4: entry bar — breaks the cluster in the trend direction
    entry_break_level = cluster_high if direction == "bullish" else cluster_low
    entry_close = entry_break_level + sign * breakout_size_atr * bar_atr
    entry_open = entry_break_level - sign * 0.1 * bar_atr
    entry_high = max(entry_close, entry_open) + 0.05 * bar_atr
    entry_low = min(entry_close, entry_open) - 0.05 * bar_atr
    closes.append(entry_close)
    opens.append(entry_open)
    highs.append(entry_high)
    lows.append(entry_low)

    n = len(closes)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="5min"),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * n,
    })


# ── Pure helper tests ────────────────────────────────────────────────────

class TestFindRecentImpulse:
    def test_returns_bullish_impulse_when_present(self):
        bars = _make_continuation_bars(direction="bullish", range1_bars=80, cluster_bars=8)
        upper, _, lower = Indicators.donchian_channel(bars, period=20)
        atr = Indicators.atr(bars, period=14)

        result = _find_recent_impulse(
            bars,
            upper=upper,
            lower=lower,
            atr=atr,
            scan_from=21,
            scan_to=len(bars) - 5,
            min_body_atr=1.0,
        )
        assert result is not None
        assert result["direction"] == "bullish"
        # Impulse should be at index range1_bars (0-indexed)
        assert result["bar_idx"] == 80

    def test_returns_bearish_impulse_when_present(self):
        bars = _make_continuation_bars(direction="bearish", range1_bars=80, cluster_bars=8)
        upper, _, lower = Indicators.donchian_channel(bars, period=20)
        atr = Indicators.atr(bars, period=14)

        result = _find_recent_impulse(
            bars,
            upper=upper,
            lower=lower,
            atr=atr,
            scan_from=21,
            scan_to=len(bars) - 5,
            min_body_atr=1.0,
        )
        assert result is not None
        assert result["direction"] == "bearish"

    def test_returns_none_when_body_too_small(self):
        bars = _make_continuation_bars(direction="bullish", impulse_size_atr=0.3)
        upper, _, lower = Indicators.donchian_channel(bars, period=20)
        atr = Indicators.atr(bars, period=14)

        result = _find_recent_impulse(
            bars,
            upper=upper,
            lower=lower,
            atr=atr,
            scan_from=21,
            scan_to=len(bars) - 5,
            min_body_atr=2.0,  # require 2x ATR — won't match
        )
        assert result is None


class TestMeasureConsolidation:
    def test_tight_cluster_passes(self):
        bars = pd.DataFrame({
            "open":  [100.0] * 8,
            "high":  [101.0] * 8,
            "low":   [99.5]  * 8,
            "close": [100.5] * 8,
        })
        result = _measure_consolidation(
            bars,
            atr_value=2.0,
            start_idx=0,
            end_idx=7,
            max_height_atr=2.0,
        )
        assert result is not None
        assert result["height"] == pytest.approx(1.5)
        assert result["height_atr"] == pytest.approx(0.75)

    def test_wide_cluster_rejected(self):
        bars = pd.DataFrame({
            "open":  [100.0, 105.0, 100.0, 110.0],
            "high":  [102.0, 107.0, 101.0, 112.0],
            "low":   [99.0,  104.0, 98.0,  108.0],
            "close": [101.0, 106.0, 100.0, 111.0],
        })
        result = _measure_consolidation(
            bars,
            atr_value=2.0,
            start_idx=0,
            end_idx=3,
            max_height_atr=2.0,  # 14-point span >> 4 = 2 × ATR
        )
        assert result is None


class TestConfirmContinuationBreakout:
    def test_bullish_break_passes(self):
        assert _confirm_continuation_breakout(
            current_open=100.0,
            current_close=102.5,
            current_atr=2.0,
            range_high=101.0,
            range_low=99.0,
            direction="bullish",
            min_body_atr=0.5,
        )

    def test_bullish_close_below_range_high_fails(self):
        assert not _confirm_continuation_breakout(
            current_open=100.0,
            current_close=100.8,  # didn't clear range_high=101
            current_atr=2.0,
            range_high=101.0,
            range_low=99.0,
            direction="bullish",
            min_body_atr=0.1,
        )

    def test_small_body_fails(self):
        assert not _confirm_continuation_breakout(
            current_open=101.5,
            current_close=101.6,  # body 0.1 < 0.5 × ATR
            current_atr=2.0,
            range_high=101.0,
            range_low=99.0,
            direction="bullish",
            min_body_atr=0.5,
        )


# ── Full pipeline tests ──────────────────────────────────────────────────

class TestStrategyPipeline:
    def test_bullish_pattern_emits_buy_signal(self, symbol, base_config):
        bars = _make_continuation_bars(direction="bullish")
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        signal = strat.on_bar(bars)
        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert signal.metadata["direction"] == "bullish"
        assert signal.metadata["cluster_bars"] >= 5

    def test_bearish_pattern_emits_sell_signal(self, symbol, base_config):
        bars = _make_continuation_bars(direction="bearish")
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        signal = strat.on_bar(bars)
        assert signal is not None
        assert signal.side == OrderSide.SELL

    def test_long_only_blocks_short(self, symbol, base_config):
        cfg = {**base_config, "long_only": True}
        bars = _make_continuation_bars(direction="bearish")
        strat = ContinuationBreakoutStrategy(symbol, cfg)
        assert strat.on_bar(bars) is None

    def test_no_signal_without_impulse(self, symbol, base_config):
        # Just sideways bars — no impulse, no continuation
        rng = np.random.default_rng(1)
        n = 100
        closes = [2000.0 + rng.normal(0, 0.5) for _ in range(n)]
        bars = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="5min"),
            "open":  [c - 0.05 for c in closes],
            "high":  [c + 0.4 for c in closes],
            "low":   [c - 0.4 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        })
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        assert strat.on_bar(bars) is None

    def test_cluster_below_breakout_level_rejected(self, symbol, base_config):
        # Bullish setup but cluster sits BELOW the impulse breakout level
        # (failed continuation — should not fire)
        bars = _make_continuation_bars(
            direction="bullish",
            cluster_offset_atr=-2.0,  # cluster below breakout — invalid
        )
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        assert strat.on_bar(bars) is None

    def test_cooldown_blocks_consecutive_signal(self, symbol, base_config):
        cfg = {**base_config, "cooldown_bars": 10}
        bars = _make_continuation_bars(direction="bullish")
        strat = ContinuationBreakoutStrategy(symbol, cfg)
        # First call fires (allowed since _bars_since_signal is initialized to cooldown)
        first = strat.on_bar(bars)
        assert first is not None
        # Same bar called again — cooldown should now block
        second = strat.on_bar(bars)
        assert second is None

    def test_disabled_returns_none(self, symbol, base_config):
        cfg = {**base_config, "enabled": False}
        bars = _make_continuation_bars(direction="bullish")
        strat = ContinuationBreakoutStrategy(symbol, cfg)
        assert strat.on_bar(bars) is None

    def test_strategy_name(self, symbol, base_config):
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        assert strat.get_name() == "continuation_breakout"

    def test_signal_metadata_contains_pattern_levels(self, symbol, base_config):
        bars = _make_continuation_bars(direction="bullish")
        strat = ContinuationBreakoutStrategy(symbol, base_config)
        sig = strat.on_bar(bars)
        assert sig is not None
        for key in (
            "atr", "adx", "rsi", "impulse_breakout_level",
            "cluster_high", "cluster_low", "cluster_height_atr",
            "cluster_bars", "direction",
        ):
            assert key in sig.metadata, f"missing {key}"
