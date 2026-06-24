#!/usr/bin/env python3
"""
Turn-of-Month (TOM) effect on equity indices — next gold-uncorrelated calendar
candidate after index_overnight. Documented anomaly: indices drift up around the
month boundary (last trading day + first ~3 of the new month) on pension/401k
inflows. Same wide-stop / time-exit / low-frequency mold that survives strict
fills and the $5k kill switch.

Method mirrors research_index_calendar (cash-session closes, IS/OOS 70/30, costs,
per-year, cross-instrument). Stage 1: is the TOM-window daily return materially
> non-TOM, OOS-stable, on BOTH US30 and NAS100? Stage 2: a tradeable hold
(enter last-trading-day close, exit after H trading days).
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.research_index_calendar import load, session_prices, tstat, INDICES, OOS_FRAC


def daily_close_series(stem, open_t, close_t):
    df = load(stem)
    s = session_prices(df, open_t, close_t)        # per trading-day cash open/close
    s = s[["close_px"]].rename(columns={"close_px": "close"})
    s["ret"] = s["close"].pct_change()             # close-to-close daily return
    # month structure
    months = s.index.to_period("M")
    s["tdom"] = s.groupby(months).cumcount() + 1                       # trading-day-of-month (1=first)
    s["tdte"] = s.groupby(months).cumcount(ascending=False)           # 0 = last trading day of month
    return s.dropna()


def windows(s, first_n=3, last_n=1):
    """TOM window = first `first_n` trading days of a month OR last `last_n` of prior."""
    return (s["tdom"] <= first_n) | (s["tdte"] < last_n)


def split(s):
    cut = int(len(s) * (1 - OOS_FRAC))
    return s.iloc[:cut], s.iloc[cut:]


def stage1(label, stem, open_t, close_t):
    s = daily_close_series(stem, open_t, close_t)
    tom = windows(s)
    print(f"\n===== {label} TOM daily-return diagnostic  (sessions={len(s)}) =====")
    print(f"{'group':10s} {'slice':4s} {'n':>4s} {'mean_bps':>9s} {'t':>6s} {'win%':>6s}")
    for gname, mask in (("TOM", tom), ("non-TOM", ~tom)):
        for nm, sl in (("ALL", s), ("IS", split(s)[0]), ("OOS", split(s)[1])):
            m = mask.reindex(sl.index, fill_value=False)
            r = sl["ret"][m].values
            if len(r) == 0:
                continue
            print(f"{gname:10s} {nm:4s} {len(r):4d} {np.nanmean(r)*1e4:9.2f} {tstat(r):6.2f} {np.nanmean(r>0)*100:6.1f}")
    return s


def stats(r):
    r = np.asarray(r); r = r[~np.isnan(r)]
    if len(r) == 0: return dict(n=0, pf=0, ret=0, dd=0, win=0, t=0)
    w, l = r[r > 0].sum(), -r[r < 0].sum()
    pf = w / l if l > 0 else np.inf
    eq = np.cumprod(1 + r); dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    return dict(n=len(r), pf=pf, ret=(eq[-1]-1)*100, dd=dd, win=(r > 0).mean()*100,
                t=r.mean()/(r.std()/np.sqrt(len(r))) if r.std() else 0)


def stage2(label, s, hold=4, cost_bps=4):
    """Tradeable: long at last-trading-day close, exit after `hold` trading days."""
    entries = s.index[s["tdte"] == 0]              # last trading day of each month
    closes = s["close"]
    pos = {d: i for i, d in enumerate(s.index)}
    trades = []
    for d in entries:
        i = pos[d]
        j = min(i + hold, len(s) - 1)
        if j <= i:
            continue
        gross = closes.iloc[j] / closes.iloc[i] - 1.0
        trades.append((s.index[j], gross - cost_bps/1e4))
    t = pd.DataFrame(trades, columns=["exit", "ret"]).set_index("exit")
    IS, OOS = split(t)
    print(f"\n-- {label} tradeable: LTD-close → +{hold}td (cost {cost_bps}bps) --")
    print(f"{'slice':4s} {'n':>4s} {'PF':>6s} {'ret%':>8s} {'maxDD%':>7s} {'win%':>6s} {'t':>6s}")
    for nm, sl in (("ALL", t), ("IS", IS), ("OOS", OOS)):
        st = stats(sl["ret"].values)
        print(f"{nm:4s} {st['n']:4d} {st['pf']:6.2f} {st['ret']:8.1f} {st['dd']:7.1f} {st['win']:6.1f} {st['t']:6.2f}")
    t["yr"] = t.index.year
    print("  per-year:", {int(y): round(stats(g['ret'].values)['pf'], 2) for y, g in t.groupby('yr')})


if __name__ == "__main__":
    for label, (stem, ot, ct) in INDICES.items():
        if label == "GER40":
            continue   # broker doesn't offer it
        s = stage1(label, stem, ot, ct)
        for h in (3, 4, 5):
            stage2(label, s, hold=h)
