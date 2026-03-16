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

        # Time-based stop (minutes) to close stuck positions
        self.time_stop_minutes: Optional[int] = trail_cfg.get('time_stop_minutes', None)

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
        # --- Robust field reads using explicit None-guards ---
        # CRITICAL: use `is None` checks, NOT truthiness (`or`).
        # A Decimal("0") price is falsy in Python — truthiness would silently
        # replace a real 0.0 value with the fallback, causing silent failures.

        # Entry price
        _entry_attr = getattr(pos, 'entry_price', None)
        if _entry_attr is not None:
            entry = float(_entry_attr)
        elif hasattr(pos, 'metadata'):
            entry = float(pos.metadata.get('entry_price', 0) or 0)
        else:
            entry = 0.0

        # Current stop loss
        _sl_attr = getattr(pos, 'stop_loss', None)
        if _sl_attr is not None:
            current_sl = float(_sl_attr)
        elif hasattr(pos, 'metadata'):
            current_sl = float(pos.metadata.get('sl', 0) or 0)
        else:
            current_sl = 0.0

        # Current price
        _price_attr = getattr(pos, 'current_price', None)
        if _price_attr is not None:
            current_price = float(_price_attr)
        elif hasattr(pos, 'metadata'):
            current_price = float(pos.metadata.get('price_current', 0) or 0)
        else:
            current_price = 0.0

        # Current take profit (may legitimately be None/0 — keep as None so
        # modify_position does NOT send tp=0 and wipe the real TP)
        _tp_attr = getattr(pos, 'take_profit', None)
        if _tp_attr is not None and float(_tp_attr) != 0.0:
            current_tp_decimal = Decimal(str(float(_tp_attr)))
        elif hasattr(pos, 'metadata') and pos.metadata.get('tp'):
            current_tp_decimal = Decimal(str(float(pos.metadata['tp'])))
        else:
            current_tp_decimal = None  # ← None means "keep existing" in modify_position

        if entry == 0 or current_price == 0:
            return

        # Capture initial SL on first seen
        if ticket_str not in self._entry_price:
            self._entry_price[ticket_str] = entry
            self._initial_sl[ticket_str] = current_sl
            atr_dist = abs(entry - current_sl) if current_sl != 0 else 0
            self._initial_atr_dist[ticket_str] = atr_dist
            self._stage[ticket_str] = 0
            if atr_dist == 0:
                logger.warning(
                    f"[TrailingStop] ticket={ticket_str}: initial SL=0, cannot compute "
                    f"ATR distance — BE/trail disabled for this position "
                    f"(entry={entry:.5f}, sl={current_sl})"
                )

        atr_dist = self._initial_atr_dist[ticket_str]
        if atr_dist == 0:
            return  # Can't calculate without knowing initial risk

        current_stage = self._stage.get(ticket_str, 0)

        # Determine direction
        from ..core.constants import PositionSide
        is_long = getattr(pos, 'side', None) == PositionSide.LONG

        profit_distance = (current_price - entry) if is_long else (entry - current_price)

        # Debug logging every 60 seconds
        import time
        if not hasattr(self, '_last_log_time'):
            self._last_log_time = {}
        if time.time() - self._last_log_time.get(ticket_str, 0) > 60:
            logger.debug(
                f"[TrailingStop] ticket={ticket_str} stage={current_stage} "
                f"entry={entry:.2f} price={current_price:.2f} sl={current_sl:.2f} "
                f"profit_dist={profit_distance:.2f} "
                f"req_BE={self.breakeven_atr_mult * atr_dist:.2f} "
                f"req_lock={self.lock_atr_mult * atr_dist:.2f}"
            )
            self._last_log_time[ticket_str] = time.time()

        # ── Time-based stop logic ──
        if self.time_stop_minutes is not None:
            # Try to get open time from position
            _opened_attr = getattr(pos, 'opened_at', None)
            if _opened_attr is not None:
                opened_at = _opened_attr
            elif hasattr(pos, 'metadata') and pos.metadata.get('time_setup'):
                opened_at = datetime.fromtimestamp(pos.metadata['time_setup'], tz=timezone.utc)
            else:
                opened_at = None

            if opened_at is not None:
                duration_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
                if duration_minutes >= self.time_stop_minutes:
                    logger.info(
                        f"[TimeStop] Closing ticket={ticket_str} after {duration_minutes:.1f} minutes "
                        f"(limit: {self.time_stop_minutes}m), profit_dist={profit_distance:.2f}."
                    )
                    # Use connector to close position
                    connector.close_position(position_id=ticket_str, symbol=getattr(pos.symbol, 'ticker', ''))
                    return

        # ── Stage 2: Lock in partial profit (entry + lock_fraction × atr_dist) ──
        if current_stage < 2 and profit_distance >= self.lock_atr_mult * atr_dist:
            new_sl = (entry + self.lock_fraction * atr_dist) if is_long \
                     else (entry - self.lock_fraction * atr_dist)
            # Only move SL in the favourable direction (never move backwards)
            if (is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl):
                success = connector.modify_position(
                    position_id=ticket_str,
                    stop_loss=Decimal(str(round(new_sl, 5))),
                    take_profit=current_tp_decimal  # None = keep existing TP in MT5
                )
                if success:
                    self._stage[ticket_str] = 2
                    logger.info(
                        f"[TrailingStop] ✅ Stage 2 LOCK: ticket={ticket_str} "
                        f"new_sl={new_sl:.5f} "
                        f"(locked {self.lock_fraction*100:.0f}% of ${atr_dist:.2f} risk)"
                    )
            return

        # ── Stage 1: Move SL to breakeven (entry price) ──
        if current_stage < 1 and profit_distance >= self.breakeven_atr_mult * atr_dist:
            new_sl = entry  # Breakeven — worst case: $0 loss
            if (is_long and new_sl > current_sl) or (not is_long and new_sl < current_sl):
                success = connector.modify_position(
                    position_id=ticket_str,
                    stop_loss=Decimal(str(round(new_sl, 5))),
                    take_profit=current_tp_decimal  # None = keep existing TP in MT5
                )
                if success:
                    self._stage[ticket_str] = 1
                    logger.info(
                        f"[TrailingStop] ✅ Stage 1 BREAKEVEN: ticket={ticket_str} "
                        f"sl_moved_to_entry={entry:.5f} "
                        f"(triggered at +{profit_distance:.2f} / req {self.breakeven_atr_mult * atr_dist:.2f})"
                    )

    def cleanup_closed(self, open_tickets: set) -> None:
        """Remove tracking state for positions that are no longer open."""
        closed = set(self._stage.keys()) - open_tickets
        for t in closed:
            self._stage.pop(t, None)
            self._entry_price.pop(t, None)
            self._initial_sl.pop(t, None)
            self._initial_atr_dist.pop(t, None)
