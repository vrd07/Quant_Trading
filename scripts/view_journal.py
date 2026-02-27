#!/usr/bin/env python3
"""
Trade Analytics CLI — Run alongside or after the trading system.

Usage:
    python scripts/view_journal.py              # one-shot report
    python scripts/view_journal.py --live       # auto-refresh every 30s
    python scripts/view_journal.py --days 7     # look back 7 days

Shows:
  1. Per-trade P&L with strategy name, side, duration
  2. Strategy scorecard: usage %, win %, loss %, net P&L
  3. Daily summary with totals
"""

import sys
import os
import signal
import time
from pathlib import Path
from datetime import datetime, timedelta
import argparse

# ── setup paths ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_FILE = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"

# ── graceful shutdown ────────────────────────────────────────────────
_running = True

def _handle_signal(sig, frame):
    global _running
    _running = False

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── helpers ──────────────────────────────────────────────────────────

G = "\033[92m"      # green
R = "\033[91m"      # red
Y = "\033[93m"      # yellow
B = "\033[94m"      # blue
DIM = "\033[2m"     # dim
BOLD = "\033[1m"    # bold
RST = "\033[0m"     # reset


def color_pnl(val: float, fmt: str = "+.2f") -> str:
    c = G if val >= 0 else R
    return f"{c}{val:{fmt}}{RST}"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def load_trades(days: int = 30):
    """Load trade journal and filter by recency."""
    import pandas as pd

    if not JOURNAL_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(JOURNAL_FILE)
    if df.empty:
        return df

    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
    cutoff = datetime.now() - timedelta(days=days)
    df = df[df["exit_time"] >= cutoff].copy()
    df.sort_values("exit_time", ascending=False, inplace=True)
    return df


# ── section printers ─────────────────────────────────────────────────

def print_header(total_trades: int):
    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════╗{RST}")
    print(f"{BOLD}║          TRADE ANALYTICS REPORT                  ║{RST}")
    print(f"{BOLD}╚══════════════════════════════════════════════════╝{RST}")
    print(f"  {DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RST}  "
          f"│  {total_trades} trades")
    print()


def print_trade_log(df, n: int = 20):
    """Section 1: Per-trade P&L."""
    if df.empty:
        print(f"  {Y}No closed trades found.{RST}\n")
        return

    recent = df.head(n)

    print(f"  {BOLD}TRADE LOG{RST}  {DIM}(most recent {len(recent)}){RST}")
    print("  " + "─" * 88)
    print(f"  {'Exit Time':<20} {'Strategy':<16} {'Side':<5} "
          f"{'Entry':>9}  {'Exit':>9}  {'P&L ($)':>10}  {'P&L (%)':>8}  {'Dur':>6}")
    print("  " + "─" * 88)

    for _, t in recent.iterrows():
        dur = t.get("duration_seconds", 0) / 60
        pnl = t.get("realized_pnl", 0)
        pnl_pct = t.get("pnl_pct", 0)
        strat = str(t.get("strategy", "?"))[:15]
        side = str(t.get("side", "?"))[:4]
        entry = t.get("entry_price", 0)
        exit_ = t.get("exit_price", 0)
        exit_t = t["exit_time"].strftime("%m-%d %H:%M") if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:16]

        print(f"  {exit_t:<20} {strat:<16} {side:<5} "
              f"${entry:>8.2f}  ${exit_:>8.2f}  "
              f"{color_pnl(pnl, '+9.2f')}  "
              f"{color_pnl(pnl_pct, '+7.2f')}%  "
              f"{dur:>5.1f}m")

    print("  " + "─" * 88)
    total_pnl = df["realized_pnl"].sum()
    print(f"  {'':20} {'':16} {'':5} {'':9}  {'total':>9}  "
          f"${color_pnl(total_pnl, '+9.2f')}")
    print()


