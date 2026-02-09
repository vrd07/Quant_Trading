"""
Unit tests for technical indicators.

Tests use synthetic data with known expected results.
"""

import pytest
import pandas as pd
import numpy as np
from decimal import Decimal

from src.data.indicators import Indicators


@pytest.fixture
def sample_bars():
    """Create sample OHLCV data for testing."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + i * 0.1 for i in range(100)],
        'high': [101.0 + i * 0.1 for i in range(100)],
        'low': [99.0 + i * 0.1 for i in range(100)],
        'close': [100.5 + i * 0.1 for i in range(100)],
        'volume': [1000.0] * 100
    }
    return pd.DataFrame(data)


def test_sma_calculation(sample_bars):
    """Test Simple Moving Average."""
    sma_10 = Indicators.sma(sample_bars, period=10)
    
    # SMA should smooth the trend
    assert not sma_10.isna().all()
    
    # First 9 values should be NaN (not enough data)
    assert sma_10[:9].isna().all()
    
    # 10th value should be average of first 10 closes
    expected = sum(sample_bars['close'][:10]) / 10
    assert abs(sma_10.iloc[9] - expected) < 0.01


def test_ema_calculation(sample_bars):
    """Test Exponential Moving Average."""
    ema_10 = Indicators.ema(sample_bars, period=10)
    
    # EMA should not be NaN (uses exponential weighting)
    assert not ema_10.iloc[10:].isna().all()
    
    # EMA should be closer to recent prices than SMA
    sma_10 = Indicators.sma(sample_bars, period=10)
    
    # In uptrend, EMA should be higher than SMA
    assert ema_10.iloc[-1] >= sma_10.iloc[-1]


def test_atr_positive(sample_bars):
    """Test ATR is always positive."""
    atr = Indicators.atr(sample_bars, period=14)
    
    # ATR should be positive (it's a distance measure)
    assert (atr.dropna() > 0).all()


def test_adx_range(sample_bars):
    """Test ADX is in 0-100 range."""
    adx = Indicators.adx(sample_bars, period=14)
    
    # ADX should be between 0 and 100
    assert (adx.dropna() >= 0).all()
    assert (adx.dropna() <= 100).all()


def test_donchian_channel():
    """Test Donchian Channel calculation."""
    # Create data with known high/low
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=30, freq='1h'),
        'open': [100.0] * 30,
        'high': [105.0 if i == 15 else 100.0 for i in range(30)],  # Peak at i=15
        'low': [95.0 if i == 10 else 100.0 for i in range(30)],    # Trough at i=10
        'close': [100.0] * 30,
        'volume': [1000.0] * 30
    }
    df = pd.DataFrame(data)
    
    upper, middle, lower = Indicators.donchian_channel(df, period=20)
    
    # After 20 bars, upper should be 105 (the peak)
    assert upper.iloc[25] == 105.0
    
    # Lower should be 95 (the trough at i=10 is still in window at i=29)
    assert lower.iloc[29] == 95.0


def test_vwap_calculation():
    """Test VWAP calculation."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=10, freq='1h'),
        'high': [101.0] * 10,
        'low': [99.0] * 10,
        'close': [100.0] * 10,
        'open': [100.0] * 10,
        'volume': [1000.0] * 10
    }
    df = pd.DataFrame(data)
    
    vwap = Indicators.vwap(df)
    
    # VWAP should equal typical price when volume is constant
    # Typical price = (101 + 99 + 100) / 3 = 100
    assert abs(vwap.iloc[-1] - 100.0) < 0.01


def test_zscore_interpretation():
    """Test Z-score mean reversion signals."""
    # Create price with small variations then a big jump
    np.random.seed(42)  # For reproducibility
    baseline_prices = [100.0 + np.random.randn() * 0.5 for i in range(100)]
    jump_prices = [125.0] * 5  # Big jump up
    prices = baseline_prices + jump_prices
    
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=105, freq='1h'),
        'open': prices,
        'high': [p + 0.5 for p in prices],
        'low': [p - 0.5 for p in prices],
        'close': prices,
        'volume': [1000.0] * 105
    }
    df = pd.DataFrame(data)
    
    zscore = Indicators.zscore(df, period=20)
    
    # After jump to 125, Z-score should be high (price far above mean)
    # Note: Since rolling window includes recent prices, Z-score won't be as high as with fixed window
    assert zscore.iloc[-1] > 1.5  # Should be well above mean (significantly overbought)


