"""
Risk Engine - Central risk management and position sizing.

This module has VETO POWER over all trading decisions.
No order can be placed without passing all risk checks.

Risk checks (in order, fail fast):
1.  Kill switch active? → REJECT
2.  Circuit breaker active? → REJECT
2b. Hour blackout (UTC) hit? → REJECT            [added 2026-04-19]
3.  Daily loss limit reached? → REJECT + TRIGGER KILL SWITCH
4.  Drawdown limit reached? → REJECT + TRIGGER KILL SWITCH
4c. Manual daily-loss cap breached? → REJECT     [added 2026-04-19]
5.  Position count limit? → REJECT
6.  Exposure limit per symbol? → REJECT
7.  Position size valid? → REJECT
8.  Stop loss present? → REJECT
9.  Risk per trade exceeded? → REJECT
10. Correlation risk? → REJECT (if enabled)

Journal-driven guards (added 2026-04-19 from 145-trade audit):
- Hour blackout:  14-16 UTC lost -$196 across all strategies; config-driven
                  list of hours blocks every new order during those windows.
- Manual cap:     'manual'-tagged orders accounted for -$358 of -$400 net.
                  After N USD of manual loss in a UTC day, no more manual orders.
- Manual sizing:  Halve lot size for 'manual'-tagged orders. Keeps the user
                  in the game while they audit whether manual has real edge.
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
        
        # Per-strategy risk overrides. A strategy listed here uses its own
        # per-trade $ cap (and risk %) instead of the operator's global one.
        # Schema:
        #   risk.strategy_risk_overrides:
        #     <strategy_name>:
        #       risk_per_trade_usd: <num>      # absolute $ cap per trade
        #       risk_per_trade_pct: <num>      # OR fraction of balance (used by sizer)
        # Used by _check_16_risk_per_trade and calculate_position_size below.
        self._strategy_risk_overrides: Dict[str, Dict] = (
            risk_config.get('strategy_risk_overrides', {}) or {}
        )

        # Initialize sub-components
        self.position_sizer = PositionSizer(config)
        self.kill_switch = KillSwitch()
        self.circuit_breaker = CircuitBreaker(
            max_consecutive_losses=risk_config.get('circuit_breaker', {}).get('max_consecutive_losses', 3),
            cooldown_minutes=risk_config.get('circuit_breaker', {}).get('cooldown_minutes', 30)
        )
        self.drawdown_tracker = DrawdownTracker(max_drawdown_pct=self.max_drawdown_pct)
        self.exposure_manager = ExposureManager(max_exposure_pct=self.max_exposure_per_symbol_pct)
        
        # Absolute dollar limits (GFT account protection)
        self.absolute_max_loss_usd = Decimal(str(risk_config.get('absolute_max_loss_usd', 0)))
        self.max_daily_profit_usd = Decimal(str(risk_config.get('max_daily_profit_usd', 0)))
        self.initial_balance = Decimal(str(config.get('account', {}).get('initial_balance', 0)))

        # Pre-trade daily loss budget: reject orders whose worst-case SL hit
        # would push the combined daily loss past a safety margin.
        # Default 85% = halt at $250.75 when absolute limit is $295, giving
        # a $44.25 buffer for slippage and concurrent fills.
        self.daily_loss_budget_safety_pct = Decimal(
            str(risk_config.get('daily_loss_budget_safety_pct', 0.85))
        )
        
        # ── Hour blackout (journal-driven) ─────────────────────────────────
        # Carmack: design for the worst case — 14h-16h UTC lost $196 of $400 net.
        # Block those hours across every strategy unless config overrides.
        tw_cfg = risk_config.get('trading_windows', {}) or {}
        self.trading_windows_enabled: bool = bool(tw_cfg.get('enabled', False))
        raw_blocked = tw_cfg.get('blocked_hours_utc', []) or []
        # Normalize once at load — downstream check is a pure O(1) set lookup
        self.blocked_hours_utc = frozenset(
            int(h) for h in raw_blocked if 0 <= int(h) <= 23
        )

        # ── Manual-trade guard (journal-driven) ────────────────────────────
        # TJ: explicit over magic. A 'manual' strategy tag is recognized by
        # exact string match; no regex, no inheritance, no surprise behavior.
        mg_cfg = risk_config.get('manual_guard', {}) or {}
        self.manual_guard_enabled: bool = bool(mg_cfg.get('enabled', False))
        self.manual_daily_loss_cap_usd = Decimal(
            str(mg_cfg.get('daily_loss_cap_usd', 0))
        )
        self.manual_size_multiplier = Decimal(
            str(mg_cfg.get('size_multiplier', 0.5))
        )
        # Tracks dollar loss from manual-tagged orders in the current UTC day.
        # Reset by reset_daily_metrics().
        self._manual_daily_loss_usd = Decimal("0")

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
            # Sequential check order MUST match the original inlined version.
            # Critical checks (01, 05, 07, 10) raise; veto checks return early.
            # Do NOT reorder, do NOT eager-evaluate into a tuple — each line
            # short-circuits the next so a kill-switch breach never invokes
            # downstream side effects.

            # 01: Kill switch (raises)
            self._check_01_kill_switch(order)

            # 02: Circuit breaker
            ok, reason = self._check_02_circuit_breaker(order)
            if not ok:
                return False, reason

            # 03: Hour blackout
            ok, reason = self._check_03_hour_blackout(order)
            if not ok:
                return False, reason

            # 04: Account balance
            ok, reason = self._check_04_account_balance(order, account_balance)
            if not ok:
                return False, reason

            # 05: Absolute daily loss (raises)
            self._check_05_absolute_daily_loss(order, account_equity)

            # 06: Pre-trade daily loss budget
            ok, reason = self._check_06_pretrade_daily_budget(order, account_equity)
            if not ok:
                return False, reason

            # 07: Daily loss limit % (raises)
            self._check_07_daily_loss_pct(order, account_balance, daily_pnl)

            # 08: Daily profit target
            ok, reason = self._check_08_daily_profit_target(order, daily_pnl)
            if not ok:
                return False, reason

            # 09: Manual daily-loss cap
            ok, reason = self._check_09_manual_loss_cap(order)
            if not ok:
                return False, reason

            # 10: Drawdown limit (raises)
            self._check_10_drawdown(order, account_equity)

            # 11: Max daily trades
            ok, reason = self._check_11_max_daily_trades(order)
            if not ok:
                return False, reason

            # 12: Max open positions
            ok, reason = self._check_12_max_positions(order, current_positions)
            if not ok:
                return False, reason

            # 13: Position size > 0
            ok, reason = self._check_13_position_size(order)
            if not ok:
                return False, reason

            # 14: Symbol exposure cap
            ok, reason = self._check_14_exposure(order, current_positions, account_equity)
            if not ok:
                return False, reason

            # 15: Stop loss present
            ok, reason = self._check_15_stop_loss_present(order)
            if not ok:
                return False, reason

            # 16: Risk per trade
            ok, reason = self._check_16_risk_per_trade(order, account_balance)
            if not ok:
                return False, reason

            # All passed
            self.logger.info(
                "Order validated successfully",
                order_id=str(order.order_id),
                symbol=order.symbol.ticker if order.symbol else None,
                side=order.side.value if order.side else None,
                quantity=float(order.quantity)
            )
            return True, "OK"

        except (KillSwitchActiveError, DailyLossLimitError, DrawdownLimitError):
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

    # ──────────────────────────────────────────────────────────────────────
    # Per-check helpers. Numbered to match the original CHECK comments.
    # Critical checks (01, 05, 07, 10) raise on breach — they trigger the
    # kill switch and must propagate so the trading loop halts. Veto
    # checks return Tuple[bool, str] — False rejects the order quietly.
    # Each method holds the EXACT same code as the inlined version it
    # replaces; this refactor is structural only.
    # ──────────────────────────────────────────────────────────────────────

    def _check_01_kill_switch(self, order: Order) -> None:
        """Raises KillSwitchActiveError if the kill switch is active."""
        if self.kill_switch.is_active():
            reason = "Kill switch is active - all trading halted"
            self.logger.critical(
                "Order rejected by kill switch",
                order_id=str(order.order_id),
                symbol=order.symbol.ticker if order.symbol else None
            )
            raise KillSwitchActiveError(reason)

    def _check_02_circuit_breaker(self, order: Order) -> Tuple[bool, str]:
        """Reject if the consecutive-loss circuit breaker is active."""
        allowed, cb_reason = self.circuit_breaker.is_trading_allowed()
        if not allowed:
            self.logger.warning(
                "Order rejected by circuit breaker",
                reason=cb_reason,
                order_id=str(order.order_id)
            )
            return False, cb_reason
        return True, ""

    def _check_03_hour_blackout(self, order: Order) -> Tuple[bool, str]:
        """Reject if the current UTC hour is in the configured blackout list."""
        if self.trading_windows_enabled and self.blocked_hours_utc:
            now_hour = datetime.now(timezone.utc).hour
            if now_hour in self.blocked_hours_utc:
                reason = (
                    f"Hour blackout: {now_hour:02d}:00 UTC is in blocked "
                    f"window {sorted(self.blocked_hours_utc)}"
                )
                self.logger.warning(
                    "Order rejected - hour blackout",
                    hour_utc=now_hour,
                    blocked_hours=sorted(self.blocked_hours_utc),
                    order_id=str(order.order_id),
                    strategy=self._order_strategy_tag(order),
                )
                return False, reason
        return True, ""

    def _check_04_account_balance(self, order: Order, account_balance: Decimal) -> Tuple[bool, str]:
        """Reject if account balance is zero or negative."""
        if account_balance <= 0:
            reason = f"Insufficient account balance: ${account_balance}"
            self.logger.warning(
                "Order rejected - zero/negative balance",
                balance=float(account_balance),
                order_id=str(order.order_id)
            )
            return False, reason
        return True, ""

    def _check_05_absolute_daily_loss(self, order: Order, account_equity: Decimal) -> None:
        """Raises DrawdownLimitError if today's USD loss has hit the GFT cap."""
        if self.absolute_max_loss_usd > 0 and self.daily_start_equity > 0:
            daily_dollar_loss = self.daily_start_equity - account_equity
            if daily_dollar_loss >= self.absolute_max_loss_usd:
                reason = f"ABSOLUTE DAILY LOSS LIMIT BREACHED: ${daily_dollar_loss:.2f} >= ${self.absolute_max_loss_usd} (daily start: ${self.daily_start_equity})"
                self.logger.critical(
                    "ABSOLUTE DAILY DOLLAR LOSS LIMIT HIT - GFT PROTECTION",
                    daily_dollar_loss=float(daily_dollar_loss),
                    limit=float(self.absolute_max_loss_usd),
                    daily_start_equity=float(self.daily_start_equity),
                    current_equity=float(account_equity),
                    order_id=str(order.order_id)
                )
                self._trigger_kill_switch(reason)
                raise DrawdownLimitError(
                    reason,
                    drawdown=daily_dollar_loss / self.daily_start_equity,
                    limit=self.absolute_max_loss_usd / self.daily_start_equity
                )
            # Warn if approaching limit (80%)
            elif daily_dollar_loss >= self.absolute_max_loss_usd * Decimal("0.8"):
                self.logger.warning(
                    "Approaching ABSOLUTE DAILY LOSS LIMIT",
                    daily_dollar_loss=float(daily_dollar_loss),
                    limit=float(self.absolute_max_loss_usd),
                    pct_used=float(daily_dollar_loss / self.absolute_max_loss_usd * 100)
                )

    def _check_06_pretrade_daily_budget(self, order: Order, account_equity: Decimal) -> Tuple[bool, str]:
        """Reject if this trade's worst-case SL hit would push past the daily budget."""
        # Pre-trade daily loss BUDGET (proactive, not reactive).
        # The CHECK 5 reactive limit fires AFTER the breach — too late for GFT.
        # This proactive check estimates: "if this trade hits SL, will we breach?"
        # and rejects BEFORE the damage is done.
        if (self.absolute_max_loss_usd > 0
                and self.daily_start_equity > 0
                and order.price and order.stop_loss and order.symbol):
            daily_dollar_loss = self.daily_start_equity - account_equity
            # Worst-case loss from this specific trade hitting its stop loss
            sl_distance = abs(order.price - order.stop_loss)
            worst_case_trade_loss = (
                sl_distance * order.quantity * order.symbol.value_per_lot
            )
            projected_daily_loss = daily_dollar_loss + worst_case_trade_loss
            budget_limit = self.absolute_max_loss_usd * self.daily_loss_budget_safety_pct

            if projected_daily_loss >= budget_limit:
                reason = (
                    f"DAILY LOSS BUDGET EXHAUSTED: current loss ${daily_dollar_loss:.2f} "
                    f"+ worst-case ${worst_case_trade_loss:.2f} = ${projected_daily_loss:.2f} "
                    f">= budget ${budget_limit:.2f} (85% of ${self.absolute_max_loss_usd})"
                )
                self.logger.warning(
                    "Order rejected — pre-trade daily loss budget exceeded",
                    daily_loss_so_far=float(daily_dollar_loss),
                    worst_case_trade=float(worst_case_trade_loss),
                    projected_total=float(projected_daily_loss),
                    budget_limit=float(budget_limit),
                    order_id=str(order.order_id)
                )
                return False, reason
        return True, ""

    def _check_07_daily_loss_pct(self, order: Order, account_balance: Decimal, daily_pnl: Decimal) -> None:
        """Raises DailyLossLimitError if daily-loss % of balance is breached."""
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

        # Warn if approaching limit (80%)
        if daily_loss >= max_daily_loss * Decimal("0.8"):
            self.logger.warning(
                "Daily loss approaching limit",
                daily_loss=float(daily_loss),
                limit=float(max_daily_loss),
                pct_used=float(daily_loss / max_daily_loss)
            )

    def _check_08_daily_profit_target(self, order: Order, daily_pnl: Decimal) -> Tuple[bool, str]:
        """Reject if today's profit target has been hit (stop trading for the day)."""
        if self.max_daily_profit_usd > 0 and daily_pnl >= self.max_daily_profit_usd:
            reason = f"🎯 Daily profit target reached: ${daily_pnl} >= ${self.max_daily_profit_usd}. Stopping for today."
            self.logger.info(
                "DAILY PROFIT TARGET REACHED",
                daily_pnl=float(daily_pnl),
                target=float(self.max_daily_profit_usd),
                order_id=str(order.order_id)
            )
            return False, reason
        return True, ""

    def _check_09_manual_loss_cap(self, order: Order) -> Tuple[bool, str]:
        """Reject manual-tagged orders if today's manual loss cap is breached."""
        # Only applies to orders tagged as 'manual' in metadata.strategy.
        # Automated orders are unaffected. Reason: the audit showed 89% of
        # losses came from manual trades; this caps that bleed per UTC day.
        if (self.manual_guard_enabled
                and self.manual_daily_loss_cap_usd > 0
                and self._is_manual_order(order)):
            if self._manual_daily_loss_usd >= self.manual_daily_loss_cap_usd:
                reason = (
                    f"Manual daily loss cap breached: "
                    f"${self._manual_daily_loss_usd:.2f} >= "
                    f"${self.manual_daily_loss_cap_usd:.2f}. "
                    f"No more manual orders today."
                )
                self.logger.warning(
                    "Order rejected - manual loss cap",
                    manual_loss_today=float(self._manual_daily_loss_usd),
                    cap=float(self.manual_daily_loss_cap_usd),
                    order_id=str(order.order_id),
                )
                return False, reason
        return True, ""

    def _check_10_drawdown(self, order: Order, account_equity: Decimal) -> None:
        """Raises DrawdownLimitError if equity drawdown from HWM hits the cap."""
        current_drawdown = self.drawdown_tracker.calculate_drawdown(
            equity_high_water_mark=self.equity_high_water_mark,
            current_equity=account_equity
        )

        # Drawdown is the prop-firm hard line — no config bypass exists.
        # If hit, trigger kill switch and raise; recovery requires manual
        # intervention (clear data/state/kill_switch_alert.json).
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

    def _check_11_max_daily_trades(self, order: Order) -> Tuple[bool, str]:
        """Reject if today's trade count has hit the configured max."""
        if self.daily_trades_count >= self.max_daily_trades:
            reason = f"Max daily trades reached: {self.daily_trades_count} >= {self.max_daily_trades}"
            self.logger.warning(
                "Order rejected - max daily trades",
                count=self.daily_trades_count,
                limit=self.max_daily_trades
            )
            return False, reason
        return True, ""

    def _check_12_max_positions(self, order: Order, current_positions: Dict[str, Position]) -> Tuple[bool, str]:
        """Reject if open position count has hit the configured max."""
        if len(current_positions) >= self.max_positions:
            reason = f"Max positions reached: {len(current_positions)} >= {self.max_positions}"
            self.logger.warning(
                "Order rejected - max positions",
                count=len(current_positions),
                limit=self.max_positions
            )
            return False, reason
        return True, ""

    def _check_13_position_size(self, order: Order) -> Tuple[bool, str]:
        """Reject if order quantity is zero or negative."""
        if order.quantity <= 0:
            reason = f"Invalid position size: {order.quantity}"
            self.logger.error("Invalid position size", quantity=float(order.quantity))
            return False, reason
        return True, ""

    def _check_14_exposure(
        self,
        order: Order,
        current_positions: Dict[str, Position],
        account_equity: Decimal,
    ) -> Tuple[bool, str]:
        """Reject if symbol exposure would exceed the per-symbol cap."""
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
        return True, ""

    def _check_15_stop_loss_present(self, order: Order) -> Tuple[bool, str]:
        """Reject if no stop loss is set on the order."""
        if not order.stop_loss:
            reason = "Stop loss is required for all orders"
            self.logger.error(
                "Order rejected - no stop loss",
                order_id=str(order.order_id)
            )
            return False, reason
        return True, ""

    def _check_16_risk_per_trade(self, order: Order, account_balance: Decimal) -> Tuple[bool, str]:
        """Reject if the order's worst-case loss exceeds the per-trade risk cap.

        Honors the user's `risk_per_trade_usd` from runtime_setup verbatim
        (no tolerance). Min-lot orders are allowed through with a warning so
        a risk_pct that's too tight for the smallest possible trade doesn't
        starve the system.
        """
        # Knuth fix: must use value_per_lot to get actual dollar risk,
        # consistent with CHECK 06's worst-case SL calculation.
        if order.price and order.stop_loss:
            sl_distance = abs(order.price - order.stop_loss)
            value_per_lot = order.symbol.value_per_lot if order.symbol else Decimal("1")
            risk_amount = sl_distance * order.quantity * value_per_lot

            # Per-strategy override: strategies in risk.strategy_risk_overrides
            # use their own cap so a wider-SL strategy (e.g. kalman_regime)
            # isn't clipped by the operator's global runtime_setup cap.
            strat = (order.metadata or {}).get('strategy') if order.metadata else None
            override = self._strategy_risk_overrides.get(strat) if strat else None
            if override:
                ov_usd = Decimal(str(override.get('risk_per_trade_usd', 0) or 0))
                if ov_usd > 0:
                    max_risk = ov_usd
                else:
                    ov_pct = Decimal(str(override.get('risk_per_trade_pct', 0) or 0))
                    max_risk = account_balance * ov_pct if ov_pct > 0 else account_balance * self.risk_per_trade_pct
            else:
                # Prefer the user's absolute USD cap (runtime_setup) over the
                # percentage — that value is the explicit "max loss per trade"
                # the operator entered at startup and is enforced exactly.
                risk_usd_cap = Decimal(str(self.config.get('risk', {}).get('risk_per_trade_usd', 0) or 0))
                if risk_usd_cap > 0:
                    max_risk = risk_usd_cap
                else:
                    max_risk = account_balance * self.risk_per_trade_pct

            if risk_amount > max_risk:
                # Allow min_lot orders through — the minimum possible trade shouldn't be blocked
                if order.symbol and order.quantity <= order.symbol.min_lot:
                    self.logger.warning(
                        "Risk per trade exceeded but allowing min_lot",
                        risk_amount=float(risk_amount),
                        max_risk=float(max_risk),
                        quantity=float(order.quantity),
                        symbol=order.symbol.ticker
                    )
                else:
                    reason = f"Risk per trade exceeded: ${risk_amount} > ${max_risk}"
                    self.logger.warning(
                        "Order rejected - risk per trade",
                        risk_amount=float(risk_amount),
                        max_risk=float(max_risk)
                    )
                    return False, reason
        return True, ""
    
    def calculate_position_size(
        self,
        symbol: Symbol,
        account_balance: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        side: OrderSide,
        current_positions: Optional[Dict[str, Position]] = None,
        account_equity: Optional[Decimal] = None,
        signal_strength: float = None,
        strategy_name: Optional[str] = None,
    ) -> Decimal:
        """
        Calculate optimal position size.

        If position_sizing.method == 'fixed_lot', returns the fixed lot
        directly (no fractional math) — this is the $5K prop firm mode.
        Otherwise falls back to fixed_fractional (risk % of balance).
        """
        sizing_cfg = self.config.get('risk', {}).get('position_sizing', {})

        # ── User-fixed lot from runtime_setup.py: AUTHORITATIVE ───────────
        # When the operator typed a lot size at startup, runtime_setup writes
        # symbols.<TKR>.{min_lot,max_lot} == user_lot. That value is law —
        # honor it verbatim. No silent scaling, no exposure-cap substitution.
        # If a downstream check disagrees with the user's size it must REJECT
        # the order, not quietly resize it.
        if (symbol is not None
                and symbol.min_lot > 0
                and symbol.min_lot == symbol.max_lot):
            user_lot = symbol.min_lot
            self.logger.info(
                "Using user-fixed lot from runtime_setup",
                lots=float(user_lot),
                symbol=symbol.ticker,
            )
            # Manual guard still applies — that scaling is itself an
            # operator-configured policy, not a hidden override.
            return self._apply_manual_size_multiplier(user_lot, symbol, strategy_name)

        # ── Fixed lot mode (config method == 'fixed_lot') ─────────────────
        if sizing_cfg.get('method') == 'fixed_lot':
            fixed = Decimal(str(sizing_cfg.get('fixed_lot', '0.01')))
            # Clamp the fixed lot strictly to the symbol boundaries so users can securely override
            # global position sizes via config `max_lot` for expensive instruments like crypto
            fixed = max(symbol.min_lot, min(symbol.max_lot, fixed))

            self.logger.debug(
                "Using fixed_lot sizing with boundaries",
                lots=float(fixed),
                symbol=symbol.ticker
            )
            # Manual guard applies here too — halve even fixed-lot manual orders.
            fixed = self._apply_manual_size_multiplier(fixed, symbol, strategy_name)
            return fixed  # FIXED LOT: Bypass exposure cap completely

        # ── Fixed fractional (default): size by % risk ────────────────────
        # Per-strategy risk override: strategies in risk.strategy_risk_overrides
        # size off their own risk budget. Resolution order: explicit USD cap
        # (turned into pct of balance), then explicit pct, else global pct.
        effective_risk_pct = self.risk_per_trade_pct
        if strategy_name and strategy_name in self._strategy_risk_overrides:
            ov = self._strategy_risk_overrides[strategy_name]
            ov_usd = Decimal(str(ov.get('risk_per_trade_usd', 0) or 0))
            if ov_usd > 0 and account_balance > 0:
                effective_risk_pct = ov_usd / account_balance
            else:
                ov_pct = Decimal(str(ov.get('risk_per_trade_pct', 0) or 0))
                if ov_pct > 0:
                    effective_risk_pct = ov_pct

        # 1. Calculate risk-based size (Stop Loss distance)
        risk_size = self.position_sizer.calculate_position_size(
            symbol=symbol,
            account_balance=account_balance,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_pct=effective_risk_pct,
            signal_strength=signal_strength,
        )

        # 2. Apply exposure limit if context provided.
        # Cap to the exposure manager's actual computed limit — never to a
        # hardcoded constant. The previous code substituted 0.1 lots silently,
        # which violated user-input lot sizes from runtime_setup.
        if current_positions is not None and account_equity is not None:
            max_exposure_size = self.exposure_manager.get_max_position_size(
                symbol=symbol,
                current_positions=current_positions,
                account_equity=account_equity,
                entry_price=entry_price
            )

            if risk_size > max_exposure_size:
                self.logger.warning(
                    "Position size capped by exposure limit",
                    risk_size=float(risk_size),
                    exposure_limit=float(max_exposure_size),
                    symbol=symbol.ticker,
                )
                risk_size = max_exposure_size

        # Manual-guard sizing: halve the final size for manual-tagged orders.
        # Applied last so it composes with fixed_lot, risk_pct, and exposure caps.
        risk_size = self._apply_manual_size_multiplier(risk_size, symbol, strategy_name)
        return risk_size

    
    def record_trade_result(self, pnl: Decimal, strategy_name: Optional[str] = None) -> None:
        """
        Record trade result for circuit breaker + manual-guard tracking.

        Args:
            pnl: Realized P&L from trade
            strategy_name: Strategy that originated the trade. Used to bump the
                manual daily-loss counter when the trade was manual-tagged.
        """
        self.circuit_breaker.record_trade(pnl)

        # Carmack: mutations should be visible. This one bumps a counter that
        # later gates new manual orders — tag the log so it's greppable.
        if self._is_manual_strategy_tag(strategy_name) and pnl < 0:
            self._manual_daily_loss_usd += abs(pnl)
            self.logger.info(
                "Manual loss tallied",
                pnl=float(pnl),
                manual_loss_today=float(self._manual_daily_loss_usd),
                cap=float(self.manual_daily_loss_cap_usd),
            )

        if pnl < 0:
            self.logger.info(
                "Trade loss recorded",
                pnl=float(pnl),
                strategy=strategy_name,
                consecutive_losses=self.circuit_breaker.consecutive_losses
            )
        else:
            self.logger.info(
                "Trade win recorded",
                pnl=float(pnl),
                strategy=strategy_name,
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
        self._manual_daily_loss_usd = Decimal("0")

        self.logger.info(
            "Daily metrics reset",
            starting_equity=float(starting_equity),
            daily_trades_count=0,
            manual_daily_loss_reset=True,
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

    # ── Pure helpers (Carmack: pure functions, no hidden state) ────────────
    # Grouped at the bottom so the main flow in validate_order() stays readable.

    # Strict: only trades whose strategy was explicitly tagged "manual" (or
    # a known manual-policy variant) get the manual-guard treatment. With
    # journal stamping fixed, "unknown" no longer leaks here — automated
    # trades whose tag was lost by an upstream bug should be investigated,
    # not silently throttled as if they were manual.
    _MANUAL_STRATEGY_TAGS = frozenset({"manual", "manual_gut", "manual_rules"})

    @classmethod
    def _is_manual_strategy_tag(cls, tag: Optional[str]) -> bool:
        """True if the given strategy tag should be treated as a manual trade.

        Pure: depends only on the input string. No I/O, no instance state.
        """
        if tag is None:
            return False
        return tag.strip().lower() in cls._MANUAL_STRATEGY_TAGS

    @classmethod
    def _order_strategy_tag(cls, order: Order) -> str:
        """Extract the strategy tag from an order's metadata, or ''.

        Pure: read-only lookup. Never raises — returns '' on missing keys.
        """
        meta = getattr(order, "metadata", None) or {}
        return str(meta.get("strategy", "")).strip()

    @classmethod
    def _is_manual_order(cls, order: Order) -> bool:
        """True if the order's strategy metadata marks it manual."""
        return cls._is_manual_strategy_tag(cls._order_strategy_tag(order))

    def _apply_manual_size_multiplier(
        self,
        size: Decimal,
        symbol: Symbol,
        strategy_name: Optional[str],
    ) -> Decimal:
        """Halve (or scale by config) the lot size for manual-tagged orders.

        Clamped to symbol.min_lot so we never emit an invalid size. Returns
        size unchanged when manual_guard is disabled or order isn't manual.
        """
        if not self.manual_guard_enabled:
            return size
        if not self._is_manual_strategy_tag(strategy_name):
            return size
        scaled = (size * self.manual_size_multiplier).quantize(symbol.lot_step) \
            if hasattr(symbol, "lot_step") else size * self.manual_size_multiplier
        # Never drop below exchange minimum — reject would happen downstream anyway
        scaled = max(symbol.min_lot, scaled)
        self.logger.info(
            "Manual size multiplier applied",
            original=float(size),
            scaled=float(scaled),
            multiplier=float(self.manual_size_multiplier),
            symbol=symbol.ticker,
            strategy=strategy_name,
        )
        return scaled
