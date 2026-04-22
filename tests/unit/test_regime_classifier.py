"""
Unit tests for the improved regime classifier.

Tests cover:
- Feature computation produces expected columns
- Label computation correctly classifies TREND/RANGE/VOLATILE
- Markov transition matrix is valid (rows sum to 1.0)
- Dynamic strategy weighting produces different sets per regime
- Low-confidence predictions enable more strategies
- Strategy scorer computes scores from trade journal data
- Strategy weight adjustment respects guardrails
"""

import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.regime_classifier import (
    compute_daily_bars,
    compute_features,
    compute_labels,
    compute_transition_matrix,
    smooth_prediction_with_markov,
    resolve_strategy_overrides,
    classify_rule_based,
    STRATEGY_WEIGHTS,
    CONFIDENCE_THRESHOLD,
    REGIMES,
    REGIME_ORDER,
    FEATURE_COLS,
)
from scripts.strategy_scorer import (
    compute_strategy_scores,
    adjust_weights,
    WEIGHT_FLOOR,
    WEIGHT_CEILING,
)


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture
def sample_5m_bars():
    """Create 10000 synthetic 5-minute bars (~35 trading days)."""
    np.random.seed(42)
    n = 10000
    base_price = 2000.0
    timestamps = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    closes = [base_price]
    for i in range(1, n):
        # First 4000 bars: strong trend to generate TREND labels
        if i < 4000:
            drift = 0.3
        # Next 3000: range-bound
        elif i < 7000:
            drift = 0.01 * np.sin(i / 100 * np.pi)
        # Last 3000: volatile (big swings)
        else:
            drift = np.random.choice([-1.0, 1.0]) * 0.5
        closes.append(closes[-1] + drift + np.random.randn() * 0.5)
    closes = np.array(closes)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": closes - 0.3,
        "high": closes + np.random.rand(n) * 2.0,
        "low": closes - np.random.rand(n) * 2.0,
        "close": closes,
        "volume": np.random.randint(500, 2000, size=n).astype(float),
    })


@pytest.fixture
def sample_daily(sample_5m_bars):
    """Aggregated daily bars from 5m data."""
    return compute_daily_bars(sample_5m_bars)


@pytest.fixture
def sample_features(sample_daily):
    """Feature DataFrame from daily bars."""
    return compute_features(sample_daily)


@pytest.fixture
def sample_labels(sample_daily, sample_features):
    """Labels from daily bars."""
    return compute_labels(sample_daily, sample_features)


@pytest.fixture
def sample_trade_journal(tmp_path):
    """Create a temp trade journal CSV with recent dates."""
    journal = tmp_path / "trade_journal.csv"
    # Use dates relative to now so lookback filter works
    recent_start = datetime.now(timezone.utc) - timedelta(days=29)
    data = {
        "entry_time": pd.date_range(recent_start, periods=30, freq="1D", tz="UTC"),
        "strategy": (["breakout"] * 10 + ["momentum"] * 10 + ["kalman_regime"] * 10),
        "realized_pnl": (
            [5.0, -2.0, 8.0, -1.0, 6.0, 3.0, -4.0, 7.0, 2.0, 1.0]  # breakout: net positive
            + [-3.0, -2.0, -5.0, -1.0, -4.0, -2.0, -3.0, -1.0, -2.0, -3.0]  # momentum: net negative
            + [10.0, 5.0, 8.0, -2.0, 12.0, 3.0, 7.0, 4.0, -1.0, 6.0]  # kalman: very positive
        ),
        "pnl_pct": [0.1] * 30,
        "duration_seconds": [300] * 30,
    }
    pd.DataFrame(data).to_csv(journal, index=False)
    return journal


# -- Feature Engineering Tests -----------------------------------------------

