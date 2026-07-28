#!/usr/bin/env python3
"""No-SL / no-TP basket scalper on M1 XAUUSD: hold until the basket is green.

Rules under test:
  - open one 0.01-lot position at every M1 candle close
  - NO stop loss, NO take profit
  - the moment TOTAL floating P&L of all open positions >= threshold, close ALL
  - repeat

The interesting number here is NOT profit factor -- by construction almost every
CYCLE closes green. What decides whether this survives is the floating drawdown
carried while waiting, and the margin that drawdown demands. Both are tracked.

Basket P&L is maintained in O(1) per tick:
    pnl = upp * ((bid*n_long - sum_entry_long) + (sum_entry_short - ask*n_short))
          - 2*comm*n_total

Usage:
    python scripts/research_basket_close_m1.py
    python scripts/research_basket_close_m1.py --dir-rule long --threshold 1.0
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd

TICKS_DIR = "data/ticks/XAUUSD"
CONTRACT = 100.0
LOTS = 0.01
COMM = 3.5 * LOTS          # per side
SLIP = 0.02
UPP = CONTRACT * LOTS      # $ per 1.00 price move per position
MARGIN_PER_POS = 40.0      # 0.01 lot gold @ ~$4000, 1:100 leverage


def build_bars(df):
    minute = df["ts"].values.astype("datetime64[m]")
    uniq, first_idx = np.unique(minute, return_index=True)
    g = pd.DataFrame({"m": minute, "bid": df["bid"].values}).groupby("m")["bid"]
    bars = g.agg(["first", "max", "min", "last"])
    bars.columns = ["open", "high", "low", "close"]
    next_start = np.append(first_idx[1:], len(df))
    return bars.reset_index(drop=True), next_start


def run_day(df, dir_rule: str, threshold: float, max_pos: int):
    bars, next_start = build_bars(df)
    c = bars["close"].to_numpy()
    o = bars["open"].to_numpy()
    nb = len(c)

    ef = pd.Series(c).ewm(span=9, adjust=False, min_periods=9).mean().to_numpy()
    es = pd.Series(c).ewm(span=21, adjust=False, min_periods=21).mean().to_numpy()

    bid = df["bid"].to_numpy()
    ask = df["ask"].to_numpy()
    n = len(df)

    # open-position aggregates
    n_long = n_short = 0
    sum_el = sum_es_ = 0.0

    realized = 0.0
    max_float_dd = 0.0        # worst basket floating loss ($)
    max_positions = 0
    cycles = []
    cycle_open_tick = None
    opened_this_cycle = 0

    # tick index -> which bar just closed (positions open at bar boundaries)
    open_at = {}
    for b in range(nb - 1):
        if not (np.isfinite(ef[b]) and np.isfinite(es[b])):
            continue
        if dir_rule == "trend":
            d = 1 if ef[b] > es[b] else -1
        elif dir_rule == "long":
            d = 1
        elif dir_rule == "fade":
            d = -1 if c[b] > o[b] else 1
        else:
            d = 1 if c[b] > o[b] else -1
        j = int(next_start[b])
        if j < n:
            open_at[j] = d

    for i in range(n):
        d = open_at.get(i)
        if d is not None and (n_long + n_short) < max_pos:
            if d == 1:
                e = ask[i] + SLIP
                n_long += 1
                sum_el += e
            else:
                e = bid[i] - SLIP
                n_short += 1
                sum_es_ += e
            if cycle_open_tick is None:
                cycle_open_tick = i
                opened_this_cycle = 0
            opened_this_cycle += 1
            max_positions = max(max_positions, n_long + n_short)

        tot = n_long + n_short
        if tot == 0:
            continue

        mark_l = bid[i] - SLIP
        mark_s = ask[i] + SLIP
        pnl = UPP * ((mark_l * n_long - sum_el) + (sum_es_ - mark_s * n_short)) \
            - 2 * COMM * tot

        if pnl < -max_float_dd:
            max_float_dd = -pnl

        if pnl >= threshold:
            realized += pnl
            cycles.append(dict(pnl=pnl, n=tot, ticks=i - (cycle_open_tick or i),
                               opened=opened_this_cycle))
            n_long = n_short = 0
            sum_el = sum_es_ = 0.0
            cycle_open_tick = None
            opened_this_cycle = 0

    # whatever is still open at day end is marked to market, not wished away
    tot = n_long + n_short
    open_mtm = 0.0
    if tot:
        mark_l = bid[-1] - SLIP
        mark_s = ask[-1] + SLIP
        open_mtm = UPP * ((mark_l * n_long - sum_el) + (sum_es_ - mark_s * n_short)) \
            - 2 * COMM * tot

    return dict(realized=realized, open_mtm=open_mtm, left_open=tot,
                cycles=len(cycles), max_float_dd=max_float_dd,
                max_positions=max_positions,
                cyc=cycles)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir-rule", choices=["trend", "long", "fade", "momo"], default="trend")
    ap.add_argument("--threshold", type=float, default=0.01, help="$ basket profit to close all")
    ap.add_argument("--max-pos", type=int, default=100000, help="hard cap on open positions")
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--account", type=float, default=5000.0)
    a = ap.parse_args()

    days = sorted(os.path.basename(f)[:-8] for f in glob.glob(f"{TICKS_DIR}/*.parquet"))
    days = [d for d in days if d >= a.start]

    rows = []
    for d in days:
        df = pd.read_parquet(f"{TICKS_DIR}/{d}.parquet")
        df = df.dropna(subset=["bid", "ask"]).sort_values("ts").reset_index(drop=True)
        df = df[df.ask > df.bid].reset_index(drop=True)
        if len(df) < 50000:
            continue
        r = run_day(df, a.dir_rule, a.threshold, a.max_pos)
        r["day"] = d
        rows.append(r)

    t = pd.DataFrame(rows)
    tot_real = t.realized.sum()
    tot_open = t.open_mtm.sum()
    worst = t.max_float_dd.max()
    worst_day = t.loc[t.max_float_dd.idxmax(), "day"]
    peak_pos = t.max_positions.max()

    print(f"\n{'=' * 72}")
    print(f"  BASKET CLOSE-IN-PROFIT | dir={a.dir_rule} | close at >= ${a.threshold} | "
          f"no SL / no TP")
    print(f"  {len(t)} days ({t.day.iloc[0]}..{t.day.iloc[-1]})   0.01 lot/entry")
    print(f"{'=' * 72}")
    print(f"  Cycles closed green   : {int(t.cycles.sum())}")
    print(f"  Realized P&L          : ${tot_real:+.2f}")
    print(f"  Open positions marked : ${tot_open:+.2f}  ({int(t.left_open.sum())} left open at day ends)")
    print(f"  TRUE total            : ${tot_real + tot_open:+.2f}")
    print(f"  Days with green close : {int((t.cycles > 0).sum())}/{len(t)}")

    print(f"\n  --- what it costs to get that ---")
    print(f"  WORST floating drawdown : ${worst:,.2f}   (on {worst_day})")
    print(f"  Peak open positions     : {int(peak_pos)}   -> ${peak_pos * MARGIN_PER_POS:,.0f} margin")
    print(f"  Median daily max float  : ${t.max_float_dd.median():,.2f}")
    print(f"  Days floating > $250    : {int((t.max_float_dd > 250).sum())}/{len(t)}")
    print(f"  Days floating > $500    : {int((t.max_float_dd > 500).sum())}/{len(t)}")

    acct = a.account
    blown = t[t.max_float_dd >= acct]
    print(f"\n  --- ${acct:,.0f} account ---")
    print(f"  Days the floating loss alone exceeds the account : {len(blown)}/{len(t)}")
    if len(blown):
        print(f"  FIRST such day: {blown.day.iloc[0]}  (floating ${blown.max_float_dd.iloc[0]:,.2f})")
        print(f"  -> account is gone on day {list(t.day).index(blown.day.iloc[0]) + 1} of {len(t)}")
    margin_call = t[t.max_positions * MARGIN_PER_POS >= acct]
    if len(margin_call):
        print(f"  Days margin requirement alone exceeds the account: {len(margin_call)}/{len(t)}")

    print(f"\n  --- worst 8 days by floating drawdown ---")
    w = t.nlargest(8, "max_float_dd")[["day", "cycles", "realized", "max_float_dd",
                                       "max_positions", "left_open"]]
    print(w.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
