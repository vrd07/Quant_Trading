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

import numpy as np
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


def completed_before(vol: pd.Series, break_ts: pd.Timestamp) -> pd.Series:
    """Hours whose full bar closed by break_ts (index label = hour start)."""
    return vol[(vol.index + pd.Timedelta("1h")) <= break_ts]


def break_rvol(vol: pd.Series, break_ts: pd.Timestamp,
               baseline_hours: int = 6) -> float:
    c = completed_before(vol, break_ts)
    if len(c) < baseline_hours + 1:
        return float("nan")
    last = float(c.iloc[-1])
    base = float(c.iloc[-(baseline_hours + 1):-1].mean())
    if base <= 0:
        return float("nan")
    return last / base


def coil_rvol(vol: pd.Series, break_ts: pd.Timestamp,
              coil_hours: int = 2, baseline_hours: int = 12) -> float:
    c = completed_before(vol, break_ts)
    if len(c) < coil_hours + baseline_hours:
        return float("nan")
    coil = float(c.iloc[-coil_hours:].mean())
    base = float(c.iloc[-(coil_hours + baseline_hours):-coil_hours].mean())
    if base <= 0:
        return float("nan")
    return coil / base
