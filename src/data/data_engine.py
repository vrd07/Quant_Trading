"""
Data Engine - Central data management and orchestration.

Responsibilities:
1. Collect ticks from MT5
2. Build bars from ticks (multiple timeframes)
3. Store bars efficiently
4. Calculate indicators on demand
5. Tag trading sessions
6. Validate data quality
"""

from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from datetime import datetime, timedelta, timezone
import pandas as pd
from collections import defaultdict

from ..connectors.mt5_connector import MT5Connector
from ..core.types import Tick, Bar, Symbol
from ..core.constants import TradingSession
from ..core.exceptions import DataValidationError, MissingDataError

from .candle_store import CandleStore
from .tick_handler import TickHandler
from .session_tagger import SessionTagger
from .data_validator import DataValidator

import logging
logger = logging.getLogger(__name__)


class DataEngine:
    """
    Central data engine for the trading system.
    
    Manages tick collection, bar building, and data storage.
    """
    
    def __init__(
        self,
        connector: MT5Connector,
        symbols: List[Symbol],
        timeframes: List[str] = None,
        tick_buffer_size: int = 10000,
        bar_buffer_size: int = 5000
    ):
        """
        Initialize data engine.
        
        Args:
            connector: MT5 connector instance
            symbols: List of symbols to track
            timeframes: List of timeframe strings (e.g., ["1m", "5m", "1h"])
            tick_buffer_size: Max ticks to keep in memory
            bar_buffer_size: Max bars to keep per timeframe
        """
        self.connector = connector
        self.symbols = {s.ticker: s for s in symbols}
        self.timeframes = timeframes or ["1m", "5m", "15m", "1h", "4h", "1d"]
        
        # Components
        self.tick_handler = TickHandler(buffer_size=tick_buffer_size)
        self.session_tagger = SessionTagger()
        self.data_validator = DataValidator()
        
        # Candle stores (one per symbol per timeframe)
        self.candle_stores: Dict[str, Dict[str, CandleStore]] = defaultdict(dict)
        for symbol in symbols:
            for tf in self.timeframes:
                self.candle_stores[symbol.ticker][tf] = CandleStore(
                    symbol=symbol,
                    timeframe=tf,
                    max_bars=bar_buffer_size
                )
        
        # State tracking
        self.last_tick_time: Dict[str, datetime] = {}
        self.bar_builders: Dict[str, Dict[str, BarBuilder]] = defaultdict(dict)
        
        # Initialize bar builders
        for symbol in symbols:
            for tf in self.timeframes:
                self.bar_builders[symbol.ticker][tf] = BarBuilder(
                    symbol=symbol,
                    timeframe=tf
                )
    
    def preload_historical_bars(self, bars_count: int = 2000) -> Dict[str, int]:
        """
        Preload historical bars on startup.

        geohot: try local CSV cache first (written by the system itself),
        then fall back to yfinance. We don't trust a single external dep
        for the critical startup path.

        bars_count=2000 is required for kalman_regime:
          min_bars = rv_ma_window(100) + rv_window(20) + 10 = 130 * 15m bars
          130 15m bars × 15 min/bar = 1950 1m bars minimum.

        Args:
            bars_count: Number of 1m bars to preload (default 2000)

        Returns:
            Dict of {symbol: bars_loaded}
        """
        results = {}

        for symbol_ticker, symbol in self.symbols.items():
            # geohot priority chain: MT5 CopyRates → local cache → yfinance
            # 1. MT5 direct (own your stack — zero external dependency)
            loaded = self._preload_from_mt5(symbol_ticker, symbol, bars_count)
            if loaded > 0:
                results[symbol_ticker] = loaded
                continue

            # 2. Local CSV cache (written by the system itself)
            loaded = self._preload_from_cache(symbol_ticker, symbol, bars_count)
            if loaded > 0:
                results[symbol_ticker] = loaded
                continue

            # 3. yfinance (external, may be slow or rate-limited)
            loaded = self._preload_from_yfinance(symbol_ticker, symbol, bars_count)
            if loaded > 0:
                results[symbol_ticker] = loaded

        return results

    def _preload_from_mt5(self, symbol_ticker: str, symbol, bars_count: int) -> int:
        """
        geohot: fetch bars directly from MT5 via CopyRates.
        Own the stack — no yfinance, no external deps.
        """
        try:
            bars_data = self.connector.get_bars(
                symbol=symbol_ticker, timeframe="M1", count=bars_count
            )
            if not bars_data:
                return 0

            loaded = 0
            for b in bars_data:
                ts = datetime.fromtimestamp(int(b['time']), tz=timezone.utc)
                bar = Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=Decimal(str(b['open'])),
                    high=Decimal(str(b['high'])),
                    low=Decimal(str(b['low'])),
                    close=Decimal(str(b['close'])),
                    volume=Decimal(str(b.get('volume', 0)))
                )
                self.candle_stores[symbol_ticker]['1m'].add_bar(bar)
                loaded += 1

            if loaded > 0:
                logger.info(f"Preloaded {loaded} 1m bars for {symbol_ticker} from MT5 CopyRates")
                self._build_higher_tf_from_1m(symbol_ticker)
            return loaded

        except Exception as e:
            logger.debug(f"MT5 CopyRates preload failed for {symbol_ticker}: {e}")
            return 0

    def _preload_from_cache(self, symbol_ticker: str, symbol, bars_count: int) -> int:
        """
        geohot: load from local CSV cache written by the system itself.
        Zero network dependency. File written by _run_nightly_classifier.
        """
        from pathlib import Path
        import os

        base = symbol_ticker.split('.')[0] if '.' in symbol_ticker else symbol_ticker
        candidates = [
            Path(f"data/logs/candle_store_{symbol_ticker}_1m.csv"),
            Path(f"data/logs/candle_store_{base}_1m.csv"),
            Path(f"data/logs/candle_store_{symbol_ticker}_5m.csv"),
            Path(f"data/logs/candle_store_{base}_5m.csv"),
        ]

        for csv_path in candidates:
            if not csv_path.exists():
                continue
            try:
                import pandas as _pd
                df = _pd.read_csv(csv_path, parse_dates=['timestamp'])
                if df.empty or len(df) < 50:
                    continue

                if len(df) > bars_count:
                    df = df.iloc[-bars_count:]

                # Determine which timeframe this CSV represents
                tf = '5m' if '5m' in str(csv_path) else '1m'
                loaded = 0
                for _, row in df.iterrows():
                    ts = row['timestamp']
                    if hasattr(ts, 'to_pydatetime'):
                        ts = ts.to_pydatetime()
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)

                    bar = Bar(
                        symbol=symbol,
                        timestamp=ts,
                        open=Decimal(str(round(float(row['open']), 6))),
                        high=Decimal(str(round(float(row['high']), 6))),
                        low=Decimal(str(round(float(row['low']), 6))),
                        close=Decimal(str(round(float(row['close']), 6))),
                        volume=Decimal(str(int(row.get('volume', 0))))
                    )
                    self.candle_stores[symbol_ticker][tf].add_bar(bar)
                    loaded += 1

                logger.info(
                    f"Preloaded {loaded} {tf} bars for {symbol_ticker} from cache: {csv_path}"
                )
                if tf == '1m':
                    self._build_higher_tf_from_1m(symbol_ticker)
                else:
                    # Cache-only 5m: still need to populate 15m/1h/etc for
                    # higher-TF strategies like kalman_regime.
                    self._build_higher_tf_from_base(symbol_ticker, base_tf=tf)
                return loaded

            except Exception as e:
                logger.debug(f"Cache preload failed for {csv_path}: {e}")

        return 0

    def _preload_from_yfinance(self, symbol_ticker: str, symbol, bars_count: int) -> int:
        """Fallback: fetch from yfinance when no local cache exists."""
        yf_symbol_map = {
            'BTCUSD': 'BTC-USD',
            'ETHUSD': 'ETH-USD',
            'XAUUSD': 'GC=F',
            'EURUSD': 'EURUSD=X',
            'US30': '^DJI',
            'USOIL': 'CL=F',
        }

        base_ticker = symbol_ticker.split('.')[0] if '.' in symbol_ticker else symbol_ticker
        yf_ticker = yf_symbol_map.get(symbol_ticker) or yf_symbol_map.get(base_ticker)

        if not yf_ticker:
            logger.warning(f"No yfinance mapping for {symbol_ticker}, skipping preload")
            return 0

        try:
            import yfinance as yf

            ticker = yf.Ticker(yf_ticker)
            hist = ticker.history(period="5d", interval="1m")

            if hist.empty:
                logger.warning(f"No yfinance data for {symbol_ticker}")
                return 0

            if len(hist) > bars_count:
                hist = hist.iloc[-bars_count:]

            loaded = 0
            for idx, row in hist.iterrows():
                ts = idx.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                bar = Bar(
                    symbol=symbol,
                    timestamp=ts,
                    open=Decimal(str(round(row['Open'], 6))),
                    high=Decimal(str(round(row['High'], 6))),
                    low=Decimal(str(round(row['Low'], 6))),
                    close=Decimal(str(round(row['Close'], 6))),
                    volume=Decimal(str(int(row.get('Volume', 0))))
                )
                self.candle_stores[symbol_ticker]['1m'].add_bar(bar)
                loaded += 1

            logger.info(
                f"Preloaded {loaded} bars for {symbol_ticker} from yfinance "
                f"({hist.index[0]} to {hist.index[-1]})"
            )
            self._build_higher_tf_from_1m(symbol_ticker)
            return loaded

        except ImportError:
            logger.warning("yfinance not installed, skipping preload")
            return 0
        except Exception as e:
            logger.error(f"Failed to preload {symbol_ticker} from yfinance: {e}", exc_info=True)
            return 0
    
    def _build_higher_tf_from_base(self, symbol_ticker: str, base_tf: str) -> None:
        """Resample a loaded base timeframe up to all larger timeframes."""
        base_store = self.candle_stores[symbol_ticker].get(base_tf)
        if not base_store:
            return
        bars_base = base_store.get_bars()
        if bars_base.empty:
            return

        symbol = self.symbols[symbol_ticker]
        base_seconds = self._get_timeframe_seconds(base_tf)

        for tf in self.timeframes:
            if tf == base_tf:
                continue
            # Only resample UP (can't build 1m from 5m).
            if self._get_timeframe_seconds(tf) <= base_seconds:
                continue

            resample_rule = {
                '5m': '5min', '15m': '15min', '1h': '1h', '4h': '4h', '1d': '1D'
            }.get(tf)
            if not resample_rule:
                continue

            try:
                df = bars_base.set_index('timestamp')
                resampled = df.resample(resample_rule).agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'
                }).dropna()

                for idx, row in resampled.iterrows():
                    ts = idx.to_pydatetime()
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    bar = Bar(
                        symbol=symbol,
                        timestamp=ts,
                        open=Decimal(str(round(row['open'], 6))),
                        high=Decimal(str(round(row['high'], 6))),
                        low=Decimal(str(round(row['low'], 6))),
                        close=Decimal(str(round(row['close'], 6))),
                        volume=Decimal(str(int(row['volume'])))
                    )
                    self.candle_stores[symbol_ticker][tf].add_bar(bar)

                htf_count = len(self.candle_stores[symbol_ticker][tf])
                if htf_count > 0:
                    logger.info(f"Built {htf_count} {tf} bars for {symbol_ticker} from {base_tf} data")
            except Exception as e:
                logger.warning(f"Failed to build {tf} bars for {symbol_ticker} from {base_tf}: {e}")

    def _build_higher_tf_from_1m(self, symbol_ticker: str) -> None:
        """Build higher timeframe bars from preloaded 1m data."""
        bars_1m = self.candle_stores[symbol_ticker]['1m'].get_bars()
        if bars_1m.empty:
            return
        
        symbol = self.symbols[symbol_ticker]
        
        for tf in self.timeframes:
            if tf == '1m':
                continue
            
            tf_seconds = self._get_timeframe_seconds(tf)
            resample_rule = {
                '5m': '5min', '15m': '15min', '1h': '1h', '4h': '4h', '1d': '1D'
            }.get(tf)
            
            if not resample_rule:
                continue
            
            try:
                df = bars_1m.set_index('timestamp')
                resampled = df.resample(resample_rule).agg({
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum'
                }).dropna()
                
                for idx, row in resampled.iterrows():
                    ts = idx.to_pydatetime()
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    
                    bar = Bar(
                        symbol=symbol,
                        timestamp=ts,
                        open=Decimal(str(round(row['open'], 6))),
                        high=Decimal(str(round(row['high'], 6))),
                        low=Decimal(str(round(row['low'], 6))),
                        close=Decimal(str(round(row['close'], 6))),
                        volume=Decimal(str(int(row['volume'])))
                    )
                    self.candle_stores[symbol_ticker][tf].add_bar(bar)
                
                htf_count = len(self.candle_stores[symbol_ticker][tf])
                if htf_count > 0:
                    logger.info(f"Built {htf_count} {tf} bars for {symbol_ticker} from 1m data")
                    
            except Exception as e:
                logger.warning(f"Failed to build {tf} bars for {symbol_ticker}: {e}")
    
    def on_tick(self, tick: Tick) -> None:
        """
        Process incoming tick.
        
        This is called every time a new tick arrives from MT5.
        
        Args:
            tick: New tick data
        """
        # Validate tick
        if not self.data_validator.validate_tick(tick, self.last_tick_time.get(tick.symbol.ticker)):
            return  # Skip invalid ticks
        
        # Store tick
        self.tick_handler.add_tick(tick)
        self.last_tick_time[tick.symbol.ticker] = tick.timestamp
        
        # Update all bar builders for this symbol
        for tf in self.timeframes:
            builder = self.bar_builders[tick.symbol.ticker][tf]
            bar = builder.update(tick)
            
            if bar:  # New bar completed
                # Validate bar
                if self.data_validator.validate_bar(bar):
                    # Tag session
                    session = self.session_tagger.get_session(bar.timestamp)
                    bar.metadata = bar.metadata or {}
                    bar.metadata['session'] = session.value
                    
                    # Store bar
                    self.candle_stores[tick.symbol.ticker][tf].add_bar(bar)
                    
                    # Log bar completion for visibility
                    store_len = len(self.candle_stores[tick.symbol.ticker][tf])
                    logger.info(
                        f"Bar completed: {tick.symbol.ticker} {tf} "
                        f"O={bar.open} H={bar.high} L={bar.low} C={bar.close} "
                        f"[{store_len} bars in store]"
                    )

    
    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        count: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        Get historical bars for a symbol.
        
        Args:
            symbol: Symbol ticker
            timeframe: Timeframe (e.g., "1m", "5m")
            count: Number of bars to return (most recent)
            start_time: Start time filter
            end_time: End time filter
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if symbol not in self.candle_stores:
            raise MissingDataError(f"No data for symbol: {symbol}")
        
        if timeframe not in self.candle_stores[symbol]:
            raise MissingDataError(f"No {timeframe} data for {symbol}")
        
        store = self.candle_stores[symbol][timeframe]
        return store.get_bars(count=count, start_time=start_time, end_time=end_time)
    
    def get_current_bar(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """
        Get the current (incomplete) bar being built.
        
        Args:
            symbol: Symbol ticker
            timeframe: Timeframe
        
        Returns:
            Current Bar object or None
        """
        if symbol in self.bar_builders and timeframe in self.bar_builders[symbol]:
            return self.bar_builders[symbol][timeframe].current_bar
        return None
    
    def get_latest_tick(self, symbol: str) -> Optional[Tick]:
        """Get most recent tick for a symbol."""
        return self.tick_handler.get_latest_tick(symbol)
    
    def get_session(self, timestamp: datetime) -> TradingSession:
        """Get trading session for a timestamp."""
        return self.session_tagger.get_session(timestamp)
    
    def update_from_connector(self) -> int:
        """
        Update all symbols from MT5 connector.
        
        Fetches latest tick for each symbol and processes it.
        
        Returns:
            Number of symbols updated
        """
        updated = 0
        
        for symbol_ticker in self.symbols.keys():
            tick = self.connector.get_current_tick(symbol_ticker)
            
            if tick:
                self.on_tick(tick)
                updated += 1
        
        return updated
    
    def get_data_status(self) -> Dict[str, Dict]:
        """
        Get status of data for all symbols.
        
        Returns:
            {
                'XAUUSD': {
                    '1m': {'bars': 1000, 'latest': datetime, 'stale': False},
                    '5m': {'bars': 500, 'latest': datetime, 'stale': False}
                }
            }
        """
        status = {}
        
        for symbol_ticker, timeframes in self.candle_stores.items():
            status[symbol_ticker] = {}
            
            for tf, store in timeframes.items():
                bars = store.get_bars(count=1)
                
                if not bars.empty:
                    latest = pd.to_datetime(bars['timestamp'].iloc[-1])
                    age = (datetime.now(timezone.utc) - latest).total_seconds()
                    
                    status[symbol_ticker][tf] = {
                        'bars': len(store),
                        'latest': latest,
                        'age_seconds': age,
                        'stale': age > self._get_timeframe_seconds(tf) * 2
                    }
                else:
                    status[symbol_ticker][tf] = {
                        'bars': 0,
                        'latest': None,
                        'stale': True
                    }
        
        return status
    
    def _get_timeframe_seconds(self, timeframe: str) -> int:
        """Convert timeframe string to seconds."""
        mapping = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400
        }
        return mapping.get(timeframe, 60)


