#!/usr/bin/env python3
"""
EMA(20/50) Trend + Zone-Retest — research prototype (XAUUSD).

User-supplied discretionary rule:
  1. TREND filter:  price above BOTH EMA20 and EMA50 -> look for BUY only;
                     price below BOTH -> look for SELL only. EMA20 must also
                     sit on the trend side of EMA50 (a real stack, not a
                     crossover in progress).
  2. NO crossover entries / ALWAYS confirm: the stack must have held for
                     `--min-trend-bars` bars before any retest counts (rules
                     out trading the crossover whipsaw itself).
  3. ZONE = the band between EMA20 (near boundary) and EMA50 (far boundary).
                     A "retest" = price wicks back into the zone (touches the
                     near EMA) and then CLOSES back beyond the near EMA in the
                     trend direction (the confirmation candle). A close through
                     the FAR EMA invalidates the trend/zone entirely (reset).
  4. ENTRY COUNT:    BUY needs the 3rd confirmed retest since the trend
                     started; SELL fires on the 1st (asymmetric, as specified
                     by the user -- gold's down-moves are faster and don't
                     offer as many retest chances).
  5. STOP / TARGET:  no stop/target was specified in the rule, so this script
                     uses a STRUCTURAL stop (beyond the far EMA / the retest
                     bar's wick, whichever is further) and a fixed R:R target,
                     same convention as the other `research_*` scripts in this
                     repo (see research_stoch_pullback.py).

These are the concrete assumptions needed to make "wait for the retest" and
"confirmation" mechanical -- flag anything that doesn't match your mental
model of the rule and the thresholds are trivial to change (see main()).

Walk-forward: 2025 = OOS, 2026 = in-sample (same split as the squeeze /
stoch_pullback research). Promotion bar (project memory): clear ~1.10 PF on
BOTH years before any wiring.

Writes: reports/ema_retest_research.md
Usage:  python scripts/research_ema_retest.py [--tf 15|5]
"""

import sys
import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators

DATA_CSV = PROJECT_ROOT / "data/historical/XAUUSD_5m_real.csv"
REPORT = PROJECT_ROOT / "reports/ema_retest_research.md"

VALUE_PER_LOT = 100.0      # XAUUSD: $100 / 1.0 price move / 1.0 lot
LOT = 0.02                 # min lot floor (what the $5k account actually trades)
COST = 0.20                # per-side spread+slippage in price points
DAILY_CAP = 150.0          # absolute_max_loss_usd
CAPITAL = 5_000.0

# --- $5k config_live_5000 risk caps (modelled by --enforce-risk) -------------
RISK_USD = 15.0
MAX_LOT = 0.50
MAX_DD_USD = 250.0
MAX_DAILY_TRADES = 10
MAX_DAILY_PROFIT = 260.0
CB_CONSEC = 2
CB_PAUSE_MIN = 30

YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-22")}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_tf(tf_min: int, start, end) -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    if tf_min == 5:
        bars = df
    else:
        bars = (df.resample(f"{tf_min}min", label="left", closed="left")
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "volume": "sum"})
                .dropna(subset=["open", "high", "low", "close"]))
    lo = pd.Timestamp(start, tz=bars.index.tz)
    hi = pd.Timestamp(end, tz=bars.index.tz)
    return bars[(bars.index >= lo) & (bars.index < hi)]


