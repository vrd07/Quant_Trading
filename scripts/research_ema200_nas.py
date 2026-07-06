#!/usr/bin/env python3
"""
EMA 200 NASDAQ strategy (new_strategies.md #2) — research prototype.

User spec (5m candles, NASDAQ-100 CFD):
  - EMA(200) on 5m closes (TradingView-standard EMA).
  - ANCHOR candle = the 5m candle at 19:10 IST = 13:40 UTC (IST has no DST ->
    fixed UTC time year-round; ~10 min after NY cash open in US summer,
    ~40 min before it in US winter).
  - Anchor closes ABOVE EMA200 -> BUY setup: any later candle closing above the
    anchor's CLOSE, within the 19:10-21:10 IST window (trigger close <= 15:40 UTC),
    is the entry trigger. Mirror below EMA200 for SELL.
  - STRICTLY one entry per day (latch).
  - SL = anchor candle's opposite extreme (low for BUY / high for SELL);
    TP = 2.0 x stop distance (RR 1:2 per spec).

Fills: strict — cost per side, entry at next bar open after the trigger close,
SL-first intrabar tie-break. Variants reported: hold-to-SL/TP vs force-close at
21:55 UTC (spec silent on overnight holds; index CFDs pay financing overnight).

Risk enforcement (user spec, ONLY these): fixed lot, max daily loss $150,
trailing max-drawdown $250 halt ($5k config values).

⚠️ value_per_lot=$1/point/lot mirrors the US30 config PLACEHOLDER — verify against
the broker's actual NASDAQ-100 ticker spec (user will input the ticker in the
start script; live wiring must keep the symbol configurable).

Writes: reports/ema200_nasdaq_research.md
Usage:  python scripts/research_ema200_nas.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_bos_structure import (  # noqa: E402
    simulate, stats, fmt, slice_period, monthly_table, deep_dive, CAPITAL,
    DAILY_CAP, MAX_DD_USD)

DATA_CSV = PROJECT_ROOT / "data/historical/NAS100_5m_real.csv"
REPORT = PROJECT_ROOT / "reports/ema200_nasdaq_research.md"

VALUE_PER_LOT = 1.0        # $/point/lot — PLACEHOLDER, verify vs broker ticker
LOT = 1.0                  # fixed lot (median anchor stop ~25-40 pts -> ~$30 risk)
COST = 1.0                 # per-side spread+slippage in index points

ANCHOR_UTC = (13, 40)      # 19:10 IST
WINDOW_END_UTC = (15, 40)  # 21:10 IST — trigger candle must CLOSE by this time
EOD_UTC = (21, 55)         # force-flat time for the eod-close variant


def load_5m() -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    flat = (df.high == df.low) & (df.volume == 0)
    return df[~flat]


def ema200_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """One-entry-per-day EMA200 anchor-break signals. Returns bar_idx (trigger
    bar; sim enters next bar open), side, stop_price."""
    close = bars.close.to_numpy(float)
    ema = bars.close.ewm(span=200, adjust=False).mean().to_numpy(float)
    ts = bars.index
    minutes = ts.hour * 60 + ts.minute
    anchor_min = ANCHOR_UTC[0] * 60 + ANCHOR_UTC[1]
    # trigger candle CLOSES by 15:40 UTC -> its open time <= 15:35
    last_trigger_open = WINDOW_END_UTC[0] * 60 + WINDOW_END_UTC[1] - 5

    rows = []
    cur_day = None
    anchor = None          # dict(side, close, sl) for today
    done_today = False
    for i in range(len(bars)):
        d = ts[i].date()
        if d != cur_day:
            cur_day, anchor, done_today = d, None, False
        m = minutes[i]
        if m == anchor_min and i >= 200:
            if close[i] > ema[i]:
                anchor = dict(side="BUY", close=close[i], sl=float(bars.low.iloc[i]))
            elif close[i] < ema[i]:
                anchor = dict(side="SELL", close=close[i], sl=float(bars.high.iloc[i]))
            continue
        if anchor is None or done_today or m <= anchor_min or m > last_trigger_open:
            continue
        if anchor["side"] == "BUY" and close[i] > anchor["close"]:
            if anchor["sl"] < close[i]:
                rows.append(dict(bar_idx=i, signal_ts=ts[i], side="BUY",
                                 stop_price=anchor["sl"]))
            done_today = True
        elif anchor["side"] == "SELL" and close[i] < anchor["close"]:
            if anchor["sl"] > close[i]:
                rows.append(dict(bar_idx=i, signal_ts=ts[i], side="SELL",
                                 stop_price=anchor["sl"]))
            done_today = True
    return pd.DataFrame(rows)


def force_eod(bars: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Re-simulate is overkill: approximate the eod-close variant by truncating
    trades at the 21:55 UTC close of the ENTRY day using bar closes."""
    if len(trades) == 0:
        return trades
    c = bars.close
    out = trades.copy()
    for idx, tr in out.iterrows():
        eod = tr.entry_ts.normalize() + pd.Timedelta(hours=EOD_UTC[0],
                                                     minutes=EOD_UTC[1])
        if tr.exit_ts > eod:
            day_bars = c[(c.index >= tr.entry_ts) & (c.index <= eod)]
            if len(day_bars) == 0:
                continue
            fill = float(day_bars.iloc[-1]) - (COST if tr.side == "buy" else -COST)
            sign = 1.0 if tr.side == "buy" else -1.0
            out.at[idx, "exit"] = fill
            out.at[idx, "exit_ts"] = day_bars.index[-1]
            out.at[idx, "exit_reason"] = "eod_close"
            out.at[idx, "pnl"] = (fill - tr.entry) * tr.lot * VALUE_PER_LOT * sign
    return out


