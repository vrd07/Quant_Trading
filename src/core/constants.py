"""System-wide constants and enumerations for the trading system.

This module defines all constants, enumerations, and default values used
throughout the trading system. These values provide sensible defaults and
standardize string values across the codebase.
"""

from decimal import Decimal
from enum import Enum


# ============================================================================
# Enumerations
# ============================================================================

class OrderSide(str, Enum):
    """Enumeration of order sides (direction).
    
    Defines whether an order is buying or selling an instrument.
    """
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Enumeration of order types.
    
    Defines the execution type for orders:
    - MARKET: Execute immediately at current market price
    - LIMIT: Execute only at specified price or better
    - STOP: Trigger market order when stop price is reached
    - STOP_LIMIT: Trigger limit order when stop price is reached
    """
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, Enum):
    """Enumeration of order status states.
    
    Tracks the lifecycle of an order from creation to final state:
    - PENDING: Created but not yet sent to broker
    - SENT: Sent to broker but not yet acknowledged
    - ACCEPTED: Acknowledged by broker and active
    - PARTIALLY_FILLED: Some quantity executed, remainder still active
    - FILLED: Completely executed
    - REJECTED: Rejected by broker
    - CANCELLED: Cancelled by user or system
    - EXPIRED: Expired due to time-in-force settings
    """
    PENDING = "PENDING"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PositionSide(str, Enum):
    """Enumeration of position sides.
    
    Defines the direction of a position:
    - LONG: Bought the instrument (profit from price increase)
    - SHORT: Sold the instrument (profit from price decrease)
    - FLAT: No position (neutral)
    """
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class MarketRegime(str, Enum):
    """Enumeration of market regime types.
    
    Defines the current market behavior pattern:
    - TREND: Directional market with sustained price movement
    - RANGE: Sideways market with price oscillating in a range
    - UNKNOWN: Regime cannot be determined or is transitioning
    """
    TREND = "TREND"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class TradingSession(str, Enum):
    """Enumeration of trading sessions.
    
    Defines major forex/commodity trading sessions based on market hours:
    - ASIAN: Tokyo/Asian markets (01:00-09:00 UTC)
    - LONDON: London/European markets (08:00-16:00 UTC)
    - NEW_YORK: New York/US markets (13:00-21:00 UTC)
    - OVERLAP: Active overlap between sessions (high liquidity)
    - OFF_HOURS: Outside major trading sessions
    """
    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP = "OVERLAP"
    OFF_HOURS = "OFF_HOURS"


class Environment(str, Enum):
    """Enumeration of trading environments.
    
    Defines the operational environment:
    - DEV: Development environment for testing and debugging
    - PAPER: Paper trading with simulated execution
    - LIVE: Live trading with real money
    """
    DEV = "dev"
    PAPER = "paper"
    LIVE = "live"


# ============================================================================
# Timeout Constants
# ============================================================================

ORDER_TIMEOUT_SECONDS: int = 30
"""Maximum time to wait for order acknowledgment from broker.

If an order is not acknowledged within this timeout, it is considered failed
and appropriate error handling is triggered.
"""

HEARTBEAT_INTERVAL_SECONDS: int = 10
"""Interval between heartbeat signals sent to connected components.

Components send heartbeats at this interval to indicate they are alive and
functioning properly.
"""

HEARTBEAT_TIMEOUT_SECONDS: int = 30
"""Maximum time to wait for a heartbeat before considering connection lost.

If no heartbeat is received within this period, the connection is considered
dead and reconnection procedures are initiated.
"""

RECONNECT_DELAY_SECONDS: int = 5
"""Delay between reconnection attempts.

After a connection failure, the system waits this many seconds before
attempting to reconnect. This prevents excessive reconnection attempts.
"""

MAX_RECONNECT_ATTEMPTS: int = 10
"""Maximum number of reconnection attempts before giving up.

After this many failed reconnection attempts, the system stops trying and
raises an error that requires manual intervention.
"""


# ============================================================================
# Risk Management Defaults
# ============================================================================

DEFAULT_RISK_PER_TRADE_PCT: Decimal = Decimal("0.0025")
"""Default risk per trade as percentage of account balance (0.25%).

This is the default amount of account balance to risk on a single trade
if not specified in configuration. Used for position sizing calculations.
"""

DEFAULT_MAX_DAILY_LOSS_PCT: Decimal = Decimal("0.02")
"""Default maximum daily loss as percentage of account balance (2%).

This is the default daily loss limit. If losses exceed this percentage,
all trading is halted for the day to prevent further losses.
"""

DEFAULT_MAX_DRAWDOWN_PCT: Decimal = Decimal("0.10")
"""Default maximum drawdown as percentage of peak balance (10%).

This is the default maximum allowed drawdown from peak equity. If drawdown
exceeds this threshold, trading is halted and manual review is required.
"""

DEFAULT_MAX_POSITIONS: int = 3
"""Default maximum number of concurrent open positions.

This limits the number of positions that can be open at the same time,
helping to manage risk and avoid over-leverage.
"""


# ============================================================================
# Data Validation Constants
# ============================================================================

MAX_TICK_AGE_SECONDS: int = 60
"""Maximum age of tick data before considered stale (1 minute).

Tick data older than this is rejected to ensure trading decisions are based
on current market conditions. Stale data may indicate data feed issues.
"""

MAX_BAR_AGE_SECONDS: int = 3600
"""Maximum age of bar data before considered stale (1 hour).

Bar data older than this is rejected for real-time trading. Historical
analysis may use older data, but live trading requires recent bars.
"""

SPIKE_THRESHOLD_STD: float = 5.0
"""Standard deviation threshold for detecting price spikes.

Price movements exceeding this many standard deviations are flagged as
potential spikes or data errors and trigger additional validation.
"""


# ============================================================================
# State Management Constants
# ============================================================================

STATE_SAVE_INTERVAL_SECONDS: int = 60
"""Interval between automatic state saves (1 minute).

The system state (positions, orders, account info) is automatically saved
at this interval to prevent data loss in case of crashes.
"""

MAX_STATE_BACKUPS: int = 10
"""Maximum number of state backup files to retain.

Old state backups are automatically deleted when this limit is exceeded,
keeping only the most recent backups for disaster recovery.
"""


# ============================================================================
# Trading Session Times (UTC)
# ============================================================================

ASIAN_SESSION_START: int = 1
"""Start hour of Asian trading session in UTC (01:00).

Asian session begins when Tokyo markets open, providing the first major
liquidity period of the trading day.
"""

ASIAN_SESSION_END: int = 9
"""End hour of Asian trading session in UTC (09:00).

Asian session concludes as Tokyo markets wind down and before full
European market opening.
"""

LONDON_SESSION_START: int = 8
"""Start hour of London trading session in UTC (08:00).

London session begins when European markets open, often providing
the highest liquidity and volatility of the day.
"""

LONDON_SESSION_END: int = 16
"""End hour of London trading session in UTC (16:00).

London session concludes when European markets close, though some
overlap with US markets continues.
"""

NY_SESSION_START: int = 13
"""Start hour of New York trading session in UTC (13:00).

New York session begins when US markets open, overlapping with the
end of the London session for maximum liquidity.
"""

NY_SESSION_END: int = 21
"""End hour of New York trading session in UTC (21:00).

New York session concludes when US markets close, marking the end
of major market hours for the day.
"""
