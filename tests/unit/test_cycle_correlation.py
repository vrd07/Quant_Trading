"""Macro-correlation size veto (§6.3) and multi-timeframe alignment (§6.4)."""
import numpy as np
import pandas as pd
import pytest

from src.cycles.correlation import CorrelationFilter, mtf_alignment


def s(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values),
                                                 freq="15min", tz="UTC"), dtype=float)


class TestCorrelationFilter:
    def test_uncorrelated_pair_is_full_size(self):
        rng = np.random.default_rng(1)
        a, b = s(np.cumsum(rng.standard_normal(100))), s(np.cumsum(rng.standard_normal(100)))
        assert CorrelationFilter().evaluate(a, b).size_multiplier == pytest.approx(1.0)

    def test_strongly_correlated_pair_is_halved(self):
        rng = np.random.default_rng(2)
        base = np.cumsum(rng.standard_normal(100))
        a, b = s(base), s(base * 2.0 + 0.01 * rng.standard_normal(100))
        veto = CorrelationFilter().evaluate(a, b)
        assert veto.correlated is True
        assert veto.size_multiplier == pytest.approx(0.5)

    def test_strong_negative_correlation_also_vetoes_when_abs(self):
        """Gold vs DXY: the spec gates on |correlation| > 0.7, not the sign."""
        rng = np.random.default_rng(3)
        base = np.cumsum(rng.standard_normal(100))
        veto = CorrelationFilter(use_abs=True).evaluate(s(base), s(-base))
        assert veto.correlated is True

    def test_signed_mode_ignores_negative_correlation(self):
        """BTC vs NQ: the spec gates on correlation > 0.8, signed."""
        rng = np.random.default_rng(4)
        base = np.cumsum(rng.standard_normal(100))
        f = CorrelationFilter(threshold=0.8, use_abs=False)
        assert f.evaluate(s(base), s(-base)).correlated is False

    def test_missing_proxy_is_pass_through(self):
        veto = CorrelationFilter().evaluate(s(np.arange(100.0)), None)
        assert veto.correlated is False
        assert veto.size_multiplier == pytest.approx(1.0)
        assert np.isnan(veto.value)

    def test_short_history_is_pass_through(self):
        a = s(np.arange(5.0))
        assert CorrelationFilter(window=20).evaluate(a, a).size_multiplier == pytest.approx(1.0)

    def test_uses_returns_not_levels(self):
        """Two independent random walks are spuriously correlated in LEVELS.
        Correlating returns is the only version that means anything."""
        rng = np.random.default_rng(5)
        a = s(100 + np.cumsum(np.abs(rng.standard_normal(400))))
        b = s(100 + np.cumsum(np.abs(rng.standard_normal(400))))
        assert CorrelationFilter(window=200).evaluate(a, b).correlated is False


class TestMTFAlignment:
    def test_all_bullish_allows_long_at_full_size(self):
        up = s(np.arange(300.0))
        al = mtf_alignment(up, up, up)
        assert al.bias == 1 and al.allow_long and al.size_multiplier == pytest.approx(1.0)

    def test_strong_bullish_anchor_bans_short(self):
        up, down = s(np.arange(300.0)), s(-np.arange(300.0))
        al = mtf_alignment(down, up, up)
        assert al.allow_short is False

    def test_conflicting_anchors_halve_size(self):
        up, down = s(np.arange(300.0)), s(-np.arange(300.0))
        assert mtf_alignment(up, up, down).size_multiplier == pytest.approx(0.5)

    def test_flat_anchors_are_neutral_and_permit_both(self):
        flat = s(np.full(300, 100.0))
        al = mtf_alignment(flat, flat, flat)
        assert al.bias == 0 and al.allow_long and al.allow_short

    def test_missing_anchor_is_neutral(self):
        up = s(np.arange(300.0))
        al = mtf_alignment(up, None, None)
        assert al.bias == 0 and al.size_multiplier == pytest.approx(1.0)
