from datetime import date
from pathlib import Path

import pandas as pd

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
