"""Unit tests for LondonBreakoutStrategy (USDJPY-only Asia-range breakout)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderSide
from src.core.types import Symbol
from src.strategies.london_breakout_strategy import LondonBreakoutStrategy


def make_symbol(ticker: str = "USDJPY") -> Symbol:
    return Symbol(
        ticker=ticker,
        pip_value=Decimal("0.001"),
        min_lot=Decimal("0.01"),
        max_lot=Decimal("0.50"),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("659"),
    )


def make_day_bars(last_hour: int, last_minute: int, breakout: str = "none",
                  base: float = 150.0, range_pips: float = 40.0) -> pd.DataFrame:
    """Build one day of 5m bars from 00:00 UTC to last_hour:last_minute.

    Asia bars (00:00-06:55) oscillate inside [base, base+range]; the final
    bar closes above/below/inside the range per `breakout`.
    """
    rng = range_pips * 0.01
    start = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 10, last_hour, last_minute, tzinfo=timezone.utc)
    idx = pd.date_range(start, end, freq="5min")
    n = len(idx)
    rs = np.random.RandomState(7)
    close = base + rng * rs.rand(n)
    high = close + 0.005
    low = close - 0.005
    df = pd.DataFrame({"open": close, "high": high, "low": low,
                       "close": close, "volume": 100.0}, index=idx)
    # pin the asia extremes so the range is exactly [base, base+rng]
    df.iloc[10, df.columns.get_loc("high")] = base + rng
    df.iloc[20, df.columns.get_loc("low")] = base
    if breakout == "up":
        df.iloc[-1, df.columns.get_loc("close")] = base + rng + 0.05
    elif breakout == "down":
        df.iloc[-1, df.columns.get_loc("close")] = base - 0.05
    return df


def make_strategy(**overrides) -> LondonBreakoutStrategy:
    cfg = {"enabled": True, "min_asia_bars": 36}
    cfg.update(overrides)
    return LondonBreakoutStrategy(make_symbol(), cfg)


class TestLondonBreakout:
    def test_buy_on_upside_break(self):
        strat = make_strategy()
        sig = strat.on_bar(make_day_bars(8, 0, "up"))
        assert sig is not None
        assert sig.side == OrderSide.BUY
        assert sig.take_profit is None                    # time-stop exit only
        assert float(sig.stop_loss) < float(sig.entry_price)
        # stop = entry - 0.5 x (actual asia range)
        expected = 0.5 * sig.metadata["asia_range"]
        assert abs((float(sig.entry_price) - float(sig.stop_loss)) - expected) < 1e-9
        assert sig.metadata["stop_price"] == float(sig.stop_loss)

    def test_sell_on_downside_break(self):
        sig = make_strategy().on_bar(make_day_bars(8, 0, "down"))
        assert sig is not None
        assert sig.side == OrderSide.SELL
        assert float(sig.stop_loss) > float(sig.entry_price)

    def test_no_signal_inside_range(self):
        assert make_strategy().on_bar(make_day_bars(8, 0, "none")) is None

    def test_no_signal_outside_entry_window(self):
        # 11:00 UTC is past entry_end_hour (10) — no chase entries
        assert make_strategy().on_bar(make_day_bars(11, 0, "up")) is None
        # During Asia itself (05:00) the range is still forming
        assert make_strategy().on_bar(make_day_bars(5, 0, "up")) is None

    def test_one_trade_per_day_latch(self):
        strat = make_strategy()
        bars = make_day_bars(8, 0, "up")
        assert strat.on_bar(bars) is not None
        later = make_day_bars(9, 0, "up")
        assert strat.on_bar(later) is None               # same day: latched

    def test_symbol_gate_blocks_non_usdjpy(self):
        strat = LondonBreakoutStrategy(make_symbol("XAUUSD"),
                                       {"enabled": True, "min_asia_bars": 36})
        assert strat.on_bar(make_day_bars(8, 0, "up")) is None

    def test_symbol_gate_allows_broker_suffix(self):
        strat = LondonBreakoutStrategy(make_symbol("USDJPYs"),
                                       {"enabled": True, "min_asia_bars": 36})
        assert strat.on_bar(make_day_bars(8, 0, "up")) is not None

    def test_min_asia_range_filter(self):
        strat = make_strategy(min_asia_range=0.60)       # 60 pips > the 40-pip range
        assert strat.on_bar(make_day_bars(8, 0, "up")) is None

    def test_insufficient_asia_bars(self):
        # Day starting at 05:00 — only ~24 asia bars < min 36
        bars = make_day_bars(8, 0, "up").iloc[60:]
        assert make_strategy().on_bar(bars) is None

    def test_disabled_strategy_silent(self):
        strat = make_strategy(enabled=False)
        assert strat.on_bar(make_day_bars(8, 0, "up")) is None


class TestRiskProcessorIntegration:
    def test_risk_processor_honors_structural_stop_no_tp(self):
        from src.risk.risk_processor import RiskProcessor
        strat = make_strategy()
        sig = strat.on_bar(make_day_bars(8, 0, "up"))
        assert sig is not None
        rp = RiskProcessor({"strategies": {}, "risk": {}})
        out = rp.calculate_stops(sig)
        assert out.take_profit is None
        assert float(out.stop_loss) == pytest.approx(sig.metadata["stop_price"])

    def test_kalman_tp_is_marked_structural(self):
        """kalman's ATR TP (the backtested edge) must opt out of the dollar-TP
        rewrite so a runtime take_profit_usd can't stretch it back to ~8×ATR."""
        from src.risk.risk_processor import RiskProcessor
        from src.core.types import Signal

        sig = Signal(
            strategy_name="kalman_regime",
            symbol=make_symbol("XAUUSD"),
            side=OrderSide.SELL,
            entry_price=Decimal("4200"),
            metadata={"atr": 6.0},
        )
        cfg = {
            "strategies": {"kalman_regime": {
                "sl_atr_multiplier": 3.0, "tp_atr_multiplier": 4.0}},
            "risk": {"kalman_min_tp_rr": 1.0},
        }
        out = RiskProcessor(cfg).calculate_stops(sig)
        # 4×ATR TP below entry on a SELL → ~RR 1.33, not a dollar target.
        assert out.metadata.get("preserve_structural_tp") is True
        assert float(out.take_profit) == pytest.approx(4200 - 4.0 * 6.0)
