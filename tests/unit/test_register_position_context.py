"""
Tests for TradingSystem._register_position_context — the per-ticket bookkeeping
that runs once MT5 hands back a ticket for a freshly placed order.

This is the seam where a strategy's fire-time context survives the fact that
MT5 later reconstructs the position from nothing but its order comment:
  * confidence, for the confidence-based reversal rule
  * regime/strength, for the trade journal
  * time_stop_minutes, for the dynamic per-trade time stop (wavelet_cycle
    publishes 1.5 x its detected cycle length)
"""

from decimal import Decimal
from unittest.mock import MagicMock

from src.core.constants import OrderSide
from src.core.types import Order, Signal, Symbol
from src.main import TradingSystem


GOLD = Symbol(ticker="XAUUSD", value_per_lot=Decimal("100"))


def _make_system():
    system = object.__new__(TradingSystem)
    system.logger = MagicMock()
    system.trade_journal = MagicMock()
    system._trailing_stop_mgr = MagicMock()
    system._open_position_confidence = {}
    return system


def _make_signal(**metadata):
    return Signal(
        strategy_name="wavelet_cycle",
        symbol=GOLD,
        side=OrderSide.BUY,
        strength=0.7,
        entry_price=Decimal("4000.00"),
        metadata=metadata,
    )


def _make_order(ticket=777):
    return Order(symbol=GOLD, side=OrderSide.BUY, metadata={'mt5_ticket': ticket})


def test_publishes_the_strategy_time_stop_to_the_trailing_stop_manager():
    system = _make_system()

    system._register_position_context(
        _make_order(), _make_signal(time_stop_minutes=270), "XAUUSD"
    )

    system._trailing_stop_mgr.register_time_stop.assert_called_once_with("777", 270)


def test_no_time_stop_registered_when_the_strategy_publishes_none():
    system = _make_system()

    system._register_position_context(_make_order(), _make_signal(), "XAUUSD")

    system._trailing_stop_mgr.register_time_stop.assert_not_called()


def test_nothing_is_registered_without_an_mt5_ticket():
    system = _make_system()
    order = Order(symbol=GOLD, side=OrderSide.BUY, metadata={})

    system._register_position_context(
        order, _make_signal(time_stop_minutes=270), "XAUUSD"
    )

    system._trailing_stop_mgr.register_time_stop.assert_not_called()
    assert system._open_position_confidence == {}


def test_time_stop_survives_a_trade_journal_failure():
    # The journal is best-effort; a broken journal must not cost the position
    # its exit rule.
    system = _make_system()
    system.trade_journal.record_signal_context.side_effect = RuntimeError("disk full")

    system._register_position_context(
        _make_order(), _make_signal(time_stop_minutes=270), "XAUUSD"
    )

    system._trailing_stop_mgr.register_time_stop.assert_called_once_with("777", 270)


def test_confidence_is_recorded_for_the_reversal_rule():
    system = _make_system()

    system._register_position_context(
        _make_order(), _make_signal(confidence=82.0), "XAUUSD"
    )

    assert system._open_position_confidence["XAUUSD"]["777"] == 82.0


def test_confidence_falls_back_to_signal_strength():
    system = _make_system()

    system._register_position_context(_make_order(), _make_signal(), "XAUUSD")

    assert system._open_position_confidence["XAUUSD"]["777"] == 70.0


def test_signal_context_reaches_the_trade_journal():
    system = _make_system()

    system._register_position_context(
        _make_order(), _make_signal(confidence=82.0), "XAUUSD"
    )

    kwargs = system.trade_journal.record_signal_context.call_args.kwargs
    assert kwargs['strategy'] == "wavelet_cycle"
    assert kwargs['confidence'] == 82.0
    assert kwargs['signal_strength'] == 0.7
