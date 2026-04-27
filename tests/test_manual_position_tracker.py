"""Tests for ManualPositionTracker — directional lock against manual MT5 positions."""

import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from src.monitoring.manual_position_tracker import (
    ManualPositionTracker,
    is_manual_position,
)
from src.core.types import Position, Symbol
from src.core.constants import PositionSide


def _make_symbol(ticker: str = "XAUUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        exchange="MT5",
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("100"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


def _make_position(
    side: PositionSide,
    strategy: str = "manual",
    ticker: str = "XAUUSD",
) -> Position:
    return Position(
        symbol=_make_symbol(ticker),
        side=side,
        quantity=Decimal("0.02"),
        entry_price=Decimal("3300.00"),
        current_price=Decimal("3310.00"),
        metadata={"strategy": strategy},
    )


# ─── is_manual_position ──────────────────────────────────────────────


class TestIsManualPosition:

    @pytest.mark.parametrize("tag", ["manual", "manual_gut", "manual_rules", "unknown", "", "none"])
    def test_manual_tags_detected(self, tag: str):
        pos = _make_position(PositionSide.LONG, strategy=tag)
        assert is_manual_position(pos) is True

    @pytest.mark.parametrize("tag", ["momentum", "kalman_regime", "breakout", "sbr", "mini_medallion"])
    def test_bot_strategies_not_manual(self, tag: str):
        pos = _make_position(PositionSide.LONG, strategy=tag)
        assert is_manual_position(pos) is False


# ─── ManualPositionTracker ────────────────────────────────────────────


class TestManualPositionTracker:

    def test_empty_on_init(self):
        tracker = ManualPositionTracker()
        assert tracker.has_manual_positions is False
        assert tracker.get_manual_directions() == set()

    def test_detects_manual_long(self):
        tracker = ManualPositionTracker()
        positions = {"1001": _make_position(PositionSide.LONG, strategy="manual")}
        events = tracker.refresh(positions)

        assert tracker.has_manual_positions is True
        assert tracker.get_manual_directions() == {"LONG"}
        assert events == {"1001": "OPENED"}

    def test_detects_manual_short(self):
        tracker = ManualPositionTracker()
        positions = {"1002": _make_position(PositionSide.SHORT, strategy="manual")}
        tracker.refresh(positions)

        assert tracker.get_manual_directions() == {"SHORT"}

    def test_ignores_bot_positions(self):
        tracker = ManualPositionTracker()
        positions = {
            "1001": _make_position(PositionSide.LONG, strategy="momentum"),
            "1002": _make_position(PositionSide.SHORT, strategy="kalman_regime"),
        }
        tracker.refresh(positions)

        assert tracker.has_manual_positions is False
        assert tracker.get_manual_directions() == set()

    def test_mixed_manual_and_bot(self):
        tracker = ManualPositionTracker()
        positions = {
            "1001": _make_position(PositionSide.LONG, strategy="manual"),
            "1002": _make_position(PositionSide.SHORT, strategy="momentum"),
        }
        tracker.refresh(positions)

        assert tracker.has_manual_positions is True
        # Only the manual position direction should appear
        assert tracker.get_manual_directions() == {"LONG"}

    def test_both_directions_manual(self):
        tracker = ManualPositionTracker()
        positions = {
            "1001": _make_position(PositionSide.LONG, strategy="manual"),
            "1002": _make_position(PositionSide.SHORT, strategy="manual_gut"),
        }
        tracker.refresh(positions)

        assert tracker.get_manual_directions() == {"LONG", "SHORT"}

    def test_symbol_filter(self):
        tracker = ManualPositionTracker()
        positions = {
            "1001": _make_position(PositionSide.LONG, strategy="manual", ticker="XAUUSD"),
            "1002": _make_position(PositionSide.SHORT, strategy="manual", ticker="BTCUSD"),
        }
        tracker.refresh(positions)

        assert tracker.get_manual_directions(symbol="XAUUSD") == {"LONG"}
        assert tracker.get_manual_directions(symbol="BTCUSD") == {"SHORT"}
        assert tracker.get_manual_directions(symbol="EURUSD") == set()
        # No filter → both
        assert tracker.get_manual_directions() == {"LONG", "SHORT"}

    def test_symbol_filter_fuzzy_match(self):
        """Broker suffixed ticker (XAUUSD.w) should match config ticker (XAUUSD)."""
        tracker = ManualPositionTracker()
        positions = {
            "1001": _make_position(PositionSide.LONG, strategy="manual", ticker="XAUUSD.w"),
        }
        tracker.refresh(positions)

        assert tracker.get_manual_directions(symbol="XAUUSD") == {"LONG"}
        assert tracker.get_manual_directions(symbol="XAUUSD.w") == {"LONG"}

    def test_closed_event_emitted(self):
        tracker = ManualPositionTracker()

        # First tick: manual position open
        positions = {"1001": _make_position(PositionSide.LONG, strategy="manual")}
        tracker.refresh(positions)
        assert tracker.has_manual_positions is True

        # Second tick: position gone
        events = tracker.refresh({})
        assert tracker.has_manual_positions is False
        assert events == {"1001": "CLOSED"}

    def test_no_events_on_steady_state(self):
        tracker = ManualPositionTracker()
        positions = {"1001": _make_position(PositionSide.LONG, strategy="manual")}

        tracker.refresh(positions)
        events = tracker.refresh(positions)  # same state
        assert events == {}

    def test_get_manual_positions_returns_copy(self):
        tracker = ManualPositionTracker()
        positions = {"1001": _make_position(PositionSide.LONG, strategy="manual")}
        tracker.refresh(positions)

        result = tracker.get_manual_positions()
        assert "1001" in result
        # Mutating the returned dict should not affect internal state
        result.clear()
        assert tracker.has_manual_positions is True
