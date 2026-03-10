"""
Realized Volatility — Regime detection via rolling log-return standard deviation.

RV_t = sqrt( Σ (ln(P_t / P_{t-1}))² )   over a rolling window

Regime classification:
    RV > MA(RV)  →  1 (Trend / high-vol)
    RV ≤ MA(RV)  →  0 (Range / low-vol)
"""

import numpy as np
import pandas as pd


def realized_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """
    Compute rolling realized volatility from log returns.

    Args:
        close: Close price series.
        window: Rolling window length.

    Returns:
        pd.Series of RV values (same index as input, leading NaNs).
    """
    log_ret = np.log(close / close.shift(1))
    rv = log_ret.rolling(window).std()
    return rv.rename("realized_vol")


def classify_regime(
    close: pd.Series,
    rv_window: int = 20,
    rv_ma_window: int = 100,
) -> pd.Series:
    """
    Classify each bar into trend (1) or range (0) based on realized volatility.

    Args:
        close: Close price series.
        rv_window: Window for realized volatility.
        rv_ma_window: Window for the RV moving average threshold.

    Returns:
        pd.Series of regime labels (1 = trend, 0 = range).
    """
    rv = realized_volatility(close, window=rv_window)
    rv_mean = rv.rolling(rv_ma_window).mean()
    regime = (rv > rv_mean).astype(int)
    return regime.rename("regime")