def print_strategy_scorecard(df):
    """Section 2: Strategy usage % and win/loss ratio."""
    if df.empty:
        return

    total = len(df)
    print(f"  {BOLD}STRATEGY SCORECARD{RST}")
    print("  " + "─" * 80)
    print(f"  {'Strategy':<16} {'Trades':>7} {'Usage':>6} "
          f"{'Wins':>5} {'Win%':>6} {'Loss%':>6} "
          f"{'Net P&L':>10} {'Avg P&L':>9} {'PF':>6}")
    print("  " + "─" * 80)

    rows = []
    for strat, grp in df.groupby("strategy"):
        cnt = len(grp)
        wins = int((grp["realized_pnl"] > 0).sum())
        losses = int((grp["realized_pnl"] < 0).sum())
        net = grp["realized_pnl"].sum()
        avg = grp["realized_pnl"].mean()
        gross_win = grp.loc[grp["realized_pnl"] > 0, "realized_pnl"].sum()
        gross_loss = abs(grp.loc[grp["realized_pnl"] < 0, "realized_pnl"].sum())
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

        rows.append((strat, cnt, cnt / total * 100, wins,
                      wins / cnt * 100, losses / cnt * 100,
                      net, avg, pf))

    rows.sort(key=lambda r: r[6], reverse=True)

    for strat, cnt, usage, wins, wpct, lpct, net, avg, pf in rows:
        pf_str = f"{pf:>5.2f}" if pf != float("inf") else "  ∞  "
        print(f"  {str(strat)[:15]:<16} {cnt:>7} {usage:>5.1f}% "
              f"{wins:>5} {wpct:>5.1f}% {lpct:>5.1f}% "
              f"${color_pnl(net, '+9.2f')} "
              f"${color_pnl(avg, '+8.2f')} "
              f"{pf_str}")

    # Totals
    total_pnl = df["realized_pnl"].sum()
    total_wins = int((df["realized_pnl"] > 0).sum())
    total_gross_win = df.loc[df["realized_pnl"] > 0, "realized_pnl"].sum()
    total_gross_loss = abs(df.loc[df["realized_pnl"] < 0, "realized_pnl"].sum())
    total_pf = total_gross_win / total_gross_loss if total_gross_loss > 0 else float("inf")
    total_pf_str = f"{total_pf:>5.2f}" if total_pf != float("inf") else "  ∞  "

    print("  " + "─" * 80)
    print(f"  {BOLD}{'TOTAL':<16}{RST} {total:>7} {100.0:>5.1f}% "
          f"{total_wins:>5} {total_wins / total * 100:>5.1f}% "
          f"{(total - total_wins) / total * 100:>5.1f}% "
          f"${color_pnl(total_pnl, '+9.2f')} "
          f"${color_pnl(total_pnl / total, '+8.2f')} "
          f"{total_pf_str}")
    print()


def print_daily_summary(df):
    """Section 3: Daily P&L breakdown."""
    if df.empty:
        return

    df["date"] = df["exit_time"].dt.date
    print(f"  {BOLD}DAILY P&L{RST}")
    print("  " + "─" * 56)
    print(f"  {'Date':<12} {'Trades':>7} {'Wins':>5} {'Win%':>6} "
          f"{'Net P&L':>10} {'Cum P&L':>10}")
    print("  " + "─" * 56)

    daily = df.groupby("date").agg(
        trades=("realized_pnl", "count"),
        wins=("realized_pnl", lambda x: (x > 0).sum()),
        net=("realized_pnl", "sum"),
    ).sort_index()

    cum = 0
    for date, row in daily.iterrows():
        cum += row["net"]
        wpct = row["wins"] / row["trades"] * 100 if row["trades"] else 0
        print(f"  {str(date):<12} {row['trades']:>7} {row['wins']:>5} {wpct:>5.1f}% "
              f"${color_pnl(row['net'], '+9.2f')} "
              f"${color_pnl(cum, '+9.2f')}")

    print("  " + "─" * 56)
    print()


# ── main ─────────────────────────────────────────────────────────────

def run_report(days: int = 30):
    df = load_trades(days)
    print_header(len(df))
    print_trade_log(df, n=25)
    print_strategy_scorecard(df)
    print_daily_summary(df)


def main():
    parser = argparse.ArgumentParser(description="Trade Analytics CLI")
    parser.add_argument("--live", action="store_true", help="Auto-refresh mode")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument("--interval", type=int, default=30, help="Refresh interval in seconds")
    args = parser.parse_args()

    if args.live:
        while _running:
            clear()
            run_report(args.days)
            print(f"  {DIM}Refreshing in {args.interval}s  (Ctrl+C to exit){RST}")
            for _ in range(args.interval * 2):
                if not _running:
                    break
                time.sleep(0.5)
    else:
        run_report(args.days)


if __name__ == "__main__":
    main()
