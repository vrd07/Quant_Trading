"""
Backtest Engine - Event-driven backtesting system.

Design Principles:
1. Use SAME strategy code as live trading
2. Event-driven: process bars chronologically
3. Realistic execution: simulate slippage, delays
4. Risk rules enforced: same risk engine as live
5. No lookahead bias: only use data available at decision time

Backtest Flow:
For each bar in history:
    1. Update data engine
    2. Call strategy.on_bar()
    3. Get signals
    4. Validate via risk engine
    5. Simulate order execution
    6. Update portfolio
    7. Track metrics
"""

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime, timezone
import pandas as pd
from dataclasses import dataclass

from ..core.types import Symbol, Signal, Order, Position
from ..core.constants import OrderSide, OrderStatus, PositionSide
from ..strategies.base_strategy import BaseStrategy
from ..risk.risk_engine import RiskEngine
from ..risk.risk_processor import RiskProcessor
from .simulation import SimulatedBroker
from .metrics import PerformanceMetrics
from .news_replay import NewsBlackoutReplay


@dataclass
class BacktestResult:
    """Results from backtest run."""
    total_return: float
    total_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    expectancy: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    equity_curve: pd.Series
    trades: List[Dict]
    daily_returns: pd.Series
    # backtest.md §1 gates G1/G2
    daily_win_rate: float = 0.0
    worst_day_r: float = 0.0
    trading_days: int = 0


