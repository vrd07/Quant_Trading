"""Manual Position Tracker — identifies manual MT5 positions and exposes their directions.

The bot's signal pipeline uses this tracker to enforce a directional lock:
  - Bot signal in the SAME direction as a manual trade → allowed (stacking).
  - Bot signal in the OPPOSITE direction → blocked (directional lock).

How manual positions are identified:
  The MT5 connector tags each position's metadata['strategy'] from the order
  comment.  Positions opened directly in MT5 (no bot comment) are tagged
  'manual'.  We use the same tag set as ManualTradeMonitor to stay consistent.

Legends applied:
  - Carmack: pure refresh() → no I/O; side-effects (logging) in orchestrator.
  - TJ:      single responsibility — track directions, nothing else.
  - geohot:  simplest thing that works — a dict scan + set return.
"""

from typing import Dict, Optional, Set

from ..core.types import Position
from ..core.constants import PositionSide


# Shared with ManualTradeMonitor._MANUAL_TAGS (duplicated literally to avoid
# cross-import coupling — only 6 strings).
_MANUAL_TAGS: frozenset = frozenset({
    "manual", "manual_gut", "manual_rules", "unknown", "", "none",
})


def is_manual_position(position: Position) -> bool:
    """Pure: true iff the position's strategy metadata marks it as manual."""
    tag = str((position.metadata or {}).get("strategy", "")).strip().lower()
    return tag in _MANUAL_TAGS


class ManualPositionTracker:
    """Tracks manually opened MT5 positions and their directions.

    Call ``refresh(positions)`` every loop tick with the current MT5 positions
    dict.  Then query ``get_manual_directions()`` to discover which directions
    have active manual trades, optionally filtered by symbol.

    Thread-safety: not required — called only from the single-threaded main loop.
    """

    def __init__(self) -> None:
        self._manual_positions: Dict[str, Position] = {}
        # Tickets seen on the previous refresh — used for appearance/disappearance logging.
        self._prev_tickets: Set[str] = set()

    def refresh(self, positions: Dict[str, Position]) -> Dict[str, str]:
        """Scan positions and update the internal manual-position set.

        Args:
            positions: ticket → Position dict from ``connector.get_positions()``.

        Returns:
            Dict of {ticket: event} where event is ``'OPENED'`` or ``'CLOSED'``
            for manual positions that appeared or disappeared since the last
            refresh.  Empty dict when nothing changed.  The caller (main loop)
            uses this to emit log lines.
        """
        new_manual: Dict[str, Position] = {}
        for ticket, pos in positions.items():
            if is_manual_position(pos):
                new_manual[ticket] = pos

        current_tickets = set(new_manual.keys())
        events: Dict[str, str] = {}

        for ticket in current_tickets - self._prev_tickets:
            events[ticket] = "OPENED"
        for ticket in self._prev_tickets - current_tickets:
            events[ticket] = "CLOSED"

        self._manual_positions = new_manual
        self._prev_tickets = current_tickets
        return events

    def get_manual_directions(self, symbol: Optional[str] = None) -> Set[str]:
        """Return the set of directions with active manual trades.

        Returns a subset of ``{'LONG', 'SHORT'}`` (or empty set if no manual
        positions exist).  When *symbol* is provided, only manual positions on
        that symbol are considered.
        """
        directions: Set[str] = set()
        for pos in self._manual_positions.values():
            if symbol is not None:
                pos_ticker = pos.symbol.ticker if pos.symbol else ""
                # Fuzzy match: handle broker suffixes (XAUUSD.w ↔ XAUUSD)
                base_pos = pos_ticker.split(".")[0].upper()
                base_query = symbol.split(".")[0].upper()
                if base_pos != base_query:
                    continue

            side = getattr(pos, "side", None)
            if side == PositionSide.LONG:
                directions.add("LONG")
            elif side == PositionSide.SHORT:
                directions.add("SHORT")
        return directions

    def get_manual_positions(self) -> Dict[str, Position]:
        """Return the current snapshot of manual positions (ticket → Position)."""
        return dict(self._manual_positions)

    @property
    def has_manual_positions(self) -> bool:
        """True when at least one manual position is currently tracked."""
        return len(self._manual_positions) > 0
