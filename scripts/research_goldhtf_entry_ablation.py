#!/usr/bin/env python3
"""Leave-one-out ablation of the GoldHTF legacy entry chain.

Each cell removes exactly one leg and is scored against ITS OWN matched
random-entry control, so cells with different trade counts stay comparable. The
thresholds below are pre-committed in
docs/superpowers/specs/2026-08-10-goldhtf-entry-ablation-design.md and must not be
adjusted after seeing results.

Usage: python scripts/research_goldhtf_entry_ablation.py [--trials 500]
"""

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.simulate_goldhtf_ea as sim   # noqa: E402

REPORT = PROJECT_ROOT / "reports/goldhtf_entry_ablation.md"

START, END = "2022-03-01", "2026-07-31"
BALANCE, MIN_LOT, MAX_LOT = 1000.0, 0.02, 1.00
SEED = 11

# Pre-committed thresholds. Do not touch after seeing results.
Z_THRESHOLD = 0.5
PCT_THRESHOLD = 10.0
A0_Z_GUARD = 1.0
COUNT_FLAG_RATIO = 3.0

CELLS = [
    dict(name="A0", label="baseline", inverted=False, overrides={}),
    dict(name="A1", label="-H4 trend", inverted=False,
         overrides={"NoTrendLeg": True}),
    dict(name="A2", label="-H1 zone", inverted=False,
         overrides={"NoZoneLeg": True}),
    dict(name="A3", label="-M15 structure", inverted=False,
         overrides={"NoMTFLeg": True}),
    dict(name="A4", label="-M5 pattern", inverted=False,
         overrides={"NoPatternLeg": True}),
    dict(name="A5", label="-M5 pattern+confirm", inverted=False,
         overrides={"NoPatternLeg": True, "NoConfirm": True}),
    dict(name="A6", label="+RANGING gate", inverted=True,
         overrides={"SkipRanging": True}),
]


def classify(delta_z, delta_pct, inverted):
    """Apply the pre-committed thresholds to one cell's deltas against A0."""
    if inverted:
        delta_z, delta_pct = -delta_z, -delta_pct
    if delta_z <= -Z_THRESHOLD and delta_pct <= -PCT_THRESHOLD:
        return "load-bearing"
    if delta_z >= Z_THRESHOLD and delta_pct >= PCT_THRESHOLD:
        return "harmful"
    return "decoration"


def calibrate_zone_stop_mult(a0_trades, h1_atr):
    """H1-ATR multiple that reproduces A0's median structural stop width."""
    med_stop = float(np.median(a0_trades["stop_pts"].to_numpy(float)))
    if not np.isfinite(med_stop) or med_stop <= 0:
        raise ValueError(
            "cannot calibrate the A2 stop: A0 median structural stop is not "
            "positive (A0 likely produced zero trades)")
    med_atr = float(np.nanmedian(np.asarray(h1_atr, dtype=float)))
    if not np.isfinite(med_atr) or med_atr <= 0:
        raise ValueError("cannot calibrate the A2 stop: H1 ATR median is not positive")
    return med_stop / med_atr


def censored_fraction(null_pf):
    """Fraction of the null PF draws pinned at the 10.0 zero-loss ceiling.

    `random_control` (scripts/simulate_goldhtf_ea.py) records PF=10.0 for any
    trial that had zero losing trades. If that happens often, the null's
    standard deviation is inflated by a censoring artifact rather than by real
    dispersion, which biases `z` toward zero. This does not change the
    pre-committed thresholds -- it only measures whether that failure mode is
    present so the percentile column can be trusted as a cross-check.

    Compares for EXACT equality with the 10.0 sentinel, not `>= 10.0`: a
    legitimate null draw that happens to score a genuine PF above 10 (small
    but nonzero losses) is a different phenomenon from the zero-loss ceiling
    and must not be counted as censored.
    """
    null_pf = np.asarray(null_pf, dtype=float)
    if null_pf.size == 0:
        return float("nan")
    return float(np.mean(null_pf == 10.0))


def censoring_summary_line(censored_values):
    """Build the post-table censoring summary line.

    `censored_values` is the per-cell censored fraction for every cell in the
    report, which may contain NaN for any cell that produced zero trades. An
    all-NaN column means censoring could not be assessed for ANY cell -- that
    is a "we don't know", not a "clean", and must not print the all-clear.
    """
    finite = [c for c in censored_values if np.isfinite(c)]
    if not finite:
        return ("Censoring undefined for every cell (no cell produced trades); "
                "z cannot be assessed as well-behaved.")
    worst = max(finite)
    if worst < 0.01:
        return "All cells: null censoring < 1%, z is well-behaved."
    return (f"**WARNING: null PF censoring is material (max {100*worst:.1f}%); "
            "read the percentile column, not z.**")


def _base_inp():
    return dict(sim.INP)


