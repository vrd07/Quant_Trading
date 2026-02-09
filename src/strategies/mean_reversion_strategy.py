"""
Mean Reversion Strategy - Z-score based mean reversion.

Entry Logic:
- Only trade when regime = RANGE
- Buy when Z-score < -2 (oversold, price 2 std devs below mean)
- Sell when Z-score > +2 (overbought, price 2 std devs above mean)
- Optional: Use MTF filter (allow NEUTRAL bias for ranging conditions)

Exit Logic:
- Take profit: Z-score returns to 0 (price at mean)
- Stop loss: Z-score reaches -3 or +3 (extreme)

Uses VWAP as the mean for Z-score calculation.
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from .multi_timeframe_filter import MultiTimeframeFilter, MTFBias
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class MeanReversionStrategy(BaseStrategy):
    """Z-score mean reversion strategy with optional MTF confirmation."""
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.zscore_lookback = config.get('zscore_lookback', 20)
        self.entry_threshold = config.get('entry_threshold', 2.0)
        self.exit_threshold = config.get('exit_threshold', 0.5)
        self.stop_threshold = config.get('stop_threshold', 3.0)
        self.rr_ratio = config.get('rr_ratio', 1.5)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]
        
        # Multi-timeframe confirmation (optional for mean reversion)
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None
        self._pending_bars_by_tf: Dict[str, pd.DataFrame] = {}
        
        # Regime filter
        self.regime_filter = RegimeFilter()
    
    def set_higher_tf_bars(self, bars_by_tf: Dict[str, pd.DataFrame]) -> None:
        """Set higher timeframe bars for MTF confirmation."""
        self._pending_bars_by_tf = bars_by_tf
    
    def get_name(self) -> str:
        return "zscore_mean_reversion"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate mean reversion signal based on Z-score.
        """
        if not self.is_enabled():
            return None
        
        if len(bars) < self.zscore_lookback + 2:
            self._log_no_signal("Insufficient data")
            return None
        
        # Check regime
        regime = self.regime_filter.classify(bars)
        
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None
        
        # Calculate Z-score
        zscore = Indicators.zscore(bars, period=self.zscore_lookback)
        current_zscore = zscore.iloc[-1]
        
        if pd.isna(current_zscore):
            self._log_no_signal("Z-score calculation failed")
            return None
        
        # Calculate reference prices for stop/target
        current_close = bars['close'].iloc[-1]
        rolling_mean = bars['close'].rolling(window=self.zscore_lookback).mean().iloc[-1]
        rolling_std = bars['close'].rolling(window=self.zscore_lookback).std().iloc[-1]
        
        # Oversold - Buy signal
        if current_zscore < -self.entry_threshold:
            # Stop loss at -3 standard deviations
            stop_loss = rolling_mean - (self.stop_threshold * rolling_std)
            # Take profit at mean
            take_profit = rolling_mean
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(abs(current_zscore) / 3, 1.0),  # Normalize 0-1
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'zscore': float(current_zscore),
                    'mean': float(rolling_mean),
                    'std': float(rolling_std),
                    'entry_reason': 'oversold'
                }
            )
        
        # Overbought - Sell signal
        if current_zscore > self.entry_threshold:
            # Stop loss at +3 standard deviations
            stop_loss = rolling_mean + (self.stop_threshold * rolling_std)
            # Take profit at mean
            take_profit = rolling_mean
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(abs(current_zscore) / 3, 1.0),
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'zscore': float(current_zscore),
                    'mean': float(rolling_mean),
                    'std': float(rolling_std),
                    'entry_reason': 'overbought'
                }
            )
        
        # Z-score within normal range
        self._log_no_signal(f"Z-score {current_zscore:.2f} within threshold ±{self.entry_threshold}")
        return None
