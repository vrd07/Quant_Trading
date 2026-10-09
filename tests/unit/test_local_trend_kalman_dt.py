"""LocalTrendKalman's elapsed-time handling.

Three properties. The first protects everything already shipped; the second is
the bug the feature exists to prevent; the third is the behaviour it is meant
to produce.
"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.kalman import LocalTrendKalman

BAR = pd.Timedelta("15min")


def _weekend_frame(prices, gap_at, gap="48h"):
    idx = pd.date_range("2026-01-02", periods=len(prices), freq=BAR, tz="UTC")
    idx = pd.DatetimeIndex(np.concatenate(
        [idx[:gap_at], (idx[gap_at:] + pd.Timedelta(gap)).to_numpy()]))
    return pd.Series(prices, index=idx), pd.Series(0.004 * prices, index=idx)


def test_dt_none_is_bit_identical_to_all_ones():
    """The default path must not move. Every shipped backtest number depends
    on it, so this is asserted bitwise rather than approximately."""
    rng = np.random.default_rng(0)
    close = pd.Series(2700 * np.exp(np.cumsum(rng.normal(0, 0.001, 2000))))
    atr = pd.Series(0.004 * close.to_numpy())
    kf = LocalTrendKalman()
    a = kf.filter_frame(close, atr).to_numpy()
    b = kf.filter_frame(close, atr, np.ones(len(close))).to_numpy()
    assert np.array_equal(a, b)


def test_bar_widths_reads_the_gap_off_the_index():
    close, _ = _weekend_frame(2700 * np.ones(400), gap_at=300)
    d = LocalTrendKalman.bar_widths(close.index, BAR)
    assert d[0] == 1.0 and (d[1:300] == 1.0).all()
    assert d[300] == pytest.approx(193.0)          # 48h of 15m bars, plus the one


def test_the_gap_does_not_invert_the_trend_across_a_weekend():
    """⚠️ THE BUG THIS FEATURE MUST NOT HAVE. Putting the elapsed gap into F as
    well as Q makes the filter predict 193 bars of accumulated drift over a
    weekend. That move never happens, so the innovation is enormous and gets
    charged to velocity — on an UNBROKEN uptrend the trend estimate flipped
    +0.572 -> -0.566, which would invert the trend gate every Monday. The gap
    belongs in Q only."""
    ramp = 2700 * np.exp(np.arange(400) * 0.0002)
    close, atr = _weekend_frame(ramp, gap_at=300)
    dt = LocalTrendKalman.bar_widths(close.index, BAR)
    v = LocalTrendKalman().filter_frame(close, atr, dt)["velocity"]
    assert v.iloc[300] > 0
    assert v.iloc[300] == pytest.approx(v.iloc[299], rel=0.05)


def test_the_gap_widens_the_velocity_posterior():
    """The behaviour it IS for: uncertainty accrues while the market is shut, so
    the filter comes back less sure of the trend than it went away."""
    rng = np.random.default_rng(1)
    px = 2700 * np.exp(np.cumsum(rng.normal(0, 0.0008, 400)))
    close, atr = _weekend_frame(px, gap_at=300)
    dt = LocalTrendKalman.bar_widths(close.index, BAR)
    kf = LocalTrendKalman()
    flat = kf.filter_frame(close, atr)["innov_z"].abs()
    gapped = kf.filter_frame(close, atr, dt)["innov_z"].abs()
    # A wider prior makes the same surprise less surprising in sigma units.
    assert gapped.iloc[300] < flat.iloc[300]
