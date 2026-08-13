"""Trade rows must carry the regime so diagnostics can split TREND from RANGE.

These are behavioural tests, not source-text assertions: a stub strategy
emits exactly one signal tagged ``MarketRegime.RANGE``, a small synthetic bar
frame is run through each engine end to end, and the recorded trade row is
inspected directly. ``MarketRegime.RANGE.value`` (not the literal "range") is
used for the comparison since the enum's own values are uppercase
("TREND"/"RANGE"/"UNKNOWN") -- ties the assertion to the real contract
instead of an assumed casing.
"""
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.backtest.backtest_engine import BacktestEngine
from src.backtest.ensemble_engine import EnsembleBacktestEngine
from src.core.constants import MarketRegime, OrderSide
from src.core.types import Symbol
from src.strategies.base_strategy import BaseStrategy
from src.strategies.strategy_manager import StrategyManager


class _RangeOnceStrategy(BaseStrategy):
    """Test double: fires exactly one BUY signal tagged MarketRegime.RANGE on
    the first bar it sees, then goes silent. Uses the same `_create_signal`
    helper real strategies use, so the Signal it builds is representative."""

    def __init__(self, symbol, config):
        super().__init__(symbol, config)
        self._fired = False

    def get_name(self) -> str:
        return "stub_regime_test"

    def on_bar(self, bars: pd.DataFrame):
        if self._fired:
            return None
        self._fired = True
        close = float(bars["close"].iloc[-1])
        return self._create_signal(
            side=OrderSide.BUY,
            strength=1.0,
            regime=MarketRegime.RANGE,
            entry_price=close,
            stop_loss=close - 5.0,
            take_profit=close + 10.0,
        )


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
def bars():
    """60 5-minute bars — comfortably clears both engines' history floors
    (BacktestEngine's `min_history` and the ensemble per-strategy 30-bar
    view floor in `EnsembleBacktestEngine._step`)."""
    n = 60
    idx = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    close = np.linspace(2000.0, 2010.0, n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1.0},
        index=idx,
    )


def _minimal_risk_config():
    return {
        "risk": {
            "risk_per_trade_pct": 0.01,
            "max_daily_loss_pct": 0.05,
            "max_drawdown_pct": 0.20,
            "max_positions": 3,
            "trailing_stop": {"enabled": False},
        },
    }


def test_backtest_engine_trade_row_includes_regime(xauusd, bars):
    strategy = _RangeOnceStrategy(symbol=xauusd, config={"enabled": True})
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=Decimal("10000"),
        risk_config=_minimal_risk_config(),
        slippage_model="strict",
    )
    result = engine.run(bars, min_history=10, max_window=500)

    # Assert a trade actually happened first, so the regime check below
    # cannot pass vacuously against an empty list.
    assert result.trades
    assert result.trades[0]["regime"] == MarketRegime.RANGE.value


def test_ensemble_engine_trade_row_includes_regime(xauusd, bars, monkeypatch):
    # Inject the stub into the live strategy registry for the duration of
    # this test only — monkeypatch restores the dict afterward. This drives
    # the real StrategyManager -> StrategyAllowlist -> RiskEngine -> broker
    # pipeline instead of reaching into engine internals.
    monkeypatch.setitem(
        StrategyManager.STRATEGY_REGISTRY, "stub_regime_test", _RangeOnceStrategy
    )
    # StrategyAllowlist is a hard default-deny with no config-driven bypass
    # (by design — see its module docstring). The allowlist policy itself is
    # irrelevant to this test, so widen it for the duration of the test only,
    # the same way STRATEGY_REGISTRY is widened above.
    monkeypatch.setattr(
        "src.strategies.strategy_allowlist.SOLO_ALLOWED",
        frozenset({"stub_regime_test"}),
    )

    cfg = _minimal_risk_config()
    cfg["strategies"] = {
        "stub_regime_test": {"enabled": True, "timeframe": "5m"},
    }
    cfg["symbols"] = {
        "XAUUSD": {
            "enabled": True,
            "pip_value": 0.01,
            "min_lot": 0.01,
            "max_lot": 100,
            "lot_step": 0.01,
            "value_per_lot": 100,
            "commission_per_lot": 0,
        }
    }

    engine = EnsembleBacktestEngine(
        symbol=xauusd,
        full_config=cfg,
        initial_capital=Decimal("10000"),
        slippage_model="strict",
    )
    result = engine.run(bars, min_history=10, max_window=500)

    assert result.aggregate.trades
    assert result.aggregate.trades[0]["regime"] == MarketRegime.RANGE.value
