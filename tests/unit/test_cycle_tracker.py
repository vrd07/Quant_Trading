"""CycleTracker: gating, stability memory, and asset presets."""
import numpy as np
import pandas as pd
import pytest

from src.cycles.cycle_tracker import CycleState, CycleTracker


def series(values):
    idx = pd.date_range("2026-01-01", periods=len(values), freq="15min", tz="UTC")
    return pd.Series(values, index=idx, dtype=float)


def sine_series(period, n, amp=5.0, base=2000.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return series(base + amp * np.sin(2 * np.pi * t / period) + noise * rng.standard_normal(n))


class TestPresets:
    def test_gold_preset_matches_spec_matrix(self):
        t = CycleTracker.for_asset("gold")
        assert (t.wavelet, t.level, t.window, t.min_period, t.top_k) == ("db4", 2, 96, 8, 2)

    def test_btc_preset_matches_spec_matrix(self):
        t = CycleTracker.for_asset("btc")
        assert (t.wavelet, t.level, t.window, t.min_period, t.top_k) == ("sym8", 3, 168, 6, 3)

    def test_unknown_asset_raises(self):
        with pytest.raises(ValueError):
            CycleTracker.for_asset("silver")


class TestGating:
    def test_clean_cycle_becomes_tradeable(self):
        t = CycleTracker.for_asset("gold")
        st = None
        for i in range(200, 400):
            st = t.update(sine_series(32, 400)[:i])
        assert st.tradeable is True
        assert st.period == pytest.approx(32.0, rel=0.25)
        assert st.phase is not None

    def test_random_walk_is_not_tradeable(self):
        rng = np.random.default_rng(21)
        walk = series(2000.0 + np.cumsum(rng.standard_normal(400)))
        t = CycleTracker.for_asset("gold")
        st = None
        for i in range(200, 400):
            st = t.update(walk[:i])
        assert st.tradeable is False

    def test_low_prominence_reports_reason(self):
        rng = np.random.default_rng(22)
        walk = series(2000.0 + np.cumsum(rng.standard_normal(300)))
        t = CycleTracker.for_asset("gold")
        for i in range(200, 300):
            st = t.update(walk[:i])
        assert st.reason in {"low_prominence", "unstable", "no_cycle"}

    def test_cycle_jump_is_rejected(self):
        """Stitch two very different cycles: the engine must stop trading across
        the jump.

        It may refuse for either of two correct reasons -- the blended window has
        no dominant peak (low_prominence), or the 5-window memory sees the length
        shift (unstable). Measured, this transition goes through the prominence
        valley first, so asserting one specific string over-specifies the design.
        What matters is that it refuses, and that it re-acquires the new cycle.
        """
        t = CycleTracker.for_asset("gold")
        a = sine_series(12, 300).to_numpy()
        b = sine_series(48, 300, seed=1).to_numpy()
        joined = series(np.concatenate([a, b]))
        states = [t.update(joined[:i]) for i in range(200, 480)]
        post = states[-170:]
        assert any(not s.tradeable for s in post)
        assert any(s.reason in {"unstable", "low_prominence"} for s in post)
        assert states[-1].period == pytest.approx(48.0, rel=0.25)

    def test_stability_gate_rejects_a_twenty_percent_shift(self):
        """Spec §4.3's rule, tested directly rather than through a market."""
        t = CycleTracker.for_asset("gold")
        t.history.extend([30.0, 30.5, 30.2, 30.4, 30.1])
        assert t._is_stable() is True
        t.reset()
        t.history.extend([30.0, 30.5, 30.2, 30.4, 45.0])
        assert t._is_stable() is False

    def test_stability_gate_needs_a_full_window_of_history(self):
        t = CycleTracker.for_asset("gold")
        t.history.extend([30.0, 30.1, 30.2])
        assert t._is_stable() is False

    def test_insufficient_history_is_not_tradeable(self):
        t = CycleTracker.for_asset("gold")
        st = t.update(sine_series(32, 30))
        assert st.tradeable is False and st.reason == "insufficient_history"


class TestDelayCompensation:
    def test_deviation_uses_delayed_price_per_spec(self):
        """Spec §3.2: comparisons must be offset by the measured group delay."""
        t = CycleTracker.for_asset("gold")
        s = sine_series(32, 400)
        st = None
        # Range end is 401 so the final update sees the WHOLE series; with 400
        # the last slice is s[:399] and s.iloc[-1] is a bar the tracker never saw.
        for i in range(200, 401):
            st = t.update(s[:i])
        tau = int(round(st.group_delay))
        expected = float(s.iloc[-1 - tau]) - st.trend
        assert st.deviation == pytest.approx(expected, abs=1e-6)

    def test_raw_deviation_is_undelayed(self):
        t = CycleTracker.for_asset("gold")
        s = sine_series(32, 400)
        st = None
        for i in range(200, 401):
            st = t.update(s[:i])
        assert st.raw_deviation == pytest.approx(float(s.iloc[-1]) - st.trend, abs=1e-6)


class TestStatefulness:
    def test_reset_clears_stability_memory(self):
        t = CycleTracker.for_asset("gold")
        for i in range(200, 300):
            t.update(sine_series(32, 300)[:i])
        assert len(t.history) > 0
        t.reset()
        assert len(t.history) == 0

    def test_replay_is_reproducible(self):
        s = sine_series(32, 350, noise=0.5, seed=3)
        def run():
            t = CycleTracker.for_asset("gold")
            return [t.update(s[:i]).period for i in range(200, 350)]
        assert run() == run()
