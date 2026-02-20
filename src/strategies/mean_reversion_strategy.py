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
    """
    Z-score mean reversion strategy with dynamic lookback and VWAP anchoring.
    
    Improvements:
    - Uses VWAP instead of simple Moving Average for "fair value"
    - Dynamic lookback based on Half-Life of mean reversion
    - Dynamic entry/exit thresholds based on recent volatility (z-score distribution)
    """
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Strategy parameters
        self.min_lookback = config.get('min_lookback', 10)
        self.max_lookback = config.get('max_lookback', 100)
        self.lookback_multiplier = config.get('lookback_multiplier', 1.0) # Multiply half-life by this
        
        self.entry_z_score = config.get('entry_z_score', 2.0) # Fallback if dynamic fails
        self.exit_z_score = config.get('exit_z_score', 0.0)
        
        self.use_dynamic_thresholds = config.get('use_dynamic_thresholds', True)
        self.threshold_window = config.get('threshold_window', 500) # Window for percentile calculation
        self.entry_percentile = config.get('entry_percentile', 95) # Enter at 95th percentile
        
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]
        
        # Multi-timeframe confirmation
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None
        self._pending_bars_by_tf: Dict[str, pd.DataFrame] = {}
        
        # Regime filter
        self.regime_filter = RegimeFilter()
        
        # State
        self.current_lookback = 20 # Initial default
        self.current_half_life = 0.0

    def set_higher_tf_bars(self, bars_by_tf: Dict[str, pd.DataFrame]) -> None:
        """Set higher timeframe bars for MTF confirmation."""
        self._pending_bars_by_tf = bars_by_tf
    
    def get_name(self) -> str:
        return "zscore_mean_reversion"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate mean reversion signal based on dynamic Z-score and VWAP.
        """
        if not self.is_enabled():
            return None
        
        # We need enough data for the maximum possible lookback + half-life calc
        min_required = max(self.max_lookback, 100) + 10
        if len(bars) < min_required:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {min_required}")
            return None
        
        # 1. Calculate Market Regime
        regime = self.regime_filter.classify(bars)
        
        # Filter by regime (optional, can be relaxed in config)
        if self.only_in_regime and regime != self.only_in_regime:
            # Allow trading if regime is UNKNOWN but specific conditions met?
            # For now, stick to user config but typically we want RANGE
            self._log_no_signal(f"Regime {regime.value} != {self.only_in_regime.value}")
            return None
        
        # 2. Calculate Half-Life
        half_life_series = Indicators.half_life(bars, period=100)
        current_hl = half_life_series.iloc[-1]
        
        if pd.isna(current_hl) or current_hl <= 0:
            current_hl = 20 # Fallback
        
        self.current_half_life = current_hl
        
        # 3. Determine Dynamic Lookback
        # Lookback ≈ Half-Life * Multiplier (e.g. 1-2x half life to capture the reversion)
        raw_lookback = int(current_hl * self.lookback_multiplier)
        self.current_lookback = max(self.min_lookback, min(self.max_lookback, raw_lookback))
        
        # 4. Calculate VWAP Z-Score with Dynamic Lookback
        zscore = Indicators.zscore_vwap(bars, period=self.current_lookback)
        current_z = zscore.iloc[-1]
        
        if pd.isna(current_z):
            self._log_no_signal("Z-score calculation failed")
            return None
            
        # 5. Determine Thresholds
        entry_thresh_long = -self.entry_z_score
        entry_thresh_short = self.entry_z_score
        
        if self.use_dynamic_thresholds and len(zscore) >= self.threshold_window:
            # Calculate percentiles over rolling window
            recent_z = zscore.iloc[-self.threshold_window:]
            entry_thresh_short = recent_z.quantile(self.entry_percentile / 100.0)
            entry_thresh_long = recent_z.quantile((100 - self.entry_percentile) / 100.0)
            
            # Safety clamp: Don't let thresholds get too tight
            entry_thresh_short = max(entry_thresh_short, 1.0)
            entry_thresh_long = min(entry_thresh_long, -1.0)

        # 6. Signal Generation
        current_close = bars['close'].iloc[-1]
        vwap = Indicators.vwap(bars).iloc[-1]
        std = bars['close'].rolling(window=self.current_lookback).std().iloc[-1]
        
        # Buy Signal (Oversold)
        if current_z < entry_thresh_long:
            # Target: VWAP
            take_profit = vwap
            # Stop: 3 std devs or recent low
            stop_loss = current_close - (3 * std)
            
            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(abs(current_z) / 3, 1.0),
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'zscore': float(current_z),
                    'half_life': float(current_hl),
                    'lookback': self.current_lookback,
                    'threshold': float(entry_thresh_long),
                    'vwap': float(vwap)
                }
            )
            
        # Sell Signal (Overbought)
        elif current_z > entry_thresh_short:
            # Target: VWAP
            take_profit = vwap
            # Stop: 3 std devs
            stop_loss = current_close + (3 * std)
            
            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(abs(current_z) / 3, 1.0),
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'zscore': float(current_z),
                    'half_life': float(current_hl),
                    'lookback': self.current_lookback,
                    'threshold': float(entry_thresh_short),
                    'vwap': float(vwap)
                }
            )
            
        self._log_no_signal(f"Z={current_z:.2f} (L={self.current_lookback}) within [{entry_thresh_long:.2f}, {entry_thresh_short:.2f}]")
        return None
