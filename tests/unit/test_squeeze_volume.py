from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.microstructure import squeeze_volume as sv


def _fake_downloader(start, end):
    idx = pd.date_range("2026-05-08 00:00", periods=6, freq="1h", tz="UTC")
    return pd.DataFrame({"Volume": [100, 200, 300, 400, 500, 600]}, index=idx)


def test_load_gc_hourly_returns_utc_volume_series(tmp_path):
    s = sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                          cache_dir=tmp_path, downloader=_fake_downloader)
    assert isinstance(s, pd.Series)
    assert s.name == "volume"
    assert str(s.index.tz) == "UTC"
    assert list(s.values) == [100, 200, 300, 400, 500, 600]


def test_load_gc_hourly_caches_and_reuses(tmp_path):
    sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                      cache_dir=tmp_path, downloader=_fake_downloader)
    cache = tmp_path / "GC_1h_2026-05-08_2026-05-08.parquet"
    assert cache.exists()

    def _boom(start, end):
        raise AssertionError("downloader must not be called when cache exists")

    s = sv.load_gc_hourly(date(2026, 5, 8), date(2026, 5, 8),
                          cache_dir=tmp_path, downloader=_boom)
    assert list(s.values) == [100, 200, 300, 400, 500, 600]


def _hourly(vals, start="2026-05-08 00:00"):
    idx = pd.date_range(start, periods=len(vals), freq="1h", tz="UTC")
    return pd.Series(vals, index=idx, name="volume")


def test_break_rvol_surge_above_one():
    # baseline hours ~100, last completed hour spikes to 300 → rvol ~3
    vol = _hourly([100, 100, 100, 100, 100, 100, 300])
    # break at 07:20 → last completed hour is 06:00 (spike); 00:00-05:00 baseline
    ts = pd.Timestamp("2026-05-08 07:20", tz="UTC")
    r = sv.break_rvol(vol, ts, baseline_hours=6)
    assert r == pytest.approx(3.0, rel=1e-6)


def test_break_rvol_causal_guard_ignores_break_hour():
    # A massive spike sits in the break's OWN hour (07:00). It must be excluded.
    vol = _hourly([100, 100, 100, 100, 100, 100, 100, 99999])
    ts = pd.Timestamp("2026-05-08 07:20", tz="UTC")  # 07:00 hour still open
    r = sv.break_rvol(vol, ts, baseline_hours=6)
    # last completed hour is 06:00 (value 100) over 00:00-05:00 baseline (100) → 1.0
    assert r == pytest.approx(1.0, rel=1e-6)
    # sanity: the 99999 hour would have blown this up if it leaked
    assert r < 2.0


def test_coil_rvol_dryup_below_one():
    # baseline hours 200, coil hours drop to 50 → rvol 0.25
    vals = [200] * 12 + [50, 50]
    vol = _hourly(vals)
    ts = pd.Timestamp("2026-05-08 14:20", tz="UTC")  # 14:00 open; 13:00 last complete
    r = sv.coil_rvol(vol, ts, coil_hours=2, baseline_hours=12)
    assert r == pytest.approx(0.25, rel=1e-6)


def test_rvol_nan_on_insufficient_history():
    vol = _hourly([100, 100])
    ts = pd.Timestamp("2026-05-08 05:20", tz="UTC")
    assert np.isnan(sv.break_rvol(vol, ts, baseline_hours=6))
    assert np.isnan(sv.coil_rvol(vol, ts, coil_hours=2, baseline_hours=12))
