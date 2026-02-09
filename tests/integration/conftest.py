"""
Pytest configuration and fixtures for integration tests.
"""

import logging
import pytest


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: Integration tests that require external services (MT5)"
    )


@pytest.fixture(autouse=True)
def setup_logging(caplog):
    """
    Setup logging for all tests.
    
    This fixture runs automatically for all tests and ensures
    log output is captured and displayed.
    """
    caplog.set_level(logging.INFO)
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Set specific loggers to appropriate levels
    logging.getLogger('src.connectors.mt5_connector').setLevel(logging.INFO)
    logging.getLogger('src.connectors.heartbeat').setLevel(logging.INFO)
    logging.getLogger('src.connectors.message_validator').setLevel(logging.WARNING)


def pytest_collection_modifyitems(config, items):
    """
    Modify test collection to handle integration test markers.
    
    This automatically marks all tests in the integration directory
    as 'integration' tests.
    """
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
