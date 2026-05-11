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
from src.monitoring.live_monitor_emitter import LiveMonitorEmitter
from src.data.news_filter import load_ff_events, is_news_blackout
from src.risk.trailing_stop_manager import TrailingStopManager
from src.core.session_manager import SessionManager
from src.core.types import SessionState
from src.monitoring.manual_position_tracker import ManualPositionTracker


class TradingSystem:
    """
    Main trading system orchestrator.
    
    Coordinates all modules and manages the trading loop.
    """
    
    def __init__(
        self,
        config_file: str = "config/config.yaml",
        user_profile: Optional[dict] = None,
        reset_hwm: bool = False,
    ):
        """
        Initialize trading system.

        Args:
            config_file: Path to configuration file
            user_profile: Optional dict with keys {username, quote, author} from the
                startup banner — forwarded to LiveMonitorEmitter so the dashboard
                can render the trader's name + daily quote.
            reset_hwm: If True, accept a stale equity high-water-mark on restore
                instead of failing. Required when intentionally switching account
                size — without it, a stale HWM aborts startup so the drawdown
                circuit breaker is never silently neutered.
        """
        self._reset_hwm_acknowledged = bool(reset_hwm)
        # Load configuration
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        if not self.config:
            raise ValueError(
                f"Config file loaded as empty: {config_file}\n"
                f"  The file exists but contains no YAML. Restore it with:\n"
                f"    git checkout -- {config_file}"
            )

        # Merge interactive runtime overrides (written by scripts/runtime_setup.py)
        override_path = "config/runtime_overrides.yaml"
        try:
            import os as _os
            if _os.path.exists(override_path):
                with open(override_path, 'r') as _of:
                    overrides = yaml.safe_load(_of) or {}

                def _deep_merge(base, over):
                    for k, v in over.items():
                        if isinstance(v, dict) and isinstance(base.get(k), dict):
                            _deep_merge(base[k], v)
                        else:
                            base[k] = v

                _deep_merge(self.config, overrides)
                print(f"[runtime_setup] Applied overrides from {override_path}")
        except Exception as _e:
            print(f"[runtime_setup] Failed to apply overrides: {_e}")
        
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

        # Live monitor pop-up feed. Always-on — it is a passive file writer
        # so the dashboard process can render a UI without touching the hot loop.
        self.live_monitor: Optional[LiveMonitorEmitter] = LiveMonitorEmitter(
            state_file="data/metrics/live_monitor_state.json",
            config_file=config_file,
            env=self.config.get('environment', 'dev'),
            user_profile=user_profile or {},
        )
        self.live_monitor.install_log_handler()
        self.live_monitor.set_status("STARTING", "Initialising trading system...")
        
        # State
        self.running = False
        self.last_state_save = datetime.now(timezone.utc)
        # Initialize to min time to force immediate reconciliation on startup
        self.last_reconciliation = datetime.min.replace(tzinfo=timezone.utc)
        self.loop_iteration = 0
        
        # Track last processed bar timestamps to prevent signal spam
        self._last_processed_bars: Dict[str, datetime] = {}

        # Regime ML override (written nightly by scripts/regime_classifier.py)
        self._regime_override: Optional[dict] = None

        # The5ers: directional lock + 5-min reversal buffer state
        self._last_close_time: Dict[str, datetime] = {}  # 'BUY' or 'SELL' → close timestamp
        self._reversal_buffer_min: int = 5

        # Carmack + TJ: session state grouped into one visible object,
        # managed by a focused SessionManager (not inline in main loop).
        self._session_mgr = SessionManager(self.config)

        # ── Manual position tracker (directional lock vs manual trades) ──
        self._manual_pos_tracker = ManualPositionTracker()
        self._manual_directional_lock: bool = bool(
            self.config.get('risk', {}).get('manual_guard', {}).get('directional_lock', True)
        )

        # ── Trailing stop manager ────────────────────────────────────────
        self._trailing_stop_mgr: Optional[TrailingStopManager] = None
        self._trailing_stop_fail_streak: int = 0
        
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
                preload_results = self.data_engine.preload_historical_bars(bars_count=2000)
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
                risk_engine=self.risk_engine,
                data_engine=self.data_engine,
            )
            self.logger.info("✓ Execution engine ready")
            
            # 6. Initialize trade journal (before portfolio so it can be passed)
            self.logger.info("6. Initializing trade journal...")
            self.trade_journal = TradeJournal()
            self.logger.info("✓ Trade journal ready")

            # 6a. Manual-trade monitor (audits MT5-side manual clicks against
            # the same guards RiskEngine applies to bot orders).
            from src.monitoring.manual_trade_monitor import ManualTradeMonitor
            self.manual_trade_monitor = ManualTradeMonitor(
                connector=self.connector, config=self.config, logger=self.logger
            )
            if self.manual_trade_monitor.enabled:
                self.logger.info(
                    "✓ Manual trade monitor active "
                    f"(max_risk=${self.manual_trade_monitor.max_risk_per_trade_usd}, "
                    f"blocked_hours={sorted(self.manual_trade_monitor.blocked_hours_utc)}, "
                    f"auto_close={self.manual_trade_monitor.auto_close})"
                )
            
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

            # Mitnick Rule: Never allow test strategies on live accounts.
            # Two 'test_strategy' trades leaked through in March, losing $27 on funded capital.
            if self.env == 'live':
                for sym_ticker, strats in self.strategy_manager.strategies.items():
                    for strat_name in list(strats.keys()):
                        if 'test' in strat_name.lower():
                            strats[strat_name].disable()
                            self.logger.error(
                                f"[Mitnick] Refusing test strategy '{strat_name}' in live mode — disabled"
                            )

            self.logger.info("✓ Strategies ready")
            
            # Initialize trailing stop manager
            self._trailing_stop_mgr = TrailingStopManager(self.config)
            
            # 10. Load news filter events if enabled
            self._load_news_filter()

            
            # 9. Restore state from crash (if any)
            self.logger.info("9. Checking for previous state...")
            self._restore_state()
            self.logger.info("✓ State restored")

            # 9b. Initialize daily metrics if the restored state is stale.
            # Mid-day restarts must preserve today's running daily_pnl /
            # daily_start_equity (prop-firm loss limits are anchored to
            # today's 00:00 UTC equity). But a restart after the UTC date
            # rollover would otherwise carry yesterday's figures forward,
            # because the midnight gate in _process_strategies is suppressed
            # by reset_daily() seeding daily_wins_date below.
            try:
                saved = self.state_manager.load_state()
                today_utc = datetime.now(timezone.utc).date()
                cross_midnight = (
                    saved is None
                    or saved.timestamp.astimezone(timezone.utc).date() != today_utc
                )
                # daily_start_equity == 0 means no valid anchor for today's
                # P&L — the previous run never ran reset_daily_metrics. Treat
                # as stale so we re-anchor against current MT5 equity.
                missing_anchor = self.risk_engine.daily_start_equity <= 0
                stale = cross_midnight or missing_anchor
                # Always seed daily_wins_date so the mid-loop gate doesn't
                # re-fire the regime classifier on the first iteration —
                # the pre-launch shell wrapper already ran it.
                self._session_mgr.state.daily_wins_date = today_utc.strftime('%Y-%m-%d')
                if stale:
                    account_info = self._get_effective_account_info()
                    self.risk_engine.reset_daily_metrics(account_info['equity'])
                    self.risk_engine.update_equity_hwm(account_info['equity'])
                    self.portfolio_engine.reset_daily_pnl()
                    self._session_mgr.state.reset_daily()
                    self.logger.info(
                        f"[Startup] Stale state detected — daily metrics reset "
                        f"(equity={float(account_info['equity']):.2f})"
                    )
                else:
                    self.logger.info(
                        f"[Startup] State is from today — preserving running daily metrics "
                        f"(daily_pnl=${float(self.portfolio_engine.daily_realized_pnl):.2f})"
                    )
            except Exception as e:
                self.logger.warning(f"[Startup] Daily reset check failed (non-critical): {e}")

            self.logger.info("=" * 60)
            self.logger.info("✓ ALL SYSTEMS OPERATIONAL")
            self.logger.info("=" * 60)

            # 10b. Load ML regime override (if fresh, written by nightly classifier)
            self._apply_regime_override()

            # Let the live monitor know setup is done and emit a first snapshot.
            if self.live_monitor is not None:
                self.live_monitor.set_status("RUNNING", "All systems operational.")
                self.live_monitor.write_snapshot(self, force=True)

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
                
                # Mitnick: kill switch requires MANUAL reset — no auto-clear.
                # If it was triggered for a legitimate reason (corrupt state,
                # bad broker data), auto-resetting repeats the catastrophe.
                # To reset: delete data/state/kill_switch_alert.json and restart.

                # 1. Check kill switch
                if self.risk_engine.kill_switch.is_active():
                    self.logger.critical("Kill switch active - halting trading")
                    break
                
                # 1a. Weekend Holding disabled via user instruction
                # 2. Update data from MT5
                self.data_engine.update_from_connector()
                
                # 3. Update portfolio positions with latest prices
                self._update_portfolio_prices()
                
                # 3b. Manage trailing stops (breakeven + lock)
                self._manage_trailing_stops()

                # 3c. Refresh manual position tracker (every tick for fresh lock data)
                self._refresh_manual_position_tracker()
                
                # 4. Process strategies for each symbol
                self._process_strategies()
                
                # 5. Process any fills from MT5
                self._process_fills()

                # 5b. Audit MT5-side manual positions against RiskEngine-equivalent
                # rules every ~15s. Runs cheaply on the existing positions poll.
                if self.loop_iteration % 60 == 0 and self.manual_trade_monitor is not None:
                    try:
                        self.manual_trade_monitor.check_once()
                    except Exception as e:
                        self.logger.error(f"Manual trade monitor error: {e}", exc_info=True)

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

                # 9b. Live-monitor snapshot (throttled to 1 Hz internally).
                if self.live_monitor is not None:
                    self.live_monitor.write_snapshot(self)

                # Jeff Dean: 250ms loop = 4x better worst-case latency than 1s.
                # Gold moves $0.50-2.00/s during news — 750ms matters.
                time.sleep(0.25)
                
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
        Throttled to 1 Hz (every 4th 250ms iteration). Trailing logic only
        needs second-resolution data; polling 4×/sec just multiplied bridge
        timeout errors without changing SL behaviour.
        """
        if self._trailing_stop_mgr is None or self.connector is None:
            return
        if self.loop_iteration % 4 != 0:
            return
        try:
            positions = self.connector.get_positions()
            self._trailing_stop_mgr.cleanup_closed(set(positions.keys()))
            self._trailing_stop_mgr.update(positions, self.connector)
            self._trailing_stop_fail_streak = 0
        except Exception as e:
            self._trailing_stop_fail_streak += 1
            # Log the first failure and then every 5th to surface persistent
            # bridge issues without flooding the log on transient hiccups.
            if self._trailing_stop_fail_streak == 1 or self._trailing_stop_fail_streak % 5 == 0:
                self.logger.warning(
                    f"Trailing stop update error (streak={self._trailing_stop_fail_streak}): {e}"
                )

    def _refresh_manual_position_tracker(self) -> None:
        """Refresh the manual position tracker with current MT5 positions.

        Called every loop tick so the directional lock in _execute_signal()
        always has fresh data.  Logs when manual positions appear or disappear.

        Uses ``get_all_positions`` (unfiltered) — ``get_positions`` only
        returns bot-magic positions, so manual trades would be invisible.
        """
        try:
            positions = self.connector.get_all_positions()
            events = self._manual_pos_tracker.refresh(positions)
            for ticket, event in events.items():
                pos = positions.get(ticket)
                sym = pos.symbol.ticker if pos and pos.symbol else '?'
                side = getattr(pos, 'side', None)
                side_str = side.value if side else '?'
                if event == 'OPENED':
                    self.logger.info(
                        f"[ManualTracker] Manual {side_str} position detected on {sym} "
                        f"(ticket={ticket}) — directional lock active",
                    )
                elif event == 'CLOSED':
                    self.logger.info(
                        f"[ManualTracker] Manual position closed (ticket={ticket})",
                    )
        except Exception as e:
            self.logger.debug(f"Manual position tracker refresh failed (non-critical): {e}")

    def _process_strategies(self) -> None:
        """Process all strategies with per-strategy timeframe routing."""
        # Reset daily tracking at midnight UTC (SessionManager handles its own
        # daily reset inside should_trade(), but we still need to trigger
        # nightly classifier + RiskEngine reset here)
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        if self._session_mgr.state.daily_wins_date != today_str:
            self._session_mgr.state.reset_daily()

            # -- Reload news filter to pick up today's auto-fetched CSV ----
            self._load_news_filter()

            # -- Run nightly regime classifier in background ---------------
            self._run_nightly_classifier()

            # -- Reset RiskEngine daily metrics --------------------------------
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

        # -- Intra-day regime shift check (self-throttled to every 4h) ------
        self._check_intraday_regime_shift()

        # Poll for a late-arriving news CSV. The midnight reset above runs
        # at 00:00 UTC, but the cron that fetches today's CSV runs at
        # 00:30 UTC — so on the first reload of the day we'd otherwise miss
        # it for 24 hours. _load_news_filter() is mtime-idempotent so this
        # is a cheap no-op once today's file is loaded.
        if self.loop_iteration % 60 == 1:
            self._load_news_filter()

        # TJ: one call replaces ~80 lines of inline session/news/profit/loss logic
        daily_pnl = float(self._get_daily_pnl())
        allowed, reason, allowed_strategies, lot_multiplier = (
            self._session_mgr.should_trade(daily_pnl, self.loop_iteration)
        )
        if not allowed:
            if self.loop_iteration % 60 == 1:
                self.logger.info(f"[SessionManager] {reason}")
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

        # Per-strategy timeframe overrides — data-driven from config.
        # Any strategy block with a `timeframe` key contributes its override;
        # everything else falls back to global_primary_tf at lookup time.
        def _strategy_timeframe(strat_name: str) -> str:
            return strategy_config.get(strat_name, {}).get('timeframe', global_primary_tf)

        for symbol_ticker in enabled_symbols:
            try:
                strategies_for_symbol = self.strategy_manager.strategies.get(symbol_ticker, {})

                # Collect signals from each strategy using its own timeframe
                all_signals = []
                for strategy_name, strategy in strategies_for_symbol.items():
                    # Session whitelist: skip if strategy not allowed in current session
                    if allowed_strategies and strategy_name not in allowed_strategies:
                        continue

                    tf = _strategy_timeframe(strategy_name)
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
                    # Inject session lot-size multiplier from SessionManager
                    signal.metadata['lot_size_multiplier'] = lot_multiplier
                    self._execute_signal(signal)
                    
            except Exception as e:
                self.logger.error(
                    "Error processing strategies",
                    symbol=symbol_ticker,
                    error=str(e)
                )
    

    def _load_regime_override_for(self, symbol_ticker: str) -> dict:
        """Load the best-available ML override for a broker symbol ticker.

        Resolution order:
          1. data/config_override_{BASE}.json  (BASE strips broker suffix, e.g. XAUUSD.x → XAUUSD)
          2. data/config_override.json         (legacy, XAUUSD-shaped)
        Returns {} if nothing is fresh (<=24h) or nothing exists.
        """
        import json as _json
        from datetime import timezone as _tz

        base = symbol_ticker.split(".")[0].upper()
        candidates = [
            PROJECT_ROOT / "data" / f"config_override_{base}.json",
            PROJECT_ROOT / "data" / "config_override.json",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                with open(path) as f:
                    data = _json.load(f)
                generated = datetime.fromisoformat(data.get("generated_at", "2000-01-01T00:00:00+00:00"))
                if generated.tzinfo is None:
                    generated = generated.replace(tzinfo=_tz.utc)
                age_hours = (datetime.now(_tz.utc) - generated).total_seconds() / 3600
                if age_hours > 24:
                    continue
                data["_age_hours"] = age_hours
                data["_source_path"] = str(path)
                return data
            except Exception:
                continue
        return {}

    def _load_news_filter(self) -> None:
        """(Re)load the ForexFactory news CSV into SessionManager.

        Idempotent: when the freshest available CSV is the same (path, mtime)
        we already loaded, this is a cheap no-op. That makes it safe to call
        on every loop iteration so today's CSV gets picked up minutes after
        the 00:30 UTC cron writes it — the 00:00 UTC midnight reload misses
        it by 30 minutes otherwise.
        """
        nf_cfg = self.config.get('trading_hours', {}).get('news_filter', {})
        if not nf_cfg.get('enabled', False):
            return

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        month_abbr = datetime.now(timezone.utc).strftime("%b").upper()

        candidates = [
            f"news/{today_str}_news.csv",
            f"news/{yesterday_str}_news.csv",
            f"news/{month_abbr}_news.csv",
            nf_cfg.get('csv_path', 'news/MAR_news.csv'),
        ]
        csv_path = next((c for c in candidates if Path(c).exists()), None)

        if csv_path is None:
            # Only log the missing-CSV warning on a state transition so
            # repeated polling doesn't spam the log.
            if getattr(self, '_last_news_csv', None) is not None:
                self.logger.warning(
                    "No news CSV found — news filter disabled. "
                    "Run: python scripts/fetch_daily_news.py"
                )
            self._last_news_csv = None
            self._last_news_mtime = None
            return

        try:
            mtime = Path(csv_path).stat().st_mtime
        except OSError:
            mtime = None

        # Cheap no-op when the chosen CSV is unchanged since the last load.
        if (csv_path == getattr(self, '_last_news_csv', None)
                and mtime == getattr(self, '_last_news_mtime', None)):
            return

        try:
            news_df = load_ff_events(
                csv_path=csv_path,
                currency=nf_cfg.get('currency', 'USD'),
                impacts=nf_cfg.get('impacts', ['high', 'red']),
            )
            self._session_mgr.set_news_events(news_df)
            self._last_news_csv = csv_path
            self._last_news_mtime = mtime
            self.logger.info(
                f"✓ News filter loaded: {csv_path} ({len(news_df)} events)"
            )
        except Exception as e:
            self.logger.warning(
                f"News filter CSV failed to load ({csv_path}): {e} — filter disabled"
            )

    def _get_active_regime(self, symbol_ticker: str) -> str:
        """Return the regime currently active for a symbol, or 'unknown'.

        Reads the same per-symbol override files that _apply_regime_override
        consumes, so the journal stamp matches what the strategy layer saw.
        Used by the broker-side fill-poll path which has no signal context.
        """
        try:
            override = self._load_regime_override_for(symbol_ticker)
            if override:
                return str(override.get("regime", "unknown")).lower()
        except Exception:
            pass
        return "unknown"

    def _apply_regime_override(self) -> None:
        """
        Apply per-symbol ML overrides written by scripts/regime_classifier.py.
        Each symbol loads its own config_override_{SYMBOL}.json; falls back to
        the legacy unsuffixed file. Silently skips on staleness or parse errors.
        """
        from src.strategies.base_strategy import _parse_ml_regime

        any_applied = False
        last_override = {}
        for symbol_ticker, strategies in self.strategy_manager.strategies.items():
            override = self._load_regime_override_for(symbol_ticker)
            if not override:
                continue
            any_applied = True
            last_override = override

            regime = override.get("regime", "UNKNOWN")
            confidence = override.get("confidence", 0.0)
            overrides = override.get("strategy_overrides", {})
            age_hours = override.get("_age_hours", 0.0)

            self.logger.info(
                f"[RegimeML][{symbol_ticker}] regime={regime} confidence={confidence:.0%} "
                f"(src={Path(override['_source_path']).name}, age={age_hours:.1f}h)"
            )

            ml_regime = _parse_ml_regime(regime)
            self.strategy_manager.set_ml_regime_all(symbol_ticker, ml_regime)

            for strat_name, strategy_obj in strategies.items():
                if strat_name in overrides:
                    should_enable = overrides[strat_name]
                    if should_enable:
                        strategy_obj.enable()
                    else:
                        strategy_obj.disable()
                    self.logger.info(
                        f"[RegimeML][{symbol_ticker}]   {'✅' if should_enable else '❌'}  "
                        f"{strat_name} → {'enabled' if should_enable else 'disabled'}"
                    )

        if not any_applied:
            self.logger.info("[RegimeML] No fresh per-symbol override found — running with config defaults")
        else:
            # Keep the most recent override around for introspection.
            self._regime_override = last_override

    def _run_nightly_classifier(self) -> None:
        """
        Spawn scripts/regime_classifier.py in a background daemon thread
        at midnight UTC so it never blocks the trading loop.
        Output is logged to data/logs/regime_classifier.log.
        """
        import threading
        import subprocess

        def _run():
            try:
                # ── Dump live 5m bars for every tracked symbol so the ML
                # classifier has fresh data per symbol (not just XAUUSD). ──
                try:
                    dumped_total = 0
                    for sym_key, tf_stores in self.data_engine.candle_stores.items():
                        safe_key = sym_key.replace("/", "_")
                        # Dump both 1m and 5m so cache-fallback on next startup
                        # can serve kalman_regime (needs 15m built from 1m).
                        for tf_name in ("1m", "5m"):
                            store = tf_stores.get(tf_name)
                            if not store or len(store) == 0:
                                continue
                            csv_path = PROJECT_ROOT / "data" / "logs" / f"candle_store_{safe_key}_{tf_name}.csv"
                            csv_path.parent.mkdir(parents=True, exist_ok=True)
                            store.to_csv(str(csv_path))
                            dumped_total += len(store)
                            self.logger.info(
                                f"[RegimeML] Dumped {len(store)} live {tf_name} bars from {sym_key}"
                            )
                    if dumped_total == 0:
                        self.logger.warning(
                            "[RegimeML] No candle stores found to dump"
                        )
                except Exception as data_err:
                    self.logger.warning(f"[RegimeML] Failed to dump live bars: {data_err}")

                classifier_script = PROJECT_ROOT / "scripts" / "regime_classifier.py"
                log_path = PROJECT_ROOT / "data" / "logs" / "regime_classifier.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)

                self.logger.info("[RegimeML] Starting nightly classifier in background...")
                with open(log_path, "a") as logf:
                    result = subprocess.run(
                        [sys.executable, str(classifier_script)],
                        cwd=str(PROJECT_ROOT),
                        capture_output=False,
                        stdout=logf,
                        stderr=logf,
                        timeout=300,
                    )
                if result.returncode == 0:
                    self.logger.info("[RegimeML] Classifier finished — loading new override")
                    self._apply_regime_override()
                else:
                    self.logger.warning(f"[RegimeML] Classifier exited with code {result.returncode}")
            except Exception as e:
                self.logger.warning(f"[RegimeML] Classifier thread error: {e}")

        t = threading.Thread(target=_run, daemon=True, name="RegimeClassifier")
        t.start()

    def _check_intraday_regime_shift(self) -> None:
        """Lightweight intra-day regime check every 4 hours.

        Uses the rule-based classifier on live candle data to detect
        regime shifts. If the new regime differs from the current
        override with high confidence, triggers a strategy refresh.
        """
        import json as _json
        from datetime import timezone as _tz

        now = datetime.now(_tz.utc)

        # Only check every 4 hours
        if not hasattr(self, "_last_intraday_regime_check"):
            self._last_intraday_regime_check = now
            return

        hours_since = (now - self._last_intraday_regime_check).total_seconds() / 3600
        if hours_since < 4.0:
            return

        self._last_intraday_regime_check = now

        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from scripts.regime_classifier import (
                compute_daily_bars, compute_features,
                classify_rule_based, resolve_strategy_overrides,
                FEATURE_COLS, override_path_for,
            )
            from scripts.strategy_scorer import compute_strategy_scores

            shift_detected = False
            for sym_key, tf_stores in self.data_engine.candle_stores.items():
                base = sym_key.split(".")[0].upper()
                override_path = override_path_for(base)
                if not override_path.exists():
                    continue
                with open(override_path) as f:
                    current = _json.load(f)
                current_regime = current.get("regime", "RANGE")

                store = tf_stores.get("5m")
                if not store or len(store) < 50:
                    continue

                df_5m = store.to_dataframe() if hasattr(store, "to_dataframe") else store
                if len(df_5m) < 50:
                    continue

                daily = compute_daily_bars(df_5m)
                if len(daily) < 5:
                    continue

                feat_df = compute_features(daily)
                valid = feat_df[FEATURE_COLS].dropna().index
                if len(valid) == 0:
                    continue

                last_feat = feat_df.loc[valid].iloc[-1][FEATURE_COLS].to_dict()
                new_regime, new_confidence = classify_rule_based(last_feat)

                if new_regime != current_regime and new_confidence >= 0.70:
                    self.logger.info(
                        f"[RegimeML][{base}] Intra-day regime shift: "
                        f"{current_regime} -> {new_regime} (confidence={new_confidence:.0%})"
                    )
                    perf_scores = compute_strategy_scores(lookback_days=30, symbol=base)
                    overrides = resolve_strategy_overrides(
                        new_regime, new_confidence, perf_scores,
                    )
                    current["regime"] = new_regime
                    current["confidence"] = round(new_confidence, 4)
                    current["classifier"] = "intraday-rule-based"
                    current["strategy_overrides"] = overrides
                    current["generated_at"] = now.isoformat()
                    with open(override_path, "w") as f:
                        _json.dump(current, f, indent=2)
                    shift_detected = True
                else:
                    self.logger.debug(
                        f"[RegimeML][{base}] Intra-day check: regime={new_regime} "
                        f"(same={new_regime == current_regime}, conf={new_confidence:.0%})"
                    )

            if shift_detected:
                self._apply_regime_override()

        except Exception as e:
            self.logger.debug(f"[RegimeML] Intra-day check skipped: {e}")


    def _get_daily_pnl(self) -> Decimal:
        """
        Daily P&L computed as (current MT5 equity) − (equity at start of today).

        This is the authoritative figure used by The5ers and other prop firms —
        it includes swap charges, broker commissions, and manual trades that the
        internal portfolio accumulator misses.

        Falls back to the internal accumulator only when daily_start_equity has
        not been set yet (e.g., first tick after a fresh start before midnight
        reset runs).
        """
        daily_start = self.risk_engine.daily_start_equity
        if daily_start <= 0:
            return self.portfolio_engine.daily_realized_pnl
        try:
            account_info = self._get_effective_account_info()
            return Decimal(str(account_info['equity'])) - daily_start
        except Exception:
            return self.portfolio_engine.daily_realized_pnl

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
            # --- live monitor: record the signal as it enters the pipeline ---
            if self.live_monitor is not None:
                try:
                    _sym = signal.symbol.ticker if signal.symbol else "?"
                    _side = getattr(signal.side, "value", str(signal.side))
                    _conf = float(signal.metadata.get("confidence",
                                   getattr(signal, "strength", 0.0) * 100.0))
                    self.live_monitor.record_signal(
                        strategy=signal.strategy_name or "?",
                        symbol=_sym,
                        side=_side,
                        confidence=_conf,
                        price=float(getattr(signal, "entry_price", 0) or 0),
                        sl=float(getattr(signal, "stop_loss", 0) or 0),
                        tp=float(getattr(signal, "take_profit", 0) or 0),
                        status="RECEIVED",
                        reason=str(signal.metadata.get("reason", "")),
                    )
                except Exception:
                    pass

            # Get current account state first (needed for daily P&L and order submission)
            account_info = self._get_effective_account_info()

            # ── Daily profit target gate ──────────────────────────────
            daily_pnl = self._get_daily_pnl()
            max_profit = self._session_mgr.state.max_daily_profit
            if max_profit > 0 and float(daily_pnl) >= max_profit:
                self.logger.info(
                    f"[SessionManager] Daily target hit (${float(daily_pnl):.2f}) — signal suppressed",
                    strategy=signal.strategy_name,
                )
                if self.live_monitor is not None:
                    self.live_monitor.mark_last_signal(
                        "VETOED",
                        f"Daily profit target hit (${float(daily_pnl):.2f})",
                    )
                return

            # CRITICAL: Get positions directly from MT5 (live source of truth)
            # Portfolio engine's internal list can be stale between reconciliation cycles,
            # causing the one-direction check to miss existing positions
            try:
                mt5_positions = self.connector.get_positions()
            except Exception:
                # Fallback to portfolio engine if MT5 fetch fails
                mt5_positions = {str(p.position_id): p for p in self.portfolio_engine.get_all_positions()}

            # ── Kalman confidence-based position-count gate ───────────────
            # Low-confidence kalman_regime signals are limited to 1 concurrent
            # position; high-confidence (>= threshold) signals may stack up to
            # risk.max_positions (still enforced by RiskEngine).
            if signal.strategy_name == "kalman_regime":
                conf = float(signal.metadata.get('confidence', signal.strength * 100.0))
                conf_threshold = float(signal.metadata.get('high_confidence_threshold', 90.0))
                if conf < conf_threshold:
                    kalman_open = sum(
                        1 for pos in mt5_positions.values()
                        if (getattr(pos, 'metadata', None) or {}).get('strategy') == 'kalman_regime'
                    )
                    if kalman_open >= 1:
                        self.logger.info(
                            f"[KalmanRegime] Low confidence ({conf:.1f} < {conf_threshold:.1f}): "
                            f"{kalman_open} kalman position(s) open, signal suppressed",
                            strategy=signal.strategy_name,
                            symbol=signal.symbol.ticker if signal.symbol else '?'
                        )
                        if self.live_monitor is not None:
                            self.live_monitor.mark_last_signal(
                                "VETOED",
                                f"Low Kalman confidence ({conf:.1f}<{conf_threshold:.1f}) with position open",
                            )
                        return

            # ── Manual Trade Directional Lock ─────────────────────────────
            # If a manual position is open, block bot signals in the opposite
            # direction. Same-direction signals are allowed (stacking).
            from src.core.constants import OrderSide as _OrderSide, PositionSide as _PositionSide
            signal_side = signal.side  # OrderSide.BUY or OrderSide.SELL
            signal_sym = signal.symbol.ticker if signal.symbol else '?'

            if self._manual_directional_lock and self._manual_pos_tracker.has_manual_positions:
                manual_dirs = self._manual_pos_tracker.get_manual_directions(symbol=signal_sym)
                if manual_dirs:
                    # BUY signal conflicts with SHORT manual; SELL conflicts with LONG manual
                    if (signal_side == _OrderSide.BUY and 'SHORT' in manual_dirs) or \
                       (signal_side == _OrderSide.SELL and 'LONG' in manual_dirs):
                        conflicting = 'SHORT' if 'SHORT' in manual_dirs else 'LONG'
                        self.logger.info(
                            f"[ManualLock] {conflicting} manual trade open on {signal_sym} "
                            f"→ {signal_side.value} bot signal blocked",
                            strategy=signal.strategy_name,
                            symbol=signal_sym,
                        )
                        if self.live_monitor is not None:
                            self.live_monitor.mark_last_signal(
                                "VETOED",
                                f"Manual {conflicting} open on {signal_sym} — opposite signal blocked",
                            )
                        return

            # ── The5ers Rule: Directional Lock (bot positions) ────────────
            # No SELL allowed if a BUY is open; no BUY allowed if a SELL is open.
            for pos in mt5_positions.values():
                pos_side = getattr(pos, 'side', None)
                if pos_side is None:
                    continue
                is_long = pos_side == _PositionSide.LONG
                is_short = pos_side == _PositionSide.SHORT
                if (signal_side == _OrderSide.BUY and is_short) or \
                   (signal_side == _OrderSide.SELL and is_long):
                    self.logger.info(
                        f"[The5ers] Directional lock: {'SHORT' if is_short else 'LONG'} open, "
                        f"rejecting {signal_side.value} signal",
                        strategy=signal.strategy_name,
                        symbol=signal_sym,
                    )
                    if self.live_monitor is not None:
                        self.live_monitor.mark_last_signal(
                            "VETOED",
                            f"Directional lock — {'SHORT' if is_short else 'LONG'} already open",
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
                    if self.live_monitor is not None:
                        self.live_monitor.mark_last_signal(
                            "VETOED",
                            f"Reversal buffer active ({elapsed_min:.1f}m of {self._reversal_buffer_min}m)",
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
                if self.live_monitor is not None:
                    self.live_monitor.mark_last_signal(
                        "FIRED",
                        f"order {str(order.order_id)[:8]} {order.status.value}",
                    )
            else:
                if self.live_monitor is not None:
                    self.live_monitor.mark_last_signal(
                        "VETOED", "Blocked by risk engine or execution checks"
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
                # Falls back to 'manual' (not 'unknown') so analytics are separated correctly.
                strategy = 'manual'
                if '|' in comment:
                    strategy = comment.split('|')[0] or 'manual'
                elif comment.startswith('Order-'):
                    strategy = 'unknown'  # bot order, strategy name missing

                # Derive original position direction from MT5 deal fields:
                # MT5 deal type: 0=BUY deal, 1=SELL deal
                # MT5 deal entry: 0=IN (open), 1=OUT (close), 2=INOUT, 3=OUT_BY
                # A closing deal (entry=OUT) type 1 (SELL) means the position that was
                # closed was LONG; type 0 (BUY close) means the position was SHORT.
                deal_type = deal.get('type', deal.get('deal_type', -1))
                deal_entry = deal.get('entry', deal.get('entry_type', -1))
                if deal_entry in (1, '1', 'OUT'):
                    # This is a closing deal — infer original position side
                    if deal_type in (1, '1'):    # SELL close → was LONG
                        deal_side = 'LONG'
                    elif deal_type in (0, '0'):  # BUY close → was SHORT
                        deal_side = 'SHORT'
                    else:
                        deal_side = 'UNKNOWN'
                else:
                    deal_side = 'UNKNOWN'  # opening deal or no entry field

                # Record in TradeJournal if available
                if self.trade_journal is not None:
                    try:
                        from decimal import Decimal as _Dec
                        # Stamp the regime that is currently active for this symbol
                        # so the journal can answer regime-conditional questions
                        # even on broker-side closes that bypass the normal path.
                        active_regime = self._get_active_regime(symbol_name)
                        self.trade_journal.record_raw_trade(
                            strategy=strategy,
                            symbol=symbol_name,
                            side=deal_side,           # derived from deal type+entry fields
                            entry_price=_Dec(str(deal.get('price_open', 0))),
                            exit_price=_Dec(str(deal.get('price', deal.get('price_close', 0)))),
                            quantity=_Dec(str(deal.get('volume', 0))),
                            realized_pnl=_Dec(str(realized_pnl)),
                            entry_time=None,
                            exit_time=None,
                            metadata={
                                'mt5_ticket': ticket,
                                'source': 'fill_poll',
                                'regime': active_regime,
                            },
                        )
                    except Exception as _je:
                        self.logger.debug(f"Fill poll: journal record failed for ticket={ticket}: {_je}")

                # Update RiskEngine circuit breaker in real-time (no more 30s blind spot)
                # Pass strategy so the manual-daily-loss counter bumps only on
                # manual-tagged closed deals (wiring the guard to real fills).
                if self.risk_engine is not None:
                    from decimal import Decimal as _Dec
                    self.risk_engine.record_trade_result(
                        _Dec(str(realized_pnl)), strategy_name=strategy
                    )

                self.logger.info(
                    f"[FillPoller] Detected closed deal: ticket={ticket} symbol={symbol_name} "
                    f"pnl={realized_pnl:.2f} strategy={strategy}"
                )

        except Exception as e:
            # Non-critical — reconciliation will catch anything missed
            self.logger.debug(f"[FillPoller] Error polling fills (non-critical): {e}")
    
    def _update_portfolio_prices(self) -> None:
        """Update all portfolio positions with latest prices — one connector call per unique symbol."""
        try:
            ticks = {}
            for position in self.portfolio_engine.get_all_positions():
                if position.symbol and position.symbol.ticker not in ticks:
                    tick = self.connector.get_current_tick(position.symbol.ticker)
                    if tick:
                        ticks[position.symbol.ticker] = tick
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

                        # Carmack: state mutations delegated to SessionState
                        ss = self._session_mgr.state
                        if pnl > 0:
                            ss.record_win()
                            current_daily_pnl = float(self._get_daily_pnl())
                            self.logger.info(
                                f"[SessionManager] WIN recorded — "
                                f"pnl=${pnl:.2f} | daily total=${current_daily_pnl:.2f}"
                                f"/${ss.max_daily_profit:.2f}"
                            )
                        elif pnl == 0:
                            self.logger.info(
                                f"[SessionManager] BREAKEVEN — "
                                f"consecutive losses unchanged={ss.consecutive_losses_today}"
                            )
                        else:
                            ss.record_loss()
                            self.logger.info(
                                f"[SessionManager] LOSS recorded — "
                                f"consecutive={ss.consecutive_losses_today} pnl=${pnl:.2f}"
                            )
                            if ss.is_loss_paused():
                                pause_min = ss.loss_pause_duration // 60
                                self.logger.warning(
                                    f"[SessionManager] LOSS PAUSE activated — "
                                    f"{ss.consecutive_losses_today} consecutive losses. "
                                    f"Paused for {pause_min} minutes."
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
            loaded_symbols = {s.ticker: s for s in self._load_symbols()}
            for position in state.positions.values():
                # Patch symbol with full config values
                if position.symbol:
                    base_ticker = position.symbol.ticker.split('.')[0] if '.' in position.symbol.ticker else position.symbol.ticker
                    if base_ticker in loaded_symbols:
                        full_sym = loaded_symbols[base_ticker]
                        from src.core.types import Symbol
                        position.symbol = Symbol(
                            ticker=position.symbol.ticker,
                            exchange=full_sym.exchange,
                            pip_value=full_sym.pip_value,
                            min_lot=full_sym.min_lot,
                            max_lot=full_sym.max_lot,
                            lot_step=full_sym.lot_step,
                            value_per_lot=full_sym.value_per_lot,
                            commission_per_lot=full_sym.commission_per_lot
                        )
                self.portfolio_engine.add_position(position)
            
            # Restore risk engine state.
            # Stale HWM guard: if the restored high-water mark would imply a
            # drawdown greater than the configured limit against the live
            # account equity, it is almost certainly leftover from a different
            # account size. Resetting silently disables the drawdown circuit
            # breaker for this session — refuse to start unless the operator
            # explicitly opts in via --reset-hwm.
            restored_hwm = state.equity_high_water_mark
            current_equity = mt5_account.get('equity', Decimal("0"))
            stale_hwm = (
                restored_hwm > 0
                and current_equity > 0
                and restored_hwm > current_equity
                and (restored_hwm - current_equity) / restored_hwm >= self.risk_engine.max_drawdown_pct
            )
            if stale_hwm:
                implied_dd = float((restored_hwm - current_equity) / restored_hwm)
                if not self._reset_hwm_acknowledged:
                    self.logger.critical(
                        "Stale equity HWM on restore — REFUSING to start",
                        restored_hwm=float(restored_hwm),
                        current_equity=float(current_equity),
                        implied_drawdown=implied_dd,
                        limit=float(self.risk_engine.max_drawdown_pct),
                    )
                    self.logger.critical(
                        "If this is an intentional account switch, restart with "
                        "--reset-hwm. Silently resetting would neuter the drawdown "
                        "circuit breaker for this session."
                    )
                    sys.exit(1)
                self.logger.warning(
                    "Stale equity HWM accepted via --reset-hwm — resetting to current equity",
                    restored_hwm=float(restored_hwm),
                    current_equity=float(current_equity),
                    implied_drawdown=implied_dd,
                    limit=float(self.risk_engine.max_drawdown_pct),
                )
                restored_hwm = current_equity
            self.risk_engine.equity_high_water_mark = restored_hwm
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
            daily_pnl = self._get_daily_pnl()
            risk_metrics = self.risk_engine.get_risk_metrics(
                account_balance=account_info['balance'],
                account_equity=account_info['equity'],
                current_positions={p.position_id: p for p in self.portfolio_engine.get_all_positions()},
                daily_pnl=daily_pnl
            )

            self.logger.info(
                "System metrics",
                iteration=self.loop_iteration,
                positions=portfolio_stats['total_positions'],
                daily_pnl=float(daily_pnl),
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

        # Tell the live-monitor pop-up the bot is going down.
        if self.live_monitor is not None:
            try:
                self.live_monitor.set_status("STOPPED", "Bot shutdown in progress.")
                self.live_monitor.write_snapshot(self, force=True)
            except Exception:
                pass

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
        finally:
            if self.live_monitor is not None:
                try:
                    self.live_monitor.shutdown("Bot stopped.")
                except Exception:
                    pass



def log_trace(msg):
    with open("debug_trace.txt", "a") as f:
        f.write(f"{datetime.now()}: {msg}\n")

_MOTIVATIONAL_QUOTES = [
    ("The market can stay irrational longer than you can stay solvent.", "John Maynard Keynes"),
    ("Risk comes from not knowing what you're doing.", "Warren Buffett"),
    ("The goal of a successful trader is to make the best trades. Money is secondary.", "Alexander Elder"),
    ("In trading, it's not about how much you make but how much you don't lose.", "Bernard Baruch"),
    ("The trend is your friend until the end when it bends.", "Ed Seykota"),
    ("Amateurs think about how much money they can make. Professionals think about how much they can lose.", "Jack Schwager"),
    ("Discipline is the bridge between goals and accomplishment.", "Jim Rohn"),
    ("The stock market is a device for transferring money from the impatient to the patient.", "Warren Buffett"),
    ("Plan the trade and trade the plan.", "Unknown"),
    ("Cut your losses short and let your profits run.", "David Ricardo"),
    ("Every battle is won before it is ever fought.", "Sun Tzu"),
    ("Patience is bitter, but its fruit is sweet.", "Aristotle"),
    ("The four most dangerous words in investing are: 'this time it's different.'", "Sir John Templeton"),
    ("Markets are never wrong; opinions are.", "Jesse Livermore"),
    ("It's not whether you're right or wrong that's important, but how much money you make when you're right and how much you lose when you're wrong.", "George Soros"),
    ("The elements of good trading are: cutting losses, cutting losses, and cutting losses.", "Ed Seykota"),
    ("Don't focus on making money; focus on protecting what you have.", "Paul Tudor Jones"),
    ("Losers average losers.", "Paul Tudor Jones"),
    ("The key to trading success is emotional discipline. Making money has nothing to do with intelligence.", "Victor Sperandeo"),
    ("I'm only rich because I know when I'm wrong.", "George Soros"),
    ("If you can learn to create a state of mind that is not affected by the market's behavior, the struggle will cease to exist.", "Mark Douglas"),
    ("Successful trading is about finding the rules that work and then sticking to those rules.", "William O'Neil"),
    ("The hard work in trading comes in the preparation. The actual process of trading should be effortless.", "Jack Schwager"),
    ("Do not anticipate and move without market confirmation — being a little late is your insurance.", "Jesse Livermore"),
    ("A peak performance trader is totally committed to being the best and doing whatever it takes to be the best.", "Van K. Tharp"),
    ("The goal is not to be right. The goal is to make money.", "Ray Dalio"),
    ("Pain + Reflection = Progress.", "Ray Dalio"),
    ("Every day I assume every position I have is wrong.", "Paul Tudor Jones"),
    ("Don't be a hero. Don't have an ego. Always question yourself and your ability.", "Paul Tudor Jones"),
    ("The best traders have no ego. You have to swallow your pride and get out of the losses.", "Tom Baldwin"),
    ("Know what you own, and know why you own it.", "Peter Lynch"),
    ("The secret to being successful from a trading perspective is to have an indefatigable and an undying and unquenchable thirst for information and knowledge.", "Paul Tudor Jones"),
    ("Confidence is not 'I will profit on this trade.' Confidence is 'I will be fine if I don't profit on this trade.'", "Yvan Byeajee"),
    ("Trade what's happening — not what you think is gonna happen.", "Doug Gregory"),
    ("The goal of trading is to exchange risk for profit. You cannot make profit without taking risk.", "Unknown"),
    ("Great traders manage risk — they don't avoid it.", "Unknown"),
    ("A loss never bothers me after I take it. I forget it overnight. But being wrong and not taking the loss — that is what does damage to the pocketbook and to the soul.", "Jesse Livermore"),
    ("Time is your friend; impulse is your enemy.", "Jack Bogle"),
    ("Opportunities come infrequently. When it rains gold, put out the bucket, not the thimble.", "Warren Buffett"),
    ("Your biggest enemy in trading is yourself.", "Unknown"),
]


def _pick_daily_quote() -> tuple:
    """Pick a quote using today's ordinal so it's stable per-day but varies across days."""
    import datetime as _dt
    return _MOTIVATIONAL_QUOTES[_dt.date.today().toordinal() % len(_MOTIVATIONAL_QUOTES)]


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
    parser.add_argument(
        '--trader-name',
        default=None,
        help='Trader name shown on the live monitor dashboard (defaults to "Trader" or interactive prompt)'
    )
    parser.add_argument(
        '--reset-hwm',
        action='store_true',
        help='Accept a stale equity high-water-mark on restore (use ONLY when intentionally switching account size; otherwise the drawdown circuit breaker would be silently disabled for this session)'
    )

    args = parser.parse_args()
    log_trace(f"Parsed args: {args}")
    
    # Determine config file based on environment
    config_files = {
        'dev': 'config/config_dev.yaml',
        'paper': 'config/config_paper.yaml',
        'live': 'config/config_live_5000.yaml'
    }
    
    config_file = args.config
    
    # Interactive prompt if live mode is specified without a hardcoded config
    if args.env == 'live' and not config_file and not args.force_live:
        print("\n" + "=" * 60)
        print("  Select Account Size Config for LIVE Trading")
        print("=" * 60)
        print("  1) $100")
        print("  2) $1,000")
        print("  3) $5,000")
        print("  4) $10,000")
        print("  5) $25,000")
        print("  6) $50,000")
        print("=" * 60)
        try:
            choice = input("  Enter choice (1-6) [Default: 3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Aborted.")
            return
            
        choice_map = {
            "1": "config/config_live_100.yaml",
            "2": "config/config_live_1000.yaml",
            "3": "config/config_live_5000.yaml",
            "4": "config/config_live_10000.yaml",
            "5": "config/config_live_25000.yaml",
            "6": "config/config_live_50000.yaml"
        }
        config_file = choice_map.get(choice, "config/config_live_5000.yaml")
        
    if not config_file:
        config_file = config_files.get(args.env, 'config/config_dev.yaml')
        
    log_trace(f"Using config file: {config_file}")
    
    # === LIVE MODE SAFETY GATE ===
    if args.env == 'live' and not args.force_live:
        # Load config to display details
        import yaml as _yaml
        with open(config_file, 'r', encoding='utf-8') as _f:
            _live_cfg = _yaml.safe_load(_f) or {}

        # Merge any runtime overrides written by scripts/runtime_setup.py so the
        # banner reflects the values the user actually entered.
        import os as _os
        _override_path = "config/runtime_overrides.yaml"
        if _os.path.exists(_override_path):
            try:
                with open(_override_path, 'r') as _of:
                    _ov = _yaml.safe_load(_of) or {}
                def _dm(b, o):
                    for k, v in o.items():
                        if isinstance(v, dict) and isinstance(b.get(k), dict):
                            _dm(b[k], v)
                        else:
                            b[k] = v
                _dm(_live_cfg, _ov)
            except Exception:
                pass

        # Defensive: any section may be missing or present-but-None after YAML load/merge.
        _account = _live_cfg.get('account') or {}
        _risk = _live_cfg.get('risk') or {}
        _sizing = _risk.get('position_sizing') or {}
        _fixed_lots = _sizing.get('fixed_lots') or {}
        _symbols_cfg = _live_cfg.get('symbols') or {}
        _balance = _account.get('initial_balance', 0)
        _max_dd = _risk.get('max_drawdown_pct', 0)
        _abs_limit = _risk.get('absolute_max_loss_usd', '?')
        _max_pos = _risk.get('max_positions', '?')
        _risk_pt = _risk.get('risk_per_trade_pct', 0)
        _daily_profit = _risk.get('max_daily_profit_usd', 0)
        try:
            _risk_usd = float(_balance) * float(_risk_pt)
        except (TypeError, ValueError):
            _risk_usd = 0.0

        # Walk enabled symbols and pick each one's effective lot size.
        # runtime_setup.py writes min_lot == max_lot == user_lot on the symbol,
        # so min_lot is the authoritative display value. Fall back to fixed_lots
        # map only when the symbol wasn't touched by runtime_setup.
        _enabled_lots: list[tuple[str, float]] = []
        for _tkr, _scfg in _symbols_cfg.items():
            if not isinstance(_scfg, dict) or not _scfg.get('enabled', False):
                continue
            _lot_val = _scfg.get('min_lot')
            if _lot_val is None:
                _lot_val = _fixed_lots.get(_tkr, _fixed_lots.get('default', 0.0))
            try:
                _enabled_lots.append((_tkr, float(_lot_val)))
            except (TypeError, ValueError):
                _enabled_lots.append((_tkr, 0.0))

        print("\n" + "=" * 60)
        print("\033[91m" + "  ⚠️  LIVE TRADING MODE  ⚠️" + "\033[0m")
        print("=" * 60)
        print(f"  Account Balance:     ${_balance}")
        print(f"  Max Drawdown:        {float(_max_dd)*100:.1f}% (${float(_balance)*float(_max_dd):.0f})")
        print(f"  Absolute Loss Limit: ${_abs_limit}")
        _budget_usd = _risk.get('risk_per_trade_usd')
        if _budget_usd:
            try:
                _budget_usd_f = float(_budget_usd)
            except (TypeError, ValueError):
                _budget_usd_f = 0.0
            print(f"  Risk Per Trade:      ${_budget_usd_f:.2f} (SL auto-sized — every trade risks exactly this)")
        else:
            print(f"  Risk Per Trade:      {_risk_pt*100:.2f}% (${_risk_usd:.2f})")
        try:
            _dp = float(_daily_profit)
        except (TypeError, ValueError):
            _dp = 0.0
        if _dp > 0:
            _dp_pct = (_dp / float(_balance) * 100) if _balance else 0.0
            print(f"  Max Daily Profit:    ${_dp:.2f} ({_dp_pct:.2f}%)")
        else:
            print(f"  Max Daily Profit:    disabled")
        if _enabled_lots:
            print(f"  Symbols / Lot Size:")
            for _tkr, _lv in _enabled_lots:
                print(f"    - {_tkr:<14s} {_lv} lots")
        else:
            print(f"  Symbols / Lot Size:  (none enabled)")
        print(f"  Max Positions:       {_max_pos}")
        print("=" * 60)

        try:
            _trader_name = input("  Enter your name: ").strip() or "Trader"
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return

        _q, _author = _pick_daily_quote()

        print()
        print(f"\033[96m  Welcome back, {_trader_name}. Let's trade with discipline today.\033[0m")
        print(f"\033[93m  \"{_q}\"\033[0m")
        print(f"\033[90m    — {_author}\033[0m")
        print("=" * 60)
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
    
    # Resolve final user profile (live confirm flow sets these; otherwise use CLI flag / defaults)
    _final_name = locals().get("_trader_name") or args.trader_name or "Trader"
    _final_q = locals().get("_q")
    _final_author = locals().get("_author")
    if not _final_q or not _final_author:
        _final_q, _final_author = _pick_daily_quote()
    user_profile = {
        "username": _final_name,
        "quote": _final_q,
        "author": _final_author,
    }

    # Create and run system
    try:
        system = TradingSystem(
            config_file=config_file,
            user_profile=user_profile,
            reset_hwm=args.reset_hwm,
        )
        log_trace("TradingSystem initialized")
        system.run()
        log_trace("TradingSystem.run() finished")
    except Exception as e:
        log_trace(f"Error in main: {e}")
        import traceback
        log_trace(traceback.format_exc())


if __name__ == "__main__":
    # Windows 11 compatibility: fix asyncio event loop policy
    # Python 3.8+ on Windows defaults to ProactorEventLoop which doesn't support
    # some subprocess operations. SelectorEventLoop is the safe cross-platform choice.
    import sys as _sys
    if _sys.platform == "win32":
        import asyncio as _asyncio
        import warnings as _warnings
        # Python 3.12 deprecated set_event_loop_policy and the Windows*EventLoopPolicy
        # classes, but they still work and are the correct fix for subprocess bugs on
        # Windows. Suppress the noise so the banner isn't buried under warnings.
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore", DeprecationWarning)
            _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
    main()
