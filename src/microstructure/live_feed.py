"""
Live tick feed: passive 1 Hz tap of the EA status file + Dukascopy stitching.

READ-ONLY against the MT5 bridge: this polls mt5_status.json by mtime (the
volatility_monitor pattern) and never touches the command/response channel,
so it is safe alongside the live bot. Tap samples carry no liquidity info,
so LIVE frames are count-weighted: bid_vol = ask_vol = 0.5 on EVERY tick of
the stitched frame (Dukascopy segment included) — one tick = weight 1.0,
which keeps detector percentiles consistent across the stitch boundary.
"""
from __future__ import annotations

import csv
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

LIVE_DIR = PROJECT_ROOT / "data" / "ticks_live"
SPILL_COLUMNS = ["ts", "bid", "ask"]
STITCHED_COLUMNS = ["bid", "ask", "bid_vol", "ask_vol", "mid", "spread"]


# ---------------------------------------------------------------- spill CSV

def spill_path(symbol: str, day: date, live_dir: Path | None = None) -> Path:
    return (live_dir or LIVE_DIR) / symbol / f"{day.isoformat()}.csv"


def append_spill(rows: list[tuple[str, float, float]], path: Path) -> None:
    """Append (iso_ts, bid, ask) rows; header written only on file creation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SPILL_COLUMNS)
        w.writerows(rows)


def load_spill(symbol: str, day: date, live_dir: Path | None = None) -> pd.DataFrame:
    p = spill_path(symbol, day, live_dir)
    if not p.exists():
        return pd.DataFrame(columns=SPILL_COLUMNS)
    df = pd.read_csv(p)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


# ------------------------------------------------------------ symbol match

def match_quote_key(symbol: str, quote_keys) -> str | None:
    """Config symbol (XAUUSD) -> broker quote key (XAUUSDs), shortest prefix."""
    keys = list(quote_keys)
    if symbol in keys:
        return symbol
    candidates = [k for k in keys if k.startswith(symbol)]
    return min(candidates, key=len) if candidates else None


# --------------------------------------------------------------- stitcher

def stitch_day(duka: pd.DataFrame, tap: pd.DataFrame) -> pd.DataFrame:
    """Merge published Dukascopy ticks with tap rows strictly after them.

    Output matches the load_ticks() shape so every detector runs unchanged.
    Weights are forced to 0.5/0.5 everywhere (count-weighted LIVE frame).
    """
    parts = []
    if not duka.empty:
        parts.append(duka[["ts", "bid", "ask"]])
        boundary = duka["ts"].max()
        if not tap.empty:
            parts.append(tap.loc[tap["ts"] > boundary, ["ts", "bid", "ask"]])
    elif not tap.empty:
        parts.append(tap[["ts", "bid", "ask"]])
    if not parts:
        empty = pd.DataFrame(columns=STITCHED_COLUMNS)
        empty.index = pd.DatetimeIndex([], tz="UTC", name="ts")
        return empty
    df = pd.concat(parts, ignore_index=True).sort_values("ts").set_index("ts")
    df["bid_vol"] = 0.5
    df["ask_vol"] = 0.5
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread"] = df["ask"] - df["bid"]
    return df
