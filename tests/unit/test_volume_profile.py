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
