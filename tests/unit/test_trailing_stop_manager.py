"""
Tests for TrailingStopManager's time-stop resolution.

The design rule under test (Carmack: worst-case beats average-case): the
operator's configured `time_stop_minutes` is a hard CEILING. A strategy that
publishes its own per-trade time stop may only TIGHTEN the hold, never extend
it past what the config allows.
"""

import datetime as dt
from decimal import Decimal

import pytest

from src.core.constants import PositionSide
from src.core.types import Position, Symbol
from src.risk.trailing_stop_manager import TrailingStopManager


GOLD = Symbol(ticker="XAUUSD", value_per_lot=Decimal("100"))


class RecordingConnector:
    """Records close/modify calls instead of talking to MT5."""

    def __init__(self):
        self.closed = []
        self.modified = []

    def close_position(self, position_id, symbol):
        self.closed.append((position_id, symbol))
        return True

    def modify_position(self, position_id, stop_loss, take_profit=None):
        self.modified.append((position_id, stop_loss, take_profit))
        return True


def make_config(strategy_time_stop=None, global_time_stop=None):
    overrides = {}
    if strategy_time_stop is not None:
        overrides['wavelet_cycle'] = {
            'time_stop_minutes': strategy_time_stop,
            'disable_be_lock': True,
        }
    trail = {'enabled': True, 'strategy_overrides': overrides}
    if global_time_stop is not None:
        trail['time_stop_minutes'] = global_time_stop
    return {'risk': {'trailing_stop': trail}}


def make_position(minutes_held, strategy='wavelet_cycle'):
    """A LONG gold position opened `minutes_held` ago, flat on P&L."""
    opened = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_held)
    return Position(
        symbol=GOLD,
        side=PositionSide.LONG,
        quantity=Decimal("0.02"),
        entry_price=Decimal("4000.00"),
        current_price=Decimal("4000.00"),
        stop_loss=Decimal("3990.00"),
        metadata={'strategy': strategy, 'time_setup': opened.timestamp()},
    )


class TestDynamicTimeStop:
    """A per-trade time stop published by the strategy, keyed by MT5 ticket."""

    def test_dynamic_time_stop_closes_before_the_configured_ceiling(self):
        # Strategy asked for 120 min; config ceiling is 1080. Held 130 min.
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", 120)

        mgr.update({"555": make_position(minutes_held=130)}, conn)

        assert conn.closed == [("555", "XAUUSD")]

    def test_dynamic_time_stop_holds_the_position_until_its_own_limit(self):
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", 120)

        mgr.update({"555": make_position(minutes_held=90)}, conn)

        assert conn.closed == []

    def test_configured_ceiling_caps_a_longer_dynamic_time_stop(self):
        # Strategy asked to hold 2000 min — longer than the operator allows.
        # The 1080 ceiling must still fire at 1200 min held.
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", 2000)

        mgr.update({"555": make_position(minutes_held=1200)}, conn)

        assert conn.closed == [("555", "XAUUSD")]

    def test_dynamic_value_applies_when_no_ceiling_is_configured(self):
        mgr = TrailingStopManager(make_config())  # no override, no global
        conn = RecordingConnector()
        mgr.register_time_stop("555", 120)

        mgr.update({"555": make_position(minutes_held=130)}, conn)

        assert conn.closed == [("555", "XAUUSD")]

    def test_unregistered_position_falls_back_to_the_configured_value(self):
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()

        mgr.update({"555": make_position(minutes_held=130)}, conn)

        assert conn.closed == []

    def test_cleanup_closed_forgets_the_dynamic_time_stop(self):
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", 120)

        mgr.cleanup_closed(open_tickets=set())
        # Ticket 555 reused by a later, unrelated trade: the stale 120-minute
        # limit must not close it.
        mgr.update({"555": make_position(minutes_held=130)}, conn)

        assert conn.closed == []

    @pytest.mark.parametrize("bad_value", [None, 0, -5])
    def test_register_ignores_non_positive_values(self, bad_value):
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", bad_value)

        mgr.update({"555": make_position(minutes_held=130)}, conn)

        assert conn.closed == []

    def test_dynamic_time_stop_is_per_ticket(self):
        mgr = TrailingStopManager(make_config(strategy_time_stop=1080))
        conn = RecordingConnector()
        mgr.register_time_stop("555", 120)

        mgr.update(
            {"555": make_position(minutes_held=130),
             "666": make_position(minutes_held=130)},
            conn,
        )

        assert conn.closed == [("555", "XAUUSD")]
