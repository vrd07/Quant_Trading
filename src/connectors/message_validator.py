"""
Message Validator for MT5 Data.

Validates all incoming data from MT5 to ensure:
1. Required fields are present
2. Types are correct
3. Values are within reasonable ranges
4. No injection or corruption
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional
from datetime import datetime

from ..core.exceptions import DataValidationError


logger = logging.getLogger(__name__)


class MT5MessageValidator:
    """Validates messages from MT5 file bridge."""
    
    # Reasonable limits for validation
    MAX_PRICE = 1_000_000.0  # Maximum reasonable price
    MAX_VOLUME = 1000.0  # Maximum lot size
    MAX_SPREAD_PCT = 0.10  # Maximum spread percentage (10%)
    MAX_LEVERAGE = 1000  # Maximum reasonable leverage
    MAX_STRING_LENGTH = 1000  # Maximum string field length
    
    @staticmethod
    def validate_heartbeat(data: Dict[str, Any]) -> None:
        """
        Validate HEARTBEAT response.
        
        Args:
            data: Response from MT5 heartbeat
            
        Raises:
            DataValidationError if invalid
        """
        if 'status' not in data:
            raise DataValidationError(
                "Heartbeat response missing 'status' field",
                data_type="heartbeat"
            )
        
        status = data['status']
        if not isinstance(status, str):
            raise DataValidationError(
                f"Heartbeat status must be string, got {type(status)}",
                data_type="heartbeat"
            )
        
        if status not in ['ALIVE', 'ERROR']:
            logger.warning("Unexpected heartbeat status: %s", status)
        
        logger.debug("Heartbeat validation passed: status=%s", status)
    
    @staticmethod
    def validate_account_info(data: Dict[str, Any]) -> None:
        """
        Validate GET_ACCOUNT_INFO response.
        
        Args:
            data: Account info response from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating account info: %s", data)
        
        required_fields = ['balance', 'equity', 'margin']
        
        for field in required_fields:
            if field not in data:
                raise DataValidationError(
                    f"Missing required field: {field}",
                    data_type="account_info",
                    received_fields=list(data.keys())
                )
        
        # Validate numeric fields
        numeric_fields = ['balance', 'equity', 'margin', 'free_margin', 'margin_level']
        
        for field in numeric_fields:
            if field in data:
                try:
                    value = float(data[field])
                    
                    # Balance, equity, and margins cannot be negative
                    if field in ['balance', 'equity', 'margin', 'free_margin']:
                        if value < 0:
                            raise DataValidationError(
                                f"{field} cannot be negative: {value}",
                                data_type="account_info",
                                field=field
                            )
                    
                    # Margin level cannot be negative
                    if field == 'margin_level' and value < 0:
                        raise DataValidationError(
                            f"Margin level cannot be negative: {value}",
                            data_type="account_info"
                        )
                    
                    # Sanity check: values shouldn't be astronomically high
                    if abs(value) > 1_000_000_000:  # 1 billion
                        logger.warning(
                            "Suspiciously large account value: %s = %.2f",
                            field, value
                        )
                    
                except (ValueError, TypeError) as e:
                    raise DataValidationError(
                        f"Invalid {field} value: {data[field]} (type: {type(data[field])})",
                        data_type="account_info",
                        field=field,
                        error=str(e)
                    )
        
        # Validate account name/number if present
        if 'account' in data:
            account = str(data['account'])
            if len(account) > MT5MessageValidator.MAX_STRING_LENGTH:
                raise DataValidationError(
                    f"Account string too long: {len(account)} chars",
                    data_type="account_info"
                )
        
        logger.debug("Account info validation passed")
    
    @staticmethod
    def validate_position(pos: Dict[str, Any]) -> None:
        """
        Validate position data from MT5.
        
        Args:
            pos: Position dictionary from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating position: %s", pos)
        
        required_fields = ['symbol', 'type', 'volume', 'price_open']
        
        for field in required_fields:
            if field not in pos:
                raise DataValidationError(
                    f"Position missing field: {field}",
                    data_type="position",
                    received_fields=list(pos.keys())
                )
        
        # Validate symbol
        symbol = str(pos['symbol'])
        if not symbol or len(symbol) > 50:
            raise DataValidationError(
                f"Invalid symbol: '{symbol}'",
                data_type="position"
            )
        
        # Validate position type
        pos_type = str(pos['type'])
        if pos_type not in ['BUY', 'SELL', 'LONG', 'SHORT']:
            raise DataValidationError(
                f"Invalid position type: {pos_type}",
                data_type="position",
                symbol=symbol
            )
        
        # Validate volume
        try:
            volume = float(pos['volume'])
            if volume <= 0:
                raise DataValidationError(
                    f"Invalid position volume: {volume}",
                    data_type="position",
                    symbol=symbol
                )
            
            if volume > MT5MessageValidator.MAX_VOLUME:
                raise DataValidationError(
                    f"Position volume too large: {volume}",
                    data_type="position",
                    symbol=symbol
                )
        except (ValueError, TypeError) as e:
            raise DataValidationError(
                f"Invalid volume: {pos['volume']}",
                data_type="position",
                symbol=symbol,
                error=str(e)
            )
        
        # Validate prices
        price_fields = ['price_open', 'price_current']
        for price_field in price_fields:
            if price_field in pos:
                try:
                    price = float(pos[price_field])
                    if price <= 0:
                        raise DataValidationError(
                            f"Invalid {price_field}: {price}",
                            data_type="position",
                            symbol=symbol
                        )
                    
                    if price > MT5MessageValidator.MAX_PRICE:
                        raise DataValidationError(
                            f"{price_field} too high: {price}",
                            data_type="position",
                            symbol=symbol
                        )
                except (ValueError, TypeError) as e:
                    raise DataValidationError(
                        f"Invalid {price_field}: {pos[price_field]}",
                        data_type="position",
                        symbol=symbol,
                        error=str(e)
                    )
        
        # Validate stop loss / take profit if present
        for sl_tp_field in ['sl', 'tp', 'stop_loss', 'take_profit']:
            if sl_tp_field in pos and pos[sl_tp_field] is not None:
                try:
                    sl_tp = float(pos[sl_tp_field])
                    if sl_tp != 0:  # 0 means no SL/TP
                        if sl_tp < 0 or sl_tp > MT5MessageValidator.MAX_PRICE:
                            raise DataValidationError(
                                f"Invalid {sl_tp_field}: {sl_tp}",
                                data_type="position",
                                symbol=symbol
                            )
                except (ValueError, TypeError) as e:
                    raise DataValidationError(
                        f"Invalid {sl_tp_field}: {pos[sl_tp_field]}",
                        data_type="position",
                        symbol=symbol,
                        error=str(e)
                    )
        
        # Validate profit if present
        if 'profit' in pos:
            try:
                profit = float(pos['profit'])
                # Profit can be negative (loss) or positive
                # Just check it's a reasonable number
                if abs(profit) > 10_000_000:  # 10 million
                    logger.warning(
                        "Suspiciously large profit/loss: %.2f for %s",
                        profit, symbol
                    )
            except (ValueError, TypeError) as e:
                raise DataValidationError(
                    f"Invalid profit value: {pos['profit']}",
                    data_type="position",
                    symbol=symbol,
                    error=str(e)
                )
        
        # Validate ticket if present
        if 'ticket' in pos:
            try:
                ticket = int(pos['ticket'])
                if ticket <= 0:
                    raise DataValidationError(
                        f"Invalid ticket number: {ticket}",
                        data_type="position",
                        symbol=symbol
                    )
            except (ValueError, TypeError) as e:
                raise DataValidationError(
                    f"Invalid ticket: {pos['ticket']}",
                    data_type="position",
                    symbol=symbol,
                    error=str(e)
                )
        
        logger.debug("Position validation passed for %s", symbol)
    
    @staticmethod
    def validate_positions_response(data: Dict[str, Any]) -> None:
        """
        Validate GET_POSITIONS response.
        
        Args:
            data: Response containing list of positions
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating positions response")
        
        if 'positions' not in data:
            raise DataValidationError(
                "Positions response missing 'positions' field",
                data_type="positions_response"
            )
        
        positions = data['positions']
        if not isinstance(positions, list):
            raise DataValidationError(
                f"Positions must be a list, got {type(positions)}",
                data_type="positions_response"
            )
        
        # Validate each position
        for i, pos in enumerate(positions):
            try:
                MT5MessageValidator.validate_position(pos)
            except DataValidationError as e:
                # Re-raise with position index
                raise DataValidationError(
                    f"Invalid position at index {i}: {e}",
                    data_type="positions_response",
                    position_index=i
                ) from e
        
        logger.debug("Positions response validation passed (%d positions)", len(positions))
    
    @staticmethod
    def validate_order_response(response: Dict[str, Any]) -> None:
        """
        Validate PLACE_ORDER response.
        
        Args:
            response: Order placement response from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating order response: %s", response)
        
        if 'status' not in response:
            raise DataValidationError(
                "Order response missing 'status' field",
                data_type="order_response",
                received_fields=list(response.keys())
            )
        
        status = str(response['status'])
        
        if status == 'ERROR':
            # Error response is valid, but needs 'message'
            if 'message' not in response:
                raise DataValidationError(
                    "Error response missing 'message' field",
                    data_type="order_response"
                )
            
            message = str(response['message'])
            if len(message) > MT5MessageValidator.MAX_STRING_LENGTH:
                raise DataValidationError(
                    f"Error message too long: {len(message)} chars",
                    data_type="order_response"
                )
        
        elif status == 'SUCCESS':
            # Success response should have order details
            if 'order_id' not in response and 'ticket' not in response:
                logger.warning(
                    "Order success response missing order_id/ticket"
                )
            
            # Validate price if present
            if 'price' in response and response['price'] is not None:
                try:
                    price = float(response['price'])
                    if price <= 0 or price > MT5MessageValidator.MAX_PRICE:
                        raise DataValidationError(
                            f"Invalid order price: {price}",
                            data_type="order_response"
                        )
                except (ValueError, TypeError) as e:
                    raise DataValidationError(
                        f"Invalid price in response: {response['price']}",
                        data_type="order_response",
                        error=str(e)
                    )
        
        else:
            logger.warning("Unexpected order status: %s", status)
        
        logger.debug("Order response validation passed: status=%s", status)
    
    @staticmethod
    def validate_tick(tick: Dict[str, Any]) -> None:
        """
        Validate tick data.
        
        Args:
            tick: Tick data from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating tick: %s", tick)
        
        required_fields = ['symbol', 'bid', 'ask']
        
        for field in required_fields:
            if field not in tick:
                raise DataValidationError(
                    f"Tick missing field: {field}",
                    data_type="tick",
                    received_fields=list(tick.keys())
                )
        
        symbol = str(tick['symbol'])
        if not symbol or len(symbol) > 50:
            raise DataValidationError(
                f"Invalid symbol in tick: '{symbol}'",
                data_type="tick"
            )
        
        # Validate bid/ask
        try:
            bid = float(tick['bid'])
            ask = float(tick['ask'])
            
            if bid <= 0 or ask <= 0:
                raise DataValidationError(
                    f"Invalid prices: bid={bid}, ask={ask}",
                    data_type="tick",
                    symbol=symbol
                )
            
            if bid > MT5MessageValidator.MAX_PRICE or ask > MT5MessageValidator.MAX_PRICE:
                raise DataValidationError(
                    f"Prices too high: bid={bid}, ask={ask}",
                    data_type="tick",
                    symbol=symbol
                )
            
            if ask < bid:
                raise DataValidationError(
                    f"Ask < Bid: bid={bid}, ask={ask}",
                    data_type="tick",
                    symbol=symbol
                )
            
            # Check for unreasonable spread (> 10%)
            spread_pct = (ask - bid) / bid
            if spread_pct > MT5MessageValidator.MAX_SPREAD_PCT:
                raise DataValidationError(
                    f"Unreasonable spread: {spread_pct:.2%}",
                    data_type="tick",
                    symbol=symbol,
                    bid=bid,
                    ask=ask
                )
        
        except (ValueError, TypeError) as e:
            raise DataValidationError(
                f"Invalid tick prices: bid={tick.get('bid')}, ask={tick.get('ask')}",
                data_type="tick",
                symbol=symbol,
                error=str(e)
            )
        
        # Validate volume if present
        if 'volume' in tick and tick['volume'] is not None:
            try:
                volume = float(tick['volume'])
                if volume < 0:
                    raise DataValidationError(
                        f"Negative tick volume: {volume}",
                        data_type="tick",
                        symbol=symbol
                    )
            except (ValueError, TypeError) as e:
                raise DataValidationError(
                    f"Invalid tick volume: {tick['volume']}",
                    data_type="tick",
                    symbol=symbol,
                    error=str(e)
                )
        
        # Validate timestamp if present
        if 'timestamp' in tick:
            timestamp = tick['timestamp']
            if isinstance(timestamp, str):
                # Try to parse ISO format
                try:
                    datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError as e:
                    raise DataValidationError(
                        f"Invalid timestamp format: {timestamp}",
                        data_type="tick",
                        symbol=symbol,
                        error=str(e)
                    )
            elif isinstance(timestamp, (int, float)):
                # Unix timestamp - should be reasonable
                if timestamp < 0 or timestamp > 2_000_000_000:  # Year ~2033
                    raise DataValidationError(
                        f"Unreasonable timestamp: {timestamp}",
                        data_type="tick",
                        symbol=symbol
                    )
        
        logger.debug("Tick validation passed for %s: bid=%s, ask=%s", symbol, bid, ask)
    
    @staticmethod
    def validate_bar(bar: Dict[str, Any]) -> None:
        """
        Validate OHLCV bar data.
        
        Args:
            bar: Bar/candlestick data from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating bar: %s", bar)
        
        required_fields = ['symbol', 'open', 'high', 'low', 'close']
        
        for field in required_fields:
            if field not in bar:
                raise DataValidationError(
                    f"Bar missing field: {field}",
                    data_type="bar",
                    received_fields=list(bar.keys())
                )
        
        symbol = str(bar['symbol'])
        
        # Validate OHLC prices
        try:
            open_price = float(bar['open'])
            high = float(bar['high'])
            low = float(bar['low'])
            close = float(bar['close'])
            
            # All prices must be positive
            if any(p <= 0 for p in [open_price, high, low, close]):
                raise DataValidationError(
                    f"Invalid bar prices (must be positive): O={open_price}, H={high}, L={low}, C={close}",
                    data_type="bar",
                    symbol=symbol
                )
            
            # High must be >= max(open, close)
            if high < max(open_price, close):
                raise DataValidationError(
                    f"Invalid bar: high ({high}) < max(open, close)",
                    data_type="bar",
                    symbol=symbol
                )
            
            # Low must be <= min(open, close)
            if low > min(open_price, close):
                raise DataValidationError(
                    f"Invalid bar: low ({low}) > min(open, close)",
                    data_type="bar",
                    symbol=symbol
                )
            
            # High must be >= low
            if high < low:
                raise DataValidationError(
                    f"Invalid bar: high ({high}) < low ({low})",
                    data_type="bar",
                    symbol=symbol
                )
        
        except (ValueError, TypeError) as e:
            raise DataValidationError(
                f"Invalid bar prices",
                data_type="bar",
                symbol=symbol,
                error=str(e)
            )
        
        # Validate volume if present
        if 'volume' in bar:
            try:
                volume = float(bar['volume'])
                if volume < 0:
                    raise DataValidationError(
                        f"Negative bar volume: {volume}",
                        data_type="bar",
                        symbol=symbol
                    )
            except (ValueError, TypeError) as e:
                raise DataValidationError(
                    f"Invalid bar volume: {bar['volume']}",
                    data_type="bar",
                    symbol=symbol,
                    error=str(e)
                )
        
        logger.debug("Bar validation passed for %s", symbol)
    
    @staticmethod
    def validate_close_position_response(response: Dict[str, Any]) -> None:
        """
        Validate CLOSE_POSITION response.
        
        Args:
            response: Position close response from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating close position response: %s", response)
        
        if 'status' not in response:
            raise DataValidationError(
                "Close position response missing 'status' field",
                data_type="close_position_response"
            )
        
        status = str(response['status'])
        
        if status == 'ERROR':
            if 'message' not in response:
                raise DataValidationError(
                    "Error response missing 'message' field",
                    data_type="close_position_response"
                )
        
        elif status == 'CLOSED':
            # Should have realized P&L
            if 'realized_pnl' in response:
                try:
                    pnl = float(response['realized_pnl'])
                    # PnL can be any value (positive or negative)
                    # Just check it's a valid number
                    if abs(pnl) > 100_000_000:  # 100 million
                        logger.warning("Extremely large realized PnL: %.2f", pnl)
                except (ValueError, TypeError) as e:
                    raise DataValidationError(
                        f"Invalid realized_pnl: {response['realized_pnl']}",
                        data_type="close_position_response",
                        error=str(e)
                    )
        
        logger.debug("Close position response validation passed")
    
    @staticmethod
    def validate_modify_order_response(response: Dict[str, Any]) -> None:
        """
        Validate MODIFY_ORDER response.
        
        Args:
            response: Order modification response from MT5
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating modify order response: %s", response)
        
        if 'status' not in response:
            raise DataValidationError(
                "Modify order response missing 'status' field",
                data_type="modify_order_response"
            )
        
        status = str(response['status'])
        
        if status not in ['SUCCESS', 'ERROR']:
            logger.warning("Unexpected modify order status: %s", status)
        
        if status == 'ERROR' and 'message' not in response:
            raise DataValidationError(
                "Error response missing 'message' field",
                data_type="modify_order_response"
            )
        
        logger.debug("Modify order response validation passed")
    
    @staticmethod
    def validate_status(status: Dict[str, Any]) -> None:
        """
        Validate status file data.
        
        Args:
            status: Status data from MT5 status file
            
        Raises:
            DataValidationError if invalid
        """
        logger.debug("Validating status data: %s", status)
        
        # Status file should have at least timestamp
        if 'timestamp' in status:
            timestamp = status['timestamp']
            if isinstance(timestamp, str):
                try:
                    datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                except ValueError as e:
                    logger.warning("Invalid timestamp in status: %s", timestamp)
        
        # Validate symbol if present
        if 'symbol' in status:
            symbol = str(status['symbol'])
            if len(symbol) > 50:
                raise DataValidationError(
                    f"Symbol too long in status: {len(symbol)} chars",
                    data_type="status"
                )
        
        # Validate bid/ask if present (same as tick validation)
        if 'bid' in status and 'ask' in status:
            try:
                bid = float(status['bid'])
                ask = float(status['ask'])
                
                if bid > 0 and ask > 0 and ask < bid:
                    raise DataValidationError(
                        f"Invalid status prices: ask < bid (bid={bid}, ask={ask})",
                        data_type="status"
                    )
            except (ValueError, TypeError) as e:
                logger.warning("Invalid bid/ask in status: %s", e)
        
        logger.debug("Status validation passed")
