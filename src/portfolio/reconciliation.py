"""
Reconciliation - Compare our state vs MT5 reality.

Detects and reports discrepancies.
"""

from typing import Dict, List, Tuple

from ..connectors.mt5_connector import MT5Connector
from ..core.types import Position


class Reconciliation:
    """Reconcile portfolio state with MT5."""
    
    def __init__(self, connector: MT5Connector):
        self.connector = connector
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def reconcile(
        self,
        our_positions: Dict[str, Position],
        mt5_positions: Dict[str, Position]
    ) -> Tuple[bool, List[str]]:
        """
        Reconcile positions.
        
        Checks:
        1. Positions in our system but not in MT5 (phantom positions)
        2. Positions in MT5 but not in our system (unknown positions)
        3. Quantity mismatches
        
        Matching is done by mt5_ticket (broker identity), NOT by Python UUID.
        
        Args:
            our_positions: Dict of our positions (keyed by Python UUID)
            mt5_positions: Dict of MT5 positions (keyed by generated UUID)
        
        Returns:
            (all_match, list_of_discrepancies)
        """
        discrepancies = []
        
        # Build ticket-based lookup for MT5 positions
        mt5_by_ticket = {}
        for pos_id, mt5_pos in mt5_positions.items():
            ticket = str(mt5_pos.metadata.get('mt5_ticket', ''))
            if ticket:
                mt5_by_ticket[ticket] = mt5_pos
        
        # Build ticket-based lookup for our positions
        our_by_ticket = {}
        for pos_id, our_pos in our_positions.items():
            ticket = str(our_pos.metadata.get('mt5_ticket', ''))
            if ticket:
                our_by_ticket[ticket] = our_pos
        
        # 1. Check for phantom positions (in our system, not in MT5) - by ticket
        for ticket, our_pos in our_by_ticket.items():
            if ticket and ticket not in mt5_by_ticket:
                discrepancy = f"Phantom position: {our_pos.symbol.ticker} (ticket {ticket}) not in MT5"
                discrepancies.append(discrepancy)
                self.logger.warning(discrepancy, position_id=str(our_pos.position_id))
        
        # 2. Check for unknown positions (in MT5, not in our system) - by ticket
        for ticket, mt5_pos in mt5_by_ticket.items():
            if ticket and ticket not in our_by_ticket:
                discrepancy = f"Unknown position in MT5: {mt5_pos.symbol.ticker} (ticket {ticket})"
                discrepancies.append(discrepancy)
                self.logger.warning(discrepancy, mt5_ticket=ticket)
        
        # 3. Check for quantity mismatches on matched positions
        for ticket in set(our_by_ticket.keys()) & set(mt5_by_ticket.keys()):
            our_pos = our_by_ticket[ticket]
            mt5_pos = mt5_by_ticket[ticket]
            
            if our_pos.quantity != mt5_pos.quantity:
                discrepancy = (
                    f"Quantity mismatch {our_pos.symbol.ticker}: "
                    f"ours={our_pos.quantity}, MT5={mt5_pos.quantity}"
                )
                discrepancies.append(discrepancy)
                self.logger.warning(discrepancy, mt5_ticket=ticket)
        
        success = len(discrepancies) == 0
        
        return success, discrepancies
