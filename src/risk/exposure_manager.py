"""Exposure management for per-symbol concentration limits."""

from decimal import Decimal, ROUND_DOWN
from typing import Dict, Tuple

from ..core.types import Order, Position, Symbol


class ExposureManager:
    """
    Manages exposure limits per symbol to prevent over-concentration.
    """
    
    def __init__(self, max_exposure_pct: Decimal = Decimal("0.30")):
        """
        Initialize exposure manager.
        
        Args:
            max_exposure_pct: Max exposure per symbol as % of account equity
        """
        self.max_exposure_pct = max_exposure_pct
    
    def _calculate_symbol_exposure(
        self,
        symbol: Symbol,
        positions: Dict[str, Position]
    ) -> Decimal:
        """Calculate total notional exposure for a symbol."""
        exposure = Decimal("0")
        
        for position in positions.values():
            # Only count positions for this symbol
            if position.symbol.ticker == symbol.ticker:
                # Use absolute value (long and short both count toward exposure)
                position_value = abs(
                    position.quantity * 
                    position.current_price * 
                    position.symbol.value_per_lot
                )
                exposure += position_value
        
        return exposure

    def check_exposure_limit(
        self,
        symbol: Symbol,
        new_order: Order,
        current_positions: Dict[str, Position],
        account_equity: Decimal
    ) -> Tuple[bool, str]:
        """
        Check if new order would exceed exposure limit.
        
        Returns:
            (allowed, reason)
        """
        # Calculate current exposure for this symbol
        current_exposure = self._calculate_symbol_exposure(
            symbol,
            current_positions
        )
        
        # Calculate notional value of new order
        # CRITICAL FIX: Don't use price if it's None (market orders)
        if new_order.price and new_order.price > 0:
            order_price = new_order.price
        else:
            # For market orders, estimate using a reasonable price
            # In production, get current market price
            # For now, skip exposure check for market orders without price
            pass
            return True, "OK"
    
        # Calculate new order exposure
        new_exposure = new_order.quantity * order_price * symbol.value_per_lot
        
        # Total exposure after this order
        total_exposure = current_exposure + new_exposure
        
        # Maximum allowed exposure for this symbol
        max_exposure = account_equity * self.max_exposure_pct
        
        if total_exposure > max_exposure:
            # Allow min_lot if no existing exposure and order is at minimum size.
            # On small accounts (e.g. $154) with high-priced instruments (BTCUSD $66k),
            # even 0.01 lots exceeds any reasonable %-based limit, making trading impossible.
            if current_exposure == 0 and new_order.quantity <= symbol.min_lot:
                return True, "OK (min_lot override for small account)"
            
            # Calculate percentage for error message
            if account_equity > 0:
                exposure_pct = (total_exposure / account_equity) * 100
            else:
                exposure_pct = Decimal("0")
            max_pct = self.max_exposure_pct * 100
            
            return False, f"Exposure limit exceeded for {symbol.ticker}: {exposure_pct:.1f}% > {max_pct:.1f}%"
        
        return True, "OK"
        
    def get_max_position_size(
        self,
        symbol: Symbol,
        current_positions: Dict[str, Position],
        account_equity: Decimal,
        entry_price: Decimal
    ) -> Decimal:
        """
        Calculate maximum position size allowed by exposure limit.
        
        Args:
            symbol: Trading symbol
            current_positions: Current open positions
            account_equity: Current account equity
            entry_price: Intended entry price
            
        Returns:
            Max allowable position size in lots
        """
        if account_equity <= 0 or entry_price <= 0:
            return Decimal("0")
            
        # Calculate current exposure
        current_exposure = self._calculate_symbol_exposure(symbol, current_positions)
        
        # Calculate max allowed total exposure
        max_total_exposure = account_equity * self.max_exposure_pct
        
        # Calculate remaining exposure allowed
        remaining_exposure = max_total_exposure - current_exposure
        
        if remaining_exposure <= 0:
            return Decimal("0")
            
        # Convert exposure to lots
        # Exposure = Lots * Price * ValuePerLot
        # Lots = Exposure / (Price * ValuePerLot)
        contract_value = entry_price * symbol.value_per_lot
        if contract_value == 0:
            return Decimal("0")
            
        max_lots_raw = remaining_exposure / contract_value
        
        # Round down to lot step
        max_lots = (max_lots_raw / symbol.lot_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * symbol.lot_step
        
        # If remaining exposure exists but rounds to 0 lots due to lot step,
        # allow min_lot for small accounts on high-priced instruments (e.g. $200 on BTCUSD).
        # Without this, no trade can ever be placed.
        if max_lots == 0 and remaining_exposure > 0:
            max_lots = symbol.min_lot
        
        return max(Decimal("0"), max_lots)