def test_bollinger_bands_width():
    """Test Bollinger Bands widen with volatility."""
    # Low volatility period
    low_vol = {
        'timestamp': pd.date_range('2024-01-01', periods=30, freq='1h'),
        'open': [100.0] * 30,
        'high': [100.1] * 30,
        'low': [99.9] * 30,
        'close': [100.0] * 30,
        'volume': [1000.0] * 30
    }
    df_low = pd.DataFrame(low_vol)
    
    # High volatility period
    high_vol = {
        'timestamp': pd.date_range('2024-01-01', periods=30, freq='1h'),
        'open': [100.0 + i * 2 for i in range(30)],
        'high': [102.0 + i * 2 for i in range(30)],
        'low': [98.0 + i * 2 for i in range(30)],
        'close': [100.0 + i * 2 for i in range(30)],
        'volume': [1000.0] * 30
    }
    df_high = pd.DataFrame(high_vol)
    
    upper_low, _, lower_low = Indicators.bollinger_bands(df_low, 20, 2.0)
    upper_high, _, lower_high = Indicators.bollinger_bands(df_high, 20, 2.0)
    
    # High volatility should have wider bands
    width_low = upper_low.iloc[-1] - lower_low.iloc[-1]
    width_high = upper_high.iloc[-1] - lower_high.iloc[-1]
    
    assert width_high > width_low


def test_rsi_range():
    """Test RSI stays in 0-100 range."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + np.sin(i/10) * 10 for i in range(100)],
        'high': [101.0 + np.sin(i/10) * 10 for i in range(100)],
        'low': [99.0 + np.sin(i/10) * 10 for i in range(100)],
        'close': [100.0 + np.sin(i/10) * 10 for i in range(100)],
        'volume': [1000.0] * 100
    }
    df = pd.DataFrame(data)
    
    rsi = Indicators.rsi(df, period=14)
    
    # RSI should be between 0 and 100
    assert (rsi.dropna() >= 0).all()
    assert (rsi.dropna() <= 100).all()


def test_macd_crossover():
    """Test MACD generates crossover signals."""
    # Create uptrending price
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + i * 0.5 for i in range(100)],
        'high': [101.0 + i * 0.5 for i in range(100)],
        'low': [99.0 + i * 0.5 for i in range(100)],
        'close': [100.0 + i * 0.5 for i in range(100)],
        'volume': [1000.0] * 100
    }
    df = pd.DataFrame(data)
    
    macd, signal, histogram = Indicators.macd(df)
    
    # In uptrend, MACD should eventually cross above signal
    # Histogram should turn positive
    assert histogram.iloc[-10:].mean() > 0


def test_stochastic_range():
    """Test Stochastic Oscillator stays in 0-100 range."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + np.sin(i/10) * 10 for i in range(100)],
        'high': [101.0 + np.sin(i/10) * 10 for i in range(100)],
        'low': [99.0 + np.sin(i/10) * 10 for i in range(100)],
        'close': [100.0 + np.sin(i/10) * 10 for i in range(100)],
        'volume': [1000.0] * 100
    }
    df = pd.DataFrame(data)
    
    k, d = Indicators.stochastic(df, period=14)
    
    # Both %K and %D should be between 0 and 100
    assert (k.dropna() >= 0).all()
    assert (k.dropna() <= 100).all()
    assert (d.dropna() >= 0).all()
    assert (d.dropna() <= 100).all()


def test_volatility_positive():
    """Test historical volatility is positive."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + np.random.randn() for i in range(100)],
        'high': [101.0 + np.random.randn() for i in range(100)],
        'low': [99.0 + np.random.randn() for i in range(100)],
        'close': [100.0 + np.random.randn() for i in range(100)],
        'volume': [1000.0] * 100
    }
    df = pd.DataFrame(data)
    
    vol = Indicators.volatility(df, period=20)
    
    # Volatility should be positive
    assert (vol.dropna() >= 0).all()


def test_calculate_indicators_comprehensive():
    """Test comprehensive indicator calculation function."""
    data = {
        'timestamp': pd.date_range('2024-01-01', periods=100, freq='1h'),
        'open': [100.0 + i * 0.1 for i in range(100)],
        'high': [101.0 + i * 0.1 for i in range(100)],
        'low': [99.0 + i * 0.1 for i in range(100)],
        'close': [100.5 + i * 0.1 for i in range(100)],
        'volume': [1000.0] * 100
    }
    df = pd.DataFrame(data)
    
    from src.data.indicators import calculate_indicators
    result = calculate_indicators(df)
    
    # Check that all expected indicators are present
    expected_columns = [
        'atr_14', 'adx_14', 'sma_20', 'ema_12',
        'donchian_upper', 'vwap', 'zscore_20', 'rsi_14', 'macd'
    ]
    
    for col in expected_columns:
        assert col in result.columns
        # Should have some non-NaN values
        assert not result[col].isna().all()
