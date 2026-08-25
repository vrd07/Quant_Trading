"""Unit tests for src/microstructure/profile_context.py — synthetic profiles only."""
import numpy as np
import pytest

from src.microstructure import volume_profile as vp
from src.microstructure import profile_context as pc


def prof_from(volumes, min_row=1000, row_size=0.10):
    v = np.asarray(volumes, dtype=float)
    h = vp.Histogram(min_row, v, row_size, accepted=int(v.sum()), rejected=0)
    return vp.build_profile(h, vp.ProfileParams(row_size=row_size))


class TestSkew:
    def test_symmetric_profile_has_exactly_zero_skew(self):
        h = vp.Histogram(1000, np.array([1.0, 4.0, 9.0, 4.0, 1.0]), 0.10, 19, 0)
        assert pc.profile_skew(h) == pytest.approx(0.0, abs=1e-12)

    def test_mirrored_profile_has_exactly_negated_skew(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v[::-1].copy(), 0.10, 27, 0))
        assert a == pytest.approx(-b, abs=1e-12)

    def test_invariant_under_uniform_price_rescale(self):
        """Dimensionless: this is why skew beats POC position."""
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v, 1.00, 27, 0))
        assert a == pytest.approx(b, rel=1e-12)

    def test_invariant_under_uniform_volume_rescale(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v * 1000.0, 0.10, 27000, 0))
        assert a == pytest.approx(b, rel=1e-12)

    def test_degenerate_profiles_return_none(self):
        assert pc.profile_skew(vp.Histogram(1000, np.array([5.0]), 0.10, 5, 0)) is None
        assert pc.profile_skew(vp.Histogram(1000, np.zeros(0), 0.10, 0, 0)) is None


