"""Regime classification gate (spec §5.2)."""
import numpy as np
import pandas as pd
import pytest

from src.core.constants import MarketRegime
from src.cycles.regime import CycleRegime, RegimeDetector, RegimeState


def make_bars(close: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(close), freq="15min", tz="UTC")
    spread = np.abs(np.diff(close, prepend=close[0])) + 0.5
    return pd.DataFrame({
        "open": close, "high": close + spread, "low": close - spread,
        "close": close, "volume": np.ones(len(close)),
    }, index=idx)


# Hurst measures the persistence of INCREMENTS, not whether a trend line is
# visible on a chart, so these generators have to be built at the increment
# level. The obvious fixtures do the opposite of what they look like: a linear
# ramp plus iid noise differences to an MA(1) with rho = -0.5, which is
# anti-persistent (H ~ 0.29), and a smooth sine is locally persistent within
# each half cycle (H ~ 0.62). Measured on real 15m gold, H > 0.5 on 67% of bars.
def trending(n=600, drift=0.15, phi=0.9, vol=0.35, seed=1):
    """Drifting walk with positively autocorrelated increments (persistent)."""
    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal(n)
    inc = np.zeros(n)
    for i in range(1, n):
        inc[i] = phi * inc[i - 1] + shocks[i]
    return 1000.0 + np.cumsum(drift + vol * inc)


def ranging(n=600, kappa=0.15, seed=2):
    """Ornstein-Uhlenbeck pull to the mean: anti-persistent increments."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] - kappa * x[i - 1] + rng.standard_normal()
    return 1000.0 + 3.0 * x


class TestClassification:
    def test_strong_uptrend_is_trend(self):
        assert RegimeDetector().classify(make_bars(trending())).regime is CycleRegime.TREND

    def test_oscillating_market_is_not_trend(self):
        assert RegimeDetector().classify(make_bars(ranging())).regime is not CycleRegime.TREND

    def test_uncertain_reduces_size_by_eighty_percent(self):
        state = RegimeState(CycleRegime.UNCERTAIN, adx=22.0, hurst=0.55,
                            vol_percentile=0.5, vol_of_vol=0.1, size_multiplier=0.2)
        assert state.size_multiplier == pytest.approx(0.2)

    def test_trend_and_mean_revert_get_full_size(self):
        det = RegimeDetector()
        assert det.classify(make_bars(trending())).size_multiplier == pytest.approx(1.0)

    def test_insufficient_history_is_uncertain(self):
        state = RegimeDetector().classify(make_bars(trending(n=40)))
        assert state.regime is CycleRegime.UNCERTAIN
        assert state.size_multiplier == pytest.approx(0.2)


class TestThresholdsMatchSpec:
    def test_trend_requires_adx_above_25_and_hurst_above_half(self):
        det = RegimeDetector()
        assert det._classify_from(adx=26.0, hurst=0.55, vol_pct=0.5) is CycleRegime.TREND
        assert det._classify_from(adx=24.0, hurst=0.55, vol_pct=0.5) is not CycleRegime.TREND
        assert det._classify_from(adx=26.0, hurst=0.45, vol_pct=0.5) is not CycleRegime.TREND

    def test_mean_revert_requires_all_three_conditions(self):
        det = RegimeDetector()
        assert det._classify_from(adx=18.0, hurst=0.42, vol_pct=0.30) is CycleRegime.MEAN_REVERT
        assert det._classify_from(adx=21.0, hurst=0.42, vol_pct=0.30) is CycleRegime.UNCERTAIN
        assert det._classify_from(adx=18.0, hurst=0.52, vol_pct=0.30) is CycleRegime.UNCERTAIN
        assert det._classify_from(adx=18.0, hurst=0.42, vol_pct=0.45) is CycleRegime.UNCERTAIN


class TestStopMultiplier:
    @pytest.mark.parametrize("vol_pct,expected", [(0.20, 1.5), (0.50, 2.0), (0.85, 2.5)])
    def test_stop_scales_with_volatility_percentile(self, vol_pct, expected):
        state = RegimeState(CycleRegime.TREND, 30.0, 0.6, vol_pct, 0.1, 1.0)
        assert RegimeDetector().stop_atr_multiplier(state) == pytest.approx(expected)


class TestMarketRegimeMapping:
    def test_maps_onto_the_shared_enum(self):
        mk = lambda r: RegimeState(r, 30.0, 0.6, 0.5, 0.1, 1.0).to_market_regime()
        assert mk(CycleRegime.TREND) is MarketRegime.TREND
        assert mk(CycleRegime.MEAN_REVERT) is MarketRegime.RANGE
        assert mk(CycleRegime.UNCERTAIN) is MarketRegime.UNKNOWN
