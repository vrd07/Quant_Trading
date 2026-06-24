#!/usr/bin/env python3
"""
Gold macro-overlay diagnostic — does a DXY / 10y-yield REGIME condition gold's
forward returns enough to gate the gold book (esp. kalman's SELL-side bleed)?

Prior caution (project_gss_no_directional_edge): the DXY/real-yield legs showed
~zero IC for gold DIRECTION over 10y. So we're not chasing directional alpha —
we test whether the regime has DEFENSIVE conditioning value: is gold's drift
materially worse (shorts pay) when DXY/yields are RISING, and better when FALLING?
If a clean split exists and is OOS-stable, it can gate kalman SELL (only short
into a rising-dollar / rising-yield backdrop).

Cheap stage-1 on daily data (mirrors the turn-of-month/overnight diagnostics):
IC + regime split + IS/OOS, full 20y and the recent live window.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
GLD = ROOT / "data" / "historical" / "GLD_daily.csv"
OOS_FRAC = 0.30


def load_panel(start="2010-01-01"):
    g = pd.read_csv(GLD, parse_dates=["date"]).set_index("date")["close"].rename("gold")
    g = g[g.index >= start]
    dxy = yf.Ticker("DX-Y.NYB").history(start=start, auto_adjust=False)["Close"].rename("dxy")
    tnx = yf.Ticker("^TNX").history(start=start, auto_adjust=False)["Close"].rename("tnx")
    for s in (dxy, tnx):
        s.index = s.index.tz_localize(None)
    df = pd.concat([g, dxy, tnx], axis=1).dropna()
    df["g_ret"] = df["gold"].pct_change()
    # macro momentum features
    df["dxy_mom20"] = df["dxy"].pct_change(20)
    df["dxy_mom5"] = df["dxy"].pct_change(5)
    df["tnx_chg20"] = df["tnx"].diff(20)
    # forward gold returns
    for h in (1, 5, 20):
        df[f"fwd{h}"] = df["gold"].shift(-h) / df["gold"] - 1.0
    return df.dropna()


def ic(x, y):
    x, y = np.asarray(x), np.asarray(y)
    m = ~(np.isnan(x) | np.isnan(y))
    if m.sum() < 10:
        return 0.0
    return np.corrcoef(x[m], y[m])[0, 1]


def split(df):
    cut = int(len(df) * (1 - OOS_FRAC))
    return df.iloc[:cut], df.iloc[cut:]


def report(df, tag):
    print(f"\n================ {tag}  ({df.index[0].date()}..{df.index[-1].date()}, n={len(df)}) ================")
    IS, OOS = split(df)

    # 1. IC of each macro feature vs forward gold return (sign: dollar up => gold down expected => negative IC)
    print("IC (macro feature → forward gold return):")
    print(f"{'feature':10s} {'fwd':>4s} {'IC_all':>7s} {'IC_IS':>7s} {'IC_OOS':>7s}")
    for feat in ("dxy_mom20", "dxy_mom5", "tnx_chg20"):
        for h in (1, 5, 20):
            print(f"{feat:10s} {h:4d} {ic(df[feat], df[f'fwd{h}']):7.3f} "
                  f"{ic(IS[feat], IS[f'fwd{h}']):7.3f} {ic(OOS[feat], OOS[f'fwd{h}']):7.3f}")

    # 2. Regime split: forward 5d gold return when DXY rising vs falling (20d momentum)
    print("\nRegime split — gold fwd5d return by DXY 20d-momentum sign:")
    print(f"{'regime':14s} {'slice':4s} {'n':>5s} {'mean_bps':>9s} {'ann%':>7s} {'win%':>6s}")
    for name, mask in (("DXY falling", df["dxy_mom20"] < 0), ("DXY rising", df["dxy_mom20"] > 0)):
        for sl_name, sl in (("ALL", df), ("IS", IS), ("OOS", OOS)):
            m = mask.reindex(sl.index, fill_value=False)
            r = sl["fwd5"][m].values
            if len(r) == 0:
                continue
            print(f"{name:14s} {sl_name:4s} {len(r):5d} {np.nanmean(r)*1e4:9.1f} "
                  f"{np.nanmean(r)*52*100:7.1f} {np.nanmean(r>0)*100:6.1f}")

    # 3. The defensive question: how negative is gold drift in the rising-DXY AND rising-yield regime?
    both_up = (df["dxy_mom20"] > 0) & (df["tnx_chg20"] > 0)
    both_dn = (df["dxy_mom20"] < 0) & (df["tnx_chg20"] < 0)
    print("\nDual-macro regime — gold fwd5d:")
    for name, mask in (("DXY+yield UP (short-friendly?)", both_up),
                       ("DXY+yield DOWN (long-friendly?)", both_dn)):
        r = df["fwd5"][mask].values
        print(f"  {name:32s} n={len(r):4d}  mean={np.nanmean(r)*1e4:7.1f}bps  win={np.nanmean(r>0)*100:5.1f}%")


if __name__ == "__main__":
    df = load_panel(start="2010-01-01")
    report(df, "FULL 2010-2026")
    report(df[df.index >= "2024-01-01"], "LIVE WINDOW 2024-2026")
