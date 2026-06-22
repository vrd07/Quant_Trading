#!/usr/bin/env python3
"""
Handcrafted portfolio weights for the live strategy roster (Rob Carver's method).

Carver's "handcrafting" (*Advanced Futures Trading Strategies*) is a robust,
optimiser-free way to turn a correlation matrix into portfolio RISK weights. It
sidesteps the overfitting of mean-variance optimisation by:

  1. Building a hierarchical tree that GROUPS correlated strategies together
     (correlation distance + average linkage).
  2. Assigning EQUAL risk weight to each branch at every node (top-down halving).
  3. Multiplying weights down the tree -> final per-strategy risk weights.

The robust prior is "equal risk within a group": correlated strategies share a
branch, so they cannot collectively dominate, and a genuinely uncorrelated
strategy that splits off near the root earns a big share for diversifying. No
return forecast is required for the base weights.

An OPTIONAL, heavily-shrunk Sharpe tilt (`--sharpe-tilt LAMBDA`, default 0) nudges
weight toward higher-expectancy strategies and collapses negative-Sharpe ones
toward zero. It is OFF by default on purpose: Sharpe is noisily estimated and
kalman is OOS-dead (see `project_kalman_v2_retune_no_edge`) — the base output
must not silently lean on a stale in-sample edge.

Also reports the Instrument Diversification Multiplier (IDM = 1/sqrt(w'Cw)) — how
much the combined book can be scaled up because the components diversify.

Reuses the return-stream loader + Sharpe harness from
`research_portfolio_correlation.py` (single source of truth for the tapes), so
adding a strategy here = adding its tape there, nowhere else.

⚠️ In-sample 2026 tapes; this is an ALLOCATION RECOMMENDATION, not a live wiring.
It prints weights you can fold into `regime_classifier.STRATEGY_WEIGHTS`
deliberately (note the confidence-band caveat in `--emit-confidences`).

Usage:
    python scripts/handcraft_weights.py                  # base handcraft weights
    python scripts/handcraft_weights.py --sharpe-tilt 0.5  # shrunk SR tilt
    python scripts/handcraft_weights.py --emit-confidences # STRATEGY_WEIGHTS map

Writes: reports/handcraft_weights_2026.md
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.spatial.distance import squareform

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

# Single source of truth for the strategy return tapes + Sharpe.
from research_portfolio_correlation import (  # noqa: E402
    WIN_START,
    WIN_END,
    load_strategies,
    perf,
)

REPORT = PROJECT_ROOT / "reports/handcraft_weights_2026.md"

# Carver caps the diversification multiplier; uncorrelated edges otherwise imply
# implausibly large leverage from a short, in-sample tape.
IDM_CAP = 2.5
# Top of the STRATEGY_WEIGHTS confidence band the strongest strategy maps to.
CONFIDENCE_ANCHOR = 0.90


# --- Pure handcrafting algorithm (unit-tested) -----------------------------

def correlation_distance(corr: pd.DataFrame) -> np.ndarray:
    """Carver's correlation distance d = sqrt(0.5*(1-rho)), clipped & symmetric."""
    c = np.nan_to_num(corr.values, nan=0.0)
    np.fill_diagonal(c, 1.0)
    d = np.sqrt(np.clip(0.5 * (1.0 - c), 0.0, 1.0))
    np.fill_diagonal(d, 0.0)
    return 0.5 * (d + d.T)  # enforce exact symmetry for squareform


def _leaf_names(node, names: list) -> list:
    """Strategy names under a scipy ClusterNode subtree."""
    if node.is_leaf():
        return [names[node.id]]
    return _leaf_names(node.left, names) + _leaf_names(node.right, names)


def handcraft_weights(corr: pd.DataFrame) -> dict:
    """Top-down equal-risk handcrafting -> {strategy: risk weight}, sums to 1.

    Pure function of the correlation matrix. At each level the set is split into
    its two top-level correlation clusters, each branch gets EQUAL weight, and we
    recurse within each branch. A strategy that splits off near the root (more
    independent) keeps a larger share than members of a correlated cluster.

    A "structureless" group — one whose pairwise correlations are all equal —
    gets flat equal weights, so symmetric inputs give symmetric outputs (binary
    halving alone would impose a spurious hierarchy on ties).
    """
    names = list(corr.columns)
    n = len(names)
    if n == 0:
        return {}
    if n == 1:
        return {names[0]: 1.0}
    if n == 2:
        return {names[0]: 0.5, names[1]: 0.5}

    d = correlation_distance(corr)
    off = d[np.triu_indices(n, k=1)]
    if float(off.max() - off.min()) < 1e-9:        # no internal structure
        return {nm: 1.0 / n for nm in names}

    z = linkage(squareform(d, checks=False), method="average")
    root = to_tree(z)
    groups = (_leaf_names(root.left, names), _leaf_names(root.right, names))

    weights: dict = {}
    for group in groups:                            # equal weight per branch
        sub = handcraft_weights(corr.loc[group, group])
        for k, v in sub.items():
            weights[k] = v / 2.0
    return weights


