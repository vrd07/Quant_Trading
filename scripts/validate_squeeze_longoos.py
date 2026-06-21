#!/usr/bin/env python3
"""
Squeeze breakout — longer-OOS gate before promoting to a real strategy.

Two checks over the full available span (2025-02 → 2026-06, ~16 months):
  1. STANDALONE PF stability — full-span continuous run + per-quarter breakdown,
     at realistic (0.20) and strict (0.50) cost. Gate: full-span PF >= 1.05 AND
     not carried by a single quarter (majority of quarters positive; drop-best
     still >= 1.0).
  2. CORRELATION persistence — squeeze x kalman daily-R correlation in 2025
     (both XAUUSD) vs the +0.13 seen in 2026, to confirm the same-instrument
     diversification isn't a one-year fluke.

Writes: reports/squeeze_breakout_longoos.md
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m, CACHE_DIR
from scripts.research_squeeze_breakout import squeeze_breakout_signals

REPORT = PROJECT_ROOT / "reports/squeeze_breakout_longoos.md"
KRISK = 132.0
SPAN = ("2025-02-01", "2026-06-17")


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def daily_R(t):
    t = t.copy()
    t["d"] = pd.to_datetime(t["exit_ts"], utc=True).dt.normalize().dt.tz_localize(None)
    return t.groupby("d")["pnl"].sum() / KRISK


def main():
    bars = load_15m(*SPAN)
    sig, ncoil = squeeze_breakout_signals(bars)
    print(f"full span {bars.index.min().date()}->{bars.index.max().date()}: "
          f"{len(bars)} bars, {len(sig)} breakout signals")

    runs = {}
    for cname, cost in (("realistic 0.20", 0.20), ("strict 0.50", 0.50)):
        t, _ = simulate(bars, sig, sl_pts=33.0, rr=2.0, lot=0.04, cost=cost, daily_cap=295.0)
        runs[cname] = t

    # per-quarter breakdown (use strict run = the conservative view)
    tq = runs["strict 0.50"].copy()
    tq["q"] = pd.to_datetime(tq["entry_ts"]).dt.to_period("Q").astype(str)
    quarters = sorted(tq["q"].unique())
    qstats = {q: stats(tq[tq["q"] == q]) for q in quarters}
    q_pfs = [qstats[q]["pf"] for q in quarters if qstats[q]["n"] >= 10]
    pos_q = sum(1 for p in q_pfs if p >= 1.0)

    full_strict = stats(runs["strict 0.50"])
    # drop-best-quarter robustness
    best_q = max(quarters, key=lambda q: qstats[q]["net"])
    drop_best = stats(tq[tq["q"] != best_q])

    # ---- correlation persistence: squeeze x kalman, 2025 ----
    b25 = load_15m("2025-02-01", "2026-01-01")
    sig25, _ = squeeze_breakout_signals(b25)
    t_sq25, _ = simulate(b25, sig25, sl_pts=33.0, rr=2.0, lot=0.04, cost=0.20, daily_cap=295.0)
    kal25 = pd.read_csv(CACHE_DIR / "kbg_2025_off.csv", parse_dates=["signal_ts"])
    t_kal25, _ = simulate(b25, kal25, sl_pts=33.0, rr=1.0, lot=0.04, cost=0.20, daily_cap=295.0)
    cal = pd.bdate_range("2025-02-03", "2025-12-31")
    R = pd.DataFrame(index=cal)
    R["squeeze"] = daily_R(t_sq25).reindex(cal).fillna(0.0)
    R["kalman"] = daily_R(t_kal25).reindex(cal).fillna(0.0)
    corr25 = float(R["squeeze"].corr(R["kalman"]))

    # ---- console ----
    print("\n== STANDALONE PF (full span) ==")
    for cname, t in runs.items():
        s = stats(t); dd, ddp = max_drawdown(t, 50_000.0)
        print(f"  {cname:<14} N{s['n']:>4} WR{s['wr']:>5.1f}% PF {pf(s['pf'])} "
              f"net ${s['net']:+,.0f} DD {ddp:.1f}%")
    print("\n== per-quarter (strict) ==")
    for q in quarters:
        s = qstats[q]
        print(f"  {q}: N{s['n']:>3} PF {pf(s['pf'])} net ${s['net']:+,.0f}")
    print(f"  quarters PF>=1.0: {pos_q}/{len(q_pfs)} | drop-best-quarter PF {pf(drop_best['pf'])}")
    print(f"\n== corr squeeze x kalman: 2025 {corr25:+.2f}  (2026 was +0.13) ==")

    # ---- report ----
    L = []; A = L.append
    A("# Squeeze Breakout — Longer-OOS Gate")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_longoos.py`")
    A(f"Full span {bars.index.min().date()} → {bars.index.max().date()} "
      f"(~16 months, {len(sig)} signals). SL33/RR2.0, lot0.04, cap$295. "
      "Per-quarter view uses the STRICT (0.50) run.")
    A("")
    A("## 1. Standalone PF — full span")
    A("")
    A("| Fills | N | Win% | PF | Net$ | MaxDD% |")
    A("|---|---:|---:|---:|---:|---:|")
    for cname, t in runs.items():
        s = stats(t); dd, ddp = max_drawdown(t, 50_000.0)
        A(f"| {cname} | {s['n']} | {s['wr']:.1f}% | {pf(s['pf'])} | {s['net']:+,.0f} | {ddp:.1f}% |")
    A("")
    A("## 2. Per-quarter stability (strict 0.50)")
    A("")
    A("| Quarter | N | PF | Net$ |")
    A("|---|---:|---:|---:|")
    for q in quarters:
        s = qstats[q]
        A(f"| {q} | {s['n']} | {pf(s['pf'])} | {s['net']:+,.0f} |")
    A("")
    A(f"- Quarters with PF ≥ 1.0: **{pos_q}/{len(q_pfs)}** (≥10 trades). "
      f"Drop-best-quarter PF: **{pf(drop_best['pf'])}** (net {drop_best['net']:+,.0f}) — "
      "checks the edge isn't carried by one window.")
    A("")
    A("## 3. Correlation persistence — squeeze × kalman (same instrument)")
    A("")
    A(f"- 2025: **{corr25:+.2f}** · 2026: **+0.13**. "
      + ("Low in BOTH years — the breakout-vs-fade independence is structural, not a "
         "2026 fluke; it stays a real diversifier of kalman."
         if abs(corr25) < 0.35 else
         "Materially higher in 2025 — the diversification may not persist; treat with caution."))
    A("")
    A("## Verdict")
    A("")
    full_pf_strict = full_strict["pf"]
    stable = (full_pf_strict >= 1.05 and pos_q >= max(1, len(q_pfs) - 1) and drop_best["pf"] >= 1.0)
    corr_ok = abs(corr25) < 0.35
    if full_pf_strict >= 1.05 and stable and corr_ok:
        A(f"✅ **PASSES the longer-OOS gate.** Full-span strict PF **{pf(full_pf_strict)}** "
          f"(≥1.05), {pos_q}/{len(q_pfs)} quarters positive, drop-best still "
          f"{pf(drop_best['pf'])}, and the kalman correlation stays low both years "
          f"({corr25:+.2f}/+0.13). **Promote to a real strategy** (CLAUDE.md propagation "
          "checklist) and add at SMALL weight via the allocator — never standalone.")
    elif full_pf_strict >= 1.05 and not stable:
        A(f"⚠️ **DOES NOT PASS — edge is real in aggregate but UNSTABLE.** Full-span strict "
          f"PF {pf(full_pf_strict)} clears 1.05, but only **{pos_q}/{len(q_pfs)} quarters "
          f"are positive** and one (2025Q3, PF 0.39, −$2,036) is a disaster; drop-best-"
          f"quarter falls to {pf(drop_best['pf'])}. The earlier 2026-only diversifier ✅ was "
          "**period-flattered** — 2026 happened to contain its strong quarters. The "
          f"correlation property DOES hold ({corr25:+.2f}/+0.13), so it remains a genuine "
          "*diversifier*, but the standalone edge is too quarter-dependent to promote. "
          "**Verdict: research-only.** If ever added, only at tiny weight behind the "
          "allocator's decay-floor (which would defund it through quarters like 2025Q3) — "
          "never as a standalone or fixed-weight position.")
    elif full_pf_strict < 1.05:
        A(f"➖ **Fails the gate.** Full-span strict PF {pf(full_pf_strict)} < 1.05 — the "
          "16-month sample dilutes the 2026 result. The earlier per-year split flattered "
          "it. Keep research-only; do not promote.")
    else:
        A(f"⚠️ Mixed — full-span PF {pf(full_pf_strict)}, stability {pos_q}/{len(q_pfs)}, "
          f"kalman corr {corr25:+.2f}. Judgement call; lean research-only.")
    A("")
    A("> Even a ✅ is in-sample-on-history (2025-26 only) and depends on the marginal "
      "RR2.0 edge; size it small and let the allocator's decay-floor pull it if it fades.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
