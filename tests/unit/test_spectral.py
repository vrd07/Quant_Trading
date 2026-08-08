"""Spectral estimation: window, Burg AR, MESA, and dominant-cycle selection."""
import numpy as np
import pytest

from src.cycles.spectral import (
    CycleEstimate,
    blackman_harris,
    burg_ar,
    dominant_cycle,
    fft_spectrum,
    mesa_spectrum,
)


def sine(period, n, amp=1.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return amp * np.sin(2 * np.pi * t / period) + noise * rng.standard_normal(n)


class TestWindow:
    def test_length_and_symmetry(self):
        w = blackman_harris(64)
        assert w.shape == (64,)
        np.testing.assert_allclose(w, w[::-1], atol=1e-12)

    def test_endpoints_near_zero(self):
        w = blackman_harris(128)
        assert w[0] < 1e-3 and w[-1] < 1e-3

    def test_peak_at_centre(self):
        w = blackman_harris(101)
        assert int(np.argmax(w)) == 50


class TestFFT:
    def test_recovers_clean_sine_period(self):
        periods, amps = fft_spectrum(sine(32, 256))
        assert periods[int(np.argmax(amps))] == pytest.approx(32.0, rel=0.1)

    def test_dc_is_excluded(self):
        periods, amps = fft_spectrum(sine(32, 256) + 1000.0)
        assert np.all(np.isfinite(periods))
        assert periods[int(np.argmax(amps))] == pytest.approx(32.0, rel=0.1)

    def test_linear_trend_does_not_masquerade_as_a_cycle(self):
        periods, amps = fft_spectrum(sine(32, 256) + 0.5 * np.arange(256))
        assert periods[int(np.argmax(amps))] == pytest.approx(32.0, rel=0.15)


class TestOverlappingWindows:
    """Spec §3.2: 50% overlapping windows to smooth the spectrogram."""

    def test_single_segment_is_the_plain_periodogram(self):
        a = fft_spectrum(sine(32, 256), segments=1)
        b = fft_spectrum(sine(32, 256))
        np.testing.assert_allclose(a[1], b[1], atol=1e-12)

    def test_overlap_averaging_reduces_spectral_variance(self):
        """The point of Welch: same peak, less noise floor jitter around it."""
        x = sine(32, 512, noise=1.0, seed=8)
        _, plain = fft_spectrum(x, segments=1)
        _, welch = fft_spectrum(x, segments=4)
        assert np.std(np.diff(welch)) / np.mean(welch) < np.std(np.diff(plain)) / np.mean(plain)

    def test_overlap_still_finds_the_peak(self):
        periods, amps = fft_spectrum(sine(40, 512, noise=0.5, seed=9), segments=4)
        assert periods[int(np.argmax(amps))] == pytest.approx(40.0, rel=0.2)

    def test_degrades_gracefully_when_window_too_short_to_split(self):
        periods, amps = fft_spectrum(sine(8, 24), segments=8)
        assert periods.size == amps.size > 0

    def test_rejects_zero_segments(self):
        with pytest.raises(ValueError):
            fft_spectrum(sine(32, 256), segments=0)


class TestBurg:
    def test_recovers_ar1_coefficient(self):
        rng = np.random.default_rng(1)
        n, phi = 4000, 0.9
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = phi * x[i - 1] + rng.standard_normal()
        a, var = burg_ar(x, order=1)
        assert a[0] == 1.0
        assert a[1] == pytest.approx(-phi, abs=0.05)
        assert var > 0

    def test_rejects_order_ge_length(self):
        with pytest.raises(ValueError):
            burg_ar(np.arange(10, dtype=float), order=10)


class TestMESA:
    def test_resolves_sine_in_short_window(self):
        """The reason MESA exists here: 96-bar windows are too short for FFT
        resolution, which is exactly Gold's configured window."""
        periods, power = mesa_spectrum(sine(24, 96, noise=0.15, seed=4))
        assert periods[int(np.argmax(power))] == pytest.approx(24.0, rel=0.15)


class TestDominantCycle:
    def test_finds_period_and_high_prominence_on_clean_sine(self):
        est = dominant_cycle(sine(34, 256), min_period=8, max_period=128)
        assert est.period == pytest.approx(34.0, rel=0.15)
        assert est.prominence > 2.0

    def test_white_noise_has_low_prominence(self):
        rng = np.random.default_rng(2)
        est = dominant_cycle(rng.standard_normal(256), min_period=8, max_period=128)
        assert est.prominence < 2.0

    def test_respects_min_period(self):
        est = dominant_cycle(sine(4, 256), min_period=8, max_period=128)
        assert est.period is None or est.period >= 8.0

    def test_auto_picks_mesa_for_short_window(self):
        assert dominant_cycle(sine(24, 96), min_period=8, max_period=48).method == "mesa"

    def test_auto_picks_fft_for_long_window(self):
        assert dominant_cycle(sine(24, 168), min_period=6, max_period=84).method == "fft"

    def test_too_short_returns_empty_estimate(self):
        est = dominant_cycle(np.arange(5, dtype=float), min_period=8, max_period=128)
        assert est == CycleEstimate(period=None, prominence=0.0, power_fraction=0.0, method="none")

    def test_nan_input_returns_empty_estimate(self):
        x = sine(32, 256)
        x[10] = np.nan
        assert dominant_cycle(x, min_period=8, max_period=128).period is None
