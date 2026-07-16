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


class TestDefendedLevels:
    def _absorb(self, price, ts_min, kind="absorption_of_selling"):
        return FlowEvent(pd.Timestamp(f"2026-07-16 09:{ts_min:02d}", tz="UTC"),
                         price, 10.0, kind)

    def test_clusters_by_band_and_counts_touches(self):
        events = [self._absorb(3300.1, 0), self._absorb(3300.4, 10),   # same 1pt band
                  self._absorb(3305.0, 20, "absorption_of_buying"),
                  FlowEvent(pd.Timestamp("2026-07-16 09:30", tz="UTC"),
                            3300.0, 5.0, "imbalance_buy")]              # ignored
        levels = lm.defended_levels(events, band_pts=1.0)
        assert len(levels) == 2
        top = levels[0]
        assert top.touches == 2 and top.side == "buyers"
        assert top.price == pytest.approx((3300.1 + 3300.4) / 2)
        assert top.last_ts == pd.Timestamp("2026-07-16 09:10", tz="UTC")
        assert levels[1].side == "sellers" and levels[1].touches == 1

    def test_empty_without_absorption(self):
        assert lm.defended_levels([]) == []


class TestLiquidityPools:
    def _bars(self, closes, highs=None, lows=None):
        idx = pd.date_range("2026-07-16 09:00", periods=len(closes), freq="5min", tz="UTC")
        c = pd.Series(list(closes), index=idx, dtype=float)
        return pd.DataFrame({"open": c, "high": highs if highs is not None else c + 0.1,
                             "low": lows if lows is not None else c - 0.1,
                             "close": c, "ticks": 10}, index=idx)

    def test_unswept_swing_high_is_buy_side_pool(self):
        # peak at bar 7 (3310), never exceeded later; price ends at 3300
        closes = [3300, 3301, 3302, 3304, 3306, 3308, 3309, 3310,
                  3308, 3306, 3304, 3302, 3301, 3300, 3300, 3300]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3,
                                   round_step=0.0)          # round layer off
        kinds = {(p.kind, p.side) for p in pools}
        assert ("swing_high", "buy_side") in kinds

    def test_swept_swing_dropped(self):
        # first peak 3310 at bar 5 later exceeded by 3312 -> only the later
        # (unswept) high survives
        closes = [3300, 3304, 3308, 3310, 3308, 3304, 3300, 3304,
                  3308, 3312, 3308, 3304, 3300, 3300, 3300, 3300]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=2, round_step=0.0)
        highs = [p.price for p in pools if p.kind == "swing_high"]
        assert 3310.1 not in highs                    # swept peak absent
        assert any(abs(h - 3312.1) < 1e-9 for h in highs)

    def test_round_numbers_both_sides(self):
        closes = [3302.0] * 16
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3, round_step=5.0)
        rounds = [(p.price, p.side) for p in pools if p.kind == "round"]
        assert (3305.0, "buy_side") in rounds
        assert (3300.0, "sell_side") in rounds

    def test_equal_lows_cluster(self):
        # two un-swept swing lows 0.2 pts apart (each strictly above the
        # other's low never being breached later) -> one equal_lows pool
        closes = [3305, 3303, 3300.1, 3303, 3305, 3303, 3300.3, 3303,
                  3305, 3305, 3305]
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=2,
                                   eq_tol_pts=0.5, round_step=0.0)
        eq = [p for p in pools if p.kind == "equal_lows"]
        assert len(eq) == 1
        assert eq[0].side == "sell_side"
        assert eq[0].price == pytest.approx((3300.0 + 3300.2) / 2)

    def test_max_levels_cap(self):
        closes = [3302.0] * 16
        pools = lm.liquidity_pools(self._bars(closes), swing_bars=3,
                                   round_step=1.0, max_levels=3)
        assert len(pools) <= 3
