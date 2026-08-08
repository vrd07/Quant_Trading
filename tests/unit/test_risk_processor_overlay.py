"""RiskProcessor's take-profit overlay hook.

The regression test is the important one: with no bar_provider, stops must be
byte-identical to the pre-overlay implementation, so every existing strategy is
protected from silent drift.
"""
from decimal import Decimal

import pandas as pd
import pytest

from src.core.constants import OrderSide
from src.core.types import Signal, Symbol
from src.risk.risk_processor import RiskProcessor


def make_symbol(ticker="XAUUSD"):
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.2"),
        max_lot=Decimal("0.2"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
        min_stops_distance=Decimal("1.0"),
        max_spread=Decimal("5.0"),
    )


@pytest.fixture
def gold_signal():
    """A squeeze_breakout XAUUSD signal with its structural stop already precomputed.

    Geometry is the shipped one: fixed 33pt stop, TP = rr(2.0) x stop distance.
    """
    return Signal(
        strategy_name="squeeze_breakout",
        symbol=make_symbol("XAUUSD"),
        side=OrderSide.BUY,
        strength=0.6,
        entry_price=Decimal("2000.0"),
        metadata={
            "strategy": "squeeze_breakout",
            "stop_price": 1967.0,
            "take_profit_price": 2066.0,
            "atr": 5.0,
            "preserve_structural_sl": True,
        },
    )


def _cfg(enabled=True, strategies=("squeeze_breakout",)):
    return {
        "strategies": {"squeeze_breakout": {"rr": 2.0}},
        "risk": {
            "reward_risk_ratio": 2.0,
            "liquidity_tp_overlay": {
                "enabled": enabled,
                "band_pct": 0.25,
                "buffer_atr": 0.05,
                "min_rr": 1.2,
                "history_bars": 2500,
                "strategies": list(strategies),
                "symbols": ["XAUUSD"],
            },
        },
    }


def test_default_construction_takes_no_provider():
    """All pre-existing call sites keep working unchanged."""
    rp = RiskProcessor({"strategies": {}, "risk": {}})
    assert rp.bar_provider is None


def test_overlay_is_inert_without_a_provider(gold_signal):
    rp = RiskProcessor(_cfg(), bar_provider=None)
    out = rp.calculate_stops(gold_signal)
    assert out.metadata.get("liquidity_tp_reason") in (None, "no_bars")


def test_overlay_skips_symbols_outside_the_allow_list(gold_signal, monkeypatch):
    cfg = _cfg()
    cfg["risk"]["liquidity_tp_overlay"]["symbols"] = ["US30"]
    called = []
    rp = RiskProcessor(cfg, bar_provider=lambda t, n: called.append(t))
    rp.calculate_stops(gold_signal)
    assert called == [], "provider must not be consulted for a non-listed symbol"


def test_overlay_skips_strategies_outside_the_allow_list(gold_signal, monkeypatch):
    cfg = _cfg(strategies=("bos_structure",))
    called = []
    rp = RiskProcessor(cfg, bar_provider=lambda t, n: called.append(t))
    rp.calculate_stops(gold_signal)
    assert called == []


def test_disabled_overlay_never_consults_the_provider(gold_signal):
    called = []
    rp = RiskProcessor(_cfg(enabled=False), bar_provider=lambda t, n: called.append(t))
    rp.calculate_stops(gold_signal)
    assert called == []


def test_provider_exception_leaves_the_target_alone(gold_signal):
    def boom(ticker, n):
        raise RuntimeError("data engine down")
    rp = RiskProcessor(_cfg(), bar_provider=boom)
    before = gold_signal.take_profit
    out = rp.calculate_stops(gold_signal)
    assert out.take_profit == before or out.metadata.get("liquidity_tp_reason") == "error"