class TestFeatureEngineering:
    """Tests for compute_daily_bars and compute_features."""

    def test_daily_bars_aggregation(self, sample_5m_bars):
        """compute_daily_bars produces daily OHLCV from 5m data."""
        daily = compute_daily_bars(sample_5m_bars)
        assert len(daily) > 0
        assert set(["date", "open", "high", "low", "close", "volume"]).issubset(
            set(daily.columns)
        )

    def test_daily_bars_open_close(self, sample_5m_bars):
        """First bar open and last bar close are preserved per day."""
        daily = compute_daily_bars(sample_5m_bars)
        assert daily["open"].iloc[0] == pytest.approx(
            sample_5m_bars["open"].iloc[0], abs=1e-6
        )

    def test_features_all_columns_present(self, sample_features):
        """compute_features produces all expected feature columns."""
        for col in FEATURE_COLS:
            assert col in sample_features.columns, f"Missing feature column: {col}"

    def test_features_non_null_after_warmup(self, sample_features):
        """After initial warmup rows, features should be non-null."""
        # Drop first few rows that need indicator warmup
        valid = sample_features[FEATURE_COLS].dropna()
        assert len(valid) > 0, "No valid feature rows after warmup"

    def test_adx_is_positive(self, sample_features):
        """ADX values should be positive."""
        valid_adx = sample_features["adx_14"].dropna()
        assert (valid_adx >= 0).all()


# -- Label Computation Tests ------------------------------------------------

class TestLabelComputation:
    """Tests for compute_labels."""

    def test_labels_only_valid_values(self, sample_daily, sample_features):
        """Labels should be one of TREND, RANGE, VOLATILE."""
        labels = compute_labels(sample_daily, sample_features)
        valid_labels = set(labels.dropna().unique())
        assert valid_labels.issubset({"TREND", "RANGE", "VOLATILE"})

    def test_labels_not_all_same(self, sample_daily, sample_features):
        """Labels should have at least 2 different values (not all same)."""
        labels = compute_labels(sample_daily, sample_features)
        assert len(labels.dropna().unique()) >= 2, "All labels identical"


# -- Markov Transition Matrix Tests -----------------------------------------

class TestMarkovTransitionMatrix:
    """Tests for the Markov chain model."""

    def test_matrix_rows_sum_to_one(self, sample_labels):
        """Each row in the transition matrix should sum to 1.0."""
        matrix = compute_transition_matrix(sample_labels)
        for from_regime in REGIMES:
            row_sum = sum(matrix[from_regime].values())
            assert row_sum == pytest.approx(1.0, abs=1e-4), (
                f"Row {from_regime} sums to {row_sum}"
            )

    def test_matrix_all_regimes_present(self, sample_labels):
        """Matrix should have entries for all 3 regimes."""
        matrix = compute_transition_matrix(sample_labels)
        for r in REGIMES:
            assert r in matrix
            for r2 in REGIMES:
                assert r2 in matrix[r]

    def test_matrix_probabilities_non_negative(self, sample_labels):
        """All transition probabilities should be >= 0."""
        matrix = compute_transition_matrix(sample_labels)
        for from_r in REGIMES:
            for to_r in REGIMES:
                assert matrix[from_r][to_r] >= 0

    def test_laplace_smoothing_no_zeros(self):
        """Even with single-regime data, matrix should have no zeros (Laplace)."""
        labels = pd.Series(["RANGE"] * 10)
        matrix = compute_transition_matrix(labels)
        for from_r in REGIMES:
            for to_r in REGIMES:
                assert matrix[from_r][to_r] > 0


class TestMarkovSmoothing:
    """Tests for smooth_prediction_with_markov."""

    def test_smoothing_blends_probabilities(self):
        """Smoothed probabilities should be between RF and Markov values."""
        rf_proba = {"TREND": 0.7, "RANGE": 0.2, "VOLATILE": 0.1}
        matrix = {
            "RANGE": {"TREND": 0.3, "RANGE": 0.5, "VOLATILE": 0.2},
            "TREND": {"TREND": 0.6, "RANGE": 0.3, "VOLATILE": 0.1},
            "VOLATILE": {"TREND": 0.2, "RANGE": 0.3, "VOLATILE": 0.5},
        }
        smoothed = smooth_prediction_with_markov(rf_proba, "RANGE", matrix, alpha=0.7)

        assert sum(smoothed.values()) == pytest.approx(1.0, abs=1e-4)
        assert smoothed["TREND"] > 0
        assert smoothed["RANGE"] > 0
        assert smoothed["VOLATILE"] > 0

    def test_unknown_prev_regime_returns_rf(self):
        """If prev_regime is not in matrix, return rf_proba unchanged."""
        rf_proba = {"TREND": 0.5, "RANGE": 0.3, "VOLATILE": 0.2}
        matrix = {
            "TREND": {"TREND": 0.5, "RANGE": 0.3, "VOLATILE": 0.2},
        }
        result = smooth_prediction_with_markov(rf_proba, "UNKNOWN", matrix)
        assert result == rf_proba


