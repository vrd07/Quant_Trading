# tests/unit/test_goldhtf_entry_ablation.py
"""Pre-committed threshold logic and the A2 stop calibration."""
import numpy as np
import pandas as pd
import pytest

from scripts.research_goldhtf_entry_ablation import (
    CELLS, calibrate_zone_stop_mult, censored_fraction, censoring_summary_line,
    classify)


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


def test_classify_reports_no_trades_instead_of_decoration_when_deltas_are_nan():
    """A zero-trade cell yields NaN stats, so both deltas are NaN and every
    threshold comparison is False -- that must not silently read as 'decoration',
    which is a measured verdict this cell never earned."""
    assert classify(float("nan"), float("nan"), inverted=False) == "no trades"
    assert classify(float("nan"), -12.0, inverted=False) == "no trades"
    assert classify(-0.6, float("nan"), inverted=False) == "no trades"
    assert classify(float("nan"), float("nan"), inverted=True) == "no trades"


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


def test_calibration_raises_when_a0_produced_no_trades():
    """Empty A0 -> np.median gives NaN -> zmult would be NaN, and zone_free_stop's
    `mult <= 0` guard does not catch NaN. Must raise here instead, before a NaN
    multiplier can ever reach the simulator."""
    a0 = pd.DataFrame({"stop_pts": []})
    h1_atr = np.array([2.0, 3.0, 4.0])
    with pytest.raises(ValueError):
        calibrate_zone_stop_mult(a0, h1_atr)


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


def test_censored_fraction_excludes_pf_genuinely_above_the_ceiling():
    """A draw with a real PF above 10 (small but nonzero losses) is a different
    phenomenon from the zero-loss sentinel and must not be counted as censored --
    only exact equality with 10.0 counts."""
    null_pf = np.array([10.0, 10.5, 11.0, 1.2])
    assert censored_fraction(null_pf) == pytest.approx(0.25, abs=1e-9)


def test_censoring_summary_all_clear_when_every_finite_cell_is_clean():
    assert censoring_summary_line([0.0, 0.002, 0.0, np.nan]) == (
        "All cells: null censoring < 1%, z is well-behaved.")


def test_censoring_summary_warns_when_material():
    msg = censoring_summary_line([0.0, 0.10, np.nan])
    assert msg.startswith("**WARNING")
    assert "10.0%" in msg


def test_censoring_summary_does_not_reassure_when_all_nan():
    """An all-NaN censoring column (no cell produced trades) is 'we don't know',
    not 'clean' -- the all-clear message must not fire."""
    msg = censoring_summary_line([np.nan, np.nan, np.nan])
    assert msg != "All cells: null censoring < 1%, z is well-behaved."
    assert not msg.startswith("**WARNING")
    assert "undefined" in msg
