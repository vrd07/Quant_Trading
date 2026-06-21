#!/usr/bin/env python3
"""
Decay-floor strategy allocator (nightly).

Reads realised closed trades from the trade journal, measures each strategy's
TRAILING edge, and writes a weights file consumed by the risk engine. A strategy
whose trailing edge has gone negative gets weight 0 → the risk engine vetoes its
signals until the edge recovers; positive/insufficient-data → weight 1.

This is the only lever this session validated as worth shipping (2026-06-21): every
FIXED entry filter on kalman flattered 2026 and failed 2025 OOS, because each bets
the next regime resembles the tuned one. The decay-floor doesn't predict the regime
— it just stops funding whatever is currently losing. Binary 0/1 (sidesteps the
pinned-min-lot fractional-sizing problem); fail-open (missing data ⇒ keep = 1).

Run nightly (e.g. via /schedule or cron):
    python scripts/strategy_allocator.py --journal data/logs/trade_journal_<stem>.csv

Enable consumption by setting in the active config:
    risk:
      decay_floor:
        enabled: true
        weights_file: data/strategy_risk_weights.json
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_OUT = PROJECT_ROOT / "data/strategy_risk_weights.json"
EXCLUDE = {"manual"}


def resolve_journal() -> Path:
    """Pick the journal for the currently-active account (ACTIVE_CONFIG), so the
    scheduled job tracks whichever config the user is running day-to-day. Falls
    back to the base journal, then to a non-existent path (→ fail-open all-keep)."""
    active = PROJECT_ROOT / "config/ACTIVE_CONFIG"
    if active.exists():
        stem = Path(active.read_text().strip()).stem      # e.g. config_live_1000
        cand = PROJECT_ROOT / f"data/logs/trade_journal_{stem}.csv"
        if cand.exists():
            return cand
    base = PROJECT_ROOT / "data/logs/trade_journal.csv"
    return base


def compute_weights(trades: pd.DataFrame, *, window_days: int = 45,
                    min_trades: int = 8, sharpe_floor: float = 0.0,
                    asof: pd.Timestamp = None) -> dict:
    """Decay-floor weights from trailing realised R. Pure / testable.

    trades needs: strategy, exit_time (datetime), realized_pnl, initial_risk.
    Rule per strategy (trailing window_days):
      - < min_trades closed         → 1.0  (insufficient data; never starve)
      - trailing daily-R Sharpe >= floor → 1.0
      - else                        → 0.0  (defund until it recovers)
    """
    if len(trades) == 0:
        return {}
    t = trades.copy()
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True, errors="coerce")
    t = t.dropna(subset=["exit_time", "realized_pnl", "strategy"])
    t = t[~t["strategy"].astype(str).str.lower().isin(EXCLUDE)]
    if asof is None:
        asof = t["exit_time"].max()
    lo = asof - pd.Timedelta(days=window_days)
    t = t[t["exit_time"] > lo]

    # R = realised_pnl / initial_risk (drop rows with non-positive/invalid risk)
    t["initial_risk"] = pd.to_numeric(t["initial_risk"], errors="coerce")
    t = t[t["initial_risk"] > 0]
    t["R"] = pd.to_numeric(t["realized_pnl"], errors="coerce") / t["initial_risk"]
    t = t.dropna(subset=["R"])

    weights = {}
    for strat, sub in t.groupby("strategy"):
        if len(sub) < min_trades:
            weights[str(strat)] = 1.0
            continue
        daily = sub.groupby(sub["exit_time"].dt.normalize())["R"].sum()
        mu, sd = daily.mean(), daily.std(ddof=1)
        if len(daily) < 2 or not np.isfinite(sd) or sd == 0:
            sharpe = float(np.sign(mu))            # degenerate → sign of edge
        else:
            sharpe = float(mu / sd)
        weights[str(strat)] = 1.0 if sharpe >= sharpe_floor else 0.0
    return weights


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default=None,
                    help="trade journal CSV (default: auto from config/ACTIVE_CONFIG)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--window-days", type=int, default=45)
    ap.add_argument("--min-trades", type=int, default=8)
    ap.add_argument("--sharpe-floor", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    jp = Path(args.journal) if args.journal else resolve_journal()
    print(f"[allocator] journal: {jp}")
    if not jp.exists():
        print(f"[allocator] journal not found: {jp} — writing all-keep (fail-open)")
        trades = pd.DataFrame(columns=["strategy", "exit_time", "realized_pnl", "initial_risk"])
    else:
        trades = pd.read_csv(jp)

    weights = compute_weights(trades, window_days=args.window_days,
                              min_trades=args.min_trades, sharpe_floor=args.sharpe_floor)
    defunded = sorted(k for k, v in weights.items() if v <= 0)
    print(f"[allocator] {len(weights)} strategies; defunded: {defunded or 'none'}")
    for k in sorted(weights):
        print(f"    {k:<20} {weights[k]:.0f}")

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "journal": str(jp),
        "window_days": args.window_days,
        "min_trades": args.min_trades,
        "sharpe_floor": args.sharpe_floor,
        "weights": weights,
    }
    if args.dry_run:
        print("[allocator] --dry-run: not writing")
        return
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    print(f"[allocator] wrote -> {args.out}")


if __name__ == "__main__":
    main()
