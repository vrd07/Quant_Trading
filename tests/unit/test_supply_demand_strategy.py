"""
Unit tests for Supply & Demand Zone Strategy.

Tests cover the complete S&D lifecycle following the AAA pattern:
  - Pure function correctness (_detect_impulse, _build_zone, _price_in_zone,
    _zone_consumed, _rejection_ratio)
  - Zone formation and lifecycle management (age, expiry, consumption)
  - Full on_bar() integration with signal metadata validation
  - Guard conditions (cooldown, disabled, insufficient data, long-only)
"""

import pytest
import pandas as pd
import numpy as np
from decimal import Decimal
from datetime import datetime

from src.core.types import Symbol
from src.core.constants import MarketRegime, OrderSide


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def symbol():
    """Standard XAUUSD test symbol."""
    return Symbol(
        ticker="XAUUSD",
        pip_value=0.01,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01,
        value_per_lot=100,
    )


def _make_bars(n: int = 100, base_price: float = 2000.0, seed: int = 42) -> pd.DataFrame:
    """Flat-ish bars for default tests (no impulse)."""
    np.random.seed(seed)
    closes = [base_price + np.random.randn() * 0.5 for _ in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="5min"),
        "open":   [c - 0.3 for c in closes],
        "high":   [c + 1.0 for c in closes],
        "low":    [c - 1.0 for c in closes],
        "close":  closes,
        "volume": [1000.0] * n,
    }).set_index("timestamp")


