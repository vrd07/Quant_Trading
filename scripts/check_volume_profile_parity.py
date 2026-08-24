#!/usr/bin/env python3
"""
Parity harness -- the drift guard for the MQL5 volume-profile port.

The definition lives in two languages and can diverge silently. This script
re-runs the Python definition over the EXACT histogram the indicator computed
(which is why the indicator exports its whole histogram, not just its levels)
and requires an exact match on every derived value.

WHAT THIS CAN AND CANNOT PROVE. Dukascopy ticks are not the broker's ticks, so
absolute row volumes will never agree and a cross-vendor comparison would be a
FAKE test -- passing or failing for reasons unrelated to correctness. So parity
is taken at the algorithm layer: given the indicator's own histogram, Python
must derive the same POC, value area, skew, shape, regime, open type, value
migration and node set. Every line of logic is covered; only the raw feed
differs, and that difference is disclosed.

Usage:
  python scripts/check_volume_profile_parity.py --dir data/parity
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.microstructure import profile_context as pc       # noqa: E402
from src.microstructure import volume_profile as vp        # noqa: E402

PRICE_TOL = 1e-6
SKEW_TOL = 1e-4
MAX_REPORTED = 25


def _blank(x) -> bool:
    """An empty CSV cell arrives as NaN or as the empty string."""
    return x is None or (isinstance(x, float) and pd.isna(x)) or str(x).strip() == ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/parity")
    args = ap.parse_args()
    d = PROJECT_ROOT / args.dir

    hist_df = pd.read_csv(d / "vp_histogram.csv")
    lvl_df = pd.read_csv(d / "vp_levels.csv")
    node_path = d / "vp_nodes.csv"
    node_df = pd.read_csv(node_path) if node_path.exists() else pd.DataFrame(
        columns=["session", "kind", "price"])
    if lvl_df.empty:
        print("PARITY FAILED -- no sessions exported")
        return 1

    # Sort by session tag so session n-1 really is the prior session. The
    # indicator writes them in order, but the checker must not depend on that.
    lvl_df = lvl_df.sort_values("session").reset_index(drop=True)

    mismatches: list[str] = []
    checks = 0

    def fail(msg: str) -> None:
        if len(mismatches) < MAX_REPORTED:
            mismatches.append(msg)

    prior_prof: vp.Profile | None = None

    for _, row in lvl_df.iterrows():
        tag = row["session"]
        g = hist_df[hist_df.session == tag]
        if g.empty:
            fail(f"{tag}: no histogram rows exported")
            prior_prof = None
            continue

        rows = g["row"].to_numpy(dtype=np.int64)
        vols = g["volume"].to_numpy(dtype=float)
        lo, hi = int(rows.min()), int(rows.max())
        dense = np.zeros(hi - lo + 1)
        dense[rows - lo] = vols

        params = vp.ProfileParams(
            row_size=float(row["row_size"]),
            value_area_pct=float(row["value_area_pct"]),
            va_algorithm="single_row" if int(row["va_algorithm"]) == 0 else "two_row",
        )
        hist = vp.Histogram(lo, dense, params.row_size, int(vols.sum()), 0)
        prof = vp.build_profile(hist, params)
        if prof is None:
            fail(f"{tag}: Python built no profile from the exported histogram")
            prior_prof = None
            continue

        for name, got, want in (("vpoc", prof.vpoc, float(row["vpoc"])),
                                ("vah", prof.vah, float(row["vah"])),
                                ("val", prof.val, float(row["val"])),
                                ("low", prof.low, float(row["low"])),
                                ("high", prof.high, float(row["high"]))):
            checks += 1
            if abs(got - want) > PRICE_TOL:
                fail(f"{tag}: {name} python={got:.6f} mql5={want:.6f}")

        cparams = pc.ContextParams(
            skew_threshold=float(row["skew_threshold"]),
            min_rows_for_shape=int(row["min_rows_for_shape"]),
            regime_min_elapsed_pct=float(row["regime_min_elapsed_pct"]),
        )
        read = pc.classify_shape(prof, cparams)

        checks += 1
        mql_skew = row["skew"]
        if _blank(mql_skew):
            if read.skew is not None:
                fail(f"{tag}: python has skew {read.skew:.6f}, mql5 has none")
        elif read.skew is None:
            fail(f"{tag}: mql5 has skew {float(mql_skew):.6f}, python has none")
        elif abs(read.skew - float(mql_skew)) > SKEW_TOL:
            fail(f"{tag}: skew python={read.skew:.6f} mql5={float(mql_skew):.6f}")

        checks += 1
        if read.shape != row["shape"]:
            fail(f"{tag}: shape python={read.shape} mql5={row['shape']}")

        checks += 1
        regime = pc.classify_regime(read.shape, float(row["elapsed_pct"]), cparams,
                                    bool(int(row["is_developing"])))
        if regime != row["regime"]:
            fail(f"{tag}: regime python={regime} mql5={row['regime']}")

        # The first session has no prior. Both sides must agree it is
        # undefined -- a default here would test nothing.
        if prior_prof is None:
            for col in ("open_type", "value_migration"):
                checks += 1
                if not _blank(row[col]):
                    fail(f"{tag}: no prior session, but mql5 exported "
                         f"{col}={row[col]!r}")
        else:
            checks += 1
            otype = pc.classify_open_type(float(row["open"]), prior_prof)
            if otype != row["open_type"]:
                fail(f"{tag}: open_type python={otype} mql5={row['open_type']}")
            checks += 1
            mig = pc.classify_value_migration(prof, prior_prof)
            if mig != row["value_migration"]:
                fail(f"{tag}: value_migration python={mig} mql5={row['value_migration']}")

        nparams = vp.NodeParams(
            hvn_prominence_pct=float(row["hvn_prominence_pct"]),
            lvn_ratio=float(row["lvn_ratio"]),
            min_separation_rows=int(row["node_min_sep_rows"]),
        )
        hvn, lvn = vp.find_nodes(prof, nparams)
        got_nodes = node_df[node_df.session == tag]
        for kind, want_prices in (("HVN", hvn), ("LVN", lvn)):
            checks += 1
            mql_prices = sorted(
                float(x) for x in got_nodes[got_nodes.kind == kind]["price"])
            py_prices = sorted(want_prices)
            if len(mql_prices) != len(py_prices) or any(
                    abs(a - b) > PRICE_TOL for a, b in zip(py_prices, mql_prices)):
                fail(f"{tag}: {kind} set differs -- python={_fmt(py_prices)} "
                     f"mql5={_fmt(mql_prices)}")

        prior_prof = prof

    ok = not mismatches
    print(f"{'PARITY OK' if ok else 'PARITY FAILED'} -- "
          f"{len(lvl_df)} sessions, {len(hist_df)} histogram rows, "
          f"{len(node_df)} nodes, {checks} comparisons")
    for m in mismatches:
        print(f"  {m}")
    if not ok:
        print("\nDo NOT relax the tolerances to make this pass. A mismatch means "
              "the two implementations genuinely disagree.")
    return 0 if ok else 1


def _fmt(prices: list[float]) -> str:
    if not prices:
        return "[]"
    if len(prices) > 6:
        return f"[{', '.join(f'{p:.2f}' for p in prices[:6])}, ... {len(prices)} total]"
    return f"[{', '.join(f'{p:.2f}' for p in prices)}]"


if __name__ == "__main__":
    raise SystemExit(main())
