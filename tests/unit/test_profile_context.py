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
