"""
research_intraday_edge.py — Hunt for a measurable, EOD-flat intraday edge in XAUUSD 5m data.

Pure research. Writes nothing to the live system. Goal: find a time-of-day / session
structure with statistically real signal BEFORE we design a strategy around it.

We measure, in UTC:
  1. Mean 5m return by hour-of-day (is there a directional drift window?)
  2. Session-open range-expansion: does the first N min of London/NY open predict the
     direction of the rest of that session? (breakout edge, gated by liquidity time)
  3. Open->close session drift (does an early-session move continue or fade by EOD?)
  4. Volatility-of-hour profile (where the moves actually live — costs must clear here)
  5. Day-of-week drift.

Everything is reported with t-stats / hit-rates so we can tell edge from noise.
"""
import sys
import numpy as np
import pandas as pd

CSV = "data/historical/XAUUSD_5m_real.csv"
POINT = 0.01            # gold quoted to 2dp
TYPICAL_SPREAD_PTS = 20 # ~20 points = $0.20 round-trip-ish (M5 SPREAD col showed 14-25)


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    df = df.rename(columns=str.lower).sort_values("timestamp").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("ts")
    df["ret"] = df["close"].pct_change()
    df["logret"] = np.log(df["close"]).diff()
    df["hour"] = df.index.hour
    df["dow"] = df.index.dayofweek  # 0=Mon
    df["date"] = df.index.normalize()
    df = df[df["dow"] < 5]  # drop weekend stragglers
    return df


def tstat(x):
    x = x.dropna().values
    if len(x) < 30:
        return np.nan, np.nan, len(x)
    return x.mean(), x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), len(x)


def hour_of_day_drift(df):
    print("\n=== 1. Mean 5m log-return by UTC hour (bps) + t-stat ===")
    print(f"{'hr':>3} {'mean_bps':>9} {'t':>7} {'n':>7} {'ann_bps/day':>12}")
    rows = []
    for h in range(24):
        sub = df.loc[df.hour == h, "logret"]
        m, t, n = tstat(sub)
        # 12 five-min bars per hour -> per-hour drift in bps
        per_hour_bps = m * 12 * 1e4 if not np.isnan(m) else np.nan
        print(f"{h:>3} {m*1e4:>9.3f} {t:>7.2f} {n:>7} {per_hour_bps:>12.2f}")
        rows.append((h, m, t, n))
    return rows


def session_open_breakout(df, open_hour, label, range_min=30, hold_hours=4):
    """First `range_min` of the session sets a high/low. Test: does a break of that
    range in the rest of the session continue to a profitable EOD-ish exit?"""
    print(f"\n=== 2. {label} open breakout (open {open_hour:02d}:00 UTC, "
          f"{range_min}m range, hold {hold_hours}h) ===")
    bars_range = range_min // 5
    longs, shorts = [], []
    for date, day in df.groupby("date"):
        win = day[(day.hour >= open_hour) & (day.hour < open_hour + hold_hours)]
        if len(win) < bars_range + 6:
            continue
        opening = win.iloc[:bars_range]
        rest = win.iloc[bars_range:]
        hi, lo = opening["high"].max(), opening["low"].min()
        # first bar in `rest` that breaks the range
        broke_up = rest[rest["high"] > hi]
        broke_dn = rest[rest["low"] < lo]
        up_t = broke_up.index[0] if len(broke_up) else None
        dn_t = broke_dn.index[0] if len(broke_dn) else None
        if up_t is not None and (dn_t is None or up_t <= dn_t):
            entry = hi
            exit_px = win.iloc[-1]["close"]  # exit at end of hold window
            longs.append((exit_px - entry) / entry)
        elif dn_t is not None:
            entry = lo
            exit_px = win.iloc[-1]["close"]
            shorts.append((entry - exit_px) / entry)
    for name, r in [("LONG break", longs), ("SHORT break", shorts)]:
        r = np.array(r)
        if len(r) == 0:
            print(f"  {name}: no trades"); continue
        cost = 2 * TYPICAL_SPREAD_PTS * POINT / df['close'].mean()  # ~round trip frac
        net = r - cost
        wr = (net > 0).mean()
        m, t, n = tstat(pd.Series(net))
        print(f"  {name}: n={n} mean_net={m*1e4:>7.2f}bps t={t:>5.2f} "
              f"hit={wr*100:>5.1f}% gross={r.mean()*1e4:>7.2f}bps")


