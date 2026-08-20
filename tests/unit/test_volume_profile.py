"""Unit tests for src/microstructure/volume_profile.py — synthetic inputs only."""
import numpy as np
import pytest

from src.microstructure import volume_profile as vp


class TestGrid:
    def test_row_index_is_absolute_not_session_anchored(self):
        # The same price must land in the same row regardless of what else traded.
        assert vp.row_index(4493.15, 0.10) == vp.row_index(4493.15, 0.10)
        assert vp.row_index(4493.15, 0.10) == 44931
        assert vp.row_index(4493.19, 0.10) == 44931
        assert vp.row_index(4493.20, 0.10) == 44932

    def test_price_exactly_on_a_row_boundary_goes_to_the_upper_row(self):
        assert vp.row_index(4493.20, 0.10) == 44932
        assert vp.row_low(44932, 0.10) == pytest.approx(4493.20, abs=1e-9)

    def test_boundary_prices_survive_floating_point(self):
        """4493.20 / 0.10 == 44931.99999999999 in IEEE754.

        A plain floor() puts these prices one row too low, and does so exactly
        at the round numbers traders care about. Worse, it would drift silently
        against the MQL5 port. The snap tolerance is what prevents both.
        """
        for price, expected in [(4493.20, 44932), (1234.60, 12346),
                                (100.30, 1003), (2000.70, 20007),
                                (4493.10, 44931), (4493.30, 44933)]:
            assert vp.row_index(price, 0.10) == expected, price

    def test_snap_tolerance_does_not_swallow_real_within_row_prices(self):
        # 4493.19 is genuinely inside row 44931 and must stay there.
        assert vp.row_index(4493.19, 0.10) == 44931
        assert vp.row_index(4493.15, 0.10) == 44931

    def test_row_edges_and_mid(self):
        assert vp.row_low(44931, 0.10) == pytest.approx(4493.10, abs=1e-9)
        assert vp.row_mid(44931, 0.10) == pytest.approx(4493.15, abs=1e-9)
        assert vp.row_high(44931, 0.10) == pytest.approx(4493.20, abs=1e-9)


class TestTickAccumulation:
    def test_each_tick_adds_one_at_its_mid_price(self):
        bid = np.array([100.00, 100.00, 100.20])
        ask = np.array([100.02, 100.02, 100.22])
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams(row_size=0.10))
        # mids are 100.01, 100.01, 100.21 -> rows 1000, 1000, 1002
        assert h.accepted == 3
        assert h.rejected == 0
        assert h.volumes.sum() == pytest.approx(3.0, abs=0.0)
        assert h.volumes[vp.row_index(100.01, 0.10) - h.min_row] == pytest.approx(2.0, abs=0.0)

    def test_wide_spread_ticks_are_rejected_and_counted(self):
        bid = np.array([100.00, 100.00])
        ask = np.array([100.02, 101.50])   # second spread is 1.50 > 1.00
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams(row_size=0.10,
                                                          max_spread_usd=1.00))
        assert h.accepted == 1
        assert h.rejected == 1
        assert h.volumes.sum() == pytest.approx(1.0, abs=0.0)

    def test_sum_of_rows_always_equals_accepted(self):
        rng = np.random.default_rng(7)
        bid = 4000 + rng.normal(0, 5, 5000)
        ask = bid + rng.uniform(0.01, 0.30, 5000)
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams())
        assert h.volumes.sum() == pytest.approx(float(h.accepted), abs=0.0)

    def test_bid_and_ask_price_modes(self):
        bid = np.array([100.00])
        ask = np.array([100.40])
        hb = vp.accumulate_ticks(bid, ask, vp.ProfileParams(tick_price_mode="bid"))
        ha = vp.accumulate_ticks(bid, ask, vp.ProfileParams(tick_price_mode="ask"))
        assert hb.min_row == vp.row_index(100.00, 0.10)
        assert ha.min_row == vp.row_index(100.40, 0.10)


class TestM1Fallback:
    def test_bar_volume_spreads_uniformly_across_touched_rows(self):
        high = np.array([100.25])
        low = np.array([100.00])
        vol = np.array([300.0])
        h = vp.accumulate_m1_bars(high, low, vol, vp.ProfileParams(row_size=0.10))
        # rows 1000, 1001, 1002 -> three rows, 100 each
        assert h.volumes.size == 3
        assert h.volumes == pytest.approx([100.0, 100.0, 100.0], abs=1e-9)
        assert h.volumes.sum() == pytest.approx(300.0, abs=1e-9)

    def test_single_row_bar_gets_all_its_volume(self):
        h = vp.accumulate_m1_bars(np.array([100.05]), np.array([100.01]),
                                  np.array([42.0]), vp.ProfileParams(row_size=0.10))
        assert h.volumes.size == 1
        assert h.volumes[0] == pytest.approx(42.0, abs=1e-9)


def hist_from(volumes, min_row=1000, row_size=0.10):
    v = np.asarray(volumes, dtype=float)
    return vp.Histogram(min_row, v, row_size, accepted=int(v.sum()), rejected=0)


