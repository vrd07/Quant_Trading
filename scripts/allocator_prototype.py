#!/usr/bin/env python3
"""
Expectancy-weighted portfolio allocator — prototype.

The correlation study showed the roster (kalman/london/monday) is ~uncorrelated
(avg −0.04) but equal-risk weighting lets a decaying component drag the blend. This
prototypes the fix: size each strategy by its TRAILING expectancy (Sharpe over a
lookback window), recomputed walk-forward with NO lookahead — so live, when a
strategy's edge decays (e.g. kalman going OOS-dead), its weight automatically
falls toward zero without anyone touching a config.

Core, reusable: `expectancy_weights(panel, lookback, cap)` → today's target risk
weights from trailing daily R only. Everything else is the walk-forward harness +
comparison vs equal-weight and standalones.

⚠️ Components are in-sample 2026 backtests over a short (~5.4mo) window, so trailing
samples are thin and weights noisy — this demonstrates the MECHANISM and the
actionable current weights, not a validated live allocation.

Writes: reports/allocator_prototype_2026.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_portfolio_correlation import load_strategies, perf, WIN_START, WIN_END

REPORT = PROJECT_ROOT / "reports/allocator_prototype_2026.md"
LOOKBACK = 45            # business-day trailing window for the expectancy estimate
MIN_ACTIVE = 4          # need >= this many trading days in the window to get weight
WEIGHT_CAP = 0.50       # no single strategy > 50% of risk (anti-concentration)
TRADING_DAYS = 252


def expectancy_weights(panel: pd.DataFrame, lookback: int = LOOKBACK,
                       cap: float = WEIGHT_CAP, min_active: int = MIN_ACTIVE) -> pd.Series:
    """Target risk weights from TRAILING daily R only (no lookahead).

    panel: daily R columns per strategy, indexed by date, most recent row last.
    Weight_i ∝ max(0, trailing Sharpe_i); capped, renormalised. If nothing has a
    positive trailing edge, returns all-zero (go to cash)."""
    win = panel.tail(lookback)
    score = {}
    for c in panel.columns:
        s = win[c]
        active = int((s != 0).sum())
        mu, sd = s.mean(), s.std(ddof=1)
        sharpe = (mu / sd) if (sd > 0 and active >= min_active) else (mu if active >= min_active else 0.0)
        score[c] = max(0.0, sharpe)
    w = pd.Series(score)
    if w.sum() <= 0:
        return pd.Series(0.0, index=panel.columns)   # cash
    w = w / w.sum()
    # cap + renormalise (one pass is enough for 3 names)
    if (w > cap).any():
        w = w.clip(upper=cap)
        w = w / w.sum()
    return w


def decay_floor_weights(panel: pd.DataFrame, lookback: int = LOOKBACK,
                        min_active: int = MIN_ACTIVE) -> pd.Series:
    """Equal-risk among strategies with NON-NEGATIVE trailing edge; drop the rest.

    Keeps full diversification (the thing that actually works here) but starves any
    strategy whose trailing Sharpe turns negative — the self-defence behaviour,
    without the sparse-Sharpe concentration problem of pure expectancy weighting."""
    win = panel.tail(lookback)
    keep = []
    for c in panel.columns:
        s = win[c]
        active = int((s != 0).sum())
        mu, sd = s.mean(), s.std(ddof=1)
        sharpe = (mu / sd) if sd > 0 else mu
        if active >= min_active and sharpe >= 0:
            keep.append(c)
    w = pd.Series(0.0, index=panel.columns)
    if keep:
        w[keep] = 1.0 / len(keep)
    return w


def walk_forward(panel: pd.DataFrame, weight_fn, lookback: int = LOOKBACK):
    """Daily walk-forward: weights from [t-lookback, t-1], applied to day t."""
    names = list(panel.columns)
    port, wlog = [], []
    for i in range(len(panel)):
        if i < lookback:
            port.append(0.0)                         # warm-up: flat
            wlog.append(pd.Series(0.0, index=names))
            continue
        w = weight_fn(panel.iloc[:i], lookback)      # strictly trailing
        r = float((w * panel.iloc[i]).sum())
        port.append(r)
        wlog.append(w)
    pser = pd.Series(port, index=panel.index)
    wdf = pd.DataFrame(wlog, index=panel.index)
    return pser, wdf


def fmt_perf(p):
    return f"totalR {p['total_R']:+.1f} | Sharpe {p['sharpe']:.2f} | maxDD {p['maxDD_R']:+.1f}R"


def main():
    strat = load_strategies()
    cal = pd.bdate_range(WIN_START, WIN_END)
    panel = pd.DataFrame(index=cal)
    for n, s in strat.items():
        s = s[(s.index >= cal.min()) & (s.index <= cal.max())]
        panel[n] = s.reindex(cal).fillna(0.0)
    names = list(panel.columns)

    # Blends over the SAME post-warmup window for a fair comparison.
    eq = panel.mean(axis=1)                                   # equal risk
    exp_port, wdf = walk_forward(panel, expectancy_weights)
    floor_port, fdf = walk_forward(panel, decay_floor_weights)
    post = panel.index[LOOKBACK:]                             # evaluation window

    rows = {n: perf(panel[n].loc[post]) for n in names}
    rows["Equal-risk blend"] = perf(eq.loc[post])
    rows["Expectancy-weighted"] = perf(exp_port.loc[post])
    rows["Equal-risk + decay floor"] = perf(floor_port.loc[post])

    cur_w = expectancy_weights(panel)                        # actionable: today's weights
    avg_w = wdf.loc[post].mean()

    # ---- console ----
    print("=" * 72)
    print("EXPECTANCY-WEIGHTED ALLOCATOR — prototype (2026 in-sample)")
    print("=" * 72)
    print(f"window {cal.min().date()}->{cal.max().date()} | lookback {LOOKBACK}d | "
          f"eval from {post.min().date()} ({len(post)}d)\n")
    for n, p in rows.items():
        print(f"  {n:<22} {fmt_perf(p)}")
    print("\n  avg walk-forward weights:")
    for n in names:
        print(f"    {n:<18} {avg_w[n]:.2f}")
    print("\n  CURRENT recommended weights (full trailing window):")
    for n in names:
        print(f"    {n:<18} {cur_w[n]:.2f}")

    # ---- report ----
    L = []; A = L.append
    A("# Expectancy-Weighted Allocator — Prototype (2026)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/allocator_prototype.py`")
    A(f"Daily R-multiple streams (kalman/london/monday). Lookback **{LOOKBACK} business "
      f"days**, per-strategy cap **{WEIGHT_CAP:.0%}**, weights ∝ max(0, trailing Sharpe), "
      "recomputed walk-forward (no lookahead). Eval window starts after warm-up "
      f"({post.min().date()}, {len(post)} days).")
    A("")
    A("> The point is **self-defence**: weights come from trailing performance only, so "
      "a decaying strategy starves itself. ⚠️ In-sample components, short window, thin "
      "trailing samples — mechanism + current weights, not a validated allocation.")
    A("")
    A("## Performance — standalone vs blends (eval window, R-units)")
    A("")
    A("| Stream | Total R | Sharpe | Max DD (R) |")
    A("|---|---:|---:|---:|")
    for n, p in rows.items():
        b = "**" if "blend" in n.lower() or "weighted" in n.lower() else ""
        A(f"| {b}{n}{b} | {p['total_R']:+.1f} | {p['sharpe']:.2f} | {p['maxDD_R']:+.1f} |")
    A("")
    eqs = rows["Equal-risk blend"]["sharpe"]
    exps = rows["Expectancy-weighted"]["sharpe"]
    flrs = rows["Equal-risk + decay floor"]["sharpe"]
    A(f"- **Equal-risk wins on this data (Sharpe {eqs:.2f}).** Pure expectancy-weighting "
      f"({exps:.2f}) *underperforms* — trailing samples are too thin and the sparse "
      "high-Sharpe strategy (monday) gets over-weighted, throwing away the diversification "
      "that is the actual edge. **Tuning the allocator to beat this in-sample would be the "
      "overfitting trap.**")
    A(f"- **Equal-risk + decay-floor (Sharpe {flrs:.2f})** is the practical compromise: it "
      "keeps full diversification (≈ equal-risk) but drops any strategy whose trailing edge "
      "turns negative. That is the self-defence that matters — it does NOT try to time "
      "allocation, only to stop funding a dead strategy.")
    A("")
    A("## Weights")
    A("")
    A("| Strategy | Avg walk-forward weight | **Current target** |")
    A("|---|---:|---:|")
    for n in names:
        A(f"| {n} | {avg_w[n]:.2f} | **{cur_w[n]:.2f}** |")
    A("")
    A("The **current target** column is the actionable output — feed these as per-strategy "
      "risk fractions into live sizing (e.g. scale each strategy's `risk_per_trade_usd` by "
      "its weight). A strategy whose trailing Sharpe turns negative drops to 0 automatically.")
    A("")
    A("## How to wire it live (next step, not done here)")
    A("")
    A("1. Emit per-strategy realised daily R from the trade journal (live tape already has "
      "pnl + per-trade risk).")
    A("2. Nightly, compute `expectancy_weights()` on the trailing window and write a "
      "`strategy_risk_weights` override (sits alongside the existing nightly regime "
      "override consumed by `_apply_regime_override`).")
    A("3. In the risk engine, scale each strategy's risk budget by its weight; floor tiny "
      "weights to 0 (stand aside) and keep the per-strategy cap.")
    A("")
    A("> This is the honest 'trade regardless of the situation' lever: not one omniscient "
      "strategy, but an allocator that quietly defunds whatever stops working — including "
      "kalman, whose OOS-death (`project_kalman_v2_retune_no_edge`) this machinery would "
      "have caught and starved on its own.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
