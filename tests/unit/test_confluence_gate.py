"""
Unit tests for ConfluenceGate (src/strategies/confluence_gate.py).

Covers the policy matrix from combine_startegy.md (2026-05-13):
    - kill list always dropped
    - kalman_regime passes solo
    - filter-only strategies never trade alone
    - COMBO A: TREND + sbr + fib + momentum → pass
    - COMBO B: RANGE + vwap + asia_range_fade + smc_ob → pass
    - COMBO C: smc_ob + fib + momentum aligned → sniper signal with 1.5× lot
    - sniper cooldown prevents back-to-back emissions
    - window eviction expires stale confluence
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from src.core.constants import MarketRegime, OrderSide
from src.core.types import Signal, Symbol
from src.strategies.confluence_gate import ConfluenceGate


SYMBOL = "XAUUSD"


def _sym() -> Symbol:
    return Symbol(ticker=SYMBOL)


def _signal(strategy: str, side: OrderSide, entry: float | None = 2000.0) -> Signal:
    return Signal(
        strategy_name=strategy,
        symbol=_sym(),
        side=side,
        strength=0.7,
        entry_price=Decimal(str(entry)) if entry is not None else None,
    )


def _gate(**kwargs) -> ConfluenceGate:
    cfg = {"enabled": True, "window_minutes": 25.0, "sniper_lot_multiplier": 1.5, "sniper_cooldown_minutes": 60.0}
    cfg.update(kwargs)
    return ConfluenceGate(cfg)


# ── Kill list ────────────────────────────────────────────────────────────────

def test_kill_list_dropped_when_gate_enabled():
    g = _gate()
    sigs = [("breakout", _signal("breakout", OrderSide.BUY)),
            ("mean_reversion", _signal("mean_reversion", OrderSide.SELL)),
            ("supply_demand", _signal("supply_demand", OrderSide.BUY)),
            ("descending_channel_breakout", _signal("descending_channel_breakout", OrderSide.BUY)),
            ("mini_medallion", _signal("mini_medallion", OrderSide.SELL)),
            ("continuation_breakout", _signal("continuation_breakout", OrderSide.BUY))]
    assert g.filter(SYMBOL, sigs, MarketRegime.TREND) == []


def test_kill_list_dropped_even_when_gate_disabled():
    """Safety net: killed strategies never reach execution even if gate=False."""
    g = _gate(enabled=False)
    sigs = [("breakout", _signal("breakout", OrderSide.BUY)),
            ("kalman_regime", _signal("kalman_regime", OrderSide.BUY))]
    out = g.filter(SYMBOL, sigs, MarketRegime.TREND)
    assert len(out) == 1 and out[0].strategy_name == "kalman_regime"


# ── Solo allowlist ───────────────────────────────────────────────────────────

def test_kalman_regime_passes_solo():
    g = _gate()
    out = g.filter(SYMBOL, [("kalman_regime", _signal("kalman_regime", OrderSide.BUY))],
                   MarketRegime.UNKNOWN)
    assert len(out) == 1 and out[0].strategy_name == "kalman_regime"


# ── Filter-only never solo ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["momentum", "asia_range_fade", "smc_ob", "fibonacci_retracement"])
def test_filter_only_strategies_never_solo(name):
    g = _gate()
    out = g.filter(SYMBOL, [(name, _signal(name, OrderSide.BUY))], MarketRegime.TREND)
    assert out == []


# ── COMBO A — TREND ──────────────────────────────────────────────────────────

def test_combo_a_passes_when_all_three_align_in_trend():
    g = _gate()
    sigs = [
        ("sbr", _signal("sbr", OrderSide.BUY, entry=2000.0)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY, entry=1995.0)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    out = g.filter(SYMBOL, sigs, MarketRegime.TREND)
    assert len(out) == 1
    sig = out[0]
    assert sig.strategy_name == "sbr"
    assert sig.metadata["combo"] == "A"
    assert "fibonacci_retracement" in sig.metadata["confluence"]
    # Entry overridden to fib level
    assert sig.entry_price == Decimal("1995.0")
    assert sig.metadata["entry_source"] == "fibonacci_retracement"


def test_combo_a_suppressed_without_fib():
    g = _gate()
    sigs = [
        ("sbr", _signal("sbr", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    assert g.filter(SYMBOL, sigs, MarketRegime.TREND) == []


def test_combo_a_suppressed_when_momentum_disagrees():
    g = _gate()
    sigs = [
        ("sbr", _signal("sbr", OrderSide.BUY)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.SELL)),
    ]
    assert g.filter(SYMBOL, sigs, MarketRegime.TREND) == []


def test_combo_a_suppressed_outside_trend_regime():
    """Even with all 3 legs, COMBO A requires TREND regime."""
    g = _gate()
    sigs = [
        ("sbr", _signal("sbr", OrderSide.BUY)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    out = g.filter(SYMBOL, sigs, MarketRegime.RANGE)
    # sbr alone is not allowed in RANGE; sniper triple also not present (no smc).
    assert out == []


# ── COMBO B — RANGE ──────────────────────────────────────────────────────────

def test_combo_b_passes_in_range_with_full_trio():
    g = _gate()
    sigs = [
        ("vwap", _signal("vwap", OrderSide.SELL, entry=2010.0)),
        ("asia_range_fade", _signal("asia_range_fade", OrderSide.SELL)),
        ("smc_ob", _signal("smc_ob", OrderSide.SELL, entry=2012.0)),
    ]
    out = g.filter(SYMBOL, sigs, MarketRegime.RANGE)
    assert len(out) == 1
    sig = out[0]
    assert sig.metadata["combo"] == "B"
    # SMC OB entry takes over the vwap signal's entry
    assert sig.entry_price == Decimal("2012.0")
    assert sig.metadata["entry_source"] == "smc_ob"


def test_combo_b_suppressed_outside_range():
    g = _gate()
    sigs = [
        ("vwap", _signal("vwap", OrderSide.SELL)),
        ("asia_range_fade", _signal("asia_range_fade", OrderSide.SELL)),
        ("smc_ob", _signal("smc_ob", OrderSide.SELL)),
    ]
    # vwap is filter-only outside RANGE → suppressed; sniper not active (no fib).
    assert g.filter(SYMBOL, sigs, MarketRegime.TREND) == []


# ── COMBO C — Sniper ─────────────────────────────────────────────────────────

def test_combo_c_sniper_fires_any_regime_with_1_5x_multiplier():
    g = _gate()
    sigs = [
        ("smc_ob", _signal("smc_ob", OrderSide.BUY, entry=1990.0)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY, entry=1992.0)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    out = g.filter(SYMBOL, sigs, MarketRegime.UNKNOWN)
    # Sniper emits ONE synthesized signal
    sniper = [s for s in out if s.strategy_name == "combo_sniper"]
    assert len(sniper) == 1
    s = sniper[0]
    assert s.side == OrderSide.BUY
    assert s.metadata["combo"] == "C"
    assert s.metadata["lot_size_multiplier"] == 1.5


def test_combo_c_respects_cooldown():
    """A second triple-alignment within the cooldown must not refire sniper."""
    g = _gate(sniper_cooldown_minutes=60.0)
    base = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    sigs = [
        ("smc_ob", _signal("smc_ob", OrderSide.BUY)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    out1 = g.filter(SYMBOL, sigs, MarketRegime.UNKNOWN, now=base)
    out2 = g.filter(SYMBOL, sigs, MarketRegime.UNKNOWN, now=base + timedelta(minutes=10))
    assert any(s.strategy_name == "combo_sniper" for s in out1)
    assert not any(s.strategy_name == "combo_sniper" for s in out2)


def test_combo_c_does_not_fire_on_mixed_sides():
    g = _gate()
    sigs = [
        ("smc_ob", _signal("smc_ob", OrderSide.BUY)),
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.SELL)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    assert g.filter(SYMBOL, sigs, MarketRegime.UNKNOWN) == []


# ── Window behaviour ─────────────────────────────────────────────────────────

def test_confluence_window_carries_across_ticks():
    """Fib fires on tick 1, sbr+momentum fire on tick 2 within window → COMBO A."""
    g = _gate(window_minutes=25.0)
    t1 = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=10)
    # Tick 1: fib alone — gets recorded but not executed.
    out1 = g.filter(SYMBOL, [
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY, entry=1995.0)),
    ], MarketRegime.TREND, now=t1)
    assert out1 == []
    # Tick 2: sbr + momentum — fib is still in window, COMBO A passes.
    out2 = g.filter(SYMBOL, [
        ("sbr", _signal("sbr", OrderSide.BUY, entry=2000.0)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ], MarketRegime.TREND, now=t2)
    assert len(out2) == 1
    assert out2[0].metadata["combo"] == "A"
    # Cross-tick entry override should still apply
    assert out2[0].entry_price == Decimal("1995.0")


def test_stale_confluence_evicted_after_window():
    g = _gate(window_minutes=25.0)
    t1 = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    t2 = t1 + timedelta(minutes=30)  # past 25min window
    g.filter(SYMBOL, [
        ("fibonacci_retracement", _signal("fibonacci_retracement", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ], MarketRegime.TREND, now=t1)
    # 30min later, sbr fires — confluence has expired.
    out = g.filter(SYMBOL, [
        ("sbr", _signal("sbr", OrderSide.BUY)),
    ], MarketRegime.TREND, now=t2)
    assert out == []


# ── Passthrough mode ─────────────────────────────────────────────────────────

def test_disabled_gate_passes_solo_strategies_through():
    g = _gate(enabled=False)
    sigs = [
        ("kalman_regime", _signal("kalman_regime", OrderSide.BUY)),
        ("sbr", _signal("sbr", OrderSide.BUY)),
        ("momentum", _signal("momentum", OrderSide.BUY)),
    ]
    out = g.filter(SYMBOL, sigs, MarketRegime.TREND)
    # All non-kill-list signals pass through unchanged when gate disabled.
    assert {s.strategy_name for s in out} == {"kalman_regime", "sbr", "momentum"}
