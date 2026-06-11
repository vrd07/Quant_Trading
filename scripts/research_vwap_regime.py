"""
research_vwap_regime.py — Stage 4: make the VWAP fade REGIME-ROBUST.

Stage 3 found NY-session 2sigma VWAP reversion has edge, but stage-4 validation showed
it's concentrated in 2026 H1 (choppy) and ~breakeven through 2025 (gold uptrend).
Hypothesis: reversion pays in RANGE, loses in TREND. Gate the fade on a daily regime
filter and check if the per-quarter curve flattens (robust) instead of relying on 2026.

Daily regime features (computed from PRIOR-day closes only, no lookahead):
  - Efficiency Ratio (Kaufman) over ER_N days: |netmove| / sum|move|.
    High ER => trending (skip fades). Low ER => choppy (fade-friendly).
  - Realized range regime: prior-day ATR percentile (reversion needs room to move).
  - Trend sign: prior close vs prior SMA(20) -> in uptrend allow only long-fades, etc.

We sweep ER cutoff and a directional-only switch, reporting per-quarter PF + IS/OOS.
"""
import numpy as np
import pandas as pd

CSV = "data/historical/XAUUSD_5m_real.csv"
POINT = 0.01
SPREAD_PTS = 30  # conservative honest cost baseline now


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).rename(columns=str.lower)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts").set_index("ts")
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek
    df["date"] = df.index.normalize()
    df["tp"] = (df.high + df.low + df.close) / 3
    return df[df.dow < 5]


def daily_regime(df, er_n=10, atr_n=14, sma_n=20):
    d = df.groupby("date").agg(o=("open", "first"), h=("high", "max"),
                               l=("low", "min"), c=("close", "last"))
    # efficiency ratio
    net = d.c.diff(er_n).abs()
    vol = d.c.diff().abs().rolling(er_n).sum()
    d["er"] = (net / vol).shift(1)  # prior-day, no lookahead
    # atr + percentile
    pc = d.c.shift(1)
    tr = pd.concat([d.h - d.l, (d.h - pc).abs(), (d.l - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(atr_n).mean().shift(1)
    d["atr_pct"] = d["atr"].rank(pct=True)
    # trend sign
    d["sma"] = d.c.rolling(sma_n).mean()
    d["trend"] = np.sign((d.c.shift(1) - d.sma.shift(1)))
    return d


def run(df, reg, er_max, atr_pct_min, directional, z=2.0, sl_sigma=2.0, tp_frac=1.0,
        open_h=13, eod_h=21, warmup=6):
    cost = 2 * SPREAD_PTS * POINT
    trades = []
    for date, day in df.groupby("date"):
        if date not in reg.index:
            continue
        rr = reg.loc[date]
        if np.isnan(rr.er) or np.isnan(rr.atr_pct):
            continue
        if rr.er > er_max:           # too trendy -> skip
            continue
        if rr.atr_pct < atr_pct_min:  # too quiet -> skip
            continue
        sess = day[(day.hour >= open_h) & (day.hour <= eod_h)].copy()
        if len(sess) < warmup + 6:
            continue
        cum_pv = (sess.tp * sess.volume).cumsum()
        cum_v = sess.volume.cumsum().replace(0, np.nan)
        sess["vwap"] = cum_pv / cum_v
        sess["dev"] = sess.close - sess.vwap
        sess["sigma"] = sess["dev"].expanding(min_periods=warmup).std()
        for i in range(warmup, len(sess)):
            row = sess.iloc[i]
            sig = row["sigma"]
            if not sig or np.isnan(sig) or sig <= 0:
                continue
            if abs(row["dev"]) < z * sig:
                continue
            side = -np.sign(row["dev"])
            # directional filter: only fade WITH the prevailing daily trend
            if directional and rr.trend != 0 and side != rr.trend:
                continue
            entry = row["close"]
            tp = entry + side * abs(row["dev"]) * tp_frac
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


def stats(tr):
    if len(tr) == 0:
        return dict(n=0, pf=np.nan, wr=np.nan, t=np.nan, tot=np.nan)
    r = tr["ret"].values
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else np.nan
    return dict(n=len(r), pf=pf, wr=(r > 0).mean(), t=t, tot=r.sum() * 1e4)


def quarter_line(tr):
    if len(tr) == 0:
        return "no trades"
    t = tr.copy()
    t["q"] = t["date"].dt.tz_localize(None).dt.to_period("Q").astype(str)
    out = []
    for q, g in t.groupby("q"):
        s = stats(g)
        out.append(f"{q[2:]}:{s['pf']:.2f}({s['n']})")
    return "  ".join(out)


def main():
    df = load()
    reg = daily_regime(df)
    print(f"SPREAD={SPREAD_PTS}pt one-way. NY 13-21 UTC, z=2 sl=2 tpf=1.0\n")
    print("Filter sweep — looking for FLAT per-quarter PF (all >~1), not 2026-carried:\n")
    configs = [
        ("no filter (baseline)", 1.01, 0.0, False),
        ("ER<0.5 (skip strong trend)", 0.5, 0.0, False),
        ("ER<0.4", 0.4, 0.0, False),
        ("ER<0.3 (only choppy)", 0.3, 0.0, False),
        ("ER<0.4 + atr_pct>0.3", 0.4, 0.3, False),
        ("ER<0.4 + atr_pct>0.5", 0.4, 0.5, False),
        ("directional only (fade w/ trend)", 1.01, 0.0, True),
        ("ER<0.5 + directional", 0.5, 0.0, True),
    ]
    for name, er, ap, dirn in configs:
        tr = run(df, reg, er, ap, dirn)
        if len(tr) < 15:
            print(f"{name:>34}: n={len(tr)} (too few)"); continue
        a = stats(tr)
        cut = tr["date"].quantile(0.7)
        o = stats(tr[tr.date > cut])
        print(f"{name:>34}: N={a['n']:>3} PF={a['pf']:.2f} t={a['t']:>5.2f} "
              f"tot={a['tot']:>6.0f} | oosPF={o['pf']:.2f} oosT={o['t']:>5.2f}")
        print(f"{'   per-Q PF(n):':>34} {quarter_line(tr)}")
    print("\n(Goal: a row whose per-quarter PFs are ALL roughly >1 — robust across regime,")
    print(" not a single 2026 spike. Fewer trades is fine if they're consistent.)")


if __name__ == "__main__":
    main()
