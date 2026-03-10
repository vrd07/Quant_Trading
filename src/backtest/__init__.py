"""
Backtest module - Event-driven backtesting system.

Exports:
- BacktestEngine: Main backtest orchestrator
- BacktestResult: Results dataclass
- SimulatedBroker: Simulated order execution
- PerformanceMetrics: Performance tracking
"""

from .backtest_engine import BacktestEngine, BacktestResult
from .simulation import SimulatedBroker
from .metrics import PerformanceMetrics

__all__ = [
    'BacktestEngine',
    'BacktestResult',
    'SimulatedBroker',
    'PerformanceMetrics'
]
