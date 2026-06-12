#!/usr/bin/env python3
"""
EURUSD-dedicated edge scan (research only, nothing wired).

Prior art:
  - generic scan (research_fx_majors --symbols EURUSD, 2026-06-12): all four
    candidates dead (london_breakout 0.91/0.69, ou_fade 1.05/0.98,
    donchian 0.77/0.88, asia_fade 0.80/0.63). EURUSD is the efficient major.
  - monday_drift rejected on EURUSD (PF 1.09); EURUSD/GBPUSD spread fade dead.
  - BUT the scan diagnostics flagged: 1h lag-24 autocorr +0.024 (t=+2.5,
    time-of-day seasonality) and positive-drift hours h02/h22 UTC — the
    classic Breedon–Ranaldo session asymmetry (a currency appreciates
    OUTSIDE its own trading hours; EUR rises during Asia, sags in Europe).

Candidates (causal: signal on completed bar, fill next open, round-trip
cost charged; overnight holds also charged swap):
  A asia_drift_hold    — long at 22:00/23:00/00:00 UTC, flat at the 07:00
                         open. Rollover-hour entries are charged DOUBLE
                         spread; the 23:00/00:00 variants are the
                         entry-delay test (memory: open-time edges on BID
                         data must survive a delayed entry).
  B europe_sag_short   — mirror leg: short the European session 07:00→15:00
                         (Breedon–Ranaldo says EUR depreciates in its own
                         hours). Control for A being mere anti-USD drift.
  C london_open_revert — if the Asia move 00:00→06:55 >= k*dailyATR, fade
                         it at the 07:00 open, stop 0.5*ATR, flat 12:00.
  D ny_spike_fade      — 15m bar with |ret| z>=3 during 12:00–19:45, fade
                         next open, stop beyond spike extreme, 2h time exit.
  E rsi2_daily_fade    — daily RSI(2)<10 long / >90 short, 1.5*ATR stop,
                         exit RSI cross 50 or 3 days. Wide-stop family
                         (the kind that survives strict fills).
Cross-pair drift check: any long-only winner is re-run on GBPUSD/AUDUSD/
USDJPY — if it prints everywhere it is the 2025–26 anti-USD drift, not a
EURUSD edge (memory: project_gbpusd_no_edge trap).

Split: IS = 2024-01-01..2025-09-30, OOS = 2025-10-01..end.
Gate: PF_net >= 1.3 IS AND same-direction OOS n >= 30; tight-stop setups
need ~1.5+ to survive the strict-fill gate.

VERDICT 2026-06-12: all five families REJECTED (best IS PF 0.89; the
eye-catching 22:00 hourly drift, t=+5.79, is a rollover bid-spread
artifact that does not monetize even before the 2x cost). The daily-
horizon follow-up (research_eurusd_daily.py + research_eurusd_streak.py)
also died. EURUSD has NO shippable pair-specific edge — do not re-scan
these families.

Usage:
    python scripts/research_eurusd.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_ROOT))

from research_fx_majors import COSTS, PIP, load, resample, stats
from research_gbpusd import _daily_atr, _exit_trade

SYMBOL = "EURUSD"
COST = COSTS[SYMBOL]
PIPSZ = PIP[SYMBOL]
IS_END = "2025-09-30 23:59:59+00:00"

# Retail swap, pips per night crossed (long EUR pays carry vs USD).
SWAP_LONG_P = -0.8
SWAP_SHORT_P = +0.2


def _swap(entry_ts: pd.Timestamp, exit_ts: pd.Timestamp, side: int) -> float:
    """Swap in pips for nights crossed (UTC date changes as proxy)."""
    nights = (exit_ts.normalize() - entry_ts.normalize()).days
    if nights <= 0:
        return 0.0
    return nights * (SWAP_LONG_P if side == 1 else SWAP_SHORT_P)


# ---------------------------------------------------------------- diagnostics

def hour_of_day_table(m5: pd.DataFrame) -> None:
    """IS-only mean hourly return in pips with t-stats."""
    h1 = resample(m5[m5.index <= IS_END], "1h")
    ret = (h1.close - h1.open) / PIPSZ
    print("    hour  avg_p     t      |  hour  avg_p     t")
    rows = []
    for h in range(24):
        r = ret[ret.index.hour == h]
        t = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 1 else 0
        rows.append((h, r.mean(), t))
    for i in range(12):
        a, b = rows[i], rows[i + 12]
        print(f"    h{a[0]:02d}  {a[1]:+6.2f}  {a[2]:+5.2f}   |  "
              f"h{b[0]:02d}  {b[1]:+6.2f}  {b[2]:+5.2f}")
    asia = ret[(ret.index.hour >= 22) | (ret.index.hour < 7)]
    eur = ret[(ret.index.hour >= 7) & (ret.index.hour < 15)]
    ny = ret[(ret.index.hour >= 15) & (ret.index.hour < 21)]
    for name, r in (("Asia 22-07", asia), ("Europe 07-15", eur), ("NY 15-21", ny)):
        t = r.mean() / r.std() * np.sqrt(len(r))
        # per-day magnitude = hourly mean * hours in block
        hrs = {"Asia 22-07": 9, "Europe 07-15": 8, "NY 15-21": 6}[name]
        print(f"    block {name:<13} avg {r.mean():+5.2f}p/h  t={t:+5.2f}  "
              f"~{r.mean() * hrs:+5.1f}p/day")


# ---------------------------------------------------------------- candidates

def session_hold(m5: pd.DataFrame, entry_hh: int, exit_hh: int, side: int,
                 double_cost_at_22: bool = True) -> pd.DataFrame:
    """Enter at the first 5m open at/after entry_hh UTC, exit at the first
    5m open at/after exit_hh (next day if exit_hh < entry_hh). No stop —
    pure seasonality hold; swap charged per night crossed."""
    rows = {}
    for day, g in m5.groupby(m5.index.date):
        ent = g[g.index.hour >= entry_hh]
        if ent.empty:
            continue
        e_ts = ent.index[0]
        entry = ent.iloc[0].open
        if exit_hh > entry_hh:
            ex = g[g.index.hour >= exit_hh]
        else:
            nxt = m5[m5.index > e_ts]
            nxt = nxt[nxt.index.date > e_ts.date()]
            ex = nxt[nxt.index.hour >= exit_hh]
            ex = ex.iloc[:1]
            # guard weekend: exit must be within 36h
            if len(ex) and (ex.index[0] - e_ts) > pd.Timedelta(hours=36):
                continue
        if ex.empty:
            continue
        x_ts = ex.index[0]
        exit_px = ex.iloc[0].open
        cost = COST * (2 if (double_cost_at_22 and e_ts.hour >= 22) else 1)
        pnl = ((exit_px - entry) * side - cost) / PIPSZ
        pnl += _swap(e_ts, x_ts, side)
        rows[e_ts] = (pnl, np.nan)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def london_open_revert(m5: pd.DataFrame, atr_d: pd.Series, k: float = 0.4,
                       stop_atr: float = 0.5, direction: int = -1) -> pd.DataFrame:
    """Fade (direction=-1) or follow (+1) the Asia move at the 07:00 open
    when |move 00:00->06:55| >= k*dailyATR. Flat at 12:00 UTC."""
    rows = {}
    for day, g in m5.groupby(m5.index.date):
        ts_day = pd.Timestamp(day, tz="UTC")
        atr = atr_d.get(ts_day, np.nan)
        if np.isnan(atr) or atr <= 0:
            continue
        asia = g.between_time("00:00", "06:55")
        eur = g.between_time("07:00", "11:55")
        if len(asia) < 60 or eur.empty:
            continue
        move = asia.iloc[-1].close - asia.iloc[0].open
        if abs(move) < k * atr:
            continue
        side = direction * int(np.sign(move))
        if side == 0:
            continue
        entry = eur.iloc[0].open
        stop = entry - side * stop_atr * atr
        pnl = _exit_trade(eur.iloc[1:], entry, side, stop, None)
        rows[eur.index[0]] = ((pnl - COST) / PIPSZ, stop_atr * atr / PIPSZ)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def ny_spike_fade(m15: pd.DataFrame, z_min: float = 3.0,
                  hold_bars: int = 8, buf_frac: float = 0.1) -> pd.DataFrame:
    """15m |return| z-score >= z_min vs rolling 96-bar std during
    12:00-19:45 UTC; fade at next open, stop beyond the spike extreme,
    time exit after hold_bars. One position at a time."""
    ret = m15.close.diff()
    sd = ret.rolling(96).std()
    z = ret / sd
    rows = {}
    last_exit = None
    times = m15.index
    for i in range(96, len(m15) - 1):
        ts = times[i]
        if not (12 <= ts.hour < 20):
            continue
        if last_exit is not None and ts < last_exit:
            continue
        zi = z.iloc[i]
        if np.isnan(zi) or abs(zi) < z_min:
            continue
        side = -int(np.sign(zi))
        bar = m15.iloc[i]
        entry = m15.iloc[i + 1].open
        rng = bar.high - bar.low
        stop = (bar.high + buf_frac * rng) if side == -1 else (bar.low - buf_frac * rng)
        window = m15.iloc[i + 1: i + 1 + hold_bars]
        pnl = _exit_trade(window, entry, side, stop, None)
        rows[ts] = ((pnl - COST) / PIPSZ, abs(entry - stop) / PIPSZ)
        last_exit = times[min(i + hold_bars, len(m15) - 1)]
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def rsi2_daily(m5: pd.DataFrame, lo: float = 10, hi: float = 90,
               stop_atr: float = 1.5, max_days: int = 3,
               long_only: bool = False) -> pd.DataFrame:
    """Daily RSI(2) fade: long below lo / short above hi at next day's first
    5m open; exit on RSI(2) crossing 50, ATR stop, or max_days. Swap charged."""
    d = resample(m5, "1D")
    delta = d.close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    atr_d = _daily_atr(m5)
    days = d.index
    rows = {}
    i = 16
    while i < len(days) - 1:
        ts = days[i]
        r = rsi.get(ts, np.nan)
        atr = atr_d.get(ts, np.nan)
        if np.isnan(r) or np.isnan(atr) or atr <= 0:
            i += 1
            continue
        side = 1 if r < lo else (-1 if (r > hi and not long_only) else 0)
        if side == 0:
            i += 1
            continue
        nxt = m5[m5.index > ts + pd.Timedelta(days=1) - pd.Timedelta(minutes=5)]
        nxt = nxt[nxt.index.normalize() > ts]
        if nxt.empty:
            break
        e_ts = nxt.index[0]
        entry = nxt.iloc[0].open
        stop = entry - side * stop_atr * atr
        # walk forward day by day
        end_ts = e_ts + pd.Timedelta(days=max_days)
        window = nxt[nxt.index <= end_ts]
        # RSI-cross-50 exit: find first daily close (after entry day) where
        # rsi crosses 50 in the profitable direction; truncate window there.
        for j, dts in enumerate(days[days > ts]):
            rj = rsi.get(dts, np.nan)
            if np.isnan(rj):
                continue
            if (side == 1 and rj >= 50) or (side == -1 and rj <= 50):
                cut = dts + pd.Timedelta(days=1)
                window = window[window.index <= cut]
                break
        if window.empty:
            i += 1
            continue
        pnl = _exit_trade(window, entry, side, stop, None)
        x_ts = window.index[-1]
        net = (pnl - COST) / PIPSZ + _swap(e_ts, x_ts, side)
        rows[e_ts] = (net, stop_atr * atr / PIPSZ)
        # skip forward past exit so positions don't overlap
        i = int(np.searchsorted(days.values, np.datetime64(x_ts.normalize())))
        i = max(i, 17)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


# ---------------------------------------------------------------- output

def show_df(df: pd.DataFrame, label: str) -> None:
    r = stats(df.pnl if len(df) else pd.Series(dtype=float), label)
    if r.get("n", 0) == 0:
        print(f"    {label:<26} n=0")
        return
    med = df.stop_p.median()
    medtxt = f"medstop={med:.0f}p" if not np.isnan(med) else "no-stop"
    print(f"    {r['label']:<26} n={r['n']:<4} PF={r['pf']:<5.2f} WR={r['wr']:5.1f}%  "
          f"avg={r['mean_pips']:+6.2f}p  t={r['t']:+5.2f}  total={r['sum_pips']:+7.0f}p  "
          f"{medtxt}")


def run(name: str, df: pd.DataFrame) -> None:
    print(f"  {name}:")
    if len(df) == 0:
        print("    n=0")
        return
    show_df(df[df.index <= IS_END], "IS  2024-01..2025-09")
    show_df(df[df.index > IS_END], "OOS 2025-10..end")


def main() -> int:
    m5 = load(SYMBOL)
    m15 = resample(m5, "15min")
    atr_d = _daily_atr(m5)
    print(f"{SYMBOL}: {m5.index[0].date()} → {m5.index[-1].date()}")
    print("  Hour-of-day diagnostics (IS only, pips):")
    hour_of_day_table(m5)

    print("\nA asia_drift_hold (long, swap charged, 2x cost at 22:00 entries):")
    for eh in (22, 23, 0):
        run(f"entry={eh:02d}:00 exit=07:00", session_hold(m5, eh, 7, side=1))

    print("\nB europe_sag_short (short 07:00→15:00, Breedon–Ranaldo mirror):")
    run("entry=07:00 exit=15:00", session_hold(m5, 7, 15, side=-1))

    print("\nC london_open_revert (fade Asia move at 07:00, flat 12:00):")
    for k in (0.25, 0.4, 0.6):
        run(f"k={k} stop=0.5atr", london_open_revert(m5, atr_d, k=k))
    run("CONTROL follow k=0.4", london_open_revert(m5, atr_d, k=0.4, direction=1))

    print("\nD ny_spike_fade (15m z>=z_min, 12:00-19:45):")
    for zm in (2.5, 3.0, 4.0):
        for hb in (4, 8):
            run(f"z={zm} hold={hb}bars", ny_spike_fade(m15, z_min=zm, hold_bars=hb))

    print("\nE rsi2_daily_fade (daily RSI(2), 1.5atr stop, swap charged):")
    for lo, hi in ((10, 90), (5, 95)):
        run(f"lo={lo} hi={hi}", rsi2_daily(m5, lo=lo, hi=hi))
    run("long-only lo=10", rsi2_daily(m5, lo=10, long_only=True))

    print("\nGate: PF_net >= 1.3 IS AND same-direction OOS n >= 30; tight stops "
          "need ~1.5+. Long-only winners must fail on GBPUSD/AUDUSD to count "
          "as EURUSD-specific (anti-USD drift trap).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
