"""Unit tests for the Beast-mode volatility monitor (src/monitoring/volatility_monitor.py)."""
from datetime import datetime, timezone

import pytest

from src.monitoring.volatility_monitor import (
    BEAST,
    DEFAULT_SESSIONS,
    HOT,
    OFF_SESSION,
    QUIET,
    WARMING,
    AlertGovernor,
    BeastConfig,
    MinuteBar,
    MinuteBarBuilder,
    SessionWindow,
    active_session,
    evaluate,
)

LONDON = datetime(2026, 6, 12, 7, 30, tzinfo=timezone.utc)   # inside London open
NY = datetime(2026, 6, 12, 13, 0, tzinfo=timezone.utc)        # inside NY open
ASIA = datetime(2026, 6, 12, 3, 0, tzinfo=timezone.utc)       # outside both

CFG = BeastConfig()


def quiet_bars(n: int, base: float = 100.0, rng: float = 0.10, start: int = 0):
    """n flat-ish bars with constant range and no drift."""
    bars = []
    for i in range(n):
        bars.append(MinuteBar(start + i * 60, base, base + rng, base - rng, base, samples=10))
    return bars


def with_burst(bars, direction: int = 1, range_mult: float = 4.0, move_atr: float = 3.0):
    """Append one expansion bar with a directional close."""
    base = bars[-1].close
    rng = (bars[-1].high - bars[-1].low) * range_mult
    move = direction * move_atr * (bars[-1].high - bars[-1].low)
    close = base + move
    hi, lo = max(base, close) + rng * 0.1, min(base, close) - rng * 0.1
    start = bars[-1].start_epoch + 60
    return bars + [MinuteBar(start, base, hi, lo, close, samples=10)]


class TestSessions:
    def test_london_and_ny_windows(self):
        assert active_session(LONDON, DEFAULT_SESSIONS) == "LONDON_OPEN"
        assert active_session(NY, DEFAULT_SESSIONS) == "NY_OPEN"
        assert active_session(ASIA, DEFAULT_SESSIONS) is None

    def test_window_edges(self):
        s = SessionWindow("X", 7 * 60, 9 * 60)
        assert s.contains(datetime(2026, 6, 12, 7, 0, tzinfo=timezone.utc))
        assert not s.contains(datetime(2026, 6, 12, 9, 0, tzinfo=timezone.utc))


class TestBarBuilder:
    def test_builds_and_rolls_minutes(self):
        b = MinuteBarBuilder()
        assert b.update(0, 100.0) is None
        assert b.update(30, 101.0) is None
        bar = b.update(61, 100.5)  # next minute -> previous bar completes
        assert bar is not None
        assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 101.0, 100.0, 101.0)
        assert len(b.bars) == 1

    def test_stale_sample_dropped(self):
        b = MinuteBarBuilder()
        b.update(120, 100.0)
        assert b.update(59, 999.0) is None
        assert b.update(181, 100.0).high == 100.0  # stale 999 never entered


class TestEvaluate:
    def test_warming_until_baseline(self):
        bars = quiet_bars(CFG.min_baseline_bars)  # one short of baseline+trigger
        v = evaluate(bars, 0.02, LONDON, CFG)
        assert v.state == WARMING

    def test_quiet_market_no_alert(self):
        v = evaluate(quiet_bars(40), 0.02, LONDON, CFG)
        assert v.state == QUIET
        assert not v.triggered

    def test_beast_buy_trigger(self):
        bars = with_burst(quiet_bars(40), direction=+1)
        v = evaluate(bars, 0.02, LONDON, CFG)
        assert v.state == BEAST
        assert v.direction == "BUY"
        assert v.range_ratio > CFG.range_expansion_mult
        assert v.momentum_atr > CFG.momentum_atr_mult

    def test_beast_sell_trigger(self):
        bars = with_burst(quiet_bars(40), direction=-1)
        v = evaluate(bars, 0.02, NY, CFG)
        assert v.state == BEAST
        assert v.direction == "SELL"
        assert v.momentum_atr < 0

    def test_session_gate_blocks_offsession(self):
        bars = with_burst(quiet_bars(40))
        v = evaluate(bars, 0.02, ASIA, CFG)
        assert v.state == OFF_SESSION
        assert not v.triggered
        assert v.range_ratio is not None  # metrics still computed for display

    def test_wide_spread_vetoes(self):
        bars = with_burst(quiet_bars(40))
        atr = 0.20  # quiet bar TR
        v = evaluate(bars, spread=atr * 2, now_utc=LONDON, cfg=CFG)
        assert v.state == HOT  # conditions met but spread veto -> not BEAST
        assert any("spread" in r for r in v.reasons)

    def test_broker_max_spread_vetoes(self):
        bars = with_burst(quiet_bars(40))
        v = evaluate(bars, 0.02, LONDON, CFG, broker_max_spread=0.01)
        assert v.state == HOT

    def test_expansion_without_momentum_is_hot(self):
        bars = quiet_bars(40)
        last = bars[-1]
        # wide bar, but close back at open -> no directional momentum
        bars = bars[:-1] + [MinuteBar(last.start_epoch, last.open, last.open + 1.0,
                                      last.open - 1.0, last.open, 10)]
        v = evaluate(bars, 0.02, LONDON, CFG)
        assert v.state == HOT
        assert v.direction is None

    def test_flat_baseline_handled(self):
        bars = [MinuteBar(i * 60, 100.0, 100.0, 100.0, 100.0, 10) for i in range(40)]
        v = evaluate(bars, 0.02, LONDON, CFG)
        assert v.state == QUIET  # zero-range baseline must not divide by zero


class TestAlertGovernor:
    def test_cooldown_blocks_repeats(self):
        g = AlertGovernor(cooldown_sec=300)
        assert g.should_fire("XAUUSD", "BUY", 1000.0)
        assert not g.should_fire("XAUUSD", "BUY", 1100.0)
        assert g.should_fire("XAUUSD", "BUY", 1301.0)

    def test_directions_and_symbols_independent(self):
        g = AlertGovernor(cooldown_sec=300)
        assert g.should_fire("XAUUSD", "BUY", 1000.0)
        assert g.should_fire("XAUUSD", "SELL", 1000.0)
        assert g.should_fire("USDJPY", "BUY", 1000.0)
