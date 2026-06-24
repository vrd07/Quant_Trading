#!/usr/bin/env python3
"""
Oil (WTI + Brent) calendar/session scan — same proven method that found the
index Tuesday-overnight edge, on a genuinely different driver (energy, ~uncorr
to gold/equities/USD-drift). WTI+Brent (~0.9 corr) = built-in cross-instrument check.

Reports, per instrument: overnight (prev session-close→open) vs intraday
decomposition (IS/OOS), and day-of-week of BOTH the overnight leg and the full
close-to-close daily return. A shippable edge must replicate on BOTH and survive
IS/OOS — the bar index_overnight cleared.

NYMEX pit RTH ≈ 14:00–19:30 UTC (9:00–14:30 ET); used for the session split.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.research_index_calendar import load, session_prices, tstat, OOS_FRAC

OILS = {"WTI": "LIGHTCMDUSD", "BRENT": "BRENTCMDUSD"}
OPEN_T, CLOSE_T = "14:00", "19:30"
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def split(s):
    cut = int(len(s) * (1 - OOS_FRAC))
    return s.iloc[:cut], s.iloc[cut:]


def scan(label, stem):
    df = load(stem)
    s = session_prices(df, OPEN_T, CLOSE_T)
    s["intraday"] = s["close_px"] / s["open_px"] - 1.0
    s["overnight"] = s["open_px"] / s["close_px"].shift(1) - 1.0
    s["daily"] = s["close_px"].pct_change()         # close-to-close
    s["dow"] = s.index.weekday
    s = s.dropna()
    IS, OOS = split(s)

    print(f"\n========== {label} ({stem})  sessions={len(s)} ==========")
    print("Overnight vs intraday (mean bps / t / win%):")
    for leg in ("overnight", "intraday"):
        for nm, sl in (("ALL", s), ("IS", IS), ("OOS", OOS)):
            r = sl[leg].values
            print(f"  {leg:9s} {nm:4s} {np.nanmean(r)*1e4:8.2f}bps  t={tstat(r):5.2f}  win={np.nanmean(r>0)*100:5.1f}%")

    for leg in ("overnight", "daily"):
        print(f"\n  DoW of {leg} return:")
        print(f"  {'dow':>4s} {'n':>4s} {'mean_bps':>9s} {'t':>6s} {'win%':>6s}")
        for d in range(5):
            r = s[s["dow"] == d][leg].values
            if len(r) == 0:
                continue
            print(f"  {_WD[d]:>4s} {len(r):4d} {np.nanmean(r)*1e4:9.2f} {tstat(r):6.2f} {np.nanmean(r>0)*100:6.1f}")


if __name__ == "__main__":
    for label, stem in OILS.items():
        scan(label, stem)
