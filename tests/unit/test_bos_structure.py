"""Unit tests for BOSStructureStrategy (XAUUSD-only CHOCH→BOS×2→pullback)."""

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.bos_structure_strategy import BOSStructureStrategy


def make_symbol(ticker: str = "XAUUSD") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.01"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("100"),
    )


def make_strategy(**overrides) -> BOSStructureStrategy:
    cfg = {"enabled": True, "pivot_bars": 2, "min_stop_pts": 0.5,
           "cooldown_bars": 0}
    cfg.update(overrides)
    return BOSStructureStrategy(make_symbol(overrides.pop("ticker", "XAUUSD"))
                                if "ticker" in overrides else make_symbol(), cfg)


def zigzag_bars(waypoints, bars_per_leg: int = 6) -> pd.DataFrame:
    """Piecewise-linear 15m closes through waypoints; hi/lo hug the close."""
    closes = []
    for a, b in zip(waypoints[:-1], waypoints[1:]):
        closes.extend(np.linspace(a, b, bars_per_leg, endpoint=False))
    closes.append(float(waypoints[-1]))
    closes = np.array(closes)
    start = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    idx = pd.date_range(start, periods=len(closes), freq="15min")
    return pd.DataFrame({
        "open": closes, "high": closes + 0.3, "low": closes - 0.3,
        "close": closes, "volume": 100.0,
    }, index=idx)


# Downtrend (CHOCH down → BOS → trend −1), then reversal: CHOCH up →
# BOS#1 (break 96 swing) → BOS#2 (break 104 swing) → higher-low pullback (103).
REVERSAL_WAYPOINTS = [100, 90, 95, 82, 88, 74, 96, 90, 104, 97, 112, 103, 118]


class TestSymbolGate:
    def test_rejects_non_gold(self):
        strat = BOSStructureStrategy(make_symbol("US30"),
                                     {"enabled": True, "pivot_bars": 2})
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        assert strat.on_bar(bars) is None

    def test_accepts_broker_suffix(self):
        strat = BOSStructureStrategy(make_symbol("XAUUSDs"),
                                     {"enabled": True, "pivot_bars": 2,
                                      "min_stop_pts": 0.5, "cooldown_bars": 0})
        assert strat.symbol.ticker.upper().startswith(
            strat.allowed_symbol_prefixes)


class TestStructureMachine:
    def test_pivots_found(self):
        strat = make_strategy()
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        piv = strat._find_pivots(bars)
        kinds = [k for _, k, _ in piv]
        assert "H" in kinds and "L" in kinds
        # confirm bars are pivot extreme + N and within range
        assert all(0 <= cb < len(bars) for cb, _, _ in piv)

    def test_reversal_sequence_emits_buy(self):
        strat = make_strategy()
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        from src.data.indicators import Indicators
        sigs = strat._walk_structure(bars, Indicators.atr(bars, period=14))
        buys = [s for s in sigs if s["side"] == OrderSide.BUY]
        assert buys, "expected a BUY after CHOCH→BOS#1→BOS#2→higher-low pullback"
        # armed only after the second BOS
        assert all(s["bos_count"] >= 2 for s in buys)

    def test_mirror_sequence_emits_sell(self):
        strat = make_strategy()
        # mirror of the reversal path around 100 → identical geometry, short side
        mirrored = [200 - w for w in REVERSAL_WAYPOINTS]
        bars = zigzag_bars(mirrored)
        from src.data.indicators import Indicators
        sigs = strat._walk_structure(bars, Indicators.atr(bars, period=14))
        sells = [s for s in sigs if s["side"] == OrderSide.SELL]
        assert sells, "expected a SELL on the mirrored sequence"


class TestOnBar:
    def test_signal_fires_on_pullback_confirm_bar(self):
        strat = make_strategy()
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        from src.data.indicators import Indicators
        sigs = strat._walk_structure(bars, Indicators.atr(bars, period=14))
        buys = [s for s in sigs if s["side"] == OrderSide.BUY]
        assert buys
        cut = buys[0]["bar_idx"] + 1
        fresh = make_strategy()
        signal = fresh.on_bar(bars.iloc[:cut])
        assert signal is not None
        assert signal.side == OrderSide.BUY
        entry = float(signal.entry_price)
        sl = float(signal.stop_loss)
        tp = float(signal.take_profit)
        assert sl < entry < tp
        # TP = rr × stop distance
        assert tp - entry == pytest.approx(2.0 * (entry - sl), rel=1e-6)
        assert signal.metadata["preserve_structural_sl"] is True
        assert signal.metadata["stop_price"] == pytest.approx(sl)

    def test_no_signal_without_pullback_confirm(self):
        strat = make_strategy()
        # plain monotone series: no CHOCH sequence, nothing should fire
        bars = zigzag_bars([100, 110, 120, 130])
        assert strat.on_bar(bars) is None

    def test_insufficient_bars(self):
        strat = make_strategy()
        bars = zigzag_bars(REVERSAL_WAYPOINTS).iloc[:10]
        assert strat.on_bar(bars) is None

    def test_disabled(self):
        strat = make_strategy(enabled=False)
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        assert strat.on_bar(bars) is None

    def test_cooldown_latch_blocks_refire(self):
        strat = make_strategy(cooldown_bars=100)
        bars = zigzag_bars(REVERSAL_WAYPOINTS)
        from src.data.indicators import Indicators
        sigs = strat._walk_structure(bars, Indicators.atr(bars, period=14))
        buys = [s for s in sigs if s["side"] == OrderSide.BUY]
        cut = buys[0]["bar_idx"] + 1
        first = strat.on_bar(bars.iloc[:cut])
        assert first is not None
        # same window again (e.g. duplicate on_bar for the same bar) → latched
        strat._last_signal_ts = strat._bar_timestamp(bars.iloc[:cut]) \
            - pd.Timedelta(minutes=15)
        assert strat.on_bar(bars.iloc[:cut]) is None
