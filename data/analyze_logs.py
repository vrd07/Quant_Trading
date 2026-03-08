import pandas as pd
from datetime import datetime, timedelta

def main():
    try:
        # Load the trade journal
        df = pd.read_csv('/Users/varadbandekar/Documents/Quant_trading/data/logs/trade_journal.csv')
    except Exception as e:
        print(f"Error loading trade journal: {e}")
        return

    if df.empty:
        print("Trade journal is empty.")
        return

    # Convert dates
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    df['exit_time'] = pd.to_datetime(df['exit_time'])

    print("=== OVERALL METRICS ===")
    print(f"Total Trades: {len(df)}")
    wins = df[df['realized_pnl'] > 0]
    losses = df[df['realized_pnl'] < 0]
    breakevens = df[df['realized_pnl'] == 0]
    
    print(f"Wins: {len(wins)}, Losses: {len(losses)}, Breakevens: {len(breakevens)}")
    print(f"Win Rate: {(len(wins)/len(df))*100:.2f}%" if len(df) > 0 else "0%")
    print(f"Total PNL: ${df['realized_pnl'].sum():.2f}")
    
    print(f"Average Win: ${wins['realized_pnl'].mean():.2f}" if not wins.empty else "$0.00")
    print(f"Average Loss: ${losses['realized_pnl'].mean():.2f}" if not losses.empty else "$0.00")
    
    print("\n=== STRATEGY BREAKDOWN ===")
    strategy_group = df.groupby('strategy').agg(
        trades=('realized_pnl', 'count'),
        total_pnl=('realized_pnl', 'sum'),
        win_rate=('realized_pnl', lambda x: (x > 0).mean() * 100),
        avg_win=('realized_pnl', lambda x: x[x > 0].mean() if len(x[x > 0]) > 0 else 0),
        avg_loss=('realized_pnl', lambda x: x[x < 0].mean() if len(x[x < 0]) > 0 else 0)
    ).round(2)
    print(strategy_group)

    print("\n=== LOT SIZE BREAKDOWN ===")
    lot_group = df.groupby('quantity').agg(
        trades=('realized_pnl', 'count'),
        total_pnl=('realized_pnl', 'sum')
    ).round(2)
    print(lot_group)

    print("\n=== TIME OF DAY BREAKDOWN (UTC HOUR) ===")
    df['hour'] = df['entry_time'].dt.hour
    hour_group = df.groupby('hour').agg(
        trades=('realized_pnl', 'count'),
        total_pnl=('realized_pnl', 'sum'),
        win_rate=('realized_pnl', lambda x: (x > 0).mean() * 100)
    ).round(2)
    print(hour_group)
    
if __name__ == "__main__":
    main()
