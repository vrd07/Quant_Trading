#!/usr/bin/env python3
"""
Backtest Runner - Run strategy backtests on historical data.

Usage:
    python scripts/run_backtest.py --strategy breakout --symbol XAUUSD --start 2024-01-01 --end 2024-12-31
"""

import sys
from pathlib import Path
import argparse
from datetime import datetime
from decimal import Decimal
import pandas as pd

# Add project root to path for proper imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.breakout_strategy import BreakoutStrategy
from src.strategies.mean_reversion_strategy import MeanReversionStrategy
from src.core.types import Symbol
import yaml


def load_historical_data(symbol: str, timeframe: str = "5m") -> pd.DataFrame:
    """
    Load historical data for backtesting.
    
    In production, this would load from:
    - Downloaded CSV files
    - Database
    - API (if available)
    
    For now, we'll use placeholder data or MT5 historical download.
    """
    # Check multiple possible data file locations
    data_files = [
        Path(f"data/historical/{symbol}_{timeframe}_real.csv"),  # Prioritize real data
        Path(f"data/historical/{symbol}_{timeframe}.csv"),
        Path(f"data/historical/{symbol}_{timeframe}_trending.csv"),
        Path(f"data/historical/{symbol}_{timeframe}_ranging.csv"),
    ]
    
    for data_file in data_files:
        if data_file.exists():
            print(f"✓ Loading data from {data_file}")
            df = pd.read_csv(data_file, parse_dates=['timestamp'])
            return df
    
    print(f"✗ No historical data found for {symbol}")
    print(f"\nSearched locations:")
    for f in data_files:
        print(f"  - {f}")
    print(f"\nTo generate sample data:")
    print(f"  python scripts/generate_sample_data.py")
    sys.exit(1)


def create_strategy(strategy_name: str, symbol: Symbol, config: dict):
    """Create strategy instance."""
    if strategy_name == "breakout":
        return BreakoutStrategy(symbol, config.get('strategies', {}).get('breakout', {}))
    elif strategy_name == "mean_reversion":
        return MeanReversionStrategy(symbol, config.get('strategies', {}).get('mean_reversion', {}))
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")


