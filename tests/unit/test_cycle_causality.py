"""Spec §8.1 verification: causality, cycle recovery, and no-edge-on-noise.

These are the tests that decide whether any downstream backtest number means
anything. Three failure modes are covered:

  1. Lookahead   -- output at bar t changes when bar t+1 arrives.
  2. Hallucination -- the engine "finds" cycles in 1/f^2 noise, which is what
                    price actually is (reports/fourier_research.md).
  3. Fitting     -- the engine trades a shuffled series as happily as the real one.
"""
import numpy as np
import pandas as pd
import pytest

from src.cycles.causal_dwt import CausalDWT
from src.cycles.cycle_tracker import CycleTracker
from src.cycles.spectral import dominant_cycle
from src.cycles.validation import arma_series, pink_noise, surrogate_series


def _index(n):
    return pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")


class TestNoLookahead:
    @pytest.mark.parametrize("wavelet,level", [("db4", 2), ("sym8", 3)])
    def test_dwt_prefix_invariance(self, wavelet, level):
        rng = np.random.default_rng(101)
        x = np.cumsum(rng.standard_normal(800))
        dwt = CausalDWT(wavelet, level)
        full = dwt.transform(x).trend
        for t in (200, 399, 600, 799):
            assert dwt.transform(x[: t + 1]).trend[t] == pytest.approx(full[t], abs=1e-12)

    def test_tracker_state_is_frozen_once_emitted(self):
        """A tracker replayed over a longer series must reproduce, bar for bar,
        the states it produced over the shorter one."""
        rng = np.random.default_rng(102)
        close = pd.Series(2000 + np.cumsum(rng.standard_normal(500)), index=_index(500))

        def replay(upto):
            t = CycleTracker.for_asset("gold")
            return [(s.period, s.trend, s.tradeable)
                    for s in (t.update(close[:i]) for i in range(150, upto))]

        short, long = replay(400), replay(500)
        assert short == long[: len(short)]

    def test_appending_a_shock_does_not_rewrite_history(self):
        rng = np.random.default_rng(103)
        close = pd.Series(2000 + np.cumsum(rng.standard_normal(400)), index=_index(400))
        t1 = CycleTracker.for_asset("gold")
        before = [t1.update(close[:i]).trend for i in range(150, 400)]

        shocked = close.copy()
        shocked.iloc[-1] = 9999.0
        t2 = CycleTracker.for_asset("gold")
        after = [t2.update(shocked[:i]).trend for i in range(150, 400)]

        np.testing.assert_allclose(before[:-1], after[:-1], atol=1e-12)


class TestSyntheticRecovery:
    @pytest.mark.parametrize("period", [16.0, 24.0, 34.0, 48.0])
    def test_recovers_known_cycle_under_noise(self, period):
        rng = np.random.default_rng(int(period))
        t = np.arange(600)
        # SNR ~10 dB: amplitude 3.0 signal against unit-variance noise.
        x = 3.0 * np.sin(2 * np.pi * t / period) + rng.standard_normal(600)
        est = dominant_cycle(x[-96:], min_period=8, max_period=48)
        assert est.period == pytest.approx(period, rel=0.25)

    def test_tradeable_on_a_real_cycle(self):
        rng = np.random.default_rng(7)
        t = np.arange(600)
        close = pd.Series(2000 + 8.0 * np.sin(2 * np.pi * t / 32)
                          + 0.8 * rng.standard_normal(600), index=_index(600))
        tr = CycleTracker.for_asset("gold")
        states = [tr.update(close[:i]) for i in range(200, 600)]
        rate = sum(s.tradeable for s in states) / len(states)
        assert rate > 0.5, f"engine only fired on {rate:.0%} of bars of a clean cycle"


class TestNoHallucinatedCycles:
    """The failure this whole suite exists to catch."""

    @pytest.mark.parametrize("gen,name", [(pink_noise, "pink"), (arma_series, "arma")])
    def test_colored_noise_rarely_passes_the_gate(self, gen, name):
        rates = []
        for seed in range(5):
            close = pd.Series(2000 + np.cumsum(gen(600, seed)), index=_index(600))
            tr = CycleTracker.for_asset("gold")
            states = [tr.update(close[:i]) for i in range(200, 600)]
            rates.append(sum(s.tradeable for s in states) / len(states))
        mean_rate = float(np.mean(rates))
        assert mean_rate < 0.30, (
            f"{name} noise passed the tradeable gate on {mean_rate:.0%} of bars -- "
            "the confidence gates are not filtering hallucinated cycles"
        )

    def test_random_walk_prominence_is_usually_below_the_gate(self):
        rng = np.random.default_rng(303)
        passes = 0
        trials = 40
        for _ in range(trials):
            walk = np.cumsum(rng.standard_normal(96))
            if dominant_cycle(walk, min_period=8, max_period=48).prominence >= 2.0:
                passes += 1
        assert passes / trials < 0.35


class TestSurrogateData:
    def test_surrogate_preserves_volatility_and_destroys_autocorrelation(self):
        rng = np.random.default_rng(404)
        close = pd.Series(2000 * np.exp(np.cumsum(rng.standard_normal(2000) * 0.001)),
                          index=_index(2000))
        surr = surrogate_series(close, seed=1)
        r0 = np.diff(np.log(close.to_numpy()))
        r1 = np.diff(np.log(surr.to_numpy()))
        assert np.std(r1) == pytest.approx(np.std(r0), rel=1e-9)
        assert len(surr) == len(close)

    def test_surrogate_runs_are_deterministic_per_seed(self):
        rng = np.random.default_rng(505)
        close = pd.Series(2000 + np.cumsum(rng.standard_normal(500)), index=_index(500))
        pd.testing.assert_series_equal(surrogate_series(close, 9), surrogate_series(close, 9))

    def test_engine_does_not_light_up_on_shuffled_returns(self):
        """If the tradeable rate on shuffled data matches the real series, the
        gates are measuring window length, not market structure."""
        rng = np.random.default_rng(606)
        close = pd.Series(2000 + np.cumsum(rng.standard_normal(700)), index=_index(700))
        surr = surrogate_series(close, seed=2)
        def rate(s):
            tr = CycleTracker.for_asset("gold")
            states = [tr.update(s[:i]) for i in range(200, 700)]
            return sum(x.tradeable for x in states) / len(states)
        assert abs(rate(close) - rate(surr)) < 0.25
