"""StrategyAllowlist — the safety net extracted from the old confluence gate."""
import pytest

from src.core.types import Signal
from src.core.constants import OrderSide
from src.strategies.strategy_allowlist import (
    StrategyAllowlist,
    SOLO_ALLOWED,
    KILL_LIST,
)


def _sig(strategy_name: str) -> Signal:
    # Every Signal field has a default; strategy_name and side are all the
    # allowlist reads. Note the strength field is `strength`, not `confidence`.
    return Signal(
        strategy_name=strategy_name,
        side=OrderSide.BUY,
        strength=0.7,
    )


class TestStrategyAllowlist:
    def test_allowlisted_strategy_passes_through(self):
        gate = StrategyAllowlist()
        sig = _sig("kalman_regime")
        assert gate.filter("XAUUSD", [("kalman_regime", sig)]) == [sig]

    def test_kill_list_strategy_is_dropped(self):
        gate = StrategyAllowlist()
        assert gate.filter("XAUUSD", [("vwap", _sig("vwap"))]) == []

    def test_unknown_strategy_is_dropped(self):
        """Not allowlisted == not executable. Default deny."""
        gate = StrategyAllowlist()
        assert gate.filter("XAUUSD", [("brand_new", _sig("brand_new"))]) == []

    def test_mixed_batch_keeps_only_allowlisted(self):
        gate = StrategyAllowlist()
        keep = _sig("squeeze_breakout")
        out = gate.filter("XAUUSD", [
            ("vwap", _sig("vwap")),
            ("squeeze_breakout", keep),
            ("smc_ob", _sig("smc_ob")),
        ])
        assert out == [keep]

    def test_empty_input_returns_empty(self):
        assert StrategyAllowlist().filter("XAUUSD", []) == []

    def test_order_is_preserved(self):
        gate = StrategyAllowlist()
        a, b = _sig("kalman_regime"), _sig("bos_structure")
        out = gate.filter("XAUUSD", [("kalman_regime", a), ("bos_structure", b)])
        assert out == [a, b]

    def test_all_eight_survivors_are_allowlisted(self):
        """Guards the spec's behaviour-identity claim."""
        assert SOLO_ALLOWED == {
            "kalman_regime", "squeeze_breakout", "stoch_pullback",
            "bos_structure", "london_breakout", "monday_drift",
            "index_overnight", "wednesday_drift",
        }

    def test_kill_list_covers_both_deletion_rounds(self):
        assert KILL_LIST == {
            "breakout", "mean_reversion", "supply_demand",
            "descending_channel_breakout", "mini_medallion",
            "continuation_breakout",
            "vwap", "momentum", "sbr", "asia_range_fade", "smc_ob",
            "fibonacci_retracement", "wavelet_cycle", "ema200_nasdaq",
        }

    def test_allowlist_and_kill_list_are_disjoint(self):
        assert not (SOLO_ALLOWED & KILL_LIST)

    def test_accepts_config_dict_but_ignores_it(self):
        """Call sites pass the old gate config; it must not crash or re-enable anything."""
        gate = StrategyAllowlist({"enabled": False, "window_minutes": 120})
        assert gate.filter("XAUUSD", [("vwap", _sig("vwap"))]) == []
