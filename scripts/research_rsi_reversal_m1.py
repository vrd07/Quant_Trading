#!/usr/bin/env python3
"""Cardwell RSI Positive/Negative Reversal on 1-minute XAUUSD.

Positive reversal (bullish continuation): in an uptrend, price makes a HIGHER
low while RSI makes a LOWER low. Negative reversal mirrors it.

Rules implemented exactly as specified:
  Trend filter : EMA9 strictly above/below EMA21
  Setup        : higher-low price + lower-low RSI (RSI >= rsi_floor)
                 lower-high price + higher-high RSI (RSI <= rsi_ceil)
  Trigger      : green candle breaking the prior candle's high (long) /
                 red candle breaking the prior candle's low (short)
  Stop         : recent 1m swing low/high, or the 21 EMA
  Target       : fixed 1:RR
  Time stop    : exit after N candles

No lookahead: pivots confirm k bars late and are only usable once confirmed;
indicators are read on completed bars; entry fills on the first tick AFTER the
trigger candle closes. Exits resolve on the real tick stream, so when both SL
and TP sit inside one candle the true ordering decides -- and gaps past the
stop fill at the gapped price.

Usage:
    python scripts/research_rsi_reversal_m1.py
    python scripts/research_rsi_reversal_m1.py --rr 1.5 --pivot-k 3 --sl-mode ema
"""
from __future__ import annotations

import argparse
import glob
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

TICKS_DIR = "data/ticks/XAUUSD"
CONTRACT_SIZE = 100.0


@dataclass
class P:
    rsi_period: int = 14
    ema_fast: int = 9
    ema_slow: int = 21
    pivot_k: int = 2            # bars each side for a confirmed swing
    rsi_floor: float = 40.0     # longs: RSI at the setup lows must stay above
    rsi_ceil: float = 60.0      # shorts: RSI at the setup highs must stay below
    trigger_window: int = 6     # bars after setup in which the trigger must fire
    rr: float = 2.0
    sl_mode: str = "swing"      # swing | ema
    sl_buffer: float = 0.10     # price units beyond the swing/EMA
    time_stop_bars: int = 5
    min_risk: float = 0.30      # skip if the stop is closer than this
    max_risk: float = 6.00      # skip absurdly wide stops
    require_pullback: bool = False   # price must have pulled toward EMA21
    pullback_atr: float = 1.0
    sess_start: int = 0
    sess_end: int = 24
    lots: float = 0.01
    slippage: float = 0.02
    commission_per_lot_side: float = 3.5
    max_spread: float = 0.90


