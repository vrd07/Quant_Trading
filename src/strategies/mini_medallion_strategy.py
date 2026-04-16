"""
Mini-Medallion Quant Trading Strategy

A statistical trading system combining multiple weak alpha signals into a single
decision score. Trades are executed only when multiple signals agree.
10 distinct signals based on mean-reversion, momentum, volatility, and order flow.
"""

from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

from .base_strategy import BaseStrategy
from ..core.types import Bar, Signal, Symbol
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MiniMedallionStrategy(BaseStrategy):
    """
    10-signal statistical alpha scoring strategy.
    Combines independent weak edges to form a high-probability trade decision.
    """

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)

        self.timeframe = config.get('timeframe', '1m')
        self.score_threshold = config.get('score_threshold', 3.5)
        self.fixed_lot = config.get('fixed_lot', None)

        # Long-only mode: SELL side loses heavily on gold (upward bias)
        self.long_only = config.get('long_only', False)

        # Session filter: only trade during profitable hours
        self.session_filter_enabled = config.get('session_filter_enabled', False)
        self.allowed_hours = config.get('allowed_hours', [])

        # ADX trend filter: require minimum trend strength
        self.adx_min_threshold = config.get('adx_min_threshold', 0)

        # Trade cooldown: minimum bars between signals to prevent overtrading
        self.cooldown_bars = config.get('cooldown_bars', 30)
        self._bars_since_signal = self.cooldown_bars

        # Signal Weights — cleaned up:
        # - Removed lead_lag (always returns 0, dead code)
        # - Removed vwap_reversion (duplicate of mean_reversion, both measure VWAP distance)
        # - Merged session_volatility + volatility_spike into single volatility_regime signal
        #   (old signals contradicted each other: one followed vol bars, other faded them)
        self.weights = config.get('weights', {
            'mean_reversion': 1.2,
            'momentum_burst': 1.0,
            'volatility_expansion': 1.2,
            'order_flow': 1.1,
            'liquidity_sweep': 1.3,
            'market_regime': 1.0,
            'volatility_regime': 0.8
        })

    def get_name(self) -> str:
        return "mini_medallion"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        # Need enough bars for 30-period VWAP and other indicators
        if len(bars) < 50:
            return None

        # Session filter: skip bars outside profitable hours
        if self.session_filter_enabled and self.allowed_hours:
            bar_hour = bars.index[-1].hour if hasattr(bars.index[-1], 'hour') else 0
            if bar_hour not in self.allowed_hours:
                return None

        # Cooldown to prevent overtrading
        self._bars_since_signal += 1
        if self._bars_since_signal < self.cooldown_bars:
            return None

        # Calculate base indicators
        vwap = Indicators.vwap(bars)
        atr = Indicators.atr(bars, period=14)
        vol_delta = Indicators.volume_delta(bars) # Proxy for order flow imbalance
        bb_upper, bb_mid, bb_lower = Indicators.bollinger_bands(bars, period=20)
        adx = Indicators.adx(bars, period=14)
        
        current_atr = float(atr.iloc[-1])
        if pd.isna(current_atr) or current_atr <= 0:
            return None
            
        current_price = float(bars['close'].iloc[-1])

        signals = {
            'mean_reversion': self._signal_mean_reversion(bars, vwap),
            'momentum_burst': self._signal_momentum_burst(bars),
            'volatility_expansion': self._signal_volatility_expansion(bars, bb_upper, bb_lower),
            'order_flow': self._signal_order_flow(vol_delta),
            'liquidity_sweep': self._signal_liquidity_sweep(bars),
            'market_regime': self._signal_market_regime(bars, adx),
            'volatility_regime': self._signal_volatility_regime(bars, atr, current_atr)
        }

        # Calculate aggregate alpha score
        alpha_score = 0.0
        for name, sig_val in signals.items():
            alpha_score += sig_val * self.weights.get(name, 1.0)

        # ADX trend filter: require minimum trend strength for higher conviction
        current_adx = float(adx.iloc[-1])
        if self.adx_min_threshold > 0 and current_adx < self.adx_min_threshold:
            return None

        # Decision threshold logic
        side = None
        if alpha_score > self.score_threshold:
            side = OrderSide.BUY
        elif alpha_score < -self.score_threshold:
            side = OrderSide.SELL

        if side is None:
            return None

        # Long-only mode: skip SELL signals (gold has strong upward bias)
        if self.long_only and side == OrderSide.SELL:
            return None

        # EMA trend alignment: only take signals in direction of 50-period EMA
        ema50 = Indicators.ema(bars, period=50)
        current_ema50 = float(ema50.iloc[-1])
        current_close = float(bars['close'].iloc[-1])
        if side == OrderSide.BUY and current_close < current_ema50:
            return None  # Don't buy below EMA50
        if side == OrderSide.SELL and current_close > current_ema50:
            return None  # Don't sell above EMA50

        # Determine Regime based on ADX
        current_adx = float(adx.iloc[-1])
        regime = MarketRegime.TREND if current_adx > 25 else MarketRegime.RANGE

        if side == OrderSide.BUY:
            strength = min((alpha_score - self.score_threshold) / 2.0, 1.0) # Normalize 0 to 1 a bit
        else: # SELL
            strength = min((abs(alpha_score) - self.score_threshold) / 2.0, 1.0)

        # Metadata processing
        metadata = {
            'alpha_score': round(alpha_score, 2),
            'signals': signals,
            'atr': float(current_atr)
        }
        if self.fixed_lot:
            metadata['fixed_lot'] = self.fixed_lot

        # Reset cooldown
        self._bars_since_signal = 0

        # Return Signal
        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_price,
            metadata=metadata
        )

    # --- INDIVIDUAL ALPHA SIGNALS ---
    # Return -1 (Bearish), 0 (Neutral), +1 (Bullish)

    def _signal_mean_reversion(self, bars: pd.DataFrame, vwap: pd.Series) -> int:
        """Signal 1: Reversion from VWAP extreme deviation (Z-score proxy)."""
        close = bars['close']
        current_vwap = vwap.iloc[-1]
        
        # Approximate z-score using 30-period std dev
        std_dev = close.rolling(30).std().iloc[-1]
        if pd.isna(std_dev) or std_dev == 0:
            return 0
            
        z = (close.iloc[-1] - current_vwap) / std_dev
        
        if z > 2.0:
            return -1 # Revert down towards mean
        elif z < -2.0:
            return 1  # Revert up towards mean
        return 0

    def _signal_momentum_burst(self, bars: pd.DataFrame) -> int:
        """Signal 2: Detect short term acceleration."""
        close = bars['close']
        returns_5 = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6]
        
        # Define threshold as 0.05% for 1m bars
        threshold = 0.0005 
        if returns_5 > threshold:
            return 1
        elif returns_5 < -threshold:
            return -1
        return 0

    def _signal_volatility_expansion(self, bars: pd.DataFrame, bb_upper: pd.Series, bb_lower: pd.Series) -> int:
        """Signal 3: Detect breakout conditions from BB width."""
        width = (bb_upper - bb_lower) / bars['close']
        # Rate of change of width over 3 bars
        width_roc = (width.iloc[-1] - width.iloc[-4]) / width.iloc[-4]
        
        current_close = bars['close'].iloc[-1]
        
        if width_roc > 0.1: # Expanding rapidly (>10% increase in 3 bars)
            if current_close > bb_upper.iloc[-2]:  # Breaking up
                return 1
            elif current_close < bb_lower.iloc[-2]: # Breaking down
                return -1
        return 0

    def _signal_order_flow(self, vol_delta: pd.Series) -> int:
        """Signal 5: Order flow imbalance proxy (using Delta)."""
        # We look at moving average of recent vol delta
        recent_delta_mean = vol_delta.iloc[-5:].mean()
        avg_vol = vol_delta.abs().rolling(20).mean().iloc[-1]
        
        if pd.isna(avg_vol) or avg_vol == 0:
            return 0
            
        imbalance = recent_delta_mean / avg_vol
        
        # Since it's symmetric around 0, we use proxy thresholds (-0.5, +0.5)
        if imbalance > 0.5:
            return 1
        elif imbalance < -0.5:
            return -1
        return 0

    def _signal_liquidity_sweep(self, bars: pd.DataFrame) -> int:
        """Signal 6: Liquidity sweep / stop hunt detection."""
        # Detect if we broke recent 20-bar high/low and immediately rejected
        highs = bars['high'].iloc[-21:-1]
        lows = bars['low'].iloc[-21:-1]
        
        recent_high = highs.max()
        recent_low = lows.min()
        
        curr_high = bars['high'].iloc[-1]
        curr_low = bars['low'].iloc[-1]
        curr_close = bars['close'].iloc[-1]
        
        # Swept high but closed lower than recent high -> Fake breakout up -> SHORT
        if curr_high > recent_high and curr_close < recent_high:
            return -1
            
        # Swept low but closed higher than recent low -> Fake breakout down -> LONG
        if curr_low < recent_low and curr_close > recent_low:
            return 1
            
        return 0

    def _signal_market_regime(self, bars: pd.DataFrame, adx: pd.Series) -> int:
        """Market regime. Provides trend-following directional leaning."""
        current_adx = adx.iloc[-1]
        close = bars['close']

        if current_adx > 25:  # Trend regime
            sma20 = close.rolling(20).mean().iloc[-1]
            return 1 if close.iloc[-1] > sma20 else -1
        return 0  # Neutral in range

    def _signal_volatility_regime(self, bars: pd.DataFrame, atr: pd.Series, current_atr: float) -> int:
        """Volatility regime signal — replaces old contradictory session_volatility + volatility_spike.

        Logic: If vol is elevated but NOT spiking, follow the trend (momentum).
        If vol is spiking (>50% jump in 3 bars), fade the move (exhaustion).
        If vol is normal, stay neutral.
        """
        long_atr = Indicators.atr(bars, period=100).iloc[-1]
        if pd.isna(long_atr) or long_atr == 0:
            return 0

        atr_roc = (atr.iloc[-1] - atr.iloc[-3]) / atr.iloc[-3] if atr.iloc[-3] != 0 else 0

        if atr_roc > 0.5:
            # Massive spike = exhaustion — fade current bar
            return -1 if bars['close'].iloc[-1] > bars['open'].iloc[-1] else 1
        elif current_atr > 1.2 * long_atr:
            # Elevated but not spiking — follow momentum
            return 1 if bars['close'].iloc[-1] > bars['open'].iloc[-1] else -1
        return 0
