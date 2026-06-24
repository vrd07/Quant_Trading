#!/usr/bin/env python3
"""
Index calendar/session-effect research — the gold-UNCORRELATED edge hunt.

Mission: find a low-frequency, wide-stop, time-exit calendar edge on equity
indices (US30/NAS100/GER40) that clears ~1.3 PF, survives an IS/OOS split,
and is robust to entry-delay (the bid-spread-artifact guard that killed the
GBPUSD/EURUSD weekend/rollover "edges").

Stage 1 here = the cheap diagnostic: decompose each index's daily return into
the OVERNIGHT leg (US cash close -> next cash open) vs the INTRADAY leg
(cash open -> cash close). The documented "night effect" says equity indices
earn ~all their drift overnight and ~zero intraday. If it's present and OOS-
stable, that's the candidate to turn into a tradeable hold.

Sessions (UTC): US cash open 13:30, close 20:00. (DEUIDXEUR uses EU session
07:00 open / 15:30 close — DAX cash.)
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DATA = PROJECT_ROOT / "data" / "historical"

# label -> (csv stem, cash_open UTC "HH:MM", cash_close UTC "HH:MM")
INDICES = {
    "US30":   ("USA30IDXUSD",   "13:30", "20:00"),
    "NAS100": ("USATECHIDXUSD", "13:30", "20:00"),
    "GER40":  ("DEUIDXEUR",     "07:00", "15:30"),
}

OOS_FRAC = 0.30  # last 30% of calendar days = out-of-sample


def load(stem: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{stem}_5m_real.csv", parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def session_prices(df: pd.DataFrame, open_t: str, close_t: str) -> pd.DataFrame:
    """For each trading day, grab the bar at/after cash-open and the last bar
    at/before cash-close. Returns per-day open_px / close_px."""
    oh, om = map(int, open_t.split(":"))
    ch, cm = map(int, close_t.split(":"))
    open_min = oh * 60 + om
    close_min = ch * 60 + cm
    mins = df.index.hour * 60 + df.index.minute
    day = df.index.normalize()

    rows = []
    for d, g in df.groupby(day):
        gm = mins[df.index.normalize() == d]
        # first bar at/after open
        oi = g[(gm >= open_min) & (gm <= open_min + 30)]
        ci = g[(gm <= close_min) & (gm >= close_min - 30)]
        if len(oi) == 0 or len(ci) == 0:
            continue
        rows.append((d, oi.iloc[0]["open"], ci.iloc[-1]["close"]))
    s = pd.DataFrame(rows, columns=["day", "open_px", "close_px"]).set_index("day")
    return s


def tstat(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    if len(x) < 2 or x.std(ddof=1) == 0:
        return 0.0
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def decompose(label, stem, open_t, close_t):
    df = load(stem)
    s = session_prices(df, open_t, close_t)
    if len(s) < 50:
        print(f"{label}: too few sessions ({len(s)})")
        return
    s["intraday"] = s["close_px"] / s["open_px"] - 1.0          # open -> close
    s["overnight"] = s["open_px"] / s["close_px"].shift(1) - 1.0  # prev close -> open
    s = s.dropna()

    n = len(s)
    cut = int(n * (1 - OOS_FRAC))
    IS, OOS = s.iloc[:cut], s.iloc[cut:]

    print(f"\n===== {label}  ({stem})  sessions={n}  "
          f"IS {s.index[0].date()}..{s.index[cut-1].date()}  "
          f"OOS {s.index[cut].date()}..{s.index[-1].date()} =====")
    hdr = f"{'leg':10s} {'slice':4s} {'mean_bps':>9s} {'t':>6s} {'ann%':>7s} {'win%':>6s} {'sharpe':>7s}"
    print(hdr)
    for leg in ("overnight", "intraday"):
        for name, sl in (("ALL", s), ("IS", IS), ("OOS", OOS)):
            r = sl[leg].values
            mean_bps = np.nanmean(r) * 1e4
            ann = np.nanmean(r) * 252 * 100
            win = np.nanmean(r > 0) * 100
            sharpe = (np.nanmean(r) / np.nanstd(r) * np.sqrt(252)) if np.nanstd(r) else 0
            print(f"{leg:10s} {name:4s} {mean_bps:9.2f} {tstat(r):6.2f} {ann:7.1f} {win:6.1f} {sharpe:7.2f}")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for label, (stem, ot, ct) in INDICES.items():
        if only and label != only:
            continue
        try:
            decompose(label, stem, ot, ct)
        except FileNotFoundError:
            print(f"{label}: no data file ({stem})")
