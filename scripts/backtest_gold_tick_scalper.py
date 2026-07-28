#!/usr/bin/env python3
"""Tick-replay backtest for EA_GoldTickScalper.mq5.

Mirrors the EA's state machine tick for tick against real Dukascopy XAUUSD
bid/ask data, so the result is not a bar-model approximation:

  - M1 ATR + EMA anchor are built from BID bars (MT5 chart convention) and
    read from the last COMPLETED bar only -- no repaint, no lookahead.
  - Velocity uses mid price, exactly as the EA computes it from MqlTick.
  - BUY fills at ask and exits at bid; SELL fills at bid and exits at ask.
    Every trade pays the true observed spread.
  - Stops trigger on the observed tick, so a tick that gaps past the level
    fills at the gapped price -- adverse gaps are charged, not ignored.

Usage:
    python scripts/backtest_gold_tick_scalper.py --day 2026-07-17
    python scripts/backtest_gold_tick_scalper.py --day 2026-07-17 --profit-lock 0.30
    python scripts/backtest_gold_tick_scalper.py --days 2026-07-13:2026-07-17
"""
from __future__ import annotations

import argparse
import glob
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TICKS_DIR = "data/ticks/XAUUSD"
CONTRACT_SIZE = 100.0  # 1.00 lot XAUUSD = 100 oz -> $100 per $1 move


# --------------------------------------------------------------------------
# Parameters -- names map 1:1 onto the EA inputs
# --------------------------------------------------------------------------
@dataclass
class Params:
    vel_window_sec: int = 20        # InpVelWindowSec
    decel_window_sec: int = 5       # InpDecelWindowSec
    trig_atr_mult: float = 0.55     # InpTrigAtrMult
    stretch_atr_mult: float = 0.35  # InpStretchAtrMult
    anchor_period_m1: int = 20      # InpAnchorPeriodM1
    atr_period_m1: int = 14         # InpAtrPeriodM1

    tp_atr_mult: float = 0.50       # InpTpAtrMult
    sl_atr_mult: float = 0.90       # InpSlAtrMult
    tp_spread_floor: float = 1.80   # InpTpSpreadFloor
    profit_lock_usd: float = 0.0    # InpProfitLockUsd
    max_hold_sec: int = 180         # InpMaxHoldSec

    max_per_minute: int = 3         # InpMaxPerMinute
    cooldown_sec: int = 8           # InpCooldownSec

    max_spread: float = 0.90        # InpMaxSpread
    sess_start_utc: int = 7         # InpSessStartUtc
    sess_end_utc: int = 16          # InpSessEndUtc

    lots: float = 0.01              # InpLots
    max_daily_loss_usd: float = 50.0

    # True  = fade the burst (mean-reversion)
    # False = trade with the burst (continuation)
    fade: bool = True

    # True = evaluate ONLY at M1 bar closes (1-minute scalper)
    # False = evaluate on every tick (original HFT mode)
    bar_mode: bool = False
    bar_lookback: int = 1        # burst measured over N completed M1 bars

    # execution costs (not EA inputs -- broker reality)
    slippage: float = 0.02          # price units, adverse, per side
    commission_per_lot_side: float = 3.5  # USD per 1.0 lot per side


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_day(day: str, spread_override: float | None = None) -> pd.DataFrame:
    path = os.path.join(TICKS_DIR, f"{day}.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no tick file for {day} at {path}")
    df = pd.read_parquet(path)
    df = df.dropna(subset=["bid", "ask"]).sort_values("ts").reset_index(drop=True)
    df = df[df.ask > df.bid]  # drop crossed/degenerate quotes

    if spread_override is not None:
        # keep the real mid path, impose a synthetic cost -- answers
        # "what spread would this strategy actually need?"
        mid = (df["bid"] + df["ask"]) * 0.5
        df = df.assign(bid=mid - spread_override / 2.0,
                       ask=mid + spread_override / 2.0)
    return df


