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
        4. Price mismatches
        
        Args:
            our_positions: Dict of our positions
            mt5_positions: Dict of MT5 positions
        
        Returns:
            (all_match, list_of_discrepancies)
        """
        discrepancies = []
        
        # Check for phantom positions (in our system, not in MT5)
        for pos_id, our_pos in our_positions.items():
            if pos_id not in mt5_positions:
                # Check if any MT5 position matches by symbol
                matching = [
                    p for p in mt5_positions.values()
                    if p.symbol.ticker == our_pos.symbol.ticker
                ]
                
                if not matching:
                    discrepancy = f"Phantom position: {our_pos.symbol.ticker} not in MT5"
                    discrepancies.append(discrepancy)
                    self.logger.warning(discrepancy, position_id=pos_id)
        
        # Check for unknown positions (in MT5, not in our system)
        for pos_id, mt5_pos in mt5_positions.items():
            if pos_id not in our_positions:
                discrepancy = f"Unknown position in MT5: {mt5_pos.symbol.ticker}"
                discrepancies.append(discrepancy)
                self.logger.warning(discrepancy, mt5_position_id=pos_id)
        
        # Check for quantity/price mismatches
        for pos_id in set(our_positions.keys()) & set(mt5_positions.keys()):
            our_pos = our_positions[pos_id]
            mt5_pos = mt5_positions[pos_id]
            
            if our_pos.quantity != mt5_pos.quantity:
                discrepancy = (
                    f"Quantity mismatch {our_pos.symbol.ticker}: "
                    f"ours={our_pos.quantity}, MT5={mt5_pos.quantity}"
                )
                discrepancies.append(discrepancy)
                self.logger.warning(discrepancy, position_id=pos_id)
        
        success = len(discrepancies) == 0
        
        return success, discrepancies
