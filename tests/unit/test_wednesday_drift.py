"""Unit tests for WednesdayDriftStrategy (AUDJPY mid-week drift, enters Tuesday)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.wednesday_drift_strategy import WednesdayDriftStrategy


def make_symbol(ticker: str = "AUDJPY") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.001"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("658"),
    )


def make_bars(end: datetime, days: int = 40, trend: str = "up",
              base: float = 112.0) -> pd.DataFrame:
    end_ts = pd.Timestamp(end)
    idx = pd.date_range(end=end_ts, periods=days * 96, freq="15min")
    n = len(idx)
    slope = 0.03 if trend == "up" else -0.03
    drift = base + slope * np.arange(n) / 96.0
    rs = np.random.RandomState(5)
    close = drift + 0.05 * rs.randn(n)
    return pd.DataFrame({
        "open": close, "high": close + 0.04, "low": close - 0.04,
        "close": close, "volume": 100.0,
    }, index=idx)


def make_strategy(**ov) -> WednesdayDriftStrategy:
    ticker = ov.pop("_ticker", "AUDJPY")
    cfg = {"enabled": True}
    cfg.update(ov)
    return WednesdayDriftStrategy(make_symbol(ticker), cfg)


TUE_1945 = datetime(2026, 6, 9, 19, 45, tzinfo=timezone.utc)   # a Tuesday
TUE_1955 = datetime(2026, 6, 9, 19, 55, tzinfo=timezone.utc)


class TestWednesdayDrift:
    def test_buy_on_tuesday_window(self):
        sig = make_strategy().on_bar(make_bars(TUE_1945))
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.take_profit is None
        assert float(sig.stop_loss) < float(sig.entry_price)
        expected = 1.5 * sig.metadata["daily_atr"]
        assert abs((float(sig.entry_price) - float(sig.stop_loss)) - expected) < 1e-6
        assert sig.metadata["stop_price"] == float(sig.stop_loss)

    def test_fires_regardless_of_trend(self):
        assert make_strategy().on_bar(make_bars(TUE_1945, trend="down")) is not None

    def test_no_signal_outside_tuesday(self):
        wed = datetime(2026, 6, 10, 19, 45, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(wed)) is None
        mon = datetime(2026, 6, 8, 19, 45, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(mon)) is None

    def test_no_signal_before_window(self):
        tue_1930 = datetime(2026, 6, 9, 19, 30, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(tue_1930)) is None

    def test_one_trade_per_week_latch(self):
        strat = make_strategy()
        assert strat.on_bar(make_bars(TUE_1945)) is not None
        assert strat.on_bar(make_bars(TUE_1955)) is None
        next_tue = datetime(2026, 6, 16, 19, 45, tzinfo=timezone.utc)
        assert strat.on_bar(make_bars(next_tue)) is not None

    def test_symbol_gate_blocks_others(self):
        for ticker in ("XAUUSD", "EURJPY", "AUDUSD", "US30"):
            assert make_strategy(_ticker=ticker).on_bar(make_bars(TUE_1945)) is None

    def test_symbol_gate_accepts_broker_suffix(self):
        assert make_strategy(_ticker="AUDJPYs").on_bar(make_bars(TUE_1945)) is not None

    def test_insufficient_history_skips(self):
        assert make_strategy().on_bar(make_bars(TUE_1945, days=10)) is None

    def test_disabled_silent(self):
        assert make_strategy(enabled=False).on_bar(make_bars(TUE_1945)) is None
