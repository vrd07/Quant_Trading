"""Unit tests for backtest.md §3 strict fill model.

Invariants we lock down:
  1. Market entries always slip against the trader (BUY higher, SELL lower).
  2. Stop fills are at least as bad as the stop price + slippage; gappy bars
     fill at the bar extremum (worse than the slipped stop).
  3. TP limit fills never benefit the trader — exact at limit, or no fill.
  4. News-active widens the spread by 3×.
"""

from decimal import Decimal

import pytest

from src.backtest.fill_model import (
    StrictFillModel,
    FillContext,
    NEWS_SPREAD_MULT,
    SLIPPAGE_SAFETY_MULT,
)
from src.core.constants import OrderSide, PositionSide
from src.core.types import Symbol


@pytest.fixture
def xauusd():
    return Symbol(
        ticker="XAUUSD",
        pip_value=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


@pytest.fixture
def model():
    return StrictFillModel()


def _ctx(open_=2000.0, high=2010.0, low=1990.0, close=2005.0, hour=13, news=False):
    return FillContext(
        bar_open=Decimal(str(open_)),
        bar_high=Decimal(str(high)),
        bar_low=Decimal(str(low)),
        bar_close=Decimal(str(close)),
        hour_utc=hour,
        news_active=news,
    )


# ---- 1. market entry slip direction ----

def test_buy_market_pays_more_than_signal(model, xauusd):
    fill = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx())
    assert fill > Decimal("2000"), "BUY must slip up"

def test_sell_market_receives_less_than_signal(model, xauusd):
    fill = model.market_fill(xauusd, OrderSide.SELL, Decimal("2000"), _ctx())
    assert fill < Decimal("2000"), "SELL must slip down"


def test_market_slip_includes_safety_multiplier(model, xauusd):
    """Slip distance must be ≥ raw_slippage × 1.5 (slippage component alone)."""
    base_slip = Decimal("0.005")  # XAU default in price units
    fill = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx(news=False))
    slip_component = fill - Decimal("2000")
    # spread/2 + 1.5×slip ≥ 1.5×0.005 = 0.0075
    assert slip_component >= base_slip * SLIPPAGE_SAFETY_MULT


# ---- 2. stop fill realism ----

def test_long_sl_fills_at_or_below_stop(model, xauusd):
    """LONG SL is a sell-stop — fill price <= stop price (worse for us)."""
    stop = Decimal("1995")
    ctx = _ctx(low=1994.0)  # bar dipped slightly below stop, no gap
    fill = model.stop_fill(xauusd, PositionSide.LONG, stop, ctx)
    assert fill <= stop, "LONG SL must fill at or below stop"


def test_long_sl_gap_fills_at_bar_low(model, xauusd):
    """When the bar gaps far below the stop, SL fills at the LOW (gap punishment)."""
    stop = Decimal("1995")
    ctx = _ctx(low=1980.0)  # 15-point gap below stop, way past slippage
    fill = model.stop_fill(xauusd, PositionSide.LONG, stop, ctx)
    assert fill == Decimal("1980.0"), "Gappy long SL must fill at bar low"


def test_short_sl_fills_at_or_above_stop(model, xauusd):
    stop = Decimal("2005")
    ctx = _ctx(high=2006.0)
    fill = model.stop_fill(xauusd, PositionSide.SHORT, stop, ctx)
    assert fill >= stop, "SHORT SL must fill at or above stop"


def test_short_sl_gap_fills_at_bar_high(model, xauusd):
    stop = Decimal("2005")
    ctx = _ctx(high=2025.0)
    fill = model.stop_fill(xauusd, PositionSide.SHORT, stop, ctx)
    assert fill == Decimal("2025.0"), "Gappy short SL must fill at bar high"


# ---- 3. limit fill (TP) ----

def test_long_tp_only_fills_when_high_reaches_limit(model, xauusd):
    tp = Decimal("2020")
    # Bar high doesn't reach: no fill.
    assert model.limit_fill(xauusd, PositionSide.LONG, tp, _ctx(high=2015.0)) is None
    # Bar high reaches: fill exactly at limit (no positive slippage).
    fill = model.limit_fill(xauusd, PositionSide.LONG, tp, _ctx(high=2025.0))
    assert fill == tp


def test_short_tp_fills_at_limit_no_benefit(model, xauusd):
    """Even if the bar dives way below the TP, we still fill at the limit, not the low."""
    tp = Decimal("1980")
    fill = model.limit_fill(xauusd, PositionSide.SHORT, tp, _ctx(low=1950.0))
    assert fill == tp


# ---- 4. news multiplier ----

def test_news_active_widens_spread(model, xauusd):
    quiet = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx(news=False))
    noisy = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx(news=True))
    quiet_slip = quiet - Decimal("2000")
    noisy_slip = noisy - Decimal("2000")
    # Noisy slip must include 3× the spread component, so it must be strictly
    # larger than quiet slip.
    assert noisy_slip > quiet_slip, "News-active must widen the spread"


# ---- 5. hour-of-day curve sanity ----

def test_thin_hour_spread_wider_than_overlap(model, xauusd):
    """22:00 UTC (Asia handover) should produce wider spread than 14:00 UTC (LDN/NY)."""
    fill_overlap = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx(hour=14))
    fill_thin = model.market_fill(xauusd, OrderSide.BUY, Decimal("2000"), _ctx(hour=22))
    assert (fill_thin - Decimal("2000")) > (fill_overlap - Decimal("2000"))
