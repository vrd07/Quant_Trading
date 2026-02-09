"""
Regime Filter - Classify market as TREND or RANGE.

Uses:
- ADX: Trend strength indicator
- ATR: Volatility measurement
- Hurst Exponent: Trending vs mean-reverting detection

Enhanced Logic:
- ADX > threshold + Hurst > 0.5 → TREND (confirmed persistent)
- ADX < threshold + Hurst < 0.5 → RANGE (confirmed mean-reverting)
- Conflicting signals → UNKNOWN

This is used by other strategies to choose appropriate tactics.
"""

from typing import Optional
import pandas as pd

from ..core.constants import MarketRegime
from ..data.indicators import Indicators


class RegimeFilter:
    """
    Enhanced market regime classifier with Hurst Exponent support.
    
    Determines if market is trending or ranging to help strategies
    choose appropriate entry logic.
    """
    
    def __init__(
        self,
        adx_period: int = 14,
        adx_trend_threshold: float = 25,
        adx_range_threshold: float = 20,
        atr_period: int = 14,
        atr_ma_period: int = 20,
        use_hurst: bool = True,
        hurst_period: int = 100,
        hurst_trend_threshold: float = 0.55,
        hurst_range_threshold: float = 0.45
    ):
        """
        Initialize regime filter.
        
        Args:
            adx_period: Period for ADX calculation
            adx_trend_threshold: ADX above this = trending
            adx_range_threshold: ADX below this = ranging
            atr_period: Period for ATR calculation
            atr_ma_period: Period for ATR moving average
            use_hurst: Whether to use Hurst exponent for confirmation
            hurst_period: Period for Hurst calculation
            hurst_trend_threshold: Hurst above this = trending
            hurst_range_threshold: Hurst below this = ranging
        """
        self.adx_period = adx_period
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_range_threshold = adx_range_threshold
        self.atr_period = atr_period
        self.atr_ma_period = atr_ma_period
        self.use_hurst = use_hurst
        self.hurst_period = hurst_period
        self.hurst_trend_threshold = hurst_trend_threshold
        self.hurst_range_threshold = hurst_range_threshold
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def classify(self, bars: pd.DataFrame) -> MarketRegime:
        """
        Classify current market regime using ADX, ATR, and optionally Hurst.
        
        Args:
            bars: OHLCV DataFrame
        
        Returns:
            MarketRegime (TREND, RANGE, or UNKNOWN)
        """
        min_required = max(self.adx_period, self.atr_ma_period) + 1
        if len(bars) < min_required:
            self.logger.debug("Insufficient data for regime classification")
            return MarketRegime.UNKNOWN
        
        # Calculate ADX
        adx = Indicators.adx(bars, period=self.adx_period)
        current_adx = adx.iloc[-1]
        
        # Calculate ATR and its moving average
        atr = Indicators.atr(bars, period=self.atr_period)
        atr_ma = atr.rolling(window=self.atr_ma_period).mean()
        
        current_atr = atr.iloc[-1]
        current_atr_ma = atr_ma.iloc[-1]
        
        # Check if ATR is rising (volatility increasing)
        atr_rising = current_atr > current_atr_ma
        
        # Calculate Hurst exponent if enabled and enough data
        current_hurst = None
        hurst_trend = None
        hurst_range = None
        
        if self.use_hurst and len(bars) >= self.hurst_period:
            hurst = Indicators.hurst_exponent(bars, period=self.hurst_period)
            current_hurst = hurst.iloc[-1]
            
            if not pd.isna(current_hurst):
                hurst_trend = current_hurst > self.hurst_trend_threshold
                hurst_range = current_hurst < self.hurst_range_threshold
        
        # Classification logic
        if pd.isna(current_adx) or pd.isna(current_atr):
            regime = MarketRegime.UNKNOWN
        else:
            adx_trend = current_adx > self.adx_trend_threshold
            adx_range = current_adx < self.adx_range_threshold
            
            if self.use_hurst and current_hurst is not None:
                # Enhanced classification with Hurst confirmation
                if adx_trend and atr_rising and hurst_trend:
                    regime = MarketRegime.TREND
                elif adx_range and not atr_rising and hurst_range:
                    regime = MarketRegime.RANGE
                elif adx_trend and hurst_trend:
                    # ADX + Hurst agree on trend
                    regime = MarketRegime.TREND
                elif adx_range and hurst_range:
                    # ADX + Hurst agree on range
                    regime = MarketRegime.RANGE
                else:
                    regime = MarketRegime.UNKNOWN
            else:
                # Original logic without Hurst
                if adx_trend and atr_rising:
                    regime = MarketRegime.TREND
                elif adx_range and not atr_rising:
                    regime = MarketRegime.RANGE
                else:
                    regime = MarketRegime.UNKNOWN
        
        self.logger.debug(
            f"Regime classified",
            regime=regime.value,
            adx=float(current_adx) if not pd.isna(current_adx) else None,
            atr=float(current_atr) if not pd.isna(current_atr) else None,
            hurst=float(current_hurst) if current_hurst is not None else None,
            atr_rising=atr_rising
        )
        
        return regime
    
    def get_regime_metrics(self, bars: pd.DataFrame) -> dict:
        """
        Get detailed regime metrics for analysis.
        
        Returns:
            Dict with ADX, ATR, Hurst, and regime classification
        """
        regime = self.classify(bars)
        
        adx = Indicators.adx(bars, period=self.adx_period)
        atr = Indicators.atr(bars, period=self.atr_period)
        
        metrics = {
            'regime': regime.value,
            'adx': float(adx.iloc[-1]) if not adx.empty and not pd.isna(adx.iloc[-1]) else None,
            'atr': float(atr.iloc[-1]) if not atr.empty and not pd.isna(atr.iloc[-1]) else None,
            'adx_threshold_trend': self.adx_trend_threshold,
            'adx_threshold_range': self.adx_range_threshold
        }
        
        # Add Hurst if enabled and available
        if self.use_hurst and len(bars) >= self.hurst_period:
            hurst = Indicators.hurst_exponent(bars, period=self.hurst_period)
            hurst_val = hurst.iloc[-1]
            metrics['hurst'] = float(hurst_val) if not pd.isna(hurst_val) else None
            metrics['hurst_trend_threshold'] = self.hurst_trend_threshold
            metrics['hurst_range_threshold'] = self.hurst_range_threshold
        
        return metrics

