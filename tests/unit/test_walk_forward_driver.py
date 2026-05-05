"""Unit tests for the walk-forward driver (backtest.md §5).

Locks down:
  • Window generation matches §5.1 (rolling 70/30, monthly roll, count fits span).
  • Aggregate metrics use OOS slices only.
  • G7 (OOS green %) computed from per-window OOS net P&L, not IS.
  • Parameter stability (§5.3) flags drifting params, accepts steady ones.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional, List
from unittest.mock import patch

import pandas as pd
import pytest

from src.backtest.walk_forward_driver import (
    WalkForwardDriver,
    WalkForwardDriverResult,
    WindowOutcome,
    WindowSpec,
)
from src.backtest.tiered_retune import Gates, RetuneResult


@dataclass
class _StubResult:
    """Minimal stand-in for BacktestResult (only the fields the driver reads)."""
    sharpe_ratio: float = 1.5
    profit_factor: float = 1.5
    daily_win_rate: float = 0.72
    worst_day_r: float = -1.5
    total_return: float = 100.0


def _bars(start: str, end: str, freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range(start, end, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 1.0},
        index=idx,
    )


# ---------------------------------------------------------------------------
# 1. window generation
# ---------------------------------------------------------------------------

def test_window_generation_5y_count():
    """5y of bars × 1mo roll × 12.5mo window → ~46-50 windows depending on edges."""
    bars = _bars("2021-01-01", "2025-12-31", freq="1d")
    drv = WalkForwardDriver.__new__(WalkForwardDriver)
    drv.bars = bars
    drv.is_months = 8.4
    drv.oos_months = 4.1
    drv.roll_months = 1.0

    windows = drv.generate_windows()
    # Hand-checked: with 12.5mo windows monthly-rolled over 60 months the last
    # start is at month 47.5 → 48 windows. Allow a one-window margin.
    assert 45 <= len(windows) <= 50, f"got {len(windows)}"


def test_window_split_is_70_30():
    bars = _bars("2021-01-01", "2025-12-31", freq="1d")
    drv = WalkForwardDriver.__new__(WalkForwardDriver)
    drv.bars = bars
    drv.is_months = 8.4
    drv.oos_months = 4.1
    drv.roll_months = 1.0
    w = drv.generate_windows()[0]
    is_days = (w.is_end - w.is_start).days
    oos_days = (w.oos_end - w.oos_start).days
    ratio = oos_days / (is_days + oos_days)
    assert 0.27 <= ratio <= 0.33, f"OOS ratio drifted: {ratio:.3f}"


def test_windows_roll_one_month():
    bars = _bars("2021-01-01", "2025-12-31", freq="1d")
    drv = WalkForwardDriver.__new__(WalkForwardDriver)
    drv.bars = bars
    drv.is_months = 8.4
    drv.oos_months = 4.1
    drv.roll_months = 1.0
    w = drv.generate_windows()
    if len(w) >= 2:
        delta = (w[1].is_start - w[0].is_start).days
        assert 28 <= delta <= 32, f"roll != ~30d: {delta}"


# ---------------------------------------------------------------------------
# 2. aggregate metrics
# ---------------------------------------------------------------------------

def _outcome(idx: int, *, oos_pf: float = 1.5, oos_ret: float = 100.0,
             oos_sharpe: float = 1.5, oos_dwr: float = 0.72,
             worst_r: float = -1.5, params: Optional[Dict] = None,
             passed: bool = True) -> WindowOutcome:
    spec = WindowSpec(
        idx=idx,
        is_start=pd.Timestamp("2021-01-01", tz="UTC"),
        is_end=pd.Timestamp("2021-09-01", tz="UTC"),
        oos_start=pd.Timestamp("2021-09-01", tz="UTC"),
        oos_end=pd.Timestamp("2022-01-01", tz="UTC"),
    )
    oos = _StubResult(
        sharpe_ratio=oos_sharpe,
        profit_factor=oos_pf,
        daily_win_rate=oos_dwr,
        worst_day_r=worst_r,
        total_return=oos_ret,
    )
    retune = RetuneResult(
        passed=passed, tier=1 if passed else 0,
        winning_params=params or {},
        oos_result=oos,
        gate_status={},
        n_combos_evaluated=10,
    )
    return WindowOutcome(spec=spec, retune=retune, oos_result=oos)


def test_oos_green_pct_uses_total_return():
    res = WalkForwardDriverResult(windows=[
        _outcome(0, oos_ret=+50),
        _outcome(1, oos_ret=+10),
        _outcome(2, oos_ret=-20),
        _outcome(3, oos_ret=+5),
    ])
    assert res.oos_profitable_window_pct == 0.75


def test_worst_oos_day_r_picks_minimum():
    res = WalkForwardDriverResult(windows=[
        _outcome(0, worst_r=-1.0),
        _outcome(1, worst_r=-2.5),  # G2 breach
        _outcome(2, worst_r=-0.5),
    ])
    assert res.worst_oos_day_r == -2.5


def test_aggregates_skip_missing_oos():
    """Crashed windows have oos_result=None and shouldn't poison averages."""
    res = WalkForwardDriverResult(windows=[
        _outcome(0, oos_pf=1.5),
        _outcome(1, oos_pf=2.0),
    ])
    # add a window where retune returned no oos_result
    bad = _outcome(2)
    bad.oos_result = None
    res.windows.append(bad)
    assert res.avg_oos_pf == pytest.approx(1.75)


# ---------------------------------------------------------------------------
# 3. parameter stability (§5.3)
# ---------------------------------------------------------------------------

def test_parameter_stability_flags_drift():
    """Param swinging across windows → stability fraction < 0.8."""
    res = WalkForwardDriverResult(windows=[
        _outcome(0, params={"lookback": 10}),
        _outcome(1, params={"lookback": 30}),
        _outcome(2, params={"lookback": 50}),  # 5× spread vs first
        _outcome(3, params={"lookback": 70}),
    ])
    stability = res.parameter_stability(tolerance_pct=0.20)
    assert stability["lookback"] < 0.80, "drifting param should flag unstable"


def test_parameter_stability_accepts_tight_cluster():
    """Same-ish param across windows → ≥ 0.8."""
    res = WalkForwardDriverResult(windows=[
        _outcome(0, params={"lookback": 20}),
        _outcome(1, params={"lookback": 22}),
        _outcome(2, params={"lookback": 19}),
        _outcome(3, params={"lookback": 21}),
        _outcome(4, params={"lookback": 23}),
    ])
    stability = res.parameter_stability(tolerance_pct=0.20)
    assert stability["lookback"] >= 0.80


def test_parameter_stability_ignores_failed_windows():
    """Only PASSED windows should contribute to the param trajectory."""
    res = WalkForwardDriverResult(windows=[
        _outcome(0, params={"lookback": 20}, passed=True),
        _outcome(1, params={"lookback": 5},  passed=False),  # degenerate failure params
        _outcome(2, params={"lookback": 21}, passed=True),
        _outcome(3, params={"lookback": 22}, passed=True),
    ])
    stability = res.parameter_stability(tolerance_pct=0.20)
    # Only the three passed windows → all within ±20% of median(20,21,22)=21.
    assert stability["lookback"] == 1.0
