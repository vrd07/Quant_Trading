"""
Integration tests for MT5Connector.

These tests require MT5 to be running with the file bridge EA active.
Run with: pytest tests/integration/test_mt5_connector.py -v -s
"""

import pytest
import time
import logging
from decimal import Decimal

from src.connectors.mt5_connector import MT5Connector, get_mt5_connector
from src.connectors.heartbeat import HeartbeatMonitor
from src.core.constants import OrderSide, OrderType
from src.core.exceptions import MT5ConnectionError, DataValidationError


logger = logging.getLogger(__name__)


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def connector():
    """
    Create MT5Connector instance for testing.
    
    This fixture is module-scoped so the connection is shared
    across all tests in this file for efficiency.
    """
    logger.info("Setting up MT5Connector for integration tests")
    conn = MT5Connector()
    
    try:
        conn.connect()
        logger.info("Successfully connected to MT5")
    except MT5ConnectionError as e:
        pytest.skip(f"MT5 not available: {e}")
    
    yield conn
    
    logger.info("Tearing down MT5Connector")
    conn.disconnect()


@pytest.fixture
def fresh_connector():
    """
    Create a fresh MT5Connector instance for tests that need isolation.
    
    This fixture is function-scoped and creates a new connection
    for each test that uses it.
    """
    conn = MT5Connector()
    
    try:
        conn.connect()
    except MT5ConnectionError as e:
        pytest.skip(f"MT5 not available: {e}")
    
    yield conn
    
    conn.disconnect()


class TestMT5Connection:
    """Test basic MT5 connection functionality."""
    
    def test_connection_successful(self, connector):
        """Test that connection to MT5 was successful."""
        assert connector.connected
        assert connector.last_heartbeat is not None
        logger.info("Connection test passed")
    
    def test_heartbeat(self, connector):
        """Test heartbeat mechanism."""
        result = connector.heartbeat()
        assert result is True
        assert connector.last_heartbeat is not None
        logger.info("Heartbeat test passed")
    
    def test_connection_health_check(self, connector):
        """Test connection health checking."""
        # Should be healthy if recently connected
        assert connector.check_connection_health()
        logger.info("Connection health check passed")
    
    def test_disconnect_and_reconnect(self, fresh_connector):
        """Test disconnecting and reconnecting."""
        # Disconnect
        fresh_connector.disconnect()
        assert not fresh_connector.connected
        
        # Reconnect
        fresh_connector.connect()
        assert fresh_connector.connected
        assert fresh_connector.heartbeat()
        logger.info("Disconnect/reconnect test passed")


class TestAccountInfo:
    """Test account information retrieval."""
    
    def test_get_account_info(self, connector):
        """Test fetching account information."""
        account = connector.get_account_info()
        
        # Check required fields are present
        assert 'balance' in account
        assert 'equity' in account
        assert 'margin' in account
        
        # Check types
        assert isinstance(account['balance'], Decimal)
        assert isinstance(account['equity'], Decimal)
        assert isinstance(account['margin'], Decimal)
        
        # Check values are non-negative
        assert account['balance'] >= 0
        assert account['equity'] >= 0
        assert account['margin'] >= 0
        
        logger.info(
            "Account info test passed: balance=%s, equity=%s",
            account['balance'], account['equity']
        )
    
    def test_account_info_validation(self, connector):
        """Test that account info is properly validated."""
        # Get account info - should not raise validation errors
        try:
            account = connector.get_account_info()
            logger.info("Account info validation test passed")
        except DataValidationError as e:
            pytest.fail(f"Account info failed validation: {e}")


