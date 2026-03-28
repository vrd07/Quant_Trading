"""
Trade Journal - Record every trade for analysis.

Tracks:
- Entry/exit details
- P&L
- Strategy that generated it
- Market conditions
- Execution quality (slippage)

Performance notes:
- Ticket dedup uses in-memory set: O(1) per check instead of O(n) CSV scan
- Set is loaded once at startup and maintained incrementally
"""

from typing import List, Dict, Optional, Set
from datetime import datetime, timezone
from pathlib import Path
import json
import csv
from decimal import Decimal

from ..core.types import Order, Position


class TradeJournal:
    """
    Record all trades for analysis and review.

    Each trade record includes:
    - Entry/exit timestamps
    - Prices and P&L
    - Strategy information
    - Market regime
    - Execution metrics
    """

    def __init__(self, journal_file: str = "data/logs/trade_journal.csv"):
        """
        Initialize trade journal.

        Args:
            journal_file: Path to journal CSV file
        """
        self.journal_file = Path(journal_file)
        self.journal_file.parent.mkdir(parents=True, exist_ok=True)

        # Initialize CSV if doesn't exist
        if not self.journal_file.exists():
            self._initialize_csv()

        # O(1) dedup: load all recorded tickets into memory once
        self._recorded_tickets: Set[str] = self._load_recorded_tickets()

        from .logger import get_logger
        self.logger = get_logger(__name__)

    def record_trade(
        self,
        position: Position,
        exit_price: Decimal,
        exit_time: datetime,
        realized_pnl: Decimal,
        exit_reason: str = "unknown"
    ) -> None:
        """
        Record completed trade.

        Args:
            position: Closed position
            exit_price: Exit price
            exit_time: Exit timestamp
            realized_pnl: Realized P&L
            exit_reason: Why position was closed
        """
        try:
            # O(1) dedup check via in-memory set
            mt5_ticket = position.metadata.get('mt5_ticket') if position.metadata else None
            if mt5_ticket and self._is_ticket_recorded(str(mt5_ticket)):
                self.logger.debug(
                    "Trade already recorded, skipping duplicate",
                    mt5_ticket=str(mt5_ticket)
                )
                return

            # Guard against zero-division in pnl_pct
            notional = position.entry_price * position.quantity * position.symbol.value_per_lot
            pnl_pct = float((realized_pnl / notional) * 100) if notional else 0.0

            trade_record = {
                'trade_id': str(position.position_id),
                'symbol': position.symbol.ticker,
                'strategy': position.metadata.get('strategy', 'unknown'),
                'side': position.side.value,

                # Entry details
                'entry_time': position.opened_at.isoformat(),
                'entry_price': float(position.entry_price),
                'quantity': float(position.quantity),

                # Exit details
                'exit_time': exit_time.isoformat(),
                'exit_price': float(exit_price),
                'exit_reason': exit_reason,

                # P&L
                'realized_pnl': float(realized_pnl),
                'pnl_pct': pnl_pct,

                # Risk metrics
                'stop_loss': float(position.stop_loss) if position.stop_loss else None,
                'take_profit': float(position.take_profit) if position.take_profit else None,
                'initial_risk': float(abs(position.entry_price - position.stop_loss) * position.quantity * position.symbol.value_per_lot) if position.stop_loss else None,

                # Duration
                'duration_seconds': (exit_time - position.opened_at).total_seconds(),

                # Metadata
                'regime': position.metadata.get('regime', 'unknown'),
                'signal_strength': position.metadata.get('signal_strength', 0),
                'mt5_ticket': str(position.metadata.get('mt5_ticket', '')) if position.metadata else ''
            }

            # Append to CSV
            self._append_to_csv(trade_record)

            # Update in-memory dedup set
            if mt5_ticket:
                self._recorded_tickets.add(str(mt5_ticket))

            self.logger.info(
                "Trade recorded",
                trade_id=trade_record['trade_id'],
                symbol=trade_record['symbol'],
                pnl=realized_pnl
            )

        except Exception as e:
            self.logger.error(f"Error recording trade: {e}", exc_info=True)

    def record_raw_trade(
        self,
        strategy: str,
        symbol: str,
        side: str,
        entry_price: Decimal,
        exit_price: Decimal,
        quantity: Decimal,
        realized_pnl: Decimal,
        entry_time: Optional[datetime] = None,
        exit_time: Optional[datetime] = None,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Record a trade from raw fields (used by _process_fills poll path).

        Unlike record_trade() which requires a Position object, this method
        accepts explicit parameters so the fill-poll loop can record trades
        with the correct strategy name extracted from the MT5 deal comment.

        Args:
            strategy: Strategy name (e.g. 'vwap_deviation', 'kalman_regime').
            symbol: Instrument ticker string.
            side: Position direction ('LONG', 'SHORT', or 'UNKNOWN').
            entry_price: Trade open price.
            exit_price: Trade close price.
            quantity: Lot size.
            realized_pnl: Net P&L including swap/commission.
            entry_time: Entry timestamp (defaults to now).
            exit_time: Exit timestamp (defaults to now).
            metadata: Extra fields (must include 'mt5_ticket' for dedup).
        """
        try:
            metadata = metadata or {}
            mt5_ticket = str(metadata.get('mt5_ticket', ''))

            if mt5_ticket and self._is_ticket_recorded(mt5_ticket):
                self.logger.debug(
                    "Raw trade already recorded, skipping duplicate",
                    mt5_ticket=mt5_ticket,
                )
                return

            now = datetime.now(timezone.utc)
            if entry_time is None:
                entry_time = now
            if exit_time is None:
                exit_time = now

            trade_record = {
                'trade_id': mt5_ticket or f"raw_{now.timestamp():.0f}",
                'symbol': symbol,
                'strategy': strategy or 'unknown',
                'side': side,
                'entry_time': entry_time.isoformat() if isinstance(entry_time, datetime) else str(entry_time),
                'entry_price': float(entry_price),
                'quantity': float(quantity),
                'exit_time': exit_time.isoformat() if isinstance(exit_time, datetime) else str(exit_time),
                'exit_price': float(exit_price),
                'exit_reason': metadata.get('source', 'fill_poll'),
                'realized_pnl': float(realized_pnl),
                'pnl_pct': 0.0,
                'stop_loss': None,
                'take_profit': None,
                'initial_risk': None,
                'duration_seconds': 0,
                'regime': metadata.get('regime', 'unknown'),
                'signal_strength': 0,
                'mt5_ticket': mt5_ticket,
            }

            self._append_to_csv(trade_record)

            if mt5_ticket:
                self._recorded_tickets.add(mt5_ticket)

            self.logger.info(
                "Raw trade recorded",
                mt5_ticket=mt5_ticket,
                symbol=symbol,
                strategy=strategy,
                pnl=float(realized_pnl),
            )

        except Exception as e:
            self.logger.error(f"Error recording raw trade: {e}", exc_info=True)

    def get_trades(
        self,
        symbol: Optional[str] = None,
        strategy: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> List[Dict]:
        """
        Get trades with optional filters.

        Args:
            symbol: Filter by symbol
            strategy: Filter by strategy
            start_date: Filter by date range
            end_date: Filter by date range

        Returns:
            List of trade records
        """
        import pandas as pd

        if not self.journal_file.exists():
            return []

        df = pd.read_csv(self.journal_file)

        # Apply filters
        if symbol:
            df = df[df['symbol'] == symbol]

        if strategy:
            df = df[df['strategy'] == strategy]

        if start_date:
            df = df[pd.to_datetime(df['entry_time']) >= start_date]

        if end_date:
            df = df[pd.to_datetime(df['entry_time']) <= end_date]

        return df.to_dict('records')

    def get_statistics(self) -> Dict:
        """
        Calculate trade statistics.

        Returns:
            Dict with statistics
        """
        import pandas as pd

        if not self.journal_file.exists():
            return {}

        df = pd.read_csv(self.journal_file)

        if len(df) == 0:
            return {}

        wins = df[df['realized_pnl'] > 0]
        losses = df[df['realized_pnl'] < 0]

        stats = {
            'total_trades': len(df),
            'winning_trades': len(wins),
            'losing_trades': len(losses),
            'win_rate': len(wins) / len(df) * 100 if len(df) > 0 else 0,

            'total_pnl': df['realized_pnl'].sum(),
            'avg_win': wins['realized_pnl'].mean() if len(wins) > 0 else 0,
            'avg_loss': losses['realized_pnl'].mean() if len(losses) > 0 else 0,
            'largest_win': wins['realized_pnl'].max() if len(wins) > 0 else 0,
            'largest_loss': losses['realized_pnl'].min() if len(losses) > 0 else 0,

            'avg_duration_minutes': df['duration_seconds'].mean() / 60,

            'profit_factor': abs(wins['realized_pnl'].sum() / losses['realized_pnl'].sum()) if len(losses) > 0 and losses['realized_pnl'].sum() != 0 else 0
        }

        return stats

    def _initialize_csv(self) -> None:
        """Initialize CSV file with headers."""
        headers = [
            'trade_id', 'symbol', 'strategy', 'side',
            'entry_time', 'entry_price', 'quantity',
            'exit_time', 'exit_price', 'exit_reason',
            'realized_pnl', 'pnl_pct',
            'stop_loss', 'take_profit', 'initial_risk',
            'duration_seconds', 'regime', 'signal_strength', 'mt5_ticket'
        ]

        with open(self.journal_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

    def _append_to_csv(self, trade_record: Dict) -> None:
        """Append trade record to CSV."""
        with open(self.journal_file, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=trade_record.keys())
            writer.writerow(trade_record)

    def _is_ticket_recorded(self, mt5_ticket: str) -> bool:
        """Check if a trade with this MT5 ticket has already been recorded. O(1)."""
        return mt5_ticket in self._recorded_tickets

    def _load_recorded_tickets(self) -> Set[str]:
        """Load all recorded MT5 tickets from CSV into memory. Runs once at startup."""
        tickets: Set[str] = set()
        if not self.journal_file.exists():
            return tickets
        try:
            with open(self.journal_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticket = row.get('mt5_ticket', '').strip()
                    if ticket:
                        tickets.add(ticket)
        except Exception:
            pass
        return tickets
