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
        sizing_cfg = risk_config.get('position_sizing', {})
        self.method = sizing_cfg.get('method', 'dynamic_atr')
        # Fixed lot map: {ticker: Decimal(lot)} — only used when method == 'fixed'
        raw_fixed = sizing_cfg.get('fixed_lots', {})
        self._fixed_lots: Dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in raw_fixed.items()
        }
    
    def calculate_position_size(
        self,
        symbol: Symbol,
        account_balance: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        risk_pct: Decimal = None,
        signal_strength: float = None,
    ) -> Decimal:
        """
        Calculate position size based on risk, optionally scaled by signal strength.

        Uses fixed fractional position sizing:
            position_size = (account_balance * effective_risk_pct) / risk_per_unit

        Signal-strength scaling (when provided):
            effective_risk = risk_pct × (0.7 + 0.6 × strength)
            This gives:
                strength=0.50 → 1.00× base risk  (minimum qualifying signal)
                strength=0.75 → 1.15× base risk
                strength=1.00 → 1.30× base risk  (high-conviction cap)
            Scaling is capped at 1.3× to stay within prop-firm risk guardrails.

        Args:
            symbol: Trading symbol
            account_balance: Current account balance
            entry_price: Intended entry price
            stop_loss: Stop loss price
            risk_pct: Risk per trade as decimal (default from config)
            signal_strength: Optional [0, 1] signal strength from strategy

        Returns:
            Position size in lots, rounded to symbol lot step
        """
        # Fixed lot mode — return a constant lot per symbol, ignore stop distance.
        # Used for prop firm accounts where consistent sizing is required.
        if self.method == 'fixed':
            ticker = symbol.ticker if symbol else 'default'
            lot = self._fixed_lots.get(ticker) or self._fixed_lots.get('default', symbol.min_lot)
            return max(symbol.min_lot, min(symbol.max_lot, lot))

        risk_pct = risk_pct or self.default_risk_pct

        # Scale risk% by signal strength when provided
        if signal_strength is not None:
            strength = max(0.0, min(1.0, float(signal_strength)))
            scale = 0.7 + 0.6 * strength          # [0.70, 1.30]
            scale = max(0.7, min(1.3, scale))      # clamp for safety
            risk_pct = risk_pct * Decimal(str(round(scale, 4)))
        
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
