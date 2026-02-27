"""
Kalman Regime-Switching Strategy.

Combines Kalman filter trend, realized-volatility regime detection,
and Ornstein-Uhlenbeck z-scored mean reversion into a single strategy.

Signal Logic (from Instruct.md):

Trend Mode  (RV > MA(RV)):
    Long   if Close > Kalman
    Short  if Close < Kalman

Range Mode  (RV ≤ MA(RV)):
    Long   if OU Z-score < -entry_threshold  (oversold)
    Short  if OU Z-score > +entry_threshold  (overbought)

Risk Management:
    Stop Loss  = 1.5 × ATR(14)
    Take Profit = 3.0 × ATR(14)

Optional:
    - News filter blackout (ForexFactory)
    - Multi-timeframe confirmation
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class KalmanRegimeStrategy(BaseStrategy):
    """
    Regime-switching strategy using Kalman filter + RV regime + OU z-score.
    
    Adapts between trend-following and mean-reversion based on
    realized volatility regime classification.
    """
    
    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)
        
        # Kalman parameters
        self.kalman_q = config.get('kalman_q', 1e-5)
        self.kalman_r = config.get('kalman_r', 0.01)
        
        # Realized volatility regime
        self.rv_window = config.get('rv_window', 20)
        self.rv_ma_window = config.get('rv_ma_window', 100)
        
        # OU z-score thresholds (range mode)
        self.zscore_window = config.get('zscore_window', 20)
        self.entry_threshold = config.get('entry_threshold', 2.0)
        
        # ATR-based risk management
        self.atr_period = config.get('atr_period', 14)
        self.sl_atr_mult = config.get('sl_atr_multiplier', 1.5)
        self.tp_atr_mult = config.get('tp_atr_multiplier', 3.0)
        
        # News filter (optional)
        self.news_filter_enabled = config.get('news_filter', False)
        self._news_events = None
        
        # Minimum data required
        self.min_bars = max(self.rv_ma_window, 100) + self.rv_window + 10
    
    def get_name(self) -> str:
        return "kalman_regime"
    
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """Generate regime-switching signal."""
        if not self.is_enabled():
            return None
        
        if len(bars) < self.min_bars:
            self._log_no_signal(f"Insufficient data: {len(bars)} < {self.min_bars}")
            return None
        
        close = bars['close']
        current_close = float(close.iloc[-1])
        
        # 1. Kalman filter trend
        kalman = Indicators.kalman_filter(close, q=self.kalman_q, r=self.kalman_r)
        current_kalman = float(kalman.iloc[-1])
        
        # 2. Realized volatility regime
        regime_series = Indicators.rv_regime(
            close, rv_window=self.rv_window, rv_ma_window=self.rv_ma_window
        )
        current_regime_val = int(regime_series.iloc[-1]) if not pd.isna(regime_series.iloc[-1]) else -1
        
        if current_regime_val == -1:
            self._log_no_signal("Regime classification unavailable (NaN)")
            return None
        
        is_trend = current_regime_val == 1
        regime = MarketRegime.TREND if is_trend else MarketRegime.RANGE
        
        # 3. OU z-score (for range mode)
        zscore = Indicators.ou_zscore(close, kalman, window=self.zscore_window)
        current_z = float(zscore.iloc[-1]) if not pd.isna(zscore.iloc[-1]) else 0.0
        
        # 4. ATR for stop/take-profit
        atr = Indicators.atr(bars, period=self.atr_period)
        current_atr = float(atr.iloc[-1])
        if current_atr <= 0 or pd.isna(current_atr):
            self._log_no_signal("ATR unavailable")
            return None
        
        stop_distance = self.sl_atr_mult * current_atr
        tp_distance = self.tp_atr_mult * current_atr
        
        # 5. Signal generation
        side = None
        strength = 0.0
        
        if is_trend:
            # Trend mode: follow Kalman direction
            if current_close > current_kalman:
                side = OrderSide.BUY
                strength = min(abs(current_close - current_kalman) / current_atr, 1.0)
            elif current_close < current_kalman:
                side = OrderSide.SELL
                strength = min(abs(current_close - current_kalman) / current_atr, 1.0)
        else:
            # Range mode: mean reversion on z-score
            if current_z < -self.entry_threshold:
                side = OrderSide.BUY
                strength = min(abs(current_z) / 3.0, 1.0)
            elif current_z > self.entry_threshold:
                side = OrderSide.SELL
                strength = min(abs(current_z) / 3.0, 1.0)
        
        if side is None:
            mode_str = "TREND" if is_trend else "RANGE"
            self._log_no_signal(
                f"No signal in {mode_str} mode "
                f"(close={current_close:.2f}, kalman={current_kalman:.2f}, z={current_z:.2f})"
            )
            return None
        
        # Compute SL / TP
        if side == OrderSide.BUY:
            stop_loss = current_close - stop_distance
            take_profit = current_close + tp_distance
        else:
            stop_loss = current_close + stop_distance
            take_profit = current_close - tp_distance
        
        return self._create_signal(
            side=side,
            strength=strength,
            regime=regime,
            entry_price=current_close,
            stop_loss=stop_loss,
            take_profit=take_profit,
            metadata={
                'strategy': 'kalman_regime',
                'mode': 'trend' if is_trend else 'range',
                'kalman': current_kalman,
                'zscore': current_z,
                'atr': current_atr,
                'sl_distance': stop_distance,
                'tp_distance': tp_distance,
            }
        )
