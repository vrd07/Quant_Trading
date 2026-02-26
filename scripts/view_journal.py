#!/usr/bin/env python3
"""
Live Trade Journal Dashboard
=============================
Runs alongside the trading system and continuously displays:
  - Per-strategy trade counts and P&L percentages
  - Overall summary statistics

Auto-cleans journal entries older than 2 days on each refresh.
Stop with Ctrl+C.
"""

import sys
import os
import time
import signal
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_FILE = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"
REFRESH_INTERVAL = 30  # seconds between refreshes

# ── graceful shutdown ────────────────────────────────────────────────
_running = True

def _handle_signal(sig, frame):
    global _running
    _running = False

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── log cleanup ──────────────────────────────────────────────────────
def cleanup_old_entries(df: pd.DataFrame, days: int = 2) -> pd.DataFrame:
    """Remove rows whose exit_time is older than `days` days and rewrite the CSV."""
    if df.empty or 'exit_time' not in df.columns:
        return df

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df['_exit_dt'] = pd.to_datetime(df['exit_time'], utc=True, errors='coerce')
    fresh = df[df['_exit_dt'] >= cutoff].drop(columns=['_exit_dt'])
    removed = len(df) - len(fresh)

    if removed > 0:
        # Rewrite CSV with only fresh entries
        fresh.to_csv(JOURNAL_FILE, index=False)
        print(f"  🗑  Cleaned up {removed} entries older than {days} days")

    if '_exit_dt' in df.columns:
        df = df.drop(columns=['_exit_dt'])

    return fresh


# ── display helpers ──────────────────────────────────────────────────
def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def print_header(trade_count: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║              📊  LIVE TRADE JOURNAL DASHBOARD              ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print(f"  Last refresh : {now}")
    print(f"  Total trades : {trade_count}")
    print(f"  Refreshing every {REFRESH_INTERVAL}s  |  Ctrl+C to stop")
    print()


def print_strategy_table(df: pd.DataFrame):
    """Print per-strategy breakdown."""
    if 'strategy' not in df.columns:
        print("  ⚠  No 'strategy' column found in journal.")
        return

    print("┌────────────────────────┬────────┬─────────┬────────────┬────────────┐")
    print("│  Strategy              │ Trades │ Win %   │ Total P&L  │ Avg P&L %  │")
    print("├────────────────────────┼────────┼─────────┼────────────┼────────────┤")

    stats = df.groupby('strategy').agg(
        trades   = ('trade_id', 'count'),
        total_pnl= ('realized_pnl', 'sum'),
        avg_pct  = ('pnl_pct', 'mean'),
        wins     = ('realized_pnl', lambda x: (x > 0).sum())
    )
    stats['win_rate'] = (stats['wins'] / stats['trades']) * 100

    for strat, row in stats.iterrows():
        pnl_color = "\033[32m" if row['total_pnl'] >= 0 else "\033[31m"
        reset     = "\033[0m"
        print(
            f"│  {strat:<20s}  │ {int(row['trades']):>6} │ {row['win_rate']:>5.1f} % "
            f"│ {pnl_color}${row['total_pnl']:>+9.2f}{reset} "
            f"│ {pnl_color}{row['avg_pct']:>+8.2f} %{reset} │"
        )

    print("└────────────────────────┴────────┴─────────┴────────────┴────────────┘")
    print()


def print_overall_summary(df: pd.DataFrame):
    """Print aggregated stats."""
    total_pnl = df['realized_pnl'].sum()
    avg_pct   = df['pnl_pct'].mean()
    wins      = (df['realized_pnl'] > 0).sum()
    losses    = (df['realized_pnl'] < 0).sum()
    win_rate  = (wins / len(df)) * 100 if len(df) > 0 else 0
    avg_dur   = df['duration_seconds'].mean() / 60 if 'duration_seconds' in df.columns else 0

    pnl_color = "\033[32m" if total_pnl >= 0 else "\033[31m"
    reset     = "\033[0m"

    print("── Overall Summary ─────────────────────────────────────────────")
    print(f"  Wins / Losses : {wins}W  /  {losses}L  ({win_rate:.1f}% win rate)")
    print(f"  Total P&L     : {pnl_color}${total_pnl:+.2f}{reset}")
    print(f"  Avg P&L %     : {pnl_color}{avg_pct:+.2f} %{reset}")
    print(f"  Avg Duration  : {avg_dur:.1f} min")
    print("─────────────────────────────────────────────────────────────────")
    print()


def print_recent_trades(df: pd.DataFrame, n: int = 5):
    """Show the most recent N trades."""
    recent = df.tail(n)
    if recent.empty:
        return

    print(f"── Last {n} Trades ──────────────────────────────────────────────")
    print(f"  {'Time':<20s}  {'Symbol':<8s}  {'Side':<5s}  {'Strategy':<15s}  {'P&L':>10s}  {'P&L %':>8s}")
    print(f"  {'─'*20}  {'─'*8}  {'─'*5}  {'─'*15}  {'─'*10}  {'─'*8}")

    for _, row in recent.iterrows():
        try:
            t = str(row['exit_time'])[:19].replace('T', ' ')
        except Exception:
            t = '—'

        pnl_val = row.get('realized_pnl', 0)
        pct_val = row.get('pnl_pct', 0)
        pnl_color = "\033[32m" if pnl_val >= 0 else "\033[31m"
        reset     = "\033[0m"

        print(
            f"  {t:<20s}  {str(row.get('symbol','')):<8s}  "
            f"{str(row.get('side','')):<5s}  {str(row.get('strategy','')):<15s}  "
            f"{pnl_color}${pnl_val:>+9.2f}{reset}  {pnl_color}{pct_val:>+6.2f} %{reset}"
        )
    print()


# ── main loop ────────────────────────────────────────────────────────
def main():
    print("Starting Live Trade Journal Dashboard …")
    print(f"Watching: {JOURNAL_FILE}")
    print(f"Press Ctrl+C to stop.\n")

    while _running:
        # ── read journal ──
        if not JOURNAL_FILE.exists():
            clear_screen()
            print_header(0)
            print("  ⏳  Waiting for journal file to be created …")
            _sleep(REFRESH_INTERVAL)
            continue

        try:
            df = pd.read_csv(JOURNAL_FILE)
        except pd.errors.EmptyDataError:
            clear_screen()
            print_header(0)
            print("  ⏳  Journal file exists but is empty. Waiting for trades …")
            _sleep(REFRESH_INTERVAL)
            continue

        if df.empty:
            clear_screen()
            print_header(0)
            print("  ⏳  No trades recorded yet. Waiting …")
            _sleep(REFRESH_INTERVAL)
            continue

        # ── cleanup old entries ──
        df = cleanup_old_entries(df, days=2)

        if df.empty:
            clear_screen()
            print_header(0)
            print("  ⏳  All entries were older than 2 days and have been cleaned up.")
            _sleep(REFRESH_INTERVAL)
            continue

        # ── render dashboard ──
        clear_screen()
        print_header(len(df))
        print_strategy_table(df)
        print_overall_summary(df)
        print_recent_trades(df)

        _sleep(REFRESH_INTERVAL)

    # ── shutdown ──
    print("\n✅  Dashboard stopped. Goodbye!")


def _sleep(seconds: int):
    """Sleep in small increments so we can respond to Ctrl+C quickly."""
    for _ in range(seconds * 2):
        if not _running:
            break
        time.sleep(0.5)


if __name__ == "__main__":
    main()
