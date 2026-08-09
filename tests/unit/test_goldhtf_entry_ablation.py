# tests/unit/test_goldhtf_entry_ablation.py
"""Pre-committed threshold logic and the A2 stop calibration."""
import numpy as np
import pandas as pd
import pytest

from scripts.research_goldhtf_entry_ablation import (
    CELLS, calibrate_zone_stop_mult, censored_fraction, classify)


def test_leg_is_load_bearing_only_when_both_metrics_drop_enough():
    assert classify(-0.6, -12.0, inverted=False) == "load-bearing"


def test_z_drop_alone_is_not_enough():
    assert classify(-0.6, -4.0, inverted=False) == "decoration"


def test_percentile_drop_alone_is_not_enough():
    assert classify(-0.2, -30.0, inverted=False) == "decoration"


def test_small_change_in_either_direction_is_decoration():
    assert classify(0.4, 8.0, inverted=False) == "decoration"
    assert classify(-0.49, -9.9, inverted=False) == "decoration"


def test_removing_a_leg_and_improving_marks_it_harmful():
    assert classify(0.7, 15.0, inverted=False) == "harmful"


def test_inverted_cell_earns_its_place_by_raising_z():
    """A6 ADDS a gate, so a rise is the pass condition."""
    assert classify(0.6, 12.0, inverted=True) == "load-bearing"
    assert classify(-0.6, -12.0, inverted=True) == "harmful"


def test_calibration_matches_the_median_structural_stop_width():
    a0 = pd.DataFrame({"stop_pts": [4.0, 6.0, 8.0]})     # median 6.0
    h1_atr = np.array([2.0, 3.0, 4.0])                   # median 3.0
    assert calibrate_zone_stop_mult(a0, h1_atr) == pytest.approx(2.0, abs=1e-9)


def test_calibration_ignores_nan_atr_values():
    a0 = pd.DataFrame({"stop_pts": [10.0]})
    h1_atr = np.array([np.nan, 5.0, np.nan])
    assert calibrate_zone_stop_mult(a0, h1_atr) == pytest.approx(2.0, abs=1e-9)


def test_cells_cover_the_spec_and_only_a6_is_inverted():
    names = [c["name"] for c in CELLS]
    assert names == ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]
    assert [c["name"] for c in CELLS if c["inverted"]] == ["A6"]
    assert CELLS[0]["overrides"] == {}


def test_censored_fraction_counts_pf_pinned_at_the_ceiling():
    null_pf = np.array([1.1, 10.0, 0.9, 10.0, 1.4, 2.0, 10.0, 0.8, 1.2, 1.0])
    assert censored_fraction(null_pf) == pytest.approx(0.3, abs=1e-9)


def test_censored_fraction_is_zero_when_nothing_hits_the_ceiling():
    null_pf = np.array([1.1, 0.9, 1.4, 2.0, 0.8, 1.2, 1.0])
    assert censored_fraction(null_pf) == pytest.approx(0.0, abs=1e-9)
