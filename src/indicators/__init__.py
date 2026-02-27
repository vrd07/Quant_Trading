"""
Quantitative indicators for regime-switching strategy.

Modules:
- kalman: Adaptive Kalman filter for trend extraction
- volatility: Realized volatility regime detection
- ou_model: Ornstein-Uhlenbeck mean-reversion model
"""

from .kalman import KalmanFilter
from .volatility import realized_volatility, classify_regime
from .ou_model import fit_ou, ou_zscore
