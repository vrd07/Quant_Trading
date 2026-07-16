"""Unit tests for src/microstructure/live_marks.py — synthetic frames only."""
import json

import numpy as np
import pandas as pd
import pytest

from src.microstructure import live_marks as lm
from src.microstructure.features import FlowEvent


def make_ticks(mids, start="2026-07-16 09:00", freq="1s", vol=0.5):
    idx = pd.date_range(start, periods=len(mids), freq=freq, tz="UTC")
    mid = pd.Series(list(mids), index=idx, dtype=float)
    df = pd.DataFrame({"bid": mid - 0.05, "ask": mid + 0.05,
                       "bid_vol": float(vol), "ask_vol": float(vol)})
    df["mid"] = mid
    df["spread"] = df["ask"] - df["bid"]
    return df


PARAMS = dict(lookback=20, band_pts=0.5, flow_pctile=90, ratio=3.0,
              burst_pctile=95, spread_pctile=95, price_bin=0.5)


class TestClosedCandleEvents:
    def test_forming_bar_event_hidden_until_close(self):
        # 20 upticks in the 09:00 5m bar -> one imbalance_buy at bar_ts 09:00
        df = make_ticks([3300.00 + 0.01 * i for i in range(20)])
        during = pd.Timestamp("2026-07-16 09:03:00", tz="UTC")   # bar still forming
        after = pd.Timestamp("2026-07-16 09:05:00", tz="UTC")    # bar closed
        assert lm.closed_candle_events(df, "5min", PARAMS, during) == []
        events = lm.closed_candle_events(df, "5min", PARAMS, after)
        assert any(e.kind == "imbalance_buy" for e in events)

    def test_events_sorted_by_time(self):
        a = make_ticks([3300.00 + 0.01 * i for i in range(20)], start="2026-07-16 09:00")
        b = make_ticks([3310.00 - 0.01 * i for i in range(20)], start="2026-07-16 09:05")
        df = pd.concat([a, b])
        now = pd.Timestamp("2026-07-16 09:10:00", tz="UTC")
        events = lm.closed_candle_events(df, "5min", PARAMS, now)
        ts = [e.ts for e in events]
        assert ts == sorted(ts)


class TestSignalFeed:
    def _events(self):
        return [FlowEvent(pd.Timestamp("2026-07-16 09:00", tz="UTC"), 3300.0, 5.0,
                          "imbalance_buy")]

    def test_dedup_and_new_only(self, tmp_path):
        feed = lm.SignalFeed(tmp_path / "sig.jsonl")
        now = pd.Timestamp("2026-07-16 09:05:01", tz="UTC")
        first = feed.ingest(self._events(), now=now)
        assert len(first) == 1 and first[0].kind == "imbalance_buy"
        assert feed.ingest(self._events(), now=now) == []
        assert len(feed.entries) == 1

    def test_jsonl_persistence_and_replay(self, tmp_path):
        p = tmp_path / "sig.jsonl"
        feed = lm.SignalFeed(p)
        feed.ingest(self._events(), now=pd.Timestamp("2026-07-16 09:05:01", tz="UTC"))
        lines = [json.loads(l) for l in p.read_text().splitlines()]
        assert lines[0]["kind"] == "imbalance_buy"
        feed2 = lm.SignalFeed(p)                      # replay
        assert len(feed2.entries) == 1
        assert feed2.ingest(self._events()) == []     # replayed keys deduped

    def test_no_path_in_memory_only(self):
        feed = lm.SignalFeed(None)
        assert len(feed.ingest(self._events())) == 1