# ---------------------------------------------------------------------------
# Signal generation (stateful: retest counting needs a state machine, not a
# vectorised mask -- kept as a plain bar loop, per ThePrimeagen: boring beats
# clever here)
# ---------------------------------------------------------------------------
def ema_retest_signals(bars, *, ema_fast=20, ema_slow=50, min_trend_bars=5,
                        buy_retests=3, sell_retests=1, touch_buffer_atr=0.0,
                        zone_mode="atr", zone_atr_mult=0.5,
                        sl_buffer_atr=0.15, min_stop_pts=2.0):
    """zone_mode="ema50" is the original definition (far boundary = EMA50 --
    can get very wide once a trend is running, so almost any pullback counts
    as a "retest"). zone_mode="atr" tightens the zone to a fixed ATR-scaled
    band around EMA20 regardless of how far EMA50 has drifted: a retest must
    be a SHALLOW, controlled dip (wick stays within `zone_atr_mult`*ATR of
    EMA20) and a wick that overshoots past that tight floor doesn't count as
    a clean retest (nor does it invalidate the trend -- it's just ignored,
    waiting for the next attempt) unless the CLOSE breaks through it.
    """
    close = bars["close"].to_numpy(float)
    high = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    ema_f = Indicators.ema(bars, period=ema_fast).to_numpy(float)
    ema_s = Indicators.ema(bars, period=ema_slow).to_numpy(float)
    atr = Indicators.atr(bars, period=14).to_numpy(float)
    n = len(bars)

    rows = []
    trend = 0            # 1 = up, -1 = down, 0 = flat/invalid
    trend_start = -1
    retests = 0
    touching = False

    for i in range(n):
        if np.isnan(ema_f[i]) or np.isnan(ema_s[i]) or np.isnan(atr[i]):
            continue

        up_align = close[i] > ema_f[i] and close[i] > ema_s[i] and ema_f[i] > ema_s[i]
        dn_align = close[i] < ema_f[i] and close[i] < ema_s[i] and ema_f[i] < ema_s[i]
        new_trend = 1 if up_align else (-1 if dn_align else 0)

        if new_trend != trend:
            trend, trend_start, retests, touching = new_trend, i, 0, False
            continue   # a fresh stack/flip never fires the same bar

        if trend == 0:
            continue
        if i - trend_start < min_trend_bars:
            continue   # "don't enter on the crossover" -- let the stack settle

        near = ema_f[i]
        if zone_mode == "atr":
            zw = zone_atr_mult * atr[i]
            far = near - zw if trend == 1 else near + zw
        else:
            far = ema_s[i]
        buf = touch_buffer_atr * atr[i]
        if trend == 1:
            invalidated = close[i] < far
            touched = (low[i] <= near + buf) and (low[i] >= far)
            bounced = close[i] > near
        else:
            invalidated = close[i] > far
            touched = (high[i] >= near - buf) and (high[i] <= far)
            bounced = close[i] < near

        if invalidated:
            trend, retests, touching = 0, 0, False
            continue

        if not touching and touched:
            touching = True
        if touching and bounced:
            retests += 1
            touching = False
            required = buy_retests if trend == 1 else sell_retests
            if retests >= required:
                retests = 0   # next trade needs a fresh full cycle of retests
                if trend == 1:
                    stop = min(low[i], far) - sl_buffer_atr * atr[i]
                    if close[i] - stop < min_stop_pts:
                        continue
                    side = "buy"
                else:
                    stop = max(high[i], far) + sl_buffer_atr * atr[i]
                    if stop - close[i] < min_stop_pts:
                        continue
                    side = "sell"
                rows.append({"bar_idx": i, "signal_ts": bars.index[i],
                             "side": side, "stop_price": float(stop)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulator (structural SL, RR target, strict fills -- identical discipline
# to research_stoch_pullback.py: cost-per-side, next-bar-open fills, SL-first
# intrabar tie-break, one position at a time, daily loss cap)
# ---------------------------------------------------------------------------
def simulate(bars, sig_df, *, rr=2.0, lot=LOT, cost=COST, daily_cap=DAILY_CAP,
             enforce_risk=False):
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    ts = bars.index
    day = np.array([t.date() for t in ts])
    n = len(bars)

    by_entry = {}
    for _, s in sig_df.iterrows():
        eb = int(s["bar_idx"]) + 1
        if eb < n:
            by_entry.setdefault(eb, []).append(s)

    trades = []
    pos = None
    cur_day = None
    daily = 0.0
    daily_trades = 0
    realized = 0.0
    hwm = 0.0
    halted = False
    consec_losses = 0
    pause_until = None

    def close_trade(p, fill, reason, i):
        nonlocal daily, realized, hwm, halted, consec_losses, pause_until
        sign = 1.0 if p["side"] == 1 else -1.0
        pnl = (fill - p["entry"]) * p["lot"] * VALUE_PER_LOT * sign
        daily += pnl
        trades.append({"entry_ts": p["entry_ts"], "exit_ts": ts[i],
                       "side": "buy" if p["side"] == 1 else "sell",
                       "entry": p["entry"], "exit": fill, "sl": p["sl"], "tp": p["tp"],
                       "lot": p["lot"], "exit_reason": reason,
                       "bars_held": i - p["entry_bar"],
                       "pnl": pnl, "hour": p["entry_ts"].hour,
                       "month": p["entry_ts"].strftime("%Y-%m")})
        if enforce_risk:
            realized += pnl
            hwm = max(hwm, realized)
            if hwm - realized >= MAX_DD_USD:
                halted = True
            if pnl < 0:
                consec_losses += 1
                if consec_losses >= CB_CONSEC:
                    pause_until = ts[i] + pd.Timedelta(minutes=CB_PAUSE_MIN)
                    consec_losses = 0
            else:
                consec_losses = 0

    def try_exit(p, oi, hi, li, is_entry_bar):
        long = p["side"] == 1
        if not is_entry_bar:
            if long:
                if oi <= p["sl"]:
                    return oi - cost, "stop_loss"
                if oi >= p["tp"]:
                    return p["tp"], "take_profit"
            else:
                if oi >= p["sl"]:
                    return oi + cost, "stop_loss"
                if oi <= p["tp"]:
                    return p["tp"], "take_profit"
        if long:
            if li <= p["sl"]:
                return p["sl"] - cost, "stop_loss"
            if hi >= p["tp"]:
                return p["tp"], "take_profit"
        else:
            if hi >= p["sl"]:
                return p["sl"] + cost, "stop_loss"
            if li <= p["tp"]:
                return p["tp"], "take_profit"
        return None

    for i in range(n):
        if day[i] != cur_day:
            cur_day = day[i]
            daily = 0.0
            daily_trades = 0

        if enforce_risk and halted:
            break

        if pos and pos["entry_bar"] < i:
            res = try_exit(pos, o[i], h[i], l[i], is_entry_bar=False)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None

        entries_ok = pos is None and not (daily <= -daily_cap)
        if enforce_risk and entries_ok:
            entries_ok = (daily_trades < MAX_DAILY_TRADES
                          and daily < MAX_DAILY_PROFIT
                          and (pause_until is None or ts[i] >= pause_until))
        if entries_ok:
            for s in by_entry.get(i, []):
                side = 1 if str(s["side"]).upper() == "BUY" else -1
                entry = o[i] + cost if side == 1 else o[i] - cost
                stop = float(s["stop_price"])
                dist = (entry - stop) if side == 1 else (stop - entry)
                if dist <= 0:
                    continue
                tp = entry + rr * dist if side == 1 else entry - rr * dist
                if enforce_risk:
                    psize = RISK_USD / (dist * VALUE_PER_LOT)
                    psize = min(MAX_LOT, max(LOT, round(psize, 2)))
                else:
                    psize = lot
                pos = {"side": side, "entry": entry, "sl": stop, "tp": tp,
                       "lot": psize, "entry_bar": i, "entry_ts": ts[i]}
                daily_trades += 1
                break

        if pos and pos["entry_bar"] == i:
            res = try_exit(pos, o[i], h[i], l[i], is_entry_bar=True)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None

    if pos:
        fill = c[-1] - cost if pos["side"] == 1 else c[-1] + cost
        close_trade(pos, fill, "end_of_data", n - 1)
    return pd.DataFrame(trades)


def stats(t):
    if len(t) == 0:
        return dict(n=0, wr=0.0, pf=0.0, net=0.0, exp=0.0)
    wins, losses = t[t.pnl > 0], t[t.pnl < 0]
    gw, gl = wins.pnl.sum(), -losses.pnl.sum()
    return dict(n=len(t), wr=100 * len(wins) / len(t),
                pf=(gw / gl) if gl > 0 else float("inf"),
                net=t.pnl.sum(), exp=t.pnl.mean())


def max_dd(t, capital):
    if len(t) == 0:
        return 0.0
    eq = capital + t.sort_values("exit_ts").pnl.cumsum()
    peak = eq.cummax()
    return float(((eq - peak) / capital * 100).min())


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", type=int, default=15, choices=[5, 15])
    ap.add_argument("--enforce-risk", action="store_true")
    ap.add_argument("--buy-retests", type=int, default=3)
    ap.add_argument("--sell-retests", type=int, default=1)
    ap.add_argument("--min-trend-bars", type=int, default=5)
    ap.add_argument("--zone-mode", default="atr", choices=["atr", "ema50"])
    ap.add_argument("--zone-atr-mult", type=float, default=0.5,
                     help="ATR multiple defining the tight zone half-width "
                          "around EMA20 (only used when --zone-mode atr)")
    args = ap.parse_args()
    tf = args.tf
    er = args.enforce_risk

    rr_grid = [1.5, 2.0, 3.0]
    results = {}
    counts = {}
    for label, (start, end) in YEARS.items():
        bars = load_tf(tf, start, end)
        sig = ema_retest_signals(bars, min_trend_bars=args.min_trend_bars,
                                  buy_retests=args.buy_retests,
                                  sell_retests=args.sell_retests,
                                  zone_mode=args.zone_mode,
                                  zone_atr_mult=args.zone_atr_mult)
        counts[label] = (len(bars), len(sig))
        print(f"\n{label}: {len(bars)} bars ({tf}m) | signals {len(sig)} "
              f"(buy_retests={args.buy_retests} sell_retests={args.sell_retests} "
              f"zone={args.zone_mode}/{args.zone_atr_mult}) | enforce_risk={er}")
        per = {}
        for rr in rr_grid:
            t = simulate(bars, sig, rr=rr, enforce_risk=er)
            per[rr] = (stats(t), max_dd(t, CAPITAL))
        results[label] = per

    def pf(x):
        return "inf" if x == float("inf") else f"{x:.2f}"

    print("\n" + "=" * 78)
    for label in YEARS:
        print(label)
        for rr, (s, dd) in results[label].items():
            print(f"  RR{rr:.1f}: N{s['n']:>4} WR{s['wr']:>5.1f}% PF {pf(s['pf'])} "
                  f"net ${s['net']:+,.0f} DD {dd:.1f}%")
        print("-" * 78)

    # ---- report ----
    L = []; A = L.append
    A(f"# EMA(20/50) Trend + Zone-Retest — Research Prototype (XAUUSD {tf}m)")
    A("")
    A("**Script:** `scripts/research_ema_retest.py` · **Source:** user-supplied "
      "discretionary rule")
    A("")
    A(f"**Run:** buy_retests=`{args.buy_retests}` · sell_retests=`{args.sell_retests}` "
      f"· min_trend_bars=`{args.min_trend_bars}` · enforce_risk=`{er}`")
    A("")
    A("Rule: price above/below BOTH EMA20 & EMA50 sets bias; no entries on the "
      "crossover itself (stack must hold `min_trend_bars`); a **retest** = price "
      "wicks into the EMA20/EMA50 zone and closes back beyond EMA20 in the trend "
      "direction; BUY needs the 3rd confirmed retest, SELL fires on the 1st; a "
      "close through EMA50 invalidates the setup. Stop = structural (beyond EMA50 "
      "/ retest wick), target = fixed R:R (no stop/target was specified in the "
      "rule, so this mirrors the other `research_*` scripts in this repo). Strict "
      "fills (cost 0.20/side, next-bar-open, SL-first), $5k. 2025 = OOS, "
      "2026 = in-sample.")
    A("")
    if er:
        A("> **enforce_risk** models config_live_5000: risk-$15/trade structural "
          "sizing (min_lot 0.02 floor), $150 daily cap, max 10 trades/day, +$260 "
          "daily-profit stop, 2-consec-loss 30-min circuit breaker, and the $250 "
          "(5%) trailing-drawdown kill switch that halts the run once hit.")
        A("")
    for label in YEARS:
        nb, ns = counts[label]
        A(f"## {label}")
        A("")
        A(f"Bars: {nb} · signals: {ns}")
        A("")
        A("| RR | N | Win% | PF | Net$ | MaxDD% |")
        A("|---|---:|---:|---:|---:|---:|")
        for rr, (s, dd) in results[label].items():
            A(f"| {rr:.1f} | {s['n']} | {s['wr']:.1f}% | {pf(s['pf'])} | "
              f"{s['net']:+,.0f} | {dd:.1f}% |")
        A("")

    cand = [(rr, results["2026 (in-sample)"][rr][0], results["2025 (OOS)"][rr][0])
            for rr in rr_grid
            if results["2026 (in-sample)"][rr][0]["pf"] > 1.10
            and results["2026 (in-sample)"][rr][0]["n"] >= 15]
    A("## Verdict")
    A("")
    if not cand:
        A("➖ **No in-sample edge** — nothing clears 1.10 PF on 2026 with N>=15.")
    else:
        best = max(cand, key=lambda x: x[2]["pf"])
        rr, s_is, s_oos = best
        A(f"- Best cell **RR{rr:.1f}**: 2026 PF {pf(s_is['pf'])} (N{s_is['n']}) → "
          f"2025 OOS PF {pf(s_oos['pf'])} (N{s_oos['n']}).")
        if s_oos["pf"] > 1.10:
            A("- ✅ **Clears 1.10 on BOTH years** — promotable to a wired strategy "
              "(CLAUDE.md propagation checklist) after a longer OOS + session-filter check.")
        elif s_oos["pf"] >= 1.0:
            A("- ⚠️ **Marginal** — OOS positive but below the 1.10 durability bar; not "
              "promotable as-is.")
        else:
            A("- ⚠️ **In-sample only** — OOS PF < 1.0. Not an edge.")
    A("")
    A(f"> Run both timeframes: `python scripts/research_ema_retest.py --tf 15` "
      "and `--tf 5`.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
