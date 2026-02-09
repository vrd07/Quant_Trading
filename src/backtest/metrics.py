"""
Performance Metrics - Calculate backtest statistics.

Metrics:
- Sharpe Ratio
- Sortino Ratio
- Max Drawdown
- Win Rate
- Profit Factor
- Expectancy
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple


class PerformanceMetrics:
    """Calculate performance metrics for backtest."""
    
    def __init__(self):
        self.trades: List[Dict] = []
        self.equity_history: List[Tuple[pd.Timestamp, float]] = []
    
    def add_trade(self, trade: Dict) -> None:
        """Add trade to history."""
        self.trades.append(trade)
    
    def update_equity(self, timestamp: pd.Timestamp, equity: float) -> None:
        """Update equity curve."""
        self.equity_history.append((timestamp, equity))
    
    def get_trades(self) -> List[Dict]:
        """Get all trades."""
        return self.trades
    
    def get_equity_curve(self) -> pd.Series:
        """Get equity curve as pandas Series."""
        if not self.equity_history:
            return pd.Series()
        
        df = pd.DataFrame(self.equity_history, columns=['timestamp', 'equity'])
        df = df.set_index('timestamp')
        return df['equity']
    
    def calculate_sharpe_ratio(
        self,
        returns: pd.Series,
        risk_free_rate: float = 0.0,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate annualized Sharpe ratio.
        
        Sharpe = (Mean Return - Risk Free Rate) / Std Dev of Returns
        """
        if len(returns) < 2:
            return 0.0
        
        excess_returns = returns - risk_free_rate
        
        if excess_returns.std() == 0:
            return 0.0
        
        sharpe = excess_returns.mean() / excess_returns.std()
        sharpe_annual = sharpe * np.sqrt(periods_per_year)
        
        return float(sharpe_annual)
    
    def calculate_sortino_ratio(
        self,
        returns: pd.Series,
        risk_free_rate: float = 0.0,
        periods_per_year: int = 252
    ) -> float:
        """
        Calculate Sortino ratio (penalizes only downside volatility).
        """
        if len(returns) < 2:
            return 0.0
        
        excess_returns = returns - risk_free_rate
        downside_returns = returns[returns < 0]
        
        if len(downside_returns) == 0:
            return float('inf')
        
        downside_std = downside_returns.std()
        
        if downside_std == 0:
            return 0.0
        
        sortino = excess_returns.mean() / downside_std
        sortino_annual = sortino * np.sqrt(periods_per_year)
        
        return float(sortino_annual)
    
    def calculate_max_drawdown(self, equity_curve: pd.Series) -> Tuple[float, float]:
        """
        Calculate maximum drawdown.
        
        Returns:
            (max_drawdown_value, max_drawdown_pct)
        """
        if len(equity_curve) < 2:
            return 0.0, 0.0
        
        # Calculate running maximum
        running_max = equity_curve.expanding().max()
        
        # Calculate drawdown
        drawdown = equity_curve - running_max
        max_dd = drawdown.min()
        
        # Calculate drawdown percentage
        max_dd_pct = (max_dd / running_max[drawdown.idxmin()]) * 100 if max_dd != 0 else 0
        
        return float(max_dd), float(max_dd_pct)
    
    def reset(self) -> None:
        """Reset metrics."""
        self.trades = []
        self.equity_history = []
