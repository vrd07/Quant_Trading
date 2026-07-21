"""
Squeeze-breakout volume-filter smell-test — pure helpers.

GC-futures relative volume (coil dry-up + break surge) as a confirmation
filter on `squeeze_breakout`. Research-only; decides whether to BUY multi-year
GC data, never whether to trade. See
docs/superpowers/specs/2026-07-21-squeeze-volume-filter-design.md.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GC_CACHE = PROJECT_ROOT / "data" / "gc_futures"


def _yf_download(start: date, end: date) -> pd.DataFrame:
    import yfinance as yf
    # yfinance `end` is exclusive → +1 day to include the last day.
    return yf.Ticker("GC=F").history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
        interval="1h",
    )


def load_gc_hourly(start: date, end: date, cache_dir: Path | None = None,
                   downloader: Callable[[date, date], pd.DataFrame] | None = None
                   ) -> pd.Series:
    """UTC-indexed hourly GC=F volume. Cached; hourly ONLY (daily is broken)."""
    cache_dir = cache_dir or GC_CACHE
    cache = cache_dir / f"GC_1h_{start.isoformat()}_{end.isoformat()}.parquet"
    if cache.exists():
        s = pd.read_parquet(cache)["volume"]
        s.index = pd.to_datetime(s.index, utc=True)
        s.name = "volume"
        return s
    df = (downloader or _yf_download)(start, end)
    if df is None or df.empty or "Volume" not in df.columns:
        raise ValueError(f"no GC hourly volume for {start}..{end}")
    vol = df["Volume"].copy()
    vol.index = pd.to_datetime(vol.index, utc=True)
    vol = vol[vol.index.notna()]
    vol.name = "volume"
    cache_dir.mkdir(parents=True, exist_ok=True)
    vol.to_frame().to_parquet(cache)
    return vol
