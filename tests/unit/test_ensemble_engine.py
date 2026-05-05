"""Smoke tests for the Phase 2 ensemble engine.

These don't assert performance numbers — they verify the pipeline is wired
correctly end-to-end: StrategyManager → RiskEngine → SimulatedBroker → metrics
with per-strategy attribution.
"""

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.backtest.ensemble_engine import EnsembleBacktestEngine, StrategyAttribution
from src.core.types import Symbol


@pytest.fixture
def xauusd():
    return Symbol(
        ticker="XAUUSD",
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("100"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
        commission_per_lot=Decimal("0"),
        leverage=Decimal("30"),
        min_stops_distance=Decimal("0"),
    )


@pytest.fixture
def synthetic_bars():
    """3 months of 5-minute bars with a gentle drift + noise — enough to keep
    momentum/breakout/etc. occasionally signalling."""
    np.random.seed(7)
    n = 3 * 30 * 24 * 12  # ~3mo of 5m bars
    idx = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    drift = np.linspace(0, 50, n)
    noise = np.cumsum(np.random.normal(0, 0.5, n))
    close = 2000 + drift + noise
    high = close + np.abs(np.random.normal(0, 0.4, n))
    low = close - np.abs(np.random.normal(0, 0.4, n))
    open_ = np.roll(close, 1); open_[0] = close[0]
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


def _minimal_config():
    """Just enough config for one strategy to fire."""
    return {
        "risk": {
            "risk_per_trade_pct": 0.01,
            "max_daily_loss_pct": 0.05,
            "max_drawdown_pct": 0.20,
            "max_positions": 3,
            "trailing_stop": {"enabled": False},
            "min_sl_distance": {"XAUUSD": 0.5},
        },
        "strategies": {
            "breakout": {
                "enabled": True,
                "timeframe": "5m",
                "lookback_period": 20,
                "atr_period": 14,
                "min_atr_threshold": 0.0,
            },
        },
        "symbols": {
            "XAUUSD": {
                "enabled": True,
                "pip_value": 0.01,
                "min_lot": 0.01,
                "max_lot": 100,
                "lot_step": 0.01,
                "value_per_lot": 100,
                "commission_per_lot": 0,
            }
        },
    }


def test_ensemble_runs_and_returns_aggregate(xauusd, synthetic_bars):
    """End-to-end smoke: engine runs without raising, produces an aggregate."""
    engine = EnsembleBacktestEngine(
        symbol=xauusd,
        full_config=_minimal_config(),
        initial_capital=Decimal("10000"),
        slippage_model="strict",
    )
    result = engine.run(synthetic_bars, min_history=50, max_window=500)
    assert result.aggregate is not None
    # Equity curve should have entries
    assert len(result.aggregate.equity_curve) > 0


def test_per_strategy_attribution_keyed_by_name(xauusd, synthetic_bars):
    """Trades should be attributable back to the strategy that emitted the signal."""
    engine = EnsembleBacktestEngine(
        symbol=xauusd,
        full_config=_minimal_config(),
        initial_capital=Decimal("10000"),
        slippage_model="strict",
    )
    result = engine.run(synthetic_bars, min_history=50, max_window=500)
    # If any trades fired, every one of them must carry a non-empty strategy tag.
    if result.aggregate.total_trades > 0:
        assert all(isinstance(k, str) and len(k) > 0 for k in result.per_strategy.keys())
        total = sum(a.trades for a in result.per_strategy.values())
        # Per-strategy trade count must reconcile with aggregate.
        assert total == result.aggregate.total_trades


def test_signal_cooldown_zeroed_in_backtest(xauusd, synthetic_bars):
    """Live cooldown is wall-clock based and breaks replay; engine must zero it."""
    cfg = _minimal_config()
    cfg["strategies"]["signal_cooldown_minutes"] = 30
    engine = EnsembleBacktestEngine(
        symbol=xauusd,
        full_config=cfg,
        initial_capital=Decimal("10000"),
    )
    # The engine deep-copies and overrides — original input dict is preserved.
    assert cfg["strategies"]["signal_cooldown_minutes"] == 30
    # Internal config must show 0
    assert engine.full_config["strategies"]["signal_cooldown_minutes"] == 0
