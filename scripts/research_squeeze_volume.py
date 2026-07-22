#!/usr/bin/env python3
"""
Squeeze-breakout volume-filter smell-test (front half, GC free data).

Reconstructs squeeze_breakout signals on local XAUUSD ticks, attaches causal
GC coil/break relative volume to each, labels with the strategy's native
33pt/66pt geometry, and reports the win/R split by volume bucket with a
GREEN/RED verdict. GREEN = worth BUYING multi-year GC data; it is NOT a live
signal (~12-18 trades). See the design spec dated 2026-07-21.

    ./venv/bin/python scripts/research_squeeze_volume.py \
        --start 2026-05-08 --end 2026-07-15
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.types import Symbol  # noqa: E402
from src.microstructure import features as ft  # noqa: E402
from src.microstructure import squeeze_volume as sv  # noqa: E402
from src.strategies.squeeze_breakout_strategy import (  # noqa: E402
    SqueezeBreakoutStrategy)

REPORT = PROJECT_ROOT / "reports" / "squeeze_volume_smell_test.md"


def reconstruct_squeeze_signals(bars15: pd.DataFrame) -> list[dict]:
    """Replay the REAL strategy over a growing window (cooldown latch intact)."""
    strat = SqueezeBreakoutStrategy(Symbol(ticker="XAUUSD"), {"enabled": True})
    min_bars = max(strat.pct_window + strat.donch + 5, strat.htf_ema_period)
    sigs = []
    for i in range(min_bars, len(bars15) + 1):
        window = bars15.iloc[:i]
        s = strat.on_bar(window)
        if s is not None:
            # resample_bars labels bars by LEFT edge, so window.index[-1] is the bar's
            # OPEN. The breakout is decided at the bar CLOSE; walking mids from the
            # close forward is causal (bar-start would resolve the trade on ticks that
            # predate the signal — a look-ahead bug). This close time also feeds the GC
            # volume lookup, which must only see hours completed by the decision moment.
            ts = pd.Timestamp(window.index[-1]) + pd.Timedelta("15min")
            sigs.append({
                "ts": ts,
                "side": s.side.value if hasattr(s.side, "value") else str(s.side),
                "entry": float(s.entry_price),
                "stop": float(s.stop_loss),
                "target": float(s.take_profit),
            })
    return sigs


def _fmt_bucket(name: str, b: dict) -> str:
    return (f"| {name:5} | {b['n']:>3} | {b['win']*100:>4.0f}% "
            f"| {b['mean_R']:>+6.2f} |")


def _split_table(title: str, s: dict) -> str:
    rows = [f"**{title}** (median {s['median']:.2f})",
            "", "| bkt | n | win% | mean_R |", "|-----|---|------|--------|",
            _fmt_bucket("high", s["high"]), _fmt_bucket("low", s["low"]), ""]
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Squeeze volume-filter smell-test")
    p.add_argument("--symbol", default="XAUUSD")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD inclusive (UTC)")
    p.add_argument("--cost-pts", type=float, default=0.5)
    args = p.parse_args()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    print(f"Loading {args.symbol} ticks {start}..{end} …")
    df = ft.load_ticks(args.symbol, start, end)
    bars15 = ft.resample_bars(df, "15min")
    mids = df["mid"]
    print(f"{len(df):,} ticks, {len(bars15):,} 15m bars; reconstructing signals …")
    sigs = reconstruct_squeeze_signals(bars15)
    print(f"{len(sigs)} squeeze breakouts")

    print("Loading GC hourly volume …")
    gc = sv.load_gc_hourly(start, end)

    trades = []
    for s in sigs:
        ts = s["ts"]
        lab = sv.label_native(mids.loc[ts:], s["side"], s["entry"],
                              s["stop"], s["target"], cost_pts=args.cost_pts)
        if lab is None:
            continue
        trades.append({
            "side": s["side"], "R": lab["R"], "outcome": lab["outcome"],
            "break_rvol": sv.break_rvol(gc, ts),
            "coil_rvol": sv.coil_rvol(gc, ts),
        })

    sells = [t for t in trades if t["side"] == "SELL"]
    brk = sv.split_stats(trades, "break_rvol")
    coil = sv.split_stats(trades, "coil_rvol")
    sell_brk = sv.split_stats(sells, "break_rvol")
    vdt = sv.verdict(trades, "break_rvol")

    body = [
        f"# Squeeze-breakout volume-filter smell-test — {args.symbol}",
        "",
        f"Range {start}..{end} · {len(trades)} labeled squeeze breakouts "
        f"({len(sells)} SELL) · native 33/66pt geometry · cost "
        f"{args.cost_pts}pt/side · GC=F hourly volume.",
        "",
        "## Break RVOL split (all trades)", "", _split_table("break_rvol", brk),
        "## Coil RVOL split (all trades)", "", _split_table("coil_rvol", coil),
        "## Break RVOL split (SELL only — the bleed)", "",
        _split_table("break_rvol · SELL", sell_brk),
        "## Verdict", "",
        f"**{vdt}** on break_rvol.",
        "",
        "⚠️ This sample is ~12–18 trades. A clean split can occur by chance, so "
        "GREEN justifies BUYING multi-year GC data for a proper every-year test "
        "— it does NOT justify any live change. RED = drop the hypothesis.",
        "",
        "Caveats: GC is COMEX futures (not spot XAUUSD; ~23h session, maintenance "
        "break) — volume used only as a relative percentile. yfinance GC daily "
        "volume is broken; hourly only. 1h volume is coarser than the 15m break; "
        "break_rvol uses the last COMPLETED hour (causal, lagged ≤1h).",
        "",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(body))
    print("\n".join(body))
    print(f"\nReport written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
