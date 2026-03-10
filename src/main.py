"""
Main Trading System - Central orchestrator.

This is the heart of the trading system that connects all modules.

Main Loop:
1. Initialize all components
2. Restore state from crash (if any)
3. Reconcile with MT5
4. Enter main trading loop:
   - Get ticks from MT5
   - Update data engine
   - Generate signals from strategies
   - Validate signals via risk engine
   - Execute approved orders
   - Update portfolio
   - Save state periodically
5. Handle shutdown gracefully

Critical Design:
- All exceptions are caught and logged
- State saved before shutdown
- Kill switch checked every iteration
- Heartbeat monitored
- Graceful degradation (if MT5 disconnects, try to reconnect)
"""

import sys
import time
import signal
from pathlib import Path
from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.connectors.mt5_connector import MT5Connector
from src.data.data_engine import DataEngine
from src.strategies.strategy_manager import StrategyManager
from src.risk.risk_engine import RiskEngine
from src.execution.execution_engine import ExecutionEngine
from src.portfolio.portfolio_engine import PortfolioEngine
from src.state.state_manager import StateManager
from src.core.types import Symbol
from src.core.exceptions import (
    KillSwitchActiveError,
    DailyLossLimitError,
    DrawdownLimitError,
    ConnectionLostError
)
from src.monitoring.logger import get_logger
from src.monitoring.trade_journal import TradeJournal
from src.monitoring.performance_dashboard import PerformanceDashboard
from src.data.news_filter import load_ff_events, is_news_blackout
from src.risk.trailing_stop_manager import TrailingStopManager


