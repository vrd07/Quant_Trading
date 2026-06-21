"""Unit tests for SqueezeBreakoutStrategy (XAUUSD-only volatility-coil breakout)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide, MarketRegime
from src.core.types import Symbol
from src.strategies.squeeze_breakout_strategy import SqueezeBreakoutStrategy


def make_symbol(ticker: str = "XAUUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


def make_strategy(**overrides) -> SqueezeBreakoutStrategy:
    cfg = {"enabled": True}
    cfg.update(overrides)
    return SqueezeBreakoutStrategy(make_symbol(), cfg)


def make_coil_then_break(direction: str = "up", n_warm: int = 180,
                         base: float = 2000.0) -> pd.DataFrame:
    """Build 15m bars: a wide-ATR warmup, a tight COIL, then a breakout bar.

    The warmup gives the 100-bar ATR percentile something to sit above so the
    coil's low ATR registers as a squeeze; the coil is flat+tight; the last bar
    expands ATR and closes beyond the prior Donchian(20) channel.
    """
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    n = n_warm + 30 + 1
    idx = pd.date_range(start, periods=n, freq="15min")
    rs = np.random.RandomState(3)

    close = np.empty(n)
    high = np.empty(n)
    low = np.empty(n)
    # Wide, choppy warmup (high ATR) oscillating around base.
    for i in range(n_warm):
        c = base + 8.0 * np.sin(i / 5.0) + rs.uniform(-3, 3)
        close[i] = c
        high[i] = c + 4.0
        low[i] = c - 4.0
    # Tight, flat COIL (low ATR, flat Kalman) — last 30 bars before the break.
    coil_level = close[n_warm - 1]
    for i in range(n_warm, n - 1):
        c = coil_level + rs.uniform(-0.3, 0.3)
        close[i] = c
        high[i] = c + 0.3
        low[i] = c - 0.3
    # Breakout bar: ATR expands and close clears the coil's Donchian extreme.
    if direction == "up":
        c = coil_level + 12.0
        close[-1] = c
        high[-1] = c + 6.0
        low[-1] = coil_level
    else:
        c = coil_level - 12.0
        close[-1] = c
        high[-1] = coil_level
        low[-1] = c - 6.0
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": 100.0}, index=idx)


class TestSqueezeBreakout:
    def test_name(self):
        assert make_strategy().get_name() == "squeeze_breakout"

    def test_symbol_gate_blocks_non_gold(self):
        strat = SqueezeBreakoutStrategy(make_symbol("EURUSD"), {"enabled": True})
        bars = make_coil_then_break("up")
        assert strat.on_bar(bars) is None

    def test_suffixed_gold_passes_gate(self):
        # broker's suffixed ticker must still pass the prefix gate
        strat = SqueezeBreakoutStrategy(make_symbol("XAUUSDs"), {"enabled": True})
        bars = make_coil_then_break("up")
        sig = strat.on_bar(bars)
        assert sig is not None and sig.side == OrderSide.BUY

    def test_disabled_returns_none(self):
        strat = make_strategy(enabled=False)
        assert strat.on_bar(make_coil_then_break("up")) is None

    def test_insufficient_bars(self):
        strat = make_strategy()
        short = make_coil_then_break("up").iloc[:50]
        assert strat.on_bar(short) is None

    def test_upside_breakout_emits_buy_with_rr2(self):
        strat = make_strategy()
        bars = make_coil_then_break("up")
        sig = strat.on_bar(bars)
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.regime == MarketRegime.TREND
        entry = float(sig.entry_price)
        sl = float(sig.stop_loss)
        tp = float(sig.take_profit)
        assert sl < entry < tp
        # TP distance == RR * SL distance (RR2.0)
        risk = entry - sl
        reward = tp - entry
        assert reward == pytest.approx(2.0 * risk, rel=1e-6)

    def test_downside_breakout_emits_sell(self):
        strat = make_strategy()
        bars = make_coil_then_break("down")
        sig = strat.on_bar(bars)
        assert sig is not None
        assert sig.side == OrderSide.SELL
        entry = float(sig.entry_price)
        assert float(sig.stop_loss) > entry > float(sig.take_profit)

    def test_no_coil_no_signal(self):
        """A pure choppy (no coil) series should not fire a breakout."""
        strat = make_strategy()
        bars = make_coil_then_break("up")
        # overwrite the coil section with wide bars → no squeeze precondition
        n = len(bars)
        rs = np.random.RandomState(9)
        for i in range(n - 31, n - 1):
            c = 2000.0 + 8.0 * np.sin(i) + rs.uniform(-3, 3)
            bars.iloc[i, bars.columns.get_loc("close")] = c
            bars.iloc[i, bars.columns.get_loc("high")] = c + 4.0
            bars.iloc[i, bars.columns.get_loc("low")] = c - 4.0
        assert strat.on_bar(bars) is None

    def test_cooldown_blocks_immediate_re_signal(self):
        strat = make_strategy(cooldown_bars=8, timeframe_minutes=15)
        bars = make_coil_then_break("up")
        first = strat.on_bar(bars)
        assert first is not None
        # same frame again → within cooldown window → suppressed
        assert strat.on_bar(bars) is None
