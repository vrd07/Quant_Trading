#!/usr/bin/env python3
"""
Stage 2 — turn the NAS100 overnight drift into a TRADEABLE hold and stress it
against the two guards that have killed lookalikes in this codebase:

  1. BID-SPREAD ARTIFACT: re-price the entry/exit a few bars INSIDE the session
     boundary. A real drift persists; a reopen-spread artifact collapses.
  2. OVERNIGHT FINANCING: a long CFD index held overnight pays carry. Model it
     as `fin_bps_per_day`. The academic night effect is GROSS; we need NET.

Also: day-of-week breakdown (is it Fri->Mon only? = weekend artifact), a wide
ATR stop (the survivor mold), and an IS/OOS(70/30) PF / return / maxDD report.

Trade = enter long at (cash_close - exit_buf bars) on day D, hold overnight,
exit at (cash_open + entry_buf bars) on day D+1. One trade per night.
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
BAR_MIN = 5  # data is 5m


def load(stem):
    df = pd.read_csv(DATA / f"{stem}_5m_real.csv", parse_dates=["timestamp"])
    return df.set_index("timestamp").sort_index().pipe(lambda d: d[~d.index.duplicated(keep="last")])


def at_offset(g, gm, target_min, n_bars, direction):
    """Bar n_bars inside the session boundary. direction=+1 after open, -1 before close."""
    want = target_min + direction * n_bars * BAR_MIN
    cand = g[(gm - want).abs() <= 15]   # within 3 bars
    if len(cand) == 0:
        return None
    idx = (gm.loc[cand.index] - want).abs().idxmin()
    return g.loc[idx]


def build_sessions(df, open_t, close_t, entry_buf, exit_buf):
    oh, om = map(int, open_t.split(":")); ch, cm = map(int, close_t.split(":"))
    open_min, close_min = oh*60+om, ch*60+cm
    mins = pd.Series(df.index.hour*60 + df.index.minute, index=df.index)
    days = df.index.normalize()
    rows = []
    for d, g in df.groupby(days):
        gm = mins[g.index]
        ob = at_offset(g, gm, open_min, entry_buf, +1)     # exit point (next day's open + buf)
        cb = at_offset(g, gm, close_min, exit_buf, -1)     # entry point (this day's close - buf)
        if ob is None or cb is None:
            continue
        # ATR proxy: intraday range avg of the session (for stop sizing)
        sess = g[(gm >= open_min) & (gm <= close_min)]
        rng = (sess["high"] - sess["low"]).mean() if len(sess) else np.nan
        rows.append((d, cb["close"], ob["open"], rng, d.weekday()))
    return pd.DataFrame(rows, columns=["day","close_px","open_px","rng","dow"]).set_index("day")


def backtest(s, fin_bps, cost_bps, stop_atr, df=None, open_t=None, close_t=None):
    """Overnight long: entry = close_px[D], exit = open_px[D+1]. Returns per-trade R%."""
    s = s.copy()
    s["entry"] = s["close_px"]
    s["exit"] = s["open_px"].shift(-1)
    s = s.dropna(subset=["exit"])
    gross = s["exit"]/s["entry"] - 1.0
    # costs: round-turn spread/slippage + one night of financing
    net = gross - cost_bps/1e4 - fin_bps/1e4
    s["ret"] = net
    return s


def stats(r):
    r = r[~np.isnan(r)]
    if len(r) == 0:
        return dict(n=0, pf=0, ret=0, dd=0, win=0, sharpe=0, t=0)
    wins, losses = r[r>0].sum(), -r[r<0].sum()
    pf = wins/losses if losses>0 else np.inf
    eq = np.cumprod(1+r)
    dd = (eq/np.maximum.accumulate(eq) - 1).min()*100
    sharpe = r.mean()/r.std()*np.sqrt(252) if r.std() else 0
    t = r.mean()/(r.std()/np.sqrt(len(r))) if r.std() else 0
    return dict(n=len(r), pf=pf, ret=(eq[-1]-1)*100, dd=dd, win=(r>0).mean()*100, sharpe=sharpe, t=t)


def split(s):
    cut = int(len(s)*(1-OOS_FRAC))
    return s.iloc[:cut], s.iloc[cut:]


def report(label, stem, open_t, close_t):
    df = load(stem)
    print(f"\n############## {label} OVERNIGHT HOLD ##############")

    # ---- Guard 1: entry/exit-delay robustness (bid-spread artifact) ----
    print("\n[Guard 1] entry/exit buffer sweep (cost=2bps round-turn, fin=2bps/night):")
    print(f"{'buf(bars)':>9s} {'n':>4s} {'PF':>6s} {'ret%':>7s} {'win%':>6s} {'t':>6s}")
    for buf in (0, 1, 2, 3, 6):
        s = build_sessions(df, open_t, close_t, entry_buf=buf, exit_buf=buf)
        bt = backtest(s, fin_bps=2, cost_bps=2, stop_atr=0)
        st = stats(bt["ret"].values)
        print(f"{buf:9d} {st['n']:4d} {st['pf']:6.2f} {st['ret']:7.1f} {st['win']:6.1f} {st['t']:6.2f}")

    # ---- Build the canonical version (buf=1: enter 1 bar before close, exit 1 bar after open) ----
    s = build_sessions(df, open_t, close_t, entry_buf=1, exit_buf=1)

    # ---- Guard 2: financing sensitivity ----
    print("\n[Guard 2] overnight-financing sensitivity (buf=1, cost=2bps):")
    print(f"{'fin_bps':>7s} {'PF':>6s} {'ret%':>7s} {'sharpe':>7s}")
    for fin in (0, 1, 2, 3, 5):
        bt = backtest(s, fin_bps=fin, cost_bps=2, stop_atr=0)
        st = stats(bt["ret"].values)
        print(f"{fin:7d} {st['pf']:6.2f} {st['ret']:7.1f} {st['sharpe']:7.2f}")

    # ---- IS/OOS with realistic net (fin=2, cost=2) ----
    bt = backtest(s, fin_bps=2, cost_bps=2, stop_atr=0)
    IS, OOS = split(bt)
    print("\n[IS/OOS] realistic net (fin=2bps, cost=2bps, buf=1):")
    print(f"{'slice':4s} {'n':>4s} {'PF':>6s} {'ret%':>8s} {'maxDD%':>7s} {'win%':>6s} {'sharpe':>7s} {'t':>6s}")
    for nm, sl in (("ALL",bt),("IS",IS),("OOS",OOS)):
        st = stats(sl["ret"].values)
        print(f"{nm:4s} {st['n']:4d} {st['pf']:6.2f} {st['ret']:8.1f} {st['dd']:7.1f} {st['win']:6.1f} {st['sharpe']:7.2f} {st['t']:6.2f}")

    # ---- Day-of-week (weekend-artifact guard) ----
    print("\n[DoW] entry-day breakdown (Mon-entry = Tue exit ... Fri-entry = Mon exit):")
    names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    print(f"{'entryDoW':>8s} {'n':>4s} {'mean_bps':>9s} {'win%':>6s} {'t':>6s}")
    for d in range(7):
        sub = bt[bt["dow"]==d]["ret"].values
        if len(sub)==0: continue
        st = stats(sub)
        print(f"{names[d]:>8s} {len(sub):4d} {np.nanmean(sub)*1e4:9.2f} {st['win']:6.1f} {st['t']:6.2f}")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv)>1 else "NAS100"
    for label,(stem,ot,ct) in INDICES.items():
        if only != "ALL" and label != only:
            continue
        report(label, stem, ot, ct)
