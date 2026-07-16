"""Unit tests for src/microstructure/live_feed.py — no network, no MT5."""
from datetime import date

import pandas as pd
import pytest

from src.microstructure import live_feed as lf


def _frame(ts_list, bid=3300.0, ask=3300.2):
    return pd.DataFrame({
        "ts": pd.to_datetime(ts_list, utc=True),
        "bid": bid, "ask": ask,
    })


class TestSpill:
    def test_append_and_load_roundtrip(self, tmp_path):
        p = lf.spill_path("XAUUSD", date(2026, 7, 16), live_dir=tmp_path)
        assert p == tmp_path / "XAUUSD" / "2026-07-16.csv"
        lf.append_spill([("2026-07-16T09:00:00+00:00", 3300.0, 3300.2)], p)
        lf.append_spill([("2026-07-16T09:00:01+00:00", 3300.1, 3300.3)], p)
        df = lf.load_spill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path)
        assert len(df) == 2
        assert df["ts"].dt.tz is not None
        assert df.loc[1, "bid"] == pytest.approx(3300.1)

    def test_load_missing_returns_empty(self, tmp_path):
        df = lf.load_spill("XAUUSD", date(2026, 1, 1), live_dir=tmp_path)
        assert df.empty and list(df.columns) == ["ts", "bid", "ask"]


class TestMatchQuoteKey:
    def test_exact_prefix_and_missing(self):
        assert lf.match_quote_key("XAUUSD", ["XAUUSD"]) == "XAUUSD"
        assert lf.match_quote_key("XAUUSD", ["XAUUSDs", "XAUUSDx"]) == "XAUUSDs"
        assert lf.match_quote_key("XAUUSD", ["EURUSD"]) is None


class TestStitchDay:
    def test_tap_rows_before_boundary_dropped_and_weights_normalized(self):
        duka = _frame(["2026-07-16 08:59:58", "2026-07-16 08:59:59"])
        duka["bid_vol"] = 2.5   # Dukascopy liquidity must be overwritten
        duka["ask_vol"] = 1.5
        tap = _frame(["2026-07-16 08:59:59", "2026-07-16 09:00:01"],
                     bid=3301.0, ask=3301.2)
        df = lf.stitch_day(duka, tap)
        assert len(df) == 3            # tap row at 08:59:59 (== boundary) dropped
        assert df.index.is_monotonic_increasing
        assert (df["bid_vol"] == 0.5).all() and (df["ask_vol"] == 0.5).all()
        assert df["mid"].iloc[-1] == pytest.approx(3301.1)
        assert df["spread"].iloc[-1] == pytest.approx(0.2)

    def test_empty_duka_uses_all_tap(self):
        tap = _frame(["2026-07-16 09:00:00", "2026-07-16 09:00:01"])
        df = lf.stitch_day(pd.DataFrame(columns=["ts", "bid", "ask"]), tap)
        assert len(df) == 2 and df["mid"].iloc[0] == pytest.approx(3300.1)

    def test_both_empty(self):
        df = lf.stitch_day(pd.DataFrame(columns=["ts", "bid", "ask"]),
                           pd.DataFrame(columns=["ts", "bid", "ask"]))
        assert df.empty
        assert list(df.columns) == ["bid", "ask", "bid_vol", "ask_vol", "mid", "spread"]
