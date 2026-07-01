"""
Regression test for the XAUUSD value_per_lot mismatch bug.

Root cause (found 2026-07-01 while auditing live SL/TP): the broker's
GET_SYMBOL_SPEC reports value_per_lot=10 for this broker's XAUUSD contract,
but real fills/PnL settle at 100 (confirmed against realized trade PnL and
MT5-reconciled unrealized_pnl in the live state file). TradingSystem._load_symbols
blindly trusted the broker spec, so BudgetSL sized every kalman_regime stop
10x too wide (a live position was risking ~$1,200 against a configured
$120 max loss).

Fix: XAUUSD is excluded from the broker auto-spec override for
value_per_lot/min_lot/max_lot/lot_step — the verified config value (100)
always wins for this symbol. Other symbols keep auto-spec (their broker
values already match config).
"""
from decimal import Decimal
from unittest.mock import MagicMock

from src.main import TradingSystem


def _make_system(config, spec_by_ticker):
    system = object.__new__(TradingSystem)
    system.config = config
    system.logger = MagicMock()
    system.connector = MagicMock()
    system.connector.get_symbol_spec.side_effect = lambda ticker, **_: spec_by_ticker.get(ticker)
    return system


def test_xauusd_ignores_mismatched_broker_value_per_lot():
    config = {
        "symbols": {
            "XAUUSD": {
                "enabled": True,
                "pip_value": 0.01,
                "min_lot": 0.03,
                "max_lot": 0.03,
                "lot_step": 0.01,
                "value_per_lot": 100,
                "leverage": 30,
            },
        }
    }
    # Broker spec disagrees with the verified config value by 10x — must be rejected.
    spec_by_ticker = {
        "XAUUSD": {
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "value_per_lot": 10.0,
            "tick_size": 0.01,
        }
    }

    system = _make_system(config, spec_by_ticker)
    symbols = system._load_symbols(apply_broker_spec=True)

    assert len(symbols) == 1
    assert symbols[0].value_per_lot == 100


def test_min_stops_distance_passed_through_with_broker_spec():
    # GET_SYMBOL_SPEC has no stops-distance field, so this must always come
    # from config regardless of whether the broker spec path is taken.
    # Previously dropped entirely -> Symbol defaulted to 0.0, silently
    # disabling the broker-min-stop safety checks in risk_processor and
    # execution_engine for every symbol.
    config = {
        "symbols": {
            "XAUUSD": {
                "enabled": True,
                "pip_value": 0.01,
                "min_lot": 0.03,
                "max_lot": 0.03,
                "lot_step": 0.01,
                "value_per_lot": 100,
                "leverage": 30,
                "min_stops_distance": 1.0,
            },
        }
    }
    spec_by_ticker = {
        "XAUUSD": {
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "value_per_lot": 10.0,
            "tick_size": 0.01,
        }
    }

    system = _make_system(config, spec_by_ticker)
    symbols = system._load_symbols(apply_broker_spec=True)

    assert symbols[0].min_stops_distance == 1.0


def test_min_stops_distance_passed_through_without_broker_spec():
    config = {
        "symbols": {
            "GBPUSD": {
                "enabled": True,
                "pip_value": 0.0001,
                "min_lot": 0.01,
                "max_lot": 10.0,
                "lot_step": 0.01,
                "value_per_lot": 100000,
                "leverage": 30,
                "min_stops_distance": 0.0005,
            },
        }
    }

    system = _make_system(config, {})
    symbols = system._load_symbols(apply_broker_spec=False)

    assert symbols[0].min_stops_distance == Decimal("0.0005")


def test_max_spread_passed_through_with_broker_spec():
    # GET_SYMBOL_SPEC has no spread field, so this must always come from
    # config. Previously dropped entirely -> Symbol defaulted to 999.0,
    # silently disabling the live spread-filter reject in execution_engine
    # for every symbol.
    config = {
        "symbols": {
            "XAUUSD": {
                "enabled": True,
                "pip_value": 0.01,
                "min_lot": 0.03,
                "max_lot": 0.03,
                "lot_step": 0.01,
                "value_per_lot": 100,
                "leverage": 30,
                "max_spread": 3.5,
            },
        }
    }
    spec_by_ticker = {
        "XAUUSD": {
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
            "value_per_lot": 10.0,
            "tick_size": 0.01,
        }
    }

    system = _make_system(config, spec_by_ticker)
    symbols = system._load_symbols(apply_broker_spec=True)

    assert symbols[0].max_spread == Decimal("3.5")


def test_max_spread_passed_through_without_broker_spec():
    config = {
        "symbols": {
            "GBPUSD": {
                "enabled": True,
                "pip_value": 0.0001,
                "min_lot": 0.01,
                "max_lot": 10.0,
                "lot_step": 0.01,
                "value_per_lot": 100000,
                "leverage": 30,
                "max_spread": 2.0,
            },
        }
    }

    system = _make_system(config, {})
    symbols = system._load_symbols(apply_broker_spec=False)

    assert symbols[0].max_spread == Decimal("2.0")


def test_other_symbols_still_use_matching_broker_spec():
    config = {
        "symbols": {
            "GBPUSD": {
                "enabled": True,
                "pip_value": 0.0001,
                "min_lot": 0.01,
                "max_lot": 10.0,
                "lot_step": 0.01,
                "value_per_lot": 100000,
                "leverage": 30,
            },
        }
    }
    spec_by_ticker = {
        "GBPUSD": {
            "volume_min": 0.01,
            "volume_max": 50.0,
            "volume_step": 0.01,
            "value_per_lot": 100000.0,
            "tick_size": 0.0001,
        }
    }

    system = _make_system(config, spec_by_ticker)
    symbols = system._load_symbols(apply_broker_spec=True)

    assert len(symbols) == 1
    assert symbols[0].value_per_lot == 100000
    assert symbols[0].max_lot == 50.0
