"""Real-time cycle phase tracking (Goertzel) and Ehlers roofing bandpass."""
import numpy as np
import pytest

from src.cycles.goertzel import CyclePhase, RoofingFilter, goertzel


def cosine(period, n, phase=0.0, amp=1.0):
    t = np.arange(n)
    return amp * np.cos(2 * np.pi * t / period + phase)


class TestGoertzelAmplitude:
    def test_recovers_amplitude_of_pure_cosine(self):
        out = goertzel(cosine(32, 256, amp=3.0), period=32.0)
        assert out.amplitude == pytest.approx(3.0, rel=0.1)

    def test_amplitude_near_zero_off_frequency(self):
        out = goertzel(cosine(32, 256), period=9.0)
        assert out.amplitude < 0.3

    def test_handles_non_integer_period(self):
        out = goertzel(cosine(27.5, 220, amp=2.0), period=27.5)
        assert out.amplitude == pytest.approx(2.0, rel=0.15)


class TestGoertzelPosition:
    """Position convention: 0.0 = peak, 0.5 = trough, advancing with time."""

    def test_position_zero_at_peak(self):
        # Signal ends exactly on a peak: length is a whole number of periods.
        out = goertzel(cosine(32, 320), period=32.0)
        assert min(out.position, 1.0 - out.position) < 0.06

    def test_position_half_at_trough(self):
        out = goertzel(cosine(32, 320 + 16), period=32.0)
        assert out.position == pytest.approx(0.5, abs=0.06)

    def test_position_in_unit_interval(self):
        for n in range(200, 240):
            assert 0.0 <= goertzel(cosine(17, n), period=17.0).position < 1.0

    def test_position_advances_monotonically_within_a_cycle(self):
        base = 320
        positions = [goertzel(cosine(32, base + k), period=32.0).position
                     for k in range(1, 12)]
        assert all(b > a for a, b in zip(positions, positions[1:]))


class TestGoertzelGuards:
    def test_short_input_returns_zero_amplitude(self):
        out = goertzel(np.arange(4, dtype=float), period=32.0)
        assert out == CyclePhase(amplitude=0.0, phase=0.0, position=0.0)

    def test_nan_input_returns_zero_amplitude(self):
        x = cosine(32, 128)
        x[5] = np.nan
        assert goertzel(x, period=32.0).amplitude == 0.0

    def test_uses_only_the_last_period_worth_of_bars(self):
        """Older history must not move the current phase reading."""
        tail = cosine(32, 512)
        a = goertzel(tail, period=32.0)
        prepended = np.concatenate([np.full(300, 999.0), tail])
        b = goertzel(prepended, period=32.0)
        assert a.position == pytest.approx(b.position, abs=1e-9)


class TestRoofingFilter:
    def test_removes_dc_offset(self):
        out = RoofingFilter(hp_period=48.0, ss_period=10.0).apply(np.full(300, 100.0))
        assert abs(out[-1]) < 1e-6

    def test_passes_the_target_cycle(self):
        x = cosine(30, 600)
        out = RoofingFilter(hp_period=60.0, ss_period=15.0).apply(x)
        assert np.std(out[200:]) > 0.3 * np.std(x[200:])

    def test_attenuates_a_slow_trend(self):
        ramp = np.arange(600, dtype=float)
        out = RoofingFilter(hp_period=48.0, ss_period=10.0).apply(ramp)
        assert np.std(out[200:]) < 0.05 * np.std(ramp[200:])

    def test_attenuates_high_frequency_noise(self):
        rng = np.random.default_rng(9)
        noise = rng.standard_normal(600)
        out = RoofingFilter(hp_period=48.0, ss_period=20.0).apply(noise)
        assert np.std(out[200:]) < np.std(noise[200:])

    def test_output_length_matches_input(self):
        assert RoofingFilter(48.0, 10.0).apply(np.zeros(123)).shape == (123,)

    def test_is_causal(self):
        rng = np.random.default_rng(13)
        x = np.cumsum(rng.standard_normal(400))
        rf = RoofingFilter(48.0, 10.0)
        full = rf.apply(x)
        assert rf.apply(x[:250])[249] == pytest.approx(full[249], abs=1e-9)