class TestPositions:
    """Test position management."""
    
    def test_get_positions(self, connector):
        """Test fetching open positions."""
        positions = connector.get_positions()
        
        # Should return a dict (may be empty)
        assert isinstance(positions, dict)
        
        # If there are positions, validate their structure
        for position_id, position in positions.items():
            assert position.symbol is not None
            assert position.side is not None
            assert position.quantity > 0
            assert position.entry_price > 0
            
            logger.info(
                "Found position: %s %s @ %s (PnL: %s)",
                position.symbol.ticker,
                position.side.value,
                position.entry_price,
                position.unrealized_pnl
            )
        
        logger.info("Get positions test passed (%d positions)", len(positions))
    
    def test_positions_validation(self, connector):
        """Test that positions are properly validated."""
        # Get positions - should not raise validation errors
        try:
            positions = connector.get_positions()
            logger.info("Positions validation test passed")
        except DataValidationError as e:
            pytest.fail(f"Positions failed validation: {e}")


class TestTickData:
    """Test tick data retrieval."""
    
    def test_get_current_tick(self, connector):
        """Test getting current tick data."""
        # Try common forex symbols
        symbols_to_test = ["EURUSD", "GBPUSD", "XAUUSD", "USDJPY"]
        
        tick_found = False
        for symbol in symbols_to_test:
            tick = connector.get_current_tick(symbol)
            
            if tick:
                tick_found = True
                # Validate tick structure
                assert tick.bid > 0
                assert tick.ask > 0
                assert tick.ask >= tick.bid
                assert tick.symbol.ticker == symbol
                
                logger.info(
                    "Tick data for %s: bid=%s, ask=%s, spread=%s",
                    symbol, tick.bid, tick.ask, tick.spread
                )
                break
        
        # At least one symbol should have tick data
        if not tick_found:
            logger.warning("No tick data found for any tested symbol")
        else:
            logger.info("Tick data test passed")
    
    def test_tick_data_freshness(self, connector):
        """Test that tick data is reasonably fresh."""
        tick = connector.get_current_tick("EURUSD")
        
        if tick:
            # Tick should have a timestamp
            assert tick.timestamp is not None
            logger.info("Tick freshness test passed")


class TestOrderPlacement:
    """Test order placement (SKIPPED by default to avoid real trading)."""
    
    @pytest.mark.skip(reason="Don't place real orders in automated tests")
    def test_place_market_order(self, connector):
        """
        Test placing a market order (SKIPPED by default).
        
        WARNING: This test places a REAL order!
        Only run manually on a DEMO account!
        
        To run: pytest tests/integration/test_mt5_connector.py::TestOrderPlacement::test_place_market_order -v -s
        """
        order = connector.place_order(
            symbol="EURUSD",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            order_type=OrderType.MARKET,
            stop_loss=Decimal("1.05000"),
            take_profit=Decimal("1.15000"),
            comment="INTEGRATION_TEST"
        )
        
        # Validate order response
        assert order is not None
        assert order.symbol.ticker == "EURUSD"
        assert order.side == OrderSide.BUY
        assert order.quantity == Decimal("0.01")
        
        logger.info(
            "Order placed: ticket=%s, price=%s",
            order.metadata.get('mt5_ticket'),
            order.price
        )
    
    @pytest.mark.skip(reason="Don't modify real positions in automated tests")
    def test_close_position(self, connector):
        """
        Test closing a position (SKIPPED by default).
        
        WARNING: This test closes a REAL position!
        Only run manually on a DEMO account after opening a test position!
        """
        # Get open positions
        positions = connector.get_positions()
        
        if not positions:
            pytest.skip("No open positions to close")
        
        # Close the first position
        position_id = list(positions.keys())[0]
        result = connector.close_position(position_id)
        
        assert result['status'] in ['CLOSED', 'SUCCESS']
        logger.info("Position closed: %s", result)


