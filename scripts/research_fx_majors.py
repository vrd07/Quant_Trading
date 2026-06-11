#!/usr/bin/env python3
"""
FX-majors edge scan — GBPUSD / AUDUSD / USDJPY (research only, nothing wired).

Goal: find a per-pair edge worth promoting to a real strategy, the way
kalman_regime earns its keep on XAUUSD. Per memory lessons:
  - flat-cost research PF must clear ~1.3 to survive strict fills
    (project_intraday_edge_research), so costs are charged here up front;
  - session/structure effects beat generic indicators on single assets
    (project_physics_strategy_research, project_breakout_15m_research).

Candidates per pair (all causal: signal on completed bar, fill next open,
round-trip cost charged):
  A london_breakout  — Asia-range (00–07 UTC) breakout during London open,
                       half-range stop, flat by 15:00 UTC. One trade/day.
  B ou_fade_1h       — z-score of 1h close vs rolling mean; fade |z|>=2,
                       exit at z~0 / time stop / 3-sigma hard stop.
  C donchian_1h      — 20-bar channel breakout, exit on 10-bar opposite
                       channel (trend control — expected dead on majors).
  D asia_fade_15m    — fade 2-sigma Bollinger pokes with RSI extreme during
                       Asia hours (works on gold; JPY pairs plausible).
Cross-pair:
  E spread_ou_1h     — EURUSD vs GBPUSD rolling-beta log-spread z fade
                       (~synthetic EURGBP stat-arb feasibility check).

Split: IS = 2024-01-01..2025-09-30, OOS = 2025-10-01..end. A candidate is
interesting only if PF_net >= 1.3 IS *and* holds direction OOS with N >= 30.

Usage:
    python scripts/research_fx_majors.py [--symbols GBPUSD AUDUSD USDJPY]
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

IS_END = "2025-09-30 23:59:59+00:00"

# Round-trip cost in price units (spread + 2x slippage, retail-typical).
COSTS = {
    "GBPUSD": 0.00015,
    "AUDUSD": 0.00013,
    "USDJPY": 0.017,
    "EURUSD": 0.00013,
}
PIP = {"GBPUSD": 0.0001, "AUDUSD": 0.0001, "USDJPY": 0.01, "EURUSD": 0.0001}


def load(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(PROJECT_ROOT / f"data/historical/{symbol}_5m_real.csv",
                     parse_dates=["timestamp"], index_col="timestamp")
    # Defensive: drop any flat padding bars that predate the fetcher scrub.
    flat = (df.open == df.close) & (df.high == df.low) & (df.volume == 0)
    return df[~flat]


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    out = df.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "high", "low", "close"])
    return out


def stats(pnl_pips: pd.Series, label: str) -> dict:
    """PF / WR / t-stat on a series of net per-trade pips, indexed by entry time."""
    n = len(pnl_pips)
    if n == 0:
        return {"label": label, "n": 0}
    wins = pnl_pips[pnl_pips > 0]
    losses = pnl_pips[pnl_pips < 0]
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf")
    t = float(pnl_pips.mean() / pnl_pips.std() * np.sqrt(n)) if n > 1 and pnl_pips.std() > 0 else 0.0
    return {"label": label, "n": n, "pf": pf, "wr": 100 * len(wins) / n,
            "mean_pips": float(pnl_pips.mean()), "t": t, "sum_pips": float(pnl_pips.sum())}


def show(rows: list) -> None:
    for r in rows:
        if r.get("n", 0) == 0:
            print(f"    {r['label']:<26} n=0")
            continue
        print(f"    {r['label']:<26} n={r['n']:<5} PF={r['pf']:<5.2f} WR={r['wr']:5.1f}%  "
              f"avg={r['mean_pips']:+6.2f}p  t={r['t']:+5.2f}  total={r['sum_pips']:+8.0f}p")


def split_is_oos(trades: pd.Series):
    return trades[trades.index <= IS_END], trades[trades.index > IS_END]


# ---------------------------------------------------------------- candidates

def london_breakout(m15: pd.DataFrame, symbol: str) -> pd.Series:
    """Asia range 00:00-06:59 UTC; first 15m close beyond it in 07:00-09:45
    enters with the break; stop = 0.5x range behind entry; flat at 15:00."""
    cost = COSTS[symbol]
    pip = PIP[symbol]
    out = {}
    for day, g in m15.groupby(m15.index.date):
        asia = g.between_time("00:00", "06:59")
        if len(asia) < 12:
            continue
        hi, lo = asia.high.max(), asia.low.min()
        rng = hi - lo
        if rng <= 0:
            continue
        window = g.between_time("07:00", "09:45")
        exit_w = g.between_time("10:00", "15:00")
        if window.empty or exit_w.empty:
            continue
        for i, (ts, bar) in enumerate(window.iterrows()):
            side = 1 if bar.close > hi else (-1 if bar.close < lo else 0)
            if side == 0:
                continue
            later = pd.concat([window.iloc[i + 1:], exit_w])
            if later.empty:
                break
            entry = later.iloc[0].open
            stop = entry - side * 0.5 * rng
            pnl = None
            for _, b in later.iterrows():
                if (side == 1 and b.low <= stop) or (side == -1 and b.high >= stop):
                    pnl = (stop - entry) * side
                    break
            if pnl is None:
                pnl = (later.iloc[-1].close - entry) * side
            out[ts] = (pnl - cost) / pip
            break  # one trade per day
    return pd.Series(out, dtype=float)


def ou_fade_1h(h1: pd.DataFrame, symbol: str, z_in: float = 2.0,
               window: int = 48, max_hold: int = 36) -> pd.Series:
    """Fade z-score extremes of 1h close vs rolling mean. Exit z cross 0,
    time stop, or 3-sigma hard stop."""
    cost = COSTS[symbol]
    pip = PIP[symbol]
    c = h1.close
    mu = c.rolling(window).mean()
    sd = c.rolling(window).std()
    z = (c - mu) / sd
    out = {}
    i = window
    idx = h1.index
    while i < len(h1) - 1:
        zi = z.iloc[i]
        if np.isnan(zi) or abs(zi) < z_in:
            i += 1
            continue
        side = -1 if zi > 0 else 1          # fade the extreme
        entry = h1.open.iloc[i + 1]
        hard = entry - side * (3.0 * sd.iloc[i])
        pnl = None
        j_end = min(i + 1 + max_hold, len(h1) - 1)
        for j in range(i + 1, j_end + 1):
            b = h1.iloc[j]
            if (side == 1 and b.low <= hard) or (side == -1 and b.high >= hard):
                pnl = (hard - entry) * side
                break
            if (side == 1 and z.iloc[j] >= 0) or (side == -1 and z.iloc[j] <= 0):
                pnl = (b.close - entry) * side
                break
        if pnl is None:
            j = j_end
            pnl = (h1.close.iloc[j] - entry) * side
        out[idx[i]] = (pnl - cost) / pip
        i = j + 1
    return pd.Series(out, dtype=float)


def donchian_1h(h1: pd.DataFrame, symbol: str, n_in: int = 20, n_out: int = 10) -> pd.Series:
    """Classic channel breakout trend control."""
    cost = COSTS[symbol]
    pip = PIP[symbol]
    hi_in = h1.high.rolling(n_in).max().shift(1)
    lo_in = h1.low.rolling(n_in).min().shift(1)
    hi_out = h1.high.rolling(n_out).max().shift(1)
    lo_out = h1.low.rolling(n_out).min().shift(1)
    out = {}
    i = n_in
    idx = h1.index
    while i < len(h1) - 1:
        b = h1.iloc[i]
        side = 1 if b.close > hi_in.iloc[i] else (-1 if b.close < lo_in.iloc[i] else 0)
        if side == 0:
            i += 1
            continue
        entry = h1.open.iloc[i + 1]
        j = i + 1
        while j < len(h1) - 1:
            bj = h1.iloc[j]
            if (side == 1 and bj.close < lo_out.iloc[j]) or \
               (side == -1 and bj.close > hi_out.iloc[j]):
                break
            j += 1
        pnl = (h1.close.iloc[j] - entry) * side
        out[idx[i]] = (pnl - cost) / pip
        i = j + 1
    return pd.Series(out, dtype=float)


def asia_fade_15m(m15: pd.DataFrame, symbol: str) -> pd.Series:
    """During 23:00-06:00 UTC fade a 15m close beyond 2-sigma Bollinger(20)
    with RSI(14) extreme; exit at mid-band, 16 bars, or 2-sigma hard stop."""
    cost = COSTS[symbol]
    pip = PIP[symbol]
    c = m15.close
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    hours = m15.index.hour
    in_session = (hours >= 23) | (hours < 6)
    out = {}
    i = 20
    idx = m15.index
    while i < len(m15) - 1:
        if not in_session[i] or np.isnan(sd.iloc[i]) or sd.iloc[i] == 0:
            i += 1
            continue
        b = m15.iloc[i]
        side = 0
        if b.close > mid.iloc[i] + 2 * sd.iloc[i] and rsi.iloc[i] > 70:
            side = -1
        elif b.close < mid.iloc[i] - 2 * sd.iloc[i] and rsi.iloc[i] < 30:
            side = 1
        if side == 0:
            i += 1
            continue
        entry = m15.open.iloc[i + 1]
        hard = entry - side * 2 * sd.iloc[i]
        pnl = None
        j_end = min(i + 17, len(m15) - 1)
        for j in range(i + 1, j_end + 1):
            bj = m15.iloc[j]
            if (side == 1 and bj.low <= hard) or (side == -1 and bj.high >= hard):
                pnl = (hard - entry) * side
                break
            if (side == 1 and bj.close >= mid.iloc[j]) or \
               (side == -1 and bj.close <= mid.iloc[j]):
                pnl = (bj.close - entry) * side
                break
        if pnl is None:
            j = j_end
            pnl = (m15.close.iloc[j] - entry) * side
        out[idx[i]] = (pnl - cost) / pip
        i = j + 1
    return pd.Series(out, dtype=float)


def spread_ou_1h(a: pd.DataFrame, b: pd.DataFrame, sym_a: str, sym_b: str,
                 window: int = 240, z_in: float = 2.0, max_hold: int = 48) -> pd.Series:
    """Rolling-beta log-spread fade between two USD pairs (synthetic cross).
    PnL reported in bps of leg-A notional, both legs' costs charged."""
    la = np.log(a.close).rename("a")
    lb = np.log(b.close).rename("b")
    df = pd.concat([la, lb], axis=1).dropna()
    beta = df.a.rolling(window).cov(df.b) / df.b.rolling(window).var()
    spread = df.a - beta * df.b
    mu = spread.rolling(window).mean()
    sd = spread.rolling(window).std()
    z = (spread - mu) / sd
    # round-trip costs of both legs in log/bps terms
    cost_bps = (COSTS[sym_a] / a.close.mean() + COSTS[sym_b] / b.close.mean()) * 1e4
    out = {}
    i = window
    idx = df.index
    while i < len(df) - 1:
        zi = z.iloc[i]
        if np.isnan(zi) or abs(zi) < z_in:
            i += 1
            continue
        side = -1 if zi > 0 else 1
        s_entry = spread.iloc[i + 1] if i + 1 < len(df) else spread.iloc[i]
        j_end = min(i + 1 + max_hold, len(df) - 1)
        j = i + 1
        while j < j_end and not ((side == 1 and z.iloc[j] >= 0) or (side == -1 and z.iloc[j] <= 0)):
            j += 1
        pnl_bps = (spread.iloc[j] - s_entry) * side * 1e4
        out[idx[i]] = pnl_bps - cost_bps
        i = j + 1
    return pd.Series(out, dtype=float)


