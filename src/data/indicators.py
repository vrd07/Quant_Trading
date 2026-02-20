"""
Technical Indicators for Trading Strategies.

All indicators use pandas Series/DataFrame for vectorized calculations.
All functions accept DataFrame with OHLCV columns.

Standard DataFrame format:
    timestamp | open | high | low | close | volume

Returns:
    pandas Series with same index as input
"""

import pandas as pd
import numpy as np
from typing import Tuple, Optional
from decimal import Decimal


class Indicators:
    """Technical indicator calculations."""
    
    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average True Range - measures volatility.
        
        ATR = SMA(True Range, period)
        True Range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        
        Args:
            df: DataFrame with high, low, close columns
            period: Lookback period
        
        Returns:
            Series with ATR values
        """
        high = df['high']
        low = df['low']
        close = df['close']
        prev_close = close.shift(1)
        
        # True Range components
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        
        # True Range = max of the three
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR = SMA of True Range
        atr = true_range.rolling(window=period).mean()
        
        return atr
    
    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average Directional Index - measures trend strength.
        
        ADX > 25: Strong trend
        ADX < 20: Weak trend / ranging
        
        Args:
            df: DataFrame with high, low, close
            period: Lookback period
        
        Returns:
            Series with ADX values (0-100)
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # Calculate +DM and -DM
        high_diff = high.diff()
        low_diff = -low.diff()
        
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
        
        # Calculate ATR
        atr = Indicators.atr(df, period)
        
        # Calculate +DI and -DI
        plus_dm_smooth = pd.Series(plus_dm).rolling(window=period).sum()
        minus_dm_smooth = pd.Series(minus_dm).rolling(window=period).sum()
        
        plus_di = 100 * (plus_dm_smooth / atr)
        minus_di = 100 * (minus_dm_smooth / atr)
        
        # Calculate DX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        
        # ADX = SMA of DX
        adx = dx.rolling(window=period).mean()
        
        return adx
    
    @staticmethod
    def donchian_channel(
        df: pd.DataFrame,
        period: int = 20
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Donchian Channel - breakout indicator.
        
        Upper = highest high over period
        Lower = lowest low over period
        Middle = (upper + lower) / 2
        
        Breakout strategy: Buy on break above upper, sell on break below lower
        
        Args:
            df: DataFrame with high, low columns
            period: Lookback period
        
        Returns:
            (upper_band, middle_band, lower_band)
        """
        upper = df['high'].rolling(window=period).max()
        lower = df['low'].rolling(window=period).min()
        middle = (upper + lower) / 2
        
        return upper, middle, lower
    
    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        """
        Volume Weighted Average Price.
        
        VWAP = Σ(Price × Volume) / Σ(Volume)
        
        Typical Price = (High + Low + Close) / 3
        
        Used for mean reversion: price far from VWAP tends to revert.
        
        Args:
            df: DataFrame with high, low, close, volume
        
        Returns:
            Series with VWAP values
        """
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        
        # VWAP = cumulative sum of (typical_price × volume) / cumulative volume
        vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
        
        return vwap
    
    @staticmethod
    def zscore(df: pd.DataFrame, period: int = 20, price_col: str = 'close') -> pd.Series:
        """
        Z-Score for mean reversion.
        
        Z = (Price - Mean) / StdDev
        
        Interpretation:
        - Z > +2: Overbought (2 std devs above mean)
        - Z < -2: Oversold (2 std devs below mean)
        - Z near 0: At mean
        
        Args:
            df: DataFrame with price column
            period: Lookback period for mean/std
            price_col: Column name for price (default 'close')
        
        Returns:
            Series with Z-score values
        """
        price = df[price_col]
        
        rolling_mean = price.rolling(window=period).mean()
        rolling_std = price.rolling(window=period).std()
        
        zscore = (price - rolling_mean) / rolling_std
        
        return zscore
    
    @staticmethod
    def bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        num_std: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands - volatility indicator.
        
        Middle = SMA(close, period)
        Upper = Middle + (num_std × StdDev)
        Lower = Middle - (num_std × StdDev)
        
        Price bouncing between bands suggests ranging market.
        Price breaking bands suggests potential trend.
        
        Args:
            df: DataFrame with close column
            period: Lookback period
            num_std: Number of standard deviations for bands
        
        Returns:
            (upper_band, middle_band, lower_band)
        """
        close = df['close']
        
        middle = close.rolling(window=period).mean()
        std = close.rolling(window=period).std()
        
        upper = middle + (num_std * std)
        lower = middle - (num_std * std)
        
        return upper, middle, lower
    
    @staticmethod
    def sma(df: pd.DataFrame, period: int, price_col: str = 'close') -> pd.Series:
        """
        Simple Moving Average.
        
        SMA = sum(prices) / period
        
        Args:
            df: DataFrame
            period: Lookback period
            price_col: Column to calculate SMA on
        
        Returns:
            Series with SMA values
        """
        return df[price_col].rolling(window=period).mean()
    
    @staticmethod
    def ema(df: pd.DataFrame, period: int, price_col: str = 'close') -> pd.Series:
        """
        Exponential Moving Average - more weight on recent prices.
        
        EMA = price × k + EMA_prev × (1 - k)
        where k = 2 / (period + 1)
        
        Args:
            df: DataFrame
            period: Lookback period
            price_col: Column to calculate EMA on
        
        Returns:
            Series with EMA values
        """
        return df[price_col].ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Relative Strength Index - momentum oscillator.
        
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss
        
        Interpretation:
        - RSI > 70: Overbought
        - RSI < 30: Oversold
        
        Args:
            df: DataFrame with close column
            period: Lookback period (typically 14)
        
        Returns:
            Series with RSI values (0-100)
        """
        close = df['close']
        delta = close.diff()
        
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def stochastic(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series]:
        """
        Stochastic Oscillator - momentum indicator.
        
        %K = 100 × (Close - Low_N) / (High_N - Low_N)
        %D = SMA(%K, 3)
        
        Interpretation:
        - %K > 80: Overbought
        - %K < 20: Oversold
        
        Args:
            df: DataFrame with high, low, close
            period: Lookback period
        
        Returns:
            (%K, %D) as tuple of Series
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        lowest_low = low.rolling(window=period).min()
        highest_high = high.rolling(window=period).max()
        
        k = 100 * (close - lowest_low) / (highest_high - lowest_low)
        d = k.rolling(window=3).mean()
        
        return k, d
    
    @staticmethod
    def macd(
        df: pd.DataFrame,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD - Moving Average Convergence Divergence.
        
        MACD Line = EMA(12) - EMA(26)
        Signal Line = EMA(MACD, 9)
        Histogram = MACD - Signal
        
        Signals:
        - MACD crosses above Signal: Bullish
        - MACD crosses below Signal: Bearish
        
        Args:
            df: DataFrame with close column
            fast_period: Fast EMA period
            slow_period: Slow EMA period
            signal_period: Signal line period
        
        Returns:
            (macd_line, signal_line, histogram)
        """
        close = df['close']
        
        fast_ema = close.ewm(span=fast_period, adjust=False).mean()
        slow_ema = close.ewm(span=slow_period, adjust=False).mean()
        
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def volatility(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Historical volatility - standard deviation of returns.
        
        Volatility = StdDev(log returns) × sqrt(252) for annualization
        
        Args:
            df: DataFrame with close column
            period: Lookback period
        
        Returns:
            Series with volatility values (annualized if period is daily)
        """
        close = df['close']
        
        # Log returns
        returns = np.log(close / close.shift(1))
        
        # Rolling standard deviation
        vol = returns.rolling(window=period).std()
        
        # Annualize (assuming daily bars, adjust if needed)
        vol_annualized = vol * np.sqrt(252)
        
        return vol_annualized
    
    @staticmethod
    def hurst_exponent(df: pd.DataFrame, period: int = 100, price_col: str = 'close') -> pd.Series:
        """
        Hurst Exponent - determines if price series is trending or mean-reverting.
        
        Uses Rescaled Range (R/S) analysis.
        
        Interpretation:
        - H > 0.5: Trending (persistent) - use breakout/momentum strategies
        - H < 0.5: Mean-reverting (anti-persistent) - use mean reversion strategies
        - H ≈ 0.5: Random walk - avoid trading
        
        Args:
            df: DataFrame with price column
            period: Lookback period for calculation (recommended 100+)
            price_col: Column name for price
        
        Returns:
            Series with Hurst exponent values (0 to 1)
        """
        prices = df[price_col].values
        n = len(prices)
        
        if n < period:
            return pd.Series([np.nan] * n, index=df.index)
        
        hurst_values = []
        
        for i in range(n):
            if i < period - 1:
                hurst_values.append(np.nan)
                continue
            
            # Get window of prices
            window = prices[i - period + 1:i + 1]
            
            # Calculate returns
            returns = np.diff(np.log(window))
            
            if len(returns) < 10:
                hurst_values.append(np.nan)
                continue
            
            # Mean of returns
            mean_return = np.mean(returns)
            
            # Cumulative deviations from mean
            deviations = returns - mean_return
            cumulative_deviations = np.cumsum(deviations)
            
            # Range (max - min of cumulative deviations)
            R = np.max(cumulative_deviations) - np.min(cumulative_deviations)
            
            # Standard deviation
            S = np.std(returns, ddof=1)
            
            if S == 0 or R == 0:
                hurst_values.append(np.nan)
                continue
            
            # R/S ratio
            RS = R / S
            
            # Hurst exponent: H = log(R/S) / log(n)
            H = np.log(RS) / np.log(len(returns))
            
            # Clamp to [0, 1] range
            H = max(0, min(1, H))
            
            hurst_values.append(H)
        
        return pd.Series(hurst_values, index=df.index)
    
    @staticmethod
    def intraday_vwap(df: pd.DataFrame, session_col: str = None) -> pd.Series:
        """
        Intraday VWAP that can reset at session boundaries.
        
        For continuous intraday trading, calculates VWAP from session start.
        If no session column provided, uses cumulative VWAP.
        
        Args:
            df: DataFrame with high, low, close, volume columns
            session_col: Optional column name indicating session (for reset)
        
        Returns:
            Series with intraday VWAP values
        """
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        
        if session_col is None or session_col not in df.columns:
            # Standard cumulative VWAP
            cum_tp_vol = (typical_price * df['volume']).cumsum()
            cum_vol = df['volume'].cumsum()
            return cum_tp_vol / cum_vol
        
        # Session-based VWAP (resets each session)
        result = pd.Series(index=df.index, dtype=float)
        
        for session in df[session_col].unique():
            mask = df[session_col] == session
            session_df = df[mask]
            
            tp = (session_df['high'] + session_df['low'] + session_df['close']) / 3
            cum_tp_vol = (tp * session_df['volume']).cumsum()
            cum_vol = session_df['volume'].cumsum()
            
            result.loc[mask] = cum_tp_vol / cum_vol
        
        return result
    
    @staticmethod
    def vwap_deviation(df: pd.DataFrame, atr_multiplier: float = 1.5) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        VWAP with deviation bands based on ATR.
        
        Useful for intraday mean reversion:
        - Price below lower band: Oversold
        - Price above upper band: Overbought
        
        Args:
            df: DataFrame with OHLCV data
            atr_multiplier: Multiplier for ATR bands
        
        Returns:
            (vwap, upper_band, lower_band)
        """
        vwap = Indicators.vwap(df)
        atr = Indicators.atr(df, period=14)
        
        upper = vwap + (atr_multiplier * atr)
        lower = vwap - (atr_multiplier * atr)
        
        return vwap, upper, lower
    
    @staticmethod
    def volume_delta(df: pd.DataFrame) -> pd.Series:
        """
        Volume Delta - Approximate buying vs selling pressure from bar data.
        
        Formula:
            delta = volume × (close - open) / (high - low)
        
        Interpretation:
        - Positive delta: Buyers dominated the bar
        - Negative delta: Sellers dominated the bar
        - Large delta: Strong conviction move
        
        This is an approximation since we don't have actual bid/ask volume.
        
        Args:
            df: DataFrame with OHLCV data
        
        Returns:
            Series with volume delta values
        """
        # Avoid division by zero for doji bars
        bar_range = df['high'] - df['low']
        bar_range = bar_range.replace(0, np.nan)
        
        # Calculate delta: volume weighted by close position in bar
        delta = df['volume'] * (df['close'] - df['open']) / bar_range
        
        # Fill NaN with 0 (neutral bars)
        delta = delta.fillna(0)
        
        return delta
    
    @staticmethod
    def cumulative_volume_delta(df: pd.DataFrame) -> pd.Series:
        """
        Cumulative Volume Delta (CVD) - Running total of volume delta.
        
        Used to identify divergences:
        - Rising price + Rising CVD = Strong uptrend
        - Rising price + Falling CVD = Weak rally (potential reversal)
        - Falling price + Rising CVD = Accumulation (potential reversal up)
        - Falling price + Falling CVD = Strong downtrend
        
        Args:
            df: DataFrame with OHLCV data
        
        Returns:
            Series with cumulative volume delta values
        """
        delta = Indicators.volume_delta(df)
        cvd = delta.cumsum()
        
        return cvd
    
    @staticmethod
    def volume_delta_oscillator(df: pd.DataFrame, fast_period: int = 5, slow_period: int = 20) -> pd.Series:
        """
        Volume Delta Oscillator - Smoothed delta for trend confirmation.
        
        Compares short-term delta to long-term delta.
        
        Interpretation:
        - Positive: Short-term buying pressure > long-term
        - Negative: Short-term selling pressure > long-term
        - Crossing zero: Potential trend change
        
        Args:
            df: DataFrame with OHLCV data
            fast_period: Fast EMA period
            slow_period: Slow EMA period
        
        Returns:
            Series with oscillator values
        """
        delta = Indicators.volume_delta(df)
        
        fast_ema = delta.ewm(span=fast_period, adjust=False).mean()
        slow_ema = delta.ewm(span=slow_period, adjust=False).mean()
        
        oscillator = fast_ema - slow_ema
        
        return oscillator
    
    @staticmethod
    def half_life(df: pd.DataFrame, period: int = 100, price_col: str = 'close') -> pd.Series:
        """
        Calculate Half-Life of mean reversion.
        
        Uses simplified Ornstein-Uhlenbeck process estimation via lag-1 autocorrelation.
        Half-Life = -ln(2) / ln(correlation)
        
        Args:
            df: DataFrame with price column
            period: Lookback period for correlation
            price_col: Column name for price
        
        Returns:
            Series with half-life values
        """
        price = df[price_col]
        lag_price = price.shift(1)
        
        # Calculate price changes
        delta_price = price - lag_price
        
        # Lag-1 correlation (calculating rolling correlation of price vs lagged price)
        # However, for OU process we need correlation of (price(t) - mean) vs (price(t-1) - mean)
        # A simpler proxy for half-life is based on the autoregressive coefficient of lag 1.
        
        # We will use a rolling correlation as a proxy for the autoregressive coefficient
        rho = price.rolling(window=period).corr(lag_price)
        
        # Half-life formula: -ln(2) / ln(abs(rho))
        # Clip rho to avoid log(0) or log(1) issues
        rho = rho.clip(lower=0.01, upper=0.99)
        
        half_life = -np.log(2) / np.log(rho)
        return half_life

    @staticmethod
    def zscore_vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Z-Score based on VWAP.
        
        Z = (Price - VWAP) / StdDev(Price)
        
        Args:
            df: DataFrame with price and volume
            period: Lookback period for standard deviation
        
        Returns:
            Series with VWAP Z-score
        """
        vwap = Indicators.vwap(df)
        std = df['close'].rolling(window=period).std()
        
        zscore = (df['close'] - vwap) / std
        return zscore


# Convenience function for getting multiple indicators at once
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate common indicators and add to DataFrame.
    
    Args:
        df: OHLCV DataFrame
    
    Returns:
        DataFrame with additional indicator columns
    """
    result = df.copy()
    
    # Volatility indicators
    result['atr_14'] = Indicators.atr(df, 14)
    result['atr_20'] = Indicators.atr(df, 20)
    
    # Trend indicators
    result['adx_14'] = Indicators.adx(df, 14)
    result['sma_20'] = Indicators.sma(df, 20)
    result['sma_50'] = Indicators.sma(df, 50)
    result['ema_12'] = Indicators.ema(df, 12)
    result['ema_26'] = Indicators.ema(df, 26)
    
    # Donchian Channel
    upper, middle, lower = Indicators.donchian_channel(df, 20)
    result['donchian_upper'] = upper
    result['donchian_middle'] = middle
    result['donchian_lower'] = lower
    
    # Bollinger Bands
    upper, middle, lower = Indicators.bollinger_bands(df, 20, 2.0)
    result['bb_upper'] = upper
    result['bb_middle'] = middle
    result['bb_lower'] = lower
    
    # Mean reversion indicators
    result['vwap'] = Indicators.vwap(df)
    result['zscore_20'] = Indicators.zscore(df, 20)
    
    # Momentum indicators
    result['rsi_14'] = Indicators.rsi(df, 14)
    
    # MACD
    macd_line, signal_line, histogram = Indicators.macd(df)
    result['macd'] = macd_line
    result['macd_signal'] = signal_line
    result['macd_histogram'] = histogram
    
    return result
