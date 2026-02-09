"""
Simulated Broker - Simulate order execution for backtesting.

Responsibilities:
- Execute orders with realistic fills
- Apply slippage based on model
- Charge commissions
- Track positions
- Check stop loss / take profit
- Maintain account balance
"""

from typing import Dict, List, Optional
from decimal import Decimal
from uuid import uuid4
import random

from ..core.types import Order, Position, Bar, Symbol
from ..core.constants import OrderSide, OrderStatus, PositionSide


class SimulatedBroker:
    """Simulate broker for backtesting."""
    
    def __init__(
        self,
        initial_capital: Decimal,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "realistic"
    ):
        """
        Initialize simulated broker.
        
        Args:
            initial_capital: Starting capital
            commission_per_trade: Commission per trade
            slippage_model: 'fixed', 'realistic', or 'aggressive'
        """
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_model = slippage_model
        
        # Account state
        self.balance = initial_capital
        self.equity = initial_capital
        
        # Positions
        self.positions: Dict[str, Position] = {}
        
        # Trade history
        self.closed_trades: List[Dict] = []
        self.daily_pnl = Decimal("0")
    
    def execute_order(self, order: Order, current_bar: Bar) -> Optional[Decimal]:
        """
        Execute order with simulated fill.
        
        Args:
            order: Order to execute
            current_bar: Current bar data
        
        Returns:
            Fill price if executed, None otherwise
        """
        # Simulate fill price with slippage
        fill_price = self._calculate_fill_price(order, current_bar)
        
        # Check if we have capital
        required_margin = fill_price * order.quantity * order.symbol.value_per_lot
        
        if required_margin > self.balance:
            return None  # Insufficient capital
        
        # Create position
        position = Position(
            position_id=uuid4(),
            symbol=order.symbol,
            side=PositionSide.LONG if order.side == OrderSide.BUY else PositionSide.SHORT,
            quantity=order.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            metadata={
                'order_id': str(order.order_id),
                'strategy': order.metadata.get('strategy')
            }
        )
        
        # Charge commission
        commission = self.commission_per_trade
        self.balance -= commission
        
        # Add position
        self.positions[str(position.position_id)] = position
        
        return fill_price
    
    def update_positions(self, current_bar: Bar) -> None:
        """Update all positions with current bar prices."""
        for position in self.positions.values():
            if position.symbol.ticker == current_bar.symbol.ticker:
                position.update_price(current_bar.close)
    
    def check_exits(self, current_bar: Bar) -> None:
        """Check if any positions hit stop loss or take profit."""
        positions_to_close = []
        
        for pos_id, position in self.positions.items():
            if position.symbol.ticker != current_bar.symbol.ticker:
                continue
            
            # Check stop loss
            if position.stop_loss:
                if position.side == PositionSide.LONG and current_bar.low <= position.stop_loss:
                    positions_to_close.append((pos_id, position.stop_loss, 'stop_loss'))
                elif position.side == PositionSide.SHORT and current_bar.high >= position.stop_loss:
                    positions_to_close.append((pos_id, position.stop_loss, 'stop_loss'))
            
            # Check take profit
            if position.take_profit:
                if position.side == PositionSide.LONG and current_bar.high >= position.take_profit:
                    positions_to_close.append((pos_id, position.take_profit, 'take_profit'))
                elif position.side == PositionSide.SHORT and current_bar.low <= position.take_profit:
                    positions_to_close.append((pos_id, position.take_profit, 'take_profit'))
        
        # Close positions
        for pos_id, exit_price, exit_reason in positions_to_close:
            self._close_position(pos_id, exit_price, exit_reason)
    
    def _close_position(self, position_id: str, exit_price: Decimal, reason: str) -> None:
        """Close position and calculate P&L."""
        position = self.positions[position_id]
        
        # Calculate P&L
        price_diff = exit_price - position.entry_price
        
        if position.side == PositionSide.LONG:
            pnl = price_diff * position.quantity * position.symbol.value_per_lot
        else:
            pnl = -price_diff * position.quantity * position.symbol.value_per_lot
        
        # Subtract commission
        pnl -= self.commission_per_trade
        
        # Update balance
        self.balance += pnl
        self.daily_pnl += pnl
        
        # Record trade
        self.closed_trades.append({
            'position_id': position_id,
            'symbol': position.symbol.ticker,
            'side': position.side.value,
            'entry_price': float(position.entry_price),
            'exit_price': float(exit_price),
            'quantity': float(position.quantity),
            'pnl': float(pnl),
            'exit_reason': reason,
            'strategy': position.metadata.get('strategy')
        })
        
        # Remove position
        del self.positions[position_id]
    
    def _calculate_fill_price(self, order: Order, current_bar: Bar) -> Decimal:
        """Calculate fill price with slippage."""
        base_price = order.price if order.price else current_bar.close
        
        if self.slippage_model == 'fixed':
            # Fixed slippage of 1 pip
            slippage = order.symbol.pip_value
        elif self.slippage_model == 'realistic':
            # Slippage based on volatility (0-2 pips)
            bar_range = current_bar.high - current_bar.low
            slippage = min(bar_range * Decimal("0.1"), order.symbol.pip_value * 2)
        elif self.slippage_model == 'aggressive':
            # Worst-case slippage (0-5 pips)
            slippage = order.symbol.pip_value * Decimal(str(random.uniform(0, 5)))
        else:
            slippage = Decimal("0")
        
        # Apply slippage (worse fill for trader)
        if order.side == OrderSide.BUY:
            fill_price = base_price + slippage
        else:
            fill_price = base_price - slippage
        
        return fill_price
    
    def get_balance(self) -> Decimal:
        """Get current balance."""
        return self.balance
    
    def get_equity(self) -> Decimal:
        """Get current equity (balance + unrealized P&L)."""
        unrealized_pnl = sum(p.unrealized_pnl for p in self.positions.values())
        return self.balance + unrealized_pnl
    
    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        return list(self.positions.values())
    
    def get_daily_pnl(self) -> Decimal:
        """Get daily P&L."""
        return self.daily_pnl
    
    def reset(self) -> None:
        """Reset broker state."""
        self.balance = self.initial_capital
        self.equity = self.initial_capital
        self.positions = {}
        self.closed_trades = []
        self.daily_pnl = Decimal("0")
    
    def reset_daily(self) -> None:
        """Reset daily metrics (called at start of each trading day)."""
        self.daily_pnl = Decimal("0")
    
    def get_closed_trades(self) -> List[Dict]:
        """Get list of closed trades."""
        return self.closed_trades
