"""
Backtest parity for the dynamic per-trade time stop.

The live TrailingStopManager caps a strategy-published time stop by the
configured ceiling. SimulatedBroker mirrors those exit rules, so a backtest of
a live config must exit on the same bar the bot would — otherwise the feature
is live-only and invisible to every backtest, which is the plumbing-null trap
that hid the liquidity TP overlay.
"""

from decimal import Decimal

import pytest

from src.backtest.simulation import SimulatedBroker
from src.core.constants import OrderSide, OrderType
from src.core.types import Order, Symbol


GOLD = Symbol(ticker="XAUUSD", value_per_lot=Decimal("100"), min_lot=Decimal("0.01"))


def make_bar(close=4000.0):
    return {'open': close, 'high': close, 'low': close, 'close': close, 'volume': 100}


def make_broker(strategy_time_stop=None, bar_minutes=15.0):
    overrides = {}
    if strategy_time_stop is not None:
        overrides['wavelet_cycle'] = {
            'time_stop_minutes': strategy_time_stop,
            'disable_be_lock': True,
        }
    broker = SimulatedBroker(
        initial_capital=Decimal("50000"),
        slippage_model="fixed",
        trailing_stop_config={'enabled': True, 'strategy_overrides': overrides},
    )
    broker._bar_interval_minutes = bar_minutes
    return broker


def open_position(broker, time_stop_minutes=None):
    metadata = {'strategy': 'wavelet_cycle'}
    if time_stop_minutes is not None:
        metadata['time_stop_minutes'] = time_stop_minutes
    order = Order(
        symbol=GOLD,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.02"),
        price=Decimal("4000.00"),
        stop_loss=Decimal("3990.00"),
        metadata=metadata,
    )
    assert broker.execute_order(order, make_bar()) is not None
    return order


def run_bars(broker, count):
    for _ in range(count):
        broker.update_positions(make_bar())
        broker.check_exits(make_bar())


class TestSimulatedDynamicTimeStop:

    def test_published_time_stop_closes_before_the_configured_ceiling(self):
        # 8 bars x 15m = 120 min published, against a 1080 min ceiling.
        broker = make_broker(strategy_time_stop=1080)
        open_position(broker, time_stop_minutes=120)

        run_bars(broker, 9)

        assert broker.positions == {}

    def test_position_survives_until_its_published_limit(self):
        broker = make_broker(strategy_time_stop=1080)
        open_position(broker, time_stop_minutes=120)

        run_bars(broker, 6)  # 90 min held

        assert len(broker.positions) == 1

    def test_configured_ceiling_caps_a_longer_published_time_stop(self):
        # Strategy asks for 1500 min; the operator allows 120.
        broker = make_broker(strategy_time_stop=120)
        open_position(broker, time_stop_minutes=1500)

        run_bars(broker, 9)

        assert broker.positions == {}

    def test_position_without_a_published_time_stop_uses_the_config(self):
        broker = make_broker(strategy_time_stop=1080)
        open_position(broker)

        run_bars(broker, 9)  # 135 min — well under the 1080 ceiling

        assert len(broker.positions) == 1

    def test_exit_is_recorded_as_a_time_stop(self):
        broker = make_broker(strategy_time_stop=1080)
        open_position(broker, time_stop_minutes=120)

        run_bars(broker, 9)

        assert broker.get_closed_trades()[-1]['exit_reason'] == 'time_stop'

    @pytest.mark.parametrize("bad_value", [0, -5, "soon"])
    def test_unusable_published_values_fall_back_to_the_config(self, bad_value):
        broker = make_broker(strategy_time_stop=1080)
        open_position(broker, time_stop_minutes=bad_value)

        run_bars(broker, 9)

        assert len(broker.positions) == 1