def apply_sharpe_tilt(weights: dict, sharpes: dict, lam: float) -> dict:
    """Optional shrunk Sharpe tilt: w_i *= max(SR_i,0)**lam, renormalised.

    lam=0 -> untouched (pure handcraft). lam=1 -> proportional to Sharpe.
    Negative-Sharpe strategies collapse toward 0. Falls back to the untilted
    weights if every strategy has non-positive Sharpe (nothing to tilt toward).
    """
    if lam <= 0:
        return dict(weights)
    tilted = {
        k: w * (max(sharpes.get(k, 0.0), 0.0) ** lam)
        for k, w in weights.items()
    }
    total = sum(tilted.values())
    if total <= 0:
        return dict(weights)
    return {k: v / total for k, v in tilted.items()}


def diversification_multiplier(weights: dict, corr: pd.DataFrame) -> float:
    """IDM = 1/sqrt(w' C w), capped. >=1; higher = more diversified book."""
    names = list(corr.columns)
    w = np.array([weights[n] for n in names])
    c = np.nan_to_num(corr.values, nan=0.0)
    np.fill_diagonal(c, 1.0)
    var = float(w @ c @ w)
    if var <= 0:
        return IDM_CAP
    return min(1.0 / np.sqrt(var), IDM_CAP)


# --- Plumbing: build the correlation matrix from the live tapes ------------

