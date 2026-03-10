"""
Kelly Criterion Position Sizing.

Kelly fraction:
    f* = (b·p − q) / b

where:
    p = win probability
    q = 1 − p
    b = average win / average loss   (payoff ratio)

We use half-Kelly (f* × 0.5) by default to reduce variance.

Also provides fixed-fractional ATR-based sizing as a convenience.
"""

import numpy as np
from typing import Optional


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5,
    max_fraction: float = 0.25,
) -> float:
    """
    Compute the (fractional) Kelly bet size.

    Args:
        win_rate: Probability of winning (0–1).
        avg_win: Average winning trade return (positive).
        avg_loss: Average losing trade return (positive magnitude).
        fraction: Kelly fraction to use (0.5 = half-Kelly).
        max_fraction: Hard cap on the resulting fraction.

    Returns:
        Position size as fraction of equity (0 to max_fraction).
    """
    if avg_loss <= 0 or avg_win <= 0 or not (0 < win_rate < 1):
        return 0.0

    b = avg_win / avg_loss  # payoff ratio
    p = win_rate
    q = 1 - p

    kelly_f = (b * p - q) / b

    # Apply fractional Kelly
    result = kelly_f * fraction

    # Clamp
    return float(np.clip(result, 0.0, max_fraction))


def fixed_fractional(
    equity: float,
    risk_pct: float,
    atr: float,
    atr_multiplier: float = 1.5,
    value_per_lot: float = 100.0,
) -> float:
    """
    Fixed-fractional position sizing using ATR stop distance.

    Size = (Equity × Risk%) / (ATR × multiplier × value_per_lot)

    Args:
        equity: Current account equity.
        risk_pct: Fraction of equity to risk per trade (e.g. 0.01 = 1%).
        atr: Current ATR value.
        atr_multiplier: Multiplier for ATR stop distance.
        value_per_lot: Dollar value per standard lot (100 oz for gold).

    Returns:
        Position size in lots.
    """
    stop_distance = atr * atr_multiplier
    if stop_distance <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * risk_pct
    size = risk_amount / (stop_distance * value_per_lot)
    return max(0.0, size)
