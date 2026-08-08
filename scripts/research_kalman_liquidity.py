#!/usr/bin/env python3
"""Does an adverse liquidity pool predict a kalman stop-out?

Reads a kalman backtest trade log, recomputes the adverse-pool distance at each
entry bar, and reports win rate and expectancy bucketed by that distance, split by
regime and side.

Method notes:

* One FrameContext is built over the whole span and `build_choice_set(ctx, t)` is
  called per trade. That is causal — live_swings scans only [t - scan_bars, t] and
  pivots are confirmed pivot_n bars late — and far cheaper than rebuilding a context
  per trade. The ATR/EMA recursions differ from a trailing-window computation only by
  the seed, which decays as (1 - 1/14)^n and is ~1e-33 after 1000 bars.
* The signal/outcome join is on the trade row's `timestamp`, which is the ENTRY BAR's
  index, not a wall clock. See project_squeeze_volume_filter_smelltest for the
  Signal.timestamp=now() footgun this avoids.
* Thresholds are chosen on IS only. The OOS block is printed for information and must
  not be consulted while picking a number.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.microstructure.liquidity_levels import DEFAULTS, build_choice_set, build_context

IS_END = pd.Timestamp("2025-12-31 23:59:59", tz="UTC")
BUCKETS = [0.0, 0.25, 0.5, 0.75, 1.0, 2.0, np.inf]
CANDIDATE_THRESHOLDS = [0.25, 0.5, 0.75, 1.0]


def load_15m(csv_path: Path) -> pd.DataFrame:
    """Same loader as scripts/research_liquidity_race.py — 5m CSV resampled to 15m."""
    df = pd.read_csv(csv_path, parse_dates=["timestamp"], index_col="timestamp")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    bars = (df.resample("15min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"]))
    flat = (bars.high == bars.low) & (bars.volume == 0)
    return bars[~flat]


def adverse_distances(bars: pd.DataFrame, trades: pd.DataFrame) -> pd.Series:
    """ATR-normalised adverse-pool distance at each trade's entry bar."""
    ctx = build_context(bars, DEFAULTS)
    pos = {ts: i for i, ts in enumerate(bars.index)}
    out = []
    for _, tr in trades.iterrows():
        t = pos.get(pd.Timestamp(tr["timestamp"]))
        if t is None or t < DEFAULTS.scan_bars:
            out.append(np.nan)
            continue
        atr = float(ctx.atr[t])
        if not np.isfinite(atr) or atr <= 0:
            out.append(np.nan)
            continue
        entry = float(tr["entry_price"])
        is_buy = str(tr["side"]).upper() == "BUY"
        levels = build_choice_set(ctx, t, DEFAULTS)
        if is_buy:
            d = [entry - lv.price for lv in levels if lv.price < entry]
        else:
            d = [lv.price - entry for lv in levels if lv.price > entry]
        d = [x for x in d if x > 0]
        out.append(min(d) / atr if d else np.nan)
    return pd.Series(out, index=trades.index, name="adverse_atr")


def bucket_table(df: pd.DataFrame) -> pd.DataFrame:
    lab = pd.cut(df["adverse_atr"], bins=BUCKETS, right=False)
    g = df.groupby(lab, observed=False)["pnl"]
    tbl = pd.DataFrame({
        "n": g.size(),
        "win_rate": g.apply(lambda s: float((s > 0).mean()) if len(s) else np.nan),
        "mean_pnl": g.mean(),
        "total_pnl": g.sum(),
    })
    none_rows = df[df["adverse_atr"].isna()]
    tbl.loc["no adverse pool"] = [
        len(none_rows),
        float((none_rows["pnl"] > 0).mean()) if len(none_rows) else np.nan,
        none_rows["pnl"].mean() if len(none_rows) else np.nan,
        none_rows["pnl"].sum() if len(none_rows) else 0.0,
    ]
    return tbl


