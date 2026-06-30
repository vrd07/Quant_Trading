"""
Tests for the budget-SL + RR-TP policy (2026-06-30, corrected).

The user's start-script "SL" is a max-loss-USD (risk.risk_per_trade_usd). The
execution engine's BudgetSL turns it into the actual SL distance, and the TP is
then set to `reward_risk_ratio` × that SL distance (1:1 / 1:2 / 1:3).

RiskProcessor resolves the reward:risk per signal (per-strategy `rr` overriding
the global `risk.reward_risk_ratio`, default 2.0) and stashes it in
signal.metadata['reward_risk_ratio']; the execution engine applies it to the
budget SL. Strategies that preserve their own SL (squeeze/stoch via
preserve_structural_sl) bypass the rewrite entirely.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.types import Signal, Symbol, Tick
from src.core.constants import OrderSide
from src.risk.risk_processor import RiskProcessor
from src.execution.execution_engine import ExecutionEngine


def make_symbol(ticker="XAUUSD"):
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.2"),
        max_lot=Decimal("0.2"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
        min_stops_distance=Decimal("1.0"),
        max_spread=Decimal("5.0"),
    )


# ── RiskProcessor: reward:risk resolution ──────────────────────────────────

def make_signal(strategy_name, side=OrderSide.BUY, entry=2000.0, strength=0.6, atr=5.0):
    return Signal(
        strategy_name=strategy_name,
        symbol=make_symbol(),
        side=side,
        strength=strength,
        entry_price=Decimal(str(entry)),
        metadata={"strategy": strategy_name, "atr": atr},
    )


def test_global_reward_risk_ratio_used_by_default():
    rp = RiskProcessor({"strategies": {"momentum": {}}, "risk": {"reward_risk_ratio": 3.0}})
    sig = make_signal("momentum_scalp")
    rp.calculate_stops(sig)
    assert sig.metadata["reward_risk_ratio"] == 3.0


def test_per_strategy_rr_overrides_global():
    rp = RiskProcessor({"strategies": {"momentum": {"rr": 1.0}}, "risk": {"reward_risk_ratio": 3.0}})
    sig = make_signal("momentum_scalp")
    rp.calculate_stops(sig)
    assert sig.metadata["reward_risk_ratio"] == 1.0


def test_rr_defaults_to_2_when_unset():
    rp = RiskProcessor({"strategies": {"momentum": {}}, "risk": {}})
    sig = make_signal("momentum_scalp")
    rp.calculate_stops(sig)
    assert sig.metadata["reward_risk_ratio"] == 2.0


# ── Execution engine: budget SL → RR TP end-to-end ─────────────────────────

def make_engine(config):
    connector = MagicMock()
    sym = make_symbol()
    connector.get_current_tick.return_value = Tick(
        symbol=sym, timestamp=datetime.now(timezone.utc),
        bid=Decimal("1999.9"), ask=Decimal("2000.1"),
        last=Decimal("2000.0"), volume=Decimal("1"),
    )
    risk_engine = MagicMock()
    risk_engine.config = config
    risk_engine.calculate_position_size.return_value = Decimal("0.2")  # user's pinned lot
    # Reject before MT5 submission, but the order still carries the computed SL/TP.
    risk_engine.validate_order.return_value = (False, "test-stop-before-submit")
    return ExecutionEngine(connector=connector, risk_engine=risk_engine)


def _submit(engine, strategy_name, side=OrderSide.BUY, entry="2000.0"):
    sig = Signal(
        strategy_name=strategy_name,
        symbol=make_symbol(),
        side=side,
        strength=0.6,
        entry_price=Decimal(entry),
        metadata={"strategy": strategy_name, "atr": 5.0},
    )
    return engine.submit_signal(
        signal=sig,
        account_balance=Decimal("5000"),
        account_equity=Decimal("5000"),
        current_positions={},
        daily_pnl=Decimal("0"),
    )


def test_budget_sl_and_rr2_tp_buy():
    # $150 max loss, lot 0.2, value_per_lot 100 -> SL distance = 150/(0.2*100) = 7.5
    # rr 2.0 -> TP distance = 15.0
    cfg = {"strategies": {"momentum": {}},
           "risk": {"risk_per_trade_usd": 150.0, "take_profit_usd": 0,
                    "reward_risk_ratio": 2.0}}
    order = _submit(make_engine(cfg), "momentum_scalp", side=OrderSide.BUY)
    assert order is not None
    assert order.stop_loss == Decimal("1992.5")     # 2000 - 7.5
    assert order.take_profit == Decimal("2015.0")   # 2000 + 15.0


def test_budget_sl_and_rr2_tp_sell():
    cfg = {"strategies": {"momentum": {}},
           "risk": {"risk_per_trade_usd": 150.0, "take_profit_usd": 0,
                    "reward_risk_ratio": 2.0}}
    order = _submit(make_engine(cfg), "momentum_scalp", side=OrderSide.SELL)
    assert order.stop_loss == Decimal("2007.5")     # 2000 + 7.5
    assert order.take_profit == Decimal("1985.0")   # 2000 - 15.0


@pytest.mark.parametrize("rr,tp", [(1.0, "2007.5"), (2.0, "2015.0"), (3.0, "2022.5")])
def test_rr_ratios_drive_tp(rr, tp):
    cfg = {"strategies": {"momentum": {}},
           "risk": {"risk_per_trade_usd": 150.0, "reward_risk_ratio": rr}}
    order = _submit(make_engine(cfg), "momentum_scalp", side=OrderSide.BUY)
    assert order.stop_loss == Decimal("1992.5")
    assert order.take_profit == Decimal(tp)


def test_squeeze_preserves_own_sl_tp():
    # squeeze sets preserve_structural_sl in its own strategy code; here we
    # simulate that via metadata. BudgetSL must NOT rewrite SL, and the RR-TP
    # must NOT fire — the strategy's own stop_price/take_profit_price stand.
    cfg = {"strategies": {"squeeze_breakout": {}},
           "risk": {"risk_per_trade_usd": 150.0, "reward_risk_ratio": 2.0}}
    engine = make_engine(cfg)
    sig = Signal(
        strategy_name="squeeze_breakout", symbol=make_symbol(), side=OrderSide.BUY,
        strength=0.6, entry_price=Decimal("2000.0"),
        metadata={"strategy": "squeeze_breakout", "atr": 5.0,
                  "stop_price": 1967.0, "take_profit_price": 2066.0,
                  "preserve_structural_sl": True},
    )
    order = engine.submit_signal(signal=sig, account_balance=Decimal("5000"),
                                 account_equity=Decimal("5000"),
                                 current_positions={}, daily_pnl=Decimal("0"))
    assert order.stop_loss == Decimal("1967.0")     # squeeze's own fixed 33pt stop
    assert order.take_profit == Decimal("2066.0")   # squeeze's own RR2 TP, untouched
