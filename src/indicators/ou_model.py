"""
Ornstein-Uhlenbeck Mean-Reversion Model.

The continuous-time OU process:
    dX_t = θ(μ − X_t) dt + σ dW_t

Parameters:
    θ  — speed of mean reversion
    μ  — long-run mean
    σ  — volatility of the process

We estimate θ via lag-1 OLS regression of Δx on x, then derive:
    β₁ (slope)  ≈  -θ Δt
    θ  = -β₁ / Δt
    half-life   = ln(2) / θ

The Z-score is the normalised deviation from the estimated mean:
    Z_t = (P_t − μ_t) / σ_t
"""

import numpy as np
import pandas as pd
from typing import Tuple


def fit_ou(
    prices: pd.Series,
    window: int = 100,
) -> Tuple[float, float, float]:
    """
    Calibrate Ornstein-Uhlenbeck parameters on the last *window* prices.

    Uses discrete-time OLS:
        ΔX_t = α + β X_{t-1}  →  θ = -β, μ = -α/β

    Args:
        prices: Price series.
        window: Number of recent observations to use.

    Returns:
        (theta, mu, sigma) — OU parameters.
          theta: mean-reversion speed (> 0 for mean-reverting)
          mu:    long-run equilibrium level
          sigma: process volatility
    """
    series = prices.iloc[-window:].values.astype(float)
    if len(series) < 3:
        return 0.0, float(series[-1]), 0.0

    x = series[:-1]
    dx = np.diff(series)

    # OLS: dx = alpha + beta * x
    A = np.column_stack([np.ones_like(x), x])
    result, _, _, _ = np.linalg.lstsq(A, dx, rcond=None)
    alpha, beta = result

    # θ = -β  (assuming Δt = 1)
    theta = max(-beta, 1e-10)  # clamp to positive
    mu = -alpha / beta if abs(beta) > 1e-12 else float(np.mean(series))
    sigma = float(np.std(dx - (alpha + beta * x)))

    return theta, mu, sigma


def ou_half_life(theta: float) -> float:
    """Half-life of mean reversion: ln(2) / θ."""
    if theta <= 0:
        return float("inf")
    return np.log(2) / theta


def ou_zscore(
    prices: pd.Series,
    reference: pd.Series,
    window: int = 20,
) -> pd.Series:
    """
    Z-score of price deviation from a reference level (e.g. Kalman trend).

    Z_t = (P_t − ref_t) / σ_t

    Args:
        prices: Raw price series.
        reference: Reference / mean series (Kalman, VWAP, etc.).
        window: Lookback for rolling standard deviation.

    Returns:
        pd.Series of Z-score values.
    """
    deviation = prices - reference
    rolling_std = deviation.rolling(window).std()
    zscore = deviation / rolling_std.replace(0, np.nan)
    return zscore.rename("ou_zscore")
