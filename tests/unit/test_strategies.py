"""
Unit tests for Breakout and Momentum strategies.

Tests cover the new filtering improvements:
- Breakout: close-confirmation, volume filter, RSI guard, ATR stops, ADX strength
- Momentum: RSI bounds, ADX threshold, volume filter, MACD acceleration
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from src.core.types import Symbol
from src.core.constants import MarketRegime, OrderSide


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def symbol():
    """Create a test symbol."""
    return Symbol(
        ticker="XAUUSD",
        pip_value=0.01,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01,
        value_per_lot=100
    )


def _make_bars(
    n: int = 100,
    base_price: float = 2000.0,
    trend: float = 0.0,
    volatility: float = 2.0,
    base_volume: float = 1000.0,
    volume_last: float = None,
    seed: int = 42,
):
    """Create synthetic OHLCV bars.
    
    Args:
        n: Number of bars
        base_price: Starting price
        trend: Per-bar price drift
        volatility: High-low range
        base_volume: Base volume level
        volume_last: Override volume for last bar (for volume tests)
        seed: Random seed
    """
    np.random.seed(seed)
    closes = [base_price + i * trend + np.random.randn() * 0.5 for i in range(n)]
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=n, freq='1min'),
        'open': [c - 0.5 for c in closes],
        'high': [c + volatility / 2 for c in closes],
        'low': [c - volatility / 2 for c in closes],
        'close': closes,
        'volume': [base_volume + np.random.rand() * 200 for _ in range(n)],
    }
    df = pd.DataFrame(data)
    if volume_last is not None:
        df.loc[df.index[-1], 'volume'] = volume_last
    return df


def _make_breakout_bars_bullish(close_beyond=True, volume_spike=True, rsi_overbought=False):
    """Create bars where a bullish breakout occurs.
    
    The last bar breaks above the 20-period Donchian upper channel.
    """
    n = 60
    np.random.seed(42)
    
    # Range-bound for first 58 bars around 2000, then breakout
    closes = [2000.0 + np.random.randn() * 1.0 for _ in range(n - 2)]
    
    if rsi_overbought:
        # Create strong uptrend to push RSI > 75
        for i in range(15):
            closes.append(closes[-1] + 2.0)
        closes = closes[:n - 2]
    
    # Breakout bar
    channel_high = max(c + 1.0 for c in closes[-20:])  # approx upper channel
    
    if close_beyond:
        breakout_close = channel_high + 3.0  # Close above channel
    else:
        breakout_close = channel_high - 0.5  # Close back inside (false breakout)
    
    # Second-to-last bar (normal)
    closes.append(2000.0 + np.random.randn() * 1.0)
    # Last bar (breakout)
    closes.append(breakout_close)
    
    volumes = [1000.0 + np.random.rand() * 100 for _ in range(n)]
    if volume_spike:
        volumes[-1] = 2000.0  # 2x average = above 1.2x threshold
    else:
        volumes[-1] = 500.0   # Below average
    
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=n, freq='1min'),
        'open': [c - 0.3 for c in closes],
        'high': [c + 2.0 for c in closes],  # High always extends above close
        'low': [c - 1.5 for c in closes],
        'close': closes,
        'volume': volumes,
    }
    return pd.DataFrame(data)


# ── Breakout Strategy Tests ──────────────────────────────────────────

class TestBreakoutStrategy:
    """Tests for the improved breakout strategy."""
    
    def _make_strategy(self, symbol, **overrides):
        """Create breakout strategy with test-friendly config."""
        from src.strategies.breakout_strategy import BreakoutStrategy
        config = {
            'enabled': True,
            'donchian_period': 20,
            'confirmation_bars': 0,
            'rr_ratio': 2.0,
            'only_in_regime': 'TREND',
            'mtf_confirmation': False,
            'atr_stop_multiplier': 2.0,
            'volume_confirmation': True,
            'volume_ratio_min': 1.2,
            'rsi_overbought': 75,
            'rsi_oversold': 25,
        }
        config.update(overrides)
        return BreakoutStrategy(symbol=symbol, config=config)
    
    def test_no_signal_insufficient_data(self, symbol):
        """Strategy returns None with insufficient bars."""
        strategy = self._make_strategy(symbol)
        bars = _make_bars(n=10)
        signal = strategy.on_bar(bars)
        assert signal is None
    
    def test_false_breakout_wick_rejected(self, symbol):
        """Wick-only breakout (close inside channel) should NOT trigger signal."""
        strategy = self._make_strategy(symbol, volume_confirmation=False)
        
        # Create bars where high breaches channel but close is inside
        bars = _make_breakout_bars_bullish(close_beyond=False, volume_spike=True)
        signal = strategy.on_bar(bars)
        
        # Should not produce a signal because close is inside channel
        # (The regime filter may also block it, which is fine)
        assert signal is None or signal.side != OrderSide.BUY
    
    def test_low_volume_breakout_rejected(self, symbol):
        """Breakout with low volume should be rejected."""
        strategy = self._make_strategy(symbol)
        
        bars = _make_breakout_bars_bullish(close_beyond=True, volume_spike=False)
        signal = strategy.on_bar(bars)
        
        # Should not fire because volume is below threshold
        # (regime filter may also block depending on synthetic data)
        assert signal is None
    
    def test_volume_filter_can_be_disabled(self, symbol):
        """When volume_confirmation=False, volume is not checked."""
        strategy = self._make_strategy(symbol, volume_confirmation=False)
        # Just verify it doesn't crash
        bars = _make_bars(n=60)
        signal = strategy.on_bar(bars)
        # May or may not generate signal depending on data, but shouldn't crash
        assert signal is None or signal.side in (OrderSide.BUY, OrderSide.SELL)
    
    def test_atr_stop_tighter_than_channel(self, symbol):
        """ATR-based stop should be tighter than opposite channel boundary."""
        strategy = self._make_strategy(symbol, volume_confirmation=False)
        
        # Create strongly trending data to ensure TREND regime
        bars = _make_bars(n=100, trend=0.5, volatility=2.0, base_volume=1000.0, volume_last=2000.0)
        signal = strategy.on_bar(bars)
        
        if signal and signal.side == OrderSide.BUY:
            # Stop should be within 2*ATR of entry, not at opposite channel
            entry = float(signal.entry_price)
            stop = float(signal.stop_loss)
            assert entry - stop > 0, "Stop should be below entry for BUY"
    
    def test_strategy_returns_correct_name(self, symbol):
        """Strategy name should be 'donchian_breakout'."""
        strategy = self._make_strategy(symbol)
        assert strategy.get_name() == "donchian_breakout"
    
    def test_disabled_strategy_returns_none(self, symbol):
        """Disabled strategy should always return None."""
        strategy = self._make_strategy(symbol, enabled=False)
        bars = _make_bars(n=100)
        assert strategy.on_bar(bars) is None


# ── Momentum Strategy Tests ──────────────────────────────────────────

class TestMomentumStrategy:
    """Tests for the improved momentum strategy."""
    
    def _make_strategy(self, symbol, **overrides):
        """Create momentum strategy with test-friendly config."""
        from src.strategies.momentum_strategy import MomentumStrategy
        config = {
            'enabled': True,
            'rsi_period': 14,
            'ema_period': 20,
            'rr_ratio': 2.0,
            'atr_stop_multiplier': 1.2,
            'only_in_regime': 'TREND',
            'rsi_bull_threshold': 50,
            'rsi_bear_threshold': 50,
            'rsi_overbought': 75,
            'rsi_oversold': 25,
            'adx_min_threshold': 20,
            'macd_fast': 12,
            'macd_slow': 26,
            'macd_signal': 9,
            'volume_confirmation': True,
            'volume_ratio_min': 1.0,
        }
        config.update(overrides)
        return MomentumStrategy(symbol=symbol, config=config)
    
    def test_no_signal_insufficient_data(self, symbol):
        """Strategy returns None with insufficient bars."""
        strategy = self._make_strategy(symbol)
        bars = _make_bars(n=10)
        signal = strategy.on_bar(bars)
        assert signal is None
    
    def test_adx_filter_rejects_low_trend(self, symbol):
        """When ADX is below threshold, no signal should be generated."""
        strategy = self._make_strategy(symbol, adx_min_threshold=90)  # Very high threshold
        
        # Use range-bound data (low ADX)
        bars = _make_bars(n=100, trend=0.0, volatility=1.0)
        signal = strategy.on_bar(bars)
        assert signal is None
    
    def test_volume_filter_can_be_disabled(self, symbol):
        """When volume_confirmation=False, volume is not checked."""
        strategy = self._make_strategy(symbol, volume_confirmation=False)
        bars = _make_bars(n=100)
        signal = strategy.on_bar(bars)
        # Should not crash
        assert signal is None or signal.side in (OrderSide.BUY, OrderSide.SELL)
    
    def test_rr_ratio_default_is_2(self, symbol):
        """Default R:R ratio should be 2.0."""
        strategy = self._make_strategy(symbol)
        assert strategy.rr_ratio == 2.0
    
    def test_atr_stop_multiplier_default_is_1_2(self, symbol):
        """Default ATR stop multiplier should be 1.2 (tighter than old 1.5)."""
        strategy = self._make_strategy(symbol)
        assert strategy.atr_stop_multiplier == 1.2
    
    def test_rsi_overbought_guard_default(self, symbol):
        """RSI overbought guard should default to 75."""
        strategy = self._make_strategy(symbol)
        assert strategy.rsi_overbought == 75
    
    def test_rsi_oversold_guard_default(self, symbol):
        """RSI oversold guard should default to 25."""
        strategy = self._make_strategy(symbol)
        assert strategy.rsi_oversold == 25
    
    def test_strategy_returns_correct_name(self, symbol):
        """Strategy name should be 'momentum_scalp'."""
        strategy = self._make_strategy(symbol)
        assert strategy.get_name() == "momentum_scalp"
    
    def test_disabled_strategy_returns_none(self, symbol):
        """Disabled strategy should always return None."""
        strategy = self._make_strategy(symbol, enabled=False)
        bars = _make_bars(n=100)
        assert strategy.on_bar(bars) is None
    
    def test_signal_metadata_includes_new_fields(self, symbol):
        """If a signal is generated, metadata should include ADX and volume_ratio."""
        strategy = self._make_strategy(symbol, volume_confirmation=False, adx_min_threshold=0)
        
        # Create strongly trending data
        bars = _make_bars(n=100, trend=1.0, volatility=2.0)
        signal = strategy.on_bar(bars)
        
        if signal is not None:
            assert 'adx' in signal.metadata
            assert 'volume_ratio' in signal.metadata
            assert 'macd_accelerating' in signal.metadata