class TestHeartbeatMonitor:
    """Test heartbeat monitoring functionality."""
    
    def test_heartbeat_monitor_basic(self, fresh_connector):
        """Test basic heartbeat monitor functionality."""
        monitor = HeartbeatMonitor(
            fresh_connector,
            interval_seconds=2,
            timeout_seconds=10
        )
        
        # Start monitoring
        monitor.start()
        assert monitor.running
        
        # Let it run for a few heartbeats
        time.sleep(6)
        
        # Should be healthy
        assert monitor.is_healthy()
        
        # Get status
        status = monitor.get_status()
        assert status['healthy']
        assert status['consecutive_failures'] == 0
        assert status['last_success'] is not None
        
        logger.info(
            "Heartbeat monitor status: %d seconds since last success",
            status['seconds_since_success']
        )
        
        # Stop monitoring
        monitor.stop()
        assert not monitor.running
        
        logger.info("Heartbeat monitor test passed")
    
    def test_heartbeat_monitor_callback(self, fresh_connector):
        """Test heartbeat monitor with connection lost callback."""
        callback_called = []
        
        def on_connection_lost():
            logger.warning("Connection lost callback triggered")
            callback_called.append(True)
        
        monitor = HeartbeatMonitor(
            fresh_connector,
            interval_seconds=1,
            timeout_seconds=5,
            on_connection_lost=on_connection_lost
        )
        
        monitor.start()
        time.sleep(3)
        
        # Should still be healthy
        assert monitor.is_healthy()
        assert len(callback_called) == 0
        
        monitor.stop()
        logger.info("Heartbeat callback test passed")
    
    def test_heartbeat_stop_start(self, fresh_connector):
        """Test stopping and restarting heartbeat monitor."""
        monitor = HeartbeatMonitor(fresh_connector, interval_seconds=2)
        
        # Start
        monitor.start()
        time.sleep(3)
        assert monitor.is_healthy()
        
        # Stop
        monitor.stop()
        assert not monitor.running
        
        # Restart
        monitor.start()
        time.sleep(3)
        assert monitor.is_healthy()
        
        # Cleanup
        monitor.stop()
        logger.info("Heartbeat stop/start test passed")


class TestSingletonPattern:
    """Test singleton pattern for MT5Connector."""
    
    def test_singleton_connector(self):
        """Test that get_mt5_connector returns the same instance."""
        conn1 = get_mt5_connector()
        conn2 = get_mt5_connector()
        
        # Should be the exact same object
        assert conn1 is conn2
        
        logger.info("Singleton pattern test passed")


class TestSymbolCache:
    """Test symbol caching functionality."""
    
    def test_symbol_cache(self, connector):
        """Test that symbols are cached correctly."""
        # Get tick for a symbol (creates cache entry)
        tick1 = connector.get_current_tick("EURUSD")
        
        # Get again (should use cache)
        tick2 = connector.get_current_tick("EURUSD")
        
        if tick1 and tick2:
            # Should reference the same Symbol object
            assert tick1.symbol is tick2.symbol
            logger.info("Symbol cache test passed")


class TestErrorHandling:
    """Test error handling and edge cases."""
    
    def test_invalid_symbol_tick(self, connector):
        """Test requesting tick for invalid symbol."""
        # Invalid symbol should return None, not crash
        tick = connector.get_current_tick("INVALID_SYMBOL_XXXXX")
        assert tick is None or tick.symbol.ticker == "INVALID_SYMBOL_XXXXX"
        logger.info("Invalid symbol test passed")
    
    def test_connection_without_mt5(self):
        """Test behavior when MT5 is not running."""
        # This test will likely fail to connect
        # It's here to document expected behavior
        
        # Create connector with non-existent data directory
        try:
            conn = MT5Connector(data_dir="/tmp/nonexistent_mt5_data")
            conn.connect()
            # If we get here, MT5 might actually be running somewhere
            conn.disconnect()
        except MT5ConnectionError:
            # Expected when MT5 not available
            logger.info("Connection failure test passed (MT5 not available)")


# Test configuration verification
def test_pytest_configuration():
    """Verify pytest is configured correctly."""
    # This test always passes but logs important info
    logger.info("Pytest configuration test")
    logger.info("To run integration tests: pytest tests/integration -v -s -m integration")
    logger.info("To skip integration tests: pytest tests -m 'not integration'")
    assert True
