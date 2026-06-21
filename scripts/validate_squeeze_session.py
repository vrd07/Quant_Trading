#!/usr/bin/env python3
"""
Squeeze breakout — the promote-vs-kill test: SESSION filter × STRICT fills.

The bare prototype was marginal (SL33/RR2.0: 2026 PF 1.27 → 2025 OOS 1.05, below
the 1.10 bar, slippage-fragile). Prior research found gold's breakout edge lives in
the London/NY session, not the pattern (project_breakout_15m_research). So this:

  1. SESSION filter — keep only breakouts whose signal hour is in London (07-11),
     NY (12-16), or London+NY (07-16) UTC.
  2. STRICT fills — re-price at cost 0.50/side (vs 0.20 realistic): breakouts are
     chased, so this is the honest slippage stress.

Decision: promote ONLY if a session variant clears ~1.10 PF on BOTH years AT STRICT
cost with adequate sample; else kill (research-only).

Writes: reports/squeeze_breakout_session_test.md
"""

import sys
import logging
from pathlib import Path

import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m
from scripts.research_squeeze_breakout import squeeze_breakout_signals

REPORT = PROJECT_ROOT / "reports/squeeze_breakout_session_test.md"
SL, RR, LOT, CAP, CAPITAL = 33.0, 2.0, 0.04, 295.0, 50_000.0      # the candidate cell
YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-17")}
SESSIONS = {
    "all hours": None,
    "London 07-11": set(range(7, 12)),
    "NY 12-16": set(range(12, 17)),
    "London+NY 07-16": set(range(7, 17)),
}
COSTS = {"realistic 0.20": 0.20, "strict 0.50": 0.50}


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def filt_session(sig, hours):
    if hours is None or len(sig) == 0:
        return sig
    h = pd.to_datetime(sig["signal_ts"]).dt.hour
    return sig[h.isin(hours)].copy()


def main():
    # generate signals once per year (cache in-memory)
    sigs, barsy = {}, {}
    for label, (start, end) in YEARS.items():
        bars = load_15m(start, end)
        sig, _ = squeeze_breakout_signals(bars)
        sigs[label], barsy[label] = sig, bars
        print(f"{label}: {len(sig)} raw breakout signals")

    # matrix: session × cost, both years, at SL33/RR2.0
    res = {}   # (session, cost_label, year) -> (stats, dd)
    for sname, hours in SESSIONS.items():
        for cname, cost in COSTS.items():
            for label in YEARS:
                sig = filt_session(sigs[label], hours)
                if len(sig) == 0:
                    res[(sname, cname, label)] = (stats(pd.DataFrame()), (0.0, 0.0))
                    continue
                t, _ = simulate(barsy[label], sig, sl_pts=SL, rr=RR, lot=LOT,
                                cost=cost, daily_cap=CAP)
                res[(sname, cname, label)] = (stats(t), max_drawdown(t, CAPITAL))

    # ---- console ----
    print("\n" + "=" * 88)
    print(f"SQUEEZE BREAKOUT — session × strict-fill (SL{SL:.0f}/RR{RR:.0f})")
    print("=" * 88)
    for sname in SESSIONS:
        for cname in COSTS:
            cells = []
            for label in YEARS:
                s, (dd, ddp) = res[(sname, cname, label)]
                cells.append(f"{label.split()[0]} PF {pf(s['pf'])} (N{s['n']}, ${s['net']:+,.0f})")
            print(f"  {sname:<18} {cname:<14} | " + " | ".join(cells))
        print("-" * 88)

    # ---- report ----
    L = []; A = L.append
    A("# Squeeze Breakout — Session Filter × Strict Fills (promote-vs-kill)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_session.py`")
    A(f"Candidate cell **SL{SL:.0f}/RR{RR:.0f}**, lot{LOT}/cap${CAP:.0f}/${CAPITAL:,.0f}. "
      "Session = UTC signal hour; strict = 0.50/side cost (breakouts are chased). 2025 OOS.")
    A("")
    A("> Decision rule: **promote only if a session variant clears ≈1.10 PF on BOTH years "
      "at STRICT cost** with N≥20; else research-only.")
    A("")
    for cname in COSTS:
        A(f"## Fills: {cname}")
        A("")
        A("| Session | 2026 PF | 2026 N | 2026 Net$ | 2025 OOS PF | 2025 N | 2025 Net$ |")
        A("|---|---:|---:|---:|---:|---:|---:|")
        for sname in SESSIONS:
            s_is, _ = res[(sname, cname, "2026 (in-sample)")]
            s_oos, _ = res[(sname, cname, "2025 (OOS)")]
            A(f"| {sname} | {pf(s_is['pf'])} | {s_is['n']} | {s_is['net']:+,.0f} | "
              f"{pf(s_oos['pf'])} | {s_oos['n']} | {s_oos['net']:+,.0f} |")
        A("")

    # verdict: best session at STRICT cost
    strict = "strict 0.50"
    cand = []
    for sname in SESSIONS:
        s_is = res[(sname, strict, "2026 (in-sample)")][0]
        s_oos = res[(sname, strict, "2025 (OOS)")][0]
        cand.append((sname, s_is, s_oos))
    # rank by min(both-year PF) among adequately-sampled
    ok = [c for c in cand if c[1]["n"] >= 20 and c[2]["n"] >= 20]
    A("## Verdict")
    A("")
    if not ok:
        best = None
        A("➖ **Sample too thin once filtered** — the session filter cuts breakouts below "
          "a tradeable count. Kill: research-only.")
    else:
        best = max(ok, key=lambda c: min(c[1]["pf"], c[2]["pf"]))
        sname, s_is, s_oos = best
        worst = min(s_is["pf"], s_oos["pf"])
        A(f"- Best at strict cost: **{sname}** — 2026 PF {pf(s_is['pf'])} (N{s_is['n']}), "
          f"2025 OOS PF {pf(s_oos['pf'])} (N{s_oos['n']}).")
        A("")
        if worst >= 1.10:
            A("✅ **PROMOTE.** Clears 1.10 on BOTH years at strict fills — the session "
              "filter rescues the squeeze breakout, exactly as the prior research predicted "
              "(the edge lives in the session). Next: build it as a real strategy per the "
              "CLAUDE.md propagation checklist (registry → STRATEGY_WEIGHTS → configs → "
              "tests), wired with this session gate + RR2.0, default-enabled only after a "
              "final run through the official strict-fill backtest gate.")
        elif worst >= 1.00:
            A("⚠️ **Still marginal — KILL as a standalone.** The session filter helps but "
              "does not lift BOTH years clear of the 1.10 bar at strict cost; the worst "
              f"year is {pf(worst)}, inside slippage noise. The edge is real-ish but too "
              "thin to carry trading costs reliably. Keep as research; do not wire live.")
        else:
            A("➖ **KILL.** Even the best session variant drops below 1.0 on one year at "
              "strict cost. Consistent with `project_breakout_15m_research` — gold intraday "
              "mean-reverts; the squeeze pre-condition is not enough. Research-only.")
    A("")
    A("> Whatever the result, this stays separate from the OOS-dead Kalman entry. A pass "
      "would be a NEW standalone strategy, sized small and added to the uncorrelated "
      "roster (`project_allweather_portfolio_and_situation_map`), not bolted onto Kalman.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
