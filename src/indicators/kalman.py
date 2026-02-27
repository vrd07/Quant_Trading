"""
Kalman Filter — Adaptive trend extraction for price series.

State model:
    x_t = x_{t-1} + ε_t          (random walk state)
    y_t = x_t + η_t              (noisy observation)

Recursive update:
    Prediction:   x̂_{t|t-1} = x̂_{t-1}
    Gain:         K_t = P_t⁻ / (P_t⁻ + R)
    Update:       x̂_t = x̂_{t|t-1} + K_t (y_t - x̂_{t|t-1})
    Covariance:   P_t = (1 - K_t) P_t⁻

Parameters:
    q  — process noise covariance  (higher → more responsive)
    r  — measurement noise covariance (higher → smoother)
"""

import numpy as np
import pandas as pd
from typing import Union


class KalmanFilter:
    """Univariate Kalman filter for trend extraction."""

    def __init__(self, q: float = 1e-5, r: float = 0.01):
        """
        Args:
            q: Process noise covariance (state uncertainty per step).
            r: Measurement noise covariance (observation uncertainty).
        """
        if q <= 0 or r <= 0:
            raise ValueError("q and r must be positive")
        self.q = q
        self.r = r

    def filter(self, series: Union[pd.Series, np.ndarray]) -> np.ndarray:
        """
        Run the Kalman filter on a 1-D price series.

        Args:
            series: Price observations (pd.Series or np.ndarray).

        Returns:
            np.ndarray of filtered (smoothed) trend values, same length as input.
        """
        if isinstance(series, pd.Series):
            values = series.values.astype(float)
        else:
            values = np.asarray(series, dtype=float)

        n = len(values)
        if n == 0:
            return np.array([])

        xhat = np.zeros(n)
        P = np.zeros(n)

        # Initialise with first observation
        xhat[0] = values[0]
        P[0] = 1.0

        for k in range(1, n):
            # Predict
            xhat_minus = xhat[k - 1]
            P_minus = P[k - 1] + self.q

            # Update
            K = P_minus / (P_minus + self.r)
            xhat[k] = xhat_minus + K * (values[k] - xhat_minus)
            P[k] = (1 - K) * P_minus

        return xhat

    def filter_series(self, series: pd.Series) -> pd.Series:
        """Convenience wrapper that returns a pd.Series with the same index."""
        result = self.filter(series)
        return pd.Series(result, index=series.index, name="kalman")