def print_results(result):
    """Print backtest results in readable format."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    
    print("\n📊 Performance Summary:")
    print(f"   Total Return:     ${result.total_return:,.2f} ({result.total_return_pct:+.2f}%)")
    print(f"   Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    print(f"   Sortino Ratio:    {result.sortino_ratio:.2f}")
    print(f"   Max Drawdown:     ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")
    
    print("\n📈 Trade Statistics:")
    print(f"   Total Trades:     {result.total_trades}")
    print(f"   Winning Trades:   {result.winning_trades} ({result.win_rate:.1f}%)")
    print(f"   Losing Trades:    {result.losing_trades}")
    print(f"   Profit Factor:    {result.profit_factor:.2f}")
    print(f"   Expectancy:       ${result.expectancy:.2f}")
    
    print("\n💰 Trade Details:")
    print(f"   Average Win:      ${result.avg_win:,.2f}")
    print(f"   Average Loss:     ${result.avg_loss:,.2f}")
    print(f"   Largest Win:      ${result.largest_win:,.2f}")
    print(f"   Largest Loss:     ${result.largest_loss:,.2f}")
    
    print("\n" + "=" * 60)
    
    # Interpretation
    print("\n💡 Interpretation:")
    
    if result.sharpe_ratio > 2:
        print("   ✓ Excellent Sharpe ratio (>2)")
    elif result.sharpe_ratio > 1:
        print("   ✓ Good Sharpe ratio (1-2)")
    elif result.sharpe_ratio > 0:
        print("   ⚠️  Low Sharpe ratio (<1)")
    else:
        print("   ✗ Negative Sharpe ratio (losing strategy)")
    
    if result.win_rate > 50:
        print(f"   ✓ Win rate above 50% ({result.win_rate:.1f}%)")
    else:
        print(f"   ⚠️  Win rate below 50% ({result.win_rate:.1f}%)")
    
    if result.profit_factor > 1.5:
        print(f"   ✓ Good profit factor ({result.profit_factor:.2f})")
    elif result.profit_factor > 1:
        print(f"   ⚠️  Marginal profit factor ({result.profit_factor:.2f})")
    else:
        print(f"   ✗ Poor profit factor ({result.profit_factor:.2f})")
    
    if abs(result.max_drawdown_pct) < 10:
        print(f"   ✓ Low drawdown ({abs(result.max_drawdown_pct):.1f}%)")
    elif abs(result.max_drawdown_pct) < 20:
        print(f"   ⚠️  Moderate drawdown ({abs(result.max_drawdown_pct):.1f}%)")
    else:
        print(f"   ✗ High drawdown ({abs(result.max_drawdown_pct):.1f}%)")
    
    print("\n" + "=" * 60)


def save_results(result, output_file: str):
    """Save backtest results to files."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Save equity curve
    equity_file = output_path.with_suffix('.csv')
    result.equity_curve.to_csv(equity_file)
    print(f"\n✓ Equity curve saved to: {equity_file}")
    
    # Save trades
    trades_file = output_path.with_name(f"{output_path.stem}_trades.csv")
    trades_df = pd.DataFrame(result.trades)
    trades_df.to_csv(trades_file, index=False)
    print(f"✓ Trade list saved to: {trades_file}")
    
    # Save summary
    summary_file = output_path.with_name(f"{output_path.stem}_summary.txt")
    with open(summary_file, 'w') as f:
        f.write("BACKTEST RESULTS SUMMARY\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total Return: ${result.total_return:,.2f} ({result.total_return_pct:+.2f}%)\n")
        f.write(f"Sharpe Ratio: {result.sharpe_ratio:.2f}\n")
        f.write(f"Max Drawdown: {result.max_drawdown_pct:.2f}%\n")
        f.write(f"Win Rate: {result.win_rate:.1f}%\n")
        f.write(f"Total Trades: {result.total_trades}\n")
        f.write(f"Profit Factor: {result.profit_factor:.2f}\n")
    print(f"✓ Summary saved to: {summary_file}")


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument('--strategy', required=True, choices=['breakout', 'mean_reversion'], help='Strategy to test')
    parser.add_argument('--symbol', required=True, help='Symbol to test (e.g., XAUUSD)')
    parser.add_argument('--timeframe', default='5m', help='Timeframe (default: 5m)')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=10000, help='Initial capital')
    parser.add_argument('--commission', type=float, default=0, help='Commission per trade')
    parser.add_argument('--slippage', default='realistic', choices=['fixed', 'realistic', 'aggressive'], help='Slippage model')
    parser.add_argument('--output', default='data/backtests/backtest_result', help='Output file prefix')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("BACKTEST CONFIGURATION")
    print("=" * 60)
    print(f"Strategy:         {args.strategy}")
    print(f"Symbol:           {args.symbol}")
    print(f"Timeframe:        {args.timeframe}")
    print(f"Initial Capital:  ${args.capital:,.2f}")
    print(f"Commission:       ${args.commission:.2f}")
    print(f"Slippage Model:   {args.slippage}")
    if args.start:
        print(f"Start Date:       {args.start}")
    if args.end:
        print(f"End Date:         {args.end}")
    print("=" * 60)
    
    # Load configuration
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)
    
    # Create symbol
    symbol_config = config.get('symbols', {}).get(args.symbol, {})
    symbol = Symbol(
        ticker=args.symbol,
        pip_value=Decimal(str(symbol_config.get('pip_value', 0.01))),
        min_lot=Decimal(str(symbol_config.get('min_lot', 0.01))),
        max_lot=Decimal(str(symbol_config.get('max_lot', 100))),
        lot_step=Decimal(str(symbol_config.get('lot_step', 0.01))),
        value_per_lot=Decimal(str(symbol_config.get('value_per_lot', 1)))
    )
    
    # Load historical data
    print("\n📁 Loading historical data...")
    bars = load_historical_data(args.symbol, args.timeframe)
    print(f"✓ Loaded {len(bars)} bars")
    print(f"  Date range: {bars['timestamp'].min()} to {bars['timestamp'].max()}")
    
    # Create strategy
    print(f"\n🎯 Creating {args.strategy} strategy...")
    strategy = create_strategy(args.strategy, symbol, config)
    print(f"✓ Strategy created")
    
    # Create backtest engine
    print("\n🔧 Initializing backtest engine...")
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=Decimal(str(args.capital)),
        risk_config=config,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage
    )
    print("✓ Engine ready")
    
    # Run backtest
    print("\n▶️  Running backtest...")
    result = engine.run(
        bars=bars,
        start_date=args.start,
        end_date=args.end
    )
    
    # Print results
    print_results(result)
    
    # Save results
    save_results(result, args.output)
    
    print("\n✓ Backtest complete!")


if __name__ == "__main__":
    main()