def build_m1_context(df: pd.DataFrame, p: Params):
    """M1 bid bars -> Wilder ATR + EMA anchor, aligned to the LAST COMPLETED bar.

    Returns (bar_pos_per_tick, atr_of_completed, anchor_of_completed) where the
    two arrays are already shifted so index i holds the value the EA would have
    read at tick i via CopyBuffer(handle, 0, 1, 1, ...).
    """
    ts = df["ts"].values
    minute = ts.astype("datetime64[m]")

    # position of each tick's minute within the ordered unique minutes
    uniq, bar_pos = np.unique(minute, return_inverse=True)

    bars = pd.DataFrame({"m": minute, "bid": df["bid"].values})
    ohlc = bars.groupby("m")["bid"].agg(["first", "max", "min", "last"])
    ohlc.columns = ["open", "high", "low", "close"]

    prev_close = ohlc["close"].shift()
    tr = pd.concat(
        [
            ohlc["high"] - ohlc["low"],
            (ohlc["high"] - prev_close).abs(),
            (ohlc["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing, matching MT5's iATR
    atr = tr.ewm(alpha=1.0 / p.atr_period_m1, adjust=False, min_periods=p.atr_period_m1).mean()
    anchor = ohlc["close"].ewm(span=p.anchor_period_m1, adjust=False,
                               min_periods=p.anchor_period_m1).mean()

    # shift(1) == "last completed bar"
    atr_c = atr.shift(1).to_numpy(dtype=float)
    anchor_c = anchor.shift(1).to_numpy(dtype=float)
    close_c = ohlc["close"].shift(1).to_numpy(dtype=float)
    open_c = ohlc["open"].shift(1).to_numpy(dtype=float)

    return bar_pos.astype(np.int64), atr_c, anchor_c, close_c, open_c


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
def simulate(df: pd.DataFrame, p: Params, day: str) -> pd.DataFrame:
    n = len(df)
    if n < 1000:
        return pd.DataFrame()

    ts_ns = df["ts"].values.astype("datetime64[ns]").astype(np.int64)
    ts_s = ts_ns // 1_000_000_000
    bid = df["bid"].to_numpy(dtype=float)
    ask = df["ask"].to_numpy(dtype=float)
    mid = (bid + ask) * 0.5
    spread = ask - bid
    hour = df["ts"].dt.hour.to_numpy()

    bar_pos, atr_c, anchor_c, close_c, open_c = build_m1_context(df, p)
    # first tick of each new M1 bar -- the only place a 1-minute scalper may act
    is_bar_open = np.zeros(n, bool)
    is_bar_open[0] = True
    is_bar_open[1:] = bar_pos[1:] != bar_pos[:-1]

    vel_ns = p.vel_window_sec * 1_000_000_000
    dec_ns = p.decel_window_sec * 1_000_000_000

    comm_side = p.commission_per_lot_side * p.lots
    usd_per_price = CONTRACT_SIZE * p.lots

    # two monotonic lookback pointers (the EA's MidAgo ring-buffer walk)
    i_full = 0
    i_dec = 0

    entry_stamps: list[int] = []
    last_entry = -10**9
    day_pnl = 0.0
    halted = False

    pos = None  # dict when open
    trades = []

    for i in range(n):
        t = ts_ns[i]

        while i_full + 1 < n and ts_ns[i_full + 1] <= t - vel_ns:
            i_full += 1
        while i_dec + 1 < n and ts_ns[i_dec + 1] <= t - dec_ns:
            i_dec += 1

        # ---------------- manage open position ----------------
        if pos is not None:
            exit_px = None
            reason = None

            if pos["dir"] == 1:
                # long: TP and SL both trigger on bid
                if bid[i] >= pos["tp"]:
                    exit_px, reason = bid[i] - p.slippage, "tp"
                elif bid[i] <= pos["sl"]:
                    exit_px, reason = bid[i] - p.slippage, "sl"
            else:
                if ask[i] <= pos["tp"]:
                    exit_px, reason = ask[i] + p.slippage, "tp"
                elif ask[i] >= pos["sl"]:
                    exit_px, reason = ask[i] + p.slippage, "sl"

            if exit_px is None and p.profit_lock_usd > 0.0:
                mark = (bid[i] - p.slippage) if pos["dir"] == 1 else (ask[i] + p.slippage)
                float_pl = (mark - pos["entry"]) * pos["dir"] * usd_per_price - 2 * comm_side
                if float_pl >= p.profit_lock_usd:
                    exit_px, reason = mark, "lock"

            if exit_px is None and p.max_hold_sec > 0 and (ts_s[i] - pos["t_s"]) >= p.max_hold_sec:
                exit_px = (bid[i] - p.slippage) if pos["dir"] == 1 else (ask[i] + p.slippage)
                reason = "time"

            if exit_px is not None:
                pnl = (exit_px - pos["entry"]) * pos["dir"] * usd_per_price - 2 * comm_side
                day_pnl += pnl
                trades.append(
                    {
                        "day": day,
                        "entry_time": pd.Timestamp(pos["t_ns"]),
                        "exit_time": pd.Timestamp(t),
                        "dir": "BUY" if pos["dir"] == 1 else "SELL",
                        "entry": pos["entry"],
                        "exit": exit_px,
                        "reason": reason,
                        "hold_s": int(ts_s[i] - pos["t_s"]),
                        "spread_at_entry": pos["spread"],
                        "pnl": pnl,
                    }
                )
                pos = None
                if p.max_daily_loss_usd > 0 and -day_pnl >= p.max_daily_loss_usd:
                    halted = True
            else:
                continue  # one position at a time

        if halted:
            continue

        # ---------------- filters ----------------
        if hour[i] < p.sess_start_utc or hour[i] >= p.sess_end_utc:
            continue
        if spread[i] > p.max_spread:
            continue
        if (ts_s[i] - last_entry) < p.cooldown_sec:
            continue

        while entry_stamps and (ts_s[i] - entry_stamps[0]) >= 60:
            entry_stamps.pop(0)
        if len(entry_stamps) >= p.max_per_minute:
            continue

        bp = bar_pos[i]
        atr = atr_c[bp]
        anchor = anchor_c[bp]
        if not np.isfinite(atr) or not np.isfinite(anchor) or atr <= 0:
            continue

        if p.bar_mode:
            # ---- 1-minute scalper: act only at a bar open, on completed bars ----
            if not is_bar_open[i]:
                continue
            k = bp - p.bar_lookback
            if k < 0 or not np.isfinite(close_c[k]):
                continue
            # burst = move across the last completed bar(s)
            vel_full = close_c[bp] - close_c[k]
            # At bar granularity the tick "stall" test has no analogue -- a bar
            # that fell vs the prior close AND closed green is near-empty once
            # the stretch gate is also applied (it produced 0 trades). Neutralise
            # it so bar mode is burst + stretch only.
            vel_recent = 0.0
            stretch = anchor - close_c[bp]
        else:
            # need the lookback windows to actually reach back far enough
            if ts_ns[i_full] > t - vel_ns or ts_ns[i_dec] > t - dec_ns:
                continue

            vel_full = mid[i] - mid[i_full]
            vel_recent = mid[i] - mid[i_dec]
            stretch = anchor - mid[i]

        trig = p.trig_atr_mult * atr
        stretch_n = p.stretch_atr_mult * atr

        if p.fade:
            # burst down into stretch, then stalls -> buy the snap-back
            go_long = (vel_full <= -trig) and (stretch >= stretch_n) and (vel_recent >= 0.0)
            go_short = (vel_full >= trig) and (stretch <= -stretch_n) and (vel_recent <= 0.0)
        else:
            # burst up and still running -> go with it
            go_long = (vel_full >= trig) and (stretch <= -stretch_n) and (vel_recent >= 0.0)
            go_short = (vel_full <= -trig) and (stretch >= stretch_n) and (vel_recent <= 0.0)

        if not go_long and not go_short:
            continue

        tp_dist = max(p.tp_atr_mult * atr, p.tp_spread_floor * spread[i])
        sl_dist = p.sl_atr_mult * atr

        if go_long:
            entry = ask[i] + p.slippage
            pos = {"dir": 1, "entry": entry, "tp": entry + tp_dist, "sl": entry - sl_dist}
        else:
            entry = bid[i] - p.slippage
            pos = {"dir": -1, "entry": entry, "tp": entry - tp_dist, "sl": entry + sl_dist}

        pos.update({"t_ns": t, "t_s": ts_s[i], "spread": spread[i]})
        entry_stamps.append(ts_s[i])
        last_entry = ts_s[i]

    return pd.DataFrame(trades)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def summarize(tr: pd.DataFrame, label: str) -> dict:
    if tr.empty:
        return {"label": label, "trades": 0, "net": 0.0, "pf": float("nan"),
                "win_rate": float("nan"), "avg": 0.0, "max_dd": 0.0}
    wins = tr.loc[tr.pnl > 0, "pnl"].sum()
    losses = -tr.loc[tr.pnl <= 0, "pnl"].sum()
    eq = tr.pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "label": label,
        "trades": len(tr),
        "net": tr.pnl.sum(),
        "pf": (wins / losses) if losses > 0 else float("inf"),
        "win_rate": 100.0 * (tr.pnl > 0).mean(),
        "avg": tr.pnl.mean(),
        "max_dd": dd,
        "gross_win": wins,
        "gross_loss": losses,
    }


def print_report(tr: pd.DataFrame, p: Params, title: str) -> None:
    s = summarize(tr, title)
    print(f"\n{'=' * 68}")
    print(f"  {title}")
    print(f"{'=' * 68}")
    if not s["trades"]:
        print("  no trades")
        return
    print(f"  Trades          : {s['trades']}")
    print(f"  Net P&L         : ${s['net']:+.2f}   (lot {p.lots}, ~${CONTRACT_SIZE * p.lots:.0f} per $1 move)")
    print(f"  Profit factor   : {s['pf']:.3f}")
    print(f"  Win rate        : {s['win_rate']:.1f}%")
    print(f"  Avg trade       : ${s['avg']:+.4f}")
    print(f"  Gross win/loss  : ${s['gross_win']:.2f} / ${s['gross_loss']:.2f}")
    print(f"  Max drawdown    : ${s['max_dd']:.2f}")
    print(f"  Avg hold        : {tr.hold_s.mean():.0f}s   (median {tr.hold_s.median():.0f}s)")
    print(f"  Avg entry spread: {tr.spread_at_entry.mean():.3f}")

    print("\n  By exit reason:")
    g = tr.groupby("reason").agg(n=("pnl", "size"), net=("pnl", "sum"), avg=("pnl", "mean"))
    for r, row in g.iterrows():
        print(f"    {r:<6} n={int(row.n):<5} net=${row.net:+9.2f}  avg=${row.avg:+.4f}")

    print("\n  By direction:")
    g = tr.groupby("dir").agg(n=("pnl", "size"), net=("pnl", "sum"), wr=("pnl", lambda x: 100 * (x > 0).mean()))
    for r, row in g.iterrows():
        print(f"    {r:<5} n={int(row.n):<5} net=${row.net:+9.2f}  win={row.wr:.1f}%")

    print("\n  By hour (UTC):")
    hh = tr.assign(h=tr.entry_time.dt.hour).groupby("h").agg(
        n=("pnl", "size"), net=("pnl", "sum"))
    for r, row in hh.iterrows():
        bar = "#" * min(30, int(abs(row.net) * 6))
        print(f"    {int(r):02d}h  n={int(row.n):<4} net=${row.net:+8.2f}  {bar}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", help="YYYY-MM-DD")
    ap.add_argument("--days", help="YYYY-MM-DD:YYYY-MM-DD inclusive range")
    ap.add_argument("--profit-lock", type=float, default=0.0)
    ap.add_argument("--tp-atr", type=float, default=None)
    ap.add_argument("--sl-atr", type=float, default=None)
    ap.add_argument("--max-spread", type=float, default=None)
    ap.add_argument("--sess", default=None, help="start:end UTC hours")
    ap.add_argument("--lots", type=float, default=None)
    ap.add_argument("--mode", choices=["fade", "continue"], default="fade")
    ap.add_argument("--bar-mode", action="store_true",
                    help="1-minute scalper: evaluate only at M1 bar closes")
    ap.add_argument("--bar-lookback", type=int, default=1)
    ap.add_argument("--no-daily-cap", action="store_true",
                    help="disable the daily loss halt (research only)")
    ap.add_argument("--spread-override", type=float, default=None,
                    help="impose a synthetic fixed spread on the real mid path")
    ap.add_argument("--csv", default=None, help="write trades here")
    args = ap.parse_args()

    p = Params()
    p.fade = (args.mode == "fade")
    p.bar_mode = args.bar_mode
    p.bar_lookback = args.bar_lookback
    if args.no_daily_cap:
        p.max_daily_loss_usd = 0.0
    if args.profit_lock:
        p.profit_lock_usd = args.profit_lock
    if args.tp_atr is not None:
        p.tp_atr_mult = args.tp_atr
    if args.sl_atr is not None:
        p.sl_atr_mult = args.sl_atr
    if args.max_spread is not None:
        p.max_spread = args.max_spread
    if args.lots is not None:
        p.lots = args.lots
    if args.sess:
        a, b = args.sess.split(":")
        p.sess_start_utc, p.sess_end_utc = int(a), int(b)

    if args.days:
        a, b = args.days.split(":")
        all_days = sorted(os.path.basename(f)[:-8] for f in glob.glob(f"{TICKS_DIR}/*.parquet"))
        days = [d for d in all_days if a <= d <= b]
    elif args.day:
        days = [args.day]
    else:
        ap.error("need --day or --days")

    frames = []
    rows = []
    for d in days:
        try:
            df = load_day(d, args.spread_override)
        except FileNotFoundError as e:
            print(f"  skip {d}: {e}")
            continue
        tr = simulate(df, p, d)
        frames.append(tr)
        rows.append(summarize(tr, d))

    allt = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if len(days) == 1:
        print_report(allt, p, f"XAUUSD tick scalper -- {days[0]}")
    else:
        print(f"\n{'=' * 68}")
        print(f"  XAUUSD tick scalper -- {days[0]} .. {days[-1]}")
        print(f"{'=' * 68}")
        print(f"  {'day':<12}{'n':>5}{'net$':>10}{'PF':>8}{'win%':>8}")
        for r in rows:
            pf = "n/a" if r["trades"] == 0 else f"{r['pf']:.2f}"
            print(f"  {r['label']:<12}{r['trades']:>5}{r['net']:>10.2f}{pf:>8}{r['win_rate']:>8.1f}")
        print_report(allt, p, "AGGREGATE")

    if args.csv and not allt.empty:
        allt.to_csv(args.csv, index=False)
        print(f"\n  trades -> {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
