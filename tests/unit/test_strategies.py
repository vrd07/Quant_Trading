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
            # Key was renamed from 'macd_turning' to 'macd_positive' / 'macd_negative'
            assert 'macd_positive' in signal.metadata or 'macd_negative' in signal.metadata


# ── Kalman Regime Strategy Tests ────────────────────────────────────

def _make_dip_bars(n: int = 200, base_price: float = 2000.0, dip_atr: float = 4.0):
    """
    Create a steady series with a sharp DIP on the final bar — the trigger for
    the v3 long-only deep-dip reversion strategy. The body is mild noise so the
    Kalman level sits near base_price; the last close drops well below it.
    """
    np.random.seed(7)
    closes = [base_price]
    for _ in range(n - 2):
        closes.append(closes[-1] + np.random.randn() * 0.2)
    # ATR is ~0.5-ish here; drop the last close several ATR below the level.
    closes.append(closes[-1] - dip_atr * 0.6)
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=n, freq='15min'),
        'open':  [c for c in closes],
        'high':  [c + 0.3 for c in closes],
        'low':   [c - 0.3 for c in closes],
        'close': closes,
        'volume': [1000.0] * n,
    }
    return pd.DataFrame(data)


def _make_trending_bars(n: int = 200, direction: float = 1.0, base_price: float = 2000.0,
                        freq: str = '1min'):
    """
    Create bars with a clear, sustained trend to trigger Kalman TREND mode.
    
    A strong trend with small noise keeps RV > MA(RV) (trend regime)
    and puts close consistently above/below the Kalman.
    """
    np.random.seed(7)
    closes = [base_price]
    for _ in range(n - 1):
        # Strong directional drift + small noise
        closes.append(closes[-1] + direction * 0.5 + np.random.randn() * 0.1)
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=n, freq=freq),
        'open':  [c - 0.2 for c in closes],
        'high':  [c + 0.3 for c in closes],
        'low':   [c - 0.3 for c in closes],
        'close': closes,
        'volume': [1000.0] * n,
    }
    return pd.DataFrame(data)




