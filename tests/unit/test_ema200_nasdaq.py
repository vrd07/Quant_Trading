"""Unit tests for EMA200NasdaqStrategy (13:40 UTC anchor break, one entry/day)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.ema200_nasdaq_strategy import EMA200NasdaqStrategy


def make_symbol(ticker: str = "NAS100") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("1.0"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("10.0"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("1"),
    )


def make_strategy(ticker: str = "NAS100", **overrides) -> EMA200NasdaqStrategy:
    cfg = {"enabled": True}
    cfg.update(overrides)
    return EMA200NasdaqStrategy(make_symbol(ticker), cfg)


def make_bars(end_utc: datetime, n: int = 700, slope: float = 0.05,
              base: float = 20000.0) -> pd.DataFrame:
    """n 5m bars ENDING at end_utc, linear trend of `slope`/bar."""
    idx = pd.date_range(end=end_utc, periods=n, freq="5min")
    closes = base + slope * np.arange(n)
    return pd.DataFrame({
        "open": closes - slope, "high": closes + 2.0, "low": closes - 2.0,
        "close": closes, "volume": 10.0,
    }, index=idx)


def anchor_ts(hour=13, minute=45):
    # a bar shortly AFTER the 13:40 anchor on a fixed weekday
    return datetime(2026, 6, 3, hour, minute, tzinfo=timezone.utc)


class TestSymbolGate:
    def test_rejects_other_symbols(self):
        strat = make_strategy("XAUUSD")
        bars = make_bars(anchor_ts())
        assert strat.on_bar(bars) is None

    def test_broker_ticker_via_config(self):
        strat = make_strategy("USTEC", allowed_symbols=["USTEC"])
        bars = make_bars(anchor_ts())
        sig = strat.on_bar(bars)
        assert sig is not None   # renamed ticker honored via config gate


class TestEntryLogic:
    def test_buy_fires_on_first_break_above_anchor(self):
        strat = make_strategy()
        bars = make_bars(anchor_ts())   # rising → anchor above EMA, next bar breaks
        sig = strat.on_bar(bars)
        assert sig is not None
        assert sig.side == OrderSide.BUY
        entry = float(sig.entry_price)
        sl = float(sig.stop_loss)
        tp = float(sig.take_profit)
        # SL = the ANCHOR candle's low, not the current bar's
        anchor_bar = bars[(bars.index.hour == 13) & (bars.index.minute == 40)]
        assert sl == pytest.approx(float(anchor_bar["low"].iloc[-1]))
        assert tp - entry == pytest.approx(2.0 * (entry - sl), rel=1e-6)
        assert sig.metadata["preserve_structural_sl"] is True

    def test_sell_fires_below_ema(self):
        strat = make_strategy()
        bars = make_bars(anchor_ts(), slope=-0.05)   # falling → anchor below EMA
        sig = strat.on_bar(bars)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        entry = float(sig.entry_price)
        sl = float(sig.stop_loss)
        assert sl > entry
        assert entry - float(sig.take_profit) == pytest.approx(
            2.0 * (sl - entry), rel=1e-6)

    def test_one_entry_per_day(self):
        strat = make_strategy()
        # last bar 13:55 — bars 13:45 and 13:50 already closed above the anchor
        bars = make_bars(anchor_ts(minute=55))
        assert strat.on_bar(bars) is None

    def test_no_signal_before_break(self):
        strat = make_strategy()
        bars = make_bars(anchor_ts())
        # flatten every close after the anchor to BELOW the anchor close
        idx = bars.index
        anchor_pos = int(np.where((idx.hour == 13) & (idx.minute == 40))[0][-1])
        anchor_close = bars["close"].iloc[anchor_pos]
        bars.iloc[anchor_pos + 1:, bars.columns.get_loc("close")] = \
            anchor_close - 1.0
        assert strat.on_bar(bars) is None

    def test_outside_window_after_1540(self):
        strat = make_strategy()
        bars = make_bars(datetime(2026, 6, 3, 15, 45, tzinfo=timezone.utc))
        assert strat.on_bar(bars) is None

    def test_before_anchor(self):
        strat = make_strategy()
        bars = make_bars(datetime(2026, 6, 3, 13, 35, tzinfo=timezone.utc))
        assert strat.on_bar(bars) is None

    def test_insufficient_warmup(self):
        strat = make_strategy()
        bars = make_bars(anchor_ts(), n=100)
        assert strat.on_bar(bars) is None

    def test_disabled(self):
        strat = make_strategy(enabled=False)
        bars = make_bars(anchor_ts())
        assert strat.on_bar(bars) is None
