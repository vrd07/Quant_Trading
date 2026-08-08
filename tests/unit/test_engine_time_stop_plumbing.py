"""
Proves the published time stop actually reaches the broker in both backtest
engines.

Without this link the value dies between Signal and Order, SimulatedBroker
never sees it, and the dynamic time stop silently degrades to the static
config ceiling in every backtest while working live — a divergence that reads
as "no effect" rather than as a bug.
"""

import ast
import inspect
from decimal import Decimal

import pytest

from src.backtest import backtest_engine, ensemble_engine
from src.core.constants import MarketRegime, OrderSide
from src.core.types import Signal, Symbol


GOLD = Symbol(ticker="XAUUSD", value_per_lot=Decimal("100"))


def _order_metadata_keys(module) -> list:
    """Keys of the metadata dict passed to every Order(...) in a module."""
    tree = ast.parse(inspect.getsource(module))
    key_sets = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Order'):
            continue
        for kw in node.keywords:
            if kw.arg == 'metadata' and isinstance(kw.value, ast.Dict):
                key_sets.append({
                    k.value for k in kw.value.keys if isinstance(k, ast.Constant)
                })
    return key_sets


@pytest.mark.parametrize(
    "module", [ensemble_engine, backtest_engine], ids=['ensemble', 'backtest']
)
def test_every_engine_order_carries_the_published_time_stop(module):
    key_sets = _order_metadata_keys(module)
    assert key_sets, f"no Order(metadata=...) construction found in {module.__name__}"
    for keys in key_sets:
        assert 'time_stop_minutes' in keys, (
            f"{module.__name__} builds an Order without carrying "
            f"signal.metadata['time_stop_minutes'] — the dynamic time stop "
            f"would be invisible to backtests run through it."
        )


def test_ensemble_engine_order_reaches_the_broker_with_the_time_stop():
    """Behavioural check: fire one signal, inspect what the broker received."""
    signal = Signal(
        strategy_name="wavelet_cycle",
        symbol=GOLD,
        side=OrderSide.BUY,
        strength=0.7,
        regime=MarketRegime.TREND,
        entry_price=Decimal("4000.00"),
        stop_loss=Decimal("3990.00"),
        take_profit=Decimal("4020.00"),
        metadata={'time_stop_minutes': 270},
    )

    engine = object.__new__(ensemble_engine.EnsembleBacktestEngine)
    engine.bypass_risk_limits = True
    engine.risk_engine = _StubRisk()
    engine.metrics = _StubMetrics()
    engine.broker = _CapturingBroker()
    engine.risk_processor = _PassThroughRiskProcessor()

    engine._execute(signal, "wavelet_cycle", _StubBar())

    assert engine.broker.orders, "no order reached the broker"
    assert engine.broker.orders[0].metadata['time_stop_minutes'] == 270


def test_a_signal_without_a_time_stop_puts_none_on_the_order():
    signal = Signal(
        strategy_name="kalman_regime",
        symbol=GOLD,
        side=OrderSide.BUY,
        strength=0.7,
        regime=MarketRegime.TREND,
        entry_price=Decimal("4000.00"),
        stop_loss=Decimal("3990.00"),
        metadata={},
    )

    engine = object.__new__(ensemble_engine.EnsembleBacktestEngine)
    engine.bypass_risk_limits = True
    engine.risk_engine = _StubRisk()
    engine.metrics = _StubMetrics()
    engine.broker = _CapturingBroker()
    engine.risk_processor = _PassThroughRiskProcessor()

    engine._execute(signal, "kalman_regime", _StubBar())

    assert engine.broker.orders[0].metadata['time_stop_minutes'] is None


class _StubBar:
    name = "2026-01-01 00:00:00"


class _StubRisk:
    def calculate_position_size(self, **kwargs):
        return Decimal("0.02")

    def update_equity_hwm(self, equity):
        pass


class _PassThroughRiskProcessor:
    """Signals here already carry their own stops; nothing to recompute."""

    def calculate_stops(self, signal):
        return signal

    def apply_tp_overlay(self, signal):
        return signal


class _StubMetrics:
    def __init__(self):
        self.trades = []

    def add_trade(self, row):
        self.trades.append(row)


class _CapturingBroker:
    def __init__(self):
        self.orders = []

    def execute_order(self, order, current_bar):
        self.orders.append(order)
        return Decimal("4000.00")

    def get_balance(self):
        return Decimal("50000")

    def get_equity(self):
        return Decimal("50000")

    def get_positions(self):
        return []

    def get_daily_pnl(self):
        return Decimal("0")
