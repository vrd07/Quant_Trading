"""Forward-return machinery for the leg-information cross-check."""
import numpy as np
import pytest

from scripts.goldhtf_leg_information import (
    day_blocked_ci, drift_adjust, forward_returns, paired_day_difference_ci)


def test_forward_return_is_signed_by_direction_and_scaled_by_atr():
    close = np.array([100.0, 101.0, 102.0, 103.0])
    atr = np.full(4, 2.0)
    out = forward_returns(close, atr, np.array([0]), np.array([1]), horizon=2)
    assert out[0] == pytest.approx(1.0, abs=1e-9)      # (102-100)/2


def test_short_direction_flips_the_sign():
    close = np.array([100.0, 101.0, 102.0, 103.0])
    atr = np.full(4, 2.0)
    out = forward_returns(close, atr, np.array([0]), np.array([-1]), horizon=2)
    assert out[0] == pytest.approx(-1.0, abs=1e-9)


def test_bars_running_past_the_end_are_nan_not_clipped():
    close = np.array([100.0, 101.0, 102.0])
    atr = np.full(3, 1.0)
    out = forward_returns(close, atr, np.array([2]), np.array([1]), horizon=2)
    assert np.isnan(out[0])


def test_zero_atr_is_nan_rather_than_infinite():
    close = np.array([100.0, 105.0])
    atr = np.array([0.0, 0.0])
    out = forward_returns(close, atr, np.array([0]), np.array([1]), horizon=1)
    assert np.isnan(out[0])


def test_bar_beyond_a_short_atr_array_is_nan_not_indexerror():
    """atr can be shorter than close (e.g. warmup-trimmed); an out-of-range
    bar index must degrade to NaN rather than raising IndexError."""
    close = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
    atr = np.array([2.0, 2.0])  # shorter than close
    out = forward_returns(close, atr, np.array([3]), np.array([1]), horizon=1)
    assert np.isnan(out[0])


def test_forward_returns_preserves_alignment_with_mixed_valid_and_invalid_bars():
    """A single call with a mix of valid and past-the-end bars must return an
    array the same length as the input, with NaN holding its position rather
    than the array being compacted."""
    close = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
    atr = np.full(5, 2.0)
    bars = np.array([0, 3, 1])
    dirs = np.array([1, 1, -1])
    out = forward_returns(close, atr, bars, dirs, horizon=2)
    assert out.shape == bars.shape
    assert out[0] == pytest.approx(1.0, abs=1e-9)   # (102-100)/2
    assert np.isnan(out[1])                          # 3+2=5, not < close.size(5)
    assert out[2] == pytest.approx(-1.0, abs=1e-9)   # (103-101)/2 * -1


def test_drift_adjust_removes_the_direction_signed_baseline():
    fwd = np.array([1.0, 1.0])
    dirs = np.array([1, -1])
    out = drift_adjust(fwd, dirs, baseline=0.4)
    assert out[0] == pytest.approx(0.6, abs=1e-9)      # long: 1.0 - (+0.4)
    assert out[1] == pytest.approx(1.4, abs=1e-9)      # short: 1.0 - (-0.4)


def test_day_blocked_ci_is_wider_than_an_iid_ci_on_clustered_data():
    """Every observation inside a day is identical, so the effective n is the
    number of DAYS. A day-blocked CI must not pretend otherwise."""
    rng = np.random.default_rng(0)
    per_day = rng.normal(0, 1, 20)
    values = np.repeat(per_day, 50)
    days = np.repeat(np.arange(20), 50)
    lo, hi = day_blocked_ci(values, days, n_boot=400, seed=1)
    iid_half = 1.96 * values.std(ddof=1) / np.sqrt(len(values))
    assert (hi - lo) / 2 > 3 * iid_half


def test_day_blocked_ci_brackets_the_mean():
    values = np.arange(100, dtype=float)
    days = np.repeat(np.arange(10), 10)
    lo, hi = day_blocked_ci(values, days, n_boot=200, seed=2)
    assert lo < values.mean() < hi


def test_day_blocked_ci_is_deterministic_given_seed():
    values = np.arange(100, dtype=float)
    days = np.repeat(np.arange(10), 10)
    lo1, hi1 = day_blocked_ci(values, days, n_boot=200, seed=42)
    lo2, hi2 = day_blocked_ci(values, days, n_boot=200, seed=42)
    assert lo1 == lo2
    assert hi1 == hi2


def test_day_blocked_ci_returns_nan_nan_when_all_values_are_nan():
    values = np.full(10, np.nan)
    days = np.repeat(np.arange(2), 5)
    lo, hi = day_blocked_ci(values, days, n_boot=50, seed=3)
    assert np.isnan(lo)
    assert np.isnan(hi)


def test_paired_day_difference_ci_excludes_zero_when_samples_differ():
    rng = np.random.default_rng(5)
    days = np.repeat(np.arange(15), 10)
    a_values = np.repeat(rng.normal(1.0, 0.2, 15), 10)
    b_values = np.repeat(rng.normal(0.0, 0.2, 15), 10)
    lo, hi = paired_day_difference_ci(a_values, days, b_values, days,
                                       n_boot=500, seed=11)
    assert lo > 0


def test_paired_day_difference_ci_includes_zero_when_samples_share_the_null():
    """Two samples drawn from the same (zero-mean) distribution, sharing the
    same day-clustering structure: the true difference is zero and the CI
    should bracket it."""
    rng = np.random.default_rng(6)
    days = np.repeat(np.arange(20), 10)
    a_values = np.repeat(rng.normal(0.0, 1.0, 20), 10)
    b_values = np.repeat(rng.normal(0.0, 1.0, 20), 10)
    lo, hi = paired_day_difference_ci(a_values, days, b_values, days,
                                       n_boot=500, seed=12)
    assert lo < 0 < hi


def test_paired_day_difference_ci_returns_nan_nan_for_empty_input():
    lo, hi = paired_day_difference_ci(
        np.array([]), np.array([]), np.array([1.0]), np.array([0]),
        n_boot=50, seed=1)
    assert np.isnan(lo)
    assert np.isnan(hi)


def test_paired_day_difference_ci_is_deterministic_given_seed():
    rng = np.random.default_rng(7)
    days = np.repeat(np.arange(10), 5)
    a_values = np.repeat(rng.normal(1.0, 0.5, 10), 5)
    b_values = np.repeat(rng.normal(0.0, 0.5, 10), 5)
    lo1, hi1 = paired_day_difference_ci(a_values, days, b_values, days,
                                         n_boot=300, seed=9)
    lo2, hi2 = paired_day_difference_ci(a_values, days, b_values, days,
                                         n_boot=300, seed=9)
    assert lo1 == lo2
    assert hi1 == hi2


def test_paired_day_difference_ci_handles_a_day_present_in_only_one_sample():
    """Day 2 exists only in `a`; the union-of-days bootstrap must not raise
    when a draw pulls a day absent from the other sample."""
    a_days = np.array([0, 0, 1, 1, 2, 2])
    a_values = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    b_days = np.array([0, 0, 1, 1])
    b_values = np.array([0.0, 0.0, 0.0, 0.0])
    lo, hi = paired_day_difference_ci(a_values, a_days, b_values, b_days,
                                       n_boot=200, seed=4)
    assert np.isfinite(lo)
    assert np.isfinite(hi)
