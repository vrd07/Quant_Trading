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


def label_native(mids: pd.Series, side: str, entry: float, stop: float,
                 target: float, cost_pts: float = 0.5, rr: float = 2.0
                 ) -> dict | None:
    """Fixed-geometry triple-barrier over an ordered mid path. Stop wins ties."""
    if len(mids) == 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    cost_R = (2.0 * cost_pts) / risk
    for px in mids.values:
        px = float(px)
        if side == "BUY":
            if px <= stop:
                return {"R": -1.0 - cost_R, "outcome": "stop"}
            if px >= target:
                return {"R": rr - cost_R, "outcome": "target"}
        else:
            if px >= stop:
                return {"R": -1.0 - cost_R, "outcome": "stop"}
            if px <= target:
                return {"R": rr - cost_R, "outcome": "target"}
    last = float(mids.iloc[-1])
    move = (last - entry) if side == "BUY" else (entry - last)
    return {"R": move / risk - cost_R, "outcome": "timeout"}


def _bucket(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win": 0.0, "mean_R": 0.0}
    rs = [t["R"] for t in trades]
    return {"n": n,
            "win": sum(1 for r in rs if r > 0) / n,
            "mean_R": sum(rs) / n}


def split_stats(trades: list[dict], feature: str) -> dict:
    vals = [t for t in trades if not np.isnan(t.get(feature, float("nan")))]
    if not vals:
        return {"median": float("nan"), "high": _bucket([]), "low": _bucket([])}
    median = float(np.median([t[feature] for t in vals]))
    high = [t for t in vals if t[feature] >= median]
    low = [t for t in vals if t[feature] < median]
    return {"median": median, "high": _bucket(high), "low": _bucket(low)}


def verdict(trades: list[dict], feature: str = "break_rvol",
            margin: float = 0.15, min_n: int = 3) -> str:
    s = split_stats(trades, feature)
    hi, lo = s["high"], s["low"]
    if hi["n"] >= min_n and lo["n"] >= min_n \
            and hi["mean_R"] - lo["mean_R"] >= margin:
        return "GREEN"
    return "RED"