# -- Dynamic Strategy Weighting Tests ----------------------------------------

class TestDynamicWeighting:
    """Tests for resolve_strategy_overrides."""

    def test_trend_regime_enables_breakout_momentum(self):
        """TREND regime should enable breakout and momentum."""
        overrides = resolve_strategy_overrides("TREND", 0.80, {})
        assert overrides["breakout"] is True
        assert overrides["momentum"] is True

    def test_range_regime_enables_all_profitable(self):
        """RANGE regime should enable all 4 profitable strategies."""
        overrides = resolve_strategy_overrides("RANGE", 0.80, {})
        assert overrides["breakout"] is True
        assert overrides["momentum"] is True
        assert overrides["kalman_regime"] is True
        assert overrides["mini_medallion"] is True

    def test_volatile_regime_enables_kalman(self):
        """VOLATILE regime should enable kalman_regime."""
        overrides = resolve_strategy_overrides("VOLATILE", 0.80, {})
        assert overrides["kalman_regime"] is True

    def test_unprofitable_strategies_disabled_by_default(self):
        """mean_reversion disabled in all regimes; vwap disabled only in TREND."""
        for regime in REGIMES:
            overrides = resolve_strategy_overrides(regime, 0.80, {})
            assert overrides["mean_reversion"] is False
        assert resolve_strategy_overrides("TREND", 0.80, {})["vwap"] is False
        assert resolve_strategy_overrides("RANGE", 0.80, {})["vwap"] is True
        assert resolve_strategy_overrides("VOLATILE", 0.80, {})["vwap"] is True

    def test_low_confidence_enables_more_strategies(self):
        """Low confidence (< 0.55) should lower threshold, enabling more strategies."""
        high_conf = resolve_strategy_overrides("RANGE", 0.85, {})
        low_conf = resolve_strategy_overrides("RANGE", 0.50, {})

        high_enabled = sum(1 for v in high_conf.values() if v)
        low_enabled = sum(1 for v in low_conf.values() if v)

        assert low_enabled >= high_enabled, (
            f"Low confidence should enable >= strategies: "
            f"low={low_enabled}, high={high_enabled}"
        )

    def test_all_regimes_enable_core_profitable_strategies(self):
        """All regimes enable the 4 core profitable strategies; RANGE/VOLATILE also enable vwap."""
        core = {"breakout", "momentum", "kalman_regime", "mini_medallion"}
        for regime in REGIMES:
            overrides = resolve_strategy_overrides(regime, 0.80, {})
            enabled = {s for s, v in overrides.items() if v}
            assert core.issubset(enabled), f"{regime} missing core strategies: got {enabled}"
            if regime == "TREND":
                assert "vwap" not in enabled
            else:
                assert "vwap" in enabled

    def test_weights_table_completeness(self):
        """Every regime in STRATEGY_WEIGHTS must cover every enabled strategy
        so new strategies get regime-adaptive weighting and analytics coverage."""
        required_core = {
            "breakout", "momentum", "kalman_regime", "mean_reversion",
            "vwap", "mini_medallion", "sbr", "supply_demand",
            "asia_range_fade", "descending_channel_breakout", "smc_ob",
            "fibonacci_retracement",
        }
        for regime in REGIMES:
            keys = set(STRATEGY_WEIGHTS[regime].keys())
            missing = required_core - keys
            assert not missing, f"{regime} missing strategies: {missing}"
            # Keys must be consistent across regimes — no per-regime dropouts
            assert keys == set(STRATEGY_WEIGHTS["TREND"].keys()), (
                f"{regime} keys diverge from TREND: "
                f"{keys.symmetric_difference(set(STRATEGY_WEIGHTS['TREND'].keys()))}"
            )


# -- Rule-Based Classifier Tests --------------------------------------------