class TradingSystem:
    """
    Main trading system orchestrator.
    
    Coordinates all modules and manages the trading loop.
    """
    
    def __init__(self, config_file: str = "config/config.yaml"):
        """
        Initialize trading system.
        
        Args:
            config_file: Path to configuration file
        """
        # Load configuration
        with open(config_file, 'r') as f:
            self.config = yaml.safe_load(f)
        
        # Environment-specific paths
        self.env = self.config.get('environment', 'dev')
        
        # Logging
        from src.monitoring.logger import setup_logger
        log_file = f"data/logs/trading_system_{self.env}.log"
        setup_logger(log_file=log_file, level=self.config.get('monitoring', {}).get('log_level', 'INFO'))
        self.logger = get_logger(__name__)
        
        # Components (initialized in setup)
        self.connector: Optional[MT5Connector] = None
        self.data_engine: Optional[DataEngine] = None
        self.strategy_manager: Optional[StrategyManager] = None
        self.risk_engine: Optional[RiskEngine] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.portfolio_engine: Optional[PortfolioEngine] = None
        self.state_manager: Optional[StateManager] = None
        
        # Monitoring
        self.trade_journal: Optional[TradeJournal] = None
        self.dashboard: Optional[PerformanceDashboard] = None
        
        # State
        self.running = False
        self.last_state_save = datetime.now(timezone.utc)
        # Initialize to min time to force immediate reconciliation on startup
        self.last_reconciliation = datetime.min.replace(tzinfo=timezone.utc)
        self.loop_iteration = 0
        
        # Track last processed bar timestamps to prevent signal spam
        self._last_processed_bars: Dict[str, datetime] = {}
        
        # The5ers: directional lock + 5-min reversal buffer state
        self._last_close_time: Dict[str, datetime] = {}  # 'BUY' or 'SELL' → close timestamp
        self._reversal_buffer_min: int = 5
        
        # ── NEW: Daily profit target ──────────────────────────────────────
        # Stop emitting new signals once max_daily_profit achieved.
        self._daily_wins_date: Optional[str] = None   # reset at midnight
        self._max_daily_profit: float = 120.0  # overridden from config in setup()
        
        # ── NEW: 2-loss consecutive pause ────────────────────────────────
        # After _loss_pause_threshold consecutive losses, suppress signals
        # for _loss_pause_duration seconds.
        self._consecutive_losses_today: int = 0
        self._loss_pause_threshold: int = 2   # overridden from config
        self._loss_pause_duration: int = 1800  # 30 min default
        self._loss_pause_until: Optional[datetime] = None
        
        # ── NEW: Trailing stop manager ───────────────────────────────────
        self._trailing_stop_mgr: Optional[TrailingStopManager] = None
        
        # News filter events (loaded during setup if enabled)
        self._news_events_df = None
        self._news_filter_cfg = None
        
        # Shutdown handler
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def setup(self) -> bool:
        """
        Initialize all components.
        
        Returns:
            True if setup successful
        """
        try:
            self.logger.info("=" * 60)
            self.logger.info("Initializing Trading System")
            self.logger.info("=" * 60)
            
            # 1. Connect to MT5
            self.logger.info("1. Connecting to MT5...")
            self.connector = MT5Connector()
            self.connector.connect()
            self.connector._system_config = self.config   # pass full config for symbol creation
            self.logger.info("✓ Connected to MT5")
            
            # 2. Initialize symbols
            self.logger.info("2. Loading symbols...")
            symbols = self._load_symbols()
            self.logger.info(f"✓ Loaded {len(symbols)} symbols")
            
            # 3. Initialize data engine
            self.logger.info("3. Initializing data engine...")
            timeframes = self.config.get('data', {}).get('timeframes', ['1m', '5m', '15m', '1h'])
            self.data_engine = DataEngine(
                connector=self.connector,
                symbols=symbols,
                timeframes=timeframes
            )
            self.logger.info("✓ Data engine ready")
            
            # 3b. Preload historical bars from yfinance (eliminates 22+ min startup delay)
            self.logger.info("   Preloading historical bars...")
            try:
                preload_results = self.data_engine.preload_historical_bars(bars_count=200)
                for sym, count in preload_results.items():
                    self.logger.info(f"   ✓ {sym}: {count} bars preloaded")
            except Exception as e:
                self.logger.warning(f"   ⚠ Preload failed (will build from live ticks): {e}")
            
            # 4. Initialize risk engine
            self.logger.info("4. Initializing risk engine...")
            self.risk_engine = RiskEngine(self.config)
            self.logger.info("✓ Risk engine ready")
            
            # 5. Initialize execution engine
            self.logger.info("5. Initializing execution engine...")
            self.execution_engine = ExecutionEngine(
                connector=self.connector,
                risk_engine=self.risk_engine
            )
            self.logger.info("✓ Execution engine ready")
            
            # 6. Initialize trade journal (before portfolio so it can be passed)
            self.logger.info("6. Initializing trade journal...")
            self.trade_journal = TradeJournal()
            self.logger.info("✓ Trade journal ready")
            
            # 6b. Sync trade history from MT5
            self.logger.info("   Syncing trade history from MT5...")
            try:
                if str(PROJECT_ROOT / "scripts") not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
                from sync_mt5_history import get_history_deals, sync_journal
                deals = get_history_deals(self.connector, days=30)
                if deals:
                    sync_journal(deals)
                    self.logger.info("   ✓ Trade history synced")
            except Exception as e:
                self.logger.warning(f"   ⚠ Failed to sync trade history: {e}")
            
            # 7. Initialize portfolio engine
            self.logger.info("7. Initializing portfolio engine...")
            self.portfolio_engine = PortfolioEngine(
                connector=self.connector,
                trade_journal=self.trade_journal
            )
            self.logger.info("✓ Portfolio engine ready")
            
            # 8. Initialize state manager
            self.logger.info("8. Initializing state manager...")
            state_dir = f"data/state/{self.env}"
            self.state_manager = StateManager(state_dir=state_dir)
            self.logger.info(f"✓ State manager ready (env: {self.env})")
            
            # 7b. Initialize dashboard
            initial_capital = Decimal(str(self.config.get('account', {}).get('initial_balance', 10000)))
            self.dashboard = PerformanceDashboard(
                portfolio=self.portfolio_engine,
                journal=self.trade_journal,
                initial_capital=initial_capital,
                data_engine=self.data_engine
            )
            self.logger.info("✓ Dashboard ready")
            
            self.logger.info("8. Initializing strategies...")
            self.strategy_manager = StrategyManager(symbols, self.config)
            self.logger.info("✓ Strategies ready")
            
            # Read advanced session controls from config
            risk_cfg = self.config.get('risk', {})
            self._max_daily_profit = float(risk_cfg.get('max_daily_profit_usd', 120.0))
            cb_cfg = risk_cfg.get('circuit_breaker', {})
            self._loss_pause_threshold = cb_cfg.get('loss_pause_consecutive', 2)
            self._loss_pause_duration = cb_cfg.get('loss_pause_minutes', 30) * 60
            
            # Initialize trailing stop manager
            self._trailing_stop_mgr = TrailingStopManager(self.config)
            
            # 10. Load news filter events if enabled
            nf_cfg = self.config.get('trading_hours', {}).get('news_filter', {})
            
            if nf_cfg.get('enabled', False):
                # Dynamic daily CSV resolution:
                # 1. Try news/YYYY-MM-DD_news.csv (today's auto-fetched file)
                # 2. Try yesterday's dated file
                # 3. Fall back to csv_path in config
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
                
                candidates = [
                    f"news/{today_str}_news.csv",
                    f"news/{yesterday_str}_news.csv",
                    nf_cfg.get('csv_path', 'news/MAR_news.csv'),
                ]
                
                csv_path = None
                for candidate in candidates:
                    if Path(candidate).exists():
                        csv_path = candidate
                        break
                
                if csv_path is None:
                    self.logger.warning(
                        "No news CSV found — news filter disabled. "
                        "Run: python scripts/fetch_daily_news.py"
                    )
                    self._news_events_df = None
                else:
                    try:
                        self._news_events_df = load_ff_events(
                            csv_path=csv_path,
                            currency=nf_cfg.get('currency', 'USD'),
                            impacts=nf_cfg.get('impacts', ['high', 'red']),
                        )
                        self._news_filter_cfg = nf_cfg
                        self.logger.info(
                            f"✓ News filter loaded: {csv_path} "
                            f"({len(self._news_events_df)} events)"
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"News filter CSV failed to load ({csv_path}): {e} — filter disabled"
                        )
                        self._news_events_df = None

            
            # 9. Restore state from crash (if any)
            self.logger.info("9. Checking for previous state...")
            self._restore_state()
            self.logger.info("✓ State restored")
            
            self.logger.info("=" * 60)
            self.logger.info("✓ ALL SYSTEMS OPERATIONAL")
            self.logger.info("=" * 60)
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Setup failed",
                error=str(e),
                exc_info=True
            )
            return False
    
    def run(self) -> None:
        """
        Run main trading loop.
        
        Loop:
        1. Check kill switch
        2. Update data from MT5
        3. Process strategies
        4. Execute signals
        5. Update portfolio
        6. Save state periodically
        """
        if not self.setup():
            self.logger.error("Setup failed - cannot start trading")
            return
        
        self.running = True
        self.logger.info("Starting main trading loop...")
        
        while self.running:
            try:
                self.loop_iteration += 1
                
                # 1. Check kill switch
                if self.risk_engine.kill_switch.is_active():
                    self.logger.critical("Kill switch active - halting trading")
                    break
                
                # 1a. Check Weekend Holding (Prop Firm Requirement)
                now_utc = datetime.now(timezone.utc)
                is_weekend = False
                if now_utc.weekday() == 5: # Saturday
                    is_weekend = True
                elif now_utc.weekday() == 4 and now_utc.hour >= 21: # Friday evening (after 21:00 UTC)
                    is_weekend = True
                elif now_utc.weekday() == 6 and now_utc.hour < 21: # Sunday before 21:00 UTC
                    is_weekend = True
                    
                if is_weekend:
                    if self.loop_iteration % 60 == 1:
                        self.logger.info("Weekend closure active - waiting for Sunday open")
                    self._close_all_open_positions()
                    time.sleep(60) # Sleep longer during weekend
                    continue
                
                # 2. Update data from MT5
                self.data_engine.update_from_connector()
                
                # 3. Update portfolio positions with latest prices
                self._update_portfolio_prices()
                
                # 3b. Manage trailing stops (breakeven + lock)
                self._manage_trailing_stops()
                
                # 4. Process strategies for each symbol
                self._process_strategies()
                
                # 5. Process any fills from MT5
                self._process_fills()
                
                # 6. Save state periodically
                if self._should_save_state():
                    self._save_state()
                
                # 7. Reconcile with MT5 periodically
                if self._should_reconcile():
                    self._reconcile_portfolio()
                
                # 8. Log metrics periodically
                if self.loop_iteration % 60 == 0:
                    self._log_metrics()
                
                # 9. Display dashboard periodically (every 5 minutes)
                if self.loop_iteration % 300 == 0:
                    self._display_dashboard()
                
                # Sleep briefly (don't hammer CPU)
                time.sleep(1)
                
            except (KillSwitchActiveError, DailyLossLimitError, DrawdownLimitError) as e:
                # Critical risk violations - stop trading
                self.logger.critical(
                    "Critical risk violation - stopping",
                    error=str(e)
                )
                break
                
            except ConnectionLostError as e:
                # Connection issue - try to reconnect
                self.logger.error("Connection lost - attempting reconnect", error=str(e))
                if not self._reconnect():
                    break
                
            except Exception as e:
                # Unexpected error - log and continue
                self.logger.error(
                    "Error in main loop",
                    iteration=self.loop_iteration,
                    error=str(e),
                    exc_info=True
                )
                time.sleep(5)  # Pause before retrying
        
        # Shutdown
        self.shutdown()
    
    def _manage_trailing_stops(self) -> None:
        """
        Called every main-loop iteration.
        Moves SL to breakeven (at +1×ATR) and locks partial profit (at +1.5×ATR).
        """
        if self._trailing_stop_mgr is None or self.connector is None:
            return
        try:
            positions = self.connector.get_positions()
            self._trailing_stop_mgr.cleanup_closed(set(positions.keys()))
            self._trailing_stop_mgr.update(positions, self.connector)
        except Exception as e:
            self.logger.warning(f"Trailing stop update error (non-critical): {e}")

    def _process_strategies(self) -> None:
        """Process all strategies with per-strategy timeframe routing."""
        # Reset daily tracking at midnight UTC
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self._daily_wins_date != today_str:
            self._daily_wins_date = today_str
            self._consecutive_losses_today = 0
            self._loss_pause_until = None
            self.logger.info("[SessionManager] New trading day — counters reset")

            # ── Reset RiskEngine daily metrics (was never called before) ──────
            # This refreshes: daily_trades_count, daily_start_equity,
            # and gives the equity HWM a chance to update to today's starting equity.
            try:
                account_info = self._get_effective_account_info()
                self.risk_engine.reset_daily_metrics(account_info['equity'])
                self.risk_engine.update_equity_hwm(account_info['equity'])
                self.logger.info(
                    f"[RiskEngine] Daily metrics reset — equity={float(account_info['equity']):.2f}"
                )
            except Exception as _e:
                self.logger.warning(f"[RiskEngine] Daily reset failed (non-critical): {_e}")

        # ── Daily profit target gate ──────────────────────────────
        daily_pnl = float(self.portfolio_engine.daily_realized_pnl + self.portfolio_engine.get_total_unrealized_pnl())
        if self._max_daily_profit > 0 and daily_pnl >= self._max_daily_profit:
            if self.loop_iteration % 60 == 1:
                self.logger.info(
                    f"[SessionManager] Daily target reached — "
                    f"${daily_pnl:.2f} / ${self._max_daily_profit:.2f}. No new signals today."
                )
            return

        # ── Loss pause gate ───────────────────────────────────────
        if self._loss_pause_until and datetime.now(timezone.utc) < self._loss_pause_until:
            remaining = (self._loss_pause_until - datetime.now(timezone.utc)).seconds // 60
            if self.loop_iteration % 60 == 1:
                self.logger.info(
                    f"[SessionManager] Loss pause active — "
                    f"{self._consecutive_losses_today} consecutive losses. "
                    f"{remaining} min remaining."
                )
            return

        # Only process enabled symbols
        enabled_symbols = [
            ticker for ticker, cfg in self.config.get('symbols', {}).items()
            if cfg.get('enabled', False)
        ]
        
        # Get strategy config
        strategy_config = self.config.get('strategies', {})
        min_bars = strategy_config.get('min_bars_required', 10)
        global_primary_tf = strategy_config.get('primary_timeframe', '5m')

        # Per-strategy timeframe overrides
        strategy_timeframes = {
            'breakout':      strategy_config.get('breakout', {}).get('timeframe', global_primary_tf),
            'momentum':      strategy_config.get('momentum', {}).get('timeframe', global_primary_tf),
            'vwap':          strategy_config.get('vwap', {}).get('timeframe', global_primary_tf),
            'kalman_regime': strategy_config.get('kalman_regime', {}).get('timeframe', global_primary_tf),
            'mean_reversion':strategy_config.get('mean_reversion', {}).get('timeframe', global_primary_tf),
        }
        
        # News filter: skip all strategy processing during blackout
        if self._news_events_df is not None and self._news_filter_cfg:
            buffer_min = self._news_filter_cfg.get('buffer_min', 15)
            tz = self._news_filter_cfg.get('timezone', 'Asia/Kolkata')
            # Use UTC-aware datetime (consistent with rest of codebase)
            if is_news_blackout(datetime.now(timezone.utc), self._news_events_df,
                               buffer_min=buffer_min, timezone=tz):
                if self.loop_iteration % 60 == 1:
                    self.logger.info("News blackout active — skipping strategies")
                return

        # ── Session time enforcement ──────────────────────────────────────────
        # Reads sessions from trading_hours.sessions config.
        # If no sessions configured: runs 24/7.
        # If sessions configured but none active: suppresses all signals.
        sessions_cfg = self.config.get('trading_hours', {}).get('sessions', [])
        allowed_strategies: set = set()   # empty set = ALL strategies allowed
        in_any_session = not sessions_cfg  # if no config, always in-session

        if sessions_cfg:
            now_utc = datetime.now(timezone.utc)
            now_hhmm = now_utc.strftime('%H:%M')
            for session in sessions_cfg:
                if not session.get('enabled', True):
                    continue
                sstart = session.get('start', '00:00')
                send   = session.get('end',   '23:59')
                # Support sessions that cross midnight (e.g. 22:00-02:00)
                if sstart <= send:
                    active = sstart <= now_hhmm < send
                else:
                    active = now_hhmm >= sstart or now_hhmm < send
                if active:
                    in_any_session = True
                    session_strats = session.get('strategies', [])
                    if session_strats:
                        allowed_strategies.update(session_strats)
                    # else: empty = all allowed
                    if self.loop_iteration % 120 == 1:
                        self.logger.info(
                            f"[Session] Active: {session['name']} "
                            f"({sstart}-{send} UTC) strategies="
                            f"{session_strats if session_strats else 'ALL'}"
                        )
                    break  # first matching session wins

        if not in_any_session:
            if self.loop_iteration % 120 == 1:
                self.logger.info(
                    f"[Session] {datetime.now(timezone.utc).strftime('%H:%M')} UTC "
                    "— outside all session windows, waiting."
                )
            return

        for symbol_ticker in enabled_symbols:
            try:
                strategies_for_symbol = self.strategy_manager.strategies.get(symbol_ticker, {})

                # Collect signals from each strategy using its own timeframe
                all_signals = []
                for strategy_name, strategy in strategies_for_symbol.items():
                    # Session whitelist: skip if strategy not allowed in current session
                    if allowed_strategies and strategy_name not in allowed_strategies:
                        continue

                    tf = strategy_timeframes.get(strategy_name, global_primary_tf)
                    bars = self.data_engine.get_bars(symbol_ticker, tf)

                    if len(bars) < min_bars:
                        if self.loop_iteration % 60 == 1:
                            self.logger.info(
                                f"Waiting for data: {len(bars)}/{min_bars} "
                                f"{tf} bars for {symbol_ticker}/{strategy_name}"
                            )
                        continue

                    # Check if we already processed this exact bar for this strategy
                    bar_key = f"{symbol_ticker}_{strategy_name}"
                    latest_bar_time = bars.iloc[-1]['timestamp'] if 'timestamp' in bars.columns else bars.index[-1]
                    if self._last_processed_bars.get(bar_key) == latest_bar_time:
                        continue
                    self._last_processed_bars[bar_key] = latest_bar_time

                    try:
                        signal = strategy.on_bar(bars)
                        if signal:
                            all_signals.append((strategy_name, signal))
                    except Exception as se:
                        self.logger.error(
                            "Strategy error", strategy=strategy_name,
                            symbol=symbol_ticker, error=str(se), exc_info=True
                        )

                for _, signal in all_signals:
                    self._execute_signal(signal)
                    
            except Exception as e:
                self.logger.error(
                    "Error processing strategies",
                    symbol=symbol_ticker,
                    error=str(e)
                )
    

    def _get_effective_account_info(self) -> Dict[str, Decimal]:
        """
        Get account info with paper trading override if enabled.
        
        Returns:
            Dict with balance, equity, etc.
        """
        account_info = self.connector.get_account_info()
        
        # Override for paper trading
        if self.config.get('environment') == 'paper':
            initial_bal_cfg = Decimal(str(self.config.get('account', {}).get('initial_balance', 10000)))
            
            # Adjust equity based on PnL (Open Equity - Open Balance)
            current_pnl = account_info['equity'] - account_info['balance']
            
            account_info['balance'] = initial_bal_cfg
            account_info['equity'] = initial_bal_cfg + current_pnl
            
        return account_info

    def _execute_signal(self, signal) -> None:
        """Execute trading signal."""
        try:
            # ── Daily profit target gate ──────────────────────────────
            daily_pnl = float(self.portfolio_engine.daily_realized_pnl + self.portfolio_engine.get_total_unrealized_pnl())
            if self._max_daily_profit > 0 and daily_pnl >= self._max_daily_profit:
                self.logger.info(
                    f"[SessionManager] Daily target hit (${daily_pnl:.2f}) — signal suppressed",
                    strategy=signal.strategy_name,
                )
                return

            # Get current account state
            account_info = self._get_effective_account_info()
            
            # CRITICAL: Get positions directly from MT5 (live source of truth)
            # Portfolio engine's internal list can be stale between reconciliation cycles,
            # causing the one-direction check to miss existing positions
            try:
                mt5_positions = self.connector.get_positions()
            except Exception:
                # Fallback to portfolio engine if MT5 fetch fails
                mt5_positions = {str(p.position_id): p for p in self.portfolio_engine.get_all_positions()}
            
            daily_pnl = self.portfolio_engine.daily_realized_pnl + self.portfolio_engine.get_total_unrealized_pnl()
            
            # ── The5ers Rule: Directional Lock ────────────────────────────
            # No SELL allowed if a BUY is open; no BUY allowed if a SELL is open.
            from src.core.constants import OrderSide as _OrderSide, PositionSide as _PositionSide
            signal_side = signal.side  # OrderSide.BUY or OrderSide.SELL
            for pos in mt5_positions.values():
                pos_side = getattr(pos, 'side', None)
                if pos_side is None:
                    continue
                # Map position side to expected signal side
                is_long = pos_side == _PositionSide.LONG
                is_short = pos_side == _PositionSide.SHORT
                if (signal_side == _OrderSide.BUY and is_short) or \
                   (signal_side == _OrderSide.SELL and is_long):
                    self.logger.info(
                        f"[The5ers] Directional lock: {'SHORT' if is_short else 'LONG'} open, "
                        f"rejecting {signal_side.value} signal",
                        strategy=signal.strategy_name,
                        symbol=signal.symbol.ticker if signal.symbol else '?'
                    )
                    return
            
            # ── The5ers Rule: 5-Minute Reversal Buffer ────────────────────
            # After closing a position in one direction, block the opposite
            # direction for reversal_buffer_min minutes.
            opposite_side_key = (
                'SELL' if signal_side == _OrderSide.BUY else 'BUY'
            )
            last_opposite_close = self._last_close_time.get(opposite_side_key)
            if last_opposite_close is not None:
                elapsed_min = (datetime.now(timezone.utc) - last_opposite_close).total_seconds() / 60.0
                if elapsed_min < self._reversal_buffer_min:
                    self.logger.info(
                        f"[The5ers] Reversal buffer active: last {opposite_side_key} closed "
                        f"{elapsed_min:.1f}m ago (buffer={self._reversal_buffer_min}m), "
                        f"rejecting {signal_side.value}",
                        strategy=signal.strategy_name,
                        symbol=signal.symbol.ticker if signal.symbol else '?'
                    )
                    return
            
            # Submit signal to execution engine
            order = self.execution_engine.submit_signal(
                signal=signal,
                account_balance=account_info['balance'],
                account_equity=account_info['equity'],
                current_positions=mt5_positions,
                daily_pnl=daily_pnl
            )
            
            if order:
                self.logger.info(
                    "Signal executed",
                    strategy=signal.strategy_name,
                    symbol=signal.symbol.ticker,
                    order_id=str(order.order_id),
                    status=order.status.value
                )
            
        except Exception as e:
            self.logger.error(
                "Error executing signal",
                signal_id=str(signal.signal_id),
                error=str(e)
            )
    
    def _process_fills(self) -> None:
        """
        Detect recently closed positions from MT5 and update TradeJournal + RiskEngine.

        The file-bridge has no push notifications — fills are only discoverable
        by polling get_closed_positions(). This runs every loop tick (1s) and
        uses a local set to avoid double-counting deals already recorded.

        Why this matters: Without this, the system only discovers SL/TP hits
        during the 30-second reconciliation cycle. This creates a ~29-second
        window where the RiskEngine thinks a position is still open and could
        approve a conflicting signal.
        """
        # Lazy-init the set of already-processed deal tickets
        if not hasattr(self, '_processed_deal_tickets'):
            self._processed_deal_tickets: set = set()

        try:
            # Only scan the last 5 minutes of history (cheap — avoids large lookback)
            deals = self.connector.get_closed_positions(minutes=5)
            if not deals:
                return

            for deal in deals:
                ticket = str(deal.get('ticket') or deal.get('order') or deal.get('deal', ''))
                if not ticket or ticket in self._processed_deal_tickets:
                    continue  # Already handled

                # Mark as processed immediately to avoid double-counting on retries
                self._processed_deal_tickets.add(ticket)

                realized_pnl = float(deal.get('profit', 0))
                symbol_name = str(deal.get('symbol', ''))
                comment = str(deal.get('comment', ''))

                # Extract strategy name from comment format "strategy|orderId"
                strategy = 'unknown'
                if '|' in comment:
                    strategy = comment.split('|')[0]

                # Record in TradeJournal if available
                if self.trade_journal is not None:
                    try:
                        from decimal import Decimal as _Dec
                        self.trade_journal.record_trade(
                            strategy=strategy,
                            symbol=symbol_name,
                            side='UNKNOWN',           # deal history has no side
                            entry_price=_Dec(str(deal.get('price_open', 0))),
                            exit_price=_Dec(str(deal.get('price', deal.get('price_close', 0)))),
                            quantity=_Dec(str(deal.get('volume', 0))),
                            realized_pnl=_Dec(str(realized_pnl)),
                            entry_time=None,
                            exit_time=None,
                            metadata={'mt5_ticket': ticket, 'source': 'fill_poll'}
                        )
                    except Exception as _je:
                        self.logger.debug(f"Fill poll: journal record failed for ticket={ticket}: {_je}")

                # Update RiskEngine circuit breaker in real-time (no more 30s blind spot)
                if self.risk_engine is not None:
                    from decimal import Decimal as _Dec
                    self.risk_engine.record_trade_result(_Dec(str(realized_pnl)))

                self.logger.info(
                    f"[FillPoller] Detected closed deal: ticket={ticket} symbol={symbol_name} "
                    f"pnl={realized_pnl:.2f} strategy={strategy}"
                )

        except Exception as e:
            # Non-critical — reconciliation will catch anything missed
            self.logger.debug(f"[FillPoller] Error polling fills (non-critical): {e}")
    
    def _update_portfolio_prices(self) -> None:
        """Update all portfolio positions with latest prices."""
        try:
            ticks = {}
            
            # Only fetch ticks for enabled symbols
            enabled_symbols = [
                ticker for ticker, cfg in self.config.get('symbols', {}).items()
                if cfg.get('enabled', False)
            ]
            for symbol_ticker in enabled_symbols:
                tick = self.connector.get_current_tick(symbol_ticker)
                if tick:
                    ticks[symbol_ticker] = tick
            
            self.portfolio_engine.update_all_positions(ticks)
            
        except Exception as e:
            self.logger.error("Error updating portfolio prices", error=str(e))
    
    def _close_all_open_positions(self) -> None:
        """Close all open positions on MT5."""
        try:
            positions = self.connector.get_positions()
            if positions:
                self.logger.info(f"Commanded to close {len(positions)} positions (e.g. Weekend Event)")
                for pos_id, pos in positions.items():
                    try:
                        ticket = pos.metadata.get('mt5_ticket', pos_id) if hasattr(pos, 'metadata') else pos_id
                        result = self.connector.close_position(str(ticket))
                        self.logger.info(
                            f"Closed position",
                            ticket=str(ticket),
                            symbol=pos.symbol.ticker if pos.symbol else '?',
                            result=result.get('status', '?')
                        )
                    except Exception as e:
                        self.logger.error(
                            f"Failed to close position",
                            ticket=str(pos_id),
                            error=str(e)
                        )
            else:
                if self.loop_iteration % 300 == 1:
                    self.logger.info("No open positions to close")
        except Exception as e:
            self.logger.error("Error closing positions", error=str(e))
    
    def _should_save_state(self) -> bool:
        """Check if state should be saved."""
        interval = self.config.get('monitoring', {}).get('state_save_interval_sec', 60)
        elapsed = (datetime.now(timezone.utc) - self.last_state_save).total_seconds()
        return elapsed >= interval
    
    def _save_state(self) -> None:
        """Save current system state."""
        try:
            account_info = self._get_effective_account_info()
            
            from src.core.types import SystemState
            state = SystemState(
                positions={p.position_id: p for p in self.portfolio_engine.get_all_positions()},
                open_orders={o.order_id: o for o in self.execution_engine.get_active_orders()},
                account_balance=account_info['balance'],
                account_equity=account_info['equity'],
                equity_high_water_mark=self.risk_engine.equity_high_water_mark,
                daily_start_equity=self.risk_engine.daily_start_equity,
                daily_pnl=self.portfolio_engine.daily_realized_pnl,
                consecutive_losses=self.risk_engine.circuit_breaker.consecutive_losses,
                daily_trades_count=self.risk_engine.daily_trades_count,
                kill_switch_active=self.risk_engine.kill_switch.is_active()
            )
            
            self.state_manager.save_state(state)
            self.last_state_save = datetime.now(timezone.utc)
            
        except Exception as e:
            self.logger.error("Error saving state", error=str(e))
    
    def _should_reconcile(self) -> bool:
        """Check if portfolio should be reconciled."""
        # Default to 60s if not specified (more frequent than before)
        interval = self.config.get('portfolio', {}).get('reconciliation_interval_sec', 60)
        elapsed = (datetime.now(timezone.utc) - self.last_reconciliation).total_seconds()
        return elapsed >= interval
    
    def _reconcile_portfolio(self) -> None:
        """Reconcile portfolio with MT5.
        
        Also detects closed positions to update the The5ers reversal buffer clock.
        """
        try:
            # Snapshot positions BEFORE reconciliation to detect closures
            positions_before = {
                str(p.position_id): p
                for p in self.portfolio_engine.get_all_positions()
            }
            
            success, discrepancies = self.portfolio_engine.reconcile_with_mt5()
            
            if not success:
                self.logger.warning(
                    "Portfolio reconciliation found discrepancies",
                    count=len(discrepancies)
                )
            
            # Detect closed positions (present before, absent now) and update
            # the reversal buffer clock for The5ers 5-minute rule.
            # Also update daily wins / consecutive loss counters.
            try:
                from src.core.constants import PositionSide as _PositionSide
                positions_after = {
                    str(p.position_id): p
                    for p in self.portfolio_engine.get_all_positions()
                }
                now_utc = datetime.now(timezone.utc)
                for pid, pos in positions_before.items():
                    if pid not in positions_after:
                        pos_side = getattr(pos, 'side', None)
                        pnl = float(getattr(pos, 'unrealized_pnl', 0) or 0)

                        # Reversal buffer (The5ers rule)
                        if pos_side == _PositionSide.LONG:
                            self._last_close_time['BUY'] = now_utc
                        elif pos_side == _PositionSide.SHORT:
                            self._last_close_time['SELL'] = now_utc

                        # Session manager counters
                        # §3.4 fix: pnl >= 0 (breakeven trade isn't a loss)
                        if pnl >= 0:
                            self._consecutive_losses_today = 0  # reset on win or breakeven
                            current_daily_pnl = float(self.portfolio_engine.daily_realized_pnl)
                            outcome = 'WIN' if pnl > 0 else 'BREAKEVEN'
                            self.logger.info(
                                f"[SessionManager] {outcome} recorded — "
                                f"pnl=${pnl:.2f} | daily total=${current_daily_pnl:.2f}/${self._max_daily_profit:.2f}"
                            )
                            if self._max_daily_profit > 0 and current_daily_pnl >= self._max_daily_profit:
                                self.logger.info(
                                    f"[SessionManager] 🎯 DAILY TARGET HIT — "
                                    f"no more signals today."
                                )
                        else:
                            self._consecutive_losses_today += 1
                            self.logger.info(
                                f"[SessionManager] LOSS recorded \u2014 "
                                f"consecutive={self._consecutive_losses_today} pnl=${pnl:.2f}"
                            )
                            if self._consecutive_losses_today >= self._loss_pause_threshold:
                                self._loss_pause_until = now_utc + timedelta(
                                    seconds=self._loss_pause_duration
                                )
                                pause_min = self._loss_pause_duration // 60
                                self.logger.warning(
                                    f"[SessionManager] \u26a0\ufe0f LOSS PAUSE activated \u2014 "
                                    f"{self._consecutive_losses_today} consecutive losses. "
                                    f"Trading paused for {pause_min} minutes."
                                )
            except Exception as rb_err:
                self.logger.warning(f"Session/reversal update failed (non-critical): {rb_err}")
            
            self.last_reconciliation = datetime.now(timezone.utc)
            
        except Exception as e:
            self.logger.error("Error during reconciliation", error=str(e))

    
    def _restore_state(self) -> None:
        """Restore state from previous session."""
        try:
            mt5_positions = self.connector.get_positions()
            mt5_account = self._get_effective_account_info()
            
            state = self.state_manager.restore_from_crash(
                mt5_positions=mt5_positions,
                mt5_account_info=mt5_account
            )
            
            # 9b. Balance Sanity Check
            if not self.risk_engine.validate_account_balance(
                reported_balance=state.account_balance,
                mt5_balance=mt5_account.get('balance', Decimal("0"))
            ):
                self.logger.critical("CRITICAL: Internal balance does not match MT5 account!")
                self.logger.critical("This usually happens when switching accounts without cleaning state.")
                self.logger.critical("Please check your environment and cleanup stale state files.")
                sys.exit(1)
            
            # Restore positions to portfolio
            for position in state.positions.values():
                self.portfolio_engine.add_position(position)
            
            # Restore risk engine state
            self.risk_engine.equity_high_water_mark = state.equity_high_water_mark
            self.risk_engine.daily_start_equity = state.daily_start_equity
            self.risk_engine.daily_trades_count = state.daily_trades_count
            self.risk_engine.circuit_breaker.consecutive_losses = state.consecutive_losses
            
            # Restore portfolio totals
            self.portfolio_engine.daily_realized_pnl = state.daily_pnl
            
        except Exception as e:
            self.logger.error("Error restoring state", error=str(e))
    
    def _reconnect(self) -> bool:
        """Attempt to reconnect to MT5."""
        max_attempts = 3
        
        for attempt in range(1, max_attempts + 1):
            try:
                self.logger.info(f"Reconnect attempt {attempt}/{max_attempts}")
                self.connector.disconnect()
                time.sleep(5)
                self.connector.connect()
                self.logger.info("Reconnection successful")
                return True
            except Exception as e:
                self.logger.error(f"Reconnect attempt {attempt} failed: {e}")
        
        self.logger.error("All reconnect attempts failed")
        return False
    
    def _log_metrics(self) -> None:
        """Log current system metrics."""
        try:
            portfolio_stats = self.portfolio_engine.get_statistics()
            # Use real account info from MT5, not portfolio totals
            account_info = self._get_effective_account_info()
            risk_metrics = self.risk_engine.get_risk_metrics(
                account_balance=account_info['balance'],
                account_equity=account_info['equity'],
                current_positions={p.position_id: p for p in self.portfolio_engine.get_all_positions()},
                daily_pnl=Decimal(str(portfolio_stats.get('daily_realized_pnl', 0)))
            )
            
            self.logger.info(
                "System metrics",
                iteration=self.loop_iteration,
                positions=portfolio_stats['total_positions'],
                daily_pnl=portfolio_stats['daily_realized_pnl'],
                total_pnl=portfolio_stats['total_pnl'],
                kill_switch=risk_metrics.kill_switch_active
            )
            
        except Exception as e:
            self.logger.error("Error logging metrics", error=str(e))
    
    def _display_dashboard(self) -> None:
        """Display performance dashboard."""
        try:
            if self.dashboard:
                self.dashboard.print_dashboard()
                self.dashboard.print_recent_trades(count=5)
                
                # Save snapshot
                self.dashboard.save_snapshot("data/metrics/dashboard_snapshot.json")
                
        except Exception as e:
            self.logger.error("Error displaying dashboard", error=str(e))
    
    def _load_symbols(self) -> list:
        """Load symbols from config."""
        from src.core.types import Symbol
        
        symbols = []
        for ticker, config in self.config.get('symbols', {}).items():
            if config.get('enabled', False):
                symbol = Symbol(
                    ticker=ticker,
                    pip_value=Decimal(str(config.get('pip_value', 0.01))),
                    min_lot=Decimal(str(config.get('min_lot', 0.01))),
                    max_lot=Decimal(str(config.get('max_lot', 100.0))),
                    lot_step=Decimal(str(config.get('lot_step', 0.01))),
                    value_per_lot=Decimal(str(config.get('value_per_lot', 1)))
                )
                symbols.append(symbol)
        
        return symbols
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum} - initiating shutdown")
        self.running = False
    
    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.logger.info("=" * 60)
        self.logger.info("Shutting down trading system")
        self.logger.info("=" * 60)
        
        try:
            # Save final state
            self.logger.info("Saving final state...")
            self._save_state()
            
            # Close positions if configured (CRITICAL for live trading)
            if self.config.get('shutdown', {}).get('close_all_positions', False):
                self.logger.info("Closing all positions on shutdown...")
                try:
                    positions = self.connector.get_positions()
                    if positions:
                        self.logger.info(f"Found {len(positions)} positions to close")
                        for pos_id, pos in positions.items():
                            try:
                                ticket = pos.metadata.get('mt5_ticket', pos_id) if hasattr(pos, 'metadata') else pos_id
                                result = self.connector.close_position(str(ticket))
                                self.logger.info(
                                    f"Closed position",
                                    ticket=str(ticket),
                                    symbol=pos.symbol.ticker if pos.symbol else '?',
                                    result=result.get('status', '?')
                                )
                            except Exception as e:
                                self.logger.error(
                                    f"Failed to close position",
                                    ticket=str(pos_id),
                                    error=str(e)
                                )
                    else:
                        self.logger.info("No open positions to close")
                except Exception as e:
                    self.logger.error("Error closing positions on shutdown", error=str(e))
            
            # Disconnect from MT5
            if self.connector:
                self.logger.info("Disconnecting from MT5...")
                self.connector.disconnect()
            
            self.logger.info("=" * 60)
            self.logger.info("✓ Shutdown complete")
            self.logger.info("=" * 60)
            
        except Exception as e:
            self.logger.error("Error during shutdown", error=str(e), exc_info=True)