# ---------------------------------------------------------------- diagnostics

def diagnostics(h1: pd.DataFrame, symbol: str) -> None:
    r = np.log(h1.close).diff().dropna()
    is_r = r[r.index <= IS_END]
    for lag in (1, 4, 24):
        ac = is_r.autocorr(lag)
        t = ac * np.sqrt(len(is_r))
        print(f"    1h ret autocorr lag{lag:<3} {ac:+.4f}  (t={t:+.1f})")
    by_hour = is_r.groupby(is_r.index.hour).agg(["mean", "std", "count"])
    by_hour["t"] = by_hour["mean"] / by_hour["std"] * np.sqrt(by_hour["count"])
    sig = by_hour[abs(by_hour.t) >= 2.0]
    if len(sig):
        print(f"    IS hours with |t|>=2: " +
              ", ".join(f"h{h:02d}({row.t:+.1f})" for h, row in sig.iterrows()))
    else:
        print("    no hour-of-day drift with |t|>=2 in-sample")


# ---------------------------------------------------------------- main

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["GBPUSD", "AUDUSD", "USDJPY"])
    args = p.parse_args()

    data = {}
    for sym in args.symbols + ["EURUSD"]:
        try:
            data[sym] = load(sym)
        except FileNotFoundError:
            print(f"⚠️ no data for {sym}")

    for sym in args.symbols:
        if sym not in data:
            continue
        df = data[sym]
        m15 = resample(df, "15min")
        h1 = resample(df, "1h")
        print(f"\n{'=' * 72}\n{sym}: {df.index[0].date()} → {df.index[-1].date()}  "
              f"({len(h1):,} 1h bars)\n{'=' * 72}")
        print("  Diagnostics (IS only):")
        diagnostics(h1, sym)
        for name, fn, frame in (
            ("london_breakout", london_breakout, m15),
            ("ou_fade_1h", ou_fade_1h, h1),
            ("donchian_1h", donchian_1h, h1),
            ("asia_fade_15m", asia_fade_15m, m15),
        ):
            trades = fn(frame, sym)
            is_t, oos_t = split_is_oos(trades)
            print(f"  {name}:")
            show([stats(is_t, "IS  2024-01..2025-09"), stats(oos_t, "OOS 2025-10..end")])

    if "EURUSD" in data and "GBPUSD" in data:
        print(f"\n{'=' * 72}\nspread_ou_1h EURUSD vs GBPUSD (bps, both legs costed)\n{'=' * 72}")
        a1 = resample(data["EURUSD"], "1h")
        b1 = resample(data["GBPUSD"], "1h")
        trades = spread_ou_1h(a1, b1, "EURUSD", "GBPUSD")
        is_t, oos_t = split_is_oos(trades)
        for r in (stats(is_t, "IS"), stats(oos_t, "OOS")):
            if r.get("n", 0):
                print(f"    {r['label']:<4} n={r['n']:<5} PF={r['pf']:<5.2f} WR={r['wr']:5.1f}%  "
                      f"avg={r['mean_pips']:+6.2f}bps  t={r['t']:+5.2f}  total={r['sum_pips']:+8.0f}bps")
            else:
                print(f"    {r['label']} n=0")

    print("\nGate reminder: PF_net >= 1.3 IS AND same-direction OOS with n >= 30, "
          "else it dies here (memory: strict fills eat ~0.3 PF).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
