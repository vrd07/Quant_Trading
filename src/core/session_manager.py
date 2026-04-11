"""
Session Manager — TJ: one module, one responsibility.

Extracted from TradingSystem to own all time-based gating logic:
- Trading session windows (London, NY, Asian, etc.)
- News blackout filtering
- Daily profit target gate
- Loss pause gate
- Friday cutoff (disabled per user instruction)

TradingSystem calls session_manager.should_trade() before dispatching
signals. This replaces ~80 lines of inline session logic in _process_strategies.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
import pandas as pd

from .types import SessionState
from ..monitoring.logger import get_logger

logger = get_logger(__name__)


class SessionManager:
    """
    TJ: clean interface — call should_trade() and get a yes/no + reason.
    All session, news, profit, and loss-pause logic lives here.
    """

    def __init__(self, config: dict):
        self.config = config
        self.sessions_cfg: List[dict] = config.get('trading_hours', {}).get('sessions', [])

        # News filter (loaded externally, injected via set_news_events)
        self._news_events_df: Optional[pd.DataFrame] = None
        self._news_filter_cfg: Optional[dict] = config.get('trading_hours', {}).get('news_filter', {})

        # Session state (Carmack: one visible object)
        risk_cfg = config.get('risk', {})
        cb_cfg = risk_cfg.get('circuit_breaker', {})
        self.state = SessionState(
            max_daily_profit=float(risk_cfg.get('max_daily_profit_usd', 120.0)),
            loss_pause_threshold=cb_cfg.get('loss_pause_consecutive', 2),
            loss_pause_duration=cb_cfg.get('loss_pause_minutes', 30) * 60,
        )

    def set_news_events(self, df: Optional[pd.DataFrame]) -> None:
        """Inject loaded news events DataFrame."""
        self._news_events_df = df

    def should_trade(
        self,
        daily_pnl: float,
        loop_iteration: int = 0,
    ) -> Tuple[bool, str, Set[str], float]:
        """
        Single gate: should we emit signals right now?

        Returns:
            (allowed, reason, allowed_strategies, lot_multiplier)
            - allowed: True if trading is permitted
            - reason: human-readable explanation if blocked
            - allowed_strategies: set of strategy names (empty = all allowed)
            - lot_multiplier: session-specific lot scaling factor
        """
        now = datetime.now(timezone.utc)
        today_str = now.strftime('%Y-%m-%d')

        # Daily reset
        if self.state.daily_wins_date != today_str:
            self.state.reset_daily()
            if loop_iteration % 60 == 1:
                logger.info("[SessionManager] New trading day — counters reset")

        # Daily profit target
        if self.state.max_daily_profit > 0 and daily_pnl >= self.state.max_daily_profit:
            return (
                False,
                f"Daily target reached: ${daily_pnl:.2f} / ${self.state.max_daily_profit:.2f}",
                set(),
                1.0,
            )

        # Loss pause
        if self.state.is_loss_paused():
            remaining = (self.state.loss_pause_until - now).seconds // 60
            return (
                False,
                f"Loss pause: {self.state.consecutive_losses_today} consecutive losses, "
                f"{remaining} min remaining",
                set(),
                1.0,
            )

        # News blackout
        if self._news_events_df is not None and self._news_filter_cfg:
            from ..data.news_filter import is_news_blackout
            buffer_min = self._news_filter_cfg.get('buffer_min', 15)
            tz = self._news_filter_cfg.get('timezone', 'Asia/Kolkata')
            if is_news_blackout(now, self._news_events_df, buffer_min=buffer_min, timezone=tz):
                return False, "News blackout active", set(), 1.0

        # Session windows
        allowed_strategies: Set[str] = set()
        lot_multiplier = 1.0
        in_any_session = not self.sessions_cfg  # no config = always active

        if self.sessions_cfg:
            now_hhmm = now.strftime('%H:%M')
            for session in self.sessions_cfg:
                if not session.get('enabled', True):
                    continue
                sstart = session.get('start', '00:00')
                send = session.get('end', '23:59')
                # Cross-midnight support
                if sstart <= send:
                    active = sstart <= now_hhmm < send
                else:
                    active = now_hhmm >= sstart or now_hhmm < send
                if active:
                    in_any_session = True
                    strats = session.get('strategies', [])
                    if strats:
                        allowed_strategies.update(strats)
                    lot_multiplier = float(session.get('lot_size_multiplier', 1.0))
                    break

        if not in_any_session:
            return (
                False,
                f"{now.strftime('%H:%M')} UTC — outside all session windows",
                set(),
                1.0,
            )

        self.state.current_lot_multiplier = lot_multiplier
        return True, "OK", allowed_strategies, lot_multiplier
