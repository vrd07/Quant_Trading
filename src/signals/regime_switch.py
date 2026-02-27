"""
Regime-Switch Signal Generator.

Combines Kalman filter trend, realized-volatility regime, and OU z-score
into a unified trading signal.

Signal logic (from Instruct.md):

Trend Mode  (regime == 1):
    +1  if Close > Kalman   (long)
    -1  if Close < Kalman   (short)

Range Mode  (regime == 0):
    +1  if Z-score < -2     (oversold → long)
    -1  if Z-score >  2     (overbought → short)
     0  otherwise           (no trade)
"""

import numpy as np
import pandas as pd

from ..indicators.kalman import KalmanFilter
from ..indicators.volatility import realized_volatility, classify_regime
from ..indicators.ou_model import ou_zscore


def generate_signals(
    df: pd.DataFrame,
    kalman_q: float = 1e-5,
    kalman_r: float = 0.01,
    rv_window: int = 20,
    rv_ma_window: int = 100,
    zscore_window: int = 20,
    zscore_entry: float = 2.0,
    close_col: str = "Close",
) -> pd.DataFrame:
    """
    Generate regime-switching signals for a price DataFrame.

    Args:
        df: DataFrame with at least a close price column.
        kalman_q: Kalman process noise.
        kalman_r: Kalman measurement noise.
        rv_window: Realized volatility rolling window.
        rv_ma_window: RV moving-average window for regime threshold.
        zscore_window: Rolling window for OU z-score std dev.
        zscore_entry: Absolute z-score threshold for range-mode entry.
        close_col: Name of the close price column.

    Returns:
        DataFrame with added columns:
            kalman, realized_vol, regime, ou_zscore, signal
    """
    close = df[close_col].copy()

    # 1. Kalman filter trend
    kf = KalmanFilter(q=kalman_q, r=kalman_r)
    kalman = kf.filter(close)

    # 2. Realized volatility & regime
    rv = realized_volatility(close, window=rv_window)
    regime = classify_regime(close, rv_window=rv_window, rv_ma_window=rv_ma_window)

    # 3. OU Z-score (deviation from Kalman trend)
    kalman_series = pd.Series(kalman, index=close.index, name="kalman")
    zscore = ou_zscore(close, kalman_series, window=zscore_window)

    # 4. Signal generation
    signal = pd.Series(0, index=close.index, dtype=int, name="signal")

    # Trend mode
    trend_mask = regime == 1
    signal.loc[trend_mask & (close > kalman_series)] = 1
    signal.loc[trend_mask & (close < kalman_series)] = -1

    # Range mode
    range_mask = regime == 0
    signal.loc[range_mask & (zscore < -zscore_entry)] = 1
    signal.loc[range_mask & (zscore > zscore_entry)] = -1

    # Assemble output
    result = df.copy()
    result["kalman"] = kalman
    result["realized_vol"] = rv.values
    result["regime"] = regime.values
    result["ou_zscore"] = zscore.values
    result["signal"] = signal.values

    return result