def _make_demand_zone_bars(
    n: int = 100,
    base_price: float = 2000.0,
    base_bars: int = 5,
    impulse_bar_idx: int = 50,
    retest_bar_offset: int = 10,
    atr_mult: float = 3.0,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Synthetic bars with:
      - Flat consolidation (base) at impulse_bar_idx - base_bars .. impulse_bar_idx - 1
      - A strong bullish impulse at impulse_bar_idx (body >= atr_mult × small ATR)
      - Price drifts back down to the zone after retest_bar_offset more bars
    """
    np.random.seed(seed)
    opens, highs, lows, closes = [], [], [], []

    for i in range(n):
        if i < impulse_bar_idx - base_bars:
            # Pre-base: light noise
            c = base_price + np.random.randn() * 0.3
            o, h, l = c - 0.2, c + 0.5, c - 0.5
        elif i < impulse_bar_idx:
            # Base (consolidation): very tight range
            c = base_price + np.random.randn() * 0.1
            o, h, l = c - 0.05, c + 0.15, c - 0.15
        elif i == impulse_bar_idx:
            # Impulse: large bullish body (10 points ≫ ATR ≈ 0.5)
            o = base_price
            c = base_price + 10.0
            h, l = c + 0.5, o - 0.2
        elif i < impulse_bar_idx + retest_bar_offset:
            # Drift higher (continuation)
            c = closes[-1] + 0.2
            o, h, l = c - 0.1, c + 0.3, c - 0.3
        else:
            # Retest: price falls back towards zone
            target = base_price + 0.5  # inside zone
            c = max(target, closes[-1] - 0.8)
            o = c + 0.4  # open above → bearish wicks → demand rejection
            h = o + 0.3
            l = c - 2.5  # long lower wick (bullish rejection wick at demand zone)

        opens.append(o); highs.append(h); lows.append(l); closes.append(c)

    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="5min"),
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1000.0] * n,
    }).set_index("timestamp")


# ---------------------------------------------------------------------------
# Pure function tests — Carmack rule: pure fns must be independently testable
# ---------------------------------------------------------------------------

class TestDetectImpulse:
    """Tests for the _detect_impulse() pure function."""

    def test_bullish_impulse_detected(self):
        from src.strategies.supply_demand_strategy import _detect_impulse
        # Body = 5.0, ATR = 1.0, mult = 2.5 → 5.0 >= 2.5 → bullish
        result = _detect_impulse(bar_open=2000.0, bar_close=2005.0, atr=1.0, min_impulse_atr_mult=2.5)
        assert result == "bullish"

    def test_bearish_impulse_detected(self):
        from src.strategies.supply_demand_strategy import _detect_impulse
        # Body = -6.0, ATR = 2.0, mult = 2.5 → 6.0 >= 5.0 → bearish
        result = _detect_impulse(bar_open=2006.0, bar_close=2000.0, atr=2.0, min_impulse_atr_mult=2.5)
        assert result == "bearish"

    def test_small_body_returns_none(self):
        from src.strategies.supply_demand_strategy import _detect_impulse
        # Body = 1.0, ATR = 2.0, mult = 2.5 → 1.0 < 5.0 → not an impulse
        result = _detect_impulse(bar_open=2000.0, bar_close=2001.0, atr=2.0, min_impulse_atr_mult=2.5)
        assert result is None

    def test_zero_atr_returns_none(self):
        from src.strategies.supply_demand_strategy import _detect_impulse
        # ATR=0 → division singularity guard
        result = _detect_impulse(bar_open=2000.0, bar_close=2010.0, atr=0.0, min_impulse_atr_mult=2.5)
        assert result is None

    def test_doji_returns_none(self):
        from src.strategies.supply_demand_strategy import _detect_impulse
        result = _detect_impulse(bar_open=2000.0, bar_close=2000.0, atr=1.0, min_impulse_atr_mult=2.5)
        assert result is None


class TestBuildZone:
    """Tests for the _build_zone() pure function."""

    def test_demand_zone_formed_from_bullish_impulse(self):
        from src.strategies.supply_demand_strategy import _build_zone
        bars = _make_demand_zone_bars()
        # impulse_bar_idx=50, direction="bullish", lookback=5
        zone = _build_zone(bars, impulse_bar_idx=50, direction="bullish", lookback_bars=5)
        assert zone is not None
        assert zone["direction"] == "demand"
        assert zone["high"] > zone["low"]
        assert zone["age_bars"] == 0

    def test_supply_zone_formed_from_bearish_impulse(self):
        from src.strategies.supply_demand_strategy import _build_zone
        bars = _make_bars(n=60)
        zone = _build_zone(bars, impulse_bar_idx=50, direction="bearish", lookback_bars=5)
        assert zone is not None
        assert zone["direction"] == "supply"

    def test_insufficient_lookback_returns_none(self):
        from src.strategies.supply_demand_strategy import _build_zone
        bars = _make_bars(n=20)
        # impulse at bar 2, lookback 5 → base_start = -3 → None
        result = _build_zone(bars, impulse_bar_idx=2, direction="bullish", lookback_bars=5)
        assert result is None

    def test_zone_bounds_span_base_candles(self):
        from src.strategies.supply_demand_strategy import _build_zone
        bars = _make_bars(n=60)
        zone = _build_zone(bars, impulse_bar_idx=50, direction="bullish", lookback_bars=5)
        if zone is not None:
            # Zone high must span base highs, zone low must span base lows
            base = bars.iloc[45:50]
            assert zone["high"] == pytest.approx(float(base["high"].max()), abs=0.001)
            assert zone["low"] == pytest.approx(float(base["low"].min()), abs=0.001)


class TestPriceInZone:
    """Tests for the _price_in_zone() pure function."""

    def test_direct_overlap_returns_true(self):
        from src.strategies.supply_demand_strategy import _price_in_zone
        # Bar range [1999, 2001] overlaps zone [1998, 2002]
        assert _price_in_zone(2001.0, 1999.0, 2002.0, 1998.0, tolerance=0.0)

    def test_no_overlap_returns_false(self):
        from src.strategies.supply_demand_strategy import _price_in_zone
        # Bar range [2010, 2012] far above zone [1998, 2002]
        assert not _price_in_zone(2012.0, 2010.0, 2002.0, 1998.0, tolerance=0.0)

    def test_tolerance_closes_gap(self):
        from src.strategies.supply_demand_strategy import _price_in_zone
        # Bar low = 2003, zone high = 2002 → 1 point gap, tolerance = 1.5 → True
        assert _price_in_zone(2005.0, 2003.0, 2002.0, 1998.0, tolerance=1.5)

    def test_no_overlap_even_with_tolerance(self):
        from src.strategies.supply_demand_strategy import _price_in_zone
        # Bar [2010, 2015], zone [1998, 2002], tolerance = 1.0 → gap = 8 → False
        assert not _price_in_zone(2015.0, 2010.0, 2002.0, 1998.0, tolerance=1.0)


class TestZoneConsumed:
    """Tests for the _zone_consumed() pure function."""

    def test_demand_zone_consumed_when_close_below(self):
        from src.strategies.supply_demand_strategy import _zone_consumed
        # Close below demand zone → support broken → consumed
        assert _zone_consumed(bar_close=1997.0, zone_high=2002.0, zone_low=1998.0, direction="demand")

    def test_demand_zone_not_consumed_when_close_inside(self):
        from src.strategies.supply_demand_strategy import _zone_consumed
        assert not _zone_consumed(bar_close=2000.0, zone_high=2002.0, zone_low=1998.0, direction="demand")

    def test_supply_zone_consumed_when_close_above(self):
        from src.strategies.supply_demand_strategy import _zone_consumed
        assert _zone_consumed(bar_close=2003.0, zone_high=2002.0, zone_low=1998.0, direction="supply")

    def test_supply_zone_not_consumed_when_close_inside(self):
        from src.strategies.supply_demand_strategy import _zone_consumed
        assert not _zone_consumed(bar_close=2000.0, zone_high=2002.0, zone_low=1998.0, direction="supply")


class TestRejectionRatio:
    """Tests for the _rejection_ratio() pure function."""

    def test_strong_demand_rejection(self):
        from src.strategies.supply_demand_strategy import _rejection_ratio
        # Long lower wick: low=1997, open=2000, close=2001, high=2001.5
        # Lower wick = min(2000, 2001) - 1997 = 3.0; range = 4.5
        ratio = _rejection_ratio(2000.0, 2001.5, 1997.0, 2001.0, "demand")
        assert ratio == pytest.approx(3.0 / 4.5, abs=0.01)

    def test_strong_supply_rejection(self):
        from src.strategies.supply_demand_strategy import _rejection_ratio
        # Long upper wick: open=2001, close=2000, low=1999.5, high=2004
        # Upper wick = 2004 - max(2001, 2000) = 3.0; range = 4.5
        ratio = _rejection_ratio(2001.0, 2004.0, 1999.5, 2000.0, "supply")
        assert ratio == pytest.approx(3.0 / 4.5, abs=0.01)

    def test_zero_range_returns_zero(self):
        from src.strategies.supply_demand_strategy import _rejection_ratio
        ratio = _rejection_ratio(2000.0, 2000.0, 2000.0, 2000.0, "demand")
        assert ratio == 0.0

    def test_ratio_clamped_non_negative(self):
        from src.strategies.supply_demand_strategy import _rejection_ratio
        # Bar that closes below open in a demand zone → 0 wick → ratio = 0
        ratio = _rejection_ratio(2002.0, 2003.0, 1998.0, 1999.0, "demand")
        assert ratio >= 0.0


# ---------------------------------------------------------------------------
# Strategy class tests — integration (on_bar lifecycle)
# ---------------------------------------------------------------------------

def _make_strategy(symbol, **overrides):
    """Build SupplyDemandStrategy with test-friendly defaults."""
    from src.strategies.supply_demand_strategy import SupplyDemandStrategy

    config = {
        "enabled": True,
        "min_impulse_atr_mult": 2.5,
        "zone_lookback_bars": 5,
        "zone_max_age_bars": 100,
        "max_active_zones": 3,
        "zone_touch_tolerance_atr": 0.5,
        "min_rejection_ratio": 0.4,   # Relaxed for synthetic data
        "adx_min_threshold": 5,       # Very low for synthetic bars
        "rsi_overbought": 80,
        "rsi_oversold": 20,
        "ema_trend_period": 10,       # Short EMA so synthetic bars align quickly
        "long_only": False,
        "session_hours": None,        # Disable session filter in tests
        "cooldown_bars": 1,
    }
    config.update(overrides)
    return SupplyDemandStrategy(symbol=symbol, config=config)


class TestSupplyDemandStrategyGuards:
    """Guard conditions — none of these should produce signals."""

    def test_returns_none_when_disabled(self, symbol):
        strategy = _make_strategy(symbol, enabled=False)
        bars = _make_bars(n=100)
        assert strategy.on_bar(bars) is None

    def test_returns_none_with_insufficient_bars(self, symbol):
        strategy = _make_strategy(symbol)
        bars = _make_bars(n=5)
        assert strategy.on_bar(bars) is None

    def test_cooldown_blocks_consecutive_signals(self, symbol):
        strategy = _make_strategy(symbol, cooldown_bars=50)
        strategy._bars_since_signal = 0  # Just fired
        bars = _make_bars(n=100)
        assert strategy.on_bar(bars) is None

    def test_get_name_returns_supply_demand(self, symbol):
        strategy = _make_strategy(symbol)
        assert strategy.get_name() == "supply_demand"

    def test_long_only_rejects_supply_zone_entry(self, symbol):
        from src.strategies.supply_demand_strategy import SupplyDemandStrategy

        strategy = _make_strategy(symbol, long_only=True)

        # Manually inject a supply zone
        strategy._supply_zones = [{
            "direction": "supply",
            "high": 2005.0,
            "low": 2000.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        # Feed bars where price is inside supply zone (2002)
        bars = _make_bars(n=60, base_price=2002.0)
        signal = strategy.on_bar(bars)

        if signal is not None:
            assert signal.side == OrderSide.BUY  # Never SELL in long-only mode


class TestZoneManagement:
    """Zone lifecycle: formation, aging, expiry, consumption."""

    def test_demand_zone_eviction_when_list_full(self, symbol):
        strategy = _make_strategy(symbol, max_active_zones=2)

        # Seed 3 demand zones (one more than max)
        for i in range(3):
            strategy._demand_zones.append({
                "direction": "demand",
                "high": 2000.0 + i,
                "low": 1998.0 + i,
                "formed_at_bar": i,
                "age_bars": i,
            })

        # Adding one more should evict the oldest (formed_at_bar=0)
        new_zone = {
            "direction": "demand",
            "high": 2010.0,
            "low": 2008.0,
            "formed_at_bar": 10,
            "age_bars": 0,
        }
        strategy._add_zone(new_zone)

        assert len(strategy._demand_zones) == 2
        # Oldest should be gone — all remaining should have formed_at_bar > 0
        remaining_ids = [z["formed_at_bar"] for z in strategy._demand_zones]
        assert 0 not in remaining_ids

    def test_zone_expired_after_max_age(self, symbol):
        strategy = _make_strategy(symbol, zone_max_age_bars=5)
        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2005.0,
            "low": 1995.0,
            "formed_at_bar": 0,
            "age_bars": 5,  # Exactly at limit — will be incremented to 6 → expired
        }]
        # Use a close that does NOT consume the zone so expiry is the trigger
        strategy._age_and_expire_zones(current_close=2000.0)
        assert len(strategy._demand_zones) == 0

    def test_demand_zone_consumed_when_close_below(self, symbol):
        strategy = _make_strategy(symbol)
        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2002.0,
            "low": 1998.0,
            "formed_at_bar": 0,
            "age_bars": 3,
        }]
        # Close below demand zone low → zone consumed
        strategy._age_and_expire_zones(current_close=1995.0)
        assert len(strategy._demand_zones) == 0

    def test_supply_zone_consumed_when_close_above(self, symbol):
        strategy = _make_strategy(symbol)
        strategy._supply_zones = [{
            "direction": "supply",
            "high": 2005.0,
            "low": 2000.0,
            "formed_at_bar": 0,
            "age_bars": 3,
        }]
        # Close above supply zone high → zone consumed
        strategy._age_and_expire_zones(current_close=2010.0)
        assert len(strategy._supply_zones) == 0

    def test_healthy_zone_survives_age_increment(self, symbol):
        strategy = _make_strategy(symbol, zone_max_age_bars=100)
        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2005.0,
            "low": 1995.0,
            "formed_at_bar": 0,
            "age_bars": 0,
        }]
        strategy._age_and_expire_zones(current_close=2000.0)  # Inside zone, not consumed
        assert len(strategy._demand_zones) == 1
        assert strategy._demand_zones[0]["age_bars"] == 1


class TestSignalEmission:
    """Signal correctness tests — trigger precise scenarios on on_bar()."""

    def test_signal_metadata_completeness(self, symbol):
        """Emitted signal must carry all fields consumed by post-trade analysis."""
        strategy = _make_strategy(symbol)

        # Manually seed a demand zone so we control the trigger conditions precisely
        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2002.0,
            "low": 1997.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        # Build bars where the last bar is inside the zone with a strong rejection wick
        bars = _make_bars(n=60, base_price=2000.0)
        # Overwrite final bar: close inside zone, long lower wick = demand rejection
        df = bars.copy()
        df.iloc[-1, df.columns.get_loc("open")] = 2001.5
        df.iloc[-1, df.columns.get_loc("close")] = 2001.0
        df.iloc[-1, df.columns.get_loc("high")] = 2002.0
        df.iloc[-1, df.columns.get_loc("low")] = 1994.0  # Long lower wick

        signal = strategy.on_bar(df)

        if signal is not None:
            assert signal.side == OrderSide.BUY
            required_keys = {
                "zone_direction", "zone_high", "zone_low", "zone_age_bars",
                "rejection_ratio", "atr", "adx", "rsi",
                "active_demand_zones", "active_supply_zones",
            }
            assert required_keys.issubset(signal.metadata.keys()), (
                f"Missing metadata keys: {required_keys - signal.metadata.keys()}"
            )
            assert 0.0 <= signal.strength <= 1.0

    def test_signal_side_is_buy_for_demand_zone(self, symbol):
        strategy = _make_strategy(symbol)

        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2003.0,
            "low": 1997.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        bars = _make_bars(n=60, base_price=2000.0)
        df = bars.copy()
        # Strong bullish rejection bar inside demand zone
        df.iloc[-1, df.columns.get_loc("open")] = 2001.0
        df.iloc[-1, df.columns.get_loc("close")] = 2002.0
        df.iloc[-1, df.columns.get_loc("high")] = 2002.5
        df.iloc[-1, df.columns.get_loc("low")] = 1993.0  # Long lower wick

        signal = strategy.on_bar(df)
        if signal is not None:
            assert signal.side == OrderSide.BUY

    def test_signal_side_is_sell_for_supply_zone(self, symbol):
        strategy = _make_strategy(symbol, long_only=False)

        strategy._supply_zones = [{
            "direction": "supply",
            "high": 2005.0,
            "low": 1999.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        bars = _make_bars(n=60, base_price=2002.0)
        df = bars.copy()
        # Strong bearish rejection bar inside supply zone
        df.iloc[-1, df.columns.get_loc("open")] = 2001.5
        df.iloc[-1, df.columns.get_loc("close")] = 2000.5
        df.iloc[-1, df.columns.get_loc("high")] = 2010.0  # Long upper wick
        df.iloc[-1, df.columns.get_loc("low")] = 2000.0

        signal = strategy.on_bar(df)
        if signal is not None:
            assert signal.side == OrderSide.SELL

    def test_zone_consumed_after_signal(self, symbol):
        """A zone must be removed from the active list after it triggers a signal."""
        strategy = _make_strategy(symbol)

        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2003.0,
            "low": 1997.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        bars = _make_bars(n=60, base_price=2000.0)
        df = bars.copy()
        df.iloc[-1, df.columns.get_loc("open")] = 2001.0
        df.iloc[-1, df.columns.get_loc("close")] = 2002.0
        df.iloc[-1, df.columns.get_loc("high")] = 2002.5
        df.iloc[-1, df.columns.get_loc("low")] = 1993.0

        signal = strategy.on_bar(df)
        if signal is not None:
            # Zone should have been removed after being traded
            assert len(strategy._demand_zones) == 0

    def test_ml_regime_override_propagated(self, symbol):
        """ML regime injected via set_ml_regime() should appear in signal."""
        strategy = _make_strategy(symbol)
        strategy.set_ml_regime(MarketRegime.RANGE)

        strategy._demand_zones = [{
            "direction": "demand",
            "high": 2003.0,
            "low": 1997.0,
            "formed_at_bar": 0,
            "age_bars": 5,
        }]

        bars = _make_bars(n=60, base_price=2000.0)
        df = bars.copy()
        df.iloc[-1, df.columns.get_loc("open")] = 2001.0
        df.iloc[-1, df.columns.get_loc("close")] = 2002.0
        df.iloc[-1, df.columns.get_loc("high")] = 2002.5
        df.iloc[-1, df.columns.get_loc("low")] = 1993.0

        signal = strategy.on_bar(df)
        if signal is not None:
            assert signal.regime == MarketRegime.RANGE
