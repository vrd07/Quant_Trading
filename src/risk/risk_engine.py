"""
Risk Engine - Central risk management and position sizing.

This module has VETO POWER over all trading decisions.
No order can be placed without passing all risk checks.

Risk checks (in order, fail fast):
1. Kill switch active? → REJECT
2. Circuit breaker active? → REJECT
3. Daily loss limit reached? → REJECT + TRIGGER KILL SWITCH
4. Drawdown limit reached? → REJECT + TRIGGER KILL SWITCH
5. Position count limit? → REJECT
6. Exposure limit per symbol? → REJECT
7. Position size valid? → REJECT
8. Stop loss present? → REJECT
9. Risk per trade exceeded? → REJECT
10. Correlation risk? → REJECT (if enabled)
"""

from typing import Dict, Tuple, Optional
from decimal import Decimal
from datetime import datetime, timezone
import json
from pathlib import Path

from ..core.types import Order, Position, Symbol, RiskMetrics, SystemState
from ..core.constants import (
    OrderSide, PositionSide,
    DEFAULT_RISK_PER_TRADE_PCT,
    DEFAULT_MAX_DAILY_LOSS_PCT,
    DEFAULT_MAX_DRAWDOWN_PCT,
    DEFAULT_MAX_POSITIONS
)
from ..core.exceptions import (
    RiskLimitExceededError,
    DailyLossLimitError,
    DrawdownLimitError,
    ExposureLimitError,
    KillSwitchActiveError,
    PositionSizeLimitError
)

from .position_sizer import PositionSizer
from .kill_switch import KillSwitch
from .circuit_breaker import CircuitBreaker
from .drawdown_tracker import DrawdownTracker
from .exposure_manager import ExposureManager


