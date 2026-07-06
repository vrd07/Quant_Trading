#!/usr/bin/env python3
"""
BOS structure strategy (new_strategies.md #1) — research prototype.

Sequence (user spec, 15m):
  1. CHOCH  — close breaks the last swing level AGAINST the prevailing trend.
  2. BOS #1 — close breaks the next swing level in the NEW direction (trend flips).
  3. BOS #2 — second break in the new direction.
  4. ENTRY  — after BOS #2, on the next CONFIRMED pullback pivot: higher-low for
              longs / lower-high for shorts (user decision 2026-07-07). Each further
              BOS in the sequence re-arms ONE more pullback entry.
  5. STOP   — just beyond the entry pivot (0.1*ATR buffer); TP = RR * stop distance
              (sweep RR 1.5 / 2.0 / 3.0). Pivot width N swept 3 / 5 / 7.

Pivots are N-bar fractals and CONFIRM N bars after their extreme — signals can only
fire post-confirmation (no lookahead). Breaks are close-based (no wick breaks).

Strict fills as elsewhere in the repo: cost per side, next-bar-open entries,
SL-first intrabar tie-break, one position at a time.

Risk enforcement (user spec: ONLY these three):
  fixed lot, max daily loss $150, trailing max-drawdown $250 halt
  ($5k config_live_5000 values, user limits 2026-05-30). No circuit breaker,
  no sizing, no profit stop, no trade-count cap.

Symbols: XAUUSD (2025-01-29..2026-07-06) and US30 (2024-01-01..2026-06-22).
⚠️ US30 value_per_lot=$1/point/lot is the config PLACEHOLDER — verify vs broker.

Writes: reports/bos_structure_research.md
Usage:  python scripts/research_bos_structure.py [--symbol XAUUSD|US30|both]
"""

import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORT = PROJECT_ROOT / "reports/bos_structure_research.md"
CAPITAL = 5_000.0
DAILY_CAP = 150.0          # max daily loss (config max_daily_loss_pct 0.03)
MAX_DD_USD = 250.0         # trailing max-drawdown kill switch (5%)

SYMBOLS = {
    "XAUUSD": dict(csv="data/historical/XAUUSD_5m_real.csv",
                   value_per_lot=100.0, lot=0.02, cost=0.20, min_stop=2.0),
    # US30 CFD: config placeholder $1/point/lot; 0.10 lot ≈ $0.10/pt so a ~300pt
    # structural stop risks ~$30 — comparable to gold's 0.02-lot trade risk.
    "US30": dict(csv="data/historical/US30_5m_real.csv",
                 value_per_lot=1.0, lot=0.10, cost=2.0, min_stop=20.0),
}

PIVOT_NS = [3, 5, 7]
RRS = [1.5, 2.0, 3.0]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_15m(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
    bars = (df.resample("15min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"]))
    # scrub flat weekend/holiday bars (Dukascopy artifacts)
    flat = (bars.high == bars.low) & (bars.volume == 0)
    return bars[~flat]


def atr14(bars: pd.DataFrame) -> pd.Series:
    h, l, c = bars.high, bars.low, bars.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean()


# ---------------------------------------------------------------------------
# Structure engine
# ---------------------------------------------------------------------------
def find_pivots(bars: pd.DataFrame, n: int):
    """N-bar fractal pivots. Returns list of (confirm_bar, kind, price, extreme_bar)
    sorted by confirm_bar; kind 'H' or 'L'. A pivot confirms n bars after the extreme."""
    h = bars.high.to_numpy(float)
    l = bars.low.to_numpy(float)
    w = 2 * n + 1
    hmax = bars.high.rolling(w, center=True).max().to_numpy(float)
    lmin = bars.low.rolling(w, center=True).min().to_numpy(float)
    piv = []
    last_h_bar = last_l_bar = -10**9
    for i in range(n, len(bars) - n):
        if h[i] == hmax[i] and i - last_h_bar > n:
            piv.append((i + n, "H", h[i], i))
            last_h_bar = i
        if l[i] == lmin[i] and i - last_l_bar > n:
            piv.append((i + n, "L", l[i], i))
            last_l_bar = i
    piv.sort(key=lambda p: p[0])
    return piv


