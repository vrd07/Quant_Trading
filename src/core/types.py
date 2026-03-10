"""Core data types for the trading system.

This module defines all fundamental data structures used throughout the
trading system using dataclasses. All types follow strict validation rules:
- Decimal for all monetary values (never float)
- datetime for all timestamps (UTC-aware)
- UUID for all IDs
- Validation in __post_init__ where needed
- Immutable types are frozen
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any
from uuid import UUID, uuid4

from .constants import (
    OrderSide, OrderType, OrderStatus,
    PositionSide, MarketRegime
)
from .exceptions import InvalidBarError


# ============================================================================
# Market Data Types
# ============================================================================

@dataclass(frozen=True)
class Symbol:
    """
    Trading instrument specification.
    
    Attributes:
        ticker: Symbol name (e.g., "XAUUSD")
        exchange: Exchange/broker (default "MT5")
        pip_value: Minimum price movement
        min_lot: Minimum position size
        max_lot: Maximum position size
        lot_step: Position size increment
        value_per_lot: Notional value multiplier (e.g., 100 for gold)
        commission_per_lot: Trading cost per lot
    """
    ticker: str
    exchange: str = "MT5"
    pip_value: Decimal = Decimal("0.01")
    min_lot: Decimal = Decimal("0.01")
    max_lot: Decimal = Decimal("100.0")
    lot_step: Decimal = Decimal("0.01")
    value_per_lot: Decimal = Decimal("1.0")
    commission_per_lot: Decimal = Decimal("0.0")
    
    def __str__(self) -> str:
        return self.ticker
    
    def __hash__(self) -> int:
        return hash(self.ticker)


@dataclass
class Bar:
    """
    OHLCV candlestick bar.
    
    Validates OHLC integrity on creation.
    """
    symbol: Symbol
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    metadata: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Validate bar integrity."""
        # High must be >= max(open, close)
        if self.high < max(self.open, self.close):
            raise InvalidBarError(
                f"Invalid bar: high ({self.high}) < max(open, close)",
                symbol=self.symbol.ticker,
                timestamp=self.timestamp.isoformat()
            )
        
        # Low must be <= min(open, close)
        if self.low > min(self.open, self.close):
            raise InvalidBarError(
                f"Invalid bar: low ({self.low}) > min(open, close)",
                symbol=self.symbol.ticker,
                timestamp=self.timestamp.isoformat()
            )
        
        # Ensure UTC timezone
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, 'timestamp', self.timestamp.replace(tzinfo=timezone.utc))
    
    @property
    def typical_price(self) -> Decimal:
        """(High + Low + Close) / 3"""
        return (self.high + self.low + self.close) / Decimal("3")
    
    @property
    def range(self) -> Decimal:
        """High - Low"""
        return self.high - self.low


@dataclass
class Tick:
    """Real-time market tick."""
    symbol: Symbol
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume: Decimal
    
    def __post_init__(self):
        """Ensure UTC timezone."""
        if self.timestamp.tzinfo is None:
            object.__setattr__(self, 'timestamp', self.timestamp.replace(tzinfo=timezone.utc))
    
    @property
    def mid(self) -> Decimal:
        """Mid price: (bid + ask) / 2"""
        return (self.bid + self.ask) / Decimal("2")
    
    @property
    def spread(self) -> Decimal:
        """Bid-ask spread in price units"""
        return self.ask - self.bid
    
    @property
    def spread_pips(self) -> Decimal:
        """Spread in pips"""
        return self.spread / self.symbol.pip_value


# ============================================================================
# Trading Types
# ============================================================================

@dataclass
class Order:
    """
    Order representation with full lifecycle tracking.
    
    State transitions:
    PENDING → SENT → ACCEPTED → FILLED
                  ↓           ↓
              REJECTED   CANCELLED
    """
    order_id: UUID = field(default_factory=uuid4)
    symbol: Optional[Symbol] = None
    side: Optional[OrderSide] = None
    order_type: OrderType = OrderType.MARKET
    quantity: Decimal = Decimal("0")
    price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    filled_price: Optional[Decimal] = None
    filled_quantity: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_terminal(self) -> bool:
        """Check if order is in terminal state (no further updates expected)."""
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED
        }
    
    def is_active(self) -> bool:
        """Check if order is active (waiting for fill)."""
        return self.status in {
            OrderStatus.PENDING,
            OrderStatus.SENT,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED
        }
    
    def calculate_slippage(self, expected_price: Decimal) -> Decimal:
        """Calculate slippage from expected fill price."""
        if self.filled_price is None:
            return Decimal("0")
        
        if self.side == OrderSide.BUY:
            # Positive slippage = paid more than expected
            return self.filled_price - expected_price
        else:
            # Positive slippage = received less than expected
            return expected_price - self.filled_price


