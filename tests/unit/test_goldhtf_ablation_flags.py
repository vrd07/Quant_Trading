"""Ablation flags must change exactly one leg each, and change nothing when off."""
import pytest

from scripts.simulate_goldhtf_ea import INP, check_entry_confirmation


@pytest.fixture(autouse=True)
def restore_inp():
    saved = dict(INP)
    yield
    INP.clear()
    INP.update(saved)


def _bars():
    """9 bars; index 0 is oldest. With EntryShift=1 the pattern sits at j-2 and the
    directional confirm at j-1, so we address them from the end."""
    o = [0.0] * 9
    h = [0.0] * 9
    l = [0.0] * 9
    c = [0.0] * 9
    return o, h, l, c


def test_pattern_leg_blocks_entry_when_no_pattern_present():
    """Baseline: flat bars form no pattern, so nothing fires."""
    INP["NoPatternLeg"] = False
    INP["NoConfirm"] = False
    o, h, l, c = _bars()
    for i in range(9):
        o[i], h[i], l[i], c[i] = 100.0, 100.5, 99.5, 100.0
    sig, why = check_entry_confirmation(o, h, l, c, 8, 1, 1)
    assert sig == 0
    assert why == "no_pattern"


def test_no_pattern_leg_fires_on_confirm_alone():
    """Ablating the pattern leg leaves the directional close-confirm as the trigger."""
    INP["NoPatternLeg"] = True
    INP["NoConfirm"] = False
    o, h, l, c = _bars()
    for i in range(9):
        o[i], h[i], l[i], c[i] = 100.0, 100.5, 99.5, 100.0
    o[7], c[7] = 100.0, 100.4          # bar j-1 (j=8, shift=1) closes green
    assert check_entry_confirmation(o, h, l, c, 8, 1, 1) == (1, "fired")


def test_no_pattern_leg_still_blocked_by_a_red_confirm_bar():
    INP["NoPatternLeg"] = True
    INP["NoConfirm"] = False
    o, h, l, c = _bars()
    for i in range(9):
        o[i], h[i], l[i], c[i] = 100.0, 100.5, 99.5, 100.0
    o[7], c[7] = 100.4, 100.0          # red confirm bar
    sig, why = check_entry_confirmation(o, h, l, c, 8, 1, 1)
    assert sig == 0
    assert why == "no_close_confirm"


def test_no_confirm_fires_regardless_of_confirm_bar_colour():
    INP["NoPatternLeg"] = True
    INP["NoConfirm"] = True
    o, h, l, c = _bars()
    for i in range(9):
        o[i], h[i], l[i], c[i] = 100.0, 100.5, 99.5, 100.0
    o[7], c[7] = 100.4, 100.0          # red, and it must not matter
    assert check_entry_confirmation(o, h, l, c, 8, 1, 1) == (1, "fired")


def test_zone_direction_mismatch_still_rejects_with_every_flag_on():
    """Ablating the pattern must NOT bypass the trend/zone agreement check."""
    INP["NoPatternLeg"] = True
    INP["NoConfirm"] = True
    o, h, l, c = _bars()
    for i in range(9):
        o[i], h[i], l[i], c[i] = 100.0, 100.5, 99.5, 100.0
    sig, why = check_entry_confirmation(o, h, l, c, 8, 1, -1)
    assert sig == 0
    assert why == "trend_vs_zone_mismatch"


def test_flags_default_off():
    for key in ("NoTrendLeg", "NoMTFLeg", "NoPatternLeg", "NoConfirm", "SkipRanging"):
        assert INP[key] is False