def bos_signals(bars: pd.DataFrame, pivot_n: int, buffer_atr=0.10, min_stop=2.0):
    """Walk bars; emit entries per the CHOCH → BOS#1 → BOS#2 → pullback-pivot spec.

    Returns DataFrame: bar_idx (signal bar = pivot confirm bar), side, stop_price.
    """
    c = bars.close.to_numpy(float)
    atr = atr14(bars).to_numpy(float)
    pivots = find_pivots(bars, pivot_n)
    by_bar = {}
    for cb, kind, price, xb in pivots:
        by_bar.setdefault(cb, []).append((kind, price, xb))

    trend = 0          # established trend: +1 up / -1 down / 0 unknown
    seq_dir = 0        # active CHOCH sequence direction
    bos_count = 0
    armed = False      # BOS#2 printed -> next pullback pivot fires (one-shot per BOS)
    cur_sh = cur_sl = None      # latest UNBROKEN swing levels (close-break targets)
    prev_hp = prev_lp = None    # previous confirmed high/low pivot price (HL/LH test)
    last_hp = last_lp = None
    rows = []

    for i in range(len(bars)):
        # 1) pivots confirming at this bar
        for kind, price, xb in by_bar.get(i, []):
            if kind == "H":
                prev_hp, last_hp = last_hp, price
                cur_sh = price
                # pullback entry: lower-high pivot while armed short
                if (armed and seq_dir == -1 and prev_hp is not None
                        and price < prev_hp):
                    stop = price + max(buffer_atr * atr[i], min_stop * 0.5)
                    if stop > c[i]:
                        rows.append(dict(bar_idx=i, signal_ts=bars.index[i],
                                         side="SELL", stop_price=stop))
                        armed = False
            else:
                prev_lp, last_lp = last_lp, price
                cur_sl = price
                # pullback entry: higher-low pivot while armed long
                if (armed and seq_dir == 1 and prev_lp is not None
                        and price > prev_lp):
                    stop = price - max(buffer_atr * atr[i], min_stop * 0.5)
                    if stop < c[i]:
                        rows.append(dict(bar_idx=i, signal_ts=bars.index[i],
                                         side="BUY", stop_price=stop))
                        armed = False

        # 2) close-based structure breaks
        if cur_sh is not None and c[i] > cur_sh:
            cur_sh = None                      # consumed until a new SH confirms
            if seq_dir == 1:                   # BOS in the new up direction
                bos_count += 1
                if bos_count == 1:
                    trend = 1
                if bos_count >= 2:
                    armed = True
            elif trend <= 0:                   # break against prevailing trend
                seq_dir, bos_count, armed = 1, 0, False       # CHOCH up
        if cur_sl is not None and c[i] < cur_sl:
            cur_sl = None
            if seq_dir == -1:
                bos_count += 1
                if bos_count == 1:
                    trend = -1
                if bos_count >= 2:
                    armed = True
            elif trend >= 0:
                seq_dir, bos_count, armed = -1, 0, False      # CHOCH down

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulator — strict fills; risk = ONLY daily cap + trailing-DD halt + fixed lot
# ---------------------------------------------------------------------------
def simulate(bars, sig_df, *, rr, lot, cost, value_per_lot,
             enforce_risk=False, daily_cap=DAILY_CAP, max_dd_usd=MAX_DD_USD):
    o = bars.open.to_numpy(float)
    h = bars.high.to_numpy(float)
    l = bars.low.to_numpy(float)
    c = bars.close.to_numpy(float)
    ts = bars.index
    day = np.array([t.date() for t in ts])
    n = len(bars)

    by_entry = {}
    if len(sig_df):
        for _, s in sig_df.iterrows():
            eb = int(s["bar_idx"]) + 1
            if eb < n:
                by_entry.setdefault(eb, []).append(s)

    trades = []
    pos = None
    cur_day = None
    daily = 0.0
    realized = 0.0
    hwm = 0.0
    halted = False

    def close_trade(p, fill, reason, i):
        nonlocal daily, realized, hwm, halted
        sign = 1.0 if p["side"] == 1 else -1.0
        pnl = (fill - p["entry"]) * p["lot"] * value_per_lot * sign
        daily += pnl
        realized += pnl
        hwm = max(hwm, realized)
        if enforce_risk and hwm - realized >= max_dd_usd:
            halted = True
        trades.append({"entry_ts": p["entry_ts"], "exit_ts": ts[i],
                       "side": "buy" if p["side"] == 1 else "sell",
                       "entry": p["entry"], "exit": fill, "sl": p["sl"],
                       "tp": p["tp"], "lot": p["lot"], "exit_reason": reason,
                       "bars_held": i - p["entry_bar"], "pnl": pnl,
                       "hour": p["entry_ts"].hour,
                       "month": p["entry_ts"].strftime("%Y-%m")})

    def try_exit(p, oi, hi, li, is_entry_bar):
        long = p["side"] == 1
        if not is_entry_bar:                       # gap through a level at open
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
        if long:                                   # intrabar, SL-first
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

        if enforce_risk and halted:
            break

        if pos and pos["entry_bar"] < i:
            res = try_exit(pos, o[i], h[i], l[i], is_entry_bar=False)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None

        entries_ok = pos is None
        if enforce_risk:
            entries_ok = entries_ok and daily > -daily_cap
        if entries_ok:
            for s in by_entry.get(i, []):
                side = 1 if str(s["side"]).upper() == "BUY" else -1
                entry = o[i] + cost if side == 1 else o[i] - cost
                stop = float(s["stop_price"])
                dist = (entry - stop) if side == 1 else (stop - entry)
                if dist <= 0:
                    continue
                tp = entry + rr * dist if side == 1 else entry - rr * dist
                pos = {"side": side, "entry": entry, "sl": stop, "tp": tp,
                       "lot": lot, "entry_bar": i, "entry_ts": ts[i]}
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