def aligned_matrix(strat: dict) -> pd.DataFrame:
    """Daily R-multiple matrix over a common business-day calendar (flat=0).

    Mirrors the alignment in research_portfolio_correlation.main so the
    correlation here matches the published portfolio_correlation report.
    """
    cal = pd.bdate_range(WIN_START, WIN_END)
    m = pd.DataFrame(index=cal)
    for name, s in strat.items():
        s = s[(s.index >= cal.min()) & (s.index <= cal.max())]
        m[name] = s.reindex(cal).fillna(0.0)
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sharpe-tilt", type=float, default=0.0,
                    help="shrinkage lambda for the optional Sharpe tilt "
                         "(0=off/pure handcraft, 1=proportional to Sharpe)")
    ap.add_argument("--emit-confidences", action="store_true",
                    help="also print STRATEGY_WEIGHTS-style 0-1 confidences "
                         "(advisory: risk weights rescaled, NOT a drop-in)")
    args = ap.parse_args()

    strat = load_strategies()
    m = aligned_matrix(strat)
    names = list(strat.keys())
    corr = m.corr()
    sharpes = {n: perf(m[n])["sharpe"] for n in names}

    base = handcraft_weights(corr)
    weights = apply_sharpe_tilt(base, sharpes, args.sharpe_tilt)
    equal = {n: 1.0 / len(names) for n in names}

    idm = diversification_multiplier(weights, corr)
    idm_eq = diversification_multiplier(equal, corr)

    # ---- console ----
    print("=" * 70)
    print("HANDCRAFTED PORTFOLIO WEIGHTS (Carver) — 2026 (in-sample)")
    print("=" * 70)
    print(f"window {m.index.min().date()} -> {m.index.max().date()}  "
          f"({len(m)} business days)   sharpe-tilt lambda={args.sharpe_tilt}\n")
    print("Correlation matrix:")
    print(corr.round(2).to_string())
    print()
    print(f"  {'strategy':<22} {'Sharpe':>7} {'equal':>7} {'handcraft':>10}")
    for n in names:
        print(f"  {n:<22} {sharpes[n]:>7.2f} {equal[n]:>7.1%} {weights[n]:>10.1%}")
    print()
    print(f"  IDM (handcraft): {idm:.2f}   IDM (equal): {idm_eq:.2f}   cap {IDM_CAP}")

    conf = None
    if args.emit_confidences:
        peak = max(weights.values())
        conf = {n: round(CONFIDENCE_ANCHOR * weights[n] / peak, 2) for n in names}
        print("\n  STRATEGY_WEIGHTS confidences (advisory, top -> "
              f"{CONFIDENCE_ANCHOR}):")
        for n in names:
            print(f"    {n:<22} {conf[n]:>5.2f}")

    # ---- report ----
    L: list[str] = []
    A = L.append
    A("# Handcrafted Portfolio Weights — 2026 (in-sample)")
    A("")
    A("**Generated:** 2026-06-23 · **Script:** `scripts/handcraft_weights.py` · "
      "**Method:** Rob Carver handcrafting (*Advanced Futures Trading Strategies*)")
    A(f"**Window:** {m.index.min().date()} → {m.index.max().date()} "
      f"({len(m)} business days). All P&L in **R-multiples**; tapes shared with "
      "`research_portfolio_correlation.py`. **Sharpe-tilt λ = "
      f"{args.sharpe_tilt}** (0 = pure handcraft).")
    A("")
    A("> Handcrafting turns the correlation matrix into robust RISK weights "
      "without an optimiser: correlated strategies are grouped onto the same "
      "branch and each branch gets equal weight, so no cluster of similar edges "
      "can dominate and a genuine diversifier is rewarded. ⚠️ In-sample 2026 "
      "tapes, **kalman is OOS-dead** — this is an allocation *recommendation*, "
      "not a deploy approval.")
    A("")
    A("## Correlation matrix")
    A("")
    A("| | " + " | ".join(names) + " |")
    A("|" + "---|" * (len(names) + 1))
    for n in names:
        A(f"| **{n}** | " + " | ".join(f"{corr.loc[n, mn]:+.2f}" for mn in names) + " |")
    A("")
    A("## Weights")
    A("")
    A("| Strategy | Sharpe (ann.) | Equal | **Handcraft** |")
    A("|---|---:|---:|---:|")
    for n in names:
        A(f"| {n} | {sharpes[n]:.2f} | {equal[n]:.1%} | **{weights[n]:.1%}** |")
    A("")
    A(f"- **IDM (handcraft): {idm:.2f}** vs equal-weight {idm_eq:.2f} "
      f"(capped at {IDM_CAP}). The combined book diversifies enough to be scaled "
      f"~{idm:.2f}× before it carries the same risk as a single undiversified "
      "component.")
    if idm < idm_eq - 1e-6:
        A(f"- ⚠️ **Handcraft IDM ({idm:.2f}) is BELOW equal-weight ({idm_eq:.2f}):** "
          "the off-diagonals are mostly correlation *noise*, so the tree's "
          "hierarchy concentrates risk without a real diversification payoff. "
          "When this holds, **equal weight is the more robust default** — read the "
          "handcraft weights only as *which strategies cluster together* (the one "
          "real signal), not as a precise split.")
    if args.sharpe_tilt > 0:
        A(f"- Sharpe tilt λ={args.sharpe_tilt} applied: weight leans toward "
          "higher-Sharpe strategies; any negative-Sharpe component collapses "
          "toward zero (the blend cannot rescue a negative edge).")
    else:
        A("- **Base (λ=0):** weights depend only on the correlation structure, "
          "not on the noisy in-sample Sharpe. Re-run with `--sharpe-tilt 0.5` "
          "to see the (heavily shrunk) expectancy tilt.")
    A("")
    if conf is not None:
        A("## STRATEGY_WEIGHTS confidences (advisory)")
        A("")
        A("⚠️ `regime_classifier.STRATEGY_WEIGHTS` holds 0–1 *confidence priors* "
          "(thresholded at 0.40 to enable/disable), **not** portfolio weights "
          "that sum to 1 — handcraft risk weights (~1/N each) would fall under "
          "the threshold and disable everything. These rescale the handcraft "
          f"weights so the strongest maps to {CONFIDENCE_ANCHOR}, preserving "
          "relative proportions. Fold in deliberately; they do not encode "
          "regime, which the table still does per-regime.")
        A("")
        A("| Strategy | Confidence |")
        A("|---|---:|")
        for n in names:
            A(f"| {n} | {conf[n]:.2f} |")
        A("")
    A("## How to use it")
    A("")
    A("1. **Base weights are the robust default** — they need no return forecast, "
      "so they don't decay when an edge does. Re-run as the roster changes.")
    A("2. **Add a strategy** by adding its tape to `load_strategies()` in "
      "`research_portfolio_correlation.py`; this allocator picks it up with no "
      "other change.")
    A("3. **The Sharpe tilt is a dial, not a default.** Use a small λ (≤0.5) if "
      "you trust the forward expectancy; λ=0 when you don't (current stance, "
      "given kalman).")
    A("")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
