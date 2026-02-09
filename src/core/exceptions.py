"""Exception hierarchy for the trading system.

This module defines all custom exceptions used throughout the trading system.
All exceptions inherit from TradingSystemError for easy catching and handling.
"""

from typing import Any, Dict


class TradingSystemError(Exception):
    """Base exception for all trading system errors.
    
    All custom exceptions in the trading system inherit from this class,
    allowing for easy catching of any trading system related errors.
    """
    
    def __init__(self, message: str, **context: Any):
        """Initialize the exception with a message and optional context.
        
        Args:
            message: Error message describing what went wrong
            **context: Additional context information for logging and debugging
        """
        super().__init__(message)
        self.context: Dict[str, Any] = context
    
    def __str__(self) -> str:
        """Return string representation including context."""
        if self.context:
            ctx = ', '.join(f'{k}={v}' for k, v in self.context.items())
            return f"{super().__str__()} [{ctx}]"
        return super().__str__()


# ============================================================================
# Configuration Exceptions
# ============================================================================

class InvalidConfigError(TradingSystemError):
    """Raised when configuration contains invalid values.
    
    This exception is raised when configuration values fail validation,
    such as negative numbers for positive-only fields, or values outside
    acceptable ranges.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class MissingConfigError(TradingSystemError):
    """Raised when required configuration is missing.
    
    This exception is raised when mandatory configuration keys or sections
    are not found in the configuration file or environment variables.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class ConfigValidationError(TradingSystemError):
    """Raised when configuration fails schema validation.
    
    This exception is raised when the configuration structure or content
    does not match the expected schema, such as wrong types or missing
    required sections.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


# ============================================================================
# Connection Exceptions
# ============================================================================

class TradingConnectionError(TradingSystemError):
    """Base class for connection-related errors.
    
    This exception serves as the base for all connection-related errors.
    Named TradingConnectionError to avoid conflict with built-in ConnectionError.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class MT5ConnectionError(TradingConnectionError):
    """Raised when connection to MetaTrader 5 fails.
    
    This exception is raised when the system cannot establish or maintain
    a connection to the MT5 platform, including login failures and
    initialization errors.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class ZmqConnectionError(TradingConnectionError):
    """Raised when ZMQ connection fails.
    
    This exception is raised when the ZMQ socket cannot connect, bind,
    or communicate with the remote endpoint.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class HeartbeatTimeoutError(TradingConnectionError):
    """Raised when heartbeat signal times out.
    
    This exception is raised when no heartbeat is received from a connected
    component within the expected timeout period, indicating a potential
    connection loss or component failure.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class ConnectionLostError(TradingConnectionError):
    """Raised when an established connection is lost.
    
    This exception is raised when a previously working connection drops
    unexpectedly, requiring reconnection or system recovery.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


# ============================================================================
# Order Exceptions
# ============================================================================

class OrderError(TradingSystemError):
    """Base class for order-related errors.
    
    This exception serves as the base for all order-related errors,
    including order placement, modification, and execution issues.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class OrderRejectedError(OrderError):
    """Raised when an order is rejected by the broker.
    
    This exception is raised when the broker rejects an order due to
    insufficient margin, invalid parameters, market conditions, or
    other broker-specific reasons.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class OrderTimeoutError(OrderError):
    """Raised when order placement or execution times out.
    
    This exception is raised when an order operation takes longer than
    the configured timeout period, which may indicate communication
    issues or broker processing delays.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class InvalidOrderError(OrderError):
    """Raised when order parameters are invalid.
    
    This exception is raised when order parameters fail validation before
    being sent to the broker, such as invalid lot sizes, prices, or
    order types.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class DuplicateOrderError(OrderError):
    """Raised when attempting to place a duplicate order.
    
    This exception is raised when an order with the same signal ID or
    characteristics already exists, preventing duplicate positions.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


# ============================================================================
# Risk Exceptions
# ============================================================================

class RiskLimitExceededError(TradingSystemError):
    """Raised when a risk limit is exceeded.
    
    This exception serves as the base for specific risk limit violations.
    It is raised when any general risk limit defined in the system is
    breached.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class DailyLossLimitError(RiskLimitExceededError):
    """Raised when daily loss limit is exceeded.
    
    This exception is raised when the accumulated losses for the current
    trading day exceed the configured daily loss limit, triggering trading
    suspension for the day.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class DrawdownLimitError(RiskLimitExceededError):
    """Raised when maximum drawdown limit is exceeded.
    
    This exception is raised when the account drawdown from the peak
    exceeds the configured maximum drawdown threshold, requiring
    intervention or trading halt.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class ExposureLimitError(RiskLimitExceededError):
    """Raised when market exposure limit is exceeded.
    
    This exception is raised when the total market exposure (notional value
    of all positions) exceeds the configured limit, preventing additional
    positions from being opened.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class PositionSizeLimitError(RiskLimitExceededError):
    """Raised when position size limit is exceeded.
    
    This exception is raised when an order's position size would exceed
    the maximum allowed position size for a symbol or the account.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class KillSwitchActiveError(RiskLimitExceededError):
    """Raised when the kill switch is active.
    
    This exception is raised when attempting to place orders while the
    emergency kill switch is active, which completely stops all trading
    activity due to critical risk conditions.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


# ============================================================================
# Data Exceptions
# ============================================================================

class DataValidationError(TradingSystemError):
    """Raised when data fails validation.
    
    This exception is raised when incoming data (market data, signals, etc.)
    fails validation checks, such as missing required fields, invalid types,
    or values outside acceptable ranges.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class StaleDataError(TradingSystemError):
    """Raised when data is too old to be used.
    
    This exception is raised when the timestamp of received data is older
    than the configured maximum staleness threshold, indicating potential
    data feed issues or delays.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class MissingDataError(TradingSystemError):
    """Raised when required data is missing.
    
    This exception is raised when essential data needed for trading decisions
    or calculations is not available, such as missing price bars or
    incomplete market data.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class InvalidBarError(TradingSystemError):
    """Raised when a price bar is invalid.
    
    This exception is raised when a price bar has invalid values, such as
    high < low, zero volume when expected, or other inconsistencies that
    indicate data corruption or errors.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


# ============================================================================
# State Exceptions
# ============================================================================

class StateError(TradingSystemError):
    """Base class for state management errors.
    
    This exception serves as the base for all state-related errors,
    including state loading, saving, synchronization, and corruption.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class StateReconciliationError(StateError):
    """Raised when state reconciliation fails.
    
    This exception is raised when the system cannot reconcile its internal
    state with the broker's state, indicating discrepancies in positions,
    orders, or account information.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class StateCorruptedError(StateError):
    """Raised when state data is corrupted.
    
    This exception is raised when loaded state data is corrupted, invalid,
    or cannot be parsed, requiring state reconstruction or manual intervention.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class StateSaveError(StateError):
    """Raised when saving state fails.
    
    This exception is raised when the system cannot persist state to disk,
    database, or other storage, which may lead to state loss on restart.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)


class StateLoadError(StateError):
    """Raised when loading state fails.
    
    This exception is raised when the system cannot load previously saved
    state from storage, potentially due to file system issues, missing
    files, or permission problems.
    """
    
    def __init__(self, message: str, **context: Any):
        super().__init__(message, **context)