# ---------------------------------------------------------------------------
# Stats / reporting
# ---------------------------------------------------------------------------
def stats(t):
    if len(t) == 0:
        return dict(n=0, wr=0.0, pf=0.0, net=0.0, exp=0.0, dd=0.0)
    wins, losses = t[t.pnl > 0], t[t.pnl < 0]
    gw, gl = wins.pnl.sum(), -losses.pnl.sum()
    eq = CAPITAL + t.sort_values("exit_ts").pnl.cumsum()
    dd = float(((eq - eq.cummax()) / CAPITAL * 100).min())
    return dict(n=len(t), wr=100 * len(wins) / len(t),
                pf=(gw / gl) if gl > 0 else float("inf"),
                net=float(t.pnl.sum()), exp=float(t.pnl.mean()), dd=dd)


def fmt(s):
    pf = f"{s['pf']:.2f}" if np.isfinite(s['pf']) else "inf"
    return (f"n={s['n']:<4} WR={s['wr']:5.1f}%  PF={pf:<5} "
            f"net=${s['net']:>+9.2f}  exp=${s['exp']:>+7.2f}  DD={s['dd']:6.2f}%")


def slice_period(t, start, end):
    if len(t) == 0:
        return t
    lo = pd.Timestamp(start, tz="UTC")
    hi = pd.Timestamp(end, tz="UTC")
    return t[(t.entry_ts >= lo) & (t.entry_ts < hi)]


def monthly_table(t):
    lines = ["| Month | Trades | WR | PF | Net |", "|---|---|---|---|---|"]
    for m, g in t.groupby("month"):
        s = stats(g)
        pf = f"{s['pf']:.2f}" if np.isfinite(s['pf']) else "inf"
        lines.append(f"| {m} | {s['n']} | {s['wr']:.0f}% | {pf} | ${s['net']:+.2f} |")
    return "\n".join(lines)