class RiskEngine:
    """
    Central risk management engine.
    
    This is the ONLY gatekeeper between strategy signals and order execution.
    """
    
    def __init__(self, config: Dict):
        """
        Initialize risk engine with configuration.
        
        Args:
            config: Risk configuration dictionary
        """
        self.config = config
        
        # Load risk limits
        risk_config = config.get('risk', {})
        self.max_daily_loss_pct = Decimal(str(risk_config.get('max_daily_loss_pct', DEFAULT_MAX_DAILY_LOSS_PCT)))
        self.max_drawdown_pct = Decimal(str(risk_config.get('max_drawdown_pct', DEFAULT_MAX_DRAWDOWN_PCT)))
        self.risk_per_trade_pct = Decimal(str(risk_config.get('risk_per_trade_pct', DEFAULT_RISK_PER_TRADE_PCT)))
        self.max_positions = risk_config.get('max_positions', DEFAULT_MAX_POSITIONS)
        self.max_exposure_per_symbol_pct = Decimal(str(risk_config.get('max_exposure_per_symbol_pct', 0.30)))
        self.max_daily_trades = risk_config.get('max_daily_trades', 50)
        
        # Initialize sub-components
        self.position_sizer = PositionSizer(config)
        self.kill_switch = KillSwitch()
        self.circuit_breaker = CircuitBreaker(
            max_consecutive_losses=risk_config.get('circuit_breaker', {}).get('max_consecutive_losses', 3),
            cooldown_minutes=risk_config.get('circuit_breaker', {}).get('cooldown_minutes', 30)
        )
        self.drawdown_tracker = DrawdownTracker(max_drawdown_pct=self.max_drawdown_pct)
        self.exposure_manager = ExposureManager(max_exposure_pct=self.max_exposure_per_symbol_pct)
        
        # Absolute dollar loss limit (GFT account protection)
        self.absolute_max_loss_usd = Decimal(str(risk_config.get('absolute_max_loss_usd', 0)))
        self.initial_balance = Decimal(str(config.get('account', {}).get('initial_balance', 0)))
        
        # State tracking
        self.daily_start_equity = Decimal("0")
        self.equity_high_water_mark = Decimal("0")
        self.daily_trades_count = 0
        
        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def validate_order(
        self,
        order: Order,
        account_balance: Decimal,
        account_equity: Decimal,
        current_positions: Dict[str, Position],
        daily_pnl: Decimal
    ) -> Tuple[bool, str]:
        """
        Validate order against ALL risk rules.
        
        This is the main entry point - every order must pass through here.
        
        Args:
            order: Order to validate
            account_balance: Current account balance
            account_equity: Current account equity
            current_positions: Dict of open positions
            daily_pnl: P&L for current trading day
        
        Returns:
            (is_valid, rejection_reason)
            If is_valid=False, the order MUST be rejected
        
        Raises:
            Various risk exceptions for critical violations
        """
        try:
            # CHECK 1: Kill switch
            if self.kill_switch.is_active():
                reason = "Kill switch is active - all trading halted"
                self.logger.critical(
                    "Order rejected by kill switch",
                    order_id=str(order.order_id),
                    symbol=order.symbol.ticker if order.symbol else None
                )
                raise KillSwitchActiveError(reason)
            
            # CHECK 2: Circuit breaker
            allowed, cb_reason = self.circuit_breaker.is_trading_allowed()
            if not allowed:
                self.logger.warning(
                    "Order rejected by circuit breaker",
                    reason=cb_reason,
                    order_id=str(order.order_id)
                )
                return False, cb_reason
            
            # CHECK 3: Annual/Account Balance Check
            if account_balance <= 0:
                reason = f"Insufficient account balance: ${account_balance}"
                self.logger.warning(
                    "Order rejected - zero/negative balance",
                    balance=float(account_balance),
                    order_id=str(order.order_id)
                )
                return False, reason

            # CHECK 3b: Absolute dollar loss limit (GFT account protection)
            if self.absolute_max_loss_usd > 0 and self.initial_balance > 0:
                total_loss = self.initial_balance - account_equity
                if total_loss >= self.absolute_max_loss_usd:
                    reason = f"ABSOLUTE LOSS LIMIT BREACHED: ${total_loss:.2f} >= ${self.absolute_max_loss_usd} (initial: ${self.initial_balance})"
                    self.logger.critical(
                        "🚨 ABSOLUTE DOLLAR LOSS LIMIT HIT - GFT PROTECTION 🚨",
                        total_loss=float(total_loss),
                        limit=float(self.absolute_max_loss_usd),
                        initial_balance=float(self.initial_balance),
                        current_equity=float(account_equity),
                        order_id=str(order.order_id)
                    )
                    self._trigger_kill_switch(reason)
                    raise DrawdownLimitError(
                        reason,
                        drawdown=total_loss / self.initial_balance,
                        limit=self.absolute_max_loss_usd / self.initial_balance
                    )
                # Warn if approaching limit (80%)
                elif total_loss >= self.absolute_max_loss_usd * Decimal("0.8"):
                    self.logger.warning(
                        "⚠️ Approaching ABSOLUTE LOSS LIMIT",
                        total_loss=float(total_loss),
                        limit=float(self.absolute_max_loss_usd),
                        pct_used=float(total_loss / self.absolute_max_loss_usd * 100)
                    )

            # CHECK 4: Daily loss limit
            daily_loss = -daily_pnl if daily_pnl < 0 else Decimal("0")
            max_daily_loss = account_balance * self.max_daily_loss_pct
            
            # Ensure we don't trigger on 0 vs 0 if balance check somehow passed or logic changes
            if daily_loss >= max_daily_loss and max_daily_loss > 0:
                reason = f"Daily loss limit reached: ${daily_loss} >= ${max_daily_loss}"
                self.logger.error(
                    "DAILY LOSS LIMIT EXCEEDED",
                    daily_loss=float(daily_loss),
                    limit=float(max_daily_loss),
                    order_id=str(order.order_id)
                )
                self._trigger_kill_switch(reason)
                raise DailyLossLimitError(
                    reason,
                    daily_loss=daily_loss,
                    limit=max_daily_loss
                )
            elif daily_loss > 0 and daily_loss >= max_daily_loss:
                 # Case where limit is 0 but we have loss (should have been caught by balance check, but safe to keep)
                reason = f"Daily loss limit reached (zero limit): ${daily_loss} >= ${max_daily_loss}"
                self._trigger_kill_switch(reason)
                raise DailyLossLimitError(reason, daily_loss=daily_loss, limit=max_daily_loss)
            
            # Warn if approaching limit (80%)
            if daily_loss >= max_daily_loss * Decimal("0.8"):
                self.logger.warning(
                    "Daily loss approaching limit",
                    daily_loss=float(daily_loss),
                    limit=float(max_daily_loss),
                    pct_used=float(daily_loss / max_daily_loss)
                )
            
            # CHECK 4: Drawdown limit
            current_drawdown = self.drawdown_tracker.calculate_drawdown(
                equity_high_water_mark=self.equity_high_water_mark,
                current_equity=account_equity
            )
            
            if current_drawdown >= self.max_drawdown_pct:
                reason = f"Drawdown limit reached: {current_drawdown:.2%} >= {self.max_drawdown_pct:.2%}"
                self.logger.error(
                    "DRAWDOWN LIMIT EXCEEDED",
                    drawdown=float(current_drawdown),
                    limit=float(self.max_drawdown_pct),
                    order_id=str(order.order_id)
                )
                self._trigger_kill_switch(reason)
                raise DrawdownLimitError(
                    reason,
                    drawdown=current_drawdown,
                    limit=self.max_drawdown_pct
                )
            
            # CHECK 5: Max daily trades limit
            if self.daily_trades_count >= self.max_daily_trades:
                reason = f"Max daily trades reached: {self.daily_trades_count} >= {self.max_daily_trades}"
                self.logger.warning(
                    "Order rejected - max daily trades",
                    count=self.daily_trades_count,
                    limit=self.max_daily_trades
                )
                return False, reason

            # CHECK 6: Position count limit
            if len(current_positions) >= self.max_positions:
                reason = f"Max positions reached: {len(current_positions)} >= {self.max_positions}"
                self.logger.warning(
                    "Order rejected - max positions",
                    count=len(current_positions),
                    limit=self.max_positions
                )
                return False, reason
            
            # CHECK 6: Position size validation
            if order.quantity <= 0:
                reason = f"Invalid position size: {order.quantity}"
                self.logger.error("Invalid position size", quantity=float(order.quantity))
                return False, reason
            
            # CHECK 7: Symbol exposure limit
            if order.symbol:
                is_allowed, exposure_reason = self.exposure_manager.check_exposure_limit(
                    symbol=order.symbol,
                    new_order=order,
                    current_positions=current_positions,
                    account_equity=account_equity
                )
                
                if not is_allowed:
                    self.logger.warning(
                        "Order rejected - exposure limit",
                        symbol=order.symbol.ticker,
                        reason=exposure_reason
                    )
                    return False, exposure_reason
            
            # CHECK 8: Stop loss requirement
            if not order.stop_loss:
                reason = "Stop loss is required for all orders"
                self.logger.error(
                    "Order rejected - no stop loss",
                    order_id=str(order.order_id)
                )
                return False, reason
            
            # CHECK 9: Risk per trade validation
            if order.price and order.stop_loss:
                risk_amount = abs(order.quantity * (order.price - order.stop_loss))
                max_risk = account_balance * self.risk_per_trade_pct
                
                if risk_amount > max_risk:
                    reason = f"Risk per trade exceeded: ${risk_amount} > ${max_risk}"
                    self.logger.warning(
                        "Order rejected - risk per trade",
                        risk_amount=float(risk_amount),
                        max_risk=float(max_risk)
                    )
                    return False, reason
            
            # All checks passed
            self.logger.info(
                "Order validated successfully",
                order_id=str(order.order_id),
                symbol=order.symbol.ticker if order.symbol else None,
                side=order.side.value if order.side else None,
                quantity=float(order.quantity)
            )
            return True, "OK"
            
        except (KillSwitchActiveError, DailyLossLimitError, DrawdownLimitError) as e:
            # Critical errors - propagate up
            raise
        except Exception as e:
            # Unexpected error - reject order to be safe
            self.logger.error(
                "Unexpected error in risk validation",
                error=str(e),
                order_id=str(order.order_id),
                exc_info=True
            )
            return False, f"Risk validation error: {str(e)}"
    
    def calculate_position_size(
        self,
        symbol: Symbol,
        account_balance: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        side: OrderSide,
        current_positions: Optional[Dict[str, Position]] = None,
        account_equity: Optional[Decimal] = None
    ) -> Decimal:
        """
        Calculate optimal position size.
        
        Uses configured position sizing method (Kelly, fixed fractional, volatility-based).
        Does NOT exceed exposure limits if context (positions/equity) is provided.
        
        Args:
            symbol: Trading symbol
            account_balance: Current account balance
            entry_price: Intended entry price
            stop_loss: Stop loss price
            side: Order side (BUY/SELL)
            current_positions: Optional dict of open positions (for exposure check)
            account_equity: Optional current account equity (for exposure check)
        
        Returns:
            Position size in lots, rounded to symbol lot step
        """
        # 1. Calculate risk-based size (Stop Loss distance)
        risk_size = self.position_sizer.calculate_position_size(
            symbol=symbol,
            account_balance=account_balance,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_pct=self.risk_per_trade_pct
        )
        
        # 2. Apply exposure limit if context provided
        if current_positions is not None and account_equity is not None:
            max_exposure_size = self.exposure_manager.get_max_position_size(
                symbol=symbol,
                current_positions=current_positions,
                account_equity=account_equity,
                entry_price=entry_price
            )
            
            if risk_size > max_exposure_size:
                # User request: Limit to 0.1 lots max when exposure limit is hit
                capped_size = min(max_exposure_size, Decimal("0.1"))
                
                self.logger.info(
                    "Position size capped by exposure limit (and user 0.1 cap)",
                    risk_size=float(risk_size),
                    exposure_limit=float(max_exposure_size),
                    final_size=float(capped_size),
                    symbol=symbol.ticker
                )
                return capped_size
                
        return risk_size
    
    def record_trade_result(self, pnl: Decimal) -> None:
        """
        Record trade result for circuit breaker tracking.
        
        Args:
            pnl: Realized P&L from trade
        """
        self.circuit_breaker.record_trade(pnl)
        
        if pnl < 0:
            self.logger.info(
                "Trade loss recorded",
                pnl=float(pnl),
                consecutive_losses=self.circuit_breaker.consecutive_losses
            )
        else:
            self.logger.info(
                "Trade win recorded",
                pnl=float(pnl)
            )
    
    def update_equity_hwm(self, current_equity: Decimal) -> None:
        """
        Update equity high water mark for drawdown calculation.
        
        Args:
            current_equity: Current account equity
        """
        if current_equity > self.equity_high_water_mark:
            old_hwm = self.equity_high_water_mark
            self.equity_high_water_mark = current_equity
            
            self.logger.info(
                "New equity high water mark",
                old_hwm=float(old_hwm),
                new_hwm=float(current_equity)
            )

    def increment_daily_trade_count(self) -> None:
        """Increment the counter for daily trades."""
        self.daily_trades_count += 1
        self.logger.info(
            "Daily trade count incremented",
            count=self.daily_trades_count,
            limit=self.max_daily_trades
        )
    
    def reset_daily_metrics(self, starting_equity: Decimal) -> None:
        """
        Reset daily tracking at start of new trading day.
        
        Args:
            starting_equity: Equity at start of day
        """
        self.daily_start_equity = starting_equity
        self.daily_trades_count = 0
        
        self.logger.info(
            "Daily metrics reset",
            starting_equity=float(starting_equity),
            daily_trades_count=0,
            date=datetime.now(timezone.utc).date().isoformat()
        )
    
    def get_risk_metrics(
        self,
        account_balance: Decimal,
        account_equity: Decimal,
        current_positions: Dict[str, Position],
        daily_pnl: Decimal
    ) -> RiskMetrics:
        """
        Get current risk metrics snapshot.
        
        Returns:
            RiskMetrics object with current risk state
        """
        # Calculate exposures
        total_exposure = sum(
            pos.quantity * pos.current_price * pos.symbol.value_per_lot
            for pos in current_positions.values()
        )
        
        net_exposure = sum(
            pos.quantity * pos.current_price * pos.symbol.value_per_lot * (1 if pos.side == PositionSide.LONG else -1)
            for pos in current_positions.values()
        )
        
        # Calculate daily loss limit remaining
        daily_loss = -daily_pnl if daily_pnl < 0 else Decimal("0")
        max_daily_loss = account_balance * self.max_daily_loss_pct
        daily_loss_remaining = max_daily_loss - daily_loss
        
        # Calculate drawdown
        current_drawdown = self.drawdown_tracker.calculate_drawdown(
            equity_high_water_mark=self.equity_high_water_mark,
            current_equity=account_equity
        )
        
        return RiskMetrics(
            timestamp=datetime.now(timezone.utc),
            account_balance=account_balance,
            account_equity=account_equity,
            total_exposure=total_exposure,
            net_exposure=net_exposure,
            daily_pnl=daily_pnl,
            daily_loss_limit=max_daily_loss,
            daily_loss_remaining=daily_loss_remaining,
            max_drawdown=self.equity_high_water_mark - account_equity,
            max_drawdown_limit=self.equity_high_water_mark * self.max_drawdown_pct,
            current_drawdown=current_drawdown,
            open_positions_count=len(current_positions),
            kill_switch_active=self.kill_switch.is_active(),
            circuit_breaker_active=not self.circuit_breaker.is_trading_allowed()[0]
        )
    
    def validate_account_balance(self, reported_balance: Decimal, mt5_balance: Decimal) -> bool:
        """
        Verify that reported balance matches reality from MT5.
        
        This prevents trading with stale/corrupted state information.
        A tolerance of 0.1% is allowed for minor rounding differences.
        
        Args:
            reported_balance: Balance from system state
            mt5_balance: Balance from MT5 connector
            
        Returns:
            True if within tolerance
        """
        if mt5_balance <= 0:
            self.logger.critical("MT5 balance is zero or negative - safety halt")
            return False
            
        difference = abs(reported_balance - mt5_balance)
        tolerance = mt5_balance * Decimal("0.001") # 0.1% tolerance
        
        if difference > tolerance:
            self.logger.error(
                "BALANCE DISCREPANCY - safety check failed",
                reported=float(reported_balance),
                actual=float(mt5_balance),
                difference=float(difference),
                tolerance=float(tolerance)
            )
            return False
            
        self.logger.info(
            "Balance verification successful",
            reported=float(reported_balance),
            actual=float(mt5_balance)
        )
        return True

    def _trigger_kill_switch(self, reason: str) -> None:
        """
        EMERGENCY STOP - Halt all trading immediately.
        
        This is irreversible and requires manual intervention.
        
        Args:
            reason: Why kill switch was triggered
        """
        self.kill_switch.trigger(reason)
        
        self.logger.critical(
            "🚨 KILL SWITCH TRIGGERED 🚨",
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
        
        # Write to separate alert file
        alert_file = Path("data/state/kill_switch_alert.json")
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(alert_file, 'w') as f:
            json.dump({
                'triggered_at': datetime.now(timezone.utc).isoformat(),
                'reason': reason,
                'status': 'ACTIVE'
            }, f, indent=2)
