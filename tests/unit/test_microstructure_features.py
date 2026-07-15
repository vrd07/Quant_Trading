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
