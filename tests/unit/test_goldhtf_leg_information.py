"""Forward-return machinery for the leg-information cross-check."""
import numpy as np
import pytest

from scripts.goldhtf_leg_information import (
    day_blocked_ci, drift_adjust, forward_returns)


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
