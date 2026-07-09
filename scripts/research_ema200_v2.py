#!/usr/bin/env python3
"""
EMA200 NASDAQ v2 redesign research (2026-07-08).

The shipped v1 rule (19:10-IST anchor break, chase entry, anchor-wick stop,
hold to SL/TP) has negative expectancy in every framing (prod PF 0.58; RR1:1
last-12mo −91R). Diagnostics localized the bleed to: (a) chase entry at the
most mean-reverting moment of the session (43% one-R win rate), (b) a ~36pt
median stop that gaps −1.45R average when it loses, (c) a fixed-IST anchor that
is 10min after the NY open in summer but ~50min BEFORE it in winter.

v2 variants tested here (grid):
  anchor  — the 5m candle starting 10min after the ACTUAL US cash open
            (DST-aware: 13:30 UTC in EDT, 14:30 UTC in EST), vs fixed 13:40.
  bias    — ema200 (v1, 5m EMA200) | onr (anchor close vs overnight range
            extremes) | dema20 (anchor close vs daily EMA20).
  entry   — chase (v1: next-bar-open after first close beyond anchor close)
            | pullback (after the trigger closes, LIMIT at the anchor close;
            cancel if unfilled by window end).
  stop    — anchor-extreme distance, floored at `atr_floor` × ATR14(5m).
  exit    — TP = rr × stop dist, SL, else FORCED FLAT ~20min before cash close.
  filter  — skip days where anchor range < `min_range_atr` × ATR.

One entry/day. Fills: cost/side, next-bar-open (chase) or limit-at-level
(pullback, fill requires the level to be traded through), SL-first intrabar.
Gate: PF >= ~1.2 EVERY year (2024/2025/2026) at cost 1.0/side, robust at 2.0.

Writes: reports/ema200_v2_research.md
"""

import sys
import itertools
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DATA_CSV = PROJECT_ROOT / "data/historical/NAS100_5m_real.csv"
REPORT = PROJECT_ROOT / "reports/ema200_v2_research.md"

COST = 1.0                 # per-side, index points
RISK_USD = 150.0           # $ risk per trade for PnL reporting (0.3% of $50k)


# ---------------------------------------------------------------------------
def us_cash_open_utc(d: date) -> int:
    """Minutes-from-midnight UTC of the 09:30 ET cash open (DST-aware)."""
    # EDT: second Sunday of March .. first Sunday of November
    mar = date(d.year, 3, 1)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7 + 7)   # 2nd Sun Mar
    nov = date(d.year, 11, 1)
    dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)         # 1st Sun Nov
    edt = dst_start <= d < dst_end
    return (13 * 60 + 30) if edt else (14 * 60 + 30)


