"""Tests for per-trade R from stop_loss (backtest.md §1, task #7).

The new G2 semantics:
  • Each trade carries r_dollars = |entry-stop| × volume × value_per_lot
  • Day's R = first losing trade's r_dollars (fallback: first trade)
  • Worst day R = min over days of (day_pnl / day_R)
  • Trades without r_dollars fall back to the legacy account-relative R.
"""

import pandas as pd

from src.backtest.metrics import PerformanceMetrics


def _trade(ts: str, pnl: float, r: float = None) -> dict:
    t = {"timestamp": ts, "pnl": pnl}
    if r is not None:
        t["r_dollars"] = r
    return t


def test_per_trade_r_used_when_present():
    """When trades carry r_dollars, the legacy fallback parameter must be IGNORED."""
    trades = [
        _trade("2025-01-02 09:00", -100.0, r=50.0),  # -2R
        _trade("2025-01-02 14:00",  +20.0, r=50.0),
    ]
    # Even with a HUGE legacy R, the per-trade R should be used.
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=1_000_000)
    assert worst == (-100 + 20) / 50.0  # = -1.6


def test_first_losing_trade_r_drives_day_r():
    """Spec: 'risk-unit at the start of that day's first losing trade'."""
    trades = [
        _trade("2025-01-02 09:00", +30.0, r=10.0),    # winner; not the day R
        _trade("2025-01-02 10:00", -50.0, r=25.0),    # FIRST LOSING TRADE — day R = 25
        _trade("2025-01-02 11:00", -20.0, r=999.0),   # subsequent loss — ignored for R
    ]
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=100)
    # day_pnl = +30 -50 -20 = -40; day_R = 25; worst = -1.6
    assert worst == -40.0 / 25.0


def test_first_trade_r_used_when_no_loss():
    """If no trade lost on that day, fall back to the first trade's R."""
    trades = [
        _trade("2025-01-02 09:00", +30.0, r=10.0),  # first trade R = 10
        _trade("2025-01-02 14:00", +5.0,  r=999.0),
    ]
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=100)
    # day_pnl positive — should not produce a negative worst, since worst starts at 0.
    assert worst == 0.0  # no negative day = 0 worst


def test_legacy_fallback_when_r_dollars_missing():
    """Trades without r_dollars use the passed-in legacy param."""
    trades = [
        _trade("2025-01-02 09:00", -150.0),  # no r_dollars
        _trade("2025-01-02 11:00",  +25.0),
    ]
    # legacy R = 50
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=50)
    assert worst == (-150 + 25) / 50.0  # = -2.5


def test_picks_worst_day_across_multiple_days():
    trades = [
        # day 1: -1.0R
        _trade("2025-01-02 09:00", -10.0, r=10.0),
        # day 2: -3.0R (worst)
        _trade("2025-01-03 09:00", -30.0, r=10.0),
        # day 3: +1.0R
        _trade("2025-01-04 09:00", +10.0, r=10.0),
    ]
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=100)
    assert worst == -3.0


def test_no_trades_returns_zero():
    assert PerformanceMetrics.calculate_worst_day_r([], 100.0) == 0.0


def test_zero_r_dollars_falls_back_to_legacy():
    """Defensive: r_dollars=0 means 'no real R recorded' — fall back to legacy."""
    trades = [_trade("2025-01-02 09:00", -100.0, r=0.0)]
    worst = PerformanceMetrics.calculate_worst_day_r(trades, risk_per_trade_dollars=50)
    assert worst == -2.0
