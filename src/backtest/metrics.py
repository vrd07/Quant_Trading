"""
Performance Metrics - Calculate backtest statistics.

Metrics:
- Sharpe Ratio
- Sortino Ratio
- Max Drawdown
- Win Rate
- Profit Factor
- Expectancy
- Daily win-rate (G1 in backtest.md §1)
- Worst-day R-multiple (G2)
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional


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

    # ------------------------------------------------------------------
    # Daily-level metrics (backtest.md §1 gates G1, G2)
    # ------------------------------------------------------------------
    @staticmethod
    def daily_pnl_from_trades(trades: List[Dict]) -> pd.Series:
        """
        Group closed trades by date (entry timestamp) and sum P&L.

        Returns pd.Series indexed by date, NaN-free, including only days that had
        at least one trade. Days with no trades are excluded (consistent with
        spec §1: gates evaluate "trading days").
        """
        if not trades:
            return pd.Series(dtype=float)
        rows = []
        for t in trades:
            ts = t.get("timestamp") or t.get("entry_time")
            pnl = t.get("pnl", 0.0)
            if ts is None:
                continue
            day = pd.to_datetime(ts).normalize()
            rows.append((day, float(pnl)))
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows, columns=["day", "pnl"])
        return df.groupby("day")["pnl"].sum().sort_index()

    @staticmethod
    def calculate_daily_win_rate(trades: List[Dict]) -> float:
        """
        G1: fraction of trading days that finished net green (pnl > 0).
        Days with exactly zero net P&L count as non-green.
        """
        daily = PerformanceMetrics.daily_pnl_from_trades(trades)
        if daily.empty:
            return 0.0
        return float((daily > 0).sum() / len(daily))

    @staticmethod
    def calculate_worst_day_r(
        trades: List[Dict],
        risk_per_trade_dollars: float,
    ) -> float:
        """
        G2: worst single trading day's net P&L expressed as an R-multiple.

        Per backtest.md §1 the spec definition is:
          "1R = the dollar amount risked on the trade as priced by the
           production PositionSizer at entry. Worst-day floor of -2R = total
           realized + open-risk loss for the day cannot be worse than 2× the
           risk-unit at the start of that day's first losing trade."

        Implementation:
          • Trades that carry an explicit `r_dollars` field (set at entry from
            |entry-stop| × volume × value_per_lot) are used directly.
          • For each trading day, the day's R is the FIRST LOSING TRADE's R
            on that day; if no losing trade, the first trade's R.
          • Trades without `r_dollars` fall back to `risk_per_trade_dollars`
            (the old account-relative approximation, kept for back-compat).

        Returns a NEGATIVE float for losing days, 0.0 if no trades / no R.
        """
        if not trades:
            return 0.0

        from collections import defaultdict
        by_day = defaultdict(list)
        for t in trades:
            ts = t.get("timestamp") or t.get("entry_time")
            if ts is None:
                continue
            ts_p = pd.to_datetime(ts)
            day = ts_p.normalize()
            r = t.get("r_dollars")
            if r is None or r <= 0:
                r = risk_per_trade_dollars
            by_day[day].append((ts_p, float(t.get("pnl", 0.0)), float(r)))
        if not by_day:
            return 0.0

        worst = 0.0
        for day_trades in by_day.values():
            day_trades.sort(key=lambda x: x[0])  # chronological
            day_pnl = sum(p for _, p, _ in day_trades)
            # Spec: "risk-unit at the start of that day's first losing trade."
            first_losing_r = next((r for _, p, r in day_trades if p < 0), None)
            day_r = first_losing_r if first_losing_r is not None else day_trades[0][2]
            if day_r <= 0:
                continue
            ratio = day_pnl / day_r
            if ratio < worst:
                worst = ratio
        return float(worst)

    @staticmethod
    def calculate_trading_days(trades: List[Dict]) -> int:
        """Count of distinct days that had at least one trade."""
        daily = PerformanceMetrics.daily_pnl_from_trades(trades)
        return int(len(daily))
