"""Causal DWT: filter-bank correctness, exact group delay, and strict causality."""
import numpy as np
import pandas as pd
import pytest

from src.cycles.causal_dwt import CausalDWT, WAVELET_FILTERS


class TestFilterBankProperties:
    """Catches a coefficient transcription typo, which would otherwise show up
    only as a subtly wrong trend line that still looks plausible on a chart."""

    @pytest.mark.parametrize("name", ["db4", "sym8"])
    def test_sums_to_sqrt2(self, name):
        assert WAVELET_FILTERS[name].sum() == pytest.approx(np.sqrt(2.0), abs=1e-12)

    @pytest.mark.parametrize("name", ["db4", "sym8"])
    def test_unit_energy(self, name):
        h = WAVELET_FILTERS[name]
        assert float(h @ h) == pytest.approx(1.0, abs=1e-12)

    @pytest.mark.parametrize("name", ["db4", "sym8"])
    def test_even_shift_orthogonality(self, name):
        h = WAVELET_FILTERS[name]
        for k in range(1, len(h) // 2):
            overlap = float(h[2 * k:] @ h[:-2 * k])
            assert overlap == pytest.approx(0.0, abs=1e-12)

    def test_tap_counts(self):
        assert len(WAVELET_FILTERS["db4"]) == 8
        assert len(WAVELET_FILTERS["sym8"]) == 16


class TestGroupDelay:
    def test_symmetric_filter_has_linear_phase_delay(self):
        """Sanity-check the group-delay formula itself against a case with a
        known closed-form answer: a symmetric FIR has delay (N-1)/2 at all freqs."""
        dwt = CausalDWT(wavelet="db4", level=1)
        sym = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
        sym = sym / sym.sum()
        for period in (10.0, 20.0, 40.0):
            assert dwt._group_delay_of(sym, period) == pytest.approx(2.0, abs=1e-9)

    def test_delay_is_positive_and_grows_with_level(self):
        d1 = CausalDWT(wavelet="db4", level=1).group_delay(32.0)
        d2 = CausalDWT(wavelet="db4", level=2).group_delay(32.0)
        assert 0.0 < d1 < d2

    def test_delay_matches_empirical_sine_lag(self):
        """The analytic group delay must equal the lag that actually maximises
        cross-correlation between a sine input and the filtered output."""
        period = 32.0
        n = 4000
        t = np.arange(n)
        x = np.sin(2 * np.pi * t / period)
        dwt = CausalDWT(wavelet="db4", level=2)
        y = dwt.transform(x).trend
        valid = slice(dwt.warmup + 200, n)
        lags = np.arange(0, 40)
        corr = [np.corrcoef(x[valid][: n - 40 - dwt.warmup - 200],
                            y[valid][lag: lag + n - 40 - dwt.warmup - 200])[0, 1]
                for lag in lags]
        empirical = float(lags[int(np.argmax(corr))])
        assert abs(dwt.group_delay(period) - empirical) <= 1.5


class TestCausality:
    def test_output_at_t_never_changes_when_future_arrives(self):
        """THE causality test. If this fails, every backtest built on top is void."""
        rng = np.random.default_rng(7)
        x = np.cumsum(rng.standard_normal(600))
        dwt = CausalDWT(wavelet="db4", level=2)
        full = dwt.transform(x).trend
        for t in (150, 301, 450, 599):
            prefix = dwt.transform(x[: t + 1]).trend
            assert prefix[t] == pytest.approx(full[t], abs=1e-12)

    def test_trend_ignores_appended_shock(self):
        rng = np.random.default_rng(11)
        x = np.cumsum(rng.standard_normal(300))
        dwt = CausalDWT(wavelet="sym8", level=3)
        before = dwt.transform(x).trend[-1]
        shocked = np.concatenate([x, [x[-1] + 500.0]])
        after = dwt.transform(shocked).trend[-2]
        assert before == pytest.approx(after, abs=1e-12)


class TestTransform:
    def test_warmup_region_is_nan(self):
        dwt = CausalDWT(wavelet="db4", level=2)
        out = dwt.transform(np.arange(200, dtype=float))
        assert np.all(np.isnan(out.trend[: dwt.warmup]))
        assert not np.isnan(out.trend[dwt.warmup])

    def test_constant_input_is_preserved(self):
        """Low-pass gain must be exactly 1 at DC after normalisation."""
        dwt = CausalDWT(wavelet="db4", level=2)
        out = dwt.transform(np.full(200, 42.0))
        assert out.trend[-1] == pytest.approx(42.0, abs=1e-9)
        assert out.detail[-1] == pytest.approx(0.0, abs=1e-9)

    def test_detail_is_residual(self):
        rng = np.random.default_rng(3)
        x = np.cumsum(rng.standard_normal(400))
        out = CausalDWT("db4", 2).transform(x)
        np.testing.assert_allclose(out.trend[50:] + out.detail[50:], x[50:], atol=1e-9)

    def test_transform_series_preserves_index(self):
        idx = pd.date_range("2026-01-01", periods=200, freq="15min", tz="UTC")
        s = pd.Series(np.cumsum(np.random.default_rng(5).standard_normal(200)), index=idx)
        df = CausalDWT("db4", 2).transform_series(s)
        assert list(df.columns) == ["trend", "detail"]
        assert df.index.equals(idx)

    def test_too_short_input_is_all_nan(self):
        dwt = CausalDWT("sym8", 3)
        out = dwt.transform(np.arange(5, dtype=float))
        assert np.all(np.isnan(out.trend))
