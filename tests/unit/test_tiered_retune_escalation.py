"""Tests for backtest.md §7 tier-2 escalation when tier-1 hits G2.

Locks down: when every tier-1 combo breaches G2 (worst-day-floor), the
retune must escalate to tier 2 (which sweeps risk knobs — wider stops are
exactly the cure). When the failure is zero-trades dominated, abort —
no risk knob saves a strategy with broken entry logic.
"""

from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.backtest_engine import BacktestResult
from src.backtest.grid_loader import Grid
from src.backtest.tiered_retune import (
    CombosOutcome,
    Gates,
    RetuneResult,
    TieredRetune,
)
from src.core.types import Symbol


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _stub_result(*, sharpe=1.5, pf=1.6, dwr=0.72, worst_r=-1.5, max_dd=-5.0,
                 trades=200, ret=200.0) -> BacktestResult:
    return BacktestResult(
        total_return=ret, total_return_pct=ret / 10000 * 100,
        sharpe_ratio=sharpe, sortino_ratio=sharpe,
        max_drawdown=max_dd * 100, max_drawdown_pct=max_dd,
        win_rate=55.0, profit_factor=pf, expectancy=2.0,
        total_trades=trades, winning_trades=int(trades * 0.55),
        losing_trades=trades - int(trades * 0.55),
        avg_win=20.0, avg_loss=-15.0, largest_win=80.0, largest_loss=-50.0,
        equity_curve=pd.Series([10000, 10200], index=pd.to_datetime(["2025-01-01", "2025-01-02"])),
        trades=[], daily_returns=pd.Series(dtype=float),
        daily_win_rate=dwr, worst_day_r=worst_r, trading_days=120,
    )


def _make_retune(g2_dominated_tier1: bool = True, *,
                 zero_trades: bool = False) -> TieredRetune:
    """Build a TieredRetune with patched _evaluate_combos / _run_one to
    simulate tier-1 G2 dominance vs zero-trades dominance."""
    sym = Symbol(ticker="XAUUSD", value_per_lot=Decimal("100"))

    # Minimal grid stub — only the methods the retune uses.
    grid = Grid.__new__(Grid)
    grid.strategy = "stub"
    grid.anchor = {}
    grid.max_combos = {"tier1": 5, "tier2": 5, "tier3": 5}
    grid.tier1_entry = {"a": [1, 2, 3]}
    grid.tier2_risk = {"sl_mult": [1.0, 2.0]}
    grid.tier3_filters = {}
    grid.presets = {}
    grid.tier1_combos = lambda: [{"a": 1}, {"a": 2}, {"a": 3}]
    grid.tier2_sweeps = lambda: [{"sl_mult": 1.0}, {"sl_mult": 2.0}]
    grid.tier3_combos = lambda: []
    grid.resolve = lambda combo: combo
    grid.build_config = lambda combo: dict(combo)

    bars = pd.DataFrame(
        {"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1},
        index=pd.date_range("2025-01-01", periods=400, freq="1h", tz="UTC"),
    )

    rt = TieredRetune(
        strategy_class=type("StubStrategy", (), {}),
        symbol=sym,
        is_bars=bars[:300],
        oos_bars=bars[300:],
        grid=grid,
        full_config={"strategies": {}},
        initial_capital=Decimal("10000"),
    )
    return rt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_g2_dominated_tier1_escalates_to_tier2():
    """When all tier-1 combos breach G2, tier 2 must run with the best-IS
    combo as the anchor — and if a tier-2 sweep clears G2 + the OOS gates,
    the whole retune passes."""
    rt = _make_retune(g2_dominated_tier1=True)

    # Patch _run_one to return:
    #  • Tier 1 combos: all G2-violating (worst_r=-3.0)
    #  • Tier 2 sweeps: one with wider stop that DOES pass everything
    runs = {"tier1": 0, "tier2": 0}
    def fake_run(combo, bars):
        if combo.get("sl_mult") is None:
            # tier 1 — all violate G2 (worst_r=-3 < -2)
            runs["tier1"] += 1
            return _stub_result(sharpe=1.0, worst_r=-3.0)
        else:
            # tier 2 sweep — sl_mult=2.0 clears G2
            runs["tier2"] += 1
            if combo.get("sl_mult") == 2.0:
                # passes all gates including G2
                return _stub_result(sharpe=1.5, pf=1.6, dwr=0.75, worst_r=-1.4)
            return _stub_result(sharpe=0.8, worst_r=-2.5)

    with patch.object(rt, "_run_one", side_effect=fake_run):
        result = rt.run()

    assert runs["tier1"] >= 3, "tier 1 must evaluate every combo"
    assert runs["tier2"] >= 1, "tier 2 must run despite tier-1 full veto"
    # Result must escalate beyond tier 0 (the abort path)
    assert result.tier in (1, 2, 3), f"expected escalation, got tier {result.tier}"


def test_zero_trades_tier1_aborts_immediately():
    """If tier-1 fails because every combo produces zero trades, abort —
    risk knobs don't fix broken entry logic."""
    rt = _make_retune()

    runs = {"tier1": 0, "tier2": 0}
    def fake_run(combo, bars):
        if combo.get("sl_mult") is None:
            runs["tier1"] += 1
            return _stub_result(trades=0, sharpe=0.0, worst_r=0.0, ret=0.0)
        runs["tier2"] += 1
        return _stub_result(sharpe=2.0, pf=2.0, dwr=0.8, worst_r=-1.0)

    with patch.object(rt, "_run_one", side_effect=fake_run):
        result = rt.run()

    assert runs["tier1"] >= 3
    assert runs["tier2"] == 0, "must NOT escalate to tier 2 on zero-trades dominance"
    assert result.passed is False
    assert result.tier == 0
    assert "zero-trades" in (result.reason or "").lower()


def test_tier1_winner_path_unchanged():
    """Sanity: when tier 1 has a clean winner that passes all OOS gates,
    the escalation patch must not change the early-success path."""
    rt = _make_retune()

    def fake_run(combo, bars):
        return _stub_result(sharpe=2.0, pf=1.8, dwr=0.85, worst_r=-1.0)

    with patch.object(rt, "_run_one", side_effect=fake_run):
        result = rt.run()

    assert result.passed is True
    assert result.tier == 1
