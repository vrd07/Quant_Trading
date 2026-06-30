"""
Tests for the uniform fixed-SL + RR-TP policy in RiskProcessor.

Policy (2026-06-30): when a strategy's config block sets `sl_points`, the
RiskProcessor uses that fixed point distance as the SL and sets TP = sl_points × rr
(clean 1:1 / 1:2 / 1:3). It also flags preserve_structural_sl/tp so the
execution-layer BudgetSL/BudgetTP do not rewrite the geometry. The four
calendar/drift strategies are excluded (no-TP by design).
"""

from decimal import Decimal

import pytest

from src.core.types import Signal, Symbol
from src.core.constants import OrderSide
from src.risk.risk_processor import RiskProcessor


def make_symbol(ticker: str = "XAUUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


def make_signal(strategy_name, side=OrderSide.BUY, entry=2000.0, strength=0.6, atr=5.0):
    return Signal(
        strategy_name=strategy_name,
        symbol=make_symbol(),
        side=side,
        strength=strength,
        entry_price=Decimal(str(entry)),
        metadata={"strategy": strategy_name, "atr": atr},
    )


def make_processor(strategy_cfg):
    return RiskProcessor({"strategies": strategy_cfg, "risk": {}})


# ── Core fixed-SL + RR geometry ────────────────────────────────────────────

def test_fixed_sl_buy_rr2():
    rp = make_processor({"vwap": {"sl_points": 33.0, "rr": 2.0}})
    sig = make_signal("vwap_deviation", side=OrderSide.BUY, entry=2000.0)
    rp.calculate_stops(sig)
    assert sig.stop_loss == Decimal("1967.0")        # 2000 - 33
    assert sig.take_profit == Decimal("2066.0")      # 2000 + 33*2


def test_fixed_sl_sell_rr2():
    rp = make_processor({"vwap": {"sl_points": 33.0, "rr": 2.0}})
    sig = make_signal("vwap_deviation", side=OrderSide.SELL, entry=2000.0)
    rp.calculate_stops(sig)
    assert sig.stop_loss == Decimal("2033.0")        # 2000 + 33
    assert sig.take_profit == Decimal("1934.0")      # 2000 - 33*2


@pytest.mark.parametrize("rr,expected_tp", [(1.0, "2010.0"), (2.0, "2020.0"), (3.0, "2030.0")])
def test_rr_ratios(rr, expected_tp):
    rp = make_processor({"momentum": {"sl_points": 10.0, "rr": rr}})
    sig = make_signal("momentum_scalp", side=OrderSide.BUY, entry=2000.0)
    rp.calculate_stops(sig)
    assert sig.stop_loss == Decimal("1990.0")
    assert sig.take_profit == Decimal(expected_tp)


def test_rr_defaults_to_2_when_absent():
    rp = make_processor({"momentum": {"sl_points": 10.0}})
    sig = make_signal("momentum_scalp", side=OrderSide.BUY, entry=2000.0)
    rp.calculate_stops(sig)
    assert sig.take_profit == Decimal("2020.0")      # default rr 2.0


# ── Preserve flags so execution layer does not override ────────────────────

def test_preserve_flags_set():
    rp = make_processor({"sbr": {"sl_points": 20.0, "rr": 2.0}})
    sig = make_signal("structure_break_retest", side=OrderSide.BUY)
    rp.calculate_stops(sig)
    assert sig.metadata.get("preserve_structural_sl") is True
    assert sig.metadata.get("preserve_structural_tp") is True


# ── Calendar/drift strategies excluded (no-TP by design) ───────────────────

@pytest.mark.parametrize("name", ["london_breakout", "monday_drift",
                                  "index_overnight", "wednesday_drift"])
def test_calendar_strategies_excluded(name):
    # Even if a stray sl_points lands in config, the no-TP strategies keep TP=None.
    rp = make_processor({name: {"sl_points": 50.0, "rr": 2.0}})
    sig = make_signal(name, side=OrderSide.BUY, entry=2000.0)
    sig.metadata["stop_price"] = 1950.0
    rp.calculate_stops(sig)
    assert sig.take_profit is None


# ── Backward compatibility: no sl_points → existing ATR path runs ──────────

def test_no_sl_points_falls_back_to_atr_path():
    rp = make_processor({"momentum": {"atr_stop_multiplier": 2.0, "rr_ratio": 2.0}})
    sig = make_signal("momentum_scalp", side=OrderSide.BUY, entry=2000.0, atr=5.0)
    rp.calculate_stops(sig)
    # ATR path: SL = 2*5 = 10 below entry; should NOT set the preserve_sl flag.
    assert sig.stop_loss == Decimal("1990.0")
    assert sig.metadata.get("preserve_structural_sl") is not True


# ── Exact geometry preserved (liquidity adjustment skipped) ────────────────

def test_liquidity_levels_do_not_alter_fixed_geometry():
    rp = make_processor({"vwap": {"sl_points": 33.0, "rr": 2.0}})
    sig = make_signal("vwap_deviation", side=OrderSide.BUY, entry=2000.0)
    # A liquidity level just below the fixed SL would normally widen it.
    sig.metadata["liquidity_levels"] = {"pdl": 1966.0, "pdh": 2100.0}
    rp.calculate_stops(sig)
    assert sig.stop_loss == Decimal("1967.0")        # unchanged by liquidity
    assert sig.take_profit == Decimal("2066.0")