@dataclass
class Position:
    """
    Open position with P&L tracking.
    """
    position_id: UUID = field(default_factory=uuid4)
    symbol: Optional[Symbol] = None
    side: PositionSide = PositionSide.FLAT
    quantity: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def update_price(self, price: Decimal) -> None:
        """Update current price and recalculate unrealized P&L."""
        self.current_price = price
        self.updated_at = datetime.now(timezone.utc)
        
        if self.quantity > 0 and self.symbol:
            price_diff = price - self.entry_price
            
            if self.side == PositionSide.LONG:
                self.unrealized_pnl = price_diff * self.quantity * self.symbol.value_per_lot
            elif self.side == PositionSide.SHORT:
                self.unrealized_pnl = -price_diff * self.quantity * self.symbol.value_per_lot
    
    @property
    def total_pnl(self) -> Decimal:
        """Total P&L (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl
    
    @property
    def is_long(self) -> bool:
        return self.side == PositionSide.LONG
    
    @property
    def is_short(self) -> bool:
        return self.side == PositionSide.SHORT
    
    @property
    def is_flat(self) -> bool:
        return self.side == PositionSide.FLAT or self.quantity == 0


@dataclass
class Signal:
    """
    Trading signal generated by a strategy.
    """
    signal_id: UUID = field(default_factory=uuid4)
    strategy_name: str = ""
    symbol: Optional[Symbol] = None
    side: Optional[OrderSide] = None
    strength: float = 0.0  # 0.0 to 1.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    regime: MarketRegime = MarketRegime.UNKNOWN
    entry_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate signal strength."""
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"Signal strength must be 0.0-1.0, got {self.strength}")


# ============================================================================
# Risk & Monitoring Types
# ============================================================================

@dataclass
class RiskMetrics:
    """
    Snapshot of current risk metrics.
    """
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    account_balance: Decimal = Decimal("0")
    account_equity: Decimal = Decimal("0")
    total_exposure: Decimal = Decimal("0")
    net_exposure: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    daily_loss_limit: Decimal = Decimal("0")
    daily_loss_remaining: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_limit: Decimal = Decimal("0")
    current_drawdown: Decimal = Decimal("0")
    open_positions_count: int = 0
    kill_switch_active: bool = False
    circuit_breaker_active: bool = False
    
    @property
    def daily_loss_pct_used(self) -> Decimal:
        """Percentage of daily loss limit used."""
        if self.daily_loss_limit == 0:
            return Decimal("0")
        return abs(self.daily_pnl) / self.daily_loss_limit
    
    @property
    def drawdown_pct_used(self) -> Decimal:
        """Percentage of max drawdown limit used."""
        if self.max_drawdown_limit == 0:
            return Decimal("0")
        return self.current_drawdown / self.max_drawdown_limit


# ============================================================================
# System State Types
# ============================================================================

@dataclass
class SystemState:
    """
    Complete system state for persistence and recovery.
    
    This is what gets saved to disk and restored after crashes.
    """
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    positions: Dict[UUID, Position] = field(default_factory=dict)
    open_orders: Dict[UUID, Order] = field(default_factory=dict)
    account_balance: Decimal = Decimal("0")
    account_equity: Decimal = Decimal("0")
    equity_high_water_mark: Decimal = Decimal("0")
    daily_start_equity: Decimal = Decimal("0")
    daily_pnl: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    consecutive_losses: int = 0
    daily_trades_count: int = 0
    kill_switch_active: bool = False
    circuit_breaker_active: bool = False
    last_trade_time: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict for persistence."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'positions': {
                str(pid): {
                    'symbol': pos.symbol.ticker if pos.symbol else None,
                    'side': pos.side.value,
                    'quantity': str(pos.quantity),
                    'entry_price': str(pos.entry_price),
                    'current_price': str(pos.current_price),
                    'stop_loss': str(pos.stop_loss) if pos.stop_loss else None,
                    'take_profit': str(pos.take_profit) if pos.take_profit else None,
                    'unrealized_pnl': str(pos.unrealized_pnl),
                    'realized_pnl': str(pos.realized_pnl),
                    'opened_at': pos.opened_at.isoformat(),
                    'metadata': pos.metadata
                }
                for pid, pos in self.positions.items()
            },
            'open_orders': {
                str(oid): {
                    'symbol': order.symbol.ticker if order.symbol else None,
                    'side': order.side.value if order.side else None,
                    'order_type': order.order_type.value,
                    'quantity': str(order.quantity),
                    'price': str(order.price) if order.price else None,
                    'stop_loss': str(order.stop_loss) if order.stop_loss else None,
                    'take_profit': str(order.take_profit) if order.take_profit else None,
                    'status': order.status.value,
                    'created_at': order.created_at.isoformat(),
                    'metadata': order.metadata
                }
                for oid, order in self.open_orders.items()
            },
            'account_balance': str(self.account_balance),
            'account_equity': str(self.account_equity),
            'equity_high_water_mark': str(self.equity_high_water_mark),
            'daily_start_equity': str(self.daily_start_equity),
            'daily_pnl': str(self.daily_pnl),
            'total_pnl': str(self.total_pnl),
            'consecutive_losses': self.consecutive_losses,
            'daily_trades_count': self.daily_trades_count,
            'kill_switch_active': self.kill_switch_active,
            'circuit_breaker_active': self.circuit_breaker_active,
            'last_trade_time': self.last_trade_time.isoformat() if self.last_trade_time else None,
            'metadata': self.metadata
        }
