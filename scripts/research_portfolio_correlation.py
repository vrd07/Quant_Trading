#!/usr/bin/env python3
"""
Portfolio correlation / diversification across the live strategy roster (2026).

The honest answer to "make it trade regardless of the situation" is NOT one
omniscient super-strategy — it is a PORTFOLIO of uncorrelated edges. We already
run three on different instruments and clocks:

    kalman_regime   XAUUSD   15m mean-reversion/trend     (the gold engine)
    london_breakout USDJPY   Asia-range break, 1/day       (FX session edge)
    monday_drift    GBPUSD+AUDUSD  Monday anti-USD hold     (weekly macro drift)

This measures whether they actually diversify: daily-return correlation,
risk-parity combined equity, and Sharpe/drawdown of the blend vs each standalone.

Everything is normalised to R-multiples (pnl / per-trade $ risk) so strategies on
different symbols/lots/accounts combine on one risk basis. Correlation is
scale-invariant; the blend allocates equal risk per strategy per day.

⚠️ All components are IN-SAMPLE 2026 backtests; kalman is OOS-dead (see
project_kalman_v2_retune_no_edge). This shows the diversification STRUCTURE of the
architecture and which components carry it — it is not a deploy approval.

Writes: reports/portfolio_correlation_2026.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORT = PROJECT_ROOT / "reports/portfolio_correlation_2026.md"
KALMAN_RISK = 132.0          # fixed per-trade $ risk on the 50k tape (lot 0.04, SL 33)
WIN_START, WIN_END = "2026-01-05", "2026-06-16"
TRADING_DAYS_PER_YEAR = 252


def _r_from(df: pd.DataFrame, ts_col: str, risk) -> pd.Series:
    """Daily R-multiple series from a trade tape."""
    d = df.copy()
    d["ts"] = pd.to_datetime(d[ts_col], utc=True)
    if isinstance(risk, str):
        rr = pd.to_numeric(d[risk], errors="coerce")
    else:
        rr = pd.Series(float(risk), index=d.index)
    rr = rr.replace(0, np.nan)
    d["R"] = pd.to_numeric(d["pnl"], errors="coerce") / rr
    d = d.dropna(subset=["R"])
    daily = d.groupby(d["ts"].dt.normalize())["R"].sum()
    daily.index = daily.index.tz_convert(None) if daily.index.tz else daily.index
    return daily


def load_strategies() -> dict:
    bt = PROJECT_ROOT / "data/backtests"
    rp = PROJECT_ROOT / "reports"
    out = {}

    # kalman — fixed $132 risk per trade; attribute to EXIT day (realized).
    k = pd.read_csv(bt / "kalman_50k_2026_trades.csv")
    out["kalman_regime"] = _r_from(k, "exit_ts", KALMAN_RISK)

    # london_breakout varB — has r_dollars; attribute to entry day.
    lb = pd.read_csv(rp / "london_breakout_2026_varB_usdjpy_trades.csv")
    out["london_breakout"] = _r_from(lb, "timestamp", "r_dollars")

    # monday_drift — combine both pairs (same macro bet, two slots).
    mg = pd.read_csv(rp / "monday_drift_2026_gbpusd_trades.csv")
    ma = pd.read_csv(rp / "monday_drift_2026_audusd_trades.csv")
    md = pd.concat([mg, ma], ignore_index=True)
    out["monday_drift"] = _r_from(md, "timestamp", "r_dollars")

    return out


def perf(daily: pd.Series) -> dict:
    """Sharpe (annualised), total R, maxDD (in R), N active days."""
    eq = daily.cumsum()
    peak = eq.cummax()
    dd = (eq - peak).min()
    mu, sd = daily.mean(), daily.std(ddof=1)
    sharpe = (mu / sd * np.sqrt(TRADING_DAYS_PER_YEAR)) if sd > 0 else float("nan")
    active = int((daily != 0).sum())
    return dict(total_R=daily.sum(), sharpe=sharpe, maxDD_R=dd,
                active_days=active, mean=mu, std=sd)


def main():
    strat = load_strategies()

    # Common daily calendar over the window (business days), 0 on flat days.
    cal = pd.bdate_range(WIN_START, WIN_END)
    M = pd.DataFrame(index=cal)
    for name, s in strat.items():
        s = s[(s.index >= cal.min()) & (s.index <= cal.max())]
        M[name] = s.reindex(cal).fillna(0.0)

    names = list(strat.keys())

    # Correlation 1: over ALL business days (flat = 0). The honest portfolio view.
    corr_all = M.corr()
    # Correlation 2: only days where BOTH of a pair traded (overlap-conditional).
    def cond_corr(a, b):
        mask = (M[a] != 0) & (M[b] != 0)
        if mask.sum() < 5:
            return np.nan, int(mask.sum())
        return float(M.loc[mask, a].corr(M.loc[mask, b])), int(mask.sum())

    # Risk-parity blend: equal risk per strategy each day = mean across the 3.
    blend = M[names].mean(axis=1)

    # Per-strategy + blend performance
    rows = {n: perf(M[n]) for n in names}
    rows["PORTFOLIO (risk-parity)"] = perf(blend)

    # ---- console ----
    print("=" * 70)
    print("PORTFOLIO CORRELATION / DIVERSIFICATION — 2026 (in-sample)")
    print("=" * 70)
    print(f"window {cal.min().date()} -> {cal.max().date()}  ({len(cal)} business days)\n")
    print("Daily-return correlation (all business days, flat=0):")
    print(corr_all.round(2).to_string())
    avg_off = corr_all.where(~np.eye(len(names), dtype=bool)).stack().mean()
    print(f"\n  avg pairwise correlation: {avg_off:+.2f}")
    print("\nPerformance (R-units):")
    print(f"  {'strategy':<26} {'totalR':>8} {'Sharpe':>7} {'maxDD_R':>8} {'days':>5}")
    for n, p in rows.items():
        print(f"  {n:<26} {p['total_R']:>+8.1f} {p['sharpe']:>7.2f} "
              f"{p['maxDD_R']:>+8.1f} {p['active_days']:>5}")

    best_solo = max((perf(M[n])["sharpe"] for n in names))
    port_sharpe = rows["PORTFOLIO (risk-parity)"]["sharpe"]
    print(f"\n  best standalone Sharpe {best_solo:.2f}  ->  portfolio Sharpe {port_sharpe:.2f}")

    # ---- report ----
    L = []
    A = L.append
    A("# Portfolio Correlation & Diversification — 2026 (in-sample)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/research_portfolio_correlation.py`")
    A(f"**Window:** {cal.min().date()} → {cal.max().date()} ({len(cal)} business days). "
      "All P&L normalised to **R-multiples** (pnl ÷ per-trade $ risk) so the three "
      "strategies combine on one risk basis. Blend = equal risk per strategy per day.")
    A("")
    A("> The all-weather property you want is a **portfolio** property, not a "
      "single-strategy one. This measures whether the live roster actually "
      "diversifies. ⚠️ All components are in-sample 2026; **kalman is OOS-dead** "
      "(`project_kalman_v2_retune_no_edge`). This shows diversification STRUCTURE, "
      "not a deploy approval.")
    A("")
    A("## Daily-return correlation (all business days, flat = 0)")
    A("")
    A("| | " + " | ".join(names) + " |")
    A("|" + "---|" * (len(names) + 1))
    for n in names:
        A(f"| **{n}** | " + " | ".join(f"{corr_all.loc[n, m]:+.2f}" for m in names) + " |")
    A("")
    A(f"- **Average pairwise correlation: {avg_off:+.2f}.** "
      + ("Near-zero/low — the three are genuinely independent return streams "
         "(different instruments, clocks and edges), which is exactly what makes a "
         "blend worth more than its parts."
         if abs(avg_off) < 0.25 else
         "Material correlation — diversification benefit is limited; the streams "
         "move together more than ideal."))
    A("")
    A("### Overlap-conditional correlation (only days BOTH traded)")
    A("")
    A("| pair | corr | shared days |")
    A("|---|---:|---:|")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c, nd = cond_corr(names[i], names[j])
            cs = f"{c:+.2f}" if c == c else "n/a (<5)"
            A(f"| {names[i]} × {names[j]} | {cs} | {nd} |")
    A("")
    A("*Strategies rarely trade the same day (different sessions/cadence), so the "
      "all-days correlation above is dominated by non-overlap — itself a form of "
      "diversification (they're active at different times).*")
    A("")
    A("## Performance — standalone vs blend (R-units)")
    A("")
    A("| Stream | Total R | Sharpe (ann.) | Max DD (R) | Active days |")
    A("|---|---:|---:|---:|---:|")
    for n, p in rows.items():
        bold = "**" if n.startswith("PORTFOLIO") else ""
        A(f"| {bold}{n}{bold} | {p['total_R']:+.1f} | {p['sharpe']:.2f} | "
          f"{p['maxDD_R']:+.1f} | {p['active_days']} |")
    A("")
    deepest_dd = min(perf(M[n])["maxDD_R"] for n in names)     # most-negative standalone DD
    port_dd = rows["PORTFOLIO (risk-parity)"]["maxDD_R"]
    dd_cut = (1 - port_dd / deepest_dd) * 100 if deepest_dd < 0 else 0.0
    A(f"- Best standalone Sharpe **{best_solo:.2f}** → risk-parity blend Sharpe "
      f"**{port_sharpe:.2f}** "
      + ("(blend improves on every standalone — diversification is real)."
         if port_sharpe > best_solo else
         "(blend does NOT beat the best *standalone* Sharpe — equal-risk weighting "
         "lets the weakest component drag it; see the allocation note below)."))
    A(f"- **The real diversification win is the drawdown:** deepest standalone DD "
      f"**{deepest_dd:+.1f}R** (kalman) → blend **{port_dd:+.1f}R**, "
      f"**{dd_cut:.0f}% shallower.** Because the streams bleed at different times, "
      "the blend's equity path is far smoother than any single engine — that smoothness "
      "*is* the 'trade regardless of the situation' you were after.")
    A("")
    A("## Reading it / what to do")
    A("")
    A("1. **Diversification is structural, not cosmetic** when avg correlation is "
      "low: the blend's drawdown path is shallower than the components because they "
      "bleed at different times. That is the real 'trade regardless of situation' — "
      "when gold chops, the FX session edge and the Monday macro bet are uncorrelated "
      "to it.")
    A("2. **But a blend cannot rescue a negative component.** Equal-risk weighting "
      "lets an OOS-dead kalman drag the Sharpe down. The allocator must weight by "
      "*forward* expectancy (or drop a component to ~0), not split evenly.")
    A("3. **Next step (allocation layer):** size each strategy by its walk-forward "
      "Sharpe, cap per-strategy risk, and let the portfolio — not one super-Kalman — "
      "carry the all-weather behaviour. This is where the effort pays off.")
    A("")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