def log_trace(msg):
    with open("debug_trace.txt", "a") as f:
        f.write(f"{datetime.now()}: {msg}\n")

def main():
    """Main entry point."""
    log_trace("Entering main()")
    import argparse
    
    parser = argparse.ArgumentParser(description="Algorithmic Trading System")
    parser.add_argument(
        '--config',
        default=None,
        help='Configuration file path (overrides env default)'
    )
    parser.add_argument(
        '--env',
        choices=['dev', 'paper', 'live'],
        default='dev',
        help='Trading environment'
    )
    parser.add_argument(
        '--force-live',
        action='store_true',
        help='Skip live mode confirmation prompt (for automated restarts)'
    )
    
    args = parser.parse_args()
    log_trace(f"Parsed args: {args}")
    
    # Determine config file based on environment
    config_files = {
        'dev': 'config/config_dev.yaml',
        'paper': 'config/config_paper.yaml',
        'live': 'config/config_live.yaml'
    }
    
    config_file = args.config if args.config else config_files.get(args.env, 'config/config_dev.yaml')
    log_trace(f"Using config file: {config_file}")
    
    # === LIVE MODE SAFETY GATE ===
    if args.env == 'live' and not args.force_live:
        # Load config to display details
        import yaml as _yaml
        with open(config_file, 'r') as _f:
            _live_cfg = _yaml.safe_load(_f)
        
        _balance = _live_cfg.get('account', {}).get('initial_balance', '?')
        _max_dd = _live_cfg.get('risk', {}).get('max_drawdown_pct', '?')
        _abs_limit = _live_cfg.get('risk', {}).get('absolute_max_loss_usd', '?')
        _max_pos = _live_cfg.get('risk', {}).get('max_positions', '?')
        _risk_pt = _live_cfg.get('risk', {}).get('risk_per_trade_pct', '?')
        
        print("\n" + "=" * 60)
        print("\033[91m" + "  ⚠️  LIVE TRADING MODE  ⚠️" + "\033[0m")
        print("=" * 60)
        print(f"  Account Balance:     ${_balance}")
        print(f"  Max Drawdown:        {float(_max_dd)*100:.1f}% (${float(_balance)*float(_max_dd):.0f})")
        print(f"  Absolute Loss Limit: ${_abs_limit}")
        print(f"  Risk Per Trade:      {float(_risk_pt)*100:.2f}% (${float(_balance)*float(_risk_pt):.0f})")
        print(f"  Max Positions:       {_max_pos}")
        print("=" * 60)
        print("\033[93m  This will trade REAL MONEY on your GFT account.\033[0m")
        print("  Type 'CONFIRM LIVE' to proceed, or Ctrl+C to abort.")
        print("=" * 60)
        
        try:
            user_input = input("\n  > ").strip()
            if user_input != "CONFIRM LIVE":
                print("\n  Aborted. Use --env paper for paper trading.")
                return
            print("\n  ✓ Live trading confirmed. Starting system...\n")
        except (KeyboardInterrupt, EOFError):
            print("\n  Aborted.")
            return
    
    # Create and run system
    try:
        system = TradingSystem(config_file=config_file)
        log_trace("TradingSystem initialized")
        system.run()
        log_trace("TradingSystem.run() finished")
    except Exception as e:
        log_trace(f"Error in main: {e}")
        import traceback
        log_trace(traceback.format_exc())


if __name__ == "__main__":
    main()
