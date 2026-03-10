"""
Integration tests for portfolio engine.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from uuid import uuid4

from src.portfolio.portfolio_engine import PortfolioEngine
from src.connectors.mt5_connector import MT5Connector
from src.core.types import Position, Symbol, Tick
from src.core.constants import PositionSide


@pytest.fixture
def portfolio():
    """Create portfolio engine for testing."""
    connector = MT5Connector()
    connector.connect()
    
    engine = PortfolioEngine(connector)
    
    yield engine
    
    connector.disconnect()


def test_add_position(portfolio):
    """Test adding position to portfolio."""
    symbol = Symbol(ticker="EURUSD", value_per_lot=Decimal("100000"))
    
    position = Position(
        position_id=uuid4(),
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("1.10000"),
        current_price=Decimal("1.10000")
    )
    
    portfolio.add_position(position)
    
    assert portfolio.position_tracker.get_position_count() == 1
    retrieved = portfolio.get_position(position.position_id)
    assert retrieved.symbol.ticker == "EURUSD"


def test_update_position_price(portfolio):
    """Test updating position with new price."""
    symbol = Symbol(ticker="EURUSD", value_per_lot=Decimal("100000"))
    
    position = Position(
        position_id=uuid4(),
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("1.10000"),
        current_price=Decimal("1.10000")
    )
    
    portfolio.add_position(position)
    
    # Update price (profit scenario)
    portfolio.update_position_price(position.position_id, Decimal("1.10050"))
    
    updated = portfolio.get_position(position.position_id)
    assert updated.current_price == Decimal("1.10050")
    assert updated.unrealized_pnl > 0  # Should be profitable


def test_close_position(portfolio):
    """Test closing position and P&L calculation."""
    symbol = Symbol(ticker="EURUSD", value_per_lot=Decimal("100000"))
    
    position = Position(
        position_id=uuid4(),
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("1.10000"),
        current_price=Decimal("1.10050")
    )
    
    portfolio.add_position(position)
    
    # Close with profit
    realized_pnl = portfolio.close_position(
        position_id=position.position_id,
        exit_price=Decimal("1.10050")
    )
    
    assert realized_pnl > 0
    assert portfolio.position_tracker.get_position_count() == 0
    assert portfolio.total_realized_pnl == realized_pnl


def test_portfolio_exposure(portfolio):
    """Test portfolio exposure calculations."""
    symbol = Symbol(ticker="EURUSD", value_per_lot=Decimal("100000"))
    
    # Add long position
    long_pos = Position(
        position_id=uuid4(),
        symbol=symbol,
        side=PositionSide.LONG,
        quantity=Decimal("0.1"),
        entry_price=Decimal("1.10000"),
        current_price=Decimal("1.10000")
    )
    portfolio.add_position(long_pos)
    
    # Add short position
    short_pos = Position(
        position_id=uuid4(),
        symbol=symbol,
        side=PositionSide.SHORT,
        quantity=Decimal("0.05"),
        entry_price=Decimal("1.10000"),
        current_price=Decimal("1.10000")
    )
    portfolio.add_position(short_pos)
    
    # Total exposure = |long| + |short|
    total_exposure = portfolio.get_total_exposure()
    assert total_exposure > 0
    
    # Net exposure = long - short
    net_exposure = portfolio.get_net_exposure()
    assert net_exposure > 0  # Net long


def test_portfolio_statistics(portfolio):
    """Test portfolio statistics."""
    stats = portfolio.get_statistics()
    
    assert 'total_positions' in stats
    assert 'long_positions' in stats
    assert 'short_positions' in stats
    assert 'total_exposure' in stats
    assert 'unrealized_pnl' in stats
    assert 'realized_pnl' in stats
    
    # Should start with zero positions
    assert stats['total_positions'] == 0


def test_reconciliation_with_mt5(portfolio):
    """Test MT5 reconciliation."""
    success, discrepancies = portfolio.reconcile_with_mt5()
    
    assert isinstance(success, bool)
    assert isinstance(discrepancies, list)
    
    # If no positions, should reconcile successfully
    if portfolio.position_tracker.get_position_count() == 0:
        # Might still have discrepancies if MT5 has open positions
        # But should not crash
        assert True
