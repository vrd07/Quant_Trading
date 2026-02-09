"""
Metrics Tracker - Track system metrics over time.

Exports metrics for:
- Performance analysis
- System monitoring
- Alerting
"""

from typing import Dict, List
from datetime import datetime, timezone
from pathlib import Path
import json
from collections import deque


class MetricsTracker:
    """
    Track system metrics over time.
    
    Metrics tracked:
    - Equity curve
    - Daily P&L
    - Position count
    - Order flow
    - Execution quality
    """
    
    def __init__(self, max_history: int = 10000):
        """
        Initialize metrics tracker.
        
        Args:
            max_history: Max metrics to keep in memory
        """
        self.max_history = max_history
        
        # Metrics buffers
        self.equity_history: deque = deque(maxlen=max_history)
        self.pnl_history: deque = deque(maxlen=max_history)
        self.position_count_history: deque = deque(maxlen=max_history)
        
        from .logger import get_logger
        self.logger = get_logger(__name__)
    
    def record_equity(self, equity: float) -> None:
        """Record equity snapshot."""
        self.equity_history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'equity': equity
        })
    
    def record_pnl(self, pnl: float) -> None:
        """Record P&L."""
        self.pnl_history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'pnl': pnl
        })
    
    def record_positions(self, count: int) -> None:
        """Record position count."""
        self.position_count_history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'count': count
        })
    
    def export_metrics(self, output_dir: str = "data/metrics") -> None:
        """Export all metrics to files."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Export equity
        if self.equity_history:
            equity_file = output_path / f"equity_{timestamp}.json"
            with open(equity_file, 'w') as f:
                json.dump(list(self.equity_history), f, indent=2)
        
        # Export P&L
        if self.pnl_history:
            pnl_file = output_path / f"pnl_{timestamp}.json"
            with open(pnl_file, 'w') as f:
                json.dump(list(self.pnl_history), f, indent=2)
        
        self.logger.info(f"Metrics exported to {output_dir}")
    
    def get_current_metrics(self) -> Dict:
        """Get latest metrics."""
        return {
            'equity': self.equity_history[-1] if self.equity_history else None,
            'pnl': self.pnl_history[-1] if self.pnl_history else None,
            'positions': self.position_count_history[-1] if self.position_count_history else None
        }