class TestPOC:
    def test_poc_is_the_max_volume_row(self):
        assert vp.poc_index(np.array([1.0, 5.0, 2.0])) == 1

    def test_poc_tie_goes_to_the_row_nearest_the_range_midpoint(self):
        # rows 0..4 occupied; midpoint index is 2. Tie between 1 and 3 -> ...
        # |1-2| == |3-2|, so the argmin picks the LOWER index per spec.
        v = np.array([1.0, 5.0, 1.0, 5.0, 1.0])
        assert vp.poc_index(v) == 1

    def test_poc_tie_resolves_toward_the_midpoint_when_distances_differ(self):
        # occupied 0..6, midpoint 3. Tie between 1 and 4 -> 4 is nearer.
        v = np.array([1.0, 5.0, 1.0, 1.0, 5.0, 1.0, 1.0])
        assert vp.poc_index(v) == 4


class TestValueArea:
    def test_single_row_expansion_absorbs_the_bigger_neighbour(self):
        #            0    1    2    3    4
        v = np.array([1.0, 8.0, 10.0, 2.0, 1.0])   # total 22, target 15.4
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # start 10; above=2 below=8 -> take below (18 >= 15.4). stop.
        assert (lo, hi) == (1, 2)

    def test_tie_goes_to_the_row_nearer_the_poc(self):
        #            0    1    2     3    4
        v = np.array([9.0, 3.0, 10.0, 3.0, 9.0])   # total 34, target 23.8
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # 10; above=3 below=3 tie, equidistant -> above (13); then above=9 below=3
        # -> above (22); then below=3 -> (25) >= 23.8
        assert (lo, hi) == (1, 4)

    def test_equidistant_tie_takes_the_higher_row(self):
        v = np.array([1.0, 4.0, 10.0, 4.0, 1.0])   # total 20, target 14
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # 10; above=4 below=4, both distance 1 -> take ABOVE -> 14 >= 14, stop
        assert (lo, hi) == (2, 3)

    def test_one_side_exhausted_keeps_taking_the_other(self):
        v = np.array([10.0, 3.0, 3.0, 3.0])        # total 19, target 13.3
        lo, hi = vp.value_area(v, poc_i=0, target_frac=0.70, algorithm="single_row")
        assert (lo, hi) == (0, 2)

    def test_two_row_algorithm_absorbs_a_pair(self):
        v = np.array([1.0, 1.0, 10.0, 4.0, 4.0])   # total 20, target 14
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="two_row")
        # above pair 4+4=8 vs below pair 1+1=2 -> take above -> 18 >= 14
        assert (lo, hi) == (2, 4)

    @pytest.mark.parametrize("seed", range(25))
    def test_value_area_always_contains_at_least_the_target_fraction(self, seed):
        rng = np.random.default_rng(seed)
        v = rng.random(60) * 100
        poc = vp.poc_index(v)
        lo, hi = vp.value_area(v, poc, 0.70, "single_row")
        assert v[lo:hi + 1].sum() >= 0.70 * v.sum() - 1e-9

    def test_flat_profile_still_terminates(self):
        v = np.full(10, 5.0)
        lo, hi = vp.value_area(v, vp.poc_index(v), 0.70, "single_row")
        assert v[lo:hi + 1].sum() >= 0.70 * v.sum() - 1e-9

    def test_single_row_profile(self):
        v = np.array([7.0])
        assert vp.value_area(v, 0, 0.70, "single_row") == (0, 0)


class TestBuildProfile:
    def test_levels_use_row_edges_so_the_band_contains_its_volume(self):
        prof = vp.build_profile(hist_from([1.0, 8.0, 10.0, 2.0, 1.0]), vp.ProfileParams())
        assert prof.vpoc == pytest.approx(vp.row_mid(1002, 0.10), abs=1e-9)
        assert prof.val == pytest.approx(vp.row_low(1001, 0.10), abs=1e-9)
        assert prof.vah == pytest.approx(vp.row_high(1002, 0.10), abs=1e-9)
        assert prof.low == pytest.approx(vp.row_low(1000, 0.10), abs=1e-9)
        assert prof.high == pytest.approx(vp.row_high(1004, 0.10), abs=1e-9)

    def test_empty_histogram_returns_none(self):
        assert vp.build_profile(hist_from([]), vp.ProfileParams()) is None


from pathlib import Path


class TestScopeBoundary:
    """The spec: chart and research only. Nothing crosses into the trading path."""

    _MODULES = (
        "src/microstructure/volume_profile.py",
        "src/microstructure/profile_context.py",
        "scripts/check_volume_profile_parity.py",
        "scripts/calibrate_profile_shape.py",
    )
    _FORBIDDEN = ("src.strategies", "src.risk", "src.execution",
                  "src.portfolio", "src.connectors")

    def _root(self):
        return Path(__file__).parent.parent.parent

    def test_profile_modules_do_not_import_the_trading_path(self):
        for rel in self._MODULES:
            path = self._root() / rel
            if not path.exists():      # later tasks create these
                continue
            src = path.read_text()
            for mod in self._FORBIDDEN:
                assert mod not in src, f"{rel} must not reference {mod}"

    def test_the_trading_path_does_not_import_the_profile_modules(self):
        root = self._root()
        for sub in ("src/strategies", "src/risk", "src/execution"):
            for py in (root / sub).rglob("*.py"):
                src = py.read_text()
                assert "volume_profile" not in src, f"{py} must not import volume_profile"
                assert "profile_context" not in src, f"{py} must not import profile_context"