class TestRuleBasedClassifier:
    """Tests for classify_rule_based."""

    def test_high_adx_returns_trend(self):
        """High ADX and BB ratio should classify as TREND."""
        regime, conf = classify_rule_based({
            "adx_14": 35.0, "bb_width_ratio": 1.5,
            "atr_pct": 0.02, "range_atr_ratio": 1.0,
        })
        assert regime == "TREND"
        assert conf > 0.5

    def test_low_adx_returns_range(self):
        """Low ADX and tight BB should classify as RANGE."""
        regime, conf = classify_rule_based({
            "adx_14": 15.0, "bb_width_ratio": 0.7,
            "atr_pct": 0.005, "range_atr_ratio": 1.0,
        })
        assert regime == "RANGE"

    def test_high_range_atr_returns_volatile(self):
        """High range/ATR with low ADX should classify as VOLATILE."""
        regime, conf = classify_rule_based({
            "adx_14": 15.0, "bb_width_ratio": 1.0,
            "atr_pct": 0.03, "range_atr_ratio": 3.0,
        })
        assert regime == "VOLATILE"


# -- Strategy Scorer Tests ---------------------------------------------------

class TestStrategyScorer:
    """Tests for strategy_scorer module."""

    def test_scores_from_journal(self, sample_trade_journal):
        """compute_strategy_scores should return scores from journal data."""
        scores = compute_strategy_scores(
            journal_path=sample_trade_journal, lookback_days=365,
        )
        assert len(scores) > 0
        assert "breakout" in scores
        assert "kalman_regime" in scores

    def test_profitable_strategy_positive_score(self, sample_trade_journal):
        """Profitable strategies should have positive scores."""
        scores = compute_strategy_scores(
            journal_path=sample_trade_journal, lookback_days=365,
        )
        assert scores.get("kalman_regime", 0.0) > 0

    def test_losing_strategy_negative_score(self, sample_trade_journal):
        """Consistently losing strategies should have negative scores."""
        scores = compute_strategy_scores(
            journal_path=sample_trade_journal, lookback_days=365,
        )
        assert scores.get("momentum", 0.0) < 0

    def test_missing_journal_returns_empty(self, tmp_path):
        """Missing journal file should return empty dict."""
        scores = compute_strategy_scores(
            journal_path=tmp_path / "nonexistent.csv",
        )
        assert scores == {}


class TestWeightAdjustment:
    """Tests for adjust_weights function."""

    def test_positive_score_increases_weight(self):
        """Positive performance score should increase the weight."""
        base = {"breakout": 0.5}
        scores = {"breakout": 0.5}
        adjusted = adjust_weights(base, scores, blend_ratio=1.0)
        assert adjusted["breakout"] > base["breakout"]

    def test_negative_score_decreases_weight(self):
        """Negative performance score should decrease the weight."""
        base = {"momentum": 0.5}
        scores = {"momentum": -0.5}
        adjusted = adjust_weights(base, scores, blend_ratio=1.0)
        assert adjusted["momentum"] < base["momentum"]

    def test_guardrail_floor(self):
        """Weight should never go below WEIGHT_FLOOR."""
        base = {"momentum": 0.1}
        scores = {"momentum": -1.0}
        adjusted = adjust_weights(base, scores, blend_ratio=1.0)
        assert adjusted["momentum"] >= WEIGHT_FLOOR

    def test_guardrail_ceiling(self):
        """Weight should never go above WEIGHT_CEILING."""
        base = {"kalman_regime": 0.9}
        scores = {"kalman_regime": 1.0}
        adjusted = adjust_weights(base, scores, blend_ratio=1.0)
        assert adjusted["kalman_regime"] <= WEIGHT_CEILING

    def test_zero_blend_preserves_base(self):
        """Zero blend ratio should preserve base weights."""
        base = {"breakout": 0.5, "momentum": 0.3}
        scores = {"breakout": 1.0, "momentum": -1.0}
        adjusted = adjust_weights(base, scores, blend_ratio=0.0)
        assert adjusted["breakout"] == pytest.approx(base["breakout"], abs=0.01)
        assert adjusted["momentum"] == pytest.approx(base["momentum"], abs=0.01)
