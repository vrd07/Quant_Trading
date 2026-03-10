"""
Integration tests for execution engine.

WARNING: These tests place REAL orders on demo account.
Only run on demo/paper trading accounts.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timezone

from src.execution.execution_engine import ExecutionEngine
from src.connectors.mt5_connector import MT5Connector
from src.risk.risk_engine import RiskEngine
from src.core.types import Signal, Symbol
from src.core.constants import OrderSide, MarketRegime


@pytest.fixture
def setup():
    """Setup execution engine with dependencies."""
    connector = MT5Connector()
    connector.connect()
    
    config = {
        'risk': {
            'max_daily_loss_pct': 0.02,
            'max_drawdown_pct': 0.10,
            'risk_per_trade_pct': 0.0025,
            'max_positions': 3,
            'max_exposure_per_symbol_pct': 0.30,
            'position_sizing': {'method': 'fixed_fractional'}
        }
    }
    
    risk_engine = RiskEngine(config)
    
    # Reset kill switch in case it was triggered previously
    risk_engine.kill_switch.reset()
    
    # Set equity high water mark to avoid drawdown issues
    risk_engine.equity_high_water_mark = Decimal("10000")
    
    execution_engine = ExecutionEngine(connector, risk_engine)
    
    yield {
        'connector': connector,
        'risk_engine': risk_engine,
        'execution': execution_engine
    }
    
    connector.disconnect()


def test_signal_to_order_conversion(setup):
    """Test converting signal to order."""
    execution = setup['execution']
    
    symbol = Symbol(ticker="EURUSD", pip_value=Decimal("0.0001"), value_per_lot=Decimal("100000"))
    
    signal = Signal(
        strategy_name="test_strategy",
        symbol=symbol,
        side=OrderSide.BUY,
        strength=0.8,
        regime=MarketRegime.TREND,
        entry_price=Decimal("1.10000"),
        stop_loss=Decimal("1.09900"),
        take_profit=Decimal("1.10200")
    )
    
    # Submit signal (will be validated but not actually sent to MT5)
    order = execution.submit_signal(
        signal=signal,
        account_balance=Decimal("10000"),
        account_equity=Decimal("10000"),
        current_positions={},
        daily_pnl=Decimal("0")
    )
    
    assert order is not None
    assert order.symbol.ticker == "EURUSD"
    assert order.side == OrderSide.BUY
    assert order.stop_loss == Decimal("1.09900")


@pytest.mark.skip(reason="Don't place real orders in automated tests")
def test_real_order_submission(setup):
    """
    Test actual order submission to MT5.
    
    MANUAL TEST ONLY - requires demo account.
    """
    execution = setup['execution']
    connector = setup['connector']
    
    # Get current price
    tick = connector.get_current_tick("EURUSD")
    
    if not tick:
        pytest.skip("No tick data available")
    
    symbol = Symbol(ticker="EURUSD", pip_value=Decimal("0.0001"), value_per_lot=Decimal("100000"))
    
    # Create signal with current price
    signal = Signal(
        strategy_name="test_strategy",
        symbol=symbol,
        side=OrderSide.BUY,
        strength=0.5,
        regime=MarketRegime.TREND,
        entry_price=tick.ask,
        stop_loss=tick.ask - Decimal("0.00100"),  # 10 pips
        take_profit=tick.ask + Decimal("0.00200")   # 20 pips
    )
    
    order = execution.submit_signal(
        signal=signal,
        account_balance=Decimal("10000"),
        account_equity=Decimal("10000"),
        current_positions={},
        daily_pnl=Decimal("0")
    )
    
    assert order is not None
    print(f"Order submitted: {order.order_id}")
    print(f"Status: {order.status.value}")


def test_order_tracking(setup):
    """Test order manager tracking."""
    execution = setup['execution']
    
    # Should start with no orders
    active_orders = execution.get_active_orders()
    assert isinstance(active_orders, list)
    
    stats = execution.order_manager.get_statistics()
    assert 'total' in stats
    assert 'active' in stats
