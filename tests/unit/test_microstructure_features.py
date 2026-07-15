"""Unit tests for src/microstructure/features.py — synthetic ticks only."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import features as ft


def make_ticks(mids, start="2026-07-01 09:00", freq="1s", vol=1.0):
    """Synthetic tick frame in load_ticks() shape (UTC ts index, mid/spread)."""
    idx = pd.date_range(start, periods=len(mids), freq=freq, tz="UTC")
    mid = pd.Series(list(mids), index=idx, dtype=float)
    df = pd.DataFrame({
        "bid": mid - 0.05, "ask": mid + 0.05,
        "bid_vol": float(vol), "ask_vol": float(vol),
    })
    df["mid"] = mid
    df["spread"] = df["ask"] - df["bid"]
    return df


class TestCoreTransforms:
    def test_sign_ticks_tick_rule(self):
        df = make_ticks([100.0, 100.1, 100.1, 100.05])
        # first tick has no prior -> 0; unchanged inherits previous sign
        assert ft.sign_ticks(df).tolist() == [0.0, 1.0, 1.0, -1.0]

    def test_cumulative_delta(self):
        df = make_ticks([100.0, 100.1, 100.1, 100.05], vol=1.0)
        # flow = sign * (bid_vol + ask_vol) = sign * 2
        assert ft.cumulative_delta(df).tolist() == [0.0, 2.0, 4.0, 2.0]

    def test_resample_bars_ohlc(self):
        df = make_ticks([100.0, 100.2, 99.9, 100.1], freq="20s")
        bars = ft.resample_bars(df, "1min")
        assert len(bars) == 2
        b0 = bars.iloc[0]
        assert (b0.open, b0.high, b0.low, b0.close, b0.ticks) == (100.0, 100.2, 99.9, 99.9, 3)

    def test_bar_delta_sums_flow_per_bar(self):
        df = make_ticks([100.0, 100.1, 100.2, 100.1], freq="20s")
        d = ft.bar_delta(df, "1min")
        # bar1 ticks: signs 0,+1,+1 -> delta +4; bar2: -1 -> delta -2
        assert d["delta"].tolist() == [4.0, -2.0]
        assert d["cum_delta"].tolist() == [4.0, 2.0]


class TestLoadTicks:
    def test_load_ticks_concats_days_and_derives_mid_spread(self, tmp_path):
        from datetime import date
        root = tmp_path / "XAUUSD"
        root.mkdir()
        for d, px in [("2026-07-01", 3300.0), ("2026-07-02", 3310.0)]:
            pd.DataFrame({
                "ts": pd.date_range(f"{d} 09:00", periods=3, freq="1s", tz="UTC"),
                "bid": px, "ask": px + 0.2, "bid_vol": 1.0, "ask_vol": 1.0,
            }).to_parquet(root / f"{d}.parquet", index=False)
        df = ft.load_ticks("XAUUSD", date(2026, 7, 1), date(2026, 7, 2), ticks_dir=tmp_path)
        assert len(df) == 6
        assert df.index.is_monotonic_increasing
        assert df["mid"].iloc[0] == pytest.approx(3300.1)
        assert df["spread"].iloc[0] == pytest.approx(0.2)

    def test_load_ticks_missing_raises(self, tmp_path):
        from datetime import date
        with pytest.raises(FileNotFoundError):
            ft.load_ticks("XAUUSD", date(2026, 1, 1), date(2026, 1, 2), ticks_dir=tmp_path)


class TestVolumeAtPrice:
    def test_histogram_buckets_price_and_time(self):
        # 09:00 block trades at ~3300.0; 09:20 block at ~3305.0
        a = make_ticks([3300.0] * 10, start="2026-07-01 09:00")
        b = make_ticks([3305.0] * 10, start="2026-07-01 09:20")
        df = pd.concat([a, b])
        vap = ft.volume_at_price(df, price_bin=0.5, time_bin="15min")
        assert 3300.0 in vap.index and 3305.0 in vap.index
        t0, t1 = pd.Timestamp("2026-07-01 09:00", tz="UTC"), pd.Timestamp("2026-07-01 09:15", tz="UTC")
        assert vap.loc[3300.0, t0] == pytest.approx(20.0)   # 10 ticks * (1+1) vol
        assert vap.loc[3305.0, t1] == pytest.approx(20.0)
        assert vap.loc[3305.0, t0] == pytest.approx(0.0)

    def test_profile_nodes_hvn_lvn(self):
        heavy = make_ticks([3300.0] * 50, start="2026-07-01 09:00")
        light = make_ticks([3302.0] * 2, start="2026-07-01 09:05")
        mid_ = make_ticks([3304.0] * 10, start="2026-07-01 09:10")
        vap = ft.volume_at_price(pd.concat([heavy, light, mid_]), price_bin=0.5, time_bin="15min")
        nodes = ft.profile_nodes(vap, hvn_pctile=80.0, lvn_pctile=40.0)
        assert 3300.0 in nodes["hvn"]
        assert 3302.0 in nodes["lvn"]
        assert 3304.0 not in nodes["hvn"] and 3304.0 not in nodes["lvn"]


class TestDetectorsI:
    def test_delta_divergence_bearish(self):
        # price grinds to new highs while delta bleeds -> bearish divergence
        idx = pd.date_range("2026-07-01 09:00", periods=30, freq="5min", tz="UTC")
        bars = pd.DataFrame({"open": 0.0, "high": 0.0, "low": 0.0,
                             "close": np.linspace(3300, 3329, 30), "ticks": 10}, index=idx)
        delta_bars = pd.DataFrame({"delta": -1.0,
                                   "cum_delta": np.linspace(-1, -30, 30)}, index=idx)
        events = ft.delta_divergence(bars, delta_bars, lookback=5)
        assert events and all(e.kind == "bearish_divergence" for e in events)
        assert all(e.strength > 0 for e in events)

    def test_delta_divergence_none_when_confirmed(self):
        # price and delta rise together -> no divergence either way
        idx = pd.date_range("2026-07-01 09:00", periods=30, freq="5min", tz="UTC")
        bars = pd.DataFrame({"open": 0.0, "high": 0.0, "low": 0.0,
                             "close": np.linspace(3300, 3329, 30), "ticks": 10}, index=idx)
        delta_bars = pd.DataFrame({"delta": 1.0,
                                   "cum_delta": np.linspace(1, 30, 30)}, index=idx)
        assert ft.delta_divergence(bars, delta_bars, lookback=5) == []

    def test_absorption_flags_one_sided_flow_in_tight_band(self):
        quiet = make_ticks([3300.0] * 10, start="2026-07-01 09:00")          # flow 0
        # 3 sawtooth cycles: 9 downticks of 0.01 then one +0.09 -> net flow -48, range 0.09
        saw = []
        px = 3300.0
        for _ in range(3):
            for _ in range(9):
                px -= 0.01
                saw.append(px)
            px += 0.09
            saw.append(px)
        absorb = make_ticks(saw, start="2026-07-01 09:02")
        trend = make_ticks(list(np.arange(3300.0, 3301.5, 0.05)),
                           start="2026-07-01 09:04")                          # wide range
        events = ft.absorption_zones(pd.concat([quiet, absorb, trend]),
                                     bucket="2min", band_pts=0.3, flow_pctile=50.0)
        assert len(events) == 1
        e = events[0]
        assert e.kind == "absorption_of_selling"
        assert e.ts == pd.Timestamp("2026-07-01 09:02", tz="UTC")
