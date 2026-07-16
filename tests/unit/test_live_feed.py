"""Unit tests for src/microstructure/live_feed.py — no network, no MT5."""
from datetime import date, datetime, timezone

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


class TestStatusTap:
    def test_sample_records_prefix_matched_quote(self, tmp_path):
        quotes = {"XAUUSDs": {"bid": 3300.0, "ask": 3300.2}}
        tap = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        now = pd.Timestamp("2026-07-16 09:00:00", tz="UTC")
        assert tap.sample(now=now) is True
        df = tap.rows_df()
        assert len(df) == 1 and df["bid"].iloc[0] == pytest.approx(3300.0)
        assert tap.staleness_s() < 5.0

    def test_sample_skips_none_missing_and_bad_quotes(self, tmp_path):
        tap = lf.StatusTap("XAUUSD", read_status=lambda: None, live_dir=tmp_path)
        assert tap.sample() is False
        tap2 = lf.StatusTap("XAUUSD", read_status=lambda: {"EURUSD": {"bid": 1, "ask": 1.1}},
                            live_dir=tmp_path)
        assert tap2.sample() is False
        tap3 = lf.StatusTap("XAUUSD", read_status=lambda: {"XAUUSDs": {"bid": 0, "ask": 0}},
                            live_dir=tmp_path)
        assert tap3.sample() is False
        assert tap3.rows_df().empty

    def test_spill_and_preload_roundtrip(self, tmp_path):
        quotes = {"XAUUSDs": {"bid": 3300.0, "ask": 3300.2}}
        tap = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        now = pd.Timestamp("2026-07-16 09:00:00", tz="UTC")
        tap.sample(now=now)
        tap.spill()
        tap2 = lf.StatusTap("XAUUSD", read_status=lambda: quotes, live_dir=tmp_path)
        n = tap2.preload_spill(date(2026, 7, 16))
        assert n == 1 and len(tap2.rows_df()) == 1


class TestDukaBackfill:
    def _fake_fetch(self, calls):
        def fetch(symbol, day, hour, point):
            calls.append(hour)
            if hour == 0:
                return pd.DataFrame({
                    "ts": pd.date_range(f"{day} 00:00", periods=3, freq="1s", tz="UTC"),
                    "bid": 3300.0, "ask": 3300.2, "bid_vol": 1.0, "ask_vol": 1.0,
                })
            return None  # not published yet
        return fetch

    def test_refresh_fetches_only_completed_hours_and_caches(self, tmp_path):
        calls: list[int] = []
        bf = lf.DukaBackfill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path,
                             fetch_hour_fn=self._fake_fetch(calls))
        now = datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc)
        df = bf.refresh(now)
        assert sorted(set(calls)) == [0, 1]          # hour 2 not complete yet
        assert len(df) == 3 and bf.published_hours() == [0]
        assert (tmp_path / "XAUUSD" / "duka_2026-07-16_00.parquet").exists()

    def test_refresh_throttles_retries_but_rereads_cache(self, tmp_path):
        calls: list[int] = []
        bf = lf.DukaBackfill("XAUUSD", date(2026, 7, 16), live_dir=tmp_path,
                             retry_min=10.0, fetch_hour_fn=self._fake_fetch(calls))
        now = datetime(2026, 7, 16, 2, 30, tzinfo=timezone.utc)
        bf.refresh(now)
        n_first = len(calls)
        df = bf.refresh(now)                          # immediate second refresh
        assert len(calls) == n_first                  # no new network attempts
        assert len(df) == 3                           # cached hour still served
