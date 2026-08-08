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
    """Outcome by adverse-pool distance. Metrics are R-multiples, not dollars.

    Raw PnL is confounded here: position size is equity-proportional, so on a
    declining account early trades carry far more dollar weight than late ones and a
    dollar total partly measures WHEN a trade happened. R = pnl / r_dollars divides
    that out. mean_R is the number that decides this diagnostic.
    """
    lab = pd.cut(df["adverse_atr"], bins=BUCKETS, right=False)
    g = df.groupby(lab, observed=False)
    tbl = pd.DataFrame({
        "n": g.size(),
        "win_rate": g["R"].apply(lambda s: float((s > 0).mean()) if len(s) else np.nan),
        "mean_R": g["R"].mean(),
        "total_R": g["R"].sum(),
    })
    none_rows = df[df["adverse_atr"].isna()]
    tbl.loc["no adverse pool"] = [
        len(none_rows),
        float((none_rows["R"] > 0).mean()) if len(none_rows) else np.nan,
        none_rows["R"].mean() if len(none_rows) else np.nan,
        none_rows["R"].sum() if len(none_rows) else 0.0,
    ]
    return tbl


def veto_table(df: pd.DataFrame) -> pd.DataFrame:
    """What a veto at each candidate threshold would remove and leave behind.

    `kept_mean_R` and `kept_win_rate` are the columns that matter. A veto that only
    raises total_R while leaving mean_R and win_rate flat is not selecting better
    trades — it is just trading less of the same distribution, which lowers the loss
    of a negative-expectancy system by arithmetic alone.
    """
    rows = []
    base_n = len(df)
    base_R, base_mean = df["R"].sum(), df["R"].mean()
    base_wr = float((df["R"] > 0).mean()) if base_n else np.nan
    for x in CANDIDATE_THRESHOLDS:
        cut = df["adverse_atr"].notna() & (df["adverse_atr"] <= x)
        kept = df[~cut]
        n_k = len(kept)
        # s.e. of the kept win rate, so a move can be read against its own noise
        wr_k = float((kept["R"] > 0).mean()) if n_k else np.nan
        se = float(np.sqrt(wr_k * (1 - wr_k) / n_k)) if n_k and np.isfinite(wr_k) else np.nan
        rows.append({
            "threshold_atr": x,
            "vetoed_n": int(cut.sum()),
            "vetoed_mean_R": float(df.loc[cut, "R"].mean()) if int(cut.sum()) else np.nan,
            "kept_n": n_k,
            "kept_win_rate": wr_k,
            "kept_wr_se": se,
            "kept_mean_R": float(kept["R"].mean()) if n_k else np.nan,
            "mean_R_delta": float(kept["R"].mean() - base_mean) if n_k else np.nan,
        })
    out = pd.DataFrame(rows)
    out.attrs["base"] = (base_n, base_R, base_mean, base_wr)
    return out


def section(fh, title, df_or_tbl):
    fh.write(f"\n### {title}\n\n")
    fh.write(df_or_tbl.to_markdown())
    fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bars", default="data/historical/XAUUSD_5m_real.csv")
    ap.add_argument("--trades-glob",
                    default="data/backtests/kalman_liq_20*_kalman_regime_trades.csv",
                    help="per-year trade logs; concatenated")
    ap.add_argument("--out", default="reports/kalman_liquidity_gate.md")
    args = ap.parse_args()

    import glob
    files = sorted(glob.glob(args.trades_glob))
    if not files:
        print(f"no trade logs matched {args.trades_glob}")
        return 1
    trades = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

    bars = load_15m(Path(args.bars))
    trades["timestamp"] = pd.to_datetime(trades["timestamp"], utc=True)
    trades = trades[trades["strategy"] == "kalman_regime"].reset_index(drop=True)
    if trades.empty:
        print("no kalman trades in the logs — nothing to diagnose")
        return 1

    # R-multiple. r_dollars is the trade's dollar risk at entry, so pnl / r_dollars is
    # sizing-normalised and comparable across years run at different equity levels.
    trades = trades[trades["r_dollars"] > 0].reset_index(drop=True)
    trades["R"] = trades["pnl"] / trades["r_dollars"]

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
                bn, bR, bmean, bwr = vt.attrs["base"]
                fh.write(f"\n**{mode}** baseline: n={bn}, total_R={bR:.2f}, "
                         f"mean_R={bmean:.4f}, win_rate={bwr:.4f}\n\n")
                fh.write(vt.to_markdown(index=False))
                fh.write("\n")

    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
