"""
Portfolio Engine - Position tracking and P&L management.

Responsibilities:
1. Track all open positions
2. Update positions with real-time prices
3. Calculate unrealized P&L
4. Calculate realized P&L when positions close
5. Reconcile positions with MT5
6. Track portfolio-level metrics

Critical Design:
- Positions tracked in memory for speed
- Regular reconciliation with MT5 (every 60 seconds)
- Discrepancies logged and alerted
- P&L calculations use Decimal for precision
- Position updates are atomic
"""

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from uuid import UUID
from decimal import Decimal
from datetime import datetime, timedelta, timezone

if TYPE_CHECKING:
    from ..monitoring.trade_journal import TradeJournal

from ..connectors.mt5_connector import MT5Connector
from ..core.types import Position, Tick, Symbol
from ..core.constants import PositionSide

from .position_tracker import PositionTracker
from .pnl_calculator import PnLCalculator
from .reconciliation import Reconciliation


class PortfolioEngine:
    """
    Central portfolio management engine.
    
    Tracks positions and calculates portfolio-level metrics.
    """
    
    def __init__(
        self, 
        connector: MT5Connector,
        trade_journal: Optional['TradeJournal'] = None
    ):
        """
        Initialize portfolio engine.
        
        Args:
            connector: MT5 connector for position reconciliation
            trade_journal: Optional trade journal for recording trades
        """
        self.connector = connector
        self.trade_journal = trade_journal
        
        # Sub-components
        self.position_tracker = PositionTracker()
        self.pnl_calculator = PnLCalculator()
        self.reconciliation = Reconciliation(connector)
        
        # Portfolio state
        self.total_realized_pnl = Decimal("0")
        self.daily_realized_pnl = Decimal("0")
        self.last_reconciliation: Optional[datetime] = None
        
        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def add_position(self, position: Position) -> None:
        """
        Add new position to portfolio.
        
        Args:
            position: Position to add
        """
        self.position_tracker.add_position(position)
        
        self.logger.info(
            "Position added to portfolio",
            position_id=str(position.position_id),
            symbol=position.symbol.ticker if position.symbol else None,
            side=position.side.value,
            quantity=float(position.quantity),
            entry_price=float(position.entry_price)
        )
    
    def update_position_price(self, position_id: UUID, current_price: Decimal) -> None:
        """
        Update position with current market price.
        
        Args:
            position_id: Position ID to update
            current_price: Current market price
        """
        position = self.position_tracker.get_position(position_id)
        
        if not position:
            self.logger.warning(
                "Cannot update unknown position",
                position_id=str(position_id)
            )
            return
        
        # Update price and recalculate P&L
        old_pnl = position.unrealized_pnl
        position.update_price(current_price)
        
        self.logger.debug(
            "Position price updated",
            position_id=str(position_id),
            current_price=float(current_price),
            unrealized_pnl=float(position.unrealized_pnl),
            pnl_change=float(position.unrealized_pnl - old_pnl)
        )
    
    def update_all_positions(self, ticks: Dict[str, Tick]) -> None:
        """
        Update all positions with latest tick prices.
        
        Args:
            ticks: Dict mapping symbol ticker to Tick
        """
        for position in self.position_tracker.get_all_positions():
            if position.symbol and position.symbol.ticker in ticks:
                tick = ticks[position.symbol.ticker]
                
                # Use bid for long, ask for short (more conservative)
                if position.side == PositionSide.LONG:
                    price = tick.bid
                else:
                    price = tick.ask
                
                self.update_position_price(position.position_id, price)
    
    def close_position(
        self,
        position_id: UUID,
        exit_price: Decimal,
        exit_time: Optional[datetime] = None,
        override_pnl: Optional[Decimal] = None,
    ) -> Decimal:
        """
        Close position and calculate realized P&L.

        Args:
            position_id: Position ID to close
            exit_price: Exit price
            exit_time: Exit timestamp (defaults to now)
            override_pnl: If set, use this exact PnL instead of calculating
                          (used when actual MT5 PnL is known from history)

        Returns:
            Realized P&L
        """
        position = self.position_tracker.get_position(position_id)

        if not position:
            self.logger.error(
                "Cannot close unknown position",
                position_id=str(position_id)
            )
            return Decimal("0")

        # Use override PnL if provided (e.g. from MT5 history), else calculate
        if override_pnl is not None:
            realized_pnl = override_pnl
        else:
            realized_pnl = self.pnl_calculator.calculate_realized_pnl(
                position=position,
                exit_price=exit_price
            )

        # Update totals
        self.total_realized_pnl += realized_pnl
        self.daily_realized_pnl += realized_pnl

        # Record to trade journal BEFORE mutating position fields
        if self.trade_journal:
            if exit_time is None:
                exit_time = datetime.now(timezone.utc)

            self.trade_journal.record_trade(
                position=position,
                exit_price=exit_price,
                exit_time=exit_time,
                realized_pnl=realized_pnl,
                exit_reason=position.metadata.get('exit_reason', 'manual') if hasattr(position, 'metadata') and position.metadata else 'manual'
            )

        # Mark position as closed (after journal recording)
        position.realized_pnl = realized_pnl
        position.side = PositionSide.FLAT
        position.quantity = Decimal("0")

        # Remove from active positions
        self.position_tracker.remove_position(position_id)

        self.logger.info(
            "Position closed",
            position_id=str(position_id),
            symbol=position.symbol.ticker if position.symbol else None,
            entry_price=float(position.entry_price),
            exit_price=float(exit_price),
            realized_pnl=float(realized_pnl),
            total_realized_pnl=float(self.total_realized_pnl)
        )

        return realized_pnl
    
    def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get position by ID."""
        return self.position_tracker.get_position(position_id)
    
    def get_all_positions(self) -> List[Position]:
        """Get all open positions."""
        return self.position_tracker.get_all_positions()
    
    def get_positions_by_symbol(self, symbol: str) -> List[Position]:
        """Get positions for specific symbol."""
        return self.position_tracker.get_positions_by_symbol(symbol)
    
    def get_total_exposure(self) -> Decimal:
        """
        Calculate total notional exposure across all positions.
        
        Returns:
            Total exposure in account currency
        """
        total = Decimal("0")
        
        for position in self.get_all_positions():
            if position.symbol:
                exposure = abs(
                    position.quantity * 
                    position.current_price * 
                    position.symbol.value_per_lot
                )
                total += exposure
        
        return total
    
    def get_net_exposure(self) -> Decimal:
        """
        Calculate net exposure (long - short).
        
        Returns:
            Net exposure (positive = net long, negative = net short)
        """
        net = Decimal("0")
        
        for position in self.get_all_positions():
            if position.symbol:
                exposure = (
                    position.quantity * 
                    position.current_price * 
                    position.symbol.value_per_lot
                )
                
                if position.side == PositionSide.LONG:
                    net += exposure
                elif position.side == PositionSide.SHORT:
                    net -= exposure
        
        return net
    
    def get_total_unrealized_pnl(self) -> Decimal:
        """Calculate total unrealized P&L across all positions."""
        return sum(
            (position.unrealized_pnl for position in self.get_all_positions()),
            Decimal("0")
        )
    
    def get_portfolio_pnl(self) -> Decimal:
        """
        Get total portfolio P&L (realized + unrealized).
        
        Returns:
            Total P&L
        """
        unrealized = self.get_total_unrealized_pnl()
        return self.total_realized_pnl + unrealized
    
    def reconcile_with_mt5(self) -> Tuple[bool, List[str]]:
        """
        Reconcile portfolio state with MT5.
        
        Compares positions in our system vs actual MT5 positions.
        Logs discrepancies and auto-corrects by checking history for closed trades.
        
        Returns:
            (success, list_of_discrepancies)
        """
        self.logger.info("Starting MT5 reconciliation")
        
        # Get positions from MT5
        mt5_positions = self.connector.get_positions()
        
        # Get our positions
        our_positions = {
            str(p.position_id): p 
            for p in self.get_all_positions()
        }
        
        # Perform initial reconciliation check
        success, discrepancies = self.reconciliation.reconcile(
            our_positions=our_positions,
            mt5_positions=mt5_positions
        )
        
        self.last_reconciliation = datetime.now(timezone.utc)
        
        if not success:
            self.logger.warning(
                "Reconciliation found discrepancies, attempting auto-correction",
                count=len(discrepancies)
            )
            
            # Check for "Phantom Positions" (We have it, MT5 doesn't)
            # This usually means the trade was closed (TP/SL hit)
            # Match by mt5_ticket to correctly handle multiple positions on same symbol
            mt5_tickets_in_broker = {
                str(mp.metadata.get('mt5_ticket')) 
                for mp in mt5_positions.values() 
                if mp.metadata.get('mt5_ticket')
            }
            
            phantom_positions = [
                p for p in our_positions.values()
                if p.metadata.get('mt5_ticket') and str(p.metadata.get('mt5_ticket')) not in mt5_tickets_in_broker
            ]
            
            if phantom_positions:
                self.logger.info(f"Checking history for {len(phantom_positions)} potential closed positions")
                
                # Fetch recent history (last 24h)
                history = self.connector.get_closed_positions(minutes=1440)
                
                for position in phantom_positions:
                    # Find matching deal in history
                    # Match by ticket if available, else by symbol + close time (approx)
                    mt5_ticket = position.metadata.get('mt5_ticket')
                    
                    matching_deal = None
                    if mt5_ticket:
                        matching_deal = next((d for d in history if str(d.get('position_ticket')) == str(mt5_ticket)), None)
                    
                    if matching_deal:
                        self.logger.info(
                            "Found closure in history",
                            position_id=str(position.position_id),
                            profit=matching_deal.get('profit'),
                            price=matching_deal.get('price')
                        )

                        # Compute actual PnL BEFORE close_position
                        # so the journal records the correct value
                        actual_pnl = (
                            Decimal(str(matching_deal.get('profit', 0)))
                            + Decimal(str(matching_deal.get('swap', 0)))
                            + Decimal(str(matching_deal.get('commission', 0)))
                        )

                        broker_offset = getattr(self.connector, "broker_offset", timedelta(0))
                        self.close_position(
                            position_id=position.position_id,
                            exit_price=Decimal(str(matching_deal.get('price', 0))),
                            exit_time=datetime.fromtimestamp(int(matching_deal.get('time', 0)), tz=timezone.utc) - broker_offset,
                            override_pnl=actual_pnl,
                        )
                        
                    else:
                        # History unavailable — still record the trade using last known price
                        exit_price = position.current_price if position.current_price else position.entry_price
                        position.metadata['exit_reason'] = 'closed_on_broker'

                        self.logger.warning(
                            "Position missing from MT5 — closing with last known price",
                            position_id=str(position.position_id),
                            ticker=mt5_ticket,
                            exit_price=float(exit_price),
                        )

                        self.close_position(
                            position_id=position.position_id,
                            exit_price=exit_price,
                            exit_time=datetime.now(timezone.utc),
                        )
            
            # 2. Check for "Unknown Positions" (MT5 has it, we don't)
            # Build O(1) lookup dicts to replace the O(n²) inner loop
            our_by_ticket: Dict[str, object] = {}
            our_by_fuzzy: Dict[tuple, object] = {}
            for our_pos in our_positions.values():
                t = str(our_pos.metadata.get('mt5_ticket', '')) if our_pos.metadata else ''
                if t:
                    our_by_ticket[t] = our_pos
                fuzzy_key = (our_pos.symbol.ticker, our_pos.side) if our_pos.symbol else None
                if fuzzy_key and fuzzy_key not in our_by_fuzzy:
                    our_by_fuzzy[fuzzy_key] = our_pos

            unknown_positions = []

            for pid, mt5_pos in mt5_positions.items():
                mt5_ticket = str(mt5_pos.metadata.get('mt5_ticket', '')) if mt5_pos.metadata else ''

                # Primary: O(1) ticket match
                if mt5_ticket and mt5_ticket in our_by_ticket:
                    continue

                # Secondary: O(1) fuzzy match by (symbol, side) then price tolerance
                fuzzy_key = (mt5_pos.symbol.ticker, mt5_pos.side) if mt5_pos.symbol else None
                our_pos = our_by_fuzzy.get(fuzzy_key) if fuzzy_key else None
                if our_pos and abs(our_pos.entry_price - mt5_pos.entry_price) < (mt5_pos.current_price * Decimal("0.001")):
                    if mt5_ticket and not our_pos.metadata.get('mt5_ticket'):
                        our_pos.metadata['mt5_ticket'] = mt5_ticket
                        self.logger.info(f"Linked existing position {our_pos.position_id} to MT5 ticket {mt5_ticket}")
                    continue

                unknown_positions.append((pid, mt5_pos))
            
            if unknown_positions:
                self.logger.info(f"Found {len(unknown_positions)} unknown positions in MT5 - adopting them")

                for pid, mt5_pos in unknown_positions:
                    # Mark positions adopted from MT5 that we didn't open ourselves.
                    # _convert_mt5_position already tags them 'manual' when the comment
                    # doesn't have the bot's "strategy|orderId" format.
                    if mt5_pos.metadata.get('strategy') not in ('manual', 'unknown'):
                        pass  # bot position recovered after restart — keep strategy tag
                    elif not mt5_pos.metadata.get('mt5_comment', ''):
                        mt5_pos.metadata['strategy'] = 'manual'  # no comment = manual

                    self.add_position(mt5_pos)
                    self.logger.info(
                        "Adopted position from MT5",
                        position_id=pid,
                        symbol=mt5_pos.symbol.ticker,
                        volume=float(mt5_pos.quantity),
                        strategy=mt5_pos.metadata.get('strategy', 'manual'),
                        side=mt5_pos.side.value,
                    )
            
        else:
            self.logger.info("Reconciliation successful - no discrepancies")
        
        return success, discrepancies
    
    def reset_daily_pnl(self) -> None:
        """Reset daily P&L counter at start of new trading day."""
        self.logger.info(
            "Daily P&L reset",
            previous_daily_pnl=float(self.daily_realized_pnl),
            date=datetime.now(timezone.utc).date().isoformat()
        )
        
        self.daily_realized_pnl = Decimal("0")
    
    def get_statistics(self) -> Dict:
        """
        Get portfolio statistics — single pass over positions, O(n).

        Returns:
            Dict with portfolio metrics
        """
        positions = self.get_all_positions()

        long_count = short_count = 0
        total_exposure = net_exposure = unrealized_pnl = Decimal("0")

        for p in positions:
            if p.side == PositionSide.LONG:
                long_count += 1
            elif p.side == PositionSide.SHORT:
                short_count += 1

            if p.symbol:
                exposure = abs(p.quantity * p.current_price * p.symbol.value_per_lot)
                total_exposure += exposure
                if p.side == PositionSide.LONG:
                    net_exposure += exposure
                elif p.side == PositionSide.SHORT:
                    net_exposure -= exposure

            unrealized_pnl += p.unrealized_pnl

        return {
            'total_positions': len(positions),
            'long_positions': long_count,
            'short_positions': short_count,
            'total_exposure': float(total_exposure),
            'net_exposure': float(net_exposure),
            'unrealized_pnl': float(unrealized_pnl),
            'realized_pnl': float(self.total_realized_pnl),
            'daily_realized_pnl': float(self.daily_realized_pnl),
            'total_pnl': float(self.total_realized_pnl + unrealized_pnl),
            'last_reconciliation': self.last_reconciliation.isoformat() if self.last_reconciliation else None
        }
