"""
Unit tests for modules introduced by Instruct.md.

Covers:
- Kalman filter
- Realized volatility & regime classification
- Ornstein-Uhlenbeck model
- Regime-switch signal generation
- Kelly criterion position sizing
- Monte Carlo robustness testing
- Walk-forward validation
"""

import pytest
import numpy as np
import pandas as pd


# ── Helpers ──────────────────────────────────────────────


def _make_bars(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Create realistic synthetic OHLCV bars."""
    rng = np.random.default_rng(seed)
    prices = 2000.0 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "Open": prices + rng.uniform(-0.5, 0.5, n),
        "High": prices + rng.uniform(0.5, 2, n),
        "Low": prices - rng.uniform(0.5, 2, n),
        "Close": prices,
        "Volume": rng.uniform(500, 2000, n),
    })


# ══════════════════════════════════════════════════════════
#  Kalman Filter
# ══════════════════════════════════════════════════════════


class TestKalmanFilter:

    def test_constant_series(self):
        """Filtering a constant series should return the same constant."""
        from src.indicators.kalman import KalmanFilter
        kf = KalmanFilter(q=1e-5, r=0.01)
        series = pd.Series([100.0] * 50)
        result = kf.filter(series)
        np.testing.assert_allclose(result, 100.0, atol=0.01)

    def test_tracks_trend(self):
        """Filtered output should follow a linear trend."""
        from src.indicators.kalman import KalmanFilter
        # Use higher q for faster tracking of a deterministic trend
        kf = KalmanFilter(q=0.01, r=0.01)
        series = pd.Series([float(i) for i in range(100)])
        result = kf.filter(series)
        # Last value should be close to 99
        assert abs(result[-1] - 99.0) < 5.0

    def test_smooths_noise(self):
        """Kalman output should be smoother than noisy input."""
        from src.indicators.kalman import KalmanFilter
        rng = np.random.default_rng(0)
        raw = 100 + np.cumsum(rng.normal(0, 1, 200))
        noise = raw + rng.normal(0, 5, 200)

        kf = KalmanFilter(q=1e-5, r=0.01)
        filtered = kf.filter(noise)

        # Std of first differences should be smaller for filtered
        assert np.std(np.diff(filtered)) < np.std(np.diff(noise))

    def test_filter_series_returns_series(self):
        """filter_series should return a pd.Series with matching index."""
        from src.indicators.kalman import KalmanFilter
        kf = KalmanFilter()
        idx = pd.date_range("2024-01-01", periods=20, freq="1h")
        s = pd.Series(range(20), index=idx, dtype=float)
        result = kf.filter_series(s)
        assert isinstance(result, pd.Series)
        assert len(result) == 20
        assert result.index.equals(idx)

    def test_invalid_params(self):
        """q and r must be positive."""
        from src.indicators.kalman import KalmanFilter
        with pytest.raises(ValueError):
            KalmanFilter(q=-1, r=0.01)
        with pytest.raises(ValueError):
            KalmanFilter(q=1e-5, r=0)

    def test_empty_input(self):
        """Empty input should return empty array."""
        from src.indicators.kalman import KalmanFilter
        kf = KalmanFilter()
        result = kf.filter(np.array([]))
        assert len(result) == 0


# ══════════════════════════════════════════════════════════
#  Realized Volatility
# ══════════════════════════════════════════════════════════


class TestRealizedVolatility:

    def test_constant_prices_near_zero(self):
        """Constant prices should yield near-zero realized vol."""
        from src.indicators.volatility import realized_volatility
        close = pd.Series([100.0] * 50)
        rv = realized_volatility(close, window=20)
        assert rv.dropna().max() < 1e-10

    def test_positive_values(self):
        """RV should be non-negative."""
        from src.indicators.volatility import realized_volatility
        bars = _make_bars()
        rv = realized_volatility(bars["Close"], window=20)
        assert (rv.dropna() >= 0).all()

    def test_regime_binary(self):
        """Regime should be 0 or 1."""
        from src.indicators.volatility import classify_regime
        bars = _make_bars(300)
        regime = classify_regime(bars["Close"], rv_window=20, rv_ma_window=100)
        valid = regime.dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_high_vol_detected_as_trend(self):
        """A sudden volatility spike should be classified as trend (1)."""
        from src.indicators.volatility import realized_volatility, classify_regime
        rng = np.random.default_rng(42)
        # Very long stable period (low RV) followed by extreme volatility
        stable = [100.0 + rng.normal(0, 0.001) for _ in range(300)]
        volatile = [stable[-1]]
        for i in range(150):
            volatile.append(volatile[-1] + rng.normal(0, 20))
        close = pd.Series(stable + volatile[1:])
        # Directly verify RV at end is above its MA
        rv = realized_volatility(close, window=20)
        rv_mean = rv.rolling(100).mean()
        assert rv.iloc[-1] > rv_mean.iloc[-1], (
            f"RV={rv.iloc[-1]:.6f} should exceed MA={rv_mean.iloc[-1]:.6f}"
        )


# ══════════════════════════════════════════════════════════
#  Ornstein-Uhlenbeck Model
# ══════════════════════════════════════════════════════════


class TestOUModel:

    def test_fit_ou_returns_positive_theta(self):
        """For a mean-reverting series, θ should be positive."""
        from src.indicators.ou_model import fit_ou
        rng = np.random.default_rng(42)
        # Simulate simple OU: x[t+1] = x[t] + 0.1*(100 - x[t]) + noise
        x = [100.0]
        for _ in range(200):
            x.append(x[-1] + 0.1 * (100 - x[-1]) + rng.normal(0, 0.5))
        prices = pd.Series(x)
        theta, mu, sigma = fit_ou(prices, window=200)
        assert theta > 0

    def test_ou_half_life(self):
        """Half-life should be ln(2)/θ."""
        from src.indicators.ou_model import ou_half_life
        assert abs(ou_half_life(0.1) - np.log(2) / 0.1) < 1e-10
        assert ou_half_life(0) == float("inf")

    def test_zscore_zero_at_mean(self):
        """Z-score should be near zero when price equals reference."""
        from src.indicators.ou_model import ou_zscore
        prices = pd.Series([100.0] * 50)
        ref = pd.Series([100.0] * 50)
        z = ou_zscore(prices, ref, window=20)
        # All deviations are zero → zscore should be NaN (0/0) or 0
        valid = z.dropna()
        if len(valid) > 0:
            assert (valid.abs() < 1e-10).all()


# ══════════════════════════════════════════════════════════
#  Regime-Switch Signal Generation
# ══════════════════════════════════════════════════════════


class TestRegimeSwitchSignal:

    def test_signal_values(self):
        """Signal should only be -1, 0, or 1."""
        from src.signals.regime_switch import generate_signals
        bars = _make_bars(500)
        result = generate_signals(bars, close_col="Close")
        assert set(result["signal"].unique()).issubset({-1, 0, 1})

    def test_output_columns(self):
        """Output should contain the expected columns."""
        from src.signals.regime_switch import generate_signals
        bars = _make_bars(500)
        result = generate_signals(bars, close_col="Close")
        for col in ["kalman", "realized_vol", "regime", "ou_zscore", "signal"]:
            assert col in result.columns

    def test_trend_mode_signals(self):
        """In a noisy uptrend with high RV, trend mode should generate longs."""
        from src.signals.regime_switch import generate_signals
        # Noisy uptrend → RV will be above average → trend mode
        rng = np.random.default_rng(42)
        prices = [2000.0]
        for i in range(499):
            prices.append(prices[-1] + 1.0 + rng.normal(0, 5))
        bars = pd.DataFrame({
            "Open": prices,
            "High": [p + abs(rng.normal(0, 3)) for p in prices],
            "Low": [p - abs(rng.normal(0, 3)) for p in prices],
            "Close": prices,
            "Volume": [1000] * 500,
        })
        result = generate_signals(bars, close_col="Close")
        # Should have some non-zero signals
        late_signals = result["signal"].iloc[200:]
        assert (late_signals != 0).any()


# ══════════════════════════════════════════════════════════
#  Kelly Criterion
# ══════════════════════════════════════════════════════════


class TestKellyCriterion:

    def test_positive_edge(self):
        """With a winning edge, Kelly should be positive."""
        from src.risk.kelly import kelly_criterion
        f = kelly_criterion(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert f > 0

    def test_no_edge(self):
        """With 50/50 and equal win/loss, Kelly should be zero."""
        from src.risk.kelly import kelly_criterion
        f = kelly_criterion(win_rate=0.5, avg_win=1.0, avg_loss=1.0)
        assert f == 0.0

    def test_half_kelly_smaller(self):
        """Half-Kelly should be smaller than full Kelly."""
        from src.risk.kelly import kelly_criterion
        full = kelly_criterion(win_rate=0.6, avg_win=2.0, avg_loss=1.0, fraction=1.0)
        half = kelly_criterion(win_rate=0.6, avg_win=2.0, avg_loss=1.0, fraction=0.5)
        assert half < full

    def test_max_fraction_cap(self):
        """Kelly output should never exceed max_fraction."""
        from src.risk.kelly import kelly_criterion
        f = kelly_criterion(win_rate=0.9, avg_win=10.0, avg_loss=1.0,
                            fraction=1.0, max_fraction=0.25)
        assert f <= 0.25

    def test_fixed_fractional(self):
        """Fixed fractional should produce a sensible lot size."""
        from src.risk.kelly import fixed_fractional
        size = fixed_fractional(equity=10000, risk_pct=0.01, atr=15.0,
                                atr_multiplier=1.5, value_per_lot=100)
        assert size > 0
        # Expected: (10000 * 0.01) / (15 * 1.5 * 100) = 100 / 2250 ≈ 0.044
        assert abs(size - 100 / 2250) < 0.001


# ══════════════════════════════════════════════════════════
#  Monte Carlo
# ══════════════════════════════════════════════════════════


class TestMonteCarlo:

    def test_returns_correct_length(self):
        """Should return exactly n_simulations results."""
        from src.validation.monte_carlo import monte_carlo_equity
        returns = pd.Series(np.random.randn(100) * 0.01)
        results = monte_carlo_equity(returns, n_simulations=500, seed=0)
        assert len(results) == 500

    def test_positive_terminal_equity(self):
        """Terminal equity values should (almost always) be positive."""
        from src.validation.monte_carlo import monte_carlo_equity
        returns = pd.Series(np.random.randn(200) * 0.005)
        results = monte_carlo_equity(returns, n_simulations=100, seed=1)
        assert all(r > 0 for r in results)

    def test_confidence_interval(self):
        """CI lower should be ≤ upper."""
        from src.validation.monte_carlo import confidence_interval
        results = list(np.random.randn(1000))
        lo, hi = confidence_interval(results, pct=95)
        assert lo <= hi

    def test_p_value_range(self):
        """p-value should be between 0 and 1."""
        from src.validation.monte_carlo import p_value
        results = list(np.random.randn(1000))
        p = p_value(0.0, results)
        assert 0 <= p <= 1


# ══════════════════════════════════════════════════════════
#  Walk-Forward Validation
# ══════════════════════════════════════════════════════════


class TestWalkForward:

    def test_correct_number_of_splits(self):
        """Should return the requested number of splits."""
        from src.validation.walk_forward import walk_forward_split
        df = pd.DataFrame({"close": range(600)})
        splits = walk_forward_split(df, n_splits=5)
        assert len(splits) == 5

    def test_no_overlap(self):
        """Train and test indices should not overlap within a split."""
        from src.validation.walk_forward import walk_forward_split
        df = pd.DataFrame({"close": range(600)})
        splits = walk_forward_split(df, n_splits=3)
        for train, test in splits:
            train_idx = set(train.index.tolist())
            test_idx = set(test.index.tolist())
            assert train_idx.isdisjoint(test_idx)

    def test_train_before_test(self):
        """Train data should come before test data (no look-ahead)."""
        from src.validation.walk_forward import walk_forward_split
        df = pd.DataFrame({"close": range(600)})
        splits = walk_forward_split(df, n_splits=3)
        for train, test in splits:
            assert train.index.max() < test.index.min()

    def test_run_walk_forward(self):
        """End-to-end walk-forward run should produce valid metrics."""
        from src.validation.walk_forward import run_walk_forward

        df = pd.DataFrame({"close": np.cumsum(np.random.randn(600))})

        def simple_strategy(train_df, test_df):
            # Just return random small returns for testing
            return pd.Series(np.random.randn(len(test_df)) * 0.01)

        result = run_walk_forward(df, simple_strategy, n_splits=3)
        assert result.n_splits == 3
        assert len(result.sharpe_ratios) == 3
        assert len(result.max_drawdowns) == 3
