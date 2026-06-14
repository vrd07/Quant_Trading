#!/usr/bin/env python3
"""
Crypto strategy hunt — BTCUSD (build) / ETHUSD (cross-asset OOS).

Same discipline as the FX-majors research (research_fx_majors.py,
research_monday_drift.py): real cost model, IS/OOS split, judge plateaus not
winners, and the strict-fill standard — a flat-cost edge must clear PF ~1.3
to have any chance of surviving adverse stop fills live (the VWAP-fade lesson,
project_intraday_edge_research).

Units: crypto spans $45k→$73k BTC, so absolute dollar PnL is meaningless.
Everything here is in PERCENT returns (per-trade %). Cost is charged in % too.

Cost model (realistic retail crypto, conservative):
    BTC round-trip 0.10% (≈10 bps: spread + 2x slippage), ETH 0.12%.
    Dukascopy/CFD crypto spreads are wider in bps terms than FX — do not
    use an FX-tight cost here or the edge will be a fantasy.

Families tested (chosen for crypto's character — 24/7, trends hard, high vol):
    1. Donchian / time-series-momentum breakout (daily) — the documented edge.
    2. Volatility breakout (prior-day range, intraday).
    3. Day-of-week / weekend seasonality (with entry-delay artifact control).
    4. Intraday mean-reversion fade (control — expected to die on fills).

Split: IS 2024-01..2025-09, OOS 2025-10..end. ETH is held out entirely as a
cross-asset confirmation — an edge that only exists on BTC is a curve fit.

Usage:
    python scripts/research_crypto.py
    python scripts/research_crypto.py --symbol ETHUSD
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_ROOT))

from research_fx_majors import resample  # reuse left/left resampler

IS_END = pd.Timestamp("2025-09-30 23:59:59+00:00")

# Round-trip cost as a fraction of price (spread + 2x slippage), retail crypto.
COST = {"BTCUSD": 0.0010, "ETHUSD": 0.0012}


# ----------------------------------------------------------------- data load

def load(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(PROJECT_ROOT / f"data/historical/{symbol}_5m_real.csv",
                     parse_dates=["timestamp"], index_col="timestamp")
    # Drop flat padding bars (MT5-capture zero-volume artifacts) so they don't
    # masquerade as real range. Keep real zero-volume only if OHLC moved.
    flat = (df.open == df.close) & (df.high == df.low)
    df = df[~flat]
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


# ----------------------------------------------------------------- stats

def stats(rets: pd.Series, label: str) -> dict:
    """rets = net per-trade returns in PERCENT (already cost-adjusted)."""
    n = len(rets)
    if n == 0:
        return {"label": label, "n": 0}
    wins, losses = rets[rets > 0], rets[rets < 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf")
    t = float(rets.mean() / rets.std() * np.sqrt(n)) if n > 1 and rets.std() > 0 else 0.0
    # crude equity / maxDD on the cumulative-sum-of-% path (good enough to flag blowups)
    eq = rets.cumsum()
    dd = float((eq - eq.cummax()).min())
    # stored rets are fractions; display everywhere in PERCENT (x100).
    return {"label": label, "n": n, "pf": pf, "wr": 100 * len(wins) / n,
            "mean": 100 * float(rets.mean()), "t": t,
            "sum": 100 * float(eq.iloc[-1]), "dd": 100 * dd}


def show(rows: list) -> None:
    for r in rows:
        if r.get("n", 0) == 0:
            print(f"    {r['label']:<30} n=0")
            continue
        print(f"    {r['label']:<30} n={r['n']:<4} PF={r['pf']:<5.2f} "
              f"WR={r['wr']:4.1f}%  avg={r['mean']:+5.2f}%  t={r['t']:+5.2f}  "
              f"sumR={r['sum']:+7.1f}%  maxDD={r['dd']:+6.1f}%")


def split(rets: pd.Series):
    return rets[rets.index <= IS_END], rets[rets.index > IS_END]


def report(rets: pd.Series, name: str) -> None:
    is_r, oos_r = split(rets)
    print(f"  {name}")
    show([stats(is_r, "IS  2024-01..2025-09"),
          stats(oos_r, "OOS 2025-10..end"),
          stats(rets, "ALL")])


# ================================================================= FAMILIES

def donchian_daily(df: pd.DataFrame, symbol: str, n_enter: int, n_exit: int,
                   long_only: bool) -> pd.Series:
    """Turtle-style channel breakout on DAILY bars. Enter at next-day open when
    close breaks the N-day high (long) / low (short). Exit when close crosses
    the n_exit-day opposite channel. One position; flip-or-flat. Returns the
    per-trade NET % return indexed by entry time."""
    d = resample(df, "1D")
    if len(d) < n_enter + 5:
        return pd.Series(dtype=float)
    hi = d.high.rolling(n_enter).max().shift(1)
    lo = d.low.rolling(n_enter).min().shift(1)
    ex_hi = d.high.rolling(n_exit).max().shift(1)
    ex_lo = d.low.rolling(n_exit).min().shift(1)
    cost = COST[symbol]
    pos = 0
    entry_px = entry_t = None
    trades = {}
    op = d.open.values
    for i in range(n_enter, len(d)):
        c = d.close.iloc[i]
        t = d.index[i]
        nxt = op[i + 1] if i + 1 < len(d) else d.close.iloc[i]
        # exit logic
        if pos == 1 and c < ex_lo.iloc[i]:
            trades[entry_t] = (nxt / entry_px - 1) - cost
            pos = 0
        elif pos == -1 and c > ex_hi.iloc[i]:
            trades[entry_t] = (entry_px / nxt - 1) - cost
            pos = 0
        # entry logic (allow same-bar flip after exit)
        if pos == 0:
            if c > hi.iloc[i]:
                pos, entry_px, entry_t = 1, nxt, t
            elif (not long_only) and c < lo.iloc[i]:
                pos, entry_px, entry_t = -1, nxt, t
    return pd.Series(trades).sort_index()


def ts_momentum_daily(df: pd.DataFrame, symbol: str, lookback: int, hold: int,
                      long_only: bool) -> pd.Series:
    """Time-series momentum: every `hold` days, go long if the trailing
    `lookback`-day return > 0 (short if < 0). Non-overlapping holds."""
    d = resample(df, "1D")
    cost = COST[symbol]
    trades = {}
    i = lookback
    while i + hold < len(d):
        past = d.close.iloc[i] / d.close.iloc[i - lookback] - 1
        side = 1 if past > 0 else (-1 if not long_only else 0)
        if side != 0:
            fwd = d.close.iloc[i + hold] / d.close.iloc[i] - 1
            trades[d.index[i]] = side * fwd - cost
        i += hold
    return pd.Series(trades).sort_index()


def prior_day_breakout(df: pd.DataFrame, symbol: str, k: float,
                       long_only: bool) -> pd.Series:
    """Volatility breakout: each UTC day, breakout level = prior-day close
    ± k * prior-day range. Enter intraday on first 1h close beyond level,
    exit at day end (23:55). One trade/day/side."""
    h1 = resample(df, "1h")
    d = resample(df, "1D")
    d_range = (d.high - d.low)
    cost = COST[symbol]
    trades = {}
    by_day = {day: g for day, g in h1.groupby(h1.index.date)}
    days = sorted(by_day)
    for j in range(1, len(days)):
        day = days[j]
        prev = days[j - 1]
        if prev not in d.index.date or pd.Timestamp(prev) not in d.index:
            pass
        try:
            pc = d.loc[d.index.date == prev, "close"].iloc[0]
            pr = d_range[d.index.date == prev].iloc[0]
        except (IndexError, KeyError):
            continue
        if pr <= 0:
            continue
        up = pc + k * pr
        dn = pc - k * pr
        g = by_day[day]
        for ts, bar in g.iterrows():
            side = 1 if bar.close > up else (-1 if (not long_only) and bar.close < dn else 0)
            if side == 0:
                continue
            exit_px = g.iloc[-1].close
            entry = bar.close
            ret = side * (exit_px / entry - 1) - cost
            trades[ts] = ret
            break  # one trade per day
    return pd.Series(trades).sort_index()


def day_of_week(df: pd.DataFrame, symbol: str, delay_h: int) -> dict:
    """Mean DAILY close-to-close return by weekday, entered `delay_h` hours
    after the UTC day open (artifact control — a real seasonal edge survives a
    1-2h entry delay; a spread/open artifact decays to zero)."""
    d = resample(df, "1D")
    cost = COST[symbol]
    if delay_h == 0:
        rets = d.close.pct_change()
        idx = d.index
    else:
        h1 = resample(df, "1h")
        # entry = price `delay_h` into the day, exit = same time next day
        anchor = h1[h1.index.hour == delay_h].copy()
        rets = anchor.close.pct_change()
        idx = anchor.index
    out = {}
    for wd, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
        mask = pd.Series(idx).dt.dayofweek.values == wd
        r = pd.Series(rets.values[mask] - cost, index=idx[mask]).dropna()
        out[name] = r
    return out


def intraday_fade(df: pd.DataFrame, symbol: str, z: float, hold_h: int) -> pd.Series:
    """CONTROL: fade extreme 1h moves. When an hourly bar moves > z * rolling
    hourly-stddev, take the opposite for hold_h hours. Expected to die on costs
    (this is the family that failed strict fills on gold/FX)."""
    h1 = resample(df, "1h")
    ret1 = h1.close.pct_change()
    vol = ret1.rolling(168).std()  # 1-week rolling hourly vol
    cost = COST[symbol]
    trades = {}
    vals = h1.close.values
    for i in range(168, len(h1) - hold_h):
        if vol.iloc[i] <= 0 or np.isnan(vol.iloc[i]):
            continue
        move = ret1.iloc[i]
        if abs(move) < z * vol.iloc[i]:
            continue
        side = -1 if move > 0 else 1   # fade
        fwd = vals[i + hold_h] / vals[i] - 1
        trades[h1.index[i]] = side * fwd - cost
    return pd.Series(trades).sort_index()


# ================================================================= MAIN

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSD")
    args = ap.parse_args()
    sym = args.symbol
    df = load(sym)
    print(f"\n{'='*78}\n{sym}  bars={len(df):,}  "
          f"{df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}  "
          f"cost={COST[sym]*100:.2f}%/round-trip\n{'='*78}")

    # Mandatory control (physics-strategy lesson): buy-and-hold is the bar any
    # long-biased "edge" must beat, and demeaning shows what's left after drift.
    d = resample(df, "1D")
    is_d = d[d.index <= IS_END]
    oos_d = d[d.index > IS_END]
    def _bh(seg, lbl):
        if len(seg) < 2:
            print(f"    {lbl:<22} n/a"); return
        r = seg.close.iloc[-1] / seg.close.iloc[0] - 1
        print(f"    {lbl:<22} buy&hold={r*100:+7.1f}%   days={len(seg)}")
    print("\n### 0. Buy & hold benchmark (the bar to beat) -----------------")
    _bh(is_d, "IS  2024-01..2025-09")
    _bh(oos_d, "OOS 2025-10..end")
    _bh(d, "ALL")

    print("\n### 1. Donchian channel breakout (daily) ----------------------")
    for ne, nx in [(20, 10), (55, 20), (20, 5), (10, 5)]:
        for lo in (True, False):
            tag = "long-only" if lo else "long/short"
            r = donchian_daily(df, sym, ne, nx, lo)
            report(r, f"Donchian enter={ne} exit={nx} [{tag}]")

    print("\n### 2. Time-series momentum (daily) ---------------------------")
    for lb, hd in [(7, 7), (14, 7), (30, 14), (30, 30), (90, 30)]:
        for lo in (True, False):
            tag = "long-only" if lo else "long/short"
            r = ts_momentum_daily(df, sym, lb, hd, lo)
            report(r, f"TSMOM lookback={lb} hold={hd} [{tag}]")

    print("\n### 3. Prior-day volatility breakout (intraday) ---------------")
    for k in (0.5, 1.0):
        for lo in (True, False):
            tag = "long-only" if lo else "long/short"
            r = prior_day_breakout(df, sym, k, lo)
            report(r, f"PrevDayBreak k={k} [{tag}]")

    print("\n### 4. Day-of-week seasonality (artifact control) -------------")
    for delay in (0, 2):
        dow = day_of_week(df, sym, delay)
        print(f"  entry delay = {delay}h")
        rows = []
        for name, r in dow.items():
            rows.append(stats(r, name))
        show(rows)

    print("\n### 5. Intraday fade (CONTROL — expect death) -----------------")
    for z in (2.0, 3.0):
        r = intraday_fade(df, sym, z, 6)
        report(r, f"Fade z>{z} hold=6h")

    print()


if __name__ == "__main__":
    main()
