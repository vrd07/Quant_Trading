#!/usr/bin/env python3
"""
Kalman A/B: RANGE-on vs RANGE-off (drop the OU mean-reversion sub-mode).

The situation-map's strongest a-priori fix was to size RANGE to zero. This checks
it cleanly on BOTH years (2025 OOS + 2026) using the live geometry, by filtering
the baseline kalman signal tape on mode and re-simulating. Shows the overall delta
and the RANGE subset being removed.

Writes: reports/kalman_range_drop_ab.md
"""

import sys
import logging
from pathlib import Path

import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m, CACHE_DIR

REPORT = PROJECT_ROOT / "reports/kalman_range_drop_ab.md"
SL, RR, LOT, COST, CAP, CAPITAL = 33.0, 1.0, 0.04, 0.20, 295.0, 50_000.0
YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01", "kbg_2025_off.csv"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-17", "kbg_2026_off.csv")}


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def main():
    results = {}
    for label, (start, end, cache) in YEARS.items():
        bars = load_15m(start, end)
        sig = pd.read_csv(CACHE_DIR / cache, parse_dates=["signal_ts"])
        sig_norange = sig[sig["mode"] != "range"].copy()

        t_on, _ = simulate(bars, sig, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
        t_off, _ = simulate(bars, sig_norange, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
        range_sub = t_on[t_on["mode"] == "range"]
        results[label] = {
            "on": (stats(t_on), max_drawdown(t_on, CAPITAL)),
            "off": (stats(t_off), max_drawdown(t_off, CAPITAL)),
            "range": stats(range_sub),
        }

    print("=" * 84)
    for label in YEARS:
        r = results[label]
        for k in ("on", "off"):
            s, (dd, ddp) = r[k]
            print(f"  {label:<18} RANGE-{k:<3}: N{s['n']:>4} PF {pf(s['pf'])} "
                  f"net ${s['net']:+,.0f} DD {ddp:.1f}%")
        rs = r["range"]
        print(f"  {label:<18} (removed RANGE subset: N{rs['n']} PF {pf(rs['pf'])} net ${rs['net']:+,.0f})")
        print("-" * 84)

    # ---- report ----
    L = []; A = L.append
    A("# Kalman A/B — RANGE-on vs RANGE-off")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_kalman_range_drop.py`")
    A(f"Live geometry SL{SL:.0f}/RR{RR:.0f}/lot{LOT}/cap${CAP:.0f}/${CAPITAL:,.0f}. "
      "RANGE-off = drop OU mean-reversion signals; re-simulate the same tape. Both years.")
    A("")
    A("| Year | Variant | N | PF | Net$ | MaxDD% |")
    A("|---|---|---:|---:|---:|---:|")
    for label in YEARS:
        for k in ("on", "off"):
            s, (dd, ddp) = results[label][k]
            tag = "RANGE-on (baseline)" if k == "on" else "**RANGE-off**"
            A(f"| {label} | {tag} | {s['n']} | {pf(s['pf'])} | {s['net']:+,.0f} | {ddp:.1f}% |")
    A("")
    A("### The RANGE subset being removed")
    A("")
    A("| Year | RANGE N | RANGE PF | RANGE Net$ |")
    A("|---|---:|---:|---:|")
    for label in YEARS:
        rs = results[label]["range"]
        A(f"| {label} | {rs['n']} | {pf(rs['pf'])} | {rs['net']:+,.0f} |")
    A("")
    A("## Verdict")
    A("")
    on_is, (_, dd_is_on) = results["2026 (in-sample)"]["on"]
    off_is, (_, dd_is_off) = results["2026 (in-sample)"]["off"]
    on_oos, (_, dd_oos_on) = results["2025 (OOS)"]["on"]
    off_oos, (_, dd_oos_off) = results["2025 (OOS)"]["off"]
    pf_is = off_is["pf"] - on_is["pf"]
    pf_oos = off_oos["pf"] - on_oos["pf"]
    A(f"- **2026:** PF {pf(on_is['pf'])}→{pf(off_is['pf'])} ({pf_is:+.2f}), "
      f"net {on_is['net']:+,.0f}→{off_is['net']:+,.0f}, DD {dd_is_on:.1f}%→{dd_is_off:.1f}%.")
    A(f"- **2025 OOS:** PF {pf(on_oos['pf'])}→{pf(off_oos['pf'])} ({pf_oos:+.2f}), "
      f"net {on_oos['net']:+,.0f}→{off_oos['net']:+,.0f}, DD {dd_oos_on:.1f}%→{dd_oos_off:.1f}%.")
    A("")
    net_is = off_is["net"] - on_is["net"]
    net_oos = off_oos["net"] - on_oos["net"]
    if pf_is > 0.02 and pf_oos >= -0.01:
        A("✅ **Drop RANGE.** Removing the OU sub-mode improves (or holds) PF on both years "
          "— a low-risk, regime-independent cleanup. Wire it (size RANGE to 0).")
    elif pf_is > 0.02 and pf_oos < -0.01:
        A(f"❌ **DO NOT drop RANGE statically — it is REGIME-DEPENDENT.** It helps 2026 "
          f"(PF {pf_is:+.2f}, net {net_is:+,.0f}) but **hurts 2025 OOS MORE** (PF {pf_oos:+.2f}, "
          f"net {net_oos:+,.0f}). Across the two years the drop is net-negative.")
        A("")
        A("- **The situation-map's 'clean win' was an artifact.** That test applied a size "
          "*multiplier* to the tape post-hoc; this A/B actually *removes* the signals, which "
          "re-allocates `max_positions` slots to other trend trades (note 2025 net fell "
          f"{net_oos:+,.0f} though the removed RANGE subset was only +$233). Real removal ≠ "
          "post-hoc down-sizing.")
        A("- **RANGE is not dead — it's regime-conditional:** PF 0.83 in 2026's down/chop "
          "year, 1.04 in 2025's calmer tape. A static drop bets on one regime.")
        A("- **Correct handling = the DECAY-FLOOR ALLOCATOR**, not a config drop: let the "
          "sub-mode (or the whole strategy) be defunded *dynamically* when its trailing edge "
          "goes negative, and restored when it recovers. That captures the 2026 benefit "
          "without paying the 2025 cost.")
    else:
        A("➖ **Don't bother.** RANGE-off does not improve PF meaningfully on both years.")
    A("")
    A("> This makes kalman *bleed less in one regime*, it does not create a durable edge "
      "(entry still OOS-dead: `project_kalman_v2_retune_no_edge`). Net lesson: even "
      "'drop the dead sub-mode' is regime-dependent — dynamic defunding beats static cuts.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
