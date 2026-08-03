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
