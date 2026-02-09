"""Position sizing calculations."""

from decimal import Decimal, ROUND_DOWN
from typing import Dict

from ..core.types import Symbol


class PositionSizer:
    """
    Calculate optimal position sizes based on risk parameters.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize position sizer.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        risk_config = config.get('risk', {})
        self.default_risk_pct = Decimal(str(risk_config.get('risk_per_trade_pct', '0.0025')))
    
    def calculate_position_size(
        self,
        symbol: Symbol,
        account_balance: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        risk_pct: Decimal = None
    ) -> Decimal:
        """
        Calculate position size based on risk.
        
        Uses fixed fractional position sizing:
        position_size = (account_balance * risk_pct) / risk_per_unit
        
        Args:
            symbol: Trading symbol
            account_balance: Current account balance
            entry_price: Intended entry price
            stop_loss: Stop loss price
            risk_pct: Risk per trade as decimal (default from config)
        
        Returns:
            Position size in lots, rounded to symbol lot step
        """
        risk_pct = risk_pct or self.default_risk_pct
        
        # Calculate risk per unit
        price_risk = abs(entry_price - stop_loss)
        if price_risk == 0:
            return Decimal("0")
        
        # Risk per lot
        risk_per_lot = price_risk * symbol.value_per_lot
        if risk_per_lot == 0:
            return Decimal("0")
        
        # Calculate position size
        risk_amount = account_balance * risk_pct
        raw_size = risk_amount / risk_per_lot
        
        # Round to lot step
        lot_step = symbol.lot_step
        position_size = (raw_size / lot_step).quantize(Decimal("1"), rounding=ROUND_DOWN) * lot_step
        
        # Apply min/max limits
        position_size = max(symbol.min_lot, min(symbol.max_lot, position_size))
        
        return position_size
    
    def calculate_risk_amount(
        self,
        position_size: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        symbol: Symbol
    ) -> Decimal:
        """
        Calculate dollar risk for a position.
        
        Args:
            position_size: Position size in lots
            entry_price: Entry price
            stop_loss: Stop loss price
            symbol: Trading symbol
        
        Returns:
            Dollar amount at risk
        """
        price_risk = abs(entry_price - stop_loss)
        return position_size * price_risk * symbol.value_per_lot
