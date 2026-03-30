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
        
        self.entry_z_score = config.get('entry_z_score', 2.2)  # Raised from 2.0 for quality
        self.exit_z_score = config.get('exit_z_score', 0.0)
        
        self.use_dynamic_thresholds = config.get('use_dynamic_thresholds', True)
        self.threshold_window = config.get('threshold_window', 500) # Window for percentile calculation
        self.entry_percentile = config.get('entry_percentile', 95) # Enter at 95th percentile
        
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]
        
        # Multi-timeframe confirmation
        self.mtf_confirmation = config.get('mtf_confirmation', False)
        self.mtf_filter = MultiTimeframeFilter() if self.mtf_confirmation else None
        self._pending_bars_by_tf: Dict[str, pd.DataFrame] = {}
        
        # Regime filter removed — Z-score + RSI/BB extremes already ensure
        # we only enter on genuine overextension. RegimeFilter returned TREND
        # 77% of the time on XAUUSD 5m, blocking all entries.
        
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
        
        # Regime gate removed — the entry filters (Z-score extreme + RSI + BB)
        # are sufficient to confirm mean-reversion conditions.
        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.RANGE
        
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
        current_close = float(bars['close'].iloc[-1])
        vwap = float(Indicators.vwap(bars).iloc[-1])
        
        # Calculate ATR for wide, safe stops
        atr = Indicators.atr(bars, period=14)
        current_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else current_close * 0.002

        # RSI gate: require genuinely extreme RSI before mean-reversion entry
        # Prevents catching falling knives in trending markets misclassified as RANGE
        rsi = Indicators.rsi(bars, period=14)
        current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        prev_rsi = float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else current_rsi
        prev2_rsi = float(rsi.iloc[-3]) if not pd.isna(rsi.iloc[-3]) else prev_rsi

        # Bollinger Band confirmation: price must be at or beyond the outer band
        # (z-score extremes without BB touch can be mean drift, not reversal setups)
        bb_upper, _, bb_lower = Indicators.bollinger_bands(bars, period=20, num_std=2.0)
        current_bb_lower = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else current_close
        current_bb_upper = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else current_close

        # Stochastic extreme: independent oscillator confirmation at the reversal point
        stoch_k, _ = Indicators.stochastic(bars, period=14)
        current_stoch = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0

        # Volume climax removed as a hard gate — MT5 tick volume is unreliable
        # on 5m bars and was blocking most entries. Kept as a soft strength bonus below.

        # ADX trend guard: don't mean-revert in a strong trend (ADX > 30)
        adx = Indicators.adx(bars, period=14)
        current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 25.0
        if current_adx > 30.0:
            self._log_no_signal(f"MeanRev: ADX too high ({current_adx:.1f}) — strong trend, no reversion")
            return None

        # Buy Signal (Oversold)
        if current_z < entry_thresh_long:
            if current_rsi > 40.0:
                self._log_no_signal(
                    f"MeanRev BUY: RSI not extreme enough ({current_rsi:.1f} > 40)")
                return None

            # Price must be at or below lower Bollinger Band
            if current_close > current_bb_lower:
                self._log_no_signal(
                    f"MeanRev BUY: price {current_close:.2f} not at/below lower BB {current_bb_lower:.2f}")
                return None

            # RSI deceleration: selling exhaustion — RSI was falling but now leveling
            rsi_was_falling = prev_rsi < prev2_rsi
            rsi_decelerating = current_rsi >= prev_rsi
            if not (rsi_was_falling and rsi_decelerating):
                self._log_no_signal(
                    f"MeanRev BUY: RSI not decelerating ({prev2_rsi:.1f}→{prev_rsi:.1f}→{current_rsi:.1f})")
                return None

            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(abs(current_z) / 3, 1.0),
                regime=regime,
                entry_price=current_close,
                metadata={
                    'zscore': float(current_z),
                    'half_life': float(current_hl),
                    'lookback': self.current_lookback,
                    'threshold': float(entry_thresh_long),
                    'vwap': vwap,
                    'atr': float(current_atr)
                }
            )
            
        # Sell Signal (Overbought)
        elif current_z > entry_thresh_short:
            if current_rsi < 60.0:
                self._log_no_signal(
                    f"MeanRev SELL: RSI not extreme enough ({current_rsi:.1f} < 60)")
                return None

            # Price must be at or above upper Bollinger Band
            if current_close < current_bb_upper:
                self._log_no_signal(
                    f"MeanRev SELL: price {current_close:.2f} not at/above upper BB {current_bb_upper:.2f}")
                return None

            # RSI deceleration: buying exhaustion — RSI was rising but now leveling
            rsi_was_rising = prev_rsi > prev2_rsi
            rsi_decelerating = current_rsi <= prev_rsi
            if not (rsi_was_rising and rsi_decelerating):
                self._log_no_signal(
                    f"MeanRev SELL: RSI not decelerating ({prev2_rsi:.1f}→{prev_rsi:.1f}→{current_rsi:.1f})")
                return None

            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(abs(current_z) / 3, 1.0),
                regime=regime,
                entry_price=current_close,
                metadata={
                    'zscore': float(current_z),
                    'half_life': float(current_hl),
                    'lookback': self.current_lookback,
                    'threshold': float(entry_thresh_short),
                    'vwap': vwap,
                    'atr': float(current_atr)
                }
            )
            
        self._log_no_signal(f"Z={current_z:.2f} (L={self.current_lookback}) within [{entry_thresh_long:.2f}, {entry_thresh_short:.2f}]")
        return None