class TestShapeClassification:
    def params(self, t=0.30):
        return pc.ContextParams(skew_threshold=t)

    def test_mass_at_highs_with_thin_tail_below_is_P(self):
        """SIGN CONVENTION PIN. Mass high + tail below => negative skew => P.

        A published indicator (BackQuant Volume Profile Skew) reads this same
        geometry as bullish 'accumulation' via the opposite sign. An inversion
        here would flip every regime call on the chart, so this is asserted
        directly rather than inferred.
        """
        v = np.array([1.0, 1.0, 1.0, 2.0, 20.0, 25.0, 22.0])
        prof = prof_from(v)
        read = pc.classify_shape(prof, self.params())
        assert read.skew < 0
        assert read.shape == "P"

    def test_mass_at_lows_with_thin_tail_above_is_b(self):
        v = np.array([22.0, 25.0, 20.0, 2.0, 1.0, 1.0, 1.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert read.skew > 0
        assert read.shape == "b"

    def test_symmetric_profile_is_D(self):
        v = np.array([1.0, 5.0, 12.0, 20.0, 12.0, 5.0, 1.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert read.shape == "D"

    def test_profile_with_too_few_rows_is_unclassified(self):
        read = pc.classify_shape(prof_from([3.0, 4.0]), self.params())
        assert read.shape == "UNCLASSIFIED"
        assert read.skew is None

    def test_corroborating_measures_are_populated(self):
        v = np.array([1.0, 1.0, 1.0, 2.0, 20.0, 25.0, 22.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert 0.0 <= read.poc_position <= 1.0
        assert 0.0 < read.va_width_frac <= 1.0
        assert read.poc_position > 0.5          # POC sits high in a P

    def test_threshold_controls_the_D_band(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0, 4.0, 2.0])
        loose = pc.classify_shape(prof_from(v), self.params(t=5.0))
        tight = pc.classify_shape(prof_from(v), self.params(t=0.001))
        assert loose.shape == "D"
        assert tight.shape in ("P", "b")


def make_profile(val, vah, low=None, high=None, row_size=0.10):
    """Construct a Profile with EXACT levels, bypassing build_profile.

    These tests exercise the classifiers, not the value-area search. Trying to
    synthesise a histogram whose 70% value area lands on chosen rows does not
    work -- expansion stops as soon as it clears the target, so a uniform block
    yields a value area narrower than the block -- and it would silently be
    re-testing value_area instead of the classifier under test.

    Row indices are derived from the prices so the dataclass stays coherent:
    val is a row's LOWER edge and vah is a row's UPPER edge (spec section 8.3).
    """
    low = val - 0.5 if low is None else low
    high = vah + 0.5 if high is None else high
    lo_row = vp.row_index(low, row_size)
    hi_row = vp.row_index(high, row_size)
    hist = vp.Histogram(lo_row, np.ones(hi_row - lo_row + 1), row_size,
                        accepted=hi_row - lo_row + 1, rejected=0)
    return vp.Profile(
        hist=hist,
        poc_row=vp.row_index((val + vah) / 2.0, row_size),
        val_row=vp.row_index(val, row_size),
        vah_row=vp.row_index(vah, row_size) - 1,
        vpoc=(val + vah) / 2.0,
        val=val, vah=vah, low=low, high=high,
    )


class TestOpenType:
    """Prior session: low 1000.0, VAL 1001.0, VAH 1003.0, high 1004.0."""

    def prior(self):
        return make_profile(val=1001.0, vah=1003.0, low=1000.0, high=1004.0)

    def test_open_above_the_prior_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.high + 0.5, p) == "OPEN_ABOVE_RANGE"

    def test_open_above_value_but_inside_range(self):
        p = self.prior()
        assert pc.classify_open_type((p.vah + p.high) / 2, p) == "OPEN_ABOVE_VA"

    def test_open_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type((p.val + p.vah) / 2, p) == "OPEN_INSIDE_VA"

    def test_open_below_value_but_inside_range(self):
        p = self.prior()
        assert pc.classify_open_type((p.low + p.val) / 2, p) == "OPEN_BELOW_VA"

    def test_open_below_the_prior_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.low - 0.5, p) == "OPEN_BELOW_RANGE"

    def test_open_exactly_on_vah_counts_as_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type(p.vah, p) == "OPEN_INSIDE_VA"

    def test_open_exactly_on_val_counts_as_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type(p.val, p) == "OPEN_INSIDE_VA"

    def test_open_exactly_on_prior_high_is_above_value_not_above_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.high, p) == "OPEN_ABOVE_VA"


class TestValueMigration:
    def test_higher_when_there_is_no_overlap(self):
        prior = make_profile(val=1000.0, vah=1001.0)
        today = make_profile(val=1002.0, vah=1003.0)
        assert pc.classify_value_migration(today, prior) == "HIGHER"

    def test_lower_when_there_is_no_overlap(self):
        prior = make_profile(val=1002.0, vah=1003.0)
        today = make_profile(val=1000.0, vah=1001.0)
        assert pc.classify_value_migration(today, prior) == "LOWER"

    def test_overlapping_higher(self):
        prior = make_profile(val=1000.0, vah=1001.0)
        today = make_profile(val=1000.5, vah=1001.5)
        assert pc.classify_value_migration(today, prior) == "OVERLAPPING_HIGHER"

    def test_overlapping_lower(self):
        prior = make_profile(val=1000.5, vah=1001.5)
        today = make_profile(val=1000.0, vah=1001.0)
        assert pc.classify_value_migration(today, prior) == "OVERLAPPING_LOWER"

    def test_inside(self):
        prior = make_profile(val=1000.0, vah=1002.0)
        today = make_profile(val=1000.5, vah=1001.5)
        assert pc.classify_value_migration(today, prior) == "INSIDE"

    def test_engulfing(self):
        prior = make_profile(val=1000.5, vah=1001.5)
        today = make_profile(val=1000.0, vah=1002.0)
        assert pc.classify_value_migration(today, prior) == "ENGULFING"

    def test_identical_value_areas_are_inside(self):
        prior = make_profile(val=1000.0, vah=1001.0)
        today = make_profile(val=1000.0, vah=1001.0)
        assert pc.classify_value_migration(today, prior) == "INSIDE"


class TestRegime:
    def params(self):
        return pc.ContextParams(skew_threshold=0.30, regime_min_elapsed_pct=0.50)

    def test_shape_maps_to_regime(self):
        p = self.params()
        assert pc.classify_regime("D", 1.0, p, False) == "BALANCED"
        assert pc.classify_regime("P", 1.0, p, False) == "OUT_OF_BALANCE_UP"
        assert pc.classify_regime("b", 1.0, p, False) == "OUT_OF_BALANCE_DOWN"
        assert pc.classify_regime("UNCLASSIFIED", 1.0, p, False) == "UNCLEAR"

    def test_developing_session_below_the_elapsed_floor_is_forming(self):
        """Every session looks like a P or a b before it has traded both ways."""
        p = self.params()
        assert pc.classify_regime("P", 0.20, p, is_developing=True) == "FORMING"
        assert pc.classify_regime("P", 0.80, p, is_developing=True) == "OUT_OF_BALANCE_UP"

    def test_completed_sessions_ignore_the_elapsed_floor(self):
        p = self.params()
        assert pc.classify_regime("P", 0.10, p, is_developing=False) == "OUT_OF_BALANCE_UP"
