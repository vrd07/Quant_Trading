"""
Trailing Stop Manager — Breakeven + Trail Stop Loss Management

This module monitors open positions and automatically moves the SL:

Stage 1 — Price reaches entry + 1×ATR:
    SL moved to BREAKEVEN (entry price) → worst case $0 loss

Stage 2 — Price reaches entry + 1.5×ATR:
    SL moved to entry + 0.5×ATR → locks in ~50% of expected profit

This is applied every loop tick and updates the SL via the MT5 connector's
MODIFY_ORDER command (requires EA_FileBridge.mq5 to support MODIFY_ORDER).
"""

from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timezone

from ..monitoring.logger import get_logger

logger = get_logger(__name__)


class TrailingStopManager:
    """
    Manages breakeven and trailing stop-loss for open positions.

    Call `update(positions, connector)` once per main loop iteration.
    Internally tracks which positions have already been moved to each stage
    so the same position is never updated twice for the same stage.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: Full system config dict. Reads from risk.trailing_stop section.
        """
        trail_cfg = config.get('risk', {}).get('trailing_stop', {})

        # Stage 1: move SL to breakeven when profit >= breakeven_atr_mult × ATR distance
        self.breakeven_atr_mult: float = trail_cfg.get('breakeven_atr_mult', 1.0)

        # Stage 2: move SL to partial lock when profit >= lock_atr_mult × ATR distance
        self.lock_atr_mult: float = trail_cfg.get('lock_atr_mult', 1.5)

        # Fraction of ATR to lock in at stage 2 (0.5 = lock in half the expected move)
        self.lock_fraction: float = trail_cfg.get('lock_fraction', 0.5)

        # Track upgrade stage per position ticket: 0=none, 1=breakeven, 2=locked
        self._stage: Dict[str, int] = {}

        # Track original entry and initial SL per ticket (to compute ATR distance)
        # Populated on first seen from position.metadata
        self._entry_price: Dict[str, float] = {}
        self._initial_sl: Dict[str, float] = {}
        self._initial_atr_dist: Dict[str, float] = {}  # abs(entry - initial_sl)

    def update(self, positions: dict, connector) -> None:
        """
        Check all open positions and move SL to breakeven/trail if criteria met.

        Args:
            positions: Dict[ticket_str -> Position] from connector.get_positions()
            connector: MT5Connector instance (must have modify_position)
        """
        if not positions:
            return

        for ticket_str, pos in positions.items():
            try:
                self._process_position(ticket_str, pos, connector)
            except Exception as e:
                logger.warning(f"TrailingStop error on ticket {ticket_str}: {e}")

    def _process_position(self, ticket_str: str, pos, connector) -> None:
        """Process a single position — upgrade SL stage if criteria are met."""
        # Get entry and current price
        entry = float(getattr(pos, 'entry_price', 0) or
                      pos.metadata.get('entry_price', 0) if hasattr(pos, 'metadata') else 0)
        current_sl = float(getattr(pos, 'stop_loss', 0) or
                           pos.metadata.get('sl', 0) if hasattr(pos, 'metadata') else 0)
        current_price = float(getattr(pos, 'current_price', 0) or
                              pos.metadata.get('price_current', 0) if hasattr(pos, 'metadata') else 0)
        current_tp = float(getattr(pos, 'take_profit', 0) or
                          pos.metadata.get('tp', 0) if hasattr(pos, 'metadata') else 0)

        if entry == 0 or current_price == 0:
            return

        # Capture initial SL on first seen
        if ticket_str not in self._entry_price:
            self._entry_price[ticket_str] = entry
            self._initial_sl[ticket_str] = current_sl
            atr_dist = abs(entry - current_sl) if current_sl != 0 else 0
            self._initial_atr_dist[ticket_str] = atr_dist
            self._stage[ticket_str] = 0

        atr_dist = self._initial_atr_dist[ticket_str]
        if atr_dist == 0:
            return  # Can't calculate without knowing initial risk

        current_stage = self._stage.get(ticket_str, 0)

        # Determine direction
        from ..core.constants import PositionSide
        is_long = getattr(pos, 'side', None) == PositionSide.LONG

        if is_long:
            profit_distance = current_price - entry
        else:
            profit_distance = entry - current_price

        # Stage 2: Lock in partial profit (entry + lock_fraction × atr_dist)
        if current_stage < 2 and profit_distance >= self.lock_atr_mult * atr_dist:
            new_sl = (entry + self.lock_fraction * atr_dist) if is_long else (entry - self.lock_fraction * atr_dist)
            # Only move SL in the favourable direction
            if (is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl):
                success = connector.modify_position(
                    position_id=ticket_str,
                    stop_loss=Decimal(str(round(new_sl, 5))),
                    take_profit=Decimal(str(current_tp)) if current_tp else None
                )
                if success:
                    self._stage[ticket_str] = 2
                    logger.info(
                        f"[TrailingStop] Stage 2 — Partial lock: ticket={ticket_str} "
                        f"new_sl={new_sl:.5f} (locked {self.lock_fraction*100:.0f}% of risk)"
                    )
            return

        # Stage 1: Move to breakeven
        if current_stage < 1 and profit_distance >= self.breakeven_atr_mult * atr_dist:
            new_sl = entry  # Breakeven — exactly at entry
            if (is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl):
                success = connector.modify_position(
                    position_id=ticket_str,
                    stop_loss=Decimal(str(round(new_sl, 5))),
                    take_profit=Decimal(str(current_tp)) if current_tp else None
                )
                if success:
                    self._stage[ticket_str] = 1
                    logger.info(
                        f"[TrailingStop] Stage 1 — Breakeven: ticket={ticket_str} "
                        f"entry={entry:.5f} sl_moved_to_breakeven"
                    )

    def cleanup_closed(self, open_tickets: set) -> None:
        """Remove tracking state for positions that are no longer open."""
        closed = set(self._stage.keys()) - open_tickets
        for t in closed:
            self._stage.pop(t, None)
            self._entry_price.pop(t, None)
            self._initial_sl.pop(t, None)
            self._initial_atr_dist.pop(t, None)