def veto_table(df: pd.DataFrame) -> pd.DataFrame:
    """What a veto at each candidate threshold would remove and leave behind."""
    rows = []
    base_n, base_pnl = len(df), df["pnl"].sum()
    base_wr = float((df["pnl"] > 0).mean()) if base_n else np.nan
    for x in CANDIDATE_THRESHOLDS:
        cut = df["adverse_atr"].notna() & (df["adverse_atr"] <= x)
        kept = df[~cut]
        rows.append({
            "threshold_atr": x,
            "vetoed_n": int(cut.sum()),
            "vetoed_pnl": float(df.loc[cut, "pnl"].sum()),
            "kept_n": len(kept),
            "kept_pnl": float(kept["pnl"].sum()),
            "kept_win_rate": float((kept["pnl"] > 0).mean()) if len(kept) else np.nan,
            "pnl_delta": float(kept["pnl"].sum() - base_pnl),
        })
    out = pd.DataFrame(rows)
    out.attrs["base"] = (base_n, base_pnl, base_wr)
    return out


def section(fh, title, df_or_tbl):
    fh.write(f"\n### {title}\n\n")
    fh.write(df_or_tbl.to_markdown())
    fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bars", default="data/historical/XAUUSD_5m_real.csv")
    ap.add_argument("--trades",
                    default="data/backtests/kalman_liq_base_kalman_regime_trades.csv")
    ap.add_argument("--out", default="reports/kalman_liquidity_gate.md")
    args = ap.parse_args()

    bars = load_15m(Path(args.bars))
    trades = pd.read_csv(Path(args.trades))
    trades["timestamp"] = pd.to_datetime(trades["timestamp"], utc=True)
    trades = trades[trades["strategy"] == "kalman_regime"].reset_index(drop=True)
    if trades.empty:
        print("no kalman trades in the log — nothing to diagnose")
        return 1

    # MarketRegime values are UPPERCASE ("TREND"/"RANGE"/"UNKNOWN"). Normalise once
    # here so a case mismatch cannot silently match zero rows and masquerade as
    # "no trades in this mode" — which would read as a genuine stop condition.
    trades["regime"] = trades["regime"].astype(str).str.upper()

    trades["adverse_atr"] = adverse_distances(bars, trades)
    trades["slice"] = np.where(trades["timestamp"] <= IS_END, "IS", "OOS")

    seen = set(trades["regime"].unique())
    if not ({"TREND", "RANGE"} & seen):
        print(f"ABORT: no TREND/RANGE rows — regime column holds {sorted(seen)}. "
              f"This is a plumbing bug, NOT a result. Do not read it as 'no effect'.")
        return 2

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        fh.write("# Kalman adverse-liquidity gate — diagnostic\n\n")
        fh.write(f"Trades: {len(trades)}  "
                 f"(IS {(trades['slice'] == 'IS').sum()} / "
                 f"OOS {(trades['slice'] == 'OOS').sum()})\n\n")
        fh.write("Thresholds are chosen on the IS tables only. The OOS tables are "
                 "printed for information and must not be consulted while picking a "
                 "number.\n")

        for sl in ("IS", "OOS"):
            s = trades[trades["slice"] == sl]
            fh.write(f"\n## {sl}\n")
            section(fh, "All modes", bucket_table(s))
            for mode in ("TREND", "RANGE"):
                m = s[s["regime"] == mode]
                if len(m):
                    section(fh, f"{mode} (n={len(m)})", bucket_table(m))
                    for side in ("BUY", "SELL"):
                        ms = m[m["side"].str.upper() == side]
                        if len(ms):
                            section(fh, f"{mode} / {side} (n={len(ms)})",
                                    bucket_table(ms))
                else:
                    fh.write(f"\n### {mode}\n\nno trades in this mode\n")

            fh.write(f"\n### Veto simulation — {sl}\n\n")
            for mode in ("TREND", "RANGE"):
                m = s[s["regime"] == mode]
                if not len(m):
                    continue
                vt = veto_table(m)
                bn, bp, bw = vt.attrs["base"]
                fh.write(f"\n**{mode}** baseline: n={bn}, "
                         f"pnl={bp:.2f}, win_rate={bw:.3f}\n\n")
                fh.write(vt.to_markdown(index=False))
                fh.write("\n")

    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
