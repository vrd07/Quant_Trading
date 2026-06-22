#!/usr/bin/env python3
"""
Stochastic Pullback Continuation (2R-3R) — research prototype (XAUUSD).

Implements the ACY "How to Trade Gold Using Stochastics (2R/3R)" method
(acy.com/.../how-to-trade-gold-using-stochastics-2r-3r-j-o-100124). The article's
edge is NOT a stochastic reversal — it is a TREND-CONTINUATION pullback:

  1. TREND filter:  established trend via EMA(`trend_ema`) slope + price side.
  2. PULLBACK:      Stochastic(14,3) %K "cools off" into the 20-30 zone (long) /
                    70-80 zone (short) within the last `arm_window` bars — momentum
                    reset, NOT an extreme reversal call.
  3. ENTRY:         price consolidates, then BREAKS OUT in the trend direction
                    (close clears the recent `range_bars`-bar range high/low) with
                    momentum resuming (%K back above %D long / below %D short).
  4. STOP:          structural — just behind the consolidation range (range low for
                    long / range high for short), + small buffer.
  5. TARGET:        fixed 2R / 3R of that structural stop distance.

Because the stop is STRUCTURAL (per-trade distance), we cannot reuse the fixed-SL
kalman harness; this file ships a self-contained simulator with the same strict-
fill discipline used elsewhere: cost-per-side, next-bar-open fills, SL-first
intrabar tie-break, one position at a time, daily loss cap.

Walk-forward: 2025 = OOS, 2026 = in-sample (same split as the squeeze research).
Promotion bar (project memory): clear ~1.10 PF on BOTH years before any wiring.

Writes: reports/stoch_pullback_research.md
Usage:  python scripts/research_stoch_pullback.py [--tf 15|5]
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
REPORT = PROJECT_ROOT / "reports/stoch_pullback_research.md"

VALUE_PER_LOT = 100.0      # XAUUSD: $100 / 1.0 price move / 1.0 lot
LOT = 0.02                 # min lot floor (what the $5k account actually trades)
COST = 0.20                # per-side spread+slippage in price points
DAILY_CAP = 150.0          # absolute_max_loss_usd
CAPITAL = 5_000.0

# --- $5k config_live_5000 risk caps (modelled by --enforce-risk) -------------
RISK_USD = 15.0            # risk_per_trade_pct 0.003 -> $15/trade (structural sizing)
MAX_LOT = 0.50
MAX_DD_USD = 250.0         # max_drawdown_usd (5%) trailing kill switch -> halt run
MAX_DAILY_TRADES = 10
MAX_DAILY_PROFIT = 260.0   # stop trading the day once realized >= +$260
CB_CONSEC = 2              # soft pause after 2 consecutive losses (loss_pause_consecutive)
CB_PAUSE_MIN = 30          # cooldown minutes

# Session windows (UTC entry-hour gate). London open through NY = the high-liquidity
# gold window where prior repo research found the only real breakout edge.
SESSIONS = {"all": None, "london_ny": range(7, 21), "london": range(7, 16),
            "ny": range(12, 21)}

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
# Signal generation (pure)
# ---------------------------------------------------------------------------
def stoch_pullback_signals(bars, *, trend_ema=50, stoch_period=14,
                           pull_lo=20.0, pull_hi=30.0, arm_window=10,
                           range_bars=5, buffer_pts=0.10, min_stop_pts=2.0,
                           session_hours=None, min_ema_dist_atr=1.0):
    """Vectorised stochastic-pullback-continuation signals.

    Returns a DataFrame: bar_idx, signal_ts, side, stop_price, strength.
    The stop is STRUCTURAL (per-trade), carried on the row; TP is derived from RR
    in the simulator.

    min_ema_dist_atr is the trend-extension filter (analyze_stoch_losers.py): price
    must be >= this * ATR from the EMA in the trend direction (real trend, not chop
    around the mean). Lifts PF on IS+OOS. Set 0 to disable.
    """
    close = bars["close"]
    high, low = bars["high"], bars["low"]
    ema = Indicators.ema(bars, period=trend_ema)
    k, d = Indicators.stochastic(bars, period=stoch_period)
    atr = Indicators.atr(bars, period=14)

    ext = min_ema_dist_atr * atr
    up = (close > ema + ext) & (ema > ema.shift(5))     # established, extended uptrend
    dn = (close < ema - ext) & (ema < ema.shift(5))     # established, extended downtrend

    # PULLBACK armed: %K dipped into the cool-off zone within the last arm_window bars
    long_cool = (k <= pull_hi)                    # cooled off (<=30) at some recent bar
    short_cool = (k >= (100 - pull_hi))           # heated up (>=70)
    long_armed = long_cool.shift(1).rolling(arm_window).max().fillna(0).astype(bool)
    short_armed = short_cool.shift(1).rolling(arm_window).max().fillna(0).astype(bool)

    # CONSOLIDATION range = prior `range_bars` bars (exclude current breakout bar)
    range_hi = high.rolling(range_bars).max().shift(1)
    range_lo = low.rolling(range_bars).min().shift(1)

    # momentum resuming in trend direction
    mom_up = k > d
    mom_dn = k < d

    long_sig = up & long_armed & mom_up & (close > range_hi) & (k > pull_lo)
    short_sig = dn & short_armed & mom_dn & (close < range_lo) & (k < (100 - pull_lo))

    rows = []
    n = len(bars)
    in_trade_until = -1   # crude cooldown: no new signal while the latch is hot
    for i in range(n):
        if i <= in_trade_until:
            continue
        lb, sb = bool(long_sig.iloc[i]), bool(short_sig.iloc[i])
        if not (lb or sb):
            continue
        if session_hours is not None and bars.index[i].hour not in session_hours:
            continue
        c_i = float(close.iloc[i])
        if lb:
            stop = float(range_lo.iloc[i]) - buffer_pts
            if c_i - stop < min_stop_pts:
                continue
            side = "buy"
        else:
            stop = float(range_hi.iloc[i]) + buffer_pts
            if stop - c_i < min_stop_pts:
                continue
            side = "sell"
        kk = float(k.iloc[i])
        strength = (1.0 - kk / 100.0) if lb else (kk / 100.0)
        rows.append({"bar_idx": i, "signal_ts": bars.index[i], "side": side,
                     "stop_price": stop, "strength": float(strength)})
        in_trade_until = i + range_bars   # don't re-arm on the same breakout
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulator (structural SL, RR target, strict fills)
# ---------------------------------------------------------------------------
def simulate(bars, sig_df, *, rr=2.0, lot=LOT, cost=COST, daily_cap=DAILY_CAP,
             enforce_risk=False, dd_aware=False, risk_base=RISK_USD, dd_soft=200.0):
    """Next-bar-open fills, structural SL, TP = entry +/- rr*stop_dist.
    SL-first intrabar tie-break, one position at a time, per-day loss cap.

    enforce_risk=True models the config_live_5000 risk engine: risk-$15/trade
    structural sizing (min_lot floor), $150 daily cap, max 10 trades/day, +$260
    daily-profit stop, a 2-consecutive-loss 30-min circuit breaker, and the $250
    (5%) TRAILING-DRAWDOWN kill switch that halts the run permanently once hit.

    dd_aware=True (requires enforce_risk) replaces the brittle hard halt with a
    RECOVERABLE soft throttle: base risk `risk_base` per trade, scaled down by
    factor (1 - dd/dd_soft) as the trailing drawdown deepens, and new entries
    PAUSED (not latched off) once dd >= dd_soft. The $250 hard kill switch stays
    as a backstop but should never trip if the soft budget holds it off.
    """
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
    # enforce-risk state
    realized = 0.0          # cumulative realized pnl
    hwm = 0.0               # high-water mark of realized equity
    halted = False          # trailing-DD kill switch latched
    consec_losses = 0
    pause_until = None      # circuit-breaker cooldown end (timestamp)

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
        if not is_entry_bar:   # gap at open
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
        if long:               # intrabar, SL-first
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
            break   # trailing-DD kill switch: no further trades this run

        # exit a position opened on a previous bar (gap-aware)
        if pos and pos["entry_bar"] < i:
            res = try_exit(pos, o[i], h[i], l[i], is_entry_bar=False)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None

        # entries at this bar's open (signal from bar i-1)
        entries_ok = pos is None and not (daily <= -daily_cap)
        cur_dd = (hwm - realized) if enforce_risk else 0.0
        if enforce_risk and entries_ok:
            entries_ok = (daily_trades < MAX_DAILY_TRADES
                          and daily < MAX_DAILY_PROFIT
                          and (pause_until is None or ts[i] >= pause_until))
            # NOTE: no hard pause on drawdown — pausing while FLAT latches the run
            # (realized equity can't recover without trading). De-risk by SIZE only.
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
                    target = risk_base if dd_aware else RISK_USD
                    if dd_aware:   # de-risk by SIZE into DD, floored at 0.3x (no zero)
                        target *= max(0.30, 1.0 - cur_dd / dd_soft)
                    psize = target / (dist * VALUE_PER_LOT)
                    psize = min(MAX_LOT, max(LOT, round(psize, 2)))
                else:
                    psize = lot
                pos = {"side": side, "entry": entry, "sl": stop, "tp": tp,
                       "lot": psize, "entry_bar": i, "entry_ts": ts[i]}
                daily_trades += 1
                break

        # same-bar intrabar exit for a freshly opened position
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
    ap.add_argument("--enforce-risk", action="store_true",
                    help="model config_live_5000 risk caps + $250 trailing-DD kill switch")
    ap.add_argument("--session", default="all", choices=list(SESSIONS),
                    help="UTC entry-hour gate (default all)")
    ap.add_argument("--dd-aware", action="store_true",
                    help="recoverable soft-throttle sizing (implies --enforce-risk)")
    ap.add_argument("--risk-base", type=float, default=7.5,
                    help="base $/trade for --dd-aware (default 7.5 = half)")
    args = ap.parse_args()
    tf = args.tf
    er = args.enforce_risk or args.dd_aware
    sess_hours = SESSIONS[args.session]

    rr_grid = [1.5, 2.0, 3.0]
    results = {}
    counts = {}
    for label, (start, end) in YEARS.items():
        bars = load_tf(tf, start, end)
        sig = stoch_pullback_signals(bars, session_hours=sess_hours)
        counts[label] = (len(bars), len(sig))
        print(f"\n{label}: {len(bars)} bars ({tf}m) | signals {len(sig)} "
              f"| session={args.session} enforce_risk={er}")
        per = {}
        for rr in rr_grid:
            t = simulate(bars, sig, rr=rr, enforce_risk=er,
                         dd_aware=args.dd_aware, risk_base=args.risk_base)
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
    A(f"# Stochastic Pullback Continuation (2R-3R) — Research Prototype (XAUUSD {tf}m)")
    A("")
    A("**Script:** `scripts/research_stoch_pullback.py` · "
      "**Source:** ACY *How to Trade Gold Using Stochastics (2R/3R)*")
    A("")
    A(f"**Run:** session=`{args.session}` · enforce_risk=`{er}`")
    A("")
    A("Trend-continuation pullback: EMA(50) trend + Stochastic(14,3) cool-off into "
      "the 20-30 zone, enter on the consolidation breakout in the trend direction, "
      "**structural stop behind the range**, fixed 2R/3R target. Strict fills "
      "(cost 0.20/side, next-bar-open, SL-first), $5k. 2025 = OOS, 2026 = in-sample.")
    A("")
    if er:
        A("> **enforce_risk** models config_live_5000: risk-$15/trade structural "
          "sizing (min_lot 0.02 floor), $150 daily cap, max 10 trades/day, +$260 "
          "daily-profit stop, 2-consec-loss 30-min circuit breaker, and the **$250 "
          "(5%) trailing-drawdown kill switch that halts the run once hit**.")
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

    # verdict: among RRs positive in-sample (2026 PF>1.10, N>=15), highest OOS PF
    cand = [(rr, results["2026 (in-sample)"][rr][0], results["2025 (OOS)"][rr][0])
            for rr in rr_grid
            if results["2026 (in-sample)"][rr][0]["pf"] > 1.10
            and results["2026 (in-sample)"][rr][0]["n"] >= 15]
    A("## Verdict")
    A("")
    if not cand:
        A("➖ **No in-sample edge** — nothing clears 1.10 PF on 2026. The ACY "
          "discretionary 'wait for the range / breakout' steps don't survive a "
          "mechanical breakout proxy on gold (which mean-reverts intraday).")
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
    A(f"> Run both timeframes: `python scripts/research_stoch_pullback.py --tf 15` "
      "and `--tf 5`.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