def session_drift_continuation(df, open_hour, label, early_hours=2, total_hours=8):
    """Does the first `early_hours` of a session predict the sign of the remaining
    move to session end? Momentum (continuation) vs reversion test."""
    print(f"\n=== 3. {label} early-move -> rest-of-session (open {open_hour:02d}, "
          f"early {early_hours}h, total {total_hours}h) ===")
    pairs = []
    for date, day in df.groupby("date"):
        win = day[(day.hour >= open_hour) & (day.hour < open_hour + total_hours)]
        if len(win) < 12:
            continue
        split = win[win.hour < open_hour + early_hours]
        rest = win[win.hour >= open_hour + early_hours]
        if len(split) < 3 or len(rest) < 3:
            continue
        early = (split.iloc[-1]["close"] - split.iloc[0]["open"]) / split.iloc[0]["open"]
        late = (rest.iloc[-1]["close"] - rest.iloc[0]["open"]) / rest.iloc[0]["open"]
        pairs.append((early, late))
    p = pd.DataFrame(pairs, columns=["early", "late"])
    if len(p) < 30:
        print("  insufficient"); return
    corr = p["early"].corr(p["late"])
    # conditional: when early move is in top/bottom tercile, what's late mean?
    p["sig"] = np.sign(p["early"])
    cont = (np.sign(p["late"]) == p["sig"]).mean()
    # momentum strategy: trade in direction of early move
    p["mom_ret"] = p["sig"] * p["late"]
    m, t, n = tstat(p["mom_ret"])
    print(f"  corr(early,late)={corr:>6.3f}  continuation_rate={cont*100:>5.1f}%  n={n}")
    print(f"  momentum(follow early): mean={m*1e4:>7.2f}bps t={t:>5.2f}  "
          f"(positive t -> continuation edge, negative -> reversion edge)")


def vol_by_hour(df):
    print("\n=== 4. Volatility (mean |5m ret| bps) by UTC hour ===")
    v = df.groupby("hour")["logret"].apply(lambda x: x.abs().mean() * 1e4)
    for h in range(24):
        bar = "#" * int(v.get(h, 0) * 3)
        print(f"{h:>3} {v.get(h,0):>6.2f} {bar}")


def dow_drift(df):
    print("\n=== 5. Day-of-week mean daily return (bps) ===")
    daily = df.groupby("date").agg(o=("open", "first"), c=("close", "last"),
                                   dow=("dow", "first"))
    daily["r"] = (daily["c"] - daily["o"]) / daily["o"]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    for d in range(5):
        m, t, n = tstat(daily.loc[daily.dow == d, "r"])
        print(f"  {names[d]}: mean={m*1e4:>7.2f}bps t={t:>5.2f} n={n}")


def main():
    df = load()
    print(f"Loaded {len(df):,} 5m bars  {df.index[0]} -> {df.index[-1]}")
    print(f"Approx round-trip cost assumed: {2*TYPICAL_SPREAD_PTS} pts "
          f"= {2*TYPICAL_SPREAD_PTS*POINT/df['close'].mean()*1e4:.2f} bps")
    hour_of_day_drift(df)
    vol_by_hour(df)
    # London open ~07:00 UTC, NY open ~13:00 UTC (13:30 cash but futures/spot 13:00)
    session_open_breakout(df, 7, "LONDON", range_min=30, hold_hours=5)
    session_open_breakout(df, 13, "NEW YORK", range_min=30, hold_hours=5)
    session_drift_continuation(df, 7, "LONDON", early_hours=2, total_hours=8)
    session_drift_continuation(df, 13, "NEW YORK", early_hours=2, total_hours=6)
    dow_drift(df)


if __name__ == "__main__":
    main()
