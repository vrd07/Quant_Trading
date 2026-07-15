"""
Pure order-flow feature functions: ticks in, arrays/events out.

Every quantity here is a PROXY computed from Dukascopy quote ticks (bid/ask +
indicative liquidity). Tick-rule delta is not true traded delta; the
volume-at-price heatmap is quoted-activity-at-price, not resting depth —
spot gold has no consolidated order book.

Contract: all functions take a tick DataFrame indexed by UTC ts with columns
bid, ask, bid_vol, ask_vol, mid, spread (the load_ticks() shape). Every
threshold is a kwarg (the viewer exposes them as sliders). No I/O except
load_ticks(); no ML; no state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TICKS_DIR = PROJECT_ROOT / "data" / "ticks"


@dataclass(frozen=True)
class FlowEvent:
    """One detected order-flow event = one mark on the chart."""
    ts: pd.Timestamp
    price: float
    strength: float
    kind: str


# ---------------------------------------------------------------- loading

def load_ticks(symbol: str, start: date, end: date,
               ticks_dir: Path | None = None) -> pd.DataFrame:
    """Read per-day tick Parquets into one UTC-indexed frame with mid/spread."""
    root = (ticks_dir or TICKS_DIR) / symbol
    frames = []
    day = start
    while day <= end:
        p = root / f"{day.isoformat()}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
        day += timedelta(days=1)
    if not frames:
        raise FileNotFoundError(f"no tick files for {symbol} {start}..{end} under {root}")
    df = pd.concat(frames, ignore_index=True).sort_values("ts").set_index("ts")
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    return df


# ------------------------------------------------------- core transforms

def sign_ticks(df: pd.DataFrame) -> pd.Series:
    """Tick rule: mid uptick = +1 (buyer-initiated proxy), downtick = -1,
    unchanged inherits the previous sign; first tick = 0."""
    diff = df["mid"].diff()
    sign = pd.Series(np.sign(diff), index=df.index)
    return sign.replace(0.0, np.nan).ffill().fillna(0.0)


def signed_flow(df: pd.DataFrame) -> pd.Series:
    """Tick sign weighted by indicative liquidity (bid_vol + ask_vol)."""
    return sign_ticks(df) * (df["bid_vol"] + df["ask_vol"])


def cumulative_delta(df: pd.DataFrame) -> pd.Series:
    return signed_flow(df).cumsum()


def resample_bars(df: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Mid-price OHLC bars + tick count per bar."""
    bars = df["mid"].resample(freq).ohlc()
    bars["ticks"] = df["mid"].resample(freq).count()
    return bars.dropna(subset=["open"])


def bar_delta(df: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """Per-bar signed-flow sum and its running total."""
    delta = signed_flow(df).resample(freq).sum()
    delta = delta[resample_bars(df, freq).index.intersection(delta.index)]
    return pd.DataFrame({"delta": delta, "cum_delta": delta.cumsum()})


# ------------------------------------------------------ heatmap / profile

def volume_at_price(df: pd.DataFrame, price_bin: float = 0.5,
                    time_bin: str = "15min") -> pd.DataFrame:
    """2-D activity histogram (price x time): the heatmap layer.
    Values are quoted-liquidity-weighted tick activity — a proxy, not depth."""
    tmp = pd.DataFrame({
        "activity": df["bid_vol"] + df["ask_vol"],
        "pbin": (df["mid"] / price_bin).round() * price_bin,
    })
    vap = (tmp.groupby([pd.Grouper(freq=time_bin), "pbin"])["activity"]
              .sum().unstack(0).fillna(0.0))
    return vap.sort_index()


def profile_nodes(vap: pd.DataFrame, hvn_pctile: float = 85.0,
                  lvn_pctile: float = 15.0) -> dict[str, list[float]]:
    """Collapse the heatmap to a profile; return high/low-volume node prices."""
    profile = vap.sum(axis=1)
    active = profile[profile > 0]
    if active.empty:
        return {"hvn": [], "lvn": []}
    hi = np.percentile(active, hvn_pctile)
    lo = np.percentile(active, lvn_pctile)
    return {"hvn": [float(p) for p in active.index[active >= hi]],
            "lvn": [float(p) for p in active.index[active <= lo]]}
