"""The adverse-pool proximity test.

The single most dangerous defect here is transposed sides: a gate that measures
the TARGET side instead of the adverse side still produces a plausible-looking
trade reduction and would pass a careless review. Both directions are asserted
explicitly.
"""
import pandas as pd
import pytest

from src.microstructure.liquidity_levels import LevelParams
from src.microstructure.pool_gate import nearest_adverse_pool_atr

# Small params so fixtures stay readable. The production gate uses DEFAULTS.
SMALL = LevelParams(scan_bars=60, atr_pctile_window=30, touch_lookback=30)
NEED = SMALL.scan_bars + 2 * SMALL.pivot_n + SMALL.atr_period  # 84


def _flat_bars(n=200, price=2000.0):
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {"open": price, "high": price + 0.5, "low": price - 0.5,
         "close": price, "volume": 100.0},
        index=idx,
    )


def _with_swing_high(bars, at, height):
    """Make bar `at` a confirmed un-swept swing high `height` above the flat band."""
    out = bars.copy()
    out.iloc[at, out.columns.get_loc("high")] = out["close"].iloc[at] + height
    return out


def _with_swing_low(bars, at, depth):
    out = bars.copy()
    out.iloc[at, out.columns.get_loc("low")] = out["close"].iloc[at] - depth
    return out


def test_pool_above_is_adverse_for_sell_only():
    bars = _with_swing_high(_flat_bars(), at=150, height=8.0)
    entry = float(bars["close"].iloc[-1])
    atr = 1.0

    sell = nearest_adverse_pool_atr(bars, entry, side_is_buy=False, atr=atr, params=SMALL)
    buy = nearest_adverse_pool_atr(bars, entry, side_is_buy=True, atr=atr, params=SMALL)

    assert sell is not None and sell > 0, "a pool above must be adverse for a SELL"
    assert buy is None, "a pool above must NOT be adverse for a BUY"


def test_pool_below_is_adverse_for_buy_only():
    bars = _with_swing_low(_flat_bars(), at=150, depth=8.0)
    entry = float(bars["close"].iloc[-1])
    atr = 1.0

    buy = nearest_adverse_pool_atr(bars, entry, side_is_buy=True, atr=atr, params=SMALL)
    sell = nearest_adverse_pool_atr(bars, entry, side_is_buy=False, atr=atr, params=SMALL)

    assert buy is not None and buy > 0, "a pool below must be adverse for a BUY"
    assert sell is None, "a pool below must NOT be adverse for a SELL"


def test_nearer_pool_reports_smaller_distance():
    # NOTE: heights adjusted from the brief's 4.0/12.0 to 3.0/6.0. With SMALL params
    # the internally-computed ATR at the tail is ~1.0, and build_choice_set drops any
    # level beyond max_dist_atr(8.0) * atr (~8.0-8.2) from the choice set entirely. A
    # height of 12.0 lands outside that band, so the "far" call returns None instead
    # of a genuinely larger distance -- verified empirically via a throwaway probe
    # script before writing this. 3.0 / 6.0 both stay inside the band (confirmed:
    # single level each, at the injected price) while remaining clearly distinct, so
    # the test still compares two different injected distances as intended.
    near = _with_swing_high(_flat_bars(), at=150, height=3.0)
    far = _with_swing_high(_flat_bars(), at=150, height=6.0)
    entry_n = float(near["close"].iloc[-1])
    entry_f = float(far["close"].iloc[-1])

    d_near = nearest_adverse_pool_atr(near, entry_n, False, 1.0, SMALL)
    d_far = nearest_adverse_pool_atr(far, entry_f, False, 1.0, SMALL)

    assert d_near is not None and d_far is not None
    assert d_near < d_far


def test_atr_normalisation_halves_distance_when_atr_doubles():
    bars = _with_swing_high(_flat_bars(), at=150, height=8.0)
    entry = float(bars["close"].iloc[-1])

    d1 = nearest_adverse_pool_atr(bars, entry, False, 1.0, SMALL)
    d2 = nearest_adverse_pool_atr(bars, entry, False, 2.0, SMALL)

    assert d1 is not None and d2 is not None
    assert d2 == pytest.approx(d1 / 2.0, rel=1e-9)


def test_short_frame_returns_none():
    bars = _with_swing_high(_flat_bars(n=NEED - 1), at=40, height=8.0)
    entry = float(bars["close"].iloc[-1])
    assert nearest_adverse_pool_atr(bars, entry, False, 1.0, SMALL) is None


def test_non_positive_atr_returns_none():
    bars = _with_swing_high(_flat_bars(), at=150, height=8.0)
    entry = float(bars["close"].iloc[-1])
    assert nearest_adverse_pool_atr(bars, entry, False, 0.0, SMALL) is None
    assert nearest_adverse_pool_atr(bars, entry, False, -1.0, SMALL) is None
    assert nearest_adverse_pool_atr(bars, entry, False, None, SMALL) is None


def test_no_pools_at_all_returns_none():
    bars = _flat_bars()
    entry = float(bars["close"].iloc[-1])
    assert nearest_adverse_pool_atr(bars, entry, True, 1.0, SMALL) is None
    assert nearest_adverse_pool_atr(bars, entry, False, 1.0, SMALL) is None


def test_malformed_frame_returns_none_rather_than_raising():
    bars = _flat_bars().reset_index(drop=True)  # loses the required DatetimeIndex
    assert nearest_adverse_pool_atr(bars, 2000.0, True, 1.0, SMALL) is None


def test_deterministic():
    bars = _with_swing_high(_flat_bars(), at=150, height=8.0)
    entry = float(bars["close"].iloc[-1])
    first = nearest_adverse_pool_atr(bars, entry, False, 1.0, SMALL)
    for _ in range(3):
        assert nearest_adverse_pool_atr(bars, entry, False, 1.0, SMALL) == first