def deep_dive(t, title):
    if len(t) == 0:
        return f"### {title}\n\nNo trades.\n"
    s = stats(t)
    out = [f"### {title}", "", f"**{fmt(s)}**", "", monthly_table(t), ""]
    out.append("| Split | Trades | WR | PF | Net |")
    out.append("|---|---|---|---|---|")
    for label, g in [("BUY", t[t.side == "buy"]), ("SELL", t[t.side == "sell"]),
                     ("Asia 0-6h", t[t.hour < 7]),
                     ("London 7-12h", t[(t.hour >= 7) & (t.hour < 13)]),
                     ("NY 13-20h", t[(t.hour >= 13) & (t.hour < 21)]),
                     ("Late 21-23h", t[t.hour >= 21])]:
        ss = stats(g)
        pf = f"{ss['pf']:.2f}" if np.isfinite(ss['pf']) else ("inf" if ss['n'] else "-")
        out.append(f"| {label} | {ss['n']} | {ss['wr']:.0f}% | {pf} | ${ss['net']:+.2f} |")
    er = t.exit_reason.value_counts().to_dict()
    out.append("")
    out.append(f"Exit reasons: {er}; avg bars held {t.bars_held.mean():.1f} "
               f"(~{t.bars_held.mean() * 15 / 60:.1f}h)")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="both", choices=["XAUUSD", "US30", "both"])
    args = ap.parse_args()
    syms = ["XAUUSD", "US30"] if args.symbol == "both" else [args.symbol]

    report = ["# BOS Structure Strategy — Research (new_strategies.md #1)", "",
              f"Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. 15m bars, strict fills "
              "(cost/side, next-bar-open, SL-first). CHOCH → BOS#1 → BOS#2 → "
              "confirmed pullback-pivot entry; structural SL + RR·TP.",
              "", "Risk enforcement = ONLY fixed lot + $150 daily loss + $250 "
              "trailing-DD halt ($5k config values). Raw = no caps.", ""]

    for sym in syms:
        spec = SYMBOLS[sym]
        bars = load_15m(PROJECT_ROOT / spec["csv"])
        end = bars.index[-1]
        year_ago = end - pd.Timedelta(days=365)
        print(f"\n=== {sym}: {bars.index[0]:%Y-%m-%d} .. {end:%Y-%m-%d} "
              f"({len(bars)} 15m bars) lot={spec['lot']} ===")
        report += [f"## {sym}", "",
                   f"Data {bars.index[0]:%Y-%m-%d} → {end:%Y-%m-%d}, "
                   f"{len(bars)} 15m bars, fixed lot {spec['lot']}, "
                   f"cost {spec['cost']}/side, ${spec['value_per_lot']}/pt/lot.", ""]
        if sym == "US30":
            report.append("⚠️ US30 contract spec is the config PLACEHOLDER "
                          "($1/pt/lot) — verify vs broker before believing $ figures.\n")

        sweep_rows = []
        results = {}
        for pn in PIVOT_NS:
            sig = bos_signals(bars, pn, min_stop=spec["min_stop"])
            for rr in RRS:
                t = simulate(bars, sig, rr=rr, lot=spec["lot"], cost=spec["cost"],
                             value_per_lot=spec["value_per_lot"])
                s_full = stats(t)
                s_2026 = stats(slice_period(t, "2026-01-01", "2027-01-01"))
                s_2025 = stats(slice_period(t, "2025-01-01", "2026-01-01"))
                results[(pn, rr)] = t
                sweep_rows.append((pn, rr, s_full, s_2025, s_2026))
                pf26 = f"{s_2026['pf']:.2f}" if np.isfinite(s_2026['pf']) else "inf"
                pf25 = f"{s_2025['pf']:.2f}" if np.isfinite(s_2025['pf']) else "inf"
                print(f"  N={pn} RR={rr}: full {fmt(s_full)} | 2025 PF {pf25} "
                      f"| 2026 PF {pf26}")

        report.append("### Sweep (raw, risk-bypassed)")
        report.append("")
        report.append("| Pivot N | RR | Trades | WR | PF full | Net | MaxDD | "
                      "PF 2025 | PF 2026 |")
        report.append("|---|---|---|---|---|---|---|---|---|")
        for pn, rr, sf, s25, s26 in sweep_rows:
            pf = lambda s: (f"{s['pf']:.2f}" if np.isfinite(s['pf'])
                            else ("inf" if s['n'] else "-"))
            report.append(f"| {pn} | {rr} | {sf['n']} | {sf['wr']:.0f}% | {pf(sf)} | "
                          f"${sf['net']:+.2f} | {sf['dd']:.2f}% | {pf(s25)} | {pf(s26)} |")
        report.append("")

        # best cell: highest min(PF 2025, PF 2026) with >=30 trades full-span
        def score(row):
            _, _, sf, s25, s26 = row
            if sf["n"] < 30 or s25["n"] == 0 or s26["n"] == 0:
                return -1
            return min(s25["pf"], s26["pf"])
        best = max(sweep_rows, key=score)
        pn, rr = best[0], best[1]
        t = results[(pn, rr)]
        report.append(f"**Best cell by min(PF 2025, PF 2026): N={pn}, RR={rr}**\n")
        print(f"  BEST {sym}: N={pn} RR={rr}")

        report.append(deep_dive(t, f"{sym} N={pn} RR={rr} — full span (raw)"))
        report.append(deep_dive(slice_period(t, str(year_ago.date()), "2027-01-01"),
                                f"{sym} — last 12 months (raw)"))
        report.append(deep_dive(slice_period(t, "2026-01-01", "2027-01-01"),
                                f"{sym} — 2026 YTD deep dive (raw)"))

        sig = bos_signals(bars, pn, min_stop=spec["min_stop"])
        te = simulate(bars, sig, rr=rr, lot=spec["lot"], cost=spec["cost"],
                      value_per_lot=spec["value_per_lot"], enforce_risk=True)
        report.append(deep_dive(te, f"{sym} N={pn} RR={rr} — ENFORCED "
                                    "($150 daily / $250 trailing halt, fixed lot)"))
        se = stats(te)
        halted = len(te) < len(t)
        report.append(f"Enforced run {'HALTED by trailing-DD kill switch' if halted and se['dd'] <= -MAX_DD_USD / CAPITAL * 100 else 'completed'}: "
                      f"{len(te)} of {len(t)} raw trades taken.\n")
        print(f"  ENFORCED: {fmt(se)}  ({len(te)}/{len(t)} trades)")

    REPORT.write_text("\n".join(report))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
