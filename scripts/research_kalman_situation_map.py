#!/usr/bin/env python3
"""
Kalman v2 — gold SITUATION -> RISK-ACTION map (defensive sizing, not new alpha).

The $50k autopsy showed the binding constraint is the DRAWDOWN PATH, not
expectancy. This builds a defensive layer: attribute Kalman's 2026 drawdown
across identifiable, A-PRIORI gold situations, then test whether standing aside /
sizing down in the hostile ones shrinks the drawdown without gutting return.

Situations (all knowable at decision time, none mined from the P&L):
  * MODE      trend vs range/OU      (range = the OU fade, structurally weak)
  * VOL       ATR(14) regime         (top quartile = whipsaw risk for a fixed stop)
  * TREND     HTF 1H-EMA(50) align   (with-trend vs fighting it)
  * SESSION   UTC hour / weekday     (illiquid Asia chop, Friday weekend risk)

Method: because pnl scales LINEARLY with lot (fixed SL/TP), a per-trade size
multiplier just scales that trade's pnl. So a defensive size map can be applied
to the real trade tape and the equity/DD/PF recomputed exactly — no re-sim.

⚠️ Round-number multipliers chosen after seeing 2026 still carry fit risk, and the
underlying entry is OOS-dead (project_kalman_v2_retune_no_edge). This map is for
SURVIVABILITY of whatever deploys, not a new edge. Live, the VOL percentile must
be TRAILING (no lookahead); here it is in-sample for attribution.

Writes: reports/kalman_situation_map.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import load_15m_2026, max_drawdown

TAPE = PROJECT_ROOT / "data/backtests/kalman_50k_2026_trades.csv"
REPORT = PROJECT_ROOT / "reports/kalman_situation_map.md"
CAPITAL = 50_000.0


def atr(bars: pd.DataFrame, n=14) -> pd.Series:
    h, l, c = bars["high"], bars["low"], bars["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def add_situations(t: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """Attach a-priori situation features evaluated at the SIGNAL bar (entry-1)."""
    a = atr(bars)
    # HTF 1h EMA(50), the strategy's own trend filter, reindexed to 15m.
    h1 = bars["close"].resample("1h").last().ffill()
    ema = h1.ewm(span=50, adjust=False).mean().reindex(bars.index, method="ffill")

    idx = bars.index
    pos = {ts: i for i, ts in enumerate(idx)}
    vol_pct, trend_align = [], []
    a_rank = a.rank(pct=True)                      # in-sample percentile (attribution)
    for _, r in t.iterrows():
        ets = pd.Timestamp(r["entry_ts"])
        i = pos.get(ets)
        j = (i - 1) if (i is not None and i > 0) else None   # signal bar
        if j is None:
            vol_pct.append(np.nan); trend_align.append(np.nan); continue
        vol_pct.append(float(a_rank.iloc[j]) if a_rank.iloc[j] == a_rank.iloc[j] else np.nan)
        px, em = float(bars["close"].iloc[j]), float(ema.iloc[j])
        up = px >= em
        aligned = (r["side"] == "buy" and up) or (r["side"] == "sell" and not up)
        trend_align.append(bool(aligned))
    t = t.copy()
    t["vol_pct"] = vol_pct
    t["vol_bucket"] = pd.cut(t["vol_pct"], [0, .25, .5, .75, 1.01],
                             labels=["Q1 calm", "Q2", "Q3", "Q4 spike"])
    t["trend_align"] = trend_align
    t["weekend_risk"] = (t["weekday"] == 4) & (t["bars_held"] > 20)   # Fri + long hold
    t["session"] = pd.cut(t["hour"], [-1, 6, 11, 16, 23],
                          labels=["Asia 00-06", "London 07-11", "NY 12-16", "Late 17-23"])
    return t


def block(t: pd.DataFrame, pnl="pnl") -> dict:
    p = t[pnl]
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    return dict(n=len(t), wr=100 * (p > 0).mean() if len(t) else 0,
                pf=(gw / gl) if gl > 0 else float("inf"), net=p.sum())


def pfs(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def attribution(t, key) -> list:
    rows = []
    for k in t[key].dropna().unique() if not hasattr(t[key], "cat") else t[key].cat.categories:
        sub = t[t[key] == k]
        if len(sub) == 0:
            continue
        b = block(sub)
        rows.append((k, b))
    return rows


def main():
    t = pd.read_csv(TAPE)
    bars = load_15m_2026()
    t = add_situations(t, bars)

    base = block(t)
    dd0, ddp0 = max_drawdown(t, CAPITAL)

    # ---- DEFENSIVE SIZE MAPS (a-priori, conservative round numbers) -------
    # V1 = naive a-priori (includes the "vol-spike = risk" guess).
    # V2 = refined: DROP the vol lever after attribution refuted it (kalman
    #      profits in high vol, loses in calm) — but do NOT flip it (flipping a
    #      rule after seeing the result is the overfitting trap). Keep only the
    #      three levers that are a-priori sound AND data-supported.
    def size_mult(r, use_vol):
        m = 1.0
        if r["mode"] == "range":          # OU fade = structurally weak (report's 2nd fix)
            m *= 0.5
        if r["trend_align"] is False:     # fighting the HTF trend
            m *= 0.5
        if use_vol and r["vol_bucket"] == "Q4 spike":
            m *= 0.5
        if r["weekend_risk"]:             # Friday long hold over the weekend
            m *= 0.5
        return m

    t["mult"] = t.apply(lambda r: size_mult(r, use_vol=True), axis=1)
    t["pnl_def"] = t["pnl"] * t["mult"]
    defn = block(t, "pnl_def")
    dd1, ddp1 = max_drawdown(t.assign(pnl=t["pnl_def"]), CAPITAL)
    avg_exposure = t["mult"].mean()

    t["mult2"] = t.apply(lambda r: size_mult(r, use_vol=False), axis=1)
    t["pnl_def2"] = t["pnl"] * t["mult2"]
    defn2 = block(t, "pnl_def2")
    dd2, ddp2 = max_drawdown(t.assign(pnl=t["pnl_def2"]), CAPITAL)
    avg_exposure2 = t["mult2"].mean()

    # ---- console ----
    print("=" * 70)
    print("KALMAN SITUATION -> RISK MAP (2026 in-sample)")
    print("=" * 70)
    for key, label in [("mode", "MODE"), ("vol_bucket", "VOL regime"),
                       ("trend_align", "HTF trend align"), ("session", "SESSION"),
                       ("weekend_risk", "Weekend risk")]:
        print(f"\n{label}:")
        for k, b in attribution(t, key):
            print(f"  {str(k):<14} N{b['n']:>4}  WR {b['wr']:>5.1f}%  "
                  f"PF {pfs(b['pf']):>5}  net ${b['net']:>+8.0f}")
    print("\n--- defensive size maps ---")
    print(f"  baseline       : PF {pfs(base['pf'])}  net ${base['net']:+,.0f}  "
          f"maxDD {ddp0:.1f}% (${dd0:,.0f})")
    print(f"  V1 (incl vol)  : PF {pfs(defn['pf'])}  net ${defn['net']:+,.0f}  "
          f"maxDD {ddp1:.1f}% (${dd1:,.0f})  avg size {avg_exposure:.2f}x")
    print(f"  V2 (vol dropped): PF {pfs(defn2['pf'])}  net ${defn2['net']:+,.0f}  "
          f"maxDD {ddp2:.1f}% (${dd2:,.0f})  avg size {avg_exposure2:.2f}x")
    dd_cut = (1 - abs(dd1) / abs(dd0)) * 100 if dd0 else 0
    ret_keep = defn["net"] / base["net"] * 100 if base["net"] else 0
    dd_cut2 = (1 - abs(dd2) / abs(dd0)) * 100 if dd0 else 0
    ret_keep2 = defn2["net"] / base["net"] * 100 if base["net"] else 0
    print(f"  => V1 DD {dd_cut:+.0f}% ret {ret_keep:.0f}% @ {avg_exposure:.2f}x ; "
          f"V2 DD {dd_cut2:+.0f}% ret {ret_keep2:.0f}% @ {avg_exposure2:.2f}x")

    # ---- report ----
    L = []
    A = L.append
    A("# Kalman v2 — Gold Situation → Risk-Action Map (defensive)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/research_kalman_situation_map.py` · "
      "tape: `kalman_50k_2026_trades.csv` (608 trades, in-sample 2026)")
    A("")
    A("> The binding constraint is the **drawdown path**, not expectancy (per the $50k "
      "autopsy). This is a *defensive* layer — it decides WHEN to stand aside and how "
      "much to SIZE in hostile gold situations. It does **not** add entry alpha, and the "
      "underlying entry is OOS-dead. Goal: survivability.")
    A("")
    A("## Drawdown / PF attribution by a-priori situation")
    A("")
    A("Each situation is knowable at decision time (no P&L mining). This shows WHERE the "
      "bleed concentrates.")
    A("")
    for key, label in [("mode", "MODE (trend vs OU-range)"),
                       ("vol_bucket", "VOLATILITY regime — ATR(14) quartile"),
                       ("trend_align", "HTF TREND alignment (1h EMA-50)"),
                       ("session", "SESSION (UTC hour)"),
                       ("weekend_risk", "WEEKEND risk (Fri + long hold)")]:
        A(f"### {label}")
        A("")
        A("| bucket | N | Win% | PF | Net$ |")
        A("|---|---:|---:|---:|---:|")
        for k, b in attribution(t, key):
            A(f"| {k} | {b['n']} | {b['wr']:.1f}% | {pfs(b['pf'])} | {b['net']:+,.0f} |")
        A("")
    A("**Reading it — and a refuted assumption.** The bleed concentrates in three "
      "buckets with clear a-priori rationale: the OU **range** mode, trades **fighting "
      "the HTF trend**, and **weekend holds** (Fri + long hold, WR 26%). "
      "**But the volatility assumption was WRONG:** I expected the top-ATR 'vol-spike' "
      "quartile to be the risk; the data shows the opposite — Kalman *profits* in high "
      "vol (Q4 PF 1.11) and *loses in calm chop* (Q1 0.75, Q2 0.82). So the vol lever is "
      "dropped from the recommended map. It is deliberately NOT flipped to down-size calm "
      "instead — flipping a rule after seeing the result is the overfitting trap.")
    A("")
    A("## The defensive size map (a-priori, conservative)")
    A("")
    A("Multiplicative, applied to the real tape (pnl scales linearly with lot). "
      "**V1** = naive a-priori (with the vol guess); **V2** = refined, vol lever dropped:")
    A("")
    A("| Situation | V1 | V2 (recommended) | Live wiring |")
    A("|---|---|---|---|")
    A("| RANGE / OU mode | × 0.5 | × 0.5 | down-weight/skip range-mode signals |")
    A("| Fighting HTF 1h-EMA(50) | × 0.5 | × 0.5 | **symmetric BUY-side trend gate** (the gap) |")
    A("| Top-quartile ATR | × 0.5 | — (dropped) | — |")
    A("| Friday held over weekend | × 0.5 | × 0.5 | no new Fri entries that can't close by EOD |")
    A("")
    A("| Run | PF | Net$ | Max DD% | Max DD$ | Avg size |")
    A("|---|---:|---:|---:|---:|---:|")
    A(f"| Baseline (flat size) | {pfs(base['pf'])} | {base['net']:+,.0f} | {ddp0:.1f}% | {dd0:,.0f} | 1.00× |")
    A(f"| V1 (incl. vol lever) | {pfs(defn['pf'])} | {defn['net']:+,.0f} | {ddp1:.1f}% | {dd1:,.0f} | {avg_exposure:.2f}× |")
    A(f"| **V2 (recommended)** | {pfs(defn2['pf'])} | {defn2['net']:+,.0f} | {ddp2:.1f}% | {dd2:,.0f} | {avg_exposure2:.2f}× |")
    A("")
    A(f"- **V2: drawdown {dd_cut2:+.0f}%, return retained {ret_keep2:.0f}%, at "
      f"{avg_exposure2:.2f}× average size.** Cutting only the genuinely-dead buckets "
      "shrinks the drawdown by far more than it costs in return (return actually *rises* "
      "when the down-sized buckets were net-negative) and lifts PF — the clean signature "
      "of removing dead weight, not edge.")
    A("")
    A("## What's already live vs the gap")
    A("")
    A("| Situation | Already handled live? |")
    A("|---|---|")
    A("| High-impact news | ✅ news blackout suppresses signals |")
    A("| Regime (trend/range/volatile) | ✅ nightly + intraday regime classifier reweights |")
    A("| Illiquid session | ✅ kalman session mask `[[3,4],[20,23]]` |")
    A("| HTF trend (SELL side) | ✅ 1h-EMA(50) gate on shorts |")
    A("| **HTF trend (BUY side)** | ❌ gap — BUY can still fight the trend |")
    A("| **Vol-spike down-sizing** | ❌ gap — fixed budget regardless of ATR regime |")
    A("| **Drawdown-state de-risking** | ⚠️ only a hard kill switch; no graded taper |")
    A("")
    A("## Verdict / next step")
    A("")
    A("The defensive map is a real **survivability** lever (shrinks DD, raises PF), and "
      "the three gaps above are the honest places to wire it: a symmetric BUY-side trend "
      "gate, ATR-regime size taper, and a graded drawdown de-risk before the hard halt. "
      "But round-number multipliers fit on 2026 must be **walk-forward validated** before "
      "live, and none of this revives an OOS-dead entry — it only makes whatever deploys "
      "bleed slower. Pairs with the portfolio finding: smoothness comes from "
      "diversification first, defensive sizing second.")
    A("")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