class BarBuilder:
    """
    Builds bars from ticks for a specific timeframe.
    
    Accumulates ticks and emits completed bars.
    """
    
    def __init__(self, symbol: Symbol, timeframe: str):
        self.symbol = symbol
        self.timeframe = timeframe
        self.current_bar: Optional[Bar] = None
        self.bar_start_time: Optional[datetime] = None
    
    def update(self, tick: Tick) -> Optional[Bar]:
        """
        Update with new tick.
        
        Args:
            tick: New tick data
        
        Returns:
            Completed Bar if bar closed, None otherwise
        """
        bar_period = self._get_bar_period()
        tick_bar_time = self._align_to_period(tick.timestamp, bar_period)
        
        # First tick or new bar period started
        if self.bar_start_time is None or tick_bar_time > self.bar_start_time:
            completed_bar = self.current_bar
            
            # Start new bar
            self.bar_start_time = tick_bar_time
            self.current_bar = Bar(
                symbol=self.symbol,
                timestamp=tick_bar_time,
                open=tick.mid,
                high=tick.mid,
                low=tick.mid,
                close=tick.mid,
                volume=tick.volume
            )
            
            return completed_bar  # Return previous completed bar
        
        # Update current bar
        if self.current_bar:
            self.current_bar.high = max(self.current_bar.high, tick.mid)
            self.current_bar.low = min(self.current_bar.low, tick.mid)
            self.current_bar.close = tick.mid
            self.current_bar.volume += tick.volume
        
        return None  # Bar not yet complete
    
    def _get_bar_period(self) -> timedelta:
        """Get bar period as timedelta."""
        periods = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1)
        }
        return periods.get(self.timeframe, timedelta(minutes=1))
    
    def _align_to_period(self, timestamp: datetime, period: timedelta) -> datetime:
        """Align timestamp to bar period start."""
        # Convert to seconds since epoch
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        seconds = (timestamp - epoch).total_seconds()
        period_seconds = period.total_seconds()
        
        # Round down to period start
        aligned_seconds = int(seconds // period_seconds) * period_seconds
        
        return epoch + timedelta(seconds=aligned_seconds)
