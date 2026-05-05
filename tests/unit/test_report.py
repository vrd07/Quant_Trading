"""Smoke tests for backtest.md §9 report generator.

Verifies every emitter writes a non-empty file in the right place.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.backtest.backtest_engine import BacktestResult
from src.backtest.ensemble_engine import EnsembleResult, StrategyAttribution
from src.backtest.report import (
    ReportContext,
    write_summary_md,
    write_per_strategy_md,
    write_ensemble_md,
    write_trade_log,
    write_equity_curves_png,
    write_failures_log,
    write_walk_forward_metrics,
)
from src.backtest.tiered_retune import Gates, RetuneResult
from src.backtest.walk_forward_driver import (
    WalkForwardDriverResult,
    WindowOutcome,
    WindowSpec,
)


def _stub_result(strategy: str = "stub", *, pf: float = 1.6, dwr: float = 0.72,
                 worst_r: float = -1.5, sharpe: float = 1.5,
                 max_dd_pct: float = -8.0, total_trades: int = 200) -> BacktestResult:
    eq = pd.Series(
        [10000, 10100, 10250, 10180, 10300, 10400],
        index=pd.date_range("2025-01-01", periods=6, freq="D", tz="UTC"),
    )
    return BacktestResult(
        total_return=400.0,
        total_return_pct=4.0,
        sharpe_ratio=sharpe,
        sortino_ratio=sharpe * 1.1,
        max_drawdown=-300.0,
        max_drawdown_pct=max_dd_pct,
        win_rate=55.0,
        profit_factor=pf,
        expectancy=2.0,
        total_trades=total_trades,
        winning_trades=int(total_trades * 0.55),
        losing_trades=total_trades - int(total_trades * 0.55),
        avg_win=20.0,
        avg_loss=-15.0,
        largest_win=80.0,
        largest_loss=-50.0,
        equity_curve=eq,
        trades=[
            {"strategy": strategy, "symbol": "XAUUSD", "side": "buy",
             "entry_price": 2000, "exit_price": 2010, "pnl": 10.0,
             "timestamp": "2025-01-02 12:00:00"},
        ],
        daily_returns=pd.Series(dtype=float),
        daily_win_rate=dwr,
        worst_day_r=worst_r,
        trading_days=120,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> ReportContext:
    return ReportContext.create(
        output_root=tmp_path,
        timestamp=datetime(2026, 5, 5, 12, 0),
        git_sha="abcd123",
    )


def test_creates_report_dir(ctx):
    assert ctx.out_dir.exists()
    assert (ctx.out_dir / "per_strategy").is_dir()
    # Name follows backtest_<date>_<sha>
    assert ctx.out_dir.name == "backtest_2026-05-05_abcd123"


def test_summary_md_includes_every_strategy(ctx):
    results = {"breakout": _stub_result("breakout"), "vwap": _stub_result("vwap", pf=1.0)}
    path = write_summary_md(ctx, results)
    text = path.read_text()
    assert "breakout" in text and "vwap" in text
    assert "Backtest Summary" in text
    # vwap fails G3 (PF<1.4), summary should reflect at least one ❌
    assert "❌" in text


def test_per_strategy_md_has_walk_forward_section_when_provided(ctx):
    result = _stub_result("kalman_regime")
    spec = WindowSpec(
        idx=0,
        is_start=pd.Timestamp("2025-01-01", tz="UTC"),
        is_end=pd.Timestamp("2025-09-01", tz="UTC"),
        oos_start=pd.Timestamp("2025-09-01", tz="UTC"),
        oos_end=pd.Timestamp("2026-01-01", tz="UTC"),
    )
    rt = RetuneResult(
        passed=True, tier=1,
        winning_params={"lookback": 20},
        oos_result=result, gate_status={}, n_combos_evaluated=10,
    )
    wf = WalkForwardDriverResult(windows=[WindowOutcome(spec, rt, result)])
    path = write_per_strategy_md(ctx, "kalman_regime", result, walk_forward=wf)
    text = path.read_text()
    assert "Aggregate" in text and "Walk-forward" in text
    assert "lookback" in text


def test_ensemble_md_lists_attribution(ctx):
    er = EnsembleResult(
        aggregate=_stub_result("ensemble"),
        per_strategy={
            "breakout": StrategyAttribution(strategy="breakout", trades=10,
                                            wins=6, losses=4, gross_pnl=50.0),
            "vwap": StrategyAttribution(strategy="vwap", trades=4,
                                        wins=1, losses=3, gross_pnl=-30.0),
        },
    )
    path = write_ensemble_md(ctx, er)
    text = path.read_text()
    assert "breakout" in text and "vwap" in text
    assert "Per-strategy attribution" in text


def test_trade_log_writes_csv_when_no_parquet(ctx):
    trades = [
        {"strategy": "breakout", "pnl": 10.0, "side": "buy"},
        {"strategy": "breakout", "pnl": -5.0, "side": "sell"},
    ]
    path = write_trade_log(ctx, trades)
    assert path is not None
    assert path.exists()
    # Either parquet or CSV — but the file must contain both rows
    if path.suffix == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_parquet(path)
    assert len(df) == 2


def test_equity_curves_png_written_when_data_present(ctx):
    results = {"a": _stub_result("a"), "b": _stub_result("b")}
    path = write_equity_curves_png(ctx, results)
    assert path is not None and path.exists()
    # PNG file > 0 bytes
    assert path.stat().st_size > 0


def test_failures_log_omits_passing_strategies(ctx):
    results = {
        "passing": _stub_result("passing"),  # default stub passes G1..G6
        "broken":  _stub_result("broken", pf=0.5, dwr=0.20, worst_r=-3.5, sharpe=-0.2),
    }
    path = write_failures_log(ctx, results)
    text = path.read_text()
    assert "broken" in text
    assert "passing" not in text


def test_walk_forward_metrics_written_when_windows_exist(ctx):
    spec = WindowSpec(
        idx=0,
        is_start=pd.Timestamp("2025-01-01", tz="UTC"),
        is_end=pd.Timestamp("2025-09-01", tz="UTC"),
        oos_start=pd.Timestamp("2025-09-01", tz="UTC"),
        oos_end=pd.Timestamp("2026-01-01", tz="UTC"),
    )
    rt = RetuneResult(
        passed=True, tier=1, winning_params={"a": 1},
        oos_result=_stub_result(), gate_status={}, n_combos_evaluated=5,
    )
    wf = WalkForwardDriverResult(windows=[WindowOutcome(spec, rt, _stub_result())])
    path = write_walk_forward_metrics(ctx, wf)
    assert path is not None and path.exists()
