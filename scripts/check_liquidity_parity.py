#!/usr/bin/env python3
"""
Parity harness — the drift guard for the MQL5 level-detection port.

Architecture A puts the level definition in two languages, which can diverge
silently. This script re-runs the Python definition over the EXACT bars the
indicator computed on (that is why the indicator exports its whole buffer, not
just a window) and requires:

  * level sets match exactly     — price, type, formation bar
  * features and scores match to 1e-4

Usage:
  python scripts/check_liquidity_parity.py --dir data/parity
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.microstructure import liquidity_levels as ll   # noqa: E402

PRICE_TOL = 1e-6
SCORE_TOL = 1e-4
MAX_REPORTED = 25


@dataclass
class ParityResult:
    ok: bool = True
    n_snapshots: int = 0
    n_rows: int = 0
    mismatches: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        if len(self.mismatches) < MAX_REPORTED:
            self.mismatches.append(msg)

    def summary(self) -> str:
        head = (f"{'PARITY OK' if self.ok else 'PARITY FAILED'} — "
                f"{self.n_snapshots} snapshots, {self.n_rows} level rows")
        if self.ok:
            return head
        more = ""
        return head + "\n" + "\n".join(f"  - {m}" for m in self.mismatches) + more


def _parse_times(s: pd.Series) -> pd.Series:
    """MT5 writes '2026.07.31 20:45:00'; pandas needs a hint for the dotted date."""
    return pd.to_datetime(s, format="%Y.%m.%d %H:%M:%S", utc=True)


def load_bars(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time_utc"] = _parse_times(df["time_utc"])
    df = df.set_index("time_utc").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df


def load_coefficients(mqh: Path) -> dict:
    """Parse LQ_BETA / LQ_MEAN / LQ_STD / LQ_PLATT_* out of the generated .mqh.

    Reading the header the indicator actually compiled against is the only way the
    score comparison means anything — re-fitting here would compare Python to Python.
    """
    text = Path(mqh).read_text()
    out: dict = {}
    for name in ("LQ_BETA", "LQ_MEAN", "LQ_STD"):
        m = re.search(rf"const double {name}\[[^\]]*\]\s*=\s*\{{(.*?)\}};", text, re.S)
        if not m:
            raise ValueError(f"{mqh}: cannot find {name}")
        out[name] = np.array([float(v) for v in m.group(1).replace("\n", "").split(",")
                              if v.strip()], dtype=float)
    for name in ("LQ_PLATT_A", "LQ_PLATT_B"):
        m = re.search(rf"const double {name}\s*=\s*([^;]+);", text)
        out[name] = float(m.group(1)) if m else (1.0 if name.endswith("A") else 0.0)
    m = re.search(r'#define LQ_MODEL_MODE\s+"([^"]+)"', text)
    out["mode"] = m.group(1) if m else "full"
    return out


def check(dirpath: Path, params: ll.LevelParams = ll.DEFAULTS,
          mqh: Path | None = None) -> ParityResult:
    res = ParityResult()
    coef = load_coefficients(mqh) if mqh is not None else None
    bars = load_bars(Path(dirpath) / "GC_LQ_bars.csv")
    levels = pd.read_csv(Path(dirpath) / "GC_LQ_levels.csv")
    levels["snapshot_time_utc"] = _parse_times(levels["snapshot_time_utc"])
    levels["formation_time_utc"] = _parse_times(levels["formation_time_utc"])

    ctx = ll.build_context(bars, params)
    pos = {ts: i for i, ts in enumerate(bars.index)}
    feat_cols = [f"f{i}" for i in range(len(ll.FEATURE_NAMES))]
    have_feats = all(c in levels.columns for c in feat_cols)

    for ts, grp in levels.groupby("snapshot_time_utc", sort=True):
        if ts not in pos:
            res.fail(f"{ts}: snapshot time is not in the exported bar buffer")
            continue
        t = pos[ts]
        got = grp.sort_values("row")
        want = ll.build_choice_set(ctx, t, params)
        res.n_snapshots += 1
        res.n_rows += len(got)

        if len(want) != len(got):
            res.fail(f"{ts}: level count {len(got)} (MQL5) vs {len(want)} (Python)")
            continue

        X = ll.feature_matrix(ctx, t, want, params)
        for i, (_, row) in enumerate(got.iterrows()):
            lv = want[i]
            if abs(float(row["price"]) - lv.price) > PRICE_TOL:
                res.fail(f"{ts} row {i}: price {row['price']} vs {lv.price}")
            if str(row["kind"]) != lv.kind:
                res.fail(f"{ts} row {i}: kind {row['kind']} vs {lv.kind}")
            want_form = bars.index[lv.formation_idx]
            if pd.Timestamp(row["formation_time_utc"]) != want_form:
                res.fail(f"{ts} row {i}: formation {row['formation_time_utc']} "
                         f"vs {want_form}")
            if str(row["side"]) != lv.side:
                res.fail(f"{ts} row {i}: side {row['side']} vs {lv.side}")
            if have_feats:
                for f, col in enumerate(feat_cols):
                    if abs(float(row[col]) - X[i, f]) > SCORE_TOL:
                        res.fail(f"{ts} row {i}: feature {col} "
                                 f"({ll.FEATURE_NAMES[f]}) {row[col]} vs {X[i, f]}")

        if coef is not None:
            # Recompute the utilities and softmax exactly as ScoreLevels() does,
            # using the coefficients the indicator compiled against.
            Z = (X - coef["LQ_MEAN"]) / coef["LQ_STD"]
            v = np.clip(Z @ coef["LQ_BETA"], -30.0, 30.0)
            probs = np.exp(v) / (1.0 + np.exp(v).sum())
            a, b = coef["LQ_PLATT_A"], coef["LQ_PLATT_B"]
            if a != 1.0 or b != 0.0:
                pc = np.clip(probs, 1e-9, 1 - 1e-9)
                lin = np.clip(a * np.log(pc / (1 - pc)) + b, -30.0, 30.0)
                probs = 1.0 / (1.0 + np.exp(-lin))
            for i, (_, row) in enumerate(got.iterrows()):
                if abs(float(row["score"]) - v[i]) > SCORE_TOL:
                    res.fail(f"{ts} row {i}: score {row['score']} vs {v[i]}")
                if abs(float(row["prob"]) - probs[i]) > SCORE_TOL:
                    res.fail(f"{ts} row {i}: prob {row['prob']} vs {probs[i]}")
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="data/parity",
                   help="directory holding GC_LQ_bars.csv and GC_LQ_levels.csv")
    p.add_argument("--mqh", default="mt5_indicators/liquidity_coefficients.mqh",
                   help="generated coefficients header; scores are compared only "
                        "when this exists")
    args = p.parse_args()
    mqh = PROJECT_ROOT / args.mqh
    res = check(Path(args.dir), mqh=mqh if mqh.exists() else None)
    print(res.summary())
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
