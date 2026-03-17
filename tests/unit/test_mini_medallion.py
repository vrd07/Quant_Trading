"""
Unit tests for the Mini-Medallion quantitative strategy.
"""

import pytest
import pandas as pd
import numpy as np

from src.core.types import Symbol
from src.core.constants import OrderSide, MarketRegime
from src.strategies.mini_medallion_strategy import MiniMedallionStrategy


@pytest.fixture
def symbol():
    return Symbol(
        ticker="XAUUSD",
        pip_value=0.01,
        min_lot=0.01,
        max_lot=10.0,
        lot_step=0.01,
        value_per_lot=100
    )


def _make_bars(n: int = 100, trend: float = 0.0, base_price: float = 2000.0, seed: int = 42):
    """Create synthentic predictable OHLCV data."""
    np.random.seed(seed)
    closes = [base_price + i * trend + np.random.randn() * 0.1 for i in range(n)]
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=n, freq='1min'),
        'open': [c - 0.5 for c in closes],
        'high': [c + 1.0 for c in closes],
        'low': [c - 1.0 for c in closes],
        'close': closes,
        'volume': [1000.0 + i for i in range(n)],
    }
    return pd.DataFrame(data)


class TestMiniMedallionStrategy:
    
    def _make_strategy(self, symbol, **overrides):
        config = {
            'enabled': True,
            'timeframe': '1m',
            'score_threshold': 3.0,
            'risk_atr_multiplier': 1.0,
            'rr_ratio': 1.5,
            'weights': {
                'mean_reversion': 1.0,
                'momentum_burst': 1.0,         # Simplified weights for easy math
                'volatility_expansion': 1.0,
                'vwap_reversion': 1.0,
                'order_flow': 1.0,
                'liquidity_sweep': 1.0,
                'lead_lag': 1.0,
                'market_regime': 1.0,
                'session_volatility': 1.0,
                'volatility_spike': 1.0
            }
        }
        config.update(overrides)
        return MiniMedallionStrategy(symbol=symbol, config=config)

    def test_no_signal_insufficient_data(self, symbol):
        strategy = self._make_strategy(symbol)
        bars = _make_bars(n=30) # Strategy requires at least 50
        assert strategy.on_bar(bars) is None

    def test_disabled_returns_none(self, symbol):
        strategy = self._make_strategy(symbol, enabled=False)
        bars = _make_bars(n=100)
        assert strategy.on_bar(bars) is None

    def test_strategy_name(self, symbol):
        strategy = self._make_strategy(symbol)
        assert strategy.get_name() == 'mini_medallion'

    def test_strong_momentum_generates_long(self, symbol):
        """A strong momentum push over threshold should generate long signal if threshold low enough."""
        # Set threshold low so a single strong signal (+1) triggers it
        strategy = self._make_strategy(symbol, score_threshold=0.5)
        
        # Create bars that burst upwards at the end
        bars = _make_bars(n=100, trend=0.0)
        # Manually inject a massive bullish momentum burst at the end
        for i in range(1, 6):
            bars.loc[bars.index[-i], 'close'] += 10.0
            
        signal = strategy.on_bar(bars)
        
        # We expect a long signal because momentum burst + mean reversion will trigger
        assert signal is not None
        assert signal.side == OrderSide.LONG
        assert 'alpha_score' in signal.metadata
        
    def test_strong_downward_momentum_generates_short(self, symbol):
        """A strong downward push should generate short signal if threshold low enough."""
        strategy = self._make_strategy(symbol, score_threshold=0.5)
        
        bars = _make_bars(n=100, trend=0.0)
        for i in range(1, 6):
            bars.loc[bars.index[-i], 'close'] -= 10.0
            
        signal = strategy.on_bar(bars)
        
        assert signal is not None
        assert signal.side == OrderSide.SHORT
        assert 'alpha_score' in signal.metadata
        
    def test_high_threshold_prevents_trades(self, symbol):
        """If threshold is unrealistically high, it shouldn't trade."""
        strategy = self._make_strategy(symbol, score_threshold=20.0) # Max possible is ~10
        bars = _make_bars(n=100, trend=1.0)
        assert strategy.on_bar(bars) is None

