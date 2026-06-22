"""Unit tests for StochPullbackStrategy (XAUUSD-only stochastic trend pullback)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide, MarketRegime
from src.core.types import Symbol
from src.strategies.stoch_pullback_strategy import StochPullbackStrategy


def make_symbol(ticker: str = "XAUUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


def make_strategy(**overrides) -> StochPullbackStrategy:
    # Default to all-hours so trend/stoch tests aren't coupled to the clock;
    # the session gate has its own dedicated test.
    cfg = {"enabled": True, "session_start_hour": 0, "session_end_hour": 24}
    cfg.update(overrides)
    return StochPullbackStrategy(make_symbol(), cfg)


def make_trend_pullback_break(direction: str = "up", n: int = 90,
                              base: float = 2000.0, hour: int = 9) -> pd.DataFrame:
    """Build 15m bars: a steady trend, a stochastic-cooling pullback, then a
    breakout of the small consolidation in the trend direction."""
    start = datetime(2026, 6, 2, hour, 0, tzinfo=timezone.utc)
    idx = pd.date_range(start, periods=n, freq="15min")
    close = np.empty(n)
    high = np.empty(n)
    low = np.empty(n)
    slope = 1.0
    sgn = 1.0 if direction == "up" else -1.0

    # Steady trend (EMA rises/falls, price on the trend side).
    for i in range(n):
        c = base + sgn * slope * i
        close[i] = c
        high[i] = c + 0.5
        low[i] = c - 0.5

    # Pullback: the 6 bars before the breakout retrace AGAINST the trend,
    # pushing Stochastic %K into the cool-off zone (long) / heat zone (short).
    dip_start = n - 7
    anchor = base + sgn * slope * (dip_start - 1)
    for j, i in enumerate(range(dip_start, n - 1)):
        c = anchor - sgn * (j + 1) * 1.4   # move counter-trend
        close[i] = c
        high[i] = c + 0.4
        low[i] = c - 0.4

    # Breakout bar: resume the trend, clearing the consolidation extreme.
    if direction == "up":
        c = anchor + 4.0
        close[-1] = c
        high[-1] = c + 0.5
        low[-1] = close[n - 2] - 0.4
    else:
        c = anchor - 4.0
        close[-1] = c
        high[-1] = close[n - 2] + 0.4
        low[-1] = c - 0.5
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": 100.0}, index=idx)


class TestStochPullback:
    def test_name(self):
        assert make_strategy().get_name() == "stoch_pullback"

    def test_symbol_gate_blocks_non_gold(self):
        strat = StochPullbackStrategy(make_symbol("EURUSD"),
                                      {"enabled": True, "session_start_hour": 0,
                                       "session_end_hour": 24})
        assert strat.on_bar(make_trend_pullback_break("up")) is None

    def test_suffixed_gold_passes_gate(self):
        strat = StochPullbackStrategy(make_symbol("XAUUSDs"),
                                      {"enabled": True, "session_start_hour": 0,
                                       "session_end_hour": 24})
        sig = strat.on_bar(make_trend_pullback_break("up"))
        assert sig is not None and sig.side == OrderSide.BUY

    def test_disabled_returns_none(self):
        assert make_strategy(enabled=False).on_bar(make_trend_pullback_break("up")) is None

    def test_insufficient_bars(self):
        short = make_trend_pullback_break("up").iloc[:40]
        assert make_strategy().on_bar(short) is None

    def test_uptrend_pullback_emits_buy_with_rr2(self):
        sig = make_strategy().on_bar(make_trend_pullback_break("up"))
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.regime == MarketRegime.TREND
        entry, sl, tp = float(sig.entry_price), float(sig.stop_loss), float(sig.take_profit)
        assert sl < entry < tp
        # TP distance == RR * SL distance (RR2.0), structural stop
        assert (tp - entry) == pytest.approx(2.0 * (entry - sl), rel=1e-6)
        assert sig.metadata.get("preserve_structural_sl") is True
        assert sig.metadata.get("stop_price") == pytest.approx(sl)

    def test_downtrend_pullback_emits_sell(self):
        sig = make_strategy().on_bar(make_trend_pullback_break("down"))
        assert sig is not None
        assert sig.side == OrderSide.SELL
        entry = float(sig.entry_price)
        assert float(sig.stop_loss) > entry > float(sig.take_profit)

    def test_session_gate_blocks_out_of_window(self):
        # Breakout bar lands at 03:00 UTC, outside the default 07-21 window.
        strat = StochPullbackStrategy(make_symbol(),
                                      {"enabled": True})  # default session 7-21
        bars = make_trend_pullback_break("up", hour=2)
        # ensure the last bar is pre-07:00 UTC
        assert bars.index[-1].hour < 7
        assert strat.on_bar(bars) is None

    def test_no_trend_no_signal(self):
        """A flat, choppy series has no established trend → no signal."""
        strat = make_strategy()
        bars = make_trend_pullback_break("up")
        n = len(bars)
        rs = np.random.RandomState(7)
        c = 2000.0 + rs.uniform(-2, 2, n)   # flat chop, EMA ~flat
        bars["close"] = c
        bars["high"] = c + 1.0
        bars["low"] = c - 1.0
        assert strat.on_bar(bars) is None

    def test_cooldown_blocks_immediate_re_signal(self):
        strat = make_strategy(cooldown_bars=5, timeframe_minutes=15)
        bars = make_trend_pullback_break("up")
        assert strat.on_bar(bars) is not None
        assert strat.on_bar(bars) is None   # within cooldown window

    def test_trend_extension_gate_blocks_chop(self):
        """Trend-extension filter: an entry with price too close to the EMA (no
        real trend separation) is rejected. A high min_ema_dist_atr forces the
        strong-trend fixture to fail the gate; disabling it (0) lets it through."""
        bars = make_trend_pullback_break("up")
        assert make_strategy(min_ema_dist_atr=0.0).on_bar(bars) is not None   # raw signal
        assert make_strategy(min_ema_dist_atr=100.0).on_bar(bars) is None     # gate vetoes
