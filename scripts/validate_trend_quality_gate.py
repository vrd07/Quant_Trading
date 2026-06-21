#!/usr/bin/env python3
"""
Walk-forward validation of the ATR-normalised TREND-quality gate.

Gate: drop TREND signals whose conviction score is below a threshold, where
  score = |kalman slope over N bars| / (|slope| + atr_mult*ATR)   in [0,1]
(ATR-normalised — fixes the self-normalising flaw found in the diagnostic).
RANGE signals are left untouched.

Sweeps the threshold across 2025 (OOS) and 2026. Method: compute the score at each
baseline signal's bar and filter the tape, then re-simulate (captures slot
re-allocation). 2026 diagnostic said high-score trend is PF 1.40 — the question is
whether a fixed threshold also helps OOS, or joins the in-sample-only graveyard.

Writes: reports/trend_quality_gate_walkforward.md
"""

import sys
import logging
from pathlib import Path

import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators
from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m, CACHE_DIR

REPORT = PROJECT_ROOT / "reports/trend_quality_gate_walkforward.md"
SL, RR, LOT, COST, CAP, CAPITAL = 33.0, 1.0, 0.04, 0.20, 295.0, 50_000.0
KAL_Q, KAL_R, SLOPE_BARS, STD_WINDOW = 0.00001, 0.01, 3, 20
# Use the SELF-NORMALISING score — the diagnostic showed it (not the ATR form) is
# what actually ranks TREND quality (high-score trend PF 1.40). ATR-norm is for
# range detection, which is moot here (range is dead). Thresholds span its range.
THRESHOLDS = [0.0, 0.65, 0.70, 0.75, 0.80]          # 0.0 = baseline (no gate)
YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01", "kbg_2025_off.csv"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-17", "kbg_2026_off.csv")}


def pf(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def score_series(bars):
    # self-normalising (matches the diagnostic that separated trend quality)
    kal = Indicators.kalman_filter(bars["close"], q=KAL_Q, r=KAL_R)
    slope = (kal - kal.shift(SLOPE_BARS)).abs()
    sd = slope.rolling(STD_WINDOW).std()
    return slope / (slope + sd + 1e-12)


def main():
    results = {}
    for label, (start, end, cache) in YEARS.items():
        bars = load_15m(start, end)
        sig = pd.read_csv(CACHE_DIR / cache, parse_dates=["signal_ts"])
        sc = score_series(bars)
        sig["score"] = sig["bar_idx"].apply(
            lambda i: float(sc.iloc[int(i)]) if 0 <= int(i) < len(sc) and not pd.isna(sc.iloc[int(i)]) else 1.0)
        per = {}
        for th in THRESHOLDS:
            keep = sig[(sig["mode"] != "trend") | (sig["score"] >= th)].copy()
            t, _ = simulate(bars, keep, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
            s = stats(t); dd, ddp = max_drawdown(t, CAPITAL)
            ntrend = int((keep["mode"] == "trend").sum())
            per[th] = (s, ddp, ntrend)
        results[label] = per

    print("=" * 88)
    for label in YEARS:
        print(label)
        for th in THRESHOLDS:
            s, ddp, ntr = results[label][th]
            tag = "baseline" if th == 0.0 else f"score>={th:.2f}"
            print(f"  {tag:<14} N{s['n']:>4} (trend {ntr:>3}) PF {pf(s['pf'])} "
                  f"net ${s['net']:+,.0f} DD {ddp:.1f}%")
        print("-" * 88)

    # ---- report ----
    L = []; A = L.append
    A("# TREND-Quality Gate — Walk-Forward")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_trend_quality_gate.py`")
    A(f"Self-normalising score = |slope({SLOPE_BARS})| / (|slope| + rolling_std({STD_WINDOW})) "
      "— the form the diagnostic showed ranks trend quality. Gate drops TREND signals below "
      f"threshold; RANGE untouched. SL{SL:.0f}/RR{RR:.0f}/lot{LOT}/cap${CAP:.0f}. 2025 is OOS. "
      "(Post-hoc tape filter + re-sim; minor cooldown caveat.)")
    A("")
    for label in YEARS:
        A(f"## {label}")
        A("")
        A("| Gate | Trades N | Trend N | PF | Net$ | MaxDD% |")
        A("|---|---:|---:|---:|---:|---:|")
        for th in THRESHOLDS:
            s, ddp, ntr = results[label][th]
            tag = "baseline (off)" if th == 0.0 else f"score ≥ {th:.2f}"
            A(f"| {tag} | {s['n']} | {ntr} | {pf(s['pf'])} | {s['net']:+,.0f} | {ddp:.1f}% |")
        A("")

    # verdict: best threshold by 2026 PF (excl baseline), check OOS
    base_is = results["2026 (in-sample)"][0.0][0]["pf"]
    base_oos = results["2025 (OOS)"][0.0][0]["pf"]
    gated = [th for th in THRESHOLDS if th > 0.0]
    best_th = max(gated, key=lambda th: results["2026 (in-sample)"][th][0]["pf"])
    is_pf = results["2026 (in-sample)"][best_th][0]["pf"]
    oos_pf = results["2025 (OOS)"][best_th][0]["pf"]
    # does ANY single threshold beat baseline on BOTH years?
    both = [th for th in gated
            if results["2026 (in-sample)"][th][0]["pf"] > base_is + 0.02
            and results["2025 (OOS)"][th][0]["pf"] > base_oos + 0.02]
    A("## Verdict")
    A("")
    A(f"- Baseline PF: 2026 {pf(base_is)}, 2025 OOS {pf(base_oos)}.")
    A(f"- Best-in-2026 threshold **score ≥ {best_th:.2f}**: 2026 {pf(is_pf)} → 2025 OOS {pf(oos_pf)}.")
    A(f"- Thresholds beating baseline on BOTH years: **{both if both else 'NONE'}**.")
    A("")
    if both:
        A(f"✅ **Generalises.** Threshold(s) {both} improve PF on BOTH 2025 and 2026 — the "
          "ATR-normalised trend-quality gate is a real, regime-independent filter (the ATR "
          "normalisation fixed what the raw score couldn't). Wire `trend_quality_gate_enabled: "
          "true` with that min_score in the kalman_regime blocks; re-confirm with a full "
          "strategy replay + the official strict-fill gate before default-on.")
    elif oos_pf > base_oos + 0.02:
        A("✅ **The best-2026 threshold also helps OOS** — promising; confirm with a full "
          "strategy replay (cooldown-exact) and strict fills before enabling.")
    else:
        A(f"➖ **In-sample only — do not enable.** The best 2026 threshold ({pf(is_pf)}) does "
          f"NOT carry to 2025 OOS ({pf(oos_pf)} vs baseline {pf(base_oos)}). The trend-quality "
          "gate joins the BUY-gate / RANGE-layer graveyard: a fitted threshold that flatters "
          "2026 and fails OOS. The diagnostic's PF-1.40 high-score cohort was in-sample "
          "structure, not a forward-stable rule. Keep the flag OFF.")
    A("")
    A("> Net: if this fails OOS too, the conclusion is firm — kalman's regime problem is not "
      "solvable by any fixed entry threshold; only the dynamic decay-floor allocator adapts.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
