"""
research_vwap_validate.py — Make-or-break checks on the NY VWAP-reversion ridge.

Winning params from stage 3: z=2.0, sl_sigma=2.0, tp_frac=1.0, NY 13:00->21:00 UTC.
Before proposing to wire this, rule out:
  A. One-regime luck  -> per-quarter PF / total.
  B. Long-only artifact of gold's bull drift -> split long-fade vs short-fade.
  C. Cost fragility    -> rerun at 20 / 30 / 40 pts one-way spread.
  D. Equity-curve shape (monotone vs one lucky cluster).
"""
import numpy as np
import pandas as pd
from research_vwap_reversion import load, run, stats  # reuse


def run_costed(df, spread_pts, **kw):
    import research_vwap_reversion as M
    old = M.SPREAD_PTS
    M.SPREAD_PTS = spread_pts
    try:
        return M.run(df, **kw)
    finally:
        M.SPREAD_PTS = old


def main():
    df = load()
    P = dict(open_h=13, eod_h=21, warmup_bars=6, z=2.0, sl_sigma=2.0, tp_frac=1.0)
    tr = run_costed(df, 20, **P)
    print(f"Base (20pt spread): {stats(tr)}\n")

    print("=== A. Per-quarter ===")
    tr["q"] = tr["date"].dt.to_period("Q")
    for q, g in tr.groupby("q"):
        s = stats(g)
        print(f"  {q}: n={s['n']:>3} PF={s['pf']:>5.2f} WR={s['wr']*100:>3.0f}% "
              f"tot={s['tot']:>7.0f}bps")

    print("\n=== B. Long-fade vs Short-fade ===")
    for side, name in [(1, "LONG-fade (buy dip <VWAP)"), (-1, "SHORT-fade (sell rip >VWAP)")]:
        s = stats(tr[tr.side == side])
        print(f"  {name:>30}: n={s['n']:>3} PF={s['pf']:>5.2f} WR={s['wr']*100:>3.0f}% "
              f"t={s['t']:>5.2f} tot={s['tot']:>6.0f}bps")

    print("\n=== C. Cost sensitivity (one-way spread pts) ===")
    for sp in (20, 30, 40, 50):
        s = stats(run_costed(df, sp, **P))
        print(f"  {sp:>2}pt: n={s['n']} PF={s['pf']:>5.2f} t={s['t']:>5.2f} "
              f"tot={s['tot']:>7.0f}bps")

    print("\n=== D. Equity curve (cumulative bps, 20pt) ===")
    eq = tr["ret"].cumsum() * 1e4
    # sample 12 points
    idx = np.linspace(0, len(eq) - 1, 12).astype(int)
    for i in idx:
        bar = "#" * max(0, int(eq.iloc[i] / 40))
        print(f"  {tr['date'].iloc[i].date()} {eq.iloc[i]:>7.0f} {bar}")
    # max drawdown of the bps equity
    peak = eq.cummax(); dd = (eq - peak)
    print(f"  max equity DD: {dd.min():.0f} bps")


if __name__ == "__main__":
    main()
