"""
research_vwap_reversion.py — Stage 3: NY-session VWAP reversion (EOD-flat).

Rationale: raw open->now thrust fade failed OOS (stage 2). VWAP is the volume-weighted
fair-value institutions revert toward and it ADAPTS through the session. We test:
  - Anchor a session VWAP + running stdev of (price - vwap) from NY open (13:00 UTC).
  - When close extends >= z * sigma from VWAP, FADE toward VWAP.
  - TP = back to VWAP (or partial), SL = sl_sigma * sigma beyond entry, EOD-flat 21:00.
  - One position per day max (first qualifying signal), costs included, IS/OOS split.

Edge is only real if OOS PF > 1 across neighbouring z / sl cells with a non-trivial t.
"""
import numpy as np
import pandas as pd

CSV = "data/historical/XAUUSD_5m_real.csv"
POINT = 0.01
SPREAD_PTS = 20


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).rename(columns=str.lower)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts").set_index("ts")
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek
    df["date"] = df.index.normalize()
    df["tp"] = (df.high + df.low + df.close) / 3
    return df[df.dow < 5]


def run(df, open_h, eod_h, warmup_bars, z, sl_sigma, tp_frac):
    """tp_frac: 1.0 => target full revert to VWAP; 0.5 => halfway."""
    cost = 2 * SPREAD_PTS * POINT
    trades = []
    for date, day in df.groupby("date"):
        sess = day[(day.hour >= open_h) & (day.hour <= eod_h)].copy()
        if len(sess) < warmup_bars + 6:
            continue
        cum_pv = (sess.tp * sess.volume).cumsum()
        cum_v = sess.volume.cumsum().replace(0, np.nan)
        sess["vwap"] = cum_pv / cum_v
        sess["dev"] = sess.close - sess.vwap
        sess["sigma"] = sess["dev"].expanding(min_periods=warmup_bars).std()
        in_trade = False
        for i in range(warmup_bars, len(sess)):
            row = sess.iloc[i]
            sig = row["sigma"]
            if not in_trade and sig and not np.isnan(sig) and sig > 0:
                if abs(row["dev"]) >= z * sig:
                    side = -np.sign(row["dev"])  # fade toward vwap
                    entry = row["close"]
                    vwap_now = row["vwap"]
                    tp = entry + side * abs(row["dev"]) * tp_frac
                    sl = entry - side * sl_sigma * sig
                    # walk forward to exit
                    exit_px = sess.iloc[-1]["close"]
                    for j in range(i + 1, len(sess)):
                        b = sess.iloc[j]
                        if side > 0:
                            if b["low"] <= sl: exit_px = sl; break
                            if b["high"] >= tp: exit_px = tp; break
                        else:
                            if b["high"] >= sl: exit_px = sl; break
                            if b["low"] <= tp: exit_px = tp; break
                    net = side * (exit_px - entry) - cost
                    trades.append((date, side, net / entry))
                    in_trade = True  # one trade per day
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


def main():
    df = load()
    print("NY-session VWAP reversion, EOD-flat. open 13:00 -> exit 21:00 UTC\n")
    print(f"{'z':>4} {'sl':>4} {'tpf':>4} | {'N':>4} {'PF':>5} {'WR':>4} {'t':>5} "
          f"{'totbps':>7} || {'oosN':>4} {'oosPF':>5} {'oosWR':>4} {'oost':>5}")
    for z in (1.0, 1.5, 2.0, 2.5):
        for sl in (1.5, 2.0, 3.0):
            for tpf in (0.7, 1.0):
                tr = run(df, 13, 21, warmup_bars=6, z=z, sl_sigma=sl, tp_frac=tpf)
                if len(tr) < 20:
                    continue
                cut = tr["date"].quantile(0.7)
                a, o = stats(tr), stats(tr[tr.date > cut])
                flag = "  <==" if (a["pf"] > 1.1 and o["pf"] > 1.05 and a["t"] > 1.5) else ""
                print(f"{z:>4} {sl:>4} {tpf:>4} | {a['n']:>4} {a['pf']:>5.2f} "
                      f"{a['wr']*100:>3.0f}% {a['t']:>5.2f} {a['tot']:>7.0f} || "
                      f"{o['n']:>4} {o['pf']:>5.2f} {o['wr']*100:>3.0f}% {o['t']:>5.2f}{flag}")


if __name__ == "__main__":
    main()
