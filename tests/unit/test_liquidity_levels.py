"""Unit tests for src/microstructure/liquidity_levels.py — synthetic frames only."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import liquidity_levels as ll


def make_bars(highs, lows, closes=None, opens=None, start="2026-01-05 00:00", freq="15min"):
    """Build a 15m OHLC frame from explicit highs/lows. Closes default to mid."""
    n = len(highs)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    if closes is None:
        closes = (highs + lows) / 2.0
    if opens is None:
        opens = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                         "close": np.asarray(closes, dtype=float),
                         "volume": np.full(n, 100.0)}, index=idx)


class TestPrimitives:
    def test_wilder_atr_matches_recursion(self):
        bars = make_bars([10, 12, 11, 13], [8, 9, 9, 10], closes=[9, 11, 10, 12])
        atr = ll.wilder_atr(bars.high.to_numpy(), bars.low.to_numpy(),
                            bars.close.to_numpy(), period=14)
        # bar 0 seeds with its own true range (high - low)
        assert atr[0] == pytest.approx(2.0)
        # bar 1 TR = max(12-9, |12-9|, |9-9|) = 3.0 -> 2.0 + (3.0-2.0)/14
        assert atr[1] == pytest.approx(2.0 + 1.0 / 14)
        assert len(atr) == 4

    def test_ema_seeds_with_first_value(self):
        out = ll.ema(np.array([10.0, 20.0, 30.0]), period=3)
        alpha = 2.0 / (3 + 1)
        assert out[0] == pytest.approx(10.0)
        assert out[1] == pytest.approx(10.0 + alpha * (20.0 - 10.0))

    def test_rolling_pctile_is_fraction_at_or_below(self):
        vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        out = ll.rolling_pctile(vals, window=3)
        # at index 4 the window is [3,4,5]; 5 is >= all 3 -> 1.0
        assert out[4] == pytest.approx(1.0)
        # at index 0 the window is just [1] -> 1.0
        assert out[0] == pytest.approx(1.0)
        assert np.all((out >= 0.0) & (out <= 1.0))

    def test_rolling_pctile_pins_window_length_with_nonmonotonic_data(self):
        # Monotonic data can't distinguish a genuine window boundary from an
        # off-by-one -- every index yields 1.0 regardless of window size. This
        # data is non-monotonic and the chosen index/window produce a real
        # fraction that only holds for exactly this window length.
        vals = np.array([5.0, 1.0, 4.0, 2.0, 8.0, 3.0, 9.0, 6.0])
        out = ll.rolling_pctile(vals, window=5)
        # window at index 5 (lo = 5-5+1 = 1) is indices[1..5] = [1,4,2,8,3];
        # 3 of those 5 values (1, 2, 3) are <= 3.0 -> 3/5 = 0.6
        assert out[5] == pytest.approx(0.6)
        # pins the exact window length: window=4 drops index 1 (value 1) ->
        # 2/4 = 0.5; window=6 adds index 0 (value 5, > 3.0, doesn't count) ->
        # 3/6 = 0.5. An off-by-one in either direction lands on 0.5, not 0.6.
        assert out[5] != pytest.approx(0.5)
        assert np.all((out > 0.0) & (out <= 1.0))


class TestPivotDetection:
    def test_pivot_is_local_max_over_2n_plus_1(self):
        # index 5 is the peak of an 11-bar window (n=5)
        highs = [1, 2, 3, 4, 5, 9, 5, 4, 3, 2, 1]
        lows = [0] * 11
        is_ph, is_pl = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert is_ph[5]
        assert is_ph.sum() == 1

    def test_pivot_requires_n_bars_on_each_side(self):
        # the peak sits at index 3 with only 3 bars to its left -> cannot confirm at n=5
        highs = [1, 2, 3, 9, 3, 2, 1, 0, 0, 0, 0]
        lows = [0] * 11
        is_ph, _ = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert not is_ph[3]

    def test_swing_low_mirror(self):
        lows = [9, 8, 7, 6, 5, 1, 5, 6, 7, 8, 9]
        highs = [10] * 11
        _, is_pl = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert is_pl[5]
        assert is_pl.sum() == 1

    def test_pivot_false_when_left_half_has_a_higher_bar(self):
        # 21 bars, n=5 -> the loop visits interior indices 5..15 (11 candidates,
        # not just the single index a bare 2n+1-length array would offer).
        # Candidate i=10 is the max of its own right half (10..15, all baseline)
        # but the LEFT boundary bar at i-n=5 is higher -> must NOT be a pivot.
        # This pins the left-slice bound exactly: an off-by-one that drops
        # index i-n (e.g. high[i-n+1:i+n+1]) would exclude the 50 at index 5,
        # leaving 20 as the truncated-window max, and wrongly mark it a pivot.
        highs = [1.0] * 21
        highs[5] = 50.0
        highs[10] = 20.0
        lows = [0.0] * 21
        is_ph, _ = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert not is_ph[10]

    def test_pivot_false_when_right_half_has_a_higher_bar(self):
        # Mirror of the above: candidate i=10 is the max of its own left half
        # (5..10, all baseline) but the RIGHT boundary bar at i+n=15 is higher
        # -> must NOT be a pivot. Pins the right-slice bound the same way: a
        # mutant like high[i-n:i+n] drops index i+n and would wrongly pivot.
        highs = [1.0] * 21
        highs[10] = 20.0
        highs[15] = 50.0
        lows = [0.0] * 21
        is_ph, _ = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert not is_ph[10]

    def test_pivot_low_false_when_left_half_has_a_lower_bar(self):
        # Low-side mirror: candidate i=10 is the min of its own right half but
        # the LEFT boundary bar at i-n=5 dips lower -> must NOT be a pivot low.
        lows = [9.0] * 21
        lows[5] = -50.0
        lows[10] = -20.0
        highs = [100.0] * 21
        _, is_pl = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert not is_pl[10]

    def test_pivot_low_false_when_right_half_has_a_lower_bar(self):
        # Low-side mirror: candidate i=10 is the min of its own left half but
        # the RIGHT boundary bar at i+n=15 dips lower -> must NOT be a pivot low.
        lows = [9.0] * 21
        lows[10] = -20.0
        lows[15] = -50.0
        highs = [100.0] * 21
        _, is_pl = ll.pivot_masks(np.array(highs, float), np.array(lows, float), n=5)
        assert not is_pl[10]


class TestSweepIndex:
    def test_next_ge_finds_first_bar_at_or_above(self):
        out = ll.next_ge_index(np.array([5.0, 3.0, 4.0, 5.0, 9.0]))
        assert out[0] == 3      # first later value >= 5.0 is the 5.0 at index 3 (ties sweep)
        assert out[1] == 2
        assert out[4] == 5      # never exceeded -> n

    def test_next_le_finds_first_bar_at_or_below(self):
        out = ll.next_le_index(np.array([5.0, 7.0, 6.0, 5.0, 1.0]))
        assert out[0] == 3
        assert out[4] == 5


class TestLiveSwings:
    def _ctx(self, bars):
        return ll.build_context(bars, ll.DEFAULTS)

    def test_pivot_not_visible_until_n_bars_later(self):
        # peak at index 20; with n=5 it confirms at index 25
        highs = [100.0] * 41
        highs[20] = 130.0
        lows = [90.0] * 41
        bars = make_bars(highs, lows, closes=[95.0] * 41)
        ctx = self._ctx(bars)
        assert ll.live_swings(ctx, 24, ll.DEFAULTS) == []
        prices = [lv.price for lv in ll.live_swings(ctx, 25, ll.DEFAULTS)]
        assert 130.0 in prices

    def test_level_dies_on_the_first_breaching_wick(self):
        highs = [100.0] * 41
        highs[20] = 130.0
        highs[30] = 130.0            # equal high sweeps the earlier pivot (ties sweep)
        lows = [90.0] * 41
        bars = make_bars(highs, lows, closes=[95.0] * 41)
        ctx = self._ctx(bars)
        alive_before = [lv.price for lv in ll.live_swings(ctx, 29, ll.DEFAULTS)]
        assert 130.0 in alive_before
        alive_after = [lv.formation_idx for lv in ll.live_swings(ctx, 35, ll.DEFAULTS)]
        assert 20 not in alive_after      # the 30-bar sweep removed it

    def test_only_pivots_inside_the_scan_window_survive(self):
        params = ll.LevelParams(scan_bars=20)
        highs = [100.0] * 61
        highs[5] = 130.0             # confirms at 10, far outside a 20-bar window at t=60
        highs[45] = 125.0            # confirms at 50, inside
        lows = [90.0] * 61
        bars = make_bars(highs, lows, closes=[95.0] * 61)
        ctx = ll.build_context(bars, params)
        idxs = [lv.formation_idx for lv in ll.live_swings(ctx, 60, params)]
        assert 45 in idxs
        assert 5 not in idxs

    def test_side_is_relative_to_the_snapshot_close(self):
        highs = [100.0] * 41
        highs[20] = 130.0
        lows = [90.0] * 41
        lows[10] = 70.0
        bars = make_bars(highs, lows, closes=[95.0] * 41)
        ctx = self._ctx(bars)
        levels = {lv.price: lv.side for lv in ll.live_swings(ctx, 40, ll.DEFAULTS)}
        assert levels[130.0] == "up"
        assert levels[70.0] == "down"


class TestEqualClustering:
    def _lv(self, price, kind, idx):
        return ll.Level(price, kind, idx, "up")

    def test_two_near_highs_collapse_to_the_cluster_extreme(self):
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.4, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].kind == "equal_highs"
        assert out[0].price == pytest.approx(100.4)     # extreme = max for highs
        assert out[0].formation_idx == 20               # most recent constituent

    def test_two_near_lows_price_at_the_minimum(self):
        levels = [ll.Level(80.0, "swing_low", 10, "down"),
                  ll.Level(79.7, "swing_low", 22, "down")]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].kind == "equal_lows"
        assert out[0].price == pytest.approx(79.7)      # extreme = min for lows
        assert out[0].formation_idx == 22

    def test_constituents_are_removed_not_double_marked(self):
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.4, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert all(lv.kind != "swing_high" for lv in out)

    def test_far_apart_highs_stay_solo(self):
        levels = [self._lv(100.0, "swing_high", 10), self._lv(105.0, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 2
        assert {lv.kind for lv in out} == {"swing_high"}

    def test_chain_linkage_groups_a_staircase_within_tolerance(self):
        # each step is inside tol of its neighbour -> one cluster, extreme 101.0
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.4, "swing_high", 12),
                  self._lv(100.8, "swing_high", 14), self._lv(101.0, "swing_high", 16)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].price == pytest.approx(101.0)

    def test_highs_and_lows_cluster_independently(self):
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.2, "swing_high", 12),
                  ll.Level(100.1, "swing_low", 14, "up")]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        kinds = sorted(lv.kind for lv in out)
        assert kinds == ["equal_highs", "swing_low"]

    def test_side_is_recomputed_against_close(self):
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.4, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=200.0)
        assert out[0].side == "down"

    def test_gap_of_exactly_tol_still_merges(self):
        # break condition is strict `>` -> a gap == tol stays inside the group.
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.5, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].kind == "equal_highs"

    def test_gap_just_over_tol_stays_solo(self):
        # one tick past the boundary above -> now > tol -> must split.
        levels = [self._lv(100.0, "swing_high", 10), self._lv(100.6, "swing_high", 20)]
        out = ll.cluster_equal(levels, tol=0.5, close=90.0)
        assert len(out) == 2
        assert {lv.kind for lv in out} == {"swing_high"}

    def test_solo_high_passthrough_recomputes_side_down(self):
        # helper hardcodes side="up" on the input Level; close sits ABOVE the
        # price so the correct recomputed side is "down". A regression that
        # returned the input Level unchanged (kept g.side) would still say
        # "up" and this would catch it.
        levels = [self._lv(100.0, "swing_high", 10)]
        out = ll.cluster_equal(levels, tol=0.5, close=200.0)
        assert len(out) == 1
        assert out[0].kind == "swing_high"
        assert out[0].side == "down"

    def test_solo_low_passthrough_recomputes_side_up(self):
        # input Level carries side="down"; close sits BELOW the price so the
        # correct recomputed side is "up".
        levels = [ll.Level(80.0, "swing_low", 10, "down")]
        out = ll.cluster_equal(levels, tol=0.5, close=50.0)
        assert len(out) == 1
        assert out[0].kind == "swing_low"
        assert out[0].side == "up"
