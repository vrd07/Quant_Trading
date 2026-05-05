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

from typing import Callable, Dict, List, Optional
from decimal import Decimal
from uuid import uuid4
import random

from ..core.types import Order, Position, Bar, Symbol
from ..core.constants import OrderSide, OrderStatus, PositionSide
from .fill_model import StrictFillModel, FillContext


class SimulatedBroker:
    """Simulate broker for backtesting."""

    def __init__(
        self,
        initial_capital: Decimal,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "realistic",
        trailing_stop_config: Optional[Dict] = None,
        news_active_at: Optional[Callable[[object], bool]] = None,
    ):
        """
        Initialize simulated broker.

        Args:
            initial_capital: Starting capital
            commission_per_trade: Commission per trade
            slippage_model: 'fixed', 'realistic', 'aggressive', or 'strict'
                (strict = backtest.md §3 fill model: spread + 1.5× slippage +
                queue penalty for stops)
            trailing_stop_config: Trailing stop config from risk.trailing_stop section
            news_active_at: Optional callable (bar) -> bool that returns True
                if the bar timestamp falls inside a high-impact news blackout
                window. Wired by #2 (news-blackout replay). When True, the
                strict fill model widens the spread by 3×.
        """
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_model = slippage_model

        # Strict fill model — only constructed when requested so legacy
        # backtests pay zero startup cost.
        self._strict_fill: Optional[StrictFillModel] = (
            StrictFillModel() if slippage_model == "strict" else None
        )
        self._news_active_at = news_active_at

        # Trailing stop config (mirrors TrailingStopManager stages)
        ts_cfg = trailing_stop_config or {}
        self.trailing_stop_enabled = ts_cfg.get('enabled', False)
        self.breakeven_atr_mult = ts_cfg.get('breakeven_atr_mult', 0.5)
        self.lock_atr_mult = ts_cfg.get('lock_atr_mult', 1.5)
        self.lock_fraction = ts_cfg.get('lock_fraction', 0.5)
        self.time_stop_minutes = ts_cfg.get('time_stop_minutes', None)

        # Trailing stop tracking: pos_id -> stage (0=none, 1=breakeven, 2=locked)
        self._trail_stage: Dict[str, int] = {}
        self._trail_initial_sl: Dict[str, Decimal] = {}
        self._trail_atr_dist: Dict[str, Decimal] = {}
        self._trail_entry_bar_idx: Dict[str, int] = {}  # for time stop
        self._current_bar_idx: int = 0
        self._bar_interval_minutes: float = 5.0  # default 5m bars

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

        # Check if we have capital (account for leverage)
        leverage = getattr(order.symbol, 'leverage', None) or Decimal('1')
        required_margin = (fill_price * order.quantity * order.symbol.value_per_lot) / leverage

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
        pos_id = str(position.position_id)
        self.positions[pos_id] = position

        # Register trailing stop tracking
        if self.trailing_stop_enabled and position.stop_loss:
            atr_dist = abs(fill_price - position.stop_loss)
            if atr_dist > 0:
                self._trail_stage[pos_id] = 0
                self._trail_initial_sl[pos_id] = position.stop_loss
                self._trail_atr_dist[pos_id] = atr_dist
                self._trail_entry_bar_idx[pos_id] = self._current_bar_idx

        return fill_price
    
    def update_positions(self, current_bar) -> None:
        """Update all positions with current bar prices."""
        # current_bar may be a pandas Series or Bar dataclass
        try:
            close = Decimal(str(current_bar['close']))
        except (KeyError, TypeError):
            close = Decimal(str(current_bar.close))
        for position in self.positions.values():
            position.update_price(close)

    def check_exits(self, current_bar) -> None:
        """Check trailing stop updates, then SL/TP hits."""
        try:
            bar_low = Decimal(str(current_bar['low']))
            bar_high = Decimal(str(current_bar['high']))
            bar_close = Decimal(str(current_bar['close']))
        except (KeyError, TypeError):
            bar_low = Decimal(str(current_bar.low))
            bar_high = Decimal(str(current_bar.high))
            bar_close = Decimal(str(current_bar.close))

        self._current_bar_idx += 1

        # ── Trailing stop: update SL stages before checking exits ──
        if self.trailing_stop_enabled:
            for pos_id, position in list(self.positions.items()):
                self._update_trailing_stop(pos_id, position, bar_close)

        positions_to_close = {}

        # Build a single FillContext for this bar; reuse for every position.
        if self._strict_fill is not None:
            news_active = bool(self._news_active_at(current_bar)) if self._news_active_at else False
            fill_ctx = self._build_fill_context(current_bar, news_active=news_active)
        else:
            fill_ctx = None

        for pos_id, position in self.positions.items():
            # Time stop: close if position has been open too long
            if self.trailing_stop_enabled and self.time_stop_minutes is not None:
                entry_idx = self._trail_entry_bar_idx.get(pos_id)
                if entry_idx is not None:
                    bars_held = self._current_bar_idx - entry_idx
                    minutes_held = bars_held * self._bar_interval_minutes
                    if minutes_held >= self.time_stop_minutes:
                        positions_to_close[pos_id] = (bar_close, 'time_stop')
                        continue

            # Check stop loss (SL assumed hit first if both SL and TP on same bar)
            if position.stop_loss:
                hit = False
                if position.side == PositionSide.LONG and bar_low <= position.stop_loss:
                    hit = True
                elif position.side == PositionSide.SHORT and bar_high >= position.stop_loss:
                    hit = True
                if hit:
                    if fill_ctx is not None:
                        # Strict: worse-of (stop ± slippage, bar extremum)
                        sl_fill = self._strict_fill.stop_fill(
                            symbol=position.symbol,
                            position_side=position.side,
                            stop_price=position.stop_loss,
                            ctx=fill_ctx,
                        )
                    else:
                        sl_fill = position.stop_loss
                    positions_to_close[pos_id] = (sl_fill, 'stop_loss')

            # Check take profit (only if not already marked for SL/time_stop)
            if pos_id not in positions_to_close and position.take_profit:
                tp_hit = False
                if position.side == PositionSide.LONG and bar_high >= position.take_profit:
                    tp_hit = True
                elif position.side == PositionSide.SHORT and bar_low <= position.take_profit:
                    tp_hit = True
                if tp_hit:
                    # TP fills exactly at limit in both legacy and strict models
                    # (§3.3: "no positive slippage, no queue priority").
                    positions_to_close[pos_id] = (position.take_profit, 'take_profit')

        # Close positions
        for pos_id, (exit_price, exit_reason) in positions_to_close.items():
            self._close_position(pos_id, exit_price, exit_reason)

    def _update_trailing_stop(self, pos_id: str, position: Position, current_price: Decimal) -> None:
        """Update trailing stop stage for a position (mirrors live TrailingStopManager)."""
        if pos_id not in self._trail_atr_dist:
            return

        atr_dist = self._trail_atr_dist[pos_id]
        stage = self._trail_stage.get(pos_id, 0)
        entry = position.entry_price
        is_long = position.side == PositionSide.LONG

        profit_distance = (current_price - entry) if is_long else (entry - current_price)

        # Convert float multipliers to Decimal for arithmetic with Decimal atr_dist
        lock_threshold = Decimal(str(self.lock_atr_mult)) * atr_dist
        be_threshold = Decimal(str(self.breakeven_atr_mult)) * atr_dist
        lock_offset = Decimal(str(self.lock_fraction)) * atr_dist

        # Stage 2: Lock in partial profit
        if stage < 2 and profit_distance >= lock_threshold:
            new_sl = (entry + lock_offset) if is_long else (entry - lock_offset)
            if (is_long and new_sl > (position.stop_loss or Decimal(0))) or \
               (not is_long and new_sl < (position.stop_loss or Decimal("999999"))):
                position.stop_loss = new_sl
                self._trail_stage[pos_id] = 2
            return

        # Stage 1: Move SL to breakeven
        if stage < 1 and profit_distance >= be_threshold:
            new_sl = entry
            if (is_long and new_sl > (position.stop_loss or Decimal(0))) or \
               (not is_long and new_sl < (position.stop_loss or Decimal("999999"))):
                position.stop_loss = new_sl
                self._trail_stage[pos_id] = 1
    
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
        if position.symbol and position.symbol.commission_per_lot > 0:
            pnl -= position.symbol.commission_per_lot * position.quantity * Decimal("2")

        # Update balance
        self.balance += pnl
        self.daily_pnl += pnl

        # Determine trailing stop stage at exit
        trail_stage = self._trail_stage.get(position_id, 0)
        stage_names = {0: 'none', 1: 'breakeven', 2: 'locked'}

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
            'trail_stage': stage_names.get(trail_stage, 'none'),
            'strategy': position.metadata.get('strategy')
        })

        # Cleanup trailing stop state
        self._trail_stage.pop(position_id, None)
        self._trail_initial_sl.pop(position_id, None)
        self._trail_atr_dist.pop(position_id, None)
        self._trail_entry_bar_idx.pop(position_id, None)

        # Remove position
        del self.positions[position_id]
    
    def _calculate_fill_price(self, order: Order, current_bar) -> Decimal:
        """Calculate fill price with slippage."""
        # current_bar may be a pandas Series or Bar dataclass
        try:
            bar_open = Decimal(str(current_bar['open']))
            bar_close = Decimal(str(current_bar['close']))
            bar_high = Decimal(str(current_bar['high']))
            bar_low = Decimal(str(current_bar['low']))
        except (KeyError, TypeError):
            bar_open = Decimal(str(current_bar.open))
            bar_close = Decimal(str(current_bar.close))
            bar_high = Decimal(str(current_bar.high))
            bar_low = Decimal(str(current_bar.low))

        base_price = order.price if order.price else bar_close

        # Strict model: cross half spread + 1.5× empirical slippage. The
        # base price is the next-bar open (matches §3.3 "fill at next-bar
        # open + slippage" for market orders); we approximate that with
        # the current bar's open since the engine already advances bars.
        if self.slippage_model == 'strict' and self._strict_fill is not None:
            news_active = bool(self._news_active_at(current_bar)) if self._news_active_at else False
            ctx = self._build_fill_context(current_bar, news_active=news_active)
            return self._strict_fill.market_fill(
                symbol=order.symbol,
                side=order.side,
                signal_price=bar_open if order.price is None else base_price,
                ctx=ctx,
            )

        if self.slippage_model == 'fixed':
            # Fixed slippage of 1 pip
            slippage = order.symbol.pip_value
        elif self.slippage_model == 'realistic':
            # Slippage based on bar range (0-10% of range, max 2 pips)
            bar_range = bar_high - bar_low
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

    def _build_fill_context(self, current_bar, news_active: bool = False) -> FillContext:
        """Translate a pandas Series / Bar into a FillContext for the strict model."""
        try:
            bar_open = Decimal(str(current_bar['open']))
            bar_close = Decimal(str(current_bar['close']))
            bar_high = Decimal(str(current_bar['high']))
            bar_low = Decimal(str(current_bar['low']))
            ts = current_bar.name  # pandas Series.name = index value
        except (KeyError, TypeError):
            bar_open = Decimal(str(current_bar.open))
            bar_close = Decimal(str(current_bar.close))
            bar_high = Decimal(str(current_bar.high))
            bar_low = Decimal(str(current_bar.low))
            ts = current_bar.timestamp

        # Hour-of-day in UTC for the spread curve. If the bar timestamp is
        # naive we assume it's already broker-local UTC (the historical CSVs
        # don't carry tz). Cheap to re-derive each bar.
        hour = getattr(ts, "hour", 0)
        return FillContext(
            bar_open=bar_open,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            hour_utc=int(hour),
            news_active=news_active,
        )
    
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
        self._trail_stage = {}
        self._trail_initial_sl = {}
        self._trail_atr_dist = {}
        self._trail_entry_bar_idx = {}
        self._current_bar_idx = 0
    
    def reset_daily(self) -> None:
        """Reset daily metrics (called at start of each trading day)."""
        self.daily_pnl = Decimal("0")
    
    def get_closed_trades(self) -> List[Dict]:
        """Get list of closed trades."""
        return self.closed_trades
