#!/usr/bin/env python3
"""
Forward-return analyzer for order-flow marks (Stage-2 front half).

Reconstructs signals by replaying the five features.py detectors over
Dukascopy tick history, labels each with a cost-aware triple-barrier R
outcome, and reports per mark x direction verdicts with a 70/30 IS/OOS
split and significance. The live {day}_signals.jsonl feed is analyzed as a
separate cohort. A sweep of dead/thin verdicts is a valid, money-saving
result — success is a trustworthy verdict, not a found edge.

    python scripts/analyze_signal_forward_returns.py --symbol XAUUSD \
        --start 2026-05-01 --end 2026-07-15
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_dukascopy_ticks import ensure_ticks  # noqa: E402
from src.microstructure import features as ft  # noqa: E402
from src.microstructure import forward_returns as fr  # noqa: E402

REPORT = PROJECT_ROOT / "reports" / "signal_forward_returns.md"
LIVE_DIR = PROJECT_ROOT / "data" / "ticks_live"


def reconstruct_events(df: pd.DataFrame, timeframe: str) -> list[dict]:
    """Replay all five detectors -> flat [{ts, kind, price}] table."""
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    evs = []
    evs += ft.delta_divergence(bars, delta)
    evs += ft.absorption_zones(df)
    evs += ft.imbalance_events(df, freq=timeframe)
    evs += ft.sweep_events(df)
    evs += ft.liquidity_withdrawal(df)
    return [{"ts": e.ts, "kind": e.kind, "price": float(e.price)} for e in evs]


def confirm_lag(kind: str, cfg: fr.LabelConfig) -> pd.Timedelta:
    """Delay from a detector's left-edge event ts to when the signal is actually
    knowable (bar/bucket complete; sweeps need the post-burst reversion window)."""
    if kind in ("bearish_divergence", "bullish_divergence",
                "imbalance_buy", "imbalance_sell"):
        return pd.Timedelta(cfg.timeframe)          # 15m bar must close
    if kind in ("absorption_of_selling", "absorption_of_buying"):
        return pd.Timedelta("2min")                 # absorption bucket
    if kind in ("sweep_high", "sweep_low"):
        return pd.Timedelta("70s")                  # 10s burst bucket + 60s revert window
    return pd.Timedelta(0)


def label_all(df: pd.DataFrame, events: list[dict], cfg: fr.LabelConfig) -> list[dict]:
    """Attach a triple-barrier outcome to each directional event.

    Entries are lagged per-kind (confirm_lag) so a signal is only tradeable
    once it is actually knowable — pandas resample labels bars/buckets by
    their LEFT edge, so event.ts is the bar START, not its close. ATR is
    looked up from the PREVIOUS completed bar (strictly causal). A same-kind
    cooldown prevents overlapping "trades" from inflating n via serial
    correlation.
    """
    bars = ft.resample_bars(df, cfg.timeframe)
    atr_series = fr.atr(bars, period=14).shift(1)
    mids = df["mid"]
    out = []
    cooldown_until: dict[str, pd.Timestamp] = {}
    for e in sorted(events, key=lambda e: e["ts"]):
        direction = fr.event_direction(e["kind"])
        if direction is None:
            continue
        entry_ts = e["ts"] + confirm_lag(e["kind"], cfg)
        if entry_ts < cooldown_until.get(e["kind"], entry_ts):
            continue
        bar_ts = e["ts"].floor(cfg.timeframe)
        if bar_ts not in atr_series.index:
            continue
        atr_val = atr_series.loc[bar_ts]
        if pd.isna(atr_val) or atr_val <= 0:
            continue
        path = mids.loc[entry_ts:]
        if path.empty:
            continue
        lab = fr.label_event(path, direction, float(atr_val), cfg)
        if lab is None:
            continue
        out.append({"ts": e["ts"], "kind": e["kind"], **lab})
        cooldown_until[e["kind"]] = entry_ts + cfg.max_hold_bars * pd.Timedelta(cfg.timeframe)
    return out


def load_live_events(symbol: str) -> list[dict]:
    """Parse Stage-1.5 {day}_signals.jsonl feeds into [{ts, kind, price}]."""
    root = LIVE_DIR / symbol
    evs = []
    if not root.exists():
        return evs
    for p in sorted(root.glob("*_signals.jsonl")):
        for line in p.read_text().splitlines():
            try:
                d = json.loads(line)
                evs.append({"ts": pd.Timestamp(d["bar_ts"]), "kind": d["kind"],
                            "price": float(d["price"])})
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return evs


def _table(cells: list[dict]) -> str:
    hdr = (f"| {'kind':22} | {'dir':5} | {'n':>4} | {'exp_R':>6} | {'PF':>5} "
           f"| {'win%':>5} | {'totR':>7} | {'t':>5} | {'IS':>6} | {'OOS':>6} "
           f"| {'mae':>6} | {'mfe':>6} | {'medTk':>6} | verdict |")
    sep = "|" + "|".join("-" * len(c) for c in hdr.split("|")[1:-1]) + "|"
    rows = [hdr, sep]
    for c in cells:
        pf = "inf" if c["profit_factor"] == float("inf") else f"{c['profit_factor']:.2f}"
        rows.append(
            f"| {c['kind']:22} | {c['direction']:5} | {c['n']:>4} | "
            f"{c['expectancy']:>6.2f} | {pf:>5} | {c['win_rate']*100:>4.0f}% | "
            f"{c['total_R']:>7.1f} | {c['t_stat']:>5.2f} | {c['exp_is']:>6.2f} | "
            f"{c['exp_oos']:>6.2f} | {c['mean_mae']:>6.2f} | {c['mean_mfe']:>6.2f} | "
            f"{c['median_ticks']:>6.1f} | {c['verdict']} |")
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Order-flow mark forward-return analyzer")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--timeframe", default="15min")
    p.add_argument("--sl-atr", type=float, default=1.0)
    p.add_argument("--tp-atr", type=float, default=2.0)
    p.add_argument("--max-hold", type=int, default=16)
    p.add_argument("--cost-pts", type=float, default=0.4)
    p.add_argument("--split-frac", type=float, default=0.7)
    args = p.parse_args()

    cfg = fr.LabelConfig(sl_atr=args.sl_atr, tp_atr=args.tp_atr,
                         max_hold_bars=args.max_hold, cost_pts=args.cost_pts,
                         timeframe=args.timeframe)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Fetching {args.symbol} ticks {start}..{end} …")
    ensure_ticks(args.symbol, start, end)
    df = ft.load_ticks(args.symbol, start, end)
    print(f"{len(df):,} ticks; reconstructing signals on {args.timeframe} …")

    hist = fr.summarize(label_all(df, reconstruct_events(df, args.timeframe), cfg),
                        split_frac=args.split_frac)
    live_raw = load_live_events(args.symbol)
    live = fr.summarize(label_all(df, live_raw, cfg), split_frac=args.split_frac) \
        if live_raw else {"boundary_ts": None, "cells": []}

    n_dir = len(hist["cells"])
    note = (f"{n_dir} directional cells tested; at p<0.05 expect ~{0.05*n_dir:.1f} "
            f"false positives by chance — treat a lone significant cell with suspicion.")
    print("\n=== HISTORICAL (reconstructed) ===")
    print(_table(hist["cells"]))
    print("\n" + note)
    if live["cells"]:
        print("\n=== LIVE cohort (real-time, thin OOS) ===")
        print(_table(live["cells"]))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w") as f:
        f.write(f"# Signal forward-return analysis — {args.symbol}\n\n")
        f.write(f"Range {start}..{end} · {args.timeframe} · triple-barrier "
                f"sl {args.sl_atr}×ATR / tp {args.tp_atr}×ATR / hold "
                f"{args.max_hold} bars / cost {args.cost_pts}pt/side · "
                f"IS/OOS split {args.split_frac:.0%} at {hist['boundary_ts']}\n\n")
        f.write("## Historical (reconstructed from tick history)\n\n")
        f.write(_table(hist["cells"]) + "\n\n")
        f.write(note + "\n\n")
        if live["cells"]:
            f.write("## Live cohort (Stage-1.5 feed, real-time, thin)\n\n")
            f.write(_table(live["cells"]) + "\n\n")
        cands = [c for c in hist["cells"] if c["verdict"] == "CANDIDATE"]
        f.write("## Bottom line\n\n")
        if cands:
            f.write("Candidate cell(s) that survived both halves + significance:\n")
            for c in cands:
                f.write(f"- **{c['kind']} {c['direction']}** — exp {c['expectancy']:.2f}R, "
                        f"PF {c['profit_factor']:.2f}, t {c['t_stat']:.2f}, n {c['n']}. "
                        f"Next: full backtest.md gate before any live use.\n")
        else:
            f.write("No cell cleared the CANDIDATE bar (n≥30/half, both halves "
                    "positive, t>2). On this sample the marks carry no tradeable "
                    "forward edge after costs — a valid, money-saving result.\n")
    print(f"\nReport written to {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
