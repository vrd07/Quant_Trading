#!/usr/bin/env python3
"""
Does the squeeze breakout earn a diversifier slot? Correlation vs the roster.

The squeeze breakout is marginal standalone (2026 PF 1.25 / 2025 OOS 1.07) — but a
low-PF stream that is UNCORRELATED to the live roster can still improve the blend's
Sharpe/DD (project_allweather_portfolio_and_situation_map). This is the final gate:

  1. Daily-return correlation of squeeze vs kalman / london / monday.
     ⚠️ squeeze is on XAUUSD — same instrument as kalman — so watch that pair.
  2. Does adding squeeze to the equal-risk blend improve Sharpe and/or shrink DD?

Earns a small diversifier weight ONLY if low-corr AND it helps the blend. All
in-sample 2026; a real slot also needs a longer OOS holding ≥1.05 (separate test).

Writes: reports/squeeze_diversifier_test.md
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import simulate
from scripts.validate_kalman_buygate import load_15m
from scripts.research_squeeze_breakout import squeeze_breakout_signals
from scripts.research_portfolio_correlation import load_strategies, perf, WIN_START, WIN_END

REPORT = PROJECT_ROOT / "reports/squeeze_diversifier_test.md"
KALMAN_RISK = 132.0    # SL33 * lot0.04 * $100/pt = $132 = 1R


def squeeze_daily_R():
    bars = load_15m("2026-01-01", "2026-06-17")
    sig, _ = squeeze_breakout_signals(bars)
    t, _ = simulate(bars, sig, sl_pts=33.0, rr=2.0, lot=0.04, cost=0.20, daily_cap=295.0)
    t["d"] = pd.to_datetime(t["exit_ts"], utc=True).dt.normalize().dt.tz_localize(None)
    return (t.groupby("d")["pnl"].sum() / KALMAN_RISK)


def pf_str(x):
    return f"{x:.2f}"


def main():
    strat = load_strategies()                       # kalman / london / monday daily R
    strat["squeeze_breakout"] = squeeze_daily_R()

    cal = pd.bdate_range(WIN_START, WIN_END)
    panel = pd.DataFrame(index=cal)
    for n, s in strat.items():
        s = s[(s.index >= cal.min()) & (s.index <= cal.max())]
        panel[n] = s.reindex(cal).fillna(0.0)

    roster = ["kalman_regime", "london_breakout", "monday_drift"]
    names = roster + ["squeeze_breakout"]
    corr = panel[names].corr()

    sq_corr = {r: corr.loc["squeeze_breakout", r] for r in roster}
    avg_abs = float(np.mean([abs(v) for v in sq_corr.values()]))

    blend3 = panel[roster].mean(axis=1)
    blend4 = panel[names].mean(axis=1)
    p3, p4 = perf(blend3), perf(blend4)
    psq = perf(panel["squeeze_breakout"])

    # ---- console ----
    print("=" * 70)
    print("SQUEEZE BREAKOUT — diversifier correlation test (2026 in-sample)")
    print("=" * 70)
    print("\ncorrelation of squeeze vs roster:")
    for r, v in sq_corr.items():
        print(f"  {r:<18} {v:+.2f}")
    print(f"  avg |corr| {avg_abs:.2f}")
    print("\nblend (equal-risk):")
    print(f"  3-way (roster)       Sharpe {p3['sharpe']:.2f}  maxDD {p3['maxDD_R']:+.1f}R  totalR {p3['total_R']:+.1f}")
    print(f"  4-way (+squeeze)     Sharpe {p4['sharpe']:.2f}  maxDD {p4['maxDD_R']:+.1f}R  totalR {p4['total_R']:+.1f}")
    print(f"  squeeze standalone   Sharpe {psq['sharpe']:.2f}  maxDD {psq['maxDD_R']:+.1f}R  totalR {psq['total_R']:+.1f}")

    # ---- report ----
    L = []; A = L.append
    A("# Squeeze Breakout — Diversifier Correlation Test (2026)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_diversifier.py`")
    A("Daily R-streams; squeeze = SL33/RR2.0 all-hours. Equal-risk blends. In-sample 2026.")
    A("")
    A("> Final gate: a marginal stream earns a slot only if it is **uncorrelated** to the "
      "roster AND **improves the blend**. ⚠️ squeeze trades XAUUSD — same instrument as "
      "kalman — so the kalman pair is the one to watch.")
    A("")
    A("## Correlation of squeeze vs roster")
    A("")
    A("| Pair | Correlation |")
    A("|---|---:|")
    for r, v in sq_corr.items():
        flag = "  ⚠️ same instrument" if r == "kalman_regime" else ""
        A(f"| squeeze × {r} | {v:+.2f}{flag} |")
    A(f"| **avg \\|corr\\|** | **{avg_abs:.2f}** |")
    A("")
    A("## Full correlation matrix")
    A("")
    A("| | " + " | ".join(names) + " |")
    A("|" + "---|" * (len(names) + 1))
    for n in names:
        A(f"| **{n}** | " + " | ".join(f"{corr.loc[n, m]:+.2f}" for m in names) + " |")
    A("")
    A("## Does adding it help the blend? (equal-risk, R-units)")
    A("")
    A("| Blend | Sharpe | Max DD (R) | Total R |")
    A("|---|---:|---:|---:|")
    A(f"| 3-way (roster) | {p3['sharpe']:.2f} | {p3['maxDD_R']:+.1f} | {p3['total_R']:+.1f} |")
    A(f"| **4-way (+squeeze)** | {p4['sharpe']:.2f} | {p4['maxDD_R']:+.1f} | {p4['total_R']:+.1f} |")
    A(f"| squeeze standalone | {psq['sharpe']:.2f} | {psq['maxDD_R']:+.1f} | {psq['total_R']:+.1f} |")
    A("")
    A("## Verdict")
    A("")
    low_corr = avg_abs < 0.25 and all(abs(v) < 0.4 for v in sq_corr.values())
    helps = (p4["sharpe"] >= p3["sharpe"] - 0.05) and (p4["maxDD_R"] >= p3["maxDD_R"] - 0.2)
    sharpe_up = p4["sharpe"] > p3["sharpe"]
    dd_better = p4["maxDD_R"] > p3["maxDD_R"]
    A(f"- Squeeze avg |corr| to roster **{avg_abs:.2f}**; kalman pair "
      f"**{sq_corr['kalman_regime']:+.2f}** (same instrument).")
    A(f"- Blend Sharpe {p3['sharpe']:.2f} → {p4['sharpe']:.2f}; "
      f"maxDD {p3['maxDD_R']:+.1f}R → {p4['maxDD_R']:+.1f}R.")
    A("")
    if low_corr and (sharpe_up or dd_better):
        A("✅ **Earns a small diversifier slot (in-sample).** Squeeze is genuinely "
          "uncorrelated to the roster — even being on XAUUSD it does not track kalman "
          "(different logic: it rides breaks where kalman fades) — and adding it improves "
          "the blend. Next and final: a longer-OOS holding ≥1.05, then add at SMALL weight "
          "via the allocator (`allocator_prototype.py`), never standalone. Build it as a "
          "real strategy only at that point (CLAUDE.md propagation checklist).")
    elif low_corr and not (sharpe_up or dd_better):
        A("➖ **Uncorrelated but doesn't help the blend here.** Low correlation is necessary "
          "but not sufficient — on this window its marginal return drags the equal-risk "
          "blend without enough variance reduction to compensate. Keep research-only; "
          "revisit only if a longer OOS lifts its standalone PF.")
    else:
        A("➖ **Too correlated to add value.** It moves with the existing roster (likely "
          "kalman, same instrument), so it brings no diversification — and it isn't a "
          "standalone money-maker. Kill as a roster addition; research-only.")
    A("")
    A("> In-sample 2026 only. Even a ✅ here is provisional until a longer OOS confirms "
      "both the standalone PF (≥1.05) and the low correlation hold.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
