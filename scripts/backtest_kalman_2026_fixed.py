#!/usr/bin/env python3
"""
Kalman v2 — 2026 YTD fixed-parameter backtest with live-faithful mechanics.

WHAT THIS DOES (per user spec 2026-06-16):
  * Period: 2026-01-01 -> 2026-06-16 ONLY (this year).
  * Signals: the REAL KalmanRegimeStrategy.on_bar() (same code as live), driven
    on 15m XAUUSD bars resampled from the canonical 5m CSV (left/left, exactly
    like the live DataEngine + run_backtest loader). Config = the live
    config_live_5000.yaml kalman_regime block.
  * FIXED stop / target / lot (NOT ATR-dynamic):
        SL  = 33.0 price points  (~= live 3.0 x median 2026 15m ATR)
        TP  = SL * RR            (RR = 1.0 primary -> live kalman_min_tp_rr)
        lot = 0.02               (XAUUSD system min; what live floors to)
  * Breakeven / lock modeled EXACTLY like the live TrailingStopManager /
    SimulatedBroker (breakeven_atr_mult 1.2, lock_atr_mult 2.0, lock_fraction
    0.5, measured in units of the initial risk distance R = SL points).
  * KILL SWITCH / CIRCUIT BREAKER / MAX-DRAWDOWN HALT: OFF (ignored, per spec).
  * DAILY LOSS LIMIT: $150 realized/day. Once breached, NO new entries for the
    rest of that UTC day. RESETS every UTC day. (This is the only account-level
    risk gate left on — it is NOT the kill switch, which force-flattens.)
  * max_positions = 2, no-hedge directional lock (live behaviour).
  * Realistic fills: signal at close of bar t -> fill at OPEN of bar t+1; a
    per-side cost (spread+slip) is paid on entry and on stop/market exits; TP
    limit fills exactly (no positive slippage). Adverse weekend/overnight gaps
    fill at the gapped open (worse than the stop). Same-bar SL+TP -> SL first
    (conservative).

Edge cases handled: gap-through stop, gap-through target, same-bar SL+TP tie,
same-bar entry+exit, breakeven/lock ratchet, daily-cap mid-day block + reset,
positions held over weekends, open positions force-closed at end of data.

Outputs: month-by-month tables, full pattern analysis, a fixed-param
sensitivity grid, trades CSV, signal cache, and a markdown report.
"""

import sys
import logging
import argparse
from pathlib import Path
from decimal import Decimal
from collections import defaultdict

import numpy as np
import pandas as pd

# Silence the per-bar "no signal" INFO flood from the strategy.
logging.disable(logging.INFO)

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
from src.core.types import Symbol

# ---------------------------------------------------------------------------
# Constants / defaults (all FIXED, all stated up front for auditability)
# ---------------------------------------------------------------------------
CONFIG_PATH = "config/config_live_5000.yaml"
DATA_CSV = "data/historical/XAUUSD_5m_real.csv"
SIG_CACHE = PROJECT_ROOT / "data/backtests/kalman_2026_signals.csv"
TRADES_OUT = PROJECT_ROOT / "data/backtests/kalman_2026_fixed_trades.csv"
REPORT_OUT = PROJECT_ROOT / "reports/kalman_2026_fixed_analysis.md"

START, END = "2026-01-01", "2026-06-16"
TIMEFRAME_MIN = 15
INITIAL_CAPITAL = 5000.0
VALUE_PER_LOT = 100.0          # XAUUSD: $100 per 1.0 price move per 1.0 lot

# FIXED trade parameters (primary scenario)
SL_PTS = 33.0                  # ~= 3.0 x median 2026 15m ATR(14) (live multiplier)
RR = 1.0                       # TP = SL * RR (live kalman_min_tp_rr = 1.0)
LOT = 0.02                     # XAUUSD min_lot; the floor live actually trades
COST = 0.20                    # per-side spread+slippage in price points

# Live breakeven / lock (risk.trailing_stop in config_live_5000.yaml)
BE_MULT = 1.2                  # move SL -> entry at +1.2R favourable
LOCK_MULT = 2.0               # move SL -> entry +0.5R at +2.0R favourable
LOCK_FRAC = 0.5

