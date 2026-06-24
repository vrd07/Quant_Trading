"""Unit tests for IndexOvernightStrategy (US30/NAS100 Tuesday night drift)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.index_overnight_strategy import (
    IndexOvernightStrategy,
    resample_daily,
    daily_atr,
)


def make_symbol(ticker: str = "NAS100") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("1.0"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("10.0"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("1"),
    )


def make_bars(end: datetime, days: int = 40, trend: str = "up",
              base: float = 18000.0) -> pd.DataFrame:
    """15m bars covering `days` calendar days ending at `end` (inclusive).
    index_overnight has NO regime gate, so trend should not change firing —
    both directions are tested to prove that."""
    end_ts = pd.Timestamp(end)
    idx = pd.date_range(end=end_ts, periods=days * 96, freq="15min")
    n = len(idx)
    slope = 5.0 if trend == "up" else -5.0
    drift = base + slope * np.arange(n) / 96.0
    rs = np.random.RandomState(7)
    close = drift + 8.0 * rs.randn(n)
    return pd.DataFrame({
        "open": close, "high": close + 6.0, "low": close - 6.0,
        "close": close, "volume": 100.0,
    }, index=idx)


def make_strategy(**overrides) -> IndexOvernightStrategy:
    ticker = overrides.pop("_ticker", "NAS100")
    cfg = {"enabled": True}
    cfg.update(overrides)
    return IndexOvernightStrategy(make_symbol(ticker), cfg)


TUE_1945 = datetime(2026, 6, 9, 19, 45, tzinfo=timezone.utc)    # a Tuesday
TUE_1955 = datetime(2026, 6, 9, 19, 55, tzinfo=timezone.utc)


class TestIndexOvernight:
    def test_buy_on_tuesday_window(self):
        sig = make_strategy().on_bar(make_bars(TUE_1945))
        assert sig is not None
        assert sig.side == OrderSide.BUY                  # long-only by design
        assert sig.take_profit is None                    # time-stop exit only
        assert float(sig.stop_loss) < float(sig.entry_price)
        # stop = entry - 1.5 x dailyATR(14)
        expected = 1.5 * sig.metadata["daily_atr"]
        assert abs((float(sig.entry_price) - float(sig.stop_loss)) - expected) < 1e-6
        assert sig.metadata["stop_price"] == float(sig.stop_loss)

    def test_fires_regardless_of_trend(self):
        # NO regime gate (unlike monday_drift) — must fire in a downtrend too.
        assert make_strategy().on_bar(make_bars(TUE_1945, trend="down")) is not None

    def test_no_signal_outside_tuesday(self):
        wed = datetime(2026, 6, 10, 19, 45, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(wed)) is None
        mon = datetime(2026, 6, 8, 19, 45, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(mon)) is None

    def test_no_signal_before_entry_window(self):
        tue_1930 = datetime(2026, 6, 9, 19, 30, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(tue_1930)) is None
        tue_1500 = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)
        assert make_strategy().on_bar(make_bars(tue_1500)) is None

    def test_one_trade_per_week_latch(self):
        strat = make_strategy()
        assert strat.on_bar(make_bars(TUE_1945)) is not None
        assert strat.on_bar(make_bars(TUE_1955)) is None
        next_tue = datetime(2026, 6, 16, 19, 45, tzinfo=timezone.utc)
        assert strat.on_bar(make_bars(next_tue)) is not None

    def test_symbol_gate_blocks_unvalidated_symbols(self):
        for ticker in ("XAUUSD", "EURUSD", "GER40", "USDJPY"):
            assert make_strategy(_ticker=ticker).on_bar(make_bars(TUE_1945)) is None

    def test_symbol_gate_accepts_broker_suffix(self):
        assert make_strategy(_ticker="US30.cash").on_bar(make_bars(TUE_1945)) is not None
        assert make_strategy(_ticker="NAS100s").on_bar(make_bars(TUE_1945)) is not None

    def test_insufficient_daily_history_skips(self):
        # 10 days < ATR(14)+1 requirement — stand aside, do not crash.
        assert make_strategy().on_bar(make_bars(TUE_1945, days=10)) is None

    def test_disabled_strategy_silent(self):
        assert make_strategy(enabled=False).on_bar(make_bars(TUE_1945)) is None


class TestPureHelpers:
    def test_resample_daily_left_label(self):
        daily = resample_daily(make_bars(TUE_1945, days=5))
        assert daily.index[-1].hour == 0
        assert len(daily) >= 4

    def test_daily_atr_short_history_none(self):
        assert daily_atr(resample_daily(make_bars(TUE_1945, days=8)), 14) is None

    def test_daily_atr_positive(self):
        atr = daily_atr(resample_daily(make_bars(TUE_1945, days=40)), 14)
        assert atr is not None and atr > 0
