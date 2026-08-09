# tests/unit/test_goldhtf_ablation_zone.py
"""The zone-free stop must be symmetric, width-matched, and side-correct."""
import pytest

from scripts.simulate_goldhtf_ea import zone_free_stop


def test_long_stop_sits_below_entry_by_mult_times_atr():
    assert zone_free_stop(2000.0, 1, 4.0, 1.5) == pytest.approx(1994.0, abs=1e-9)


def test_short_stop_sits_above_entry_by_mult_times_atr():
    assert zone_free_stop(2000.0, -1, 4.0, 1.5) == pytest.approx(2006.0, abs=1e-9)


def test_width_is_identical_on_both_sides():
    long_w = 2000.0 - zone_free_stop(2000.0, 1, 3.3, 2.0)
    short_w = zone_free_stop(2000.0, -1, 3.3, 2.0) - 2000.0
    assert long_w == pytest.approx(short_w, abs=1e-9)


def test_zero_multiple_is_rejected_rather_than_producing_a_zero_width_stop():
    with pytest.raises(ValueError):
        zone_free_stop(2000.0, 1, 4.0, 0.0)