def main():
    bars = load_5m()
    end = bars.index[-1]
    year_ago = end - pd.Timedelta(days=365)
    print(f"NAS100 {bars.index[0]:%Y-%m-%d} .. {end:%Y-%m-%d} ({len(bars)} 5m bars)")

    sig = ema200_signals(bars)
    print(f"signals: {len(sig)} ({(sig.side == 'BUY').sum()} BUY / "
          f"{(sig.side == 'SELL').sum()} SELL)")

    report = ["# EMA 200 NASDAQ Strategy — Research (new_strategies.md #2)", "",
              f"Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. NAS100 5m "
              f"({bars.index[0]:%Y-%m-%d} → {end:%Y-%m-%d}), strict fills, "
              f"cost {COST}/side, fixed lot {LOT}, ${VALUE_PER_LOT}/pt/lot "
              "(PLACEHOLDER spec — verify vs broker ticker).", "",
              "Anchor 13:40 UTC (19:10 IST); trigger close ≤ 15:40 UTC; one "
              "entry/day; SL = anchor extreme; TP = 2R (spec).", "",
              f"Signals: {len(sig)} over {bars.index.normalize().nunique()} days "
              f"({(sig.side == 'BUY').sum()} BUY / {(sig.side == 'SELL').sum()} SELL).",
              ""]

    variants = {}
    t_hold = simulate(bars, sig, rr=2.0, lot=LOT, cost=COST,
                      value_per_lot=VALUE_PER_LOT)
    variants["hold to SL/TP"] = t_hold
    variants["EOD close 21:55 UTC"] = force_eod(bars, t_hold)

    report.append("### Variants (raw, risk-bypassed)")
    report.append("")
    report.append("| Variant | Trades | WR | PF full | Net | MaxDD | PF 2024 | "
                  "PF 2025 | PF 2026 |")
    report.append("|---|---|---|---|---|---|---|---|---|")
    for name, t in variants.items():
        s = stats(t)
        ys = [stats(slice_period(t, f"{y}-01-01", f"{y + 1}-01-01"))
              for y in (2024, 2025, 2026)]
        pf = lambda x: (f"{x['pf']:.2f}" if np.isfinite(x['pf'])
                        else ("inf" if x['n'] else "-"))
        report.append(f"| {name} | {s['n']} | {s['wr']:.0f}% | {pf(s)} | "
                      f"${s['net']:+.2f} | {s['dd']:.2f}% | "
                      f"{pf(ys[0])} | {pf(ys[1])} | {pf(ys[2])} |")
        print(f"{name}: {fmt(s)} | " +
              " | ".join(f"{y} PF {pf(x)}" for y, x in zip((2024, 2025, 2026), ys)))
    report.append("")

    # cost robustness on the primary (hold) variant
    report.append("### Cost robustness (hold variant)")
    report.append("")
    report.append("| Cost/side | PF full | Net | PF 2024 | PF 2025 | PF 2026 |")
    report.append("|---|---|---|---|---|---|")
    for cost in (1.0, 2.0, 3.0):
        t = simulate(bars, sig, rr=2.0, lot=LOT, cost=cost,
                     value_per_lot=VALUE_PER_LOT)
        s = stats(t)
        ys = [stats(slice_period(t, f"{y}-01-01", f"{y + 1}-01-01"))
              for y in (2024, 2025, 2026)]
        pf = lambda x: (f"{x['pf']:.2f}" if np.isfinite(x['pf'])
                        else ("inf" if x['n'] else "-"))
        report.append(f"| {cost:.1f} | {pf(s)} | ${s['net']:+.2f} | "
                      f"{pf(ys[0])} | {pf(ys[1])} | {pf(ys[2])} |")
    report.append("")

    t = t_hold
    d = (t.entry - t.sl).abs()
    report.append(f"Stop distance: median {d.median():.1f} pts "
                  f"(p25 {d.quantile(.25):.1f} / p75 {d.quantile(.75):.1f}) → "
                  f"median risk ~${d.median() * LOT * VALUE_PER_LOT:.0f}/trade at "
                  f"lot {LOT}.\n")

    report.append(deep_dive(t, "Full span (hold, raw)"))
    report.append(deep_dive(slice_period(t, str(year_ago.date()), "2027-01-01"),
                            "Last 12 months (hold, raw)"))
    report.append(deep_dive(slice_period(t, "2026-01-01", "2027-01-01"),
                            "2026 YTD deep dive (hold, raw)"))

    te = simulate(bars, sig, rr=2.0, lot=LOT, cost=COST,
                  value_per_lot=VALUE_PER_LOT, enforce_risk=True)
    report.append(deep_dive(te, "ENFORCED ($150 daily / $250 trailing halt, "
                                f"fixed lot {LOT})"))
    halted = len(te) < len(t)
    report.append(f"Enforced run {'HALTED by trailing-DD kill switch' if halted else 'completed'}: "
                  f"{len(te)} of {len(t)} raw trades taken.\n")
    print(f"ENFORCED: {fmt(stats(te))} ({len(te)}/{len(t)})")

    REPORT.write_text("\n".join(report))
    print(f"Report -> {REPORT}")


if __name__ == "__main__":
    main()
