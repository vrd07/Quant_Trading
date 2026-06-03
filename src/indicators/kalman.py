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


class LocalTrendKalman:
    """
    Two-state local-linear-trend Kalman filter (a.k.a. constant-velocity model).

    Unlike the scalar ``KalmanFilter`` above — which only smooths the level and
    has no notion of direction — this filter tracks BOTH a smoothed price level
    and its per-bar velocity (slope). The velocity is the trend signal; its sign
    and magnitude tell you where the filter believes price is heading, with far
    less lag than a moving-average slope.

    State / model (dt = 1 bar):
        x      = [level, velocity]ᵀ
        F      = [[1, 1],            level_t = level_{t-1} + velocity_{t-1}
                  [0, 1]]            velocity follows a random walk
        H      = [1, 0]             we observe the level (close) only
        Q      = σ_a² · [[1/4, 1/2],  discrete white-noise-acceleration cov
                         [1/2,  1 ]]
        R      = measurement variance

    Adaptivity: both σ_a² (process) and R (measurement) are scaled by ATR² so the
    filter is *scale-invariant* — it behaves identically whether gold trades at
    2,700 or 4,600 — and becomes more responsive when volatility rises. The Q/R
    ratio (``process_scale`` / ``measurement_scale``) is the single tuning knob
    that trades responsiveness against smoothness.

    Outputs (all aligned to the input index):
        level   — filtered price level
        velocity— filtered per-bar slope (price units / bar)
        innov_z — standardized one-step innovation  (y_t − level̂⁻) / √S_t,
                  i.e. how surprising the latest close is vs the filter's
                  prediction, in std-dev units. Used for mean-reversion entries.
    """

    def __init__(self, process_scale: float = 1e-3, measurement_scale: float = 1.0):
        if process_scale <= 0 or measurement_scale <= 0:
            raise ValueError("process_scale and measurement_scale must be positive")
        self.process_scale = float(process_scale)
        self.measurement_scale = float(measurement_scale)

    def filter(self, prices: Union[pd.Series, np.ndarray],
               atr: Union[pd.Series, np.ndarray]) -> dict:
        """
        Run the two-state filter.

        Args:
            prices: observed close prices.
            atr:    per-bar ATR (same length) used to scale Q and R. Non-positive
                    or NaN ATR falls back to a tiny floor so the filter never divides
                    by zero on warm-up bars.

        Returns:
            dict with numpy arrays: ``level``, ``velocity``, ``innov_z``.
        """
        y = (prices.values if isinstance(prices, pd.Series) else np.asarray(prices)).astype(float)
        a = (atr.values if isinstance(atr, pd.Series) else np.asarray(atr)).astype(float)
        n = len(y)
        level = np.full(n, np.nan)
        velocity = np.full(n, np.nan)
        innov_z = np.zeros(n)
        if n == 0:
            return {"level": level, "velocity": velocity, "innov_z": innov_z}

        # State [x0=level, x1=velocity] and covariance P (symmetric: p01==p10).
        # The 2-state local-linear-trend filter is unrolled into plain scalars so
        # the hot loop has ZERO per-step numpy allocation — this matters because
        # the backtest re-runs the whole filter on a rolling window every bar.
        # Model:  F=[[1,1],[0,1]]  H=[1,0]  Q=σ²·[[.25,.5],[.5,1]]  R=σ²·meas
        x0, x1 = y[0], 0.0
        p00, p01, p11 = 1.0, 0.0, 1.0
        qs = self.process_scale
        ms = self.measurement_scale
        level[0], velocity[0] = x0, x1
        sqrt = np.sqrt
        for k in range(1, n):
            ak = a[k]
            var = ak * ak if (ak > 0 and ak == ak) else 1e-12  # ak==ak filters NaN
            q00 = qs * var * 0.25
            q01 = qs * var * 0.5
            q11 = qs * var
            r = ms * var

            # Predict:  x = F x ;  P = F P Fᵀ + Q
            x0 = x0 + x1                      # level += velocity
            # F P Fᵀ for F=[[1,1],[0,1]]:
            #   p00' = p00 + 2 p01 + p11 ; p01' = p01 + p11 ; p11' = p11
            p00 = p00 + 2.0 * p01 + p11 + q00
            p01 = p01 + p11 + q01
            p11 = p11 + q11

            # Update:  innovation e = y - level ; S = p00 + r
            e = y[k] - x0
            S = p00 + r
            innov_z[k] = e / sqrt(S) if S > 0 else 0.0
            k0 = p00 / S      # Kalman gain (level)
            k1 = p01 / S      # Kalman gain (velocity)
            x0 = x0 + k0 * e
            x1 = x1 + k1 * e
            # P = (I - K H) P, with H=[1,0]:
            #   p00*=(1-k0); p01*=(1-k0); p11 -= k1 p01_old
            p11 = p11 - k1 * p01
            p00 = (1.0 - k0) * p00
            p01 = (1.0 - k0) * p01

            level[k], velocity[k] = x0, x1

        return {"level": level, "velocity": velocity, "innov_z": innov_z}

    def filter_frame(self, prices: pd.Series, atr: pd.Series) -> pd.DataFrame:
        """Convenience wrapper returning a DataFrame aligned to ``prices.index``."""
        out = self.filter(prices, atr)
        return pd.DataFrame(out, index=prices.index)