class BacktestEngine:
    """
    Event-driven backtesting engine.
    
    Replays historical data and simulates trading.
    Uses the SAME strategy code as live trading for consistency.
    """
    
    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: Decimal,
        risk_config: Dict,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "realistic",
        bypass_risk_limits: bool = True,
        news_replay: Optional[NewsBlackoutReplay] = None,
    ):
        """
        Initialize backtest engine.

        Args:
            strategy: Strategy to test (same code as live)
            initial_capital: Starting capital
            risk_config: Risk engine configuration
            commission_per_trade: Commission per trade
            slippage_model: 'fixed', 'realistic', 'aggressive', or 'strict'
                ('strict' = backtest.md §3 — spread + 1.5× slippage + queue
                penalty for stops; the production gate.)
            news_replay: Optional NewsBlackoutReplay. When supplied, signals
                emitted inside a high-impact news window are dropped (matches
                live news_filter behavior) and the strict fill model widens
                the spread by 3× during the window. Open positions stay open.
        """
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.risk_config = risk_config
        self.news_replay = news_replay

        # Trailing stop config from risk section
        trailing_stop_config = risk_config.get('risk', {}).get('trailing_stop', {})

        # Simulated broker handles order execution
        # Wire the news_replay into the broker so the strict fill model gets
        # the 3× spread multiplier during high-impact windows. Closure pulls
        # the bar timestamp out of either a Series or a Bar dataclass.
        news_active_at = news_replay.is_active_at_bar if news_replay is not None else None
        self.broker = SimulatedBroker(
            initial_capital=initial_capital,
            commission_per_trade=commission_per_trade,
            slippage_model=slippage_model,
            trailing_stop_config=trailing_stop_config,
            news_active_at=news_active_at,
        )
        
        # Risk engine (same as live trading)
        self.risk_engine = RiskEngine(risk_config)
        self.risk_engine.equity_high_water_mark = initial_capital
        self.risk_engine.daily_start_equity = initial_capital

        # When True, skip kill-switch / circuit-breaker / daily-loss-limit checks.
        # This lets us evaluate the strategy's raw signal quality without protective
        # overrides permanently halting a backtest after early losses.
        self.bypass_risk_limits = bypass_risk_limits

        # Risk processor: computes SL/TP from signal metadata
        self.risk_processor = RiskProcessor(risk_config)
        
        # Performance tracking
        self.metrics = PerformanceMetrics()
        
        # State
        self.current_bar_index = 0
        self.bars_processed = 0
        self._current_day: Optional[datetime] = None
        
        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
    def run(
        self,
        bars: pd.DataFrame,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        min_history: int = 50,
        max_window: int = 1000
    ) -> BacktestResult:
        """
        Run backtest on historical data.
        
        Args:
            bars: Historical OHLCV data with columns:
                  ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            start_date: Start date filter (optional)
            end_date: End date filter (optional)
            min_history: Minimum bars needed before trading
        
        Returns:
            BacktestResult with performance metrics
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting Backtest")
        self.logger.info("=" * 60)
        self.logger.info(f"Strategy: {self.strategy.get_name()}")
        self.logger.info(f"Initial Capital: ${self.initial_capital}")
        self.logger.info(f"Total Bars Available: {len(bars)}")
        
        # Normalise: ensure the index is a DatetimeIndex so strategies that use
        # .resample() / .index.hour work correctly.  If timestamp is a column we
        # set it as the index; if it's already the index we just ensure it's datetime.
        bars = bars.copy()
        if 'timestamp' in bars.columns:
            bars['timestamp'] = pd.to_datetime(bars['timestamp'])
            bars = bars.set_index('timestamp')
        elif not isinstance(bars.index, pd.DatetimeIndex):
            bars.index = pd.to_datetime(bars.index)

        # Filter by date range — coerce CLI strings to match the bars' tz.
        idx_tz = getattr(bars.index, 'tz', None)
        def _coerce(d):
            ts = pd.to_datetime(d)
            if idx_tz is not None and ts.tzinfo is None:
                ts = ts.tz_localize(idx_tz)
            elif idx_tz is None and ts.tzinfo is not None:
                ts = ts.tz_convert(None)
            return ts
        if start_date:
            bars = bars[bars.index >= _coerce(start_date)]
        if end_date:
            bars = bars[bars.index <= _coerce(end_date)]

        self.logger.info(f"Bars After Date Filter: {len(bars)}")
        self.logger.info(f"Date Range: {bars.index[0]} to {bars.index[-1]}")
        
        # Reset state
        self.broker.reset()
        self.metrics.reset()
        self.bars_processed = 0
        self._current_day = None

        # Detect bar interval for time stop calculation
        if len(bars) >= 2:
            delta = (bars.index[1] - bars.index[0]).total_seconds() / 60.0
            self.broker._bar_interval_minutes = max(delta, 1.0)
        
        # Process each bar
        for i in range(len(bars)):
            self.current_bar_index = i

            # Get data available up to this bar (no lookahead).
            # Limit window to max_window bars to avoid O(n²) indicator recompute.
            # All strategies' maximum lookback fits well within 1000 bars.
            window_start = max(0, i + 1 - max_window)
            available_bars = bars.iloc[window_start:i+1]
            
            if len(available_bars) < min_history:
                continue  # Need minimum history for indicators
            
            # Check for new day (reset daily metrics)
            current_bar = available_bars.iloc[-1]
            # Index is now DatetimeIndex; use .name to get the timestamp of the last bar
            bar_date = pd.to_datetime(current_bar.name).date()
            
            if self._current_day is None:
                self._current_day = bar_date
            elif bar_date != self._current_day:
                self._on_new_day(bar_date)
                self._current_day = bar_date
            
            # Process bar
            self._process_bar(available_bars)
            
            self.bars_processed += 1
            
            # Log progress
            if self.bars_processed % 500 == 0:
                equity = float(self.broker.get_equity())
                pnl = equity - float(self.initial_capital)
                self.logger.info(
                    f"Progress: {self.bars_processed}/{len(bars)} bars "
                    f"({self.bars_processed/len(bars)*100:.1f}%) | "
                    f"Equity: ${equity:,.2f} | P&L: ${pnl:,.2f}"
                )
        
        # Close any remaining open positions at final price
        self._close_all_positions(bars.iloc[-1])
        
        # Generate results
        result = self._generate_results()
        
        self.logger.info("=" * 60)
        self.logger.info("Backtest Complete")
        self.logger.info("=" * 60)
        self.logger.info(f"Total Return: ${result.total_return:,.2f} ({result.total_return_pct:,.2f}%)")
        self.logger.info(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
        self.logger.info(f"Sortino Ratio: {result.sortino_ratio:.2f}")
        self.logger.info(f"Max Drawdown: ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")
        self.logger.info(f"Win Rate: {result.win_rate:.2f}%")
        self.logger.info(f"Profit Factor: {result.profit_factor:.2f}")
        self.logger.info(f"Total Trades: {result.total_trades}")
        
        return result
    
    def _on_new_day(self, new_date: datetime) -> None:
        """
        Handle transition to new trading day.

        In enforced-risk mode, reset kill switch and circuit breaker at each
        new trading day.  This simulates a prop firm's daily restart — in live
        trading you would manually reset after reviewing the breach, so the
        backtest mirrors that workflow instead of permanently halting after
        the first bad day.

        Args:
            new_date: New trading day
        """
        self.broker.reset_daily()
        self.risk_engine.reset_daily_metrics(self.broker.get_equity())

        # Reset protective mechanisms on each new day so the backtest can
        # continue and produce per-day survival statistics.
        if not self.bypass_risk_limits:
            self.risk_engine.kill_switch.reset()
            self.risk_engine.circuit_breaker.reset()

        self.logger.debug(f"New trading day: {new_date}")
    
    def _process_bar(self, available_bars: pd.DataFrame) -> None:
        """
        Process single bar through the trading pipeline.
        
        Args:
            available_bars: All bars available up to current time
        """
        try:
            current_bar = available_bars.iloc[-1]
            
            # 1. Update existing positions with current bar price
            self.broker.update_positions(current_bar)
            
            # 2. Check stop loss / take profit on existing positions
            # Note: SimulatedBroker handles closes internally
            self.broker.check_exits(current_bar)
            
            # 3. Generate signal from strategy
            signal = self.strategy.on_bar(available_bars)

            if signal is None:
                # No signal, just track equity
                self.metrics.update_equity(
                    timestamp=current_bar.name,
                    equity=float(self.broker.get_equity())
                )
                return

            # 3b. News-blackout replay (backtest.md §3.4): drop new signals
            # inside high-impact news windows. Open positions stay open —
            # this mirrors live news_filter behavior exactly. The bar's
            # spread is already widened 3× by the strict fill model.
            if self.news_replay is not None and self.news_replay.is_active_at_bar(current_bar):
                self.metrics.update_equity(
                    timestamp=current_bar.name,
                    equity=float(self.broker.get_equity())
                )
                return
            
            # 4. Calculate SL/TP via risk processor (strategies emit signals without stops)
            if signal.entry_price and not signal.stop_loss:
                try:
                    signal = self.risk_processor.calculate_stops(signal)
                except Exception as e:
                    self.logger.debug(f"RiskProcessor.calculate_stops failed: {e}")

            # Validate signal has required fields after stop calculation
            if not signal.entry_price or not signal.stop_loss:
                self.logger.debug(
                    f"Signal missing entry_price or stop_loss",
                    signal_id=str(signal.signal_id)
                )
                return
            
            # 5. Calculate position size via risk engine
            current_positions = self.broker.get_positions()
            daily_pnl = self.broker.get_daily_pnl()
            
            position_size = self.risk_engine.calculate_position_size(
                symbol=signal.symbol,
                account_balance=self.broker.get_balance(),
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                side=signal.side
            )
            
            if position_size <= 0:
                self.logger.debug("Position size calculated as zero")
                return
            
            # 6. Create order
            order = Order(
                symbol=signal.symbol,
                side=signal.side,
                quantity=position_size,
                price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                status=OrderStatus.PENDING,
                metadata={
                    'signal_id': str(signal.signal_id),
                    'strategy': signal.strategy_name,
                    'regime': signal.regime.value if signal.regime else 'unknown',
                    'strength': signal.strength
                }
            )
            
            # 7. Validate via risk engine (same rules as live)
            # In bypass_risk_limits mode we skip kill-switch / circuit-breaker
            # checks so a single bad streak doesn't halt the entire backtest.
            if not self.bypass_risk_limits:
                try:
                    is_valid, reason = self.risk_engine.validate_order(
                        order=order,
                        account_balance=self.broker.get_balance(),
                        account_equity=self.broker.get_equity(),
                        current_positions={str(p.position_id): p for p in current_positions},
                        daily_pnl=daily_pnl
                    )

                    if not is_valid:
                        self.logger.debug(f"Order rejected by risk engine: {reason}")
                        return

                except Exception as e:
                    # Risk engine exceptions (kill switch, etc.) - don't trade
                    self.logger.debug(f"Risk engine exception: {e}")
                    return
            
            # 8. Execute order via simulated broker
            fill_price = self.broker.execute_order(
                order=order,
                current_bar=current_bar
            )
            
            if fill_price:
                # Track trade entry. r_dollars is the per-trade R per
                # backtest.md §1: |entry - stop| × volume × value_per_lot.
                # Stamping it here lets G2 use real per-trade R instead of
                # the account-relative approximation.
                vpl = signal.symbol.value_per_lot if signal.symbol else Decimal("1")
                r_dollars = float(abs(fill_price - signal.stop_loss) * position_size * vpl)
                trade_idx = len(self.metrics.trades)
                self.metrics.add_trade({
                    'trade_idx': trade_idx,
                    'timestamp': str(current_bar.name),
                    'symbol': signal.symbol.ticker if signal.symbol else 'unknown',
                    'side': signal.side.value if signal.side else 'unknown',
                    'entry_price': float(fill_price),
                    'quantity': float(position_size),
                    'stop_loss': float(signal.stop_loss) if signal.stop_loss else None,
                    'take_profit': float(signal.take_profit) if signal.take_profit else None,
                    'strategy': signal.strategy_name,
                    'strength': signal.strength,
                    'r_dollars': r_dollars,
                    'pnl': 0  # Will be updated when closed
                })
                
                self.logger.debug(
                    f"Trade opened: {signal.side.value if signal.side else '?'} "
                    f"{signal.symbol.ticker if signal.symbol else '?'} @ {fill_price}"
                )
            
            # 9. Update equity high water mark
            self.risk_engine.update_equity_hwm(self.broker.get_equity())
            
            # 10. Track equity
            self.metrics.update_equity(
                timestamp=current_bar.name,
                equity=float(self.broker.get_equity())
            )
            
        except Exception as e:
            self.logger.error(
                f"Error processing bar {self.current_bar_index}",
                error=str(e),
                exc_info=True
            )
    
    def _close_all_positions(self, final_bar: pd.Series) -> None:
        """
        Close all remaining positions at end of backtest.
        
        Args:
            final_bar: Last bar in backtest
        """
        final_price = Decimal(str(final_bar['close'] if 'close' in final_bar.index else 0))
        positions = list(self.broker.positions.items())
        
        for pos_id, position in positions:
            # Calculate P&L
            if position.side == PositionSide.LONG:
                pnl = (final_price - position.entry_price) * position.quantity
                if position.symbol:
                    pnl *= position.symbol.value_per_lot
            else:
                pnl = (position.entry_price - final_price) * position.quantity
                if position.symbol:
                    pnl *= position.symbol.value_per_lot
            
            # Per-symbol round-trip commission (in addition to flat per-trade)
            symbol_commission = Decimal("0")
            if position.symbol and position.symbol.commission_per_lot > 0:
                symbol_commission = position.symbol.commission_per_lot * position.quantity * Decimal("2")
            total_commission = self.broker.commission_per_trade + symbol_commission

            # Record closed trade
            self.broker.closed_trades.append({
                'position_id': str(pos_id),
                'symbol': position.symbol.ticker if position.symbol else 'unknown',
                'side': position.side.value,
                'entry_price': float(position.entry_price),
                'exit_price': float(final_price),
                'quantity': float(position.quantity),
                'pnl': float(pnl),
                'commission': float(total_commission),
                'net_pnl': float(pnl - total_commission),
                'exit_reason': 'backtest_end',
                'exit_time': str(final_bar.name),
                'strategy': position.metadata.get('strategy', 'unknown')
            })

            # Update balance
            self.broker.balance += pnl - total_commission
            
            # Remove position
            del self.broker.positions[pos_id]
        
        if positions:
            self.logger.info(f"Closed {len(positions)} remaining positions at backtest end")
    
    def _generate_results(self) -> BacktestResult:
        """Generate comprehensive backtest results."""
        # Get closed trades from broker (includes P&L)
        closed_trades = self.broker.get_closed_trades()
        
        # Match trades with metrics trades and update P&L
        for trade in closed_trades:
            trade_pnl = trade.get('net_pnl', trade.get('pnl', 0))
            # Update corresponding trade in metrics
            for metric_trade in self.metrics.trades:
                if (metric_trade.get('symbol') == trade.get('symbol') and 
                    abs(metric_trade.get('entry_price', 0) - trade.get('entry_price', 0)) < 0.01):
                    metric_trade['pnl'] = trade_pnl
                    metric_trade['exit_price'] = trade.get('exit_price')
                    metric_trade['exit_reason'] = trade.get('exit_reason')
                    break
        
        # Get equity curve
        equity_curve = self.metrics.get_equity_curve()
        
        # Calculate returns
        initial_equity = float(self.initial_capital)
        final_equity = float(self.broker.get_equity())
        total_return = final_equity - initial_equity
        total_return_pct = (total_return / initial_equity) * 100
        
        # Calculate risk metrics
        if len(equity_curve) > 1:
            returns = equity_curve.pct_change().dropna()
            sharpe = self.metrics.calculate_sharpe_ratio(returns)
            sortino = self.metrics.calculate_sortino_ratio(returns)
            max_dd, max_dd_pct = self.metrics.calculate_max_drawdown(equity_curve)
            daily_returns = equity_curve.resample('D').last().pct_change().dropna()
        else:
            returns = pd.Series(dtype=float)
            sharpe = 0.0
            sortino = 0.0
            max_dd, max_dd_pct = 0.0, 0.0
            daily_returns = pd.Series(dtype=float)
        
        # Trade statistics
        trades = self.metrics.get_trades()
        winning_trades = [t for t in trades if t.get('pnl', 0) > 0]
        losing_trades = [t for t in trades if t.get('pnl', 0) < 0]
        
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        
        avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        gross_profit = sum(t['pnl'] for t in winning_trades)
        gross_loss = abs(sum(t['pnl'] for t in losing_trades))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
        
        largest_win = max((t['pnl'] for t in winning_trades), default=0)
        largest_loss = min((t['pnl'] for t in losing_trades), default=0)

        # G1/G2 daily metrics (backtest.md §1).
        # R = risk_per_trade_pct * initial_capital, account-relative.
        # Per-trade R from stop_loss is a TODO (needs value_per_lot on each trade).
        risk_pct = 0.01  # default fallback
        if isinstance(self.risk_config, dict):
            r = self.risk_config.get("risk", self.risk_config)
            if isinstance(r, dict):
                risk_pct = float(r.get("risk_per_trade_pct", 0.01))
        r_dollars = float(self.initial_capital) * risk_pct
        daily_win_rate = PerformanceMetrics.calculate_daily_win_rate(trades)
        worst_day_r = PerformanceMetrics.calculate_worst_day_r(trades, r_dollars)
        trading_days = PerformanceMetrics.calculate_trading_days(trades)

        return BacktestResult(
            total_return=float(total_return),
            total_return_pct=float(total_return_pct),
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown=float(max_dd),
            max_drawdown_pct=float(max_dd_pct),
            win_rate=float(win_rate),
            profit_factor=float(profit_factor),
            expectancy=float(expectancy),
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            avg_win=float(avg_win),
            avg_loss=float(avg_loss),
            largest_win=float(largest_win),
            largest_loss=float(largest_loss),
            equity_curve=equity_curve,
            trades=trades,
            daily_returns=daily_returns,
            daily_win_rate=float(daily_win_rate),
            worst_day_r=float(worst_day_r),
            trading_days=int(trading_days),
        )
    
    def get_strategy(self) -> BaseStrategy:
        """Get the strategy being tested."""
        return self.strategy
    
    def get_broker(self) -> SimulatedBroker:
        """Get the simulated broker."""
        return self.broker
    
    def get_metrics(self) -> PerformanceMetrics:
        """Get the performance metrics tracker."""
        return self.metrics
