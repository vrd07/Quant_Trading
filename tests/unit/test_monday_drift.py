"""Unit tests for MondayDriftStrategy (GBPUSD/AUDUSD Monday anti-USD drift)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.monday_drift_strategy import (
    MondayDriftStrategy,
    resample_daily,
    uptrend_gate_and_atr,
)


def make_symbol(ticker: str = "GBPUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.00001"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100000"),
    )


def make_bars(end: datetime, days: int = 80, trend: str = "up",
              base: float = 1.30) -> pd.DataFrame:
    """15m bars covering `days` calendar days ending at `end` (inclusive).

    trend='up' ramps closes so the last close sits above SMA(50);
    trend='down' ramps them downward so the gate rejects.
    """
    end_ts = pd.Timestamp(end)
    idx = pd.date_range(end=end_ts, periods=days * 96, freq="15min")
    n = len(idx)
    slope = 0.0008 if trend == "up" else -0.0008
    drift = base + slope * np.arange(n) / 96.0
    rs = np.random.RandomState(11)
    close = drift + 0.0005 * rs.randn(n)
    df = pd.DataFrame({
        "open": close, "high": close + 0.0004, "low": close - 0.0004,
        "close": close, "volume": 100.0,
    }, index=idx)
    return df


def make_strategy(**overrides) -> MondayDriftStrategy:
    cfg = {"enabled": True}
    cfg.update(overrides)
    ticker = overrides.pop("_ticker", "GBPUSD")
    return MondayDriftStrategy(make_symbol(ticker), cfg)


MONDAY_0000 = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)    # a Monday
MONDAY_0045 = datetime(2026, 6, 8, 0, 45, tzinfo=timezone.utc)


class TestMondayDrift:
    def test_buy_on_monday_uptrend(self):
        strat = make_strategy()
        sig = strat.on_bar(make_bars(MONDAY_0000, trend="up"))
        assert sig is not None
        assert sig.side == OrderSide.BUY                  # long-only by design
        assert sig.take_profit is None                    # time-stop exit only
        assert float(sig.stop_loss) < float(sig.entry_price)
        # stop = entry - 1.0 x dailyATR(14)
        expected = 1.0 * sig.metadata["daily_atr"]
        assert abs((float(sig.entry_price) - float(sig.stop_loss)) - expected) < 1e-9
        assert sig.metadata["stop_price"] == float(sig.stop_loss)

    def test_gate_rejects_downtrend(self):
        assert make_strategy().on_bar(make_bars(MONDAY_0000, trend="down")) is None

    def test_no_signal_outside_monday(self):
        tuesday = datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(tuesday, trend="up")) is None

    def test_no_signal_outside_entry_hour(self):
        monday_0700 = datetime(2026, 6, 8, 7, 0, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(monday_0700, trend="up")) is None

    def test_one_trade_per_week_latch(self):
        strat = make_strategy()
        assert strat.on_bar(make_bars(MONDAY_0000, trend="up")) is not None
        assert strat.on_bar(make_bars(MONDAY_0045, trend="up")) is None
        # next Monday fires again
        next_monday = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
        assert strat.on_bar(make_bars(next_monday, trend="up")) is not None

    def test_symbol_gate_blocks_unvalidated_pairs(self):
        for ticker in ("XAUUSD", "EURUSD", "USDJPY"):
            strat = make_strategy(_ticker=ticker)
            assert strat.on_bar(make_bars(MONDAY_0000, trend="up")) is None

    def test_symbol_gate_accepts_broker_suffix(self):
        sig = make_strategy(_ticker="GBPUSDs").on_bar(make_bars(MONDAY_0000, trend="up"))
        assert sig is not None
        sig = make_strategy(_ticker="AUDUSDs").on_bar(make_bars(MONDAY_0000, trend="up"))
        assert sig is not None

    def test_insufficient_daily_history_skips(self):
        # 20 days < SMA(50) requirement — must stand aside, not crash
        assert make_strategy().on_bar(make_bars(MONDAY_0000, days=20)) is None

    def test_disabled_strategy_silent(self):
        assert make_strategy(enabled=False).on_bar(make_bars(MONDAY_0000)) is None


class TestPureHelpers:
    def test_resample_daily_left_label(self):
        bars = make_bars(MONDAY_0000, days=5)
        daily = resample_daily(bars)
        assert daily.index[-1].hour == 0
        assert len(daily) >= 4

    def test_gate_and_atr_short_history_none(self):
        daily = resample_daily(make_bars(MONDAY_0000, days=10))
        assert uptrend_gate_and_atr(daily, 50, 14) is None

    def test_gate_direction(self):
        up = resample_daily(make_bars(MONDAY_0000, days=80, trend="up"))
        dn = resample_daily(make_bars(MONDAY_0000, days=80, trend="down"))
        assert uptrend_gate_and_atr(up, 50, 14)[0] is True
        assert uptrend_gate_and_atr(dn, 50, 14)[0] is False
        assert uptrend_gate_and_atr(up, 50, 14)[1] > 0