# Account gates kept ON
DAILY_LOSS_CAP = 150.0         # absolute_max_loss_usd
MAX_POSITIONS = 2
DIRECTIONAL_LOCK = True        # no hedging (opposite side blocked while open)


# ---------------------------------------------------------------------------
# Data + signal generation (Phase A — slow, cached)
# ---------------------------------------------------------------------------
def load_15m_2026() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    bars = (
        df.resample(f"{TIMEFRAME_MIN}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min",
              "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )
    lo = pd.Timestamp(START, tz=bars.index.tz)
    hi = pd.Timestamp(END, tz=bars.index.tz) + pd.Timedelta(days=1)
    return bars[(bars.index >= lo) & (bars.index < hi)]


def build_symbol(cfg: dict) -> Symbol:
    s = cfg.get("symbols", {}).get("XAUUSD", {})
    return Symbol(
        ticker="XAUUSD",
        pip_value=Decimal(str(s.get("pip_value", 0.01))),
        min_lot=Decimal(str(s.get("min_lot", 0.02))),
        max_lot=Decimal(str(s.get("max_lot", 0.05))),
        lot_step=Decimal(str(s.get("lot_step", 0.01))),
        value_per_lot=Decimal(str(s.get("value_per_lot", 100))),
        min_stops_distance=Decimal(str(s.get("min_stops_distance", 1.0))),
        leverage=Decimal(str(s.get("leverage", 30))),
    )


def generate_signals(bars: pd.DataFrame, cfg: dict, refresh: bool) -> pd.DataFrame:
    """Replay the real strategy once; cache emitted signals to CSV.

    A signal recorded at positional bar `t` means on_bar(window ending at t)
    fired; the trade will be FILLED at the open of bar t+1.
    """
    if SIG_CACHE.exists() and not refresh:
        sig = pd.read_csv(SIG_CACHE, parse_dates=["signal_ts"])
        print(f"  [cache] loaded {len(sig)} signals from {SIG_CACHE}")
        return sig

    symbol = build_symbol(cfg)
    kcfg = dict(cfg["strategies"]["kalman_regime"])
    kcfg["enabled"] = True
    strat = KalmanRegimeStrategy(symbol, kcfg)

    n = len(bars)
    max_window = 1000          # same cap as BacktestEngine
    rows = []
    print(f"  replaying {n} bars through KalmanRegimeStrategy.on_bar() ...")
    for i in range(n):
        w0 = max(0, i + 1 - max_window)
        window = bars.iloc[w0:i + 1]
        if len(window) < 50:
            continue
        sig = strat.on_bar(window)
        if sig is not None:
            md = sig.metadata or {}
            rows.append({
                "bar_idx": i,
                "signal_ts": bars.index[i],
                "side": sig.side.value,          # 'buy' / 'sell'
                "strength": float(sig.strength),
                "mode": md.get("mode"),
                "adx": md.get("adx"),
                "rsi": md.get("rsi"),
                "atr": md.get("atr"),
                "confidence": md.get("confidence"),
            })
        if (i + 1) % 2000 == 0:
            print(f"    {i+1}/{n} bars, {len(rows)} signals so far")
    sig_df = pd.DataFrame(rows)
    SIG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    sig_df.to_csv(SIG_CACHE, index=False)
    print(f"  [cache] wrote {len(sig_df)} signals -> {SIG_CACHE}")
    return sig_df


# ---------------------------------------------------------------------------
# Fixed-parameter fill/exit simulation (Phase B — fast, runs many times)
# ---------------------------------------------------------------------------
def simulate(bars: pd.DataFrame, sig_df: pd.DataFrame, *,
             sl_pts=SL_PTS, rr=RR, lot=LOT, cost=COST,
             be_enabled=True, daily_cap=DAILY_LOSS_CAP,
             max_positions=MAX_POSITIONS, directional_lock=DIRECTIONAL_LOCK,
             range_max_bars=None):
    """Run the fixed-SL/TP/lot simulation. Returns (trades_df, skipped_counts).

    range_max_bars: if set, force-close RANGE-mode trades at market once they have
    been open this many bars without hitting SL/TP (layer-4 time-stop).
    """
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    ts = bars.index
    day = np.array([t.date() for t in ts])
    n = len(bars)
    tp_pts = sl_pts * rr

    # Map entry bar -> list of signals (signal at t fills at t+1).
    by_entry = defaultdict(list)
    for _, s in sig_df.iterrows():
        eb = int(s["bar_idx"]) + 1
        if eb < n:
            by_entry[eb].append(s)

    open_pos = []          # list of dicts
    trades = []
    skipped = defaultdict(int)
    cur_day = None
    daily_realized = 0.0
    cap_hit_days = set()

    def close_trade(p, exit_fill, reason, i):
        nonlocal daily_realized
        sign = 1.0 if p["side"] == 1 else -1.0
        pnl = (exit_fill - p["entry"]) * lot * VALUE_PER_LOT * sign
        daily_realized += pnl
        trades.append({
            "entry_ts": p["entry_ts"], "exit_ts": ts[i],
            "side": "buy" if p["side"] == 1 else "sell",
            "mode": p["mode"], "strength": p["strength"],
            "entry": p["entry"], "exit": exit_fill,
            "sl0": p["sl0"], "tp": p["tp"],
            "exit_reason": reason, "stage": p["stage"],
            "bars_held": i - p["entry_bar"],
            "pnl": pnl,
            "hour": p["entry_ts"].hour, "weekday": p["entry_ts"].weekday(),
            "month": p["entry_ts"].strftime("%Y-%m"),
        })
        if daily_realized <= -daily_cap:
            cap_hit_days.add(cur_day)

    def try_exit(p, oi, hi, li, is_entry_bar):
        """Return (exit_fill, reason) or None. SL-first tie-break."""
        long = p["side"] == 1
        # Gap at open (only for positions opened on a previous bar).
        if not is_entry_bar:
            if long:
                if oi <= p["sl"]:
                    return oi - cost, "stop"
                if oi >= p["tp"]:
                    return p["tp"], "take_profit"
            else:
                if oi >= p["sl"]:
                    return oi + cost, "stop"
                if oi <= p["tp"]:
                    return p["tp"], "take_profit"
        # Intrabar, SL assumed hit before TP if both inside the bar.
        if long:
            if li <= p["sl"]:
                return p["sl"] - cost, "stop"
            if hi >= p["tp"]:
                return p["tp"], "take_profit"
        else:
            if hi >= p["sl"]:
                return p["sl"] + cost, "stop"
            if li <= p["tp"]:
                return p["tp"], "take_profit"
        return None

    def update_be(p, close):
        if not be_enabled:
            return
        R = sl_pts
        if p["side"] == 1:
            profit = close - p["entry"]
            if p["stage"] < 2 and profit >= LOCK_MULT * R:
                new_sl = p["entry"] + LOCK_FRAC * R
                if new_sl > p["sl"]:
                    p["sl"], p["stage"] = new_sl, 2
            elif p["stage"] < 1 and profit >= BE_MULT * R:
                if p["entry"] > p["sl"]:
                    p["sl"], p["stage"] = p["entry"], 1
        else:
            profit = p["entry"] - close
            if p["stage"] < 2 and profit >= LOCK_MULT * R:
                new_sl = p["entry"] - LOCK_FRAC * R
                if new_sl < p["sl"]:
                    p["sl"], p["stage"] = new_sl, 2
            elif p["stage"] < 1 and profit >= BE_MULT * R:
                if p["entry"] < p["sl"]:
                    p["sl"], p["stage"] = p["entry"], 1

    def reason_name(reason, stage):
        if reason == "take_profit":
            return "take_profit"
        return {0: "stop_loss", 1: "breakeven", 2: "locked"}[stage]

    for i in range(n):
        d = day[i]
        if d != cur_day:
            cur_day = d
            daily_realized = 0.0

        # B1: exit positions opened on a PREVIOUS bar (gap-aware).
        for p in list(open_pos):
            if p["entry_bar"] < i:
                # Layer-4 RANGE time-stop: close at market if not reverted in time.
                if (range_max_bars and p.get("mode") == "range"
                        and (i - p["entry_bar"]) >= range_max_bars):
                    fill = o[i] - cost if p["side"] == 1 else o[i] + cost
                    close_trade(p, fill, "time_stop", i)
                    open_pos.remove(p)
                    continue
                res = try_exit(p, o[i], h[i], l[i], is_entry_bar=False)
                if res:
                    fill, reason = res
                    close_trade(p, fill, reason_name(reason, p["stage"]), i)
                    open_pos.remove(p)

        # A: entries filling at THIS bar's open (signal came from bar i-1).
        for s in by_entry.get(i, []):
            if daily_realized <= -daily_cap:
                skipped["daily_cap"] += 1
                continue
            if len(open_pos) >= max_positions:
                skipped["max_positions"] += 1
                continue
            side = 1 if str(s["side"]).upper() == "BUY" else -1
            if directional_lock and open_pos and any(p["side"] != side for p in open_pos):
                skipped["directional_lock"] += 1
                continue
            entry = o[i] + cost if side == 1 else o[i] - cost
            sl0 = entry - sl_pts if side == 1 else entry + sl_pts
            tp = entry + tp_pts if side == 1 else entry - tp_pts
            open_pos.append({
                "side": side, "entry": entry, "sl": sl0, "sl0": sl0, "tp": tp,
                "stage": 0, "entry_bar": i, "entry_ts": ts[i],
                "mode": s.get("mode"), "strength": s.get("strength"),
            })

        # B2: same-bar exit for freshly opened positions (no gap; intrabar).
        for p in list(open_pos):
            if p["entry_bar"] == i:
                res = try_exit(p, o[i], h[i], l[i], is_entry_bar=True)
                if res:
                    fill, reason = res
                    close_trade(p, fill, reason_name(reason, p["stage"]), i)
                    open_pos.remove(p)

        # C: breakeven/lock ratchet on the close (affects subsequent bars).
        for p in open_pos:
            update_be(p, c[i])

    # Force-close anything still open at end of data.
    for p in list(open_pos):
        sign = 1.0 if p["side"] == 1 else -1.0
        fill = c[-1] - cost if p["side"] == 1 else c[-1] + cost
        close_trade(p, fill, "end_of_data", n - 1)
        open_pos.remove(p)

    trades_df = pd.DataFrame(trades)
    skipped["cap_hit_days"] = len(cap_hit_days)
    return trades_df, skipped


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------
def stats(t: pd.DataFrame) -> dict:
    if len(t) == 0:
        return dict(n=0, wr=0, pf=0, net=0, avg_w=0, avg_l=0, exp=0,
                    gw=0, gl=0, max_w=0, max_l=0, mcl=0)
    wins = t[t.pnl > 0]
    losses = t[t.pnl < 0]
    gw, gl = wins.pnl.sum(), -losses.pnl.sum()
    # max consecutive losses
    mcl = cur = 0
    for p in t.sort_values("exit_ts").pnl:
        if p < 0:
            cur += 1
            mcl = max(mcl, cur)
        else:
            cur = 0
    return dict(
        n=len(t), wr=100 * len(wins) / len(t),
        pf=(gw / gl) if gl > 0 else float("inf"),
        net=t.pnl.sum(), avg_w=wins.pnl.mean() if len(wins) else 0,
        avg_l=losses.pnl.mean() if len(losses) else 0,
        exp=t.pnl.mean(), gw=gw, gl=gl,
        max_w=t.pnl.max(), max_l=t.pnl.min(), mcl=mcl,
    )


def max_drawdown(t: pd.DataFrame, capital: float):
    if len(t) == 0:
        return 0.0, 0.0
    eq = capital + t.sort_values("exit_ts").pnl.cumsum()
    eq = pd.concat([pd.Series([capital]), eq], ignore_index=True)
    peak = eq.cummax()
    dd = eq - peak
    return float(dd.min()), float((dd / peak).min() * 100)


def grp_table(t: pd.DataFrame, key: str, order=None) -> str:
    rows = []
    keys = order if order else sorted(t[key].dropna().unique())
    for k in keys:
        sub = t[t[key] == k]
        if len(sub) == 0:
            continue
        s = stats(sub)
        pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
        rows.append(f"  {str(k):<10} {s['n']:>5} {s['wr']:>6.1f}% {pf:>6} "
                    f"{s['net']:>+10.2f} {s['exp']:>+8.2f}")
    head = f"  {key:<10} {'N':>5} {'Win%':>7} {'PF':>6} {'Net$':>10} {'Exp$':>8}"
    return head + "\n" + "\n".join(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-signals", action="store_true",
                    help="Force re-run of the (slow) on_bar replay.")
    args = ap.parse_args()

    with open(PROJECT_ROOT / CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    print("=" * 78)
    print("KALMAN v2 — 2026 YTD FIXED-PARAMETER BACKTEST (XAUUSD 15m)")
    print("=" * 78)
    bars = load_15m_2026()
    print(f"  bars: {len(bars)}  ({bars.index.min()} -> {bars.index.max()})")
    sig_df = generate_signals(bars, cfg, args.refresh_signals)
    print(f"  signals emitted by on_bar(): {len(sig_df)}")

    # ---- PRIMARY run -----------------------------------------------------
    print("\n" + "=" * 78)
    print("PRIMARY SCENARIO (live-faithful, FIXED params)")
    print("=" * 78)
    print(f"  SL={SL_PTS} pts | TP={SL_PTS*RR} pts (RR {RR}) | lot={LOT} | "
          f"cost={COST}/side | BE 1.2R lock 2.0R | dailycap=${DAILY_LOSS_CAP} | "
          f"maxpos={MAX_POSITIONS} | killswitch OFF")
    t, sk = simulate(bars, sig_df)
    t.to_csv(TRADES_OUT, index=False)
    s = stats(t)
    dd, ddp = max_drawdown(t, INITIAL_CAPITAL)
    final_eq = INITIAL_CAPITAL + s["net"]
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    print(f"\n  Trades taken:     {s['n']}   (signals {len(sig_df)}; "
          f"skipped maxpos {sk['max_positions']}, dircap {sk['directional_lock']}, "
          f"dailycap {sk['daily_cap']})")
    print(f"  Net P&L:          ${s['net']:+,.2f}   ({100*s['net']/INITIAL_CAPITAL:+.2f}% of ${INITIAL_CAPITAL:,.0f})")
    print(f"  Final equity:     ${final_eq:,.2f}")
    print(f"  Win rate:         {s['wr']:.1f}%   ({len(t[t.pnl>0])}W / {len(t[t.pnl<0])}L)")
    print(f"  Profit factor:    {pf}")
    print(f"  Expectancy:       ${s['exp']:+.2f}/trade")
    print(f"  Avg win / loss:   ${s['avg_w']:+.2f} / ${s['avg_l']:+.2f}")
    print(f"  Largest win/loss: ${s['max_w']:+.2f} / ${s['max_l']:+.2f}")
    print(f"  Max consec losses:{s['mcl']}")
    print(f"  Max drawdown:     ${dd:,.2f} ({ddp:.2f}%)")
    print(f"  Days daily-cap hit: {sk['cap_hit_days']}")

    # ---- MONTH BY MONTH --------------------------------------------------
    print("\n" + "=" * 78)
    print("MONTH-BY-MONTH")
    print("=" * 78)
    print(f"  {'Month':<8} {'N':>4} {'Win%':>6} {'PF':>6} {'Net$':>10} "
          f"{'Exp$':>7} {'MaxLoss':>8} {'MCL':>4} {'EndEq$':>10}")
    run_eq = INITIAL_CAPITAL
    for m in sorted(t.month.unique()):
        sub = t[t.month == m]
        ss = stats(sub)
        run_eq += ss["net"]
        pfm = "inf" if ss["pf"] == float("inf") else f"{ss['pf']:.2f}"
        print(f"  {m:<8} {ss['n']:>4} {ss['wr']:>5.1f}% {pfm:>6} "
              f"{ss['net']:>+10.2f} {ss['exp']:>+7.2f} {ss['max_l']:>+8.2f} "
              f"{ss['mcl']:>4} {run_eq:>10.2f}")

    # ---- PATTERNS --------------------------------------------------------
    print("\n" + "=" * 78)
    print("PATTERNS")
    print("=" * 78)
    print("\nBy SIDE:")
    print(grp_table(t, "side", order=["buy", "sell"]))
    print("\nBy REGIME / mode:")
    print(grp_table(t, "mode", order=["trend", "range"]))
    print("\nBy EXIT REASON:")
    print(grp_table(t, "exit_reason"))
    print("\nBy UTC HOUR (entry):")
    print(grp_table(t, "hour"))
    wd = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    tt = t.copy()
    tt["wd"] = tt.weekday.map(wd)
    print("\nBy WEEKDAY (entry):")
    print(grp_table(tt, "wd", order=["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"]))

    # ---- SENSITIVITY GRID ------------------------------------------------
    print("\n" + "=" * 78)
    print("SENSITIVITY GRID (lot=0.02, cost=0.20, BE on, dailycap on)")
    print("=" * 78)
    print(f"  {'SL':>4} {'RR':>4} {'TP':>5} {'N':>5} {'Win%':>6} {'PF':>6} "
          f"{'Net$':>10} {'MaxDD$':>9} {'MaxDD%':>7}")
    for sl in (22.0, 33.0, 49.0):
        for rr in (1.0, 1.5, 2.0):
            tg, _ = simulate(bars, sig_df, sl_pts=sl, rr=rr)
            sg = stats(tg)
            ddg, ddpg = max_drawdown(tg, INITIAL_CAPITAL)
            pfg = "inf" if sg["pf"] == float("inf") else f"{sg['pf']:.2f}"
            print(f"  {sl:>4.0f} {rr:>4.1f} {sl*rr:>5.0f} {sg['n']:>5} "
                  f"{sg['wr']:>5.1f}% {pfg:>6} {sg['net']:>+10.2f} "
                  f"{ddg:>9.0f} {ddpg:>6.1f}%")

    print("\n  -- lot sensitivity (SL33 RR1.0) --")
    for lot in (0.02, 0.05):
        tl, _ = simulate(bars, sig_df, lot=lot)
        sl_ = stats(tl)
        ddl, ddpl = max_drawdown(tl, INITIAL_CAPITAL)
        pfl = "inf" if sl_["pf"] == float("inf") else f"{sl_['pf']:.2f}"
        print(f"    lot {lot}: N {sl_['n']} | Net ${sl_['net']:+,.2f} | "
              f"PF {pfl} | MaxDD ${ddl:,.0f} ({ddpl:.1f}%)")

    print("\n  -- cost sensitivity (SL33 RR1.0 lot0.02) --")
    for cst in (0.0, 0.20, 0.50):
        tc, _ = simulate(bars, sig_df, cost=cst)
        sc = stats(tc)
        pfc = "inf" if sc["pf"] == float("inf") else f"{sc['pf']:.2f}"
        print(f"    cost {cst:.2f}: Net ${sc['net']:+,.2f} | PF {pfc} | "
              f"Win% {sc['wr']:.1f}")

    print("\n  -- breakeven & daily-cap toggles (SL33 RR2.0 lot0.02) --")
    for be in (True, False):
        for cap in (DAILY_LOSS_CAP, 1e9):
            tb, skb = simulate(bars, sig_df, rr=2.0, be_enabled=be, daily_cap=cap)
            sb = stats(tb)
            pfb = "inf" if sb["pf"] == float("inf") else f"{sb['pf']:.2f}"
            ddb, _ = max_drawdown(tb, INITIAL_CAPITAL)
            cap_s = f"${DAILY_LOSS_CAP:.0f}" if cap < 1e9 else "OFF"
            print(f"    BE {str(be):<5} dailycap {cap_s:<5}: Net ${sb['net']:+,.2f} | "
                  f"PF {pfb} | Win% {sb['wr']:.1f} | MaxDD ${ddb:,.0f} | "
                  f"capdays {skb['cap_hit_days']}")

    print(f"\n  trades saved -> {TRADES_OUT}")
    print("\nDONE.")


if __name__ == "__main__":
    main()