def run_cell(m5, overrides, trials, zone_stop_mult):
    """Run one cell and score it against its own matched control."""
    saved = _base_inp()
    try:
        sim.INP["ZoneV2"] = True
        sim.INP["ZoneATR"] = True
        sim.INP["UseDoubleFVG"] = False
        sim.INP["MinLot"], sim.INP["MaxLot"] = MIN_LOT, MAX_LOT
        sim.INP["ZoneStopATRMult"] = zone_stop_mult
        for k, v in overrides.items():
            sim.INP[k] = v

        trades, funnel, _ladder, _bal, _taken = sim.run(m5, START, END)
        if len(trades) == 0:
            return dict(trades=trades, funnel=funnel, pf=float("nan"),
                        stats=dict(z=float("nan"), percentile=float("nan"),
                                   null_mean=float("nan"), null_sd=float("nan")),
                        censored=float("nan"))
        gl = -trades[trades.pnl < 0].pnl.sum()
        pf = trades[trades.pnl > 0].pnl.sum() / gl if gl > 0 else float("inf")
        t0 = pd.Timestamp(START, tz="UTC")
        t1 = pd.Timestamp(END, tz="UTC") + pd.Timedelta(days=1)
        ctl = sim.random_control(m5, trades, None, trials, t0, t1, seed=SEED)
        stats = sim.control_stats(pf if np.isfinite(pf) else 10.0, ctl["pf"])
        censored = censored_fraction(ctl["pf"])
        return dict(trades=trades, funnel=funnel, pf=pf, stats=stats,
                    censored=censored)
    finally:
        sim.INP.clear()
        sim.INP.update(saved)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=500)
    args = ap.parse_args()

    sim.ARGS = SimpleNamespace(balance=BALANCE)
    df = pd.read_csv(sim.CSV, parse_dates=["timestamp"], index_col="timestamp")
    df = df[~((df.high == df.low) & (df.volume == 0))]
    warm = pd.Timestamp(START, tz="UTC") - pd.Timedelta(days=45)
    m5 = df[df.index >= warm]

    # A0 first: it sets the reference and calibrates the A2 stop.
    a0 = run_cell(m5, {}, args.trials, zone_stop_mult=1.0)
    h1_atr = sim.wilder(
        sim.true_range(sim.TF(m5, "1h", 60).bars), sim.INP["ATRPeriod"]).to_numpy(float)
    zmult = calibrate_zone_stop_mult(a0["trades"], h1_atr)

    results = {"A0": a0}
    for cell in CELLS[1:]:
        results[cell["name"]] = run_cell(m5, cell["overrides"], args.trials, zmult)

    L = []
    def say(s=""):
        print(s); L.append(s)

    a0z, a0p, a0n = (a0["stats"]["z"], a0["stats"]["percentile"], len(a0["trades"]))
    say("# GoldHTF legacy entry chain — leave-one-out ablation")
    say()
    say(f"XAUUSD 5m {START}..{END}, ${BALANCE:,.0f}, min lot {MIN_LOT}, "
        f"strict ${sim.COST}/side, legacy path isolated (`--no-dfvg`), "
        f"{args.trials} control trials, seed {SEED}.")
    say(f"A2 stop calibrated to **{zmult:.2f} x H1 ATR** "
        f"(A0 median structural stop {np.median(a0['trades'].stop_pts):.1f} pts).")
    say()
    say("Thresholds pre-committed in the design doc: load-bearing needs "
        f"dz <= -{Z_THRESHOLD} AND dpct <= -{PCT_THRESHOLD}pp against A0.")
    say()
    say("| cell | leg removed | trades | PF | null mean | z | pct | dz | dpct | censored | verdict |")
    say("|---|---|---|---|---|---|---|---|---|---|---|")
    for cell in CELLS:
        r = results[cell["name"]]
        s, n = r["stats"], len(r["trades"])
        c = r["censored"]
        if cell["name"] == "A0":
            verdict = "reference"
            dz = dp = 0.0
        else:
            dz, dp = s["z"] - a0z, s["percentile"] - a0p
            verdict = classify(dz, dp, cell["inverted"])
            if n > COUNT_FLAG_RATIO * a0n or n * COUNT_FLAG_RATIO < a0n:
                verdict += " (qualitative: trade count off by >3x)"
        say(f"| {cell['name']} | {cell['label']} | {n} | {r['pf']:.2f} | "
            f"{s['null_mean']:.3f} | {s['z']:+.2f} | {s['percentile']:.1f}% | "
            f"{dz:+.2f} | {dp:+.1f} | {100*c:.1f}% | {verdict} |")
    say()
    say(censoring_summary_line([results[c["name"]]["censored"] for c in CELLS]))
    say()
    if not np.isfinite(a0z) or a0z < A0_Z_GUARD:
        say(f"## GUARD 1 TRIPPED — A0 z = {a0z:+.2f} < {A0_Z_GUARD}")
        say()
        say("The full chain is not distinguishable from its own null, so differences "
            "between its legs are noise being ranked. **Leg-level verdicts above are "
            "not to be acted on.** Verdict: decoration end to end.")
    else:
        say(f"A0 z = {a0z:+.2f} clears the {A0_Z_GUARD} guard; leg verdicts stand.")
    say()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    for name, r in results.items():
        r["trades"].to_csv(
            str(REPORT).replace(".md", f"_{name}_trades.csv"), index=False)
    print(f"\nwrote {REPORT}")


if __name__ == "__main__":
    main()
