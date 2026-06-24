#!/usr/bin/env python3
"""
Silver + JPY-cross calendar/session scan — same proven method (overnight/intraday
decomposition + day-of-week, IS/OOS) that found the index Tuesday-overnight edge.

- XAGUSD (silver): precious-metal cousin of gold (~0.7 corr → less diversifying,
  but trades differently — more industrial/volatile). NY session 13:30–20:00.
- EURJPY / AUDJPY (JPY crosses): carry / risk-proxy pairs with strong session
  effects; less USD-driven than the majors. London+NY window 07:00–20:00.

A shippable edge must (a) beat its other weekdays, (b) hold IS/OOS, (c) be
per-year consistent. Stage-1 only — surface candidates, then validate like oil.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.research_index_calendar import load, session_prices, tstat, OOS_FRAC

# label -> (stem, open_t, close_t)
INSTR = {
    "XAGUSD": ("XAGUSD", "13:30", "20:00"),
    "EURJPY": ("EURJPY", "07:00", "20:00"),
    "AUDJPY": ("AUDJPY", "07:00", "20:00"),
}
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def split(s):
    cut = int(len(s) * (1 - OOS_FRAC)); return s.iloc[:cut], s.iloc[cut:]


def scan(label, stem, open_t, close_t):
    s = session_prices(load(stem), open_t, close_t)
    s["intraday"] = s["close_px"] / s["open_px"] - 1.0
    s["overnight"] = s["open_px"] / s["close_px"].shift(1) - 1.0
    s["daily"] = s["close_px"].pct_change()
    s["dow"] = s.index.weekday
    s = s.dropna()
    IS, OOS = split(s)

    print(f"\n========== {label} ({stem})  sessions={len(s)} ==========")
    print("Overnight vs intraday:")
    for leg in ("overnight", "intraday"):
        for nm, sl in (("ALL", s), ("IS", IS), ("OOS", OOS)):
            r = sl[leg].values
            print(f"  {leg:9s} {nm:4s} {np.nanmean(r)*1e4:8.2f}bps t={tstat(r):5.2f} win={np.nanmean(r>0)*100:5.1f}%")

    for leg in ("overnight", "daily"):
        print(f"\n  DoW of {leg} (mean_bps / t / win% / IS-bps / OOS-bps):")
        for d in range(5):
            r = s[s["dow"] == d][leg].values
            ri = IS[IS["dow"] == d][leg].values
            ro = OOS[OOS["dow"] == d][leg].values
            if len(r) == 0:
                continue
            print(f"  {_WD[d]:>4s} n={len(r):4d} {np.nanmean(r)*1e4:8.2f} t={tstat(r):5.2f} "
                  f"win={np.nanmean(r>0)*100:5.1f}  IS={np.nanmean(ri)*1e4 if len(ri) else 0:7.2f} "
                  f"OOS={np.nanmean(ro)*1e4 if len(ro) else 0:7.2f}")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for label, (stem, ot, ct) in INSTR.items():
        if only and label != only:
            continue
        try:
            scan(label, stem, ot, ct)
        except FileNotFoundError:
            print(f"{label}: no data yet ({stem})")