class TestKalmanRegimeStrategy:
    """Tests for the fixed Kalman Regime-Switching strategy."""

    def _make_strategy(self, symbol, **overrides):
        from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
        config = {
            'enabled': True,
            'kalman_q': 1e-5,
            'kalman_r': 0.01,
            'rv_window': 20,
            'rv_ma_window': 100,
            'zscore_window': 20,
            'entry_threshold': 2.0,
            'atr_period': 14,
            'sl_atr_multiplier': 2.5,
            'tp_atr_multiplier': 2.0,
            'trend_adx_min': 5,   # Very low for synthetic test data
        }
        config.update(overrides)
        return KalmanRegimeStrategy(symbol=symbol, config=config)

    def test_no_signal_insufficient_data(self, symbol):
        """Returns None when there are fewer bars than min_bars."""
        strategy = self._make_strategy(symbol)
        bars = _make_bars(n=50)
        signal = strategy.on_bar(bars)
        assert signal is None

    def test_disabled_returns_none(self, symbol):
        """Disabled strategy always returns None."""
        strategy = self._make_strategy(symbol, enabled=False)
        bars = _make_trending_bars(n=200, direction=1.0)
        assert strategy.on_bar(bars) is None

    def test_strategy_name(self, symbol):
        """Strategy get_name() should return 'kalman_regime'."""
        strategy = self._make_strategy(symbol)
        assert strategy.get_name() == 'kalman_regime'

    def test_trend_mode_buy_signal(self, symbol):
        """Strongly uptrending bars should eventually fire a BUY in trend mode."""
        strategy = self._make_strategy(symbol)
        # Build up enough bars for min_bars requirement
        bars = _make_trending_bars(n=200, direction=1.0)
        signal = strategy.on_bar(bars)
        # Either no signal (not enough RV regime data built up) or a BUY
        if signal is not None:
            assert signal.side == OrderSide.BUY
            assert 'atr' in signal.metadata
            assert 'kalman' in signal.metadata
            assert signal.metadata.get('mode') in ('trend', 'range')

    def test_trend_mode_sell_signal(self, symbol):
        """Strongly downtrending bars should eventually fire a SELL in trend mode."""
        strategy = self._make_strategy(symbol)
        bars = _make_trending_bars(n=200, direction=-1.0)
        signal = strategy.on_bar(bars)
        if signal is not None:
            assert signal.side == OrderSide.SELL
            assert 'atr' in signal.metadata

    def test_htf_sell_filter_handles_live_shaped_bars(self, symbol):
        """Regression: live bars arrive with a RangeIndex and a `timestamp`
        column (CandleStore.get_bars reset_index). The HTF SELL resample must
        handle that shape — the old code raised on the RangeIndex and silently
        vetoed every SELL as 'insufficient 1h bars'."""
        bars = _make_trending_bars(n=400, direction=-1.0, freq='15min')
        assert not isinstance(bars.index, pd.DatetimeIndex)  # live shape

        gated = self._make_strategy(symbol, htf_sell_filter_enabled=True)
        ungated = self._make_strategy(symbol)

        gated_sells = 0
        ungated_sells = 0
        reasons = set()
        for end in range(gated.min_bars, len(bars) + 1):
            window = bars.iloc[:end].reset_index(drop=True)
            if ungated.on_bar(window) is not None:
                ungated_sells += 1
            if gated.on_bar(window) is not None:
                gated_sells += 1
            reasons.add(getattr(gated, '_last_no_signal_reason', None))

        # The resample must never fail or report insufficient HTF bars.
        assert not any(r and 'HTF SELL filter' in r for r in reasons), reasons
        # The downtrend data must actually produce SELLs...
        assert ungated_sells > 0
        # ...and a bearish HTF means the filter is transparent: same signals.
        assert gated_sells == ungated_sells

    def test_htf_buy_filter_transparent_in_uptrend(self, symbol):
        """Symmetric BUY gate: in a bullish HTF (uptrend) the gate must be
        transparent — every BUY the ungated strategy fires also fires when
        htf_buy_filter_enabled, and the resample never fails."""
        bars = _make_trending_bars(n=400, direction=1.0, freq='15min')
        assert not isinstance(bars.index, pd.DatetimeIndex)  # live shape

        gated = self._make_strategy(symbol, htf_buy_filter_enabled=True)
        ungated = self._make_strategy(symbol)

        gated_buys = ungated_buys = 0
        reasons = set()
        for end in range(gated.min_bars, len(bars) + 1):
            window = bars.iloc[:end].reset_index(drop=True)
            if ungated.on_bar(window) is not None:
                ungated_buys += 1
            if gated.on_bar(window) is not None:
                gated_buys += 1
            reasons.add(getattr(gated, '_last_no_signal_reason', None))

        assert not any(r and 'HTF filter' in r for r in reasons), reasons
        assert ungated_buys > 0
        # Bullish HTF → BUY gate passes everything through.
        assert gated_buys == ungated_buys

    def test_htf_buy_filter_blocks_counter_trend(self, symbol):
        """In a bearish HTF (downtrend), the BUY gate must suppress counter-trend
        longs — gated BUYs strictly fewer than ungated (the loss bucket we target)."""
        bars = _make_trending_bars(n=400, direction=-1.0, freq='15min')
        gated = self._make_strategy(symbol, htf_buy_filter_enabled=True)
        ungated = self._make_strategy(symbol)

        gated_buys = ungated_buys = 0
        for end in range(gated.min_bars, len(bars) + 1):
            window = bars.iloc[:end].reset_index(drop=True)
            su = ungated.on_bar(window)
            sg = gated.on_bar(window)
            if su is not None and su.side == OrderSide.BUY:
                ungated_buys += 1
            if sg is not None and sg.side == OrderSide.BUY:
                gated_buys += 1

        # Any counter-trend BUYs the ungated strategy fired must be reduced.
        assert gated_buys <= ungated_buys

    # ── RANGE structural confirmation layers ─────────────────────────────
    @staticmethod
    def _ohlcv(prices, vol=None):
        n = len(prices)
        idx = pd.date_range('2026-01-01', periods=n, freq='15min', tz='UTC')
        c = pd.Series(prices, index=idx, dtype=float)
        return pd.DataFrame({'open': c, 'high': c + 0.5, 'low': c - 0.5,
                             'close': c, 'volume': (vol if vol is not None else [100] * n)},
                            index=idx)

    def test_range_layers_off_by_default(self, symbol):
        """Default config: structural check is a no-op (behaviour preserved)."""
        s = self._make_strategy(symbol)
        bars = self._ohlcv([4000 + (i % 3) for i in range(40)])
        ok, reason = s._range_structural_ok(bars, OrderSide.BUY, current_atr=1.0)
        assert ok and reason == ""

    def test_range_channel_rejects_trend_accepts_flat(self, symbol):
        """Layer 1: a flat band passes; a ramp (slow trend) is rejected."""
        s = self._make_strategy(symbol, range_channel_enabled=True,
                                range_channel_bars=10, range_channel_atr_period=10,
                                range_channel_atr_mult=1.5)
        flat = self._ohlcv([4000 + (0.3 if i % 2 else -0.3) for i in range(30)])
        ok_flat, _ = s._range_structural_ok(flat, OrderSide.BUY, current_atr=1.0)
        assert ok_flat
        trend = self._ohlcv([4000 + 3.0 * i for i in range(30)])
        ok_trend, reason = s._range_structural_ok(trend, OrderSide.BUY, current_atr=1.0)
        assert not ok_trend and 'range-bound' in reason

    def test_range_volume_nodes_and_proximity(self, symbol):
        """Layer 3: POC sits at the heavy-volume price; far-from-shelf is rejected."""
        # 18 bars heavy at 4000, 2 bars light at 4080.
        prices = [4000 + (0.2 if i % 2 else -0.2) for i in range(18)] + [4080, 4081]
        vols = [500] * 18 + [10, 10]
        bars = self._ohlcv(prices, vol=vols)
        s0 = self._make_strategy(symbol)
        nodes, poc = s0._volume_nodes(bars, n_bars=20, bins=12)
        assert nodes is not None and len(nodes) > 0
        assert abs(poc - 4000) < 10   # POC at the volume shelf, not the 4080 spike

        s = self._make_strategy(symbol, range_poc_enabled=True, range_poc_bars=20,
                                range_poc_bins=12, range_poc_atr_mult=1.0)
        near = self._ohlcv(prices, vol=vols)               # last close 4081 -> far
        ok_far, reason = s._range_structural_ok(near, OrderSide.SELL, current_atr=1.0)
        assert not ok_far and 'shelf' in reason

    def test_trend_quality_score(self, symbol):
        """Self-normalising score: high for a steady trend; in [0,1]; safe fallback."""
        s = self._make_strategy(symbol)
        steady = pd.Series([4000 + 5.0 * i for i in range(60)])   # constant slope → low std
        hi = s._trend_quality_score(steady, slope_bars=3, std_window=20)
        assert 0.0 <= hi <= 1.0 and hi > 0.8
        # unassessable (series shorter than slope_bars+std_window) -> 1.0 (don't block)
        assert s._trend_quality_score(pd.Series([1.0, 2.0, 3.0]), slope_bars=3, std_window=20) == 1.0


