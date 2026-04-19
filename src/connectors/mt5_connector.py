"""
MT5 Connector - Integration with MT5 File Bridge.

This module wraps the existing file-based MT5 bridge and provides
a clean interface that returns our system's data types.
"""

import sys
import logging
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import time

# Add mt5_bridge to path
sys.path.append(str(Path(__file__).parent.parent.parent / "mt5_bridge"))
from mt5_file_client import MT5FileClient

from ..core.types import Order, Position, Symbol, Tick
from ..core.constants import OrderSide, OrderType, OrderStatus, PositionSide
from ..core.exceptions import (
    MT5ConnectionError,
    OrderRejectedError,
    OrderTimeoutError,
    ConnectionLostError
)

logger = logging.getLogger(__name__)


class MT5Connector:
    """
    Connector to MT5 via file-based bridge.
    
    This wraps the MT5FileClient and converts between file format
    and our trading system types.
    """
    
    def __init__(self, data_dir: Optional[str] = None):
        """
        Initialize MT5 connector.
        
        Args:
            data_dir: MT5 Common/Files directory path
                     If None, auto-detected from MT5FileClient
        """
        logger.info("Initializing MT5Connector with data_dir=%s", data_dir)
        try:
            self.client = MT5FileClient(data_dir=data_dir)
            self.connected = False
            self.last_heartbeat = None
            self.symbols_cache: Dict[str, Symbol] = {}
            self._symbol_map: Dict[str, str] = {}
            # Broker TZ offset: broker_time - real_utc. Detected on connect().
            # Default 0 so mis-bootstrap behavior matches "assume broker is UTC".
            self.broker_offset: timedelta = timedelta(0)
            logger.info("MT5Connector initialized successfully")
            
        except Exception as e:
            logger.error("Failed to initialize MT5 client: %s", e, exc_info=True)
            raise MT5ConnectionError(f"Failed to initialize MT5 client: {e}")
    
    def connect(self) -> bool:
        """
        Connect to MT5 and verify it's responding.
        
        Returns:
            True if connected successfully
            
        Raises:
            MT5ConnectionError if connection fails
        """
        logger.info("Attempting to connect to MT5...")
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                # Test connection with heartbeat
                response = self.client.heartbeat()
                logger.debug("Heartbeat response: %s", response)
                
                if response.get("status") == "ALIVE":
                    self.connected = True
                    self.last_heartbeat = datetime.now(timezone.utc)
                    self._detect_broker_offset()
                    logger.info("Successfully connected to MT5")
                    return True
                else:
                    # Might be reading a response from a previous command on startup
                    logger.warning("Unexpected heartbeat response (attempt %d/%d): %s", attempt+1, max_retries, response)
                    time.sleep(1)
                    
            except Exception as e:
                logger.warning("Connection attempt %d/%d failed: %s", attempt+1, max_retries, e)
                time.sleep(1)

        logger.error("Failed to connect to MT5 after %d attempts", max_retries)
        raise MT5ConnectionError("Failed to connect to MT5 after multiple attempts")
    
    def _detect_broker_offset(self) -> None:
        """
        Read `server_time` from the EA status file and compute broker_time - utc_now,
        rounded to the nearest whole hour. Brokers virtually always run on integer-hour
        offsets; rounding absorbs the ~1s clock skew between EA write and our read.
        On any failure, leave broker_offset at its previous value (default timedelta(0)).
        """
        try:
            status = self.client.get_status()
            server_time_str = status.get("server_time")
            if not server_time_str:
                logger.warning("EA status has no server_time field; broker_offset stays %s", self.broker_offset)
                return
            # MT5 TimeToString with TIME_DATE|TIME_SECONDS → "YYYY.MM.DD HH:MM:SS"
            broker_naive = datetime.strptime(server_time_str, "%Y.%m.%d %H:%M:%S")
            broker_utc_labeled = broker_naive.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            raw_delta = broker_utc_labeled - now_utc
            hours = round(raw_delta.total_seconds() / 3600)
            self.broker_offset = timedelta(hours=hours)
            logger.info(
                "Broker offset detected: %+d hour(s) (server_time=%s, raw_delta=%.1fs)",
                hours, server_time_str, raw_delta.total_seconds(),
            )
        except Exception as e:
            logger.warning("Broker offset detection failed: %s. Keeping %s.", e, self.broker_offset)

    def disconnect(self) -> None:
        """Clean disconnect."""
        logger.info("Disconnecting from MT5")
        self.connected = False
        logger.debug("Disconnected successfully")
    
    def heartbeat(self) -> bool:
        """
        Send heartbeat to check if MT5 is alive.
        
        Returns:
            True if MT5 responding
        """
        logger.debug("Sending heartbeat to MT5")
        try:
            response = self.client.heartbeat()
            
            if response.get("status") == "ALIVE":
                self.last_heartbeat = datetime.now(timezone.utc)
                logger.debug("Heartbeat successful, MT5 is alive")
                return True
            
            logger.warning("Heartbeat failed: status=%s", response.get("status"))
            return False
            
        except Exception as e:
            logger.error("Heartbeat error: %s", e, exc_info=True)
            return False
    
    def get_account_info(self) -> Dict[str, Decimal]:
        """
        Get account information from MT5.
        
        Returns:
            {
                'balance': Decimal,
                'equity': Decimal,
                'margin': Decimal,
                'free_margin': Decimal,
                'margin_level': Decimal
            }
        """
        logger.debug("Requesting account info from MT5")
        try:
            response = self.client.get_account_info()
            logger.debug("Account info response: %s", response)
            
            account_info = {
                'balance': Decimal(str(response.get('balance', 0))),
                'equity': Decimal(str(response.get('equity', 0))),
                'margin': Decimal(str(response.get('margin', 0))),
                'free_margin': Decimal(str(response.get('free_margin', 0))),
                'margin_level': Decimal(str(response.get('margin_level', 0)))
            }
            
            logger.debug("Account info retrieved: balance=%s, equity=%s", 
                        account_info['balance'], account_info['equity'])
            return account_info
            
        except Exception as e:
            logger.error("Failed to get account info: %s", e, exc_info=True)
            raise MT5ConnectionError(f"Failed to get account info: {e}")
    
    def get_positions(self) -> Dict[str, Position]:
        """
        Get all open positions from MT5.
        
        Returns:
            Dict mapping MT5 ticket str to Position object
            (keyed by ticket so TrailingStopManager can pass it to modify_position)
        """
        logger.debug("Requesting positions from MT5")
        try:
            response = self.client.get_positions()
            logger.debug("Positions response: %s", response)
            
            positions = {}
            
            for mt5_pos in response.get('positions', []):
                position = self._convert_mt5_position(mt5_pos)
                # Key by MT5 ticket so the trailing stop manager can call
                # modify_position(position_id=ticket_str) directly
                ticket_str = str(mt5_pos.get('ticket', position.position_id))
                positions[ticket_str] = position
                logger.debug("Converted position: %s %s @ %s (ticket=%s PnL: %s)",
                           position.symbol.ticker, position.side.value,
                           position.entry_price, ticket_str, position.unrealized_pnl)
            
            logger.debug("Retrieved %d positions from MT5", len(positions))
            return positions
            
        except Exception as e:
            logger.error("Failed to get positions: %s", e, exc_info=True)
            raise MT5ConnectionError(f"Failed to get positions: {e}")
    
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[Decimal] = None,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None,
        comment: str = ""
    ) -> Order:
        """
        Place an order on MT5.
        
        Note: MT5FileClient only supports basic market orders.
        SL/TP and price parameters are accepted but may not be applied.
        """
        logger.info("Placing order: %s %s %s", side.value, quantity, symbol)
        
        # Use mapped symbol if available (e.g. BTCUSD -> BTCUSD.x)
        mapped_symbol = self._symbol_map.get(symbol, symbol)
        if mapped_symbol != symbol:
             logger.info("Using mapped symbol for order: %s -> %s", symbol, mapped_symbol)
        
        try:
            # Round SL/TP to 2 decimal places (broker requirement)
            rounded_sl = round(float(stop_loss), 2) if stop_loss else None
            rounded_tp = round(float(take_profit), 2) if take_profit else None
            rounded_price = round(float(price), 2) if price else None
            
            # Enforce minimum stops distance dynamically (John Carmack / Kevin Mitnick rules)
            # Brokers reject SL/TP too close to entry. Read this dynamically from config.
            sym_config = self._get_or_create_symbol(symbol)
            min_stops_distance = float(getattr(sym_config, 'min_stops_distance', 1.0))

            if rounded_price and rounded_sl:
                if abs(rounded_price - rounded_sl) < min_stops_distance:
                    if side == OrderSide.BUY:
                        rounded_sl = round(rounded_price - min_stops_distance, 2)
                    else:
                        rounded_sl = round(rounded_price + min_stops_distance, 2)
                    logger.warning("SL adjusted to meet broker minimum distance: %s", rounded_sl)
            
            if rounded_price and rounded_tp:
                if abs(rounded_price - rounded_tp) < min_stops_distance:
                    if side == OrderSide.BUY:
                        rounded_tp = round(rounded_price + min_stops_distance, 2)
                    else:
                        rounded_tp = round(rounded_price - min_stops_distance, 2)
                    logger.warning("TP adjusted to meet broker minimum distance: %s", rounded_tp)
            
            response = self.client.place_order(
                symbol=mapped_symbol,
                order_type=side.value,
                volume=float(quantity),
                price=rounded_price,
                sl=rounded_sl,
                tp=rounded_tp,
                comment=comment
            )
            logger.debug("Order response: %s", response)
            
            if response.get("status") == "ERROR":
                error_msg = response.get("message", "Order rejected")
                logger.error("Order rejected: %s", error_msg)
                raise OrderRejectedError(error_msg, symbol=symbol, side=side.value)
            
            order = Order(
                symbol=self._get_or_create_symbol(symbol),
                side=side,
                order_type=order_type,
                quantity=quantity,
                price=Decimal(str(response.get('price', 0))) if response.get('price') else None,
                stop_loss=stop_loss,
                take_profit=take_profit,
                status=OrderStatus.SENT,
                sent_at=datetime.now(timezone.utc),
                metadata={
                    'mt5_ticket': response.get('ticket'),
                    'comment': comment
                }
            )
            
            logger.info("Order placed: ticket=%s", order.metadata.get('mt5_ticket'))
            return order
            
        except OrderRejectedError:
            raise
        except Exception as e:
            logger.error("Failed to place order: %s", e, exc_info=True)
            raise OrderTimeoutError(f"Failed to place order: {e}", symbol=symbol)
    
    def close_position(self, position_id: str) -> Dict[str, Any]:
        """Close a position on MT5."""
        logger.info("Closing position: %s", position_id)
        try:
            response = self.client.close_position(position_id)
            logger.debug("Close position response: %s", response)
            
            result = {
                'status': response.get('status'),
                'realized_pnl': Decimal(str(response.get('realized_pnl', 0)))
            }
            
            logger.info("Position closed: %s", result['status'])
            return result
            
        except Exception as e:
            logger.error("Failed to close position: %s", e, exc_info=True)
            raise MT5ConnectionError(f"Failed to close position: {e}")
    
    def modify_position(
        self,
        position_id: str,
        stop_loss: Optional[Decimal] = None,
        take_profit: Optional[Decimal] = None
    ) -> bool:
        """
        Modify SL/TP of an existing position via MODIFY_ORDER command.

        Uses the EA's HandleModifyOrder (TRADE_ACTION_SLTP) to update
        stop loss and/or take profit without placing a new order.

        Args:
            position_id: MT5 ticket (string)
            stop_loss:   New stop loss price (None = keep current)
            take_profit: New take profit price (None = keep current, NOT zero it out)

        Returns:
            True if modification was accepted by the EA, False otherwise
        """
        logger.info(
            "Modifying position %s: sl=%s tp=%s", position_id, stop_loss, take_profit
        )
        try:
            command: dict = {
                "command": "MODIFY_ORDER",
                "ticket": int(position_id),
                "sl": float(stop_loss) if stop_loss is not None else 0,
            }
            # CRITICAL: Only include 'tp' when the caller explicitly provides one.
            # Sending tp=0 would wipe out the existing take-profit on the broker.
            # When take_profit is None, omit the key entirely so the EA keeps
            # whatever TP is already set.
            if take_profit is not None:
                command["tp"] = float(take_profit)

            response = self.client.send_command(command)
            logger.debug("Modify response: %s", response)

            if response.get("status") == "SUCCESS":
                logger.info(
                    "Position %s modified: new_sl=%s new_tp=%s",
                    position_id,
                    response.get("new_sl"),
                    response.get("new_tp"),
                )
                return True
            else:
                logger.warning(
                    "Modify position failed: %s", response.get("message", "Unknown")
                )
                return False

        except Exception as e:
            logger.error("Failed to modify position %s: %s", position_id, e, exc_info=True)
            return False


    def get_closed_positions(self, minutes: int = 1440) -> List[Dict]:
        """
        Get recently closed positions (history).
        
        Args:
            minutes: Lookback period in minutes
            
        Returns:
            List of dicts with closed position details (ticket, profit, price)
        """
        logger.debug("Requesting history for last %d minutes", minutes)
        try:
            response = self.client.get_history(minutes=minutes)
            
            if response.get("status") == "ERROR":
                logger.error("Failed to get history: %s", response.get("message"))
                return []
            
            deals = response.get("deals", [])
            logger.debug("Retrieved %d historical deals", len(deals))
            return deals
            
        except Exception as e:
            logger.error("Error getting closed positions: %s", e, exc_info=True)
            return []
    
    def get_bars(self, symbol: str, timeframe: str = "M1", count: int = 500) -> List[Dict]:
        """
        Fetch historical bars from MT5 via CopyRates (geohot: own your stack).

        Args:
            symbol: Instrument ticker
            timeframe: MT5 timeframe (M1/M5/M15/H1/H4/D1)
            count: Number of bars

        Returns:
            List of {time, open, high, low, close, volume} dicts
        """
        mapped = self._symbol_map.get(symbol, symbol)
        logger.debug("Requesting %d %s bars for %s", count, timeframe, mapped)
        try:
            response = self.client.get_bars(symbol=mapped, timeframe=timeframe, count=count)
            if response.get("status") == "ERROR":
                # Broker may use a suffixed name (e.g. XAUUSD.pro). Discover via
                # status quotes and retry once — preload runs before any tick
                # has populated _symbol_map through the normal path.
                if mapped == symbol:
                    discovered = self._discover_broker_symbol(symbol)
                    if discovered and discovered != symbol:
                        logger.info("Retrying GET_BARS with discovered symbol: %s -> %s", symbol, discovered)
                        response = self.client.get_bars(symbol=discovered, timeframe=timeframe, count=count)
                        if response.get("status") != "ERROR":
                            return response.get("bars", [])
                logger.warning("GET_BARS failed: %s", response.get("message"))
                return []
            return response.get("bars", [])
        except Exception as e:
            logger.warning("GET_BARS error for %s: %s", symbol, e)
            return []

    def _discover_broker_symbol(self, symbol: str) -> Optional[str]:
        """Find the broker's actual symbol name (handles suffixes like .pro, .i, etc.)."""
        try:
            status = self.client.get_status()
            quotes = status.get('quotes', {})
            for broker_sym in quotes:
                if broker_sym.startswith(symbol) or symbol.startswith(broker_sym):
                    self._symbol_map[symbol] = broker_sym
                    logger.info("Symbol mapped via discovery: %s -> %s", symbol, broker_sym)
                    return broker_sym
        except Exception as e:
            logger.debug("Symbol discovery failed for %s: %s", symbol, e)
        return None

    def get_current_tick(self, symbol: str) -> Optional[Tick]:
        """Get current tick for a symbol."""
        logger.debug("Getting current tick for %s", symbol)
        try:
            status = self.client.get_status()
            
            # 1. Check for quotes object (Multi-Symbol Support)
            quotes = status.get('quotes', {})
            
            # Log available symbols on first encounter for debugging
            if not hasattr(self, '_logged_quotes_keys'):
                self._logged_quotes_keys = True
                if quotes:
                    logger.info("Available quote symbols from EA: %s", list(quotes.keys()))
                else:
                    logger.warning("No quotes in status file. Keys: %s", list(status.keys()))
            
            # Use cached symbol mapping first (consistent with is_market_open)
            quote = None
            matched_symbol = None
            mapped = self._symbol_map.get(symbol, symbol)

            if mapped in quotes:
                quote = quotes[mapped]
                matched_symbol = mapped
            elif symbol in quotes:
                quote = quotes[symbol]
                matched_symbol = symbol
            else:
                # Fuzzy match: find any quote symbol that starts with our symbol
                # (handles suffixes like BTCUSDi, BTCUSD.i, BTCUSD.raw, etc.)
                for broker_sym in quotes:
                    if broker_sym.startswith(symbol) or symbol.startswith(broker_sym):
                        quote = quotes[broker_sym]
                        matched_symbol = broker_sym
                        if not hasattr(self, '_symbol_map'):
                            self._symbol_map = {}
                        if symbol not in self._symbol_map:
                            self._symbol_map[symbol] = matched_symbol
                            logger.info("Symbol mapped: %s -> %s (broker name)", symbol, matched_symbol)
                        break
            
            if quote:
                tick = Tick(
                    symbol=self._get_or_create_symbol(symbol),
                    timestamp=datetime.now(timezone.utc),
                    bid=Decimal(str(quote.get('bid', 0))),
                    ask=Decimal(str(quote.get('ask', 0))),
                    last=Decimal(str((quote.get('bid', 0) + quote.get('ask', 0)) / 2)),
                    volume=Decimal("0")
                )
                logger.debug("Tick (Multi): %s bid=%s ask=%s", symbol, tick.bid, tick.ask)
                return tick

            # 2. Fallback to single symbol check (Backward Compatibility)
            status_sym = status.get('symbol', '')
            if status_sym == symbol or status_sym.startswith(symbol) or symbol.startswith(status_sym):
                tick = Tick(
                    symbol=self._get_or_create_symbol(symbol),
                    timestamp=datetime.now(timezone.utc),
                    bid=Decimal(str(status.get('bid', 0))),
                    ask=Decimal(str(status.get('ask', 0))),
                    last=Decimal(str((status.get('bid', 0) + status.get('ask', 0)) / 2)),
                    volume=Decimal("0")
                )
                logger.debug("Tick (Single): %s bid=%s ask=%s", symbol, tick.bid, tick.ask)
                return tick
            
            return None
            
        except Exception as e:
            logger.error("Failed to get tick: %s", e, exc_info=True)
            return None
    
    def _convert_mt5_position(self, mt5_pos: Dict) -> Position:
        """Convert MT5 position dict to Position object."""
        symbol = self._get_or_create_symbol(mt5_pos['symbol'])
        
        pos_type = mt5_pos.get('type', mt5_pos.get('side', ''))
        # EA sends integer type: 0 = BUY (LONG), 1 = SELL (SHORT)
        # Also handle legacy string format 'BUY'/'LONG'
        if pos_type in (0, '0', 'BUY', 'LONG') or mt5_pos.get('side') == 'LONG':
            side = PositionSide.LONG
        else:
            side = PositionSide.SHORT
        
        # Extract strategy from comment (format: "strategy|orderId" or "Order-id")
        # Positions without the bot comment format are tagged 'manual' (not 'unknown')
        # so analytics can separate human trades from bot trades.
        comment = mt5_pos.get('comment', '')
        strategy = 'manual'   # default: assume manual unless we see the bot format
        order_id_prefix = ''
        if '|' in comment:
            parts = comment.split('|', 1)
            strategy = parts[0] or 'manual'
            order_id_prefix = parts[1] if len(parts) > 1 else ''
        elif comment.startswith('Order-'):
            # Legacy bot format without strategy prefix
            order_id_prefix = comment.replace('Order-', '')
            strategy = 'unknown'  # bot order but strategy name missing
        
        position = Position(
            symbol=symbol,
            side=side,
            quantity=Decimal(str(mt5_pos.get('volume', 0))),
            entry_price=Decimal(str(mt5_pos.get('price_open', 0))),
            current_price=Decimal(str(mt5_pos.get('price_current', 0))),
            stop_loss=Decimal(str(mt5_pos['sl'])) if mt5_pos.get('sl') else None,
            take_profit=Decimal(str(mt5_pos['tp'])) if mt5_pos.get('tp') else None,
            unrealized_pnl=Decimal(str(mt5_pos.get('profit', 0))),
            metadata={
                'mt5_ticket': mt5_pos.get('ticket'),
                'mt5_magic': mt5_pos.get('magic'),
                'mt5_comment': comment,
                'strategy': strategy,
                'order_id_prefix': order_id_prefix
            }
        )
        
        return position
    
    def _get_or_create_symbol(self, ticker: str) -> Symbol:
        """Get symbol from cache or create from config, falling back to defaults."""
        if ticker not in self.symbols_cache:
            # Try to read lot constraints from system config
            sym_cfg = {}
            if hasattr(self, '_system_config') and self._system_config:
                # Strip known suffixes (like .w) to find base symbol in config
                base_ticker = ticker.split('.')[0] if '.' in ticker else ticker
                sym_cfg = self._system_config.get('symbols', {}).get(ticker) or \
                          self._system_config.get('symbols', {}).get(base_ticker, {})
            self.symbols_cache[ticker] = Symbol(
                ticker=ticker,
                exchange="MT5",
                pip_value=Decimal(str(sym_cfg.get('pip_value', '0.01'))),
                min_lot=Decimal(str(sym_cfg.get('min_lot', '0.01'))),
                max_lot=Decimal(str(sym_cfg.get('max_lot', '100.0'))),
                lot_step=Decimal(str(sym_cfg.get('lot_step', '0.01'))),
                value_per_lot=Decimal(str(sym_cfg.get('value_per_lot', '1.0'))),
                commission_per_lot=Decimal(str(sym_cfg.get('commission_per_lot', '0.0'))),
                max_spread=Decimal(str(sym_cfg.get('max_spread', '999.0'))),
                min_stops_distance=Decimal(str(sym_cfg.get('min_stops_distance', '1.0'))),
            )
        return self.symbols_cache[ticker]
    
    def is_market_open(self, symbol: str, max_age_seconds: int = 120) -> bool:
        """
        Check if the market for a given symbol is open.
        
        George Hotz rule: Don't guess, use system ground truth.
        A market is considered dead/closed if we haven't received a live tick 
        in the last `max_age_seconds` (assuming EA pushes ticks continuously).
        """
        try:
            status = self.client.get_status()
            
            quotes = status.get('quotes', {})
            mapped_symbol = self._symbol_map.get(symbol, symbol)
            
            if mapped_symbol in quotes:
                quote = quotes[mapped_symbol]
                # If we have an MT5 server timestamp in the quote, check it
                if 'time_ms' in quote:
                    # EA puts milliseconds since epoch
                    server_age = datetime.now(timezone.utc).timestamp() - (quote['time_ms'] / 1000.0)
                    if server_age > max_age_seconds:
                        return False
            
            # Additional logic: MT5 EA might export `market_open: bool` globally or per symbol
            if 'market_open' in status:
                return bool(status['market_open'])

            return True  # Optimistic fallback if EA doesn't support timestamps
            
        except Exception as e:
            logger.warning("Failed to check if market is open: %s", e)
            return False
    
    def check_connection_health(self) -> bool:
        """Check if connection is healthy."""
        if not self.connected:
            return False
        
        if self.last_heartbeat is None:
            return False
        
        age = (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()
        
        if age > 30:
            raise ConnectionLostError(f"Heartbeat stale: {age:.0f} seconds")
        
        return True


# Singleton instance
_mt5_connector_instance: Optional[MT5Connector] = None

def get_mt5_connector(data_dir: Optional[str] = None) -> MT5Connector:
    """Get or create singleton MT5Connector instance."""
    global _mt5_connector_instance
    
    if _mt5_connector_instance is None:
        _mt5_connector_instance = MT5Connector(data_dir=data_dir)
    
    return _mt5_connector_instance