def wilder_rsi(close: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(close)
    d = s.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rd = dn.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = ru / rd.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.to_numpy()


def find_pivots(arr: np.ndarray, k: int):
    """Strict fractal pivots. lo[i]/hi[i] are TRUE at bar i but only knowable at i+k."""
    n = len(arr)
    lo = np.zeros(n, bool)
    hi = np.zeros(n, bool)
    for i in range(k, n - k):
        left = arr[i - k:i]
        right = arr[i + 1:i + k + 1]
        v = arr[i]
        if v < left.min() and v <= right.min():
            lo[i] = True
        if v > left.max() and v >= right.max():
            hi[i] = True
    return lo, hi


def build_bars(df: pd.DataFrame):
    """M1 bid bars (MT5 chart convention) + the first tick index of each bar."""
    minute = df["ts"].values.astype("datetime64[m]")
    uniq, first_idx = np.unique(minute, return_index=True)
    g = pd.DataFrame({"m": minute, "bid": df["bid"].values}).groupby("m")["bid"]
    bars = g.agg(["first", "max", "min", "last"])
    bars.columns = ["open", "high", "low", "close"]
    bars = bars.reset_index(drop=True)
    # index of the first tick of the NEXT bar == where we can fill after close
    next_start = np.append(first_idx[1:], len(df))
    return bars, first_idx, next_start


def gen_signals(bars: pd.DataFrame, p: P):
    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    lo_ = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    n = len(c)

    rsi = wilder_rsi(c, p.rsi_period)
    ema_f = pd.Series(c).ewm(span=p.ema_fast, adjust=False, min_periods=p.ema_fast).mean().to_numpy()
    ema_s = pd.Series(c).ewm(span=p.ema_slow, adjust=False, min_periods=p.ema_slow).mean().to_numpy()
    atr = pd.Series(h - lo_).rolling(14).mean().to_numpy()

    piv_lo, _ = find_pivots(lo_, p.pivot_k)
    _, piv_hi = find_pivots(h, p.pivot_k)

    sigs = []
    for t in range(max(p.ema_slow, p.rsi_period) + 3 * p.pivot_k + 2, n - 1):
        if not (np.isfinite(ema_f[t]) and np.isfinite(ema_s[t]) and np.isfinite(rsi[t])):
            continue

        up_trend = ema_f[t] > ema_s[t]
        dn_trend = ema_f[t] < ema_s[t]
        if not (up_trend or dn_trend):
            continue

        # trigger candle must be complete at t
        green = c[t] > o[t] and h[t] > h[t - 1]
        red = c[t] < o[t] and lo_[t] < lo_[t - 1]

        # pivots confirmed on or before t
        cutoff = t - p.pivot_k

        if up_trend and green:
            idxs = np.where(piv_lo[:cutoff + 1])[0]
            if len(idxs) >= 2:
                p1, p2 = idxs[-2], idxs[-1]
                if (t - p2) <= p.trigger_window:
                    higher_low = lo_[p2] > lo_[p1]
                    lower_rsi = rsi[p2] < rsi[p1]
                    rsi_ok = rsi[p2] >= p.rsi_floor
                    pull_ok = True
                    if p.require_pullback and np.isfinite(atr[t]):
                        pull_ok = (lo_[p2] - ema_s[p2]) <= p.pullback_atr * atr[t]
                    if higher_low and lower_rsi and rsi_ok and pull_ok:
                        stop = (lo_[p2] if p.sl_mode == "swing" else ema_s[t]) - p.sl_buffer
                        sigs.append((t, 1, stop))

        if dn_trend and red:
            idxs = np.where(piv_hi[:cutoff + 1])[0]
            if len(idxs) >= 2:
                p1, p2 = idxs[-2], idxs[-1]
                if (t - p2) <= p.trigger_window:
                    lower_high = h[p2] < h[p1]
                    higher_rsi = rsi[p2] > rsi[p1]
                    rsi_ok = rsi[p2] <= p.rsi_ceil
                    pull_ok = True
                    if p.require_pullback and np.isfinite(atr[t]):
                        pull_ok = (ema_s[p2] - h[p2]) <= p.pullback_atr * atr[t]
                    if lower_high and higher_rsi and rsi_ok and pull_ok:
                        stop = (h[p2] if p.sl_mode == "swing" else ema_s[t]) + p.sl_buffer
                        sigs.append((t, -1, stop))
    return sigs


def simulate(df: pd.DataFrame, bars, first_idx, next_start, sigs, p: P, day: str):
    bid = df["bid"].to_numpy()
    ask = df["ask"].to_numpy()
    ts_s = df["ts"].values.astype("datetime64[ns]").astype(np.int64) // 1_000_000_000
    hour = df["ts"].dt.hour.to_numpy()
    n = len(df)

    comm = p.commission_per_lot_side * p.lots
    upp = CONTRACT_SIZE * p.lots

    trades = []
    busy_until = -1  # bar index; one position at a time

    for (t, dirn, stop) in sigs:
        if t <= busy_until:
            continue
        j = int(next_start[t])           # first tick after the trigger candle closed
        if j >= n:
            continue
        if hour[j] < p.sess_start or hour[j] >= p.sess_end:
            continue
        if (ask[j] - bid[j]) > p.max_spread:
            continue

        entry = ask[j] + p.slippage if dirn == 1 else bid[j] - p.slippage
        risk = (entry - stop) if dirn == 1 else (stop - entry)
        if not (p.min_risk <= risk <= p.max_risk):
            continue
        tp = entry + p.rr * risk if dirn == 1 else entry - p.rr * risk

        t_end = t + p.time_stop_bars
        end_tick = int(next_start[min(t_end, len(next_start) - 1)])
        end_tick = min(max(end_tick, j + 1), n)

        seg = slice(j, end_tick)
        if dirn == 1:
            px = bid[seg]
            hit = (px >= tp) | (px <= stop)
        else:
            px = ask[seg]
            hit = (px <= tp) | (px >= stop)

        if hit.any():
            w = int(np.argmax(hit))
            k = j + w
            reason = "tp" if ((dirn == 1 and bid[k] >= tp) or (dirn == -1 and ask[k] <= tp)) else "sl"
        else:
            k = end_tick - 1
            reason = "time"

        exit_px = bid[k] - p.slippage if dirn == 1 else ask[k] + p.slippage
        pnl = (exit_px - entry) * dirn * upp - 2 * comm

        trades.append(dict(day=day, bar=t, dir="BUY" if dirn == 1 else "SELL",
                           entry=entry, exit=exit_px, stop=stop, risk=risk,
                           R=(pnl / (risk * upp)) if risk > 0 else 0.0,
                           reason=reason, hold_s=int(ts_s[k] - ts_s[j]),
                           hour=int(hour[j]), pnl=pnl))
        busy_until = t_end
    return trades


def stats(tr: pd.DataFrame):
    if tr.empty:
        return dict(n=0, net=0.0, pf=float("nan"), wr=float("nan"), R=0.0)
    w = tr.loc[tr.pnl > 0, "pnl"].sum()
    l = -tr.loc[tr.pnl <= 0, "pnl"].sum()
    return dict(n=len(tr), net=tr.pnl.sum(), pf=(w / l if l > 0 else np.inf),
                wr=100 * (tr.pnl > 0).mean(), R=tr.R.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--pivot-k", type=int, default=2)
    ap.add_argument("--sl-mode", choices=["swing", "ema"], default="swing")
    ap.add_argument("--time-stop", type=int, default=5)
    ap.add_argument("--trigger-window", type=int, default=6)
    ap.add_argument("--require-pullback", action="store_true")
    ap.add_argument("--no-rsi-gate", action="store_true")
    ap.add_argument("--sess", default=None)
    ap.add_argument("--slip", type=float, default=None,
                    help="adverse slippage per side (cost robustness)")
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="9999-12-31")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()

    p = P(rr=a.rr, pivot_k=a.pivot_k, sl_mode=a.sl_mode,
          time_stop_bars=a.time_stop, trigger_window=a.trigger_window,
          require_pullback=a.require_pullback)
    if a.no_rsi_gate:
        p.rsi_floor, p.rsi_ceil = 0.0, 100.0
    if a.slip is not None:
        p.slippage = a.slip
    if a.sess:
        x, y = a.sess.split(":")
        p.sess_start, p.sess_end = int(x), int(y)

    days = sorted(os.path.basename(f)[:-8] for f in glob.glob(f"{TICKS_DIR}/*.parquet"))
    days = [d for d in days if a.start <= d <= a.end]

    all_tr = []
    per_day = []
    for d in days:
        df = pd.read_parquet(f"{TICKS_DIR}/{d}.parquet")
        df = df.dropna(subset=["bid", "ask"]).sort_values("ts").reset_index(drop=True)
        df = df[df.ask > df.bid].reset_index(drop=True)
        if len(df) < 50000:
            continue
        bars, first_idx, next_start = build_bars(df)
        sigs = gen_signals(bars, p)
        tr = simulate(df, bars, first_idx, next_start, sigs, p, d)
        all_tr.extend(tr)
        per_day.append((d, len(tr), sum(x["pnl"] for x in tr)))

    t = pd.DataFrame(all_tr)
    print(f"\n{'=' * 72}")
    print(f"  RSI Reversal M1 | RR 1:{p.rr} | pivot_k {p.pivot_k} | SL {p.sl_mode} | "
          f"time-stop {p.time_stop_bars}b")
    print(f"  {len(per_day)} days ({days[0]}..{days[-1]})")
    print(f"{'=' * 72}")
    if t.empty:
        print("  NO TRADES")
        return 0

    half = len(per_day) // 2
    is_days = {d for d, _, _ in per_day[:half]}
    s_all, s_is = stats(t), stats(t[t.day.isin(is_days)])
    s_oos = stats(t[~t.day.isin(is_days)])

    print(f"  Trades          : {s_all['n']}  ({s_all['n'] / max(1, len(per_day)):.1f}/day)")
    print(f"  Net P&L         : ${s_all['net']:+.2f}   @ {p.lots} lot")
    print(f"  Profit factor   : {s_all['pf']:.3f}")
    print(f"  Win rate        : {s_all['wr']:.1f}%")
    print(f"  Total R         : {s_all['R']:+.1f}R   (avg {t.R.mean():+.3f}R)")
    print(f"  Avg risk        : {t.risk.mean():.2f} pts   avg hold {t.hold_s.mean():.0f}s")
    print(f"\n  IS  ({half}d): n={s_is['n']:<5} PF={s_is['pf']:.3f}  net=${s_is['net']:+.2f}  wr={s_is['wr']:.1f}%")
    print(f"  OOS ({len(per_day) - half}d): n={s_oos['n']:<5} PF={s_oos['pf']:.3f}  net=${s_oos['net']:+.2f}  wr={s_oos['wr']:.1f}%")

    print("\n  By exit reason:")
    for r, g in t.groupby("reason"):
        print(f"    {r:<5} n={len(g):<5} net=${g.pnl.sum():+9.2f}  avgR={g.R.mean():+.3f}")
    print("\n  By direction:")
    for r, g in t.groupby("dir"):
        print(f"    {r:<5} n={len(g):<5} net=${g.pnl.sum():+9.2f}  wr={100 * (g.pnl > 0).mean():.1f}%")

    pos = sum(1 for _, _, v in per_day if v > 0)
    print(f"\n  Profitable days : {pos}/{len(per_day)}")

    if a.csv:
        t.to_csv(a.csv, index=False)
        print(f"  trades -> {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
