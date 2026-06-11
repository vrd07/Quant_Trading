"""
research_session_fade.py — Stage 2: validate the intraday MEAN-REVERSION edge.

Stage 1 found corr(early-session move, rest-of-session move) = -0.25 for London and
NY-open long-breakouts that lose money. Theme: gold fades its own intraday thrusts.

Here we build a tradeable, EOD-flat fade and stress it:
  - Entry: at `assess_hour`, measure move since `open_hour` open, normalized by ATR.
    If |move| >= k*ATR (an over-extension), FADE it (enter opposite).
  - Risk: SL = sl_atr * ATR, TP = tp_atr * ATR, hard EOD-flat exit at `eod_hour`.
  - Costs: round-trip spread subtracted.
  - Robustness: report IN-SAMPLE (first 70%) and OUT-OF-SAMPLE (last 30%) separately,
    plus a small parameter sweep so we see if the edge is a ridge or a single lucky cell.

PF = gross profit / gross loss. Edge is only real if OOS PF > 1 across neighbouring params.
"""
import numpy as np
import pandas as pd

CSV = "data/historical/XAUUSD_5m_real.csv"
POINT = 0.01
SPREAD_PTS = 20  # one-way; round trip = 2x


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).rename(columns=str.lower)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts").set_index("ts")
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek
    df["date"] = df.index.normalize()
    df = df[df["dow"] < 5]
    return df


def atr_daily(df, n=14):
    """Per-day ATR (price units) from prior days' true range, on 5m->daily agg."""
    daily = df.groupby("date").agg(h=("high", "max"), l=("low", "min"),
                                   c=("close", "last"))
    pc = daily["c"].shift(1)
    tr = pd.concat([daily["h"] - daily["l"], (daily["h"] - pc).abs(),
                    (daily["l"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().shift(1)  # prior-day ATR, no lookahead


def run_fade(df, atr, open_hour, assess_hour, eod_hour, k, sl_atr, tp_atr):
    trades = []  # (date, side, ret_frac_net)
    cost = 2 * SPREAD_PTS * POINT
    for date, day in df.groupby("date"):
        a = atr.get(date, np.nan)
        if np.isnan(a) or a <= 0:
            continue
        sess = day[(day.hour >= open_hour) & (day.hour <= eod_hour)]
        if len(sess) < 12:
            continue
        opn = sess[sess.hour == open_hour]
        if len(opn) == 0:
            continue
        open_px = opn.iloc[0]["open"]
        assess = sess[sess.hour < assess_hour]
        if len(assess) == 0:
            continue
        cur_px = assess.iloc[-1]["close"]
        move = cur_px - open_px
        if abs(move) < k * a:
            continue
        side = -np.sign(move)  # FADE
        entry = cur_px
        sl = entry - side * sl_atr * a
        tp = entry + side * tp_atr * a
        after = sess[sess.hour >= assess_hour]
        exit_px = after.iloc[-1]["close"] if len(after) else entry  # default EOD
        for _, bar in after.iterrows():
            if side > 0:  # long fade
                if bar["low"] <= sl:
                    exit_px = sl; break
                if bar["high"] >= tp:
                    exit_px = tp; break
            else:  # short fade
                if bar["high"] >= sl:
                    exit_px = sl; break
                if bar["low"] <= tp:
                    exit_px = tp; break
        gross = side * (exit_px - entry)
        net = gross - cost
        trades.append((date, side, net / entry))
    return pd.DataFrame(trades, columns=["date", "side", "ret"])


def stats(tr):
    if len(tr) == 0:
        return dict(n=0, pf=np.nan, wr=np.nan, t=np.nan, tot=np.nan, mean_bps=np.nan)
    r = tr["ret"].values
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else np.nan
    return dict(n=len(r), pf=pf, wr=(r > 0).mean(), t=t,
                tot=r.sum() * 1e4, mean_bps=r.mean() * 1e4)


def split_eval(df, atr, **kw):
    tr = run_fade(df, atr, **kw)
    if len(tr) < 20:
        return None
    cut = tr["date"].quantile(0.7)
    is_, oos = tr[tr.date <= cut], tr[tr.date > cut]
    return stats(tr), stats(is_), stats(oos)


def main():
    df = load()
    atr = atr_daily(df)
    print(f"Bars {len(df):,}  ATR median={atr.median():.2f}\n")
    sessions = [("LONDON", 7, 9, 20), ("NY", 13, 15, 21)]
    print(f"{'sess':>7} {'k':>4} {'sl':>4} {'tp':>4} | "
          f"{'N':>4} {'PF':>5} {'WR':>5} {'t':>5} {'totbps':>7} || "
          f"{'oosN':>4} {'oosPF':>5} {'oosWR':>5} {'oost':>5}")
    for sname, oh, ah, eh in sessions:
        for k in (0.15, 0.20, 0.25, 0.30):
            for sl, tp in ((0.3, 0.3), (0.3, 0.5), (0.5, 0.3), (0.5, 0.5)):
                res = split_eval(df, atr, open_hour=oh, assess_hour=ah, eod_hour=eh,
                                 k=k, sl_atr=sl, tp_atr=tp)
                if res is None:
                    continue
                a, i, o = res
                print(f"{sname:>7} {k:>4} {sl:>4} {tp:>4} | "
                      f"{a['n']:>4} {a['pf']:>5.2f} {a['wr']*100:>4.0f}% "
                      f"{a['t']:>5.2f} {a['tot']:>7.0f} || "
                      f"{o['n']:>4} {o['pf']:>5.2f} {o['wr']*100:>4.0f}% {o['t']:>5.2f}")


if __name__ == "__main__":
    main()
