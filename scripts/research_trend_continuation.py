"""
research_trend_continuation.py — Stage 5: a TREND-day complement to the RANGE fade.

The VWAP reversion (stage 4) only trades RANGE days (ER<0.4). This hunts the mirror:
on TREND days (high ER), does gold CONTINUE rather than revert? If yes, the two
strategies are mechanically uncorrelated (mutually-exclusive ER buckets) and together
cover both regimes.

Two continuation flavors tested on the SAME session-VWAP machinery, NY 13-21 UTC, EOD-flat:
  MODE A "ride"     : on a trend day, when dev >= z*sigma IN the trend direction,
                      enter WITH it (ride the extension). TP further out, SL behind.
  MODE B "pullback" : on a trend day, when price pulls back and TOUCHES VWAP,
                      enter in the daily-trend direction (buy the dip in an uptrend).
                      TP = k*sigma extension, SL = sl*sigma beyond VWAP.

Trend direction = prior-day close vs prior SMA20 (no lookahead). Conservative 30pt cost.
"""
import numpy as np
import pandas as pd
from research_vwap_regime import load, daily_regime, stats

POINT = 0.01
SPREAD_PTS = 30


def run(df, reg, mode, er_min, z=1.5, sl_sigma=2.0, tp_sigma=3.0,
        open_h=13, eod_h=21, warmup=6):
    cost = 2 * SPREAD_PTS * POINT
    trades = []
    for date, day in df.groupby("date"):
        if date not in reg.index:
            continue
        rr = reg.loc[date]
        if np.isnan(rr.er) or rr.er < er_min or rr.trend == 0:
            continue  # need a clear trend day with a known direction
        trend = rr.trend
        sess = day[(day.hour >= open_h) & (day.hour <= eod_h)].copy()
        if len(sess) < warmup + 6:
            continue
        cum_pv = (sess.tp * sess.volume).cumsum()
        cum_v = sess.volume.cumsum().replace(0, np.nan)
        sess["vwap"] = cum_pv / cum_v
        sess["dev"] = sess.close - sess.vwap
        sess["sigma"] = sess["dev"].expanding(min_periods=warmup).std()
        prev_dev = None
        for i in range(warmup, len(sess)):
            row = sess.iloc[i]
            sig = row["sigma"]
            if not sig or np.isnan(sig) or sig <= 0:
                prev_dev = row["dev"]; continue
            fire = False
            if mode == "ride":
                # same-direction extension as the trend
                if abs(row["dev"]) >= z * sig and np.sign(row["dev"]) == trend:
                    fire = True
            elif mode == "pullback":
                # price pulls back to VWAP: dev crosses 0 toward trend side, or touches
                touched = (prev_dev is not None and
                           np.sign(prev_dev) != np.sign(row["dev"])) or \
                          (abs(row["dev"]) <= 0.25 * sig)
                # only in trend direction: in uptrend we want price near/just-below VWAP
                if touched:
                    fire = True
            prev_dev = row["dev"]
            if not fire:
                continue
            side = trend
            entry = row["close"]
            tp = entry + side * tp_sigma * sig
            sl = entry - side * sl_sigma * sig
            exit_px = sess.iloc[-1]["close"]
            for j in range(i + 1, len(sess)):
                b = sess.iloc[j]
                if side > 0:
                    if b.low <= sl: exit_px = sl; break
                    if b.high >= tp: exit_px = tp; break
                else:
                    if b.high >= sl: exit_px = sl; break
                    if b.low <= tp: exit_px = tp; break
            trades.append((date, side, (side * (exit_px - entry) - cost) / entry))
            break
    return pd.DataFrame(trades, columns=["date", "side", "ret"])


def qline(tr):
    if len(tr) == 0:
        return ""
    t = tr.copy()
    t["q"] = t["date"].dt.tz_localize(None).dt.to_period("Q").astype(str)
    return "  ".join(f"{q[2:]}:{stats(g)['pf']:.2f}({stats(g)['n']})"
                     for q, g in t.groupby("q"))


def main():
    df = load()
    reg = daily_regime(df)
    print(f"TREND-day continuation, NY 13-21 UTC, {SPREAD_PTS}pt cost.\n")
    grid = [
        ("ride",     0.5, 1.5, 2.0, 3.0),
        ("ride",     0.5, 2.0, 2.0, 3.0),
        ("ride",     0.6, 1.5, 2.0, 3.0),
        ("ride",     0.6, 1.0, 1.5, 2.5),
        ("pullback", 0.5, 0.0, 1.5, 2.0),
        ("pullback", 0.5, 0.0, 1.5, 3.0),
        ("pullback", 0.5, 0.0, 2.0, 3.0),
        ("pullback", 0.6, 0.0, 2.0, 3.0),
        ("pullback", 0.4, 0.0, 1.5, 2.5),
    ]
    for mode, er, z, sl, tp in grid:
        tr = run(df, reg, mode, er, z=z, sl_sigma=sl, tp_sigma=tp)
        if len(tr) < 15:
            print(f"{mode:>9} er>{er} z{z} sl{sl} tp{tp}: n={len(tr)} (too few)"); continue
        a = stats(tr)
        cut = tr["date"].quantile(0.7)
        o = stats(tr[tr.date > cut])
        print(f"{mode:>9} er>{er} z{z} sl{sl} tp{tp}: N={a['n']:>3} PF={a['pf']:.2f} "
              f"t={a['t']:>5.2f} tot={a['tot']:>6.0f} | oosPF={o['pf']:.2f} oosT={o['t']:>5.2f}")
        print(f"            per-Q: {qline(tr)}")


if __name__ == "__main__":
    main()
