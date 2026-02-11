#!/usr/bin/env python3
"""
Script to analyze the trade journal and print strategy performance.
"""

import sys
import pandas as pd
from pathlib import Path
from decimal import Decimal

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_FILE = PROJECT_ROOT / "data/logs/trade_journal.csv"

def main():
    if not JOURNAL_FILE.exists():
        print(f"Error: Journal file not found at {JOURNAL_FILE}")
        return

    try:
        df = pd.read_csv(JOURNAL_FILE)
    except pd.errors.EmptyDataError:
        print("Journal file is empty.")
        return

    if df.empty:
        print("No trades recorded yet.")
        return

    print("=" * 60)
    print("TRADE JOURNAL ANALYSIS")
    print("=" * 60)
    print(f"Total Trades: {len(df)}")
    print(f"Total P&L:    ${df['realized_pnl'].sum():.2f}")
    
    # Calculate win rate
    wins = df[df['realized_pnl'] > 0]
    win_rate = (len(wins) / len(df)) * 100 if len(df) > 0 else 0
    print(f"Win Rate:     {win_rate:.1f}%")
    print("-" * 60)

    # Group by Strategy
    if 'strategy' in df.columns:
        print("\nPERFORMANCE BY STRATEGY:")
        print(f"{'STRATEGY':<20} | {'TRADES':<6} | {'WIN%':<6} | {'P&L ($)':<10}")
        print("-" * 50)
        
        strategy_stats = df.groupby('strategy').agg(
            trades=('trade_id', 'count'),
            pnl=('realized_pnl', 'sum'),
            wins=('realized_pnl', lambda x: (x > 0).sum())
        )
        
        strategy_stats['win_rate'] = (strategy_stats['wins'] / strategy_stats['trades']) * 100
        
        for strategy, row in strategy_stats.iterrows():
            print(f"{strategy:<20} | {row['trades']:<6} | {row['win_rate']:>5.1f}% | ${row['pnl']:>9.2f}")
    
    print("=" * 60)

    # Show recent trades
    print("\nRECENT TRADES (Last 5):")
    recent = df.tail(5)
    print(f"{'TIME':<20} | {'SYMBOL':<8} | {'SIDE':<5} | {'STRATEGY':<15} | {'P&L'}")
    print("-" * 65)
    for _, row in recent.iterrows():
        time_str = row['exit_time'][:19].replace('T', ' ')
        print(f"{time_str:<20} | {row['symbol']:<8} | {row['side']:<5} | {row['strategy']:<15} | ${row['realized_pnl']:.2f}")

if __name__ == "__main__":
    main()
