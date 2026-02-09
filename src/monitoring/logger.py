"""Structured logging for the trading system."""

import logging
import sys
from typing import Any, Optional
from datetime import datetime, timezone


class TradingLogger:
    """
    Structured logger for trading system with keyword argument support.
    """
    
    def __init__(self, name: str, level: int = logging.INFO):
        """
        Initialize trading logger.
        
        Args:
            name: Logger name (typically __name__)
            level: Logging level
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        
        # Add handler if not already configured
        if not self.logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(level)
            formatter = logging.Formatter(
                '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def _format_message(self, msg: str, **kwargs) -> str:
        """Format message with keyword arguments."""
        if kwargs:
            extra = ' | '.join(f'{k}={v}' for k, v in kwargs.items())
            return f"{msg} | {extra}"
        return msg
    
    def debug(self, msg: str, **kwargs) -> None:
        """Log debug message."""
        self.logger.debug(self._format_message(msg, **kwargs))
    
    def info(self, msg: str, **kwargs) -> None:
        """Log info message."""
        self.logger.info(self._format_message(msg, **kwargs))
    
    def warning(self, msg: str, **kwargs) -> None:
        """Log warning message."""
        self.logger.warning(self._format_message(msg, **kwargs))
    
    def error(self, msg: str, exc_info: bool = False, **kwargs) -> None:
        """Log error message."""
        self.logger.error(self._format_message(msg, **kwargs), exc_info=exc_info)
    
    def critical(self, msg: str, **kwargs) -> None:
        """Log critical message."""
        self.logger.critical(self._format_message(msg, **kwargs))


_loggers = {}


def get_logger(name: str) -> TradingLogger:
    """
    Get or create a trading logger.
    
    Args:
        name: Logger name (typically __name__)
    
    Returns:
        TradingLogger instance
    """
    if name not in _loggers:
        _loggers[name] = TradingLogger(name)
    return _loggers[name]