def load():
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    flat = (df.high == df.low) & (df.volume == 0)
    df = df[~flat]
    df["ema200"] = df.close.ewm(span=200, adjust=False).mean()
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(),
                    (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    daily_close = df.close.resample("1D").last().dropna()
    dema = daily_close.ewm(span=20, adjust=False).mean().shift(1)  # yesterday's
    df["dema20"] = dema.reindex(df.index, method="ffill")
    return df


def run_variant(df, *, anchor_mode, bias, entry_mode, atr_floor, min_range_atr, rr):
    """One pass over all days; returns trades DataFrame with r (R-multiple)."""
    idx = df.index
    minutes = idx.hour * 60 + idx.minute
    o = df.open.to_numpy(float); h = df.high.to_numpy(float)
    l = df.low.to_numpy(float); c = df.close.to_numpy(float)
    ema = df.ema200.to_numpy(float); atr = df.atr.to_numpy(float)
    dema = df.dema20.to_numpy(float)
    dates = idx.date
    # group bar positions by day
    day_pos = {}
    for i, d in enumerate(dates):
        day_pos.setdefault(d, []).append(i)

    trades = []
    for d, pos in day_pos.items():
        open_min = us_cash_open_utc(d) if anchor_mode == "cash" else 13 * 60 + 30
        anchor_min = open_min + 10
        window_end = anchor_min + 120          # 2h entry window (as v1)
        flat_min = open_min + 370              # ~20min before cash close (390)
        # anchor bar
        a = next((i for i in pos if minutes[i] == anchor_min), None)
        if a is None or a < 600:               # EMA warmup
            continue
        arange = h[a] - l[a]
        if atr[a] <= 0 or np.isnan(atr[a]):
            continue
        if min_range_atr and arange < min_range_atr * atr[a]:
            continue
        # bias / direction
        if bias == "ema200":
            if np.isnan(ema[a]) or c[a] == ema[a]:
                continue
            side = 1 if c[a] > ema[a] else -1
        elif bias == "dema20":
            if np.isnan(dema[a]) or c[a] == dema[a]:
                continue
            side = 1 if c[a] > dema[a] else -1
        else:  # onr: overnight range from 16h before open to the open
            on = [i for i in range(max(0, a - 250), a)
                  if minutes[i] < open_min or dates[i] != d]
            on = [i for i in on if (a - i) * 5 <= 16 * 60]
            if not on:
                continue
            on_hi = max(h[j] for j in on); on_lo = min(l[j] for j in on)
            if c[a] > on_hi:
                side = 1
            elif c[a] < on_lo:
                side = -1
            else:
                continue                        # inside the overnight range
        level = c[a]                            # anchor close = trigger/limit level
        # trigger: first close beyond level in bias direction, in window
        t = None
        for i in pos:
            if i <= a or minutes[i] > window_end - 5:
                continue
            if (side == 1 and c[i] > level) or (side == -1 and c[i] < level):
                t = i
                break
        if t is None:
            continue
        # stop distance: anchor-extreme dist from LEVEL, floored by ATR
        raw_dist = (level - l[a]) if side == 1 else (h[a] - level)
        dist = max(raw_dist, atr_floor * atr[t]) if atr_floor else raw_dist
        if dist <= 0:
            continue
        # entry
        if entry_mode == "chase":
            e = t + 1
            if e >= len(df) or dates[e] != d:
                continue
            entry = o[e] + COST * side
        else:  # pullback limit at the level
            e = None
            for i in pos:
                if i <= t or minutes[i] > flat_min:
                    break
                if (side == 1 and l[i] < level) or (side == -1 and h[i] > level):
                    e = i
                    break
            if e is None:
                continue                        # never pulled back — no trade
            entry = level + COST * side * 0.5   # limit fill: half cost (no cross)
        stop = entry - dist * side
        tp = entry + rr * dist * side
        # walk to exit
        exit_px = None; reason = None
        for i in range(e, pos[-1] + 1):
            if dates[i] != d:
                break
            first_bar = (i == e)
            if not first_bar:                   # gap at open
                if side == 1 and o[i] <= stop:
                    exit_px, reason = o[i] - COST, "sl"
                elif side == -1 and o[i] >= stop:
                    exit_px, reason = o[i] + COST, "sl"
                elif side == 1 and o[i] >= tp:
                    exit_px, reason = tp, "tp"
                elif side == -1 and o[i] <= tp:
                    exit_px, reason = tp, "tp"
            if exit_px is None:                 # intrabar, SL-first
                if side == 1 and l[i] <= stop:
                    exit_px, reason = stop - COST, "sl"
                elif side == -1 and h[i] >= stop:
                    exit_px, reason = stop + COST, "sl"
                elif side == 1 and h[i] >= tp:
                    exit_px, reason = tp, "tp"
                elif side == -1 and l[i] <= tp:
                    exit_px, reason = tp, "tp"
            if exit_px is None and minutes[i] >= flat_min:
                exit_px, reason = c[i] - COST * side, "eod"
            if exit_px is not None:
                break
        if exit_px is None:                     # day ended without flat bar
            last = pos[-1]
            exit_px, reason = c[last] - COST * side, "eod"
        r = (exit_px - entry) * side / dist
        trades.append(dict(day=d, side=side, r=r, reason=reason,
                           month=f"{d.year}-{d.month:02d}", year=d.year))
    return pd.DataFrame(trades)


def year_pf(t):
    out = {}
    for y in (2024, 2025, 2026):
        g = t[t.year == y]
        gw = g.r[g.r > 0].sum(); gl = -g.r[g.r < 0].sum()
        out[y] = (gw / gl) if gl > 0 else (float("inf") if len(g) else 0.0)
    return out


def main():
    df = load()
    rows = []
    grid = list(itertools.product(
        ["cash", "fixed"], ["ema200", "onr", "dema20"], ["pullback", "chase"],
        [1.0, 0.0], [0.3, 0.0], [2.0, 1.5]))
    print(f"{len(grid)} variants…")
    for am, bias, em, af, mr, rr in grid:
        t = run_variant(df, anchor_mode=am, bias=bias, entry_mode=em,
                        atr_floor=af, min_range_atr=mr, rr=rr)
        if len(t) < 60:
            continue
        gw = t.r[t.r > 0].sum(); gl = -t.r[t.r < 0].sum()
        pf = gw / gl if gl > 0 else 0.0
        ypf = year_pf(t)
        rows.append(dict(anchor=am, bias=bias, entry=em, atrfl=af, minrg=mr,
                         rr=rr, n=len(t), wr=100 * (t.r > 0).mean(),
                         pf=pf, pf24=ypf[2024], pf25=ypf[2025], pf26=ypf[2026],
                         netR=t.r.sum(), usd=t.r.sum() * RISK_USD))
    res = pd.DataFrame(rows)
    res["minpf"] = res[["pf24", "pf25", "pf26"]].min(axis=1)
    res = res.sort_values("minpf", ascending=False)
    with pd.option_context("display.width", 200):
        print(res.head(20).to_string(index=False,
              float_format=lambda x: f"{x:.2f}"))

    lines = ["# EMA200 NASDAQ v2 research", "",
             f"Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. Grid of "
             f"{len(grid)} variants, {len(res)} with >=60 trades. Cost "
             f"{COST}/side; R reported; $ at ${RISK_USD:.0f} risk/trade.",
             "", "Gate: PF >= 1.2 EVERY year (2024/2025/2026).", "",
             "| anchor | bias | entry | ATRfloor | minRange | RR | n | WR | "
             "PF | 2024 | 2025 | 2026 | netR | $ |", "|" + "---|" * 14]
    for _, r in res.iterrows():
        lines.append(f"| {r.anchor} | {r.bias} | {r.entry} | {r.atrfl} | "
                     f"{r.minrg} | {r.rr} | {int(r.n)} | {r.wr:.0f}% | "
                     f"{r.pf:.2f} | {r.pf24:.2f} | {r.pf25:.2f} | {r.pf26:.2f} | "
                     f"{r.netR:+.1f} | ${r.usd:+,.0f} |")
    passing = res[res.minpf >= 1.2]
    lines += ["", f"**Variants clearing the gate: {len(passing)}**"]
    REPORT.write_text("\n".join(lines))
    print(f"\npassing gate (min-year PF >= 1.2): {len(passing)}")
    print(f"report -> {REPORT}")


if __name__ == "__main__":
    main()
