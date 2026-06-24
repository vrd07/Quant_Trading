#!/usr/bin/env python3
"""
Stage 3 — the focused candidate: MIDWEEK OVERNIGHT INDEX LONG ("Turnaround
Tuesday" night drift). Cross-instrument-consistent in stage 2 (Tue overnight
+ve & significant on NAS100/US30/GER40 independently).

Structure mirrors the shipped `monday_drift` (the ONE thing that passed the
strict gate): low-frequency (~1/wk), wide window, time-exit, regime-gated.

Trade = LONG at Tue cash-close (buf=1 bar inside), exit at Wed cash-open
(buf=1 bar inside). Optional Wed-entry leg too. Regime gate: only enter if
daily close > SMA(N) (don't catch a bear leg — the monday_drift kill-switch).

Reports IS/OOS(70/30) PF/ret/maxDD, financing sensitivity, regime-gate effect,
and a per-year breakdown (consistency, not one-year wonder).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DATA = PROJECT_ROOT / "data" / "historical"
INDICES = {
    "NAS100": ("USATECHIDXUSD", "13:30", "20:00"),
    "US30":   ("USA30IDXUSD",   "13:30", "20:00"),
    "GER40":  ("DEUIDXEUR",     "07:00", "15:30"),
}
OOS_FRAC = 0.30
BAR_MIN = 5


def load(stem):
    df = pd.read_csv(DATA / f"{stem}_5m_real.csv", parse_dates=["timestamp"])
    return df.set_index("timestamp").sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])


def at_offset(g, gm, target_min, n_bars, direction):
    want = target_min + direction * n_bars * BAR_MIN
    cand = g[(gm - want).abs() <= 15]
    if len(cand) == 0:
        return None
    return g.loc[(gm.loc[cand.index] - want).abs().idxmin()]


def build(df, open_t, close_t, buf=1):
    oh, om = map(int, open_t.split(":")); ch, cm = map(int, close_t.split(":"))
    open_min, close_min = oh*60+om, ch*60+cm
    mins = pd.Series(df.index.hour*60 + df.index.minute, index=df.index)
    days = df.index.normalize()
    rows = []
    for d, g in df.groupby(days):
        gm = mins[g.index]
        entry = at_offset(g, gm, close_min, buf, -1)   # this day's close - buf
        ex = at_offset(g, gm, open_min, buf, +1)       # this day's open + buf (for next-day exit lookup)
        if entry is None or ex is None:
            continue
        rows.append((d, entry["close"], ex["open"], d.weekday()))
    s = pd.DataFrame(rows, columns=["day","close_px","open_px","dow"]).set_index("day")
    # exit is NEXT session's open
    s["exit"] = s["open_px"].shift(-1)
    s["sma"] = s["close_px"].rolling(50).mean()
    return s.dropna(subset=["exit"])


def stats(r):
    r = np.asarray(r); r = r[~np.isnan(r)]
    if len(r)==0: return dict(n=0,pf=0,ret=0,dd=0,win=0,sharpe=0,t=0)
    w, l = r[r>0].sum(), -r[r<0].sum()
    pf = w/l if l>0 else np.inf
    eq = np.cumprod(1+r); dd = (eq/np.maximum.accumulate(eq)-1).min()*100
    return dict(n=len(r), pf=pf, ret=(eq[-1]-1)*100, dd=dd, win=(r>0).mean()*100,
                sharpe=r.mean()/r.std()*np.sqrt(50) if r.std() else 0,
                t=r.mean()/(r.std()/np.sqrt(len(r))) if r.std() else 0)


def trades(s, dows, fin_bps, cost_bps, regime_gate):
    sel = s[s["dow"].isin(dows)].copy()
    if regime_gate:
        sel = sel[sel["close_px"] > sel["sma"]]
    gross = sel["exit"]/sel["close_px"] - 1.0
    sel["ret"] = gross - cost_bps/1e4 - fin_bps/1e4
    return sel


def report(label, stem, open_t, close_t):
    df = load(stem)
    s = build(df, open_t, close_t, buf=1)
    print(f"\n############## {label}  midweek overnight long ##############")

    for tag, dows in (("Tue-only",[1]), ("Tue+Wed",[1,2])):
        for gate in (False, True):
            t = trades(s, dows, fin_bps=2, cost_bps=2, regime_gate=gate)
            cut = int(len(t)*(1-OOS_FRAC))
            IS, OOS = t.iloc[:cut], t.iloc[cut:]
            g = "SMA50-gate" if gate else "no-gate   "
            print(f"\n-- {tag:8s} {g} (fin2/cost2 bps) --")
            print(f"{'slice':4s} {'n':>4s} {'PF':>6s} {'ret%':>8s} {'maxDD%':>7s} {'win%':>6s} {'t':>6s}")
            for nm, sl in (("ALL",t),("IS",IS),("OOS",OOS)):
                st = stats(sl["ret"].values)
                print(f"{nm:4s} {st['n']:4d} {st['pf']:6.2f} {st['ret']:8.1f} {st['dd']:7.1f} {st['win']:6.1f} {st['t']:6.2f}")

    # per-year, Tue-only no-gate (consistency)
    t = trades(s, [1], fin_bps=2, cost_bps=2, regime_gate=False)
    t["yr"] = t.index.year
    print(f"\n-- Tue-only per-year (fin2/cost2) --")
    print(f"{'yr':>4s} {'n':>4s} {'PF':>6s} {'ret%':>7s} {'win%':>6s}")
    for yr, sub in t.groupby("yr"):
        st = stats(sub["ret"].values)
        print(f"{yr:4d} {st['n']:4d} {st['pf']:6.2f} {st['ret']:7.1f} {st['win']:6.1f}")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv)>1 else "ALL"
    for label,(stem,ot,ct) in INDICES.items():
        if only!="ALL" and label!=only: continue
        report(label, stem, ot, ct)
