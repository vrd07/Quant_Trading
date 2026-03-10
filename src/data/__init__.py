"""
Data Layer - Market data management and processing.

This module provides the complete data infrastructure for the trading system:
- Real-time tick collection and validation
- Multi-timeframe bar building
- Efficient storage and retrieval
- Trading session tagging
- Data quality validation

Main Components:
    DataEngine: Central orchestrator for all data operations
    CandleStore: Efficient OHLCV bar storage using pandas
    TickHandler: Real-time tick buffering with bounded memory
    SessionTagger: Trading session identification
    DataValidator: Data quality validation and spike detection
"""

from .data_engine import DataEngine, BarBuilder
from .candle_store import CandleStore
from .tick_handler import TickHandler
from .session_tagger import SessionTagger
from .data_validator import DataValidator

__all__ = [
    "DataEngine",
    "BarBuilder",
    "CandleStore",
    "TickHandler",
    "SessionTagger",
    "DataValidator",
]
