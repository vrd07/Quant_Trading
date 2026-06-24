#!/usr/bin/env python3
"""
Oil Thursday-drift validation — stage 2. The DoW scan showed Thursday as a clean
up-day on BOTH WTI and Brent (t=1.88 / 2.21), plausibly post-EIA (crude report
Wed, NatGas report Thu). Now the index_overnight bar: IS/OOS stability, per-year
consistency, cost-robustness, on a tradeable hold.

Trade = LONG at Wednesday session-close, exit Thursday session-close (captures
the Thursday close-to-close move). One trade/week per instrument.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.research_index_calendar import load, session_prices, OOS_FRAC

OILS = {"WTI": "LIGHTCMDUSD", "BRENT": "BRENTCMDUSD"}
OPEN_T, CLOSE_T = "14:00", "19:30"


def stats(r):
    r = np.asarray(r); r = r[~np.isnan(r)]
    if len(r) == 0: return dict(n=0, pf=0, ret=0, dd=0, win=0, t=0)
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    pf = w / l if l > 0 else np.inf
    eq = np.cumprod(1 + r); dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    return dict(n=len(r), pf=pf, ret=(eq[-1]-1)*100, dd=dd, win=(r > 0).mean()*100,
                t=r.mean()/(r.std()/np.sqrt(len(r))) if r.std() else 0)


def split(t):
    cut = int(len(t) * (1 - OOS_FRAC)); return t.iloc[:cut], t.iloc[cut:]


def run(label, stem, sma_gate=0):
    s = session_prices(load(stem), OPEN_T, CLOSE_T)
    s["dow"] = s.index.weekday
    s["prev_close"] = s["close_px"].shift(1)        # Wednesday close (entry)
    s["sma"] = s["close_px"].rolling(sma_gate).mean() if sma_gate else 0.0
    thu = s[s["dow"] == 3].copy()                   # Thursday rows
    thu["ret_raw"] = thu["close_px"] / thu["prev_close"] - 1.0   # Wed close -> Thu close
    if sma_gate:
        # monday_drift-style kill-switch: only go long when oil is in an uptrend
        # (Wed close > SMA). Protects the long-only edge in oil downtrends (2025).
        thu = thu[thu["prev_close"] > thu["sma"]].dropna(subset=["sma"])

    print(f"\n========== {label} Thursday LONG (Wed close → Thu close) ==========")
    print(f"{'cost_bps':>8s} {'slice':4s} {'n':>4s} {'PF':>6s} {'ret%':>7s} {'maxDD%':>7s} {'win%':>6s} {'t':>6s}")
    for cost in (4, 6, 8):
        t = thu.copy(); t["ret"] = t["ret_raw"] - cost/1e4
        IS, OOS = split(t)
        for nm, sl in ((f"c{cost}", t), (" IS", IS), (" OOS", OOS)):
            st = stats(sl["ret"].values)
            print(f"{cost if nm.startswith('c') else '':>8} {nm:4s} {st['n']:4d} {st['pf']:6.2f} {st['ret']:7.1f} {st['dd']:7.1f} {st['win']:6.1f} {st['t']:6.2f}")

    t = thu.copy(); t["ret"] = t["ret_raw"] - 4/1e4; t["yr"] = t.index.year
    print("  per-year PF (cost 4bps):",
          {int(y): round(stats(g['ret'].values)['pf'], 2) for y, g in t.groupby('yr')})


if __name__ == "__main__":
    gate = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if gate:
        print(f"### SMA({gate}) uptrend gate ON ###")
    for label, stem in OILS.items():
        run(label, stem, sma_gate=gate)
