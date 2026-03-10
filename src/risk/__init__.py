"""Risk management module."""

from .risk_engine import RiskEngine
from .position_sizer import PositionSizer
from .kill_switch import KillSwitch
from .circuit_breaker import CircuitBreaker
from .drawdown_tracker import DrawdownTracker
from .exposure_manager import ExposureManager
# Kelly criterion — optional position sizing utility (not used in live fixed_lot mode,
# but available for backtesting / paper trading with position_sizing.method: kelly)
from .kelly import kelly_criterion, fixed_fractional

__all__ = [
    "RiskEngine",
    "PositionSizer",
    "KillSwitch",
    "CircuitBreaker",
    "DrawdownTracker",
    "ExposureManager",
    "kelly_criterion",
    "fixed_fractional",
]
