"""Unit tests for src/microstructure/liquidity_levels.py — synthetic frames only."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import liquidity_levels as ll
from pathlib import Path


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


SESSION_PARAMS = ll.LevelParams(scan_bars=1000, forming_band_atr=0.25)


def make_session_bars(n_days=3, start_day="2026-01-05"):
    """96 15m bars per UTC day, each day a distinct high/low.

    NOTE: the brief's original version of this helper (`base = 100 + arange*0.01`,
    a single continuously-rising ramp with no reset) is unusable for testing prior-
    period highs: with price monotonically climbing forever, the very next bar
    after any "yesterday" always trades above yesterday's high, so pd_high /
    asia_high / pw_high could never survive the un-swept rule no matter how the
    rest of the implementation is written (verified empirically -- the brief's
    own test_prior_day_high_and_low_are_yesterdays_extremes fails against the
    brief's own reference implementation with that helper). Fixed here with a
    per-day tent: each day rises to a peak at bar 20 (inside the Asia window,
    so an "asia session's own high" can't be exceeded by that day's own later
    hours either) then declines to a trough at the day's last bar. Amplitude
    shrinks geometrically (20 * 0.8**day) so day 0 is strictly the tallest
    peak and deepest trough of the whole frame -- no later day's range ever
    pokes outside an earlier one, so prior-day/prior-week extremes stay
    un-swept arbitrarily far into the future, while each day/week is still
    trivially distinguishable by its own peak/trough value.
    """
    idx = pd.date_range(f"{start_day} 00:00", periods=96 * n_days, freq="15min", tz="UTC")
    n = len(idx)
    bar_in_day = np.arange(n) % 96
    day_of = np.arange(n) // 96
    amplitude = 20.0 * (0.8 ** day_of)
    shape = np.where(bar_in_day <= 20, bar_in_day / 20.0,
                     1.0 - 2.0 * (bar_in_day - 20) / 75.0)
    base = 100.0 + amplitude * shape
    return pd.DataFrame({"open": base, "high": base + 0.5, "low": base - 0.5,
                         "close": base, "volume": 100.0}, index=idx)


class TestSessionLevels:
    def test_prior_day_high_and_low_are_yesterdays_extremes(self):
        bars = make_session_bars(n_days=3)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        t = 96 * 2 + 40                      # mid third day
        got = {lv.kind: lv.price for lv in ll.session_levels(ctx, t, SESSION_PARAMS)}
        prev_day = bars.iloc[96:192]
        assert got["pd_high"] == pytest.approx(prev_day.high.max())
        assert got["pd_low"] == pytest.approx(prev_day.low.min())

    def test_prior_completed_asia_uses_yesterdays_window_not_todays(self):
        bars = make_session_bars(n_days=3)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        t = 96 * 2 + 40                      # 10:00 UTC day 3 -> Asia already closed today
        got = {lv.kind: lv.price for lv in ll.session_levels(ctx, t, SESSION_PARAMS)}
        todays_asia = bars.iloc[192:192 + 28]         # 00:00-07:00 of day 3
        assert got["asia_high"] == pytest.approx(todays_asia.high.max())

    def test_forming_session_extreme_at_current_price_is_excluded(self):
        # price printing a new session high: the level IS price, so it is not a pool
        idx = pd.date_range("2026-01-05 07:00", periods=8, freq="15min", tz="UTC")
        base = 100.0 + np.arange(8) * 1.0
        bars = pd.DataFrame({"open": base, "high": base + 0.05, "low": base - 0.05,
                             "close": base + 0.04, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        kinds = {lv.kind for lv in ll.session_levels(ctx, 7, SESSION_PARAMS)}
        assert "london_high" not in kinds

    def test_forming_session_extreme_enters_once_price_pulls_away(self):
        idx = pd.date_range("2026-01-05 07:00", periods=12, freq="15min", tz="UTC")
        highs = np.full(12, 100.0)
        highs[2] = 130.0                     # session high set early
        lows = np.full(12, 99.0)
        closes = np.full(12, 99.5)
        bars = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                             "close": closes, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        got = {lv.kind: lv.price for lv in ll.session_levels(ctx, 11, SESSION_PARAMS)}
        assert got["london_high"] == pytest.approx(130.0)

    def test_swept_period_level_drops_out(self):
        bars = make_session_bars(n_days=3).copy()
        # blow through the prior-day high on the third day
        bars.iloc[96 * 2 + 10, bars.columns.get_loc("high")] = 10_000.0
        ctx = ll.build_context(bars, SESSION_PARAMS)
        kinds = {lv.kind for lv in ll.session_levels(ctx, 96 * 2 + 40, SESSION_PARAMS)}
        assert "pd_high" not in kinds

    def test_prior_week_extremes_use_the_previous_iso_week(self):
        # start on a Monday so week boundaries are unambiguous
        bars = make_session_bars(n_days=10, start_day="2026-01-05")   # 2026-01-05 is a Monday
        ctx = ll.build_context(bars, SESSION_PARAMS)
        t = 96 * 8                            # inside the second ISO week
        got = {lv.kind: lv.price for lv in ll.session_levels(ctx, t, SESSION_PARAMS)}
        first_week = bars.iloc[:96 * 7]
        assert got["pw_high"] == pytest.approx(first_week.high.max())
        assert got["pw_low"] == pytest.approx(first_week.low.min())

    def test_formation_idx_points_at_the_bar_that_set_the_extreme(self):
        bars = make_session_bars(n_days=3).copy()
        spike = 96 + 33
        bars.iloc[spike, bars.columns.get_loc("high")] = 500.0
        ctx = ll.build_context(bars, SESSION_PARAMS)
        levels = {lv.kind: lv for lv in ll.session_levels(ctx, 96 * 2 + 40, SESSION_PARAMS)}
        assert levels["pd_high"].formation_idx == spike

    # ---- hardening beyond the brief: pin the exact boundaries, not just the middle ----

    def test_prior_session_run_outside_window_is_most_recent_completed_not_two_back(self):
        """At a snapshot OUTSIDE any session window, 'prior' must be the most
        recently completed run (runs[-1], already finished today) -- NOT one
        run further back (runs[-2]). A flat `prior = runs[-2]` mutant would
        silently pick day 2's asia session here instead of day 3's; the
        explicit != assertion below is what would catch that mutant, since a
        plain equality against the right answer alone wouldn't distinguish it
        from "happens to also be true"."""
        bars = make_session_bars(n_days=3)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        t = 96 * 2 + 40   # 10:00 UTC day 3, asia (0-7) already closed today
        got = {lv.kind: lv.price for lv in ll.session_levels(ctx, t, SESSION_PARAMS)}
        day3_asia = bars.iloc[192:192 + 28]     # today's already-completed asia run
        day2_asia = bars.iloc[96:96 + 28]       # one run further back -- the wrong,
                                                 # flat-runs[-2] answer
        assert got["asia_high"] == pytest.approx(day3_asia.high.max())
        assert got["asia_high"] != pytest.approx(day2_asia.high.max())

    def test_prior_and_forming_session_instances_coexist_under_same_kind(self):
        """A completed run (yesterday) and a still-forming run (today) both
        emit a Level tagged "london_high" -- they are distinguished by
        formation_idx, not by kind. A naive {lv.kind: lv.price} lookup (as
        used in most tests above, for convenience) would silently keep only
        one of the two; this test asserts on the full filtered list instead,
        so both the prior and current branches of the two-branch conditional
        are proven to run simultaneously rather than one masking the other."""
        day1_highs = [100.0] * 36
        day1_highs[10] = 150.0                 # yesterday's completed london high
        day1 = make_bars(day1_highs, [90.0] * 36, closes=[95.0] * 36,
                         start="2026-01-05 07:00")
        day2_highs = [100.0] * 12
        day2_highs[2] = 130.0                  # today's forming london high (smaller,
                                                # so it never threatens yesterday's level)
        day2 = make_bars(day2_highs, [90.0] * 12, closes=[95.0] * 12,
                         start="2026-01-06 07:00")
        bars = pd.concat([day1, day2])
        ctx = ll.build_context(bars, SESSION_PARAMS)
        levels = [lv for lv in ll.session_levels(ctx, 47, SESSION_PARAMS)
                 if lv.kind == "london_high"]
        assert len(levels) == 2
        by_idx = {lv.formation_idx: lv.price for lv in levels}
        assert by_idx[10] == pytest.approx(150.0)
        assert by_idx[38] == pytest.approx(130.0)

    def test_overlap_hours_contribute_to_both_london_and_ny_forming(self):
        # 13:00-15:45 sits inside BOTH the london [7,16) and ny [13,21) windows.
        # The same spike bar must independently qualify as the forming extreme
        # for both session types -- proving the overlap is real, not just an
        # artifact of how the two masks happen to be defined.
        idx = pd.date_range("2026-01-05 13:00", periods=12, freq="15min", tz="UTC")
        highs = np.full(12, 100.0)
        highs[2] = 130.0
        lows = np.full(12, 99.0)
        closes = np.full(12, 99.5)
        bars = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                             "close": closes, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        levels = {lv.kind: lv for lv in ll.session_levels(ctx, 11, SESSION_PARAMS)}
        assert levels["london_high"].price == pytest.approx(130.0)
        assert levels["ny_high"].price == pytest.approx(130.0)
        assert levels["london_high"].formation_idx == 2
        assert levels["ny_high"].formation_idx == 2

    def test_forming_extreme_formation_idx_points_at_the_spike_not_run_start(self):
        # spike sits at local index 5 -- neither the run's start (0) nor its
        # end (11, the snapshot bar) -- so this can't pass by accident from a
        # formation_idx that was hardcoded to the run boundary.
        idx = pd.date_range("2026-01-05 07:00", periods=12, freq="15min", tz="UTC")
        highs = np.full(12, 100.0)
        highs[5] = 130.0
        lows = np.full(12, 99.0)
        closes = np.full(12, 99.5)
        bars = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                             "close": closes, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        levels = {lv.kind: lv for lv in ll.session_levels(ctx, 11, SESSION_PARAMS)}
        assert levels["london_high"].formation_idx == 5

    def test_forming_band_boundary_at_exactly_threshold_is_included(self):
        # Every bar's true range is forced to exactly 1.0 (high-low=1.0, and
        # gaps against the constant prior close of 99.5 stay under 1.0), so
        # Wilder's ATR recursion is a fixed point at 1.0 for the whole frame
        # -- this pins ctx.atr[t] == 1.0 without depending on any decay math.
        # The forming-band threshold is then exactly 0.25 * 1.0 = 0.25, and
        # bar 0's high (100.0) is placed exactly 0.25 away from the snapshot
        # close (99.75). The comparison in the implementation is strict `<`,
        # so a distance EQUAL to the threshold must NOT be treated as "at
        # price" -- the level must survive.
        idx = pd.date_range("2026-01-05 07:00", periods=5, freq="15min", tz="UTC")
        highs = np.array([100.0, 99.9, 99.9, 99.9, 99.9])
        lows = np.array([99.0, 98.9, 98.9, 98.9, 98.9])
        closes = np.array([99.5, 99.5, 99.5, 99.5, 99.75])
        bars = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                             "close": closes, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        assert ctx.atr[4] == pytest.approx(1.0)      # pin the constructed ATR
        kinds = {lv.kind for lv in ll.session_levels(ctx, 4, SESSION_PARAMS)}
        assert "london_high" in kinds

    def test_forming_band_just_inside_threshold_is_excluded(self):
        # Identical construction to the boundary test above, except the
        # snapshot close moves one cent closer (distance 0.24 < the 0.25
        # threshold) -- this must now be excluded. Together the two tests pin
        # the comparison as strict `<` (not `<=`) on both sides of the line.
        idx = pd.date_range("2026-01-05 07:00", periods=5, freq="15min", tz="UTC")
        highs = np.array([100.0, 99.9, 99.9, 99.9, 99.9])
        lows = np.array([99.0, 98.9, 98.9, 98.9, 98.9])
        closes = np.array([99.5, 99.5, 99.5, 99.5, 99.76])
        bars = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                             "close": closes, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        assert ctx.atr[4] == pytest.approx(1.0)
        kinds = {lv.kind for lv in ll.session_levels(ctx, 4, SESSION_PARAMS)}
        assert "london_high" not in kinds


class TestChoiceSet:
    def _ctx_with_atr(self, bars, params):
        return ll.build_context(bars, params)

    def test_levels_beyond_max_distance_are_dropped(self):
        params = ll.LevelParams(max_dist_atr=2.0, scan_bars=200)
        highs = np.full(60, 100.0)
        highs[20] = 130.0                    # far above, ~30 price units
        lows = np.full(60, 99.0)
        closes = np.full(60, 99.5)
        bars = make_bars(highs, lows, closes=closes)
        ctx = self._ctx_with_atr(bars, params)
        prices = [lv.price for lv in ll.build_choice_set(ctx, 59, params)]
        assert 130.0 not in prices           # 30 units >> 2 * ATR(~1)

    def test_at_most_levels_per_side_on_each_side(self):
        params = ll.LevelParams(levels_per_side=2, scan_bars=500, max_dist_atr=100.0)
        n = 200
        highs = np.full(n, 100.0)
        lows = np.full(n, 90.0)
        for k, i in enumerate(range(20, 120, 12)):        # a ladder of swing highs
            highs[i] = 101.0 + k * 3.0
        for k, i in enumerate(range(25, 125, 12)):        # and of swing lows
            lows[i] = 89.0 - k * 3.0
        closes = np.full(n, 95.0)
        bars = make_bars(highs, lows, closes=closes)
        ctx = self._ctx_with_atr(bars, params)
        got = ll.build_choice_set(ctx, n - 1, params)
        assert sum(lv.side == "up" for lv in got) <= 2
        assert sum(lv.side == "down" for lv in got) <= 2

    def test_merge_keeps_the_higher_priority_type(self):
        equal = ll.Level(100.0, "equal_highs", 10, "up")
        session = ll.Level(100.02, "pd_high", 12, "up")
        swing = ll.Level(100.04, "swing_high", 14, "up")
        out = ll._merge_by_priority([swing, session, equal], tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].kind == "equal_highs"

    def test_merge_prefers_session_over_solo_swing(self):
        session = ll.Level(100.0, "pd_low", 12, "down")
        swing = ll.Level(100.02, "swing_low", 14, "down")
        out = ll._merge_by_priority([swing, session], tol=0.5, close=200.0)
        assert len(out) == 1
        assert out[0].kind == "pd_low"

    def test_merge_leaves_well_separated_levels_alone(self):
        a = ll.Level(100.0, "swing_high", 10, "up")
        b = ll.Level(105.0, "swing_high", 12, "up")
        out = ll._merge_by_priority([a, b], tol=0.5, close=90.0)
        assert len(out) == 2

    def test_output_is_sorted_up_side_first_then_by_distance(self):
        params = ll.LevelParams(levels_per_side=6, scan_bars=500, max_dist_atr=100.0)
        n = 200
        highs = np.full(n, 100.0)
        lows = np.full(n, 90.0)
        highs[20], highs[40] = 104.0, 108.0
        lows[30], lows[50] = 86.0, 82.0
        closes = np.full(n, 95.0)
        bars = make_bars(highs, lows, closes=closes)
        ctx = self._ctx_with_atr(bars, params)
        got = ll.build_choice_set(ctx, n - 1, params)
        sides = [lv.side for lv in got]
        assert sides == sorted(sides, key=lambda s: 0 if s == "up" else 1)
        ups = [abs(lv.price - 95.0) for lv in got if lv.side == "up"]
        assert ups == sorted(ups)

    def test_empty_frame_head_returns_empty_set(self):
        bars = make_bars([100.0] * 5, [99.0] * 5)
        ctx = ll.build_context(bars, ll.DEFAULTS)
        assert ll.build_choice_set(ctx, 4, ll.DEFAULTS) == []

    # ---- hardening beyond the brief: pin the exact boundaries, not just the middle ----

    def test_up_and_down_sides_are_each_sorted_with_genuine_multi_level_ties_to_break(self):
        # The brief's own ordering fixture (transcribed verbatim above) has a
        # hidden flaw: highs[20]=104 then highs[40]=108 means the LATER,
        # TALLER spike sweeps the earlier one (a later high >= an earlier
        # pivot kills it, same rule as everywhere else in this module), and
        # symmetrically for the lows -- so only ONE level survives per side
        # in that fixture. `ups == sorted(ups)` there is checking a
        # single-element list: true no matter what the sort key is, or even
        # if sorting were skipped entirely. Verified empirically: printing
        # ll.build_choice_set(ctx, n-1, params) against that exact fixture
        # yields exactly 1 up + 1 down.
        #
        # This fixture uses a decaying (earlier-bigger, later-smaller) shape
        # on BOTH sides -- the same "tent" shape load-bearing in
        # make_session_bars for the same underlying reason -- so TWO
        # distinct, un-swept levels survive per side at different distances,
        # genuinely exercising ascending-distance order with something that
        # would visibly reorder under a wrong/missing sort key.
        params = ll.LevelParams(levels_per_side=6, scan_bars=500, max_dist_atr=100.0)
        n = 200
        highs = np.full(n, 100.0)
        lows = np.full(n, 90.0)
        highs[20], highs[40] = 110.0, 104.0     # earlier bigger, later smaller -> both survive
        lows[30], lows[50] = 80.0, 86.0         # earlier deeper, later shallower -> both survive
        closes = np.full(n, 95.0)
        bars = make_bars(highs, lows, closes=closes)
        ctx = self._ctx_with_atr(bars, params)
        got = ll.build_choice_set(ctx, n - 1, params)
        ups = [lv.price - 95.0 for lv in got if lv.side == "up"]
        downs = [95.0 - lv.price for lv in got if lv.side == "down"]
        assert len(ups) >= 2 and len(downs) >= 2      # not vacuous on either side
        assert ups == sorted(ups)
        assert downs == sorted(downs)
        sides = [lv.side for lv in got]
        assert sides == sorted(sides, key=lambda s: 0 if s == "up" else 1)

    def test_distance_cap_boundary_is_inclusive_at_exactly_max_dist(self, monkeypatch):
        # Pins the `<=` in `abs(lv.price - close) <= max_dist`: a level placed
        # at EXACTLY max_dist_atr * ATR must be kept, one epsilon further out
        # must be dropped, and one just inside must be kept. A test that only
        # shows "far things get dropped" (the brief's own test above) can't
        # tell a `<=` from a `<` mutant at the boundary itself -- this can.
        # live_swings/session_levels are monkeypatched so the levels under
        # test are placed at exact, independently-computed offsets from a
        # pinned ATR=1.0, rather than depending on the pivot/session pipeline
        # to happen to produce a level at exactly the boundary.
        params = ll.LevelParams(max_dist_atr=8.0, merge_tol_atr=0.0, levels_per_side=6)
        bars = make_bars([100.0] * 5, [99.0] * 5, closes=[95.0] * 5)
        ctx = self._ctx_with_atr(bars, params)
        ctx.atr[:] = 1.0
        close = float(ctx.close[4])
        max_dist = params.max_dist_atr * 1.0
        on_boundary = ll.Level(close + max_dist, "pd_high", 0, "up")
        just_outside = ll.Level(close + max_dist + 1e-6, "asia_high", 1, "up")
        just_inside = ll.Level(close + max_dist - 0.01, "london_high", 2, "up")
        monkeypatch.setattr(ll, "live_swings", lambda ctx, t, params: [])
        monkeypatch.setattr(
            ll, "session_levels",
            lambda ctx, t, params: [on_boundary, just_outside, just_inside])
        got = ll.build_choice_set(ctx, 4, params)
        kinds = {lv.kind for lv in got}
        assert "pd_high" in kinds
        assert "london_high" in kinds
        assert "asia_high" not in kinds

    def test_merge_runs_before_the_per_side_cap(self, monkeypatch):
        # levels_per_side=2, three up-side candidates: L_a (dist 1.00) and
        # L_b (dist 1.05) are within merge tol (0.10) of each other; L_c
        # (dist 2.00) is a genuinely distinct third level, further than both.
        #
        # Correct order (merge THEN cap): merging collapses {L_a, L_b} into
        # one survivor before the cap runs, so the 2-slot cap keeps BOTH the
        # merged survivor and L_c -> 2 output levels, one at each distance.
        #
        # Wrong order (cap THEN merge): the 2 nearest by raw distance are
        # {L_a, L_b} -- L_c is capped out before it ever gets a chance to
        # merge. Merging the surviving pair then collapses them into ONE
        # level, so the wrong order yields only 1 output level and L_c's
        # distance is completely absent. This is what "cap first and a merge
        # could leave fewer than six per side" means in the brief -- and a
        # test that only checked the final PRICE (which could coincide under
        # either order) rather than the full membership/count would not
        # actually discriminate the two orderings.
        params = ll.LevelParams(levels_per_side=2, merge_tol_atr=0.10, max_dist_atr=100.0)
        bars = make_bars([100.0] * 5, [99.0] * 5, closes=[95.0] * 5)
        ctx = self._ctx_with_atr(bars, params)
        ctx.atr[:] = 1.0
        close = float(ctx.close[4])
        L_a = ll.Level(close + 1.00, "pd_high", 0, "up")
        L_b = ll.Level(close + 1.05, "asia_high", 1, "up")
        L_c = ll.Level(close + 2.00, "london_high", 2, "up")
        monkeypatch.setattr(ll, "live_swings", lambda ctx, t, params: [])
        monkeypatch.setattr(ll, "session_levels", lambda ctx, t, params: [L_a, L_b, L_c])
        got = ll.build_choice_set(ctx, 4, params)
        assert len(got) == 2
        offsets = sorted(round(lv.price - close, 2) for lv in got)
        assert offsets == [1.0, 2.0]     # L_c's slot must survive

    def test_cap_applies_independently_per_side_not_as_a_global_pool(self, monkeypatch):
        # 5 valid "up" candidates (distances 0.5..2.5) but only 1 "down"
        # candidate, placed FARTHER away (distance 10) than every up. With
        # levels_per_side=2 the correct per-side cap keeps 2 ups (the nearest
        # two) and the lone down (fewer than the cap, so untouched: 3 total).
        # A buggy "global pool of 2*levels_per_side=4 nearest overall" cap
        # would instead keep the 4 nearest ups and drop the down entirely
        # (4 total, all up) -- these two behaviours are distinguishable by
        # per-side counts alone.
        params = ll.LevelParams(levels_per_side=2, merge_tol_atr=0.0, max_dist_atr=100.0)
        bars = make_bars([100.0] * 5, [99.0] * 5, closes=[95.0] * 5)
        ctx = self._ctx_with_atr(bars, params)
        ctx.atr[:] = 1.0
        close = float(ctx.close[4])
        ups = [ll.Level(close + d, "pd_high", i, "up")
               for i, d in enumerate([0.5, 1.0, 1.5, 2.0, 2.5])]
        down = [ll.Level(close - 10.0, "pd_low", 10, "down")]
        monkeypatch.setattr(ll, "live_swings", lambda ctx, t, params: [])
        monkeypatch.setattr(ll, "session_levels", lambda ctx, t, params: ups + down)
        got = ll.build_choice_set(ctx, 4, params)
        up_count = sum(1 for lv in got if lv.side == "up")
        down_count = sum(1 for lv in got if lv.side == "down")
        assert up_count == 2
        assert down_count == 1

    def test_merge_prefers_equal_cluster_over_session_alone(self):
        # Isolates the equal>session leg of the priority order without a
        # swing in the mix (the brief's 3-way test could pass even if
        # equal>session were broken, so long as swing still lost to both).
        equal = ll.Level(100.0, "equal_highs", 10, "up")
        session = ll.Level(100.02, "pd_high", 12, "up")
        out = ll._merge_by_priority([session, equal], tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].kind == "equal_highs"

    def test_merge_prefers_equal_cluster_over_solo_swing_alone(self):
        # Isolates the equal>swing leg directly (session absent).
        equal = ll.Level(100.0, "equal_lows", 10, "down")
        swing = ll.Level(100.02, "swing_low", 14, "down")
        out = ll._merge_by_priority([swing, equal], tol=0.5, close=200.0)
        assert len(out) == 1
        assert out[0].kind == "equal_lows"

    def test_merge_tie_break_prefers_earlier_formation_idx_when_priority_ties(self):
        # Two levels of EQUAL priority (both solo swings) within tol: the
        # survivor must be whichever has the EARLIER formation_idx. Here the
        # earlier-idx level is placed at the HIGHER price, so a wrong
        # tie-break like "always keep the lower price" would also give the
        # wrong answer -- this isolates formation_idx specifically.
        earlier_higher_price = ll.Level(100.3, "swing_high", 5, "up")
        later_lower_price = ll.Level(100.1, "swing_high", 20, "up")
        out = ll._merge_by_priority([later_lower_price, earlier_higher_price],
                                    tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].price == pytest.approx(100.3)
        assert out[0].formation_idx == 5

    def test_merge_tie_break_falls_back_to_lower_price_when_idx_also_ties(self):
        # Equal priority AND equal formation_idx (e.g. a swing high/low
        # confirming on the same bar) -- the final tie-break is the lower
        # price. The higher price is listed FIRST in the input so a naive
        # "first element wins" bug would keep the wrong one.
        higher = ll.Level(100.5, "swing_high", 7, "up")
        lower = ll.Level(100.4, "swing_high", 7, "up")
        out = ll._merge_by_priority([higher, lower], tol=0.5, close=90.0)
        assert len(out) == 1
        assert out[0].price == pytest.approx(100.4)


class TestSessionMaskBoundaries:
    def test_half_open_bounds_at_the_exact_hour(self):
        # Pins every session's [start, end) endpoint at once: hour 7 belongs
        # to london but NOT asia (start inclusive); hour 16 belongs to
        # NEITHER london nor ny (end exclusive on london, ny starts at 13);
        # hours 13-15 belong to BOTH london and ny (the intentional overlap);
        # hour 20 still belongs to ny (just under its 21 end); hour 6 belongs
        # only to asia.
        hour = np.array([6, 7, 12, 13, 15, 16, 20])
        asia = ll._session_mask(hour, 0, 7)
        london = ll._session_mask(hour, 7, 16)
        ny = ll._session_mask(hour, 13, 21)
        assert list(asia) == [True, False, False, False, False, False, False]
        assert list(london) == [False, True, True, True, True, False, False]
        assert list(ny) == [False, False, False, True, True, True, True]


class TestFeatureMatrix:
    def _frame(self, n=300, close=100.0):
        highs = np.full(n, close + 1.0)
        lows = np.full(n, close - 1.0)
        closes = np.full(n, close)
        return make_bars(highs, lows, closes=closes, start="2026-01-05 00:00")

    def test_shape_and_column_order(self):
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        levels = [ll.Level(110.0, "swing_high", 100, "up"),
                  ll.Level(90.0, "swing_low", 120, "down")]
        X = ll.feature_matrix(ctx, 299, levels, ll.DEFAULTS)
        assert X.shape == (2, len(ll.FEATURE_NAMES)) == (2, 12)

    def test_log_dist_atr_is_log1p_of_atr_normalised_distance(self):
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        lv = ll.Level(110.0, "swing_high", 100, "up")
        X = ll.feature_matrix(ctx, 299, [lv], ll.DEFAULTS)
        expected = np.log1p(10.0 / ctx.atr[299])
        assert X[0, 0] == pytest.approx(expected)

    def test_n_closer_same_side_counts_only_nearer_same_side_members(self):
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        levels = [ll.Level(105.0, "swing_high", 100, "up"),
                  ll.Level(110.0, "swing_high", 101, "up"),
                  ll.Level(95.0, "swing_low", 102, "down")]
        X = ll.feature_matrix(ctx, 299, levels, ll.DEFAULTS)
        assert X[0, 1] == 0.0
        assert X[1, 1] == 1.0     # the 105 level sits between price and 110
        assert X[2, 1] == 0.0     # other side does not count

    def test_side_up_and_type_one_hots(self):
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        levels = [ll.Level(110.0, "equal_highs", 100, "up"),
                  ll.Level(90.0, "pd_low", 100, "down"),
                  ll.Level(92.0, "swing_low", 100, "down")]
        X = ll.feature_matrix(ctx, 299, levels, ll.DEFAULTS)
        assert list(X[:, 2]) == [1.0, 0.0, 0.0]        # side_up
        assert list(X[:, 3]) == [1.0, 0.0, 0.0]        # type_equal
        assert list(X[:, 4]) == [0.0, 1.0, 0.0]        # type_session
        assert list(X[:, 5])[0] == pytest.approx(np.log1p(199))   # log_age_bars

    def test_touch_count_counts_near_misses_not_breaches(self):
        n = 60
        highs = np.full(n, 100.0)
        lows = np.full(n, 99.0)
        closes = np.full(n, 99.5)
        # level at 110; bars 30 and 40 approach to within the band without touching
        highs[30] = 109.9
        highs[40] = 109.95
        bars = make_bars(highs, lows, closes=closes)
        # ATR here is ~1.24 (the two approach bars spike it above the flat 1.0), so a
        # 1.0x band reaches down to ~108.8: it catches the two near-misses and excludes
        # the 100.0 bars. A large band would count every bar in the frame.
        params = ll.LevelParams(touch_band_atr=1.0)
        ctx = ll.build_context(bars, params)
        lv = ll.Level(110.0, "swing_high", 10, "up")
        X = ll.feature_matrix(ctx, 59, [lv], params)
        assert X[0, 6] == pytest.approx(2.0)

    def test_trend_align_follows_the_ema_slope_sign(self):
        n = 300
        base = 100.0 + np.arange(n) * 0.05          # steadily rising -> positive slope
        bars = pd.DataFrame({"open": base, "high": base + 0.5, "low": base - 0.5,
                             "close": base, "volume": 100.0},
                            index=pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC"))
        ctx = ll.build_context(bars, ll.DEFAULTS)
        c = ctx.close[299]
        up = ll.Level(c + 5.0, "swing_high", 100, "up")
        down = ll.Level(c - 5.0, "swing_low", 100, "down")
        X = ll.feature_matrix(ctx, 299, [up, down], ll.DEFAULTS)
        assert X[0, 7] == 1.0
        assert X[1, 7] == 0.0

    def test_session_dummies_are_both_set_during_the_overlap(self):
        # 14:00 UTC is inside London (7-16) and NY (13-21)
        idx = pd.date_range("2026-01-05 10:00", periods=20, freq="15min", tz="UTC")
        base = np.full(20, 100.0)
        bars = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                             "close": base, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, ll.DEFAULTS)
        t = 16                                    # 14:00 UTC
        assert ctx.hour[t] == 14
        X = ll.feature_matrix(ctx, t, [ll.Level(110.0, "swing_high", 0, "up")], ll.DEFAULTS)
        assert X[0, 9] == 1.0 and X[0, 10] == 1.0

    def test_asia_snapshot_sets_neither_session_dummy(self):
        idx = pd.date_range("2026-01-05 02:00", periods=8, freq="15min", tz="UTC")
        base = np.full(8, 100.0)
        bars = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                             "close": base, "volume": 100.0}, index=idx)
        ctx = ll.build_context(bars, ll.DEFAULTS)
        X = ll.feature_matrix(ctx, 7, [ll.Level(110.0, "swing_high", 0, "up")], ll.DEFAULTS)
        assert X[0, 9] == 0.0 and X[0, 10] == 0.0

    def test_interaction_is_the_product_of_columns_0_and_8(self):
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        X = ll.feature_matrix(ctx, 299, [ll.Level(110.0, "swing_high", 100, "up")],
                              ll.DEFAULTS)
        assert X[0, 11] == pytest.approx(X[0, 0] * X[0, 8])

    def test_all_features_are_finite_on_a_real_shaped_frame(self):
        bars = self._frame(n=400)
        ctx = ll.build_context(bars, ll.DEFAULTS)
        levels = ll.build_choice_set(ctx, 399, ll.DEFAULTS)
        X = ll.feature_matrix(ctx, 399, levels, ll.DEFAULTS)
        assert np.isfinite(X).all()

    # ---- hardening beyond the brief: pin column order, exact-zero trend_align,
    # touch_count's three boundary rules, the session-dummy edges, the
    # n_closer_same_side tie case, and the log_age_bars floor ----

    def test_column_order_matches_feature_names_index_by_index(self):
        """Fingerprint fixture: side_up/type_equal/type_session each carry a
        DIFFERENT one-hot pattern across the four rows (unlike the brief's own
        test_side_up_and_type_one_hots fixture above, where row 0 happens to
        score 1.0 on BOTH side_up and type_equal -- identical columns there,
        so a transposition of columns 2 and 3 would slip through undetected).
        n_closer_same_side, log_dist_atr and log_age_bars are independently
        computed from the same rows, and atr_pctile is pinned away from 1.0
        so the interaction column (col0 * col8) is provably NOT the same
        vector as col0 -- otherwise swapping columns 0 and 11 would also be
        invisible. Every assertion below is name-indexed via
        FEATURE_NAMES.index(...), so a reordering of the contract itself
        (not just a hardcoded position) is what is under test.
        """
        bars = self._frame(n=300, close=100.0)
        ctx = ll.build_context(bars, ll.DEFAULTS)
        # Pin the frame-level scalars directly so the expected values below
        # depend only on feature_matrix's own algebra, not on the ATR/
        # percentile/slope recursions being reproduced correctly elsewhere.
        ctx.atr[:] = 2.0
        ctx.atr_pctile[:] = 0.7
        ctx.ema_slope[:] = 0.0
        ctx.hour[:] = 2                 # Asia -> both session dummies read 0
        t = 299

        levels = [
            ll.Level(110.0, "equal_highs", 250, "up"),    # dist 10, age 49
            ll.Level(108.0, "swing_high", 200, "up"),     # dist 8,  age 99
            ll.Level(90.0, "pd_low", 280, "down"),        # dist 10, age 19
            ll.Level(85.0, "swing_low", 100, "down"),     # dist 15, age 199
        ]
        X = ll.feature_matrix(ctx, t, levels, ll.DEFAULTS)

        def col(name):
            return list(X[:, ll.FEATURE_NAMES.index(name)])

        atr, apct = 2.0, 0.7
        dists = (10.0, 8.0, 10.0, 15.0)
        ages = (49, 99, 19, 199)
        expected_log_dist = [np.log1p(d / atr) for d in dists]
        expected_log_age = [np.log1p(a) for a in ages]

        assert col("log_dist_atr") == pytest.approx(expected_log_dist)
        assert col("n_closer_same_side") == [1.0, 0.0, 0.0, 1.0]
        assert col("side_up") == [1.0, 1.0, 0.0, 0.0]
        assert col("type_equal") == [1.0, 0.0, 0.0, 0.0]
        assert col("type_session") == [0.0, 0.0, 1.0, 0.0]
        assert col("log_age_bars") == pytest.approx(expected_log_age)
        assert col("touch_count") == [0.0, 0.0, 0.0, 0.0]
        assert col("atr_pctile") == pytest.approx([0.7, 0.7, 0.7, 0.7])
        assert col("dist_x_atrpctile") == pytest.approx(
            [v * apct for v in expected_log_dist])
        # col11 must differ from col0 now that atr_pctile != 1.0 -- otherwise
        # a 0<->11 transposition would be numerically invisible.
        assert col("dist_x_atrpctile") != pytest.approx(col("log_dist_atr"))

    def test_trend_align_is_zero_on_both_sides_at_exactly_zero_slope(self):
        """Locked definition: an exactly-zero slope scores 0.0 for BOTH the
        up and the down side -- not 1.0 for one of them. A `>=`/`<=` in
        either branch of the aligned check would flip one of these to 1.0."""
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        ctx.ema_slope[:] = 0.0
        c = ctx.close[299]
        up = ll.Level(c + 5.0, "swing_high", 100, "up")
        down = ll.Level(c - 5.0, "swing_low", 100, "down")
        X = ll.feature_matrix(ctx, 299, [up, down], ll.DEFAULTS)
        col = ll.FEATURE_NAMES.index("trend_align")
        assert X[0, col] == 0.0
        assert X[1, col] == 0.0

    def test_touch_count_edge_cases_band_boundary_and_at_level_breach(self):
        """Two boundary rules pinned against a known ATR (overridden to an
        exact 1.0) so the band threshold is a hand-checkable round number:
        a bar whose high sits EXACTLY at the band's outer edge
        (110 - 1.0*1.0 = 109.0) must count (`>=`, not `>`); a bar whose high
        sits EXACTLY at the level price (110.0) is a breach, not a touch,
        and must NOT count (`< price`, not `<= price`)."""
        n = 20
        highs = np.full(n, 100.0)
        lows = np.full(n, 99.0)
        closes = np.full(n, 99.5)
        highs[5] = 109.0      # exactly at the band edge
        highs[10] = 110.0     # exactly AT the level -- a breach
        bars = make_bars(highs, lows, closes=closes)
        params = ll.LevelParams(touch_band_atr=1.0)
        ctx = ll.build_context(bars, params)
        ctx.atr[:] = 1.0
        lv = ll.Level(110.0, "swing_high", 0, "up")
        X = ll.feature_matrix(ctx, n - 1, [lv], params)
        assert X[0, ll.FEATURE_NAMES.index("touch_count")] == pytest.approx(1.0)

    def test_touch_count_scan_starts_strictly_after_formation_idx(self):
        """The near-miss bar sits at the SAME index the level formed on. Even
        though it is within the band, it must not count -- the scan window
        is `formation_idx + 1 .. t`, strictly after formation, not
        `formation_idx .. t`. A mutant that started the scan AT
        formation_idx (no +1) would count it."""
        n = 20
        highs = np.full(n, 100.0)
        lows = np.full(n, 99.0)
        closes = np.full(n, 99.5)
        highs[8] = 109.5      # a near miss, if it were in the scan window
        bars = make_bars(highs, lows, closes=closes)
        params = ll.LevelParams(touch_band_atr=1.0)
        ctx = ll.build_context(bars, params)
        ctx.atr[:] = 1.0
        lv = ll.Level(110.0, "swing_high", 8, "up")     # formed ON the near-miss bar
        X = ll.feature_matrix(ctx, n - 1, [lv], params)
        assert X[0, ll.FEATURE_NAMES.index("touch_count")] == 0.0

    def test_touch_count_respects_touch_lookback_window(self):
        """A near-miss that sits before the lookback horizon must not count,
        even though it is comfortably after formation_idx. Pins
        `t - lookback + 1` as the other half of the scan-start max(), not
        just formation_idx + 1 -- a mutant that dropped the lookback term
        would count it."""
        n = 30
        highs = np.full(n, 100.0)
        lows = np.full(n, 99.0)
        closes = np.full(n, 99.5)
        highs[2] = 109.5       # near miss, but before the lookback horizon
        bars = make_bars(highs, lows, closes=closes)
        params = ll.LevelParams(touch_band_atr=1.0, touch_lookback=10)
        ctx = ll.build_context(bars, params)
        ctx.atr[:] = 1.0
        lv = ll.Level(110.0, "swing_high", 0, "up")
        t = n - 1               # 29; lookback=10 -> scan starts at 20, bar 2 excluded
        X = ll.feature_matrix(ctx, t, [lv], params)
        assert X[0, ll.FEATURE_NAMES.index("touch_count")] == 0.0

    def test_session_dummy_boundaries_at_each_half_open_edge(self):
        """Pins every edge of the two [start, end) session windows via the
        snapshot hour (feature_matrix classifies the snapshot, not the
        level): 07:00 opens london only; 13:00 is the first minute BOTH are
        set (the overlap's start); 16:00 closes london out while ny keeps
        running; 21:00 closes ny out too, landing back in the no-session
        tail alongside 22:00 -- both must read exactly like Asia (0, 0)."""
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        lv = ll.Level(110.0, "swing_high", 0, "up")
        expected = {
            7: (1.0, 0.0),
            13: (1.0, 1.0),
            16: (0.0, 1.0),
            21: (0.0, 0.0),
            22: (0.0, 0.0),
        }
        lon_col = ll.FEATURE_NAMES.index("session_london")
        ny_col = ll.FEATURE_NAMES.index("session_ny")
        for hour, (exp_lon, exp_ny) in expected.items():
            ctx.hour[299] = hour
            X = ll.feature_matrix(ctx, 299, [lv], ll.DEFAULTS)
            assert X[0, lon_col] == exp_lon, hour
            assert X[0, ny_col] == exp_ny, hour

    def test_n_closer_same_side_ties_do_not_count(self):
        """Two same-side levels sit at the IDENTICAL price (hence identical
        distance from close). Neither is strictly closer than the other, so
        a `<=` in place of the `<` would make each count the other and read
        1.0 where the correct answer is 0.0 for both."""
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        levels = [ll.Level(105.0, "swing_high", 100, "up"),
                  ll.Level(105.0, "equal_highs", 120, "up")]
        X = ll.feature_matrix(ctx, 299, levels, ll.DEFAULTS)
        col = ll.FEATURE_NAMES.index("n_closer_same_side")
        assert X[0, col] == 0.0
        assert X[1, col] == 0.0

    def test_all_features_are_finite_with_a_genuinely_nonempty_choice_set(self):
        """The brief's own fixture above (self._frame: flat highs/lows/closes)
        produces ZERO un-swept pivots -- every interior bar ties for both the
        rolling max and min, and ties sweep (per pivot_masks/next_ge_index),
        so every candidate pivot dies on the very next identical bar.
        build_choice_set returns [] on it (verified empirically), so
        test_all_features_are_finite_on_a_real_shaped_frame's per-row loop
        body never actually executes and its isfinite check is vacuously
        true regardless of any bug inside the loop -- the same flat/
        monotonic fixture trap already hit twice elsewhere in this module.
        Reused here is make_session_bars, already built to produce genuine
        un-swept swing AND session levels, so the loop body genuinely runs."""
        bars = make_session_bars(n_days=5)
        ctx = ll.build_context(bars, SESSION_PARAMS)
        t = len(bars) - 1
        levels = ll.build_choice_set(ctx, t, SESSION_PARAMS)
        assert len(levels) > 0          # not vacuous
        X = ll.feature_matrix(ctx, t, levels, SESSION_PARAMS)
        assert np.isfinite(X).all()

    def test_log_age_bars_floors_negative_age_at_zero(self):
        """Pins the `max(0, t - formation_idx)` floor: a level whose
        formation_idx equals t reads log1p(0) == 0.0, and one whose
        formation_idx is AFTER t (age would go negative under a raw
        subtraction) must floor to the same 0.0 rather than propagate a
        negative value into log1p (whose domain is x > -1)."""
        bars = self._frame()
        ctx = ll.build_context(bars, ll.DEFAULTS)
        at_t = ll.Level(110.0, "swing_high", 299, "up")     # age exactly 0
        future = ll.Level(108.0, "swing_high", 305, "up")   # "formed after t"
        X = ll.feature_matrix(ctx, 299, [at_t, future], ll.DEFAULTS)
        col = ll.FEATURE_NAMES.index("log_age_bars")
        assert X[0, col] == 0.0
        assert X[1, col] == 0.0


class TestFirstTouch:
    def _ctx(self, highs, lows, closes):
        return ll.build_context(make_bars(highs, lows, closes=closes), ll.DEFAULTS)

    def test_returns_minus_one_when_nothing_is_touched(self):
        ctx = self._ctx([100.0] * 20, [99.0] * 20, [99.5] * 20)
        levels = [ll.Level(150.0, "swing_high", 1, "up"),
                  ll.Level(50.0, "swing_low", 1, "down")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == -1

    def test_credits_the_level_reached_first_in_time(self):
        highs = [100.0] * 20
        highs[8] = 111.0          # touches the 110 level at bar 8
        highs[12] = 121.0         # touches the 120 level later
        ctx = self._ctx(highs, [99.0] * 20, [99.5] * 20)
        levels = [ll.Level(110.0, "swing_high", 1, "up"),
                  ll.Level(120.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == 0

    def test_same_bar_tie_credits_the_nearer_level(self):
        highs = [100.0] * 20
        highs[8] = 130.0          # one bar spans both 110 and 120
        ctx = self._ctx(highs, [99.0] * 20, [99.5] * 20)
        levels = [ll.Level(120.0, "swing_high", 1, "up"),
                  ll.Level(110.0, "swing_high", 1, "up")]
        # index 1 is the 110 level -> nearer to the 99.5 close
        assert ll.first_touch(ctx, 5, levels, horizon=10) == 1

    def test_same_bar_tie_across_sides_credits_the_nearer_level(self):
        highs = [100.0] * 20
        lows = [99.0] * 20
        highs[8] = 105.0          # 3.0 above close, reaches the 102 level
        lows[8] = 90.0            # 9.5 below close, reaches the 95 level
        ctx = self._ctx(highs, lows, [99.5] * 20)
        levels = [ll.Level(95.0, "swing_low", 1, "down"),
                  ll.Level(102.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == 1

    def test_snapshot_bar_itself_does_not_count(self):
        highs = [100.0] * 20
        highs[5] = 130.0          # the snapshot bar's own wick
        ctx = self._ctx(highs, [99.0] * 20, [99.5] * 20)
        levels = [ll.Level(110.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == -1

    def test_horizon_is_respected(self):
        highs = [100.0] * 30
        highs[20] = 130.0
        ctx = self._ctx(highs, [99.0] * 30, [99.5] * 30)
        levels = [ll.Level(110.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == -1
        assert ll.first_touch(ctx, 5, levels, horizon=20) == 0

    # ----------------------------------------------------------- hardening

    def test_time_beats_nearness_across_sides(self):
        """The plan's `..._reached_first_in_time` test cannot actually prove this.

        There the earlier-touched level (110) is ALSO the nearer one, so an
        implementation that ignored time and simply returned the nearest level
        touched anywhere in the window would pass it. On a single side the case
        is unconstructable — reaching 120 from below always crosses 110 first —
        so time-vs-nearness can only be separated ACROSS sides.

        Here the FARTHER level (up at 110, 10.0 away) is touched at bar 8 and the
        NEARER one (down at 95, 5.0 away) not until bar 12. Time must win.
        """
        highs = [100.5] * 20
        lows = [99.5] * 20
        highs[8] = 110.5          # touches the up level only
        lows[12] = 94.0           # touches the down level, later but nearer
        ctx = self._ctx(highs, lows, [100.0] * 20)
        levels = [ll.Level(95.0, "swing_low", 1, "down"),
                  ll.Level(110.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == 1

    def test_a_touch_exactly_at_the_level_price_counts(self):
        """Pins `>=` / `<=` at the boundary. Every one of the plan's own tests
        overshoots the level, so a strict `>` / `<` implementation passes them all."""
        highs = [100.0] * 20
        lows = [99.0] * 20
        highs[8] = 110.0                      # EXACTLY the up level
        ctx = self._ctx(highs, lows, [99.5] * 20)
        assert ll.first_touch(ctx, 5, [ll.Level(110.0, "swing_high", 1, "up")],
                              horizon=10) == 0
        lows2 = [99.0] * 20
        lows2[8] = 90.0                       # EXACTLY the down level
        ctx2 = self._ctx([100.0] * 20, lows2, [99.5] * 20)
        assert ll.first_touch(ctx2, 5, [ll.Level(90.0, "swing_low", 1, "down")],
                              horizon=10) == 0

    def test_a_bar_one_tick_short_of_the_level_does_not_count(self):
        highs = [100.0] * 20
        highs[8] = 109.999
        ctx = self._ctx(highs, [99.0] * 20, [99.5] * 20)
        assert ll.first_touch(ctx, 5, [ll.Level(110.0, "swing_high", 1, "up")],
                              horizon=10) == -1

    def test_touch_exactly_on_the_last_horizon_bar_counts(self):
        """t + horizon is inclusive; t + horizon + 1 is not."""
        highs = [100.0] * 30
        highs[15] = 130.0                     # t=5, horizon=10 -> last bar is 15
        ctx = self._ctx(highs, [99.0] * 30, [99.5] * 30)
        assert ll.first_touch(ctx, 5, [ll.Level(110.0, "swing_high", 1, "up")],
                              horizon=10) == 0
        highs2 = [100.0] * 30
        highs2[16] = 130.0
        ctx2 = self._ctx(highs2, [99.0] * 30, [99.5] * 30)
        assert ll.first_touch(ctx2, 5, [ll.Level(110.0, "swing_high", 1, "up")],
                              horizon=10) == -1

    def test_horizon_is_clamped_to_the_end_of_the_frame(self):
        highs = [100.0] * 20
        highs[18] = 130.0
        ctx = self._ctx(highs, [99.0] * 20, [99.5] * 20)
        # horizon runs past the frame; must clamp, not raise
        assert ll.first_touch(ctx, 5, [ll.Level(110.0, "swing_high", 1, "up")],
                              horizon=10_000) == 0

    def test_degenerate_windows_return_minus_one(self):
        ctx = self._ctx([100.0] * 20, [99.0] * 20, [99.5] * 20)
        lv = [ll.Level(110.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, lv, horizon=0) == -1      # end == t
        assert ll.first_touch(ctx, 19, lv, horizon=10) == -1    # t is the last bar
        assert ll.first_touch(ctx, 5, [], horizon=10) == -1     # no runners

    def test_nearness_is_measured_from_the_snapshot_close_and_no_other_bar(self):
        """Both levels are hit by bar 8, and the tie must be broken from the close
        of the SNAPSHOT bar t=5 — not from any other bar in the window.

        Three different reference closes give three different winners here, so this
        pins the choice rather than merely being consistent with it:
          close[5]  = 100.0 -> up 104 is 4.0 away, down 93 is 7.0  -> UP   (correct)
          close[6]  =  94.0 -> up 104 is 10.0 away, down 93 is 1.0 -> down
          close[8]  =  95.0 -> up 104 is 9.0 away,  down 93 is 2.0 -> down
        Bar 6 is deliberately pushed low WITHOUT touching either level (its low is
        93.5, above the 93 level), so it changes the close without stealing the touch.
        """
        highs = [100.5] * 20
        lows = [99.5] * 20
        closes = [100.0] * 20
        highs[6], lows[6], closes[6] = 95.5, 93.5, 94.0     # near-miss, moves close
        highs[8], lows[8], closes[8] = 105.0, 92.0, 95.0    # spans both levels
        ctx = self._ctx(highs, lows, closes)
        levels = [ll.Level(93.0, "swing_low", 1, "down"),
                  ll.Level(104.0, "swing_high", 1, "up")]
        assert ll.first_touch(ctx, 5, levels, horizon=10) == 1


def _walk_bars(n=900, seed=4, start="2026-01-05"):
    rng = np.random.default_rng(seed)
    walk = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
    return pd.DataFrame({"open": walk, "high": walk + 0.4, "low": walk - 0.4,
                         "close": walk, "volume": 100.0},
                        index=pd.date_range(start, periods=n, freq="15min", tz="UTC"))


class TestBuildDataset:
    def test_dataset_shapes_and_meta_alignment(self):
        n = 900
        rng = np.random.default_rng(4)
        walk = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
        bars = pd.DataFrame({"open": walk, "high": walk + 0.4, "low": walk - 0.4,
                             "close": walk, "volume": 100.0},
                            index=pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC"))
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(bars, params, horizon=20, warmup=250, stride=5)
        assert data.X.shape[0] == len(meta)
        assert data.X.shape[1] == len(ll.FEATURE_NAMES)
        assert len(data.chosen) == data.snap_id.max() + 1
        assert len(data.day_id) == len(data.chosen)
        assert set(meta.columns) >= {"snapshot_ts", "price", "kind", "formation_idx",
                                     "dist_atr", "side", "snap_id"}

    def test_no_snapshot_can_see_past_its_horizon(self):
        # a dataset built on the first half must equal the same rows built on the whole
        n = 900
        rng = np.random.default_rng(9)
        walk = 100.0 + np.cumsum(rng.normal(0, 0.3, n))
        idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
        bars = pd.DataFrame({"open": walk, "high": walk + 0.4, "low": walk - 0.4,
                             "close": walk, "volume": 100.0}, index=idx)
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        full, meta_full = ll.build_dataset(bars, params, horizon=20, warmup=250, stride=5)
        cut = 600
        part, meta_part = ll.build_dataset(bars.iloc[:cut], params, horizon=20,
                                           warmup=250, stride=5)
        common = meta_part.snapshot_ts.unique()
        common = common[common <= meta_full.snapshot_ts.max()]
        # every shared snapshot must have produced identical level prices
        a = meta_full[meta_full.snapshot_ts.isin(common)].sort_values(
            ["snapshot_ts", "price"]).price.to_numpy()
        b = meta_part[meta_part.snapshot_ts.isin(common)].sort_values(
            ["snapshot_ts", "price"]).price.to_numpy()
        assert a == pytest.approx(b)

    def test_empty_choice_sets_are_dropped(self):
        # a perfectly flat frame produces no un-swept structure worth ranking
        n = 400
        flat = np.full(n, 100.0)
        bars = pd.DataFrame({"open": flat, "high": flat, "low": flat,
                             "close": flat, "volume": 100.0},
                            index=pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC"))
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(bars, params, horizon=20, warmup=250, stride=5)
        assert data.X.shape[0] == len(meta)
        if len(data.chosen):
            assert np.bincount(data.snap_id, minlength=data.n_snap).min() >= 1

    # ----------------------------------------------------------- hardening

    def test_the_flat_frame_really_does_yield_an_empty_dataset(self):
        """The plan's `test_empty_choice_sets_are_dropped` guards its only real
        assertion behind `if len(data.chosen):`, which is False here — so it
        asserts nothing. Pin the actual outcome, including the meta columns of
        the empty return path."""
        n = 400
        flat = np.full(n, 100.0)
        bars = pd.DataFrame({"open": flat, "high": flat, "low": flat,
                             "close": flat, "volume": 100.0},
                            index=pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC"))
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(bars, params, horizon=20, warmup=250, stride=5)
        assert data.X.shape == (0, len(ll.FEATURE_NAMES))
        assert len(data.chosen) == 0 and len(data.snap_id) == 0 and len(data.day_id) == 0
        assert len(meta) == 0
        assert set(meta.columns) >= {"snap_id", "snapshot_ts", "price", "kind",
                                     "formation_idx", "side", "dist_atr"}

    def test_chosen_is_a_global_row_index_inside_its_own_snapshot(self):
        """The cursor offset is the whole ballgame. `chosen` indexes the STACKED X,
        not the per-snapshot block, so an implementation that stored the local
        `pick` would point into snapshot 0's rows for every later snapshot."""
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(_walk_bars(), params, horizon=20,
                                      warmup=250, stride=5)
        picked = np.flatnonzero(data.chosen >= 0)
        assert len(picked) > 0, "fixture must produce at least one touched snapshot"
        assert picked.max() > 0, "fixture must exercise a snapshot after the first"
        for s in picked:
            row = data.chosen[s]
            assert 0 <= row < data.X.shape[0]
            assert data.snap_id[row] == s
            assert int(meta.iloc[row].snap_id) == s

    def test_meta_rows_are_positionally_aligned_with_the_feature_rows(self):
        """meta.iloc[i] must describe X[i]. Recompute column 0 from meta's own
        dist_atr: log_dist_atr == log1p(|dist_atr|)."""
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(_walk_bars(), params, horizon=20,
                                      warmup=250, stride=5)
        assert len(meta) > 0
        col = ll.FEATURE_NAMES.index("log_dist_atr")
        expected = np.log1p(np.abs(meta.dist_atr.to_numpy()))
        assert data.X[:, col] == pytest.approx(expected)
        # and the side one-hot must agree with meta's own side string
        side_col = ll.FEATURE_NAMES.index("side_up")
        assert data.X[:, side_col] == pytest.approx(
            (meta.side.to_numpy() == "up").astype(float))

    def test_snapshot_ids_are_contiguous_and_every_snapshot_has_rows(self):
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(_walk_bars(), params, horizon=20,
                                      warmup=250, stride=5)
        counts = np.bincount(data.snap_id, minlength=data.n_snap)
        assert len(counts) == data.n_snap
        assert counts.min() >= 1                       # no empty snapshot survived
        assert list(np.unique(data.snap_id)) == list(range(data.n_snap))

    def test_stride_and_warmup_bound_the_snapshot_positions(self):
        bars = _walk_bars()
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        horizon, warmup, stride = 20, 250, 5
        data, meta = ll.build_dataset(bars, params, horizon=horizon,
                                      warmup=warmup, stride=stride)
        pos = bars.index.get_indexer(pd.DatetimeIndex(meta.snapshot_ts.unique()))
        assert pos.min() >= warmup
        # last = n - horizon - 1, so no snapshot can see past the frame's end
        assert pos.max() <= len(bars) - horizon - 1
        # every snapshot sits on the stride grid anchored at warmup
        assert set(((pos - warmup) % stride).tolist()) == {0}

    def test_warmup_defaults_to_scan_bars(self):
        bars = _walk_bars()
        params = ll.LevelParams(scan_bars=300, atr_pctile_window=100)
        data, meta = ll.build_dataset(bars, params, horizon=20, stride=25)
        pos = bars.index.get_indexer(pd.DatetimeIndex(meta.snapshot_ts.unique()))
        assert pos.min() >= 300

    def test_day_id_matches_the_snapshot_bars_utc_date(self):
        bars = _walk_bars(n=900, start="2026-03-02")
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(bars, params, horizon=20, warmup=250, stride=5)
        first_ts = meta.groupby("snap_id").snapshot_ts.first()
        expected = np.array([ts.date().toordinal() for ts in first_ts])
        assert list(data.day_id) == list(expected)
        assert len(np.unique(data.day_id)) > 1, "fixture must span several days"

    def test_meta_dist_atr_is_signed_and_matches_the_side(self):
        params = ll.LevelParams(scan_bars=200, atr_pctile_window=100)
        data, meta = ll.build_dataset(_walk_bars(), params, horizon=20,
                                      warmup=250, stride=5)
        up = meta[meta.side == "up"]
        down = meta[meta.side == "down"]
        assert len(up) > 0 and len(down) > 0
        assert (up.dist_atr > 0).all()      # up levels sit above the close
        assert (down.dist_atr < 0).all()    # down levels sit below it


class TestScopeBoundary:
    """The spec: research and chart only. Nothing imports into the trading path."""

    def _source(self, rel):
        root = Path(__file__).parent.parent.parent
        return (root / rel).read_text()

    def test_liquidity_modules_do_not_import_the_trading_path(self):
        forbidden = ("src.strategies", "src.risk", "src.execution",
                     "src.portfolio", "src.connectors")
        for rel in ("src/microstructure/liquidity_levels.py",
                    "src/microstructure/liquidity_race.py",
                    "scripts/research_liquidity_race.py",
                    "scripts/check_liquidity_parity.py"):
            src = self._source(rel)
            for mod in forbidden:
                assert mod not in src, f"{rel} must not reference {mod}"

    def test_the_trading_path_does_not_import_the_liquidity_modules(self):
        root = Path(__file__).parent.parent.parent
        for sub in ("src/strategies", "src/risk", "src/execution"):
            for py in (root / sub).rglob("*.py"):
                text = py.read_text()
                assert "liquidity_levels" not in text or "monitoring" in text, (
                    f"{py} imports the liquidity race marking tool — that is a "
                    f"strategy decision behind the full backtest.md 8-gate process")
                assert "liquidity_race" not in text, f"{py} imports liquidity_race"
