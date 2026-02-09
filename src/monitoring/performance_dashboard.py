"""
Performance Dashboard - Display live trading metrics.

Shows:
- Current equity
- Daily P&L
- Open positions
- Recent trades
- Key metrics
"""

from typing import Dict, List, Optional
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pandas as pd

from ..portfolio.portfolio_engine import PortfolioEngine
from .trade_journal import TradeJournal


class PerformanceDashboard:
    """
    Display trading performance metrics.
    
    Can output to:
    - Console (text)
    - JSON file
    - Web interface (future)
    """
    
    def __init__(
        self,
        portfolio: PortfolioEngine,
        journal: TradeJournal,
        initial_capital: Decimal,
        data_engine=None
    ):
        """
        Initialize dashboard.
        
        Args:
            portfolio: Portfolio engine
            journal: Trade journal
            initial_capital: Starting capital
            data_engine: Optional data engine for bar counts
        """
        self.portfolio = portfolio
        self.journal = journal
        self.initial_capital = initial_capital
        self.data_engine = data_engine
        
        from .logger import get_logger
        self.logger = get_logger(__name__)
    
    def get_current_status(self) -> Dict:
        """
        Get current trading status.
        
        Returns:
            Dict with current metrics
        """
        stats = self.portfolio.get_statistics()
        journal_stats = self.journal.get_statistics()
        
        current_equity = Decimal(str(stats.get('total_pnl', 0))) + self.initial_capital
        
        return {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            
            # Account
            'initial_capital': float(self.initial_capital),
            'current_equity': float(current_equity),
            'total_return': float(current_equity - self.initial_capital),
            'total_return_pct': float((current_equity - self.initial_capital) / self.initial_capital * 100),
            
            # Positions
            'open_positions': stats['total_positions'],
            'long_positions': stats['long_positions'],
            'short_positions': stats['short_positions'],
            
            # P&L
            'unrealized_pnl': stats['unrealized_pnl'],
            'realized_pnl': stats['realized_pnl'],
            'daily_realized_pnl': stats['daily_realized_pnl'],
            
            # Exposure
            'total_exposure': stats['total_exposure'],
            'net_exposure': stats['net_exposure'],
            
            # Trade stats
            'total_trades': journal_stats.get('total_trades', 0),
            'win_rate': journal_stats.get('win_rate', 0),
            'profit_factor': journal_stats.get('profit_factor', 0),
            
            # Bar counts (for debugging)
            'bar_counts': self._get_bar_counts(),
            
            # Last reconciliation
            'last_reconciliation': stats.get('last_reconciliation')
        }
    
    def _get_bar_counts(self) -> Dict:
        """Get bar counts from data engine for each symbol/timeframe."""
        if not self.data_engine:
            return {}
        
        counts = {}
        try:
            for symbol_ticker, timeframes in self.data_engine.candle_stores.items():
                counts[symbol_ticker] = {}
                for tf, store in timeframes.items():
                    counts[symbol_ticker][tf] = len(store)
        except Exception:
            pass
        
        return counts
    
    def print_dashboard(self) -> None:
        """Print dashboard to console."""
        status = self.get_current_status()
        
        print("\n" + "=" * 60)
        print("TRADING PERFORMANCE DASHBOARD")
        print("=" * 60)
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        print("\n💰 Account Status:")
        print(f"   Initial Capital:  ${status['initial_capital']:,.2f}")
        print(f"   Current Equity:   ${status['current_equity']:,.2f}")
        print(f"   Total Return:     ${status['total_return']:+,.2f} ({status['total_return_pct']:+.2f}%)")
        
        print("\n📊 Positions:")
        print(f"   Open:    {status['open_positions']}")
        print(f"   Long:    {status['long_positions']}")
        print(f"   Short:   {status['short_positions']}")
        
        print("\n💵 P&L:")
        print(f"   Unrealized:  ${status['unrealized_pnl']:+,.2f}")
        print(f"   Realized:    ${status['realized_pnl']:+,.2f}")
        print(f"   Daily:       ${status['daily_realized_pnl']:+,.2f}")
        
        print("\n📈 Exposure:")
        print(f"   Total:  ${status['total_exposure']:,.2f}")
        print(f"   Net:    ${status['net_exposure']:+,.2f}")
        
        print("\n🎯 Trade Statistics:")
        print(f"   Total Trades:   {status['total_trades']}")
        print(f"   Win Rate:       {status['win_rate']:.1f}%")
        print(f"   Profit Factor:  {status['profit_factor']:.2f}")
        
        print("\n" + "=" * 60)
    
    def save_snapshot(self, output_file: str) -> None:
        """Save current status to JSON file."""
        import json
        from pathlib import Path
        
        status = self.get_current_status()
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(status, f, indent=2)
        
        self.logger.info(f"Dashboard snapshot saved to {output_file}")
    
    def get_recent_trades(self, count: int = 10) -> List[Dict]:
        """Get most recent trades."""
        all_trades = self.journal.get_trades()
        
        # Sort by exit time (most recent first)
        sorted_trades = sorted(
            all_trades,
            key=lambda t: t['exit_time'],
            reverse=True
        )
        
        return sorted_trades[:count]
    
    def print_recent_trades(self, count: int = 10) -> None:
        """Print recent trades to console."""
        trades = self.get_recent_trades(count)
        
        if not trades:
            print("\nNo trades yet")
            return
        
        print(f"\n📋 Recent Trades (Last {min(count, len(trades))}):")
        print("-" * 100)
        print(f"{'Symbol':<8} {'Side':<6} {'Entry':<12} {'Exit':<12} {'P&L':<12} {'Duration':<12} {'Strategy':<15}")
        print("-" * 100)
        
        for trade in trades:
            duration_min = trade['duration_seconds'] / 60
            
            print(
                f"{trade['symbol']:<8} "
                f"{trade['side']:<6} "
                f"${trade['entry_price']:<11.2f} "
                f"${trade['exit_price']:<11.2f} "
                f"${trade['realized_pnl']:+11.2f} "
                f"{duration_min:>6.1f}m     "
                f"{trade['strategy']:<15}"
            )
        
        print("-" * 100)
