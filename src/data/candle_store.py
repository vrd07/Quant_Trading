"""
Candle Store - Efficient bar storage using pandas.

Stores bars in memory with fast lookups and slicing.
"""

import pandas as pd
from typing import Optional, List
from datetime import datetime
from decimal import Decimal

from ..core.types import Bar, Symbol
from ..core.exceptions import MissingDataError


class CandleStore:
    """
    Store for OHLCV bars with efficient access.
    
    Uses pandas DataFrame internally for performance.
    """
    
    def __init__(self, symbol: Symbol, timeframe: str, max_bars: int = 5000):
        """
        Initialize candle store.
        
        Args:
            symbol: Symbol for this store
            timeframe: Timeframe (e.g., "1m")
            max_bars: Maximum bars to keep in memory
        """
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_bars = max_bars
        
        # Initialize empty DataFrame
        self.df = pd.DataFrame(columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume'
        ])
        self.df = self.df.astype({
            'timestamp': 'datetime64[ns]',
            'open': 'float64',
            'high': 'float64',
            'low': 'float64',
            'close': 'float64',
            'volume': 'float64'
        })
        
        self.df.set_index('timestamp', inplace=True)
    
    def add_bar(self, bar: Bar) -> None:
        """
        Add a bar to the store.
        
        Args:
            bar: Bar to add
        """
        # Convert Bar to DataFrame row
        row = pd.DataFrame({
            'timestamp': [bar.timestamp],
            'open': [float(bar.open)],
            'high': [float(bar.high)],
            'low': [float(bar.low)],
            'close': [float(bar.close)],
            'volume': [float(bar.volume)]
        })
        row.set_index('timestamp', inplace=True)
        
        # Append to existing data
        self.df = pd.concat([self.df, row])
        
        # Remove duplicates (keep latest)
        self.df = self.df[~self.df.index.duplicated(keep='last')]
        
        # Sort by timestamp
        self.df.sort_index(inplace=True)
        
        # Trim to max_bars
        if len(self.df) > self.max_bars:
            self.df = self.df.iloc[-self.max_bars:]
    
    def add_bars(self, bars: List[Bar]) -> None:
        """Add multiple bars at once."""
        for bar in bars:
            self.add_bar(bar)
    
    def get_bars(
        self,
        count: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Get bars from store.
        
        Args:
            count: Number of most recent bars
            start_time: Filter by start time
            end_time: Filter by end time
        
        Returns:
            DataFrame with OHLCV data
        """
        df = self.df.copy()
        
        # Apply time filters
        if start_time:
            df = df[df.index >= start_time]
        if end_time:
            df = df[df.index <= end_time]
        
        # Apply count limit
        if count and len(df) > count:
            df = df.iloc[-count:]
        
        # Reset index to make timestamp a column
        df = df.reset_index()
        
        return df
    
    def get_latest_bar(self) -> Optional[Bar]:
        """Get most recent bar."""
        if len(self.df) == 0:
            return None
        
        row = self.df.iloc[-1]
        
        return Bar(
            symbol=self.symbol,
            timestamp=row.name,  # Index is timestamp
            open=Decimal(str(row['open'])),
            high=Decimal(str(row['high'])),
            low=Decimal(str(row['low'])),
            close=Decimal(str(row['close'])),
            volume=Decimal(str(row['volume']))
        )
    
    def get_bar_at(self, timestamp: datetime) -> Optional[Bar]:
        """Get bar at specific timestamp."""
        if timestamp not in self.df.index:
            return None
        
        row = self.df.loc[timestamp]
        
        return Bar(
            symbol=self.symbol,
            timestamp=timestamp,
            open=Decimal(str(row['open'])),
            high=Decimal(str(row['high'])),
            low=Decimal(str(row['low'])),
            close=Decimal(str(row['close'])),
            volume=Decimal(str(row['volume']))
        )
    
    def __len__(self) -> int:
        """Number of bars in store."""
        return len(self.df)
    
    def to_csv(self, filepath: str) -> None:
        """Export bars to CSV file."""
        self.df.to_csv(filepath)
    
    def from_csv(self, filepath: str) -> None:
        """Load bars from CSV file."""
        df = pd.read_csv(filepath, parse_dates=['timestamp'])
        df.set_index('timestamp', inplace=True)
        self.df = df
