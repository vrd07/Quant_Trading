#!/usr/bin/env python3
"""
Backtest Runner - Run strategy backtests on historical data.

Usage:
    python scripts/run_backtest.py --strategy breakout --symbol XAUUSD
    python scripts/run_backtest.py --strategy all --symbol XAUUSD --config config/config_live_50000.yaml
"""

import sys
import logging
from pathlib import Path
import argparse
from decimal import Decimal
import pandas as pd

# Suppress verbose per-bar strategy logging during backtest runs
# (strategies log INFO for every "no signal" reason change, which floods output)
logging.disable(logging.INFO)

# Add project root to path for proper imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.breakout_strategy import BreakoutStrategy
from src.strategies.mean_reversion_strategy import MeanReversionStrategy
from src.strategies.momentum_strategy import MomentumStrategy
from src.strategies.vwap_strategy import VWAPStrategy
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
from src.strategies.mini_medallion_strategy import MiniMedallionStrategy
from src.strategies.structure_break_retest import StructureBreakRetestStrategy
from src.core.types import Symbol
import yaml

STRATEGY_CHOICES = ['breakout', 'mean_reversion', 'momentum', 'vwap', 'kalman_regime', 'mini_medallion', 'sbr', 'all']


def load_historical_data(symbol: str, timeframe: str = "5m") -> pd.DataFrame:
    data_files = [
        Path(f"data/historical/{symbol}_{timeframe}_real.csv"),
        Path(f"data/historical/{symbol}_{timeframe}.csv"),
        Path(f"data/historical/{symbol}_{timeframe}_trending.csv"),
        Path(f"data/historical/{symbol}_{timeframe}_ranging.csv"),
    ]

    for data_file in data_files:
        if data_file.exists():
            print(f"  Loading data from {data_file}")
            df = pd.read_csv(data_file, parse_dates=['timestamp'], index_col='timestamp')
            return df

    print(f"  No historical data found for {symbol}")
    print(f"\nSearched locations:")
    for f in data_files:
        print(f"  - {f}")
    print(f"\nTo generate sample data:")
    print(f"  python scripts/generate_sample_data.py")
    sys.exit(1)


def create_symbol(symbol_name: str, config: dict) -> Symbol:
    symbol_config = config.get('symbols', {}).get(symbol_name, {})
    return Symbol(
        ticker=symbol_name,
        pip_value=Decimal(str(symbol_config.get('pip_value', 0.01))),
        min_lot=Decimal(str(symbol_config.get('min_lot', 0.01))),
        max_lot=Decimal(str(symbol_config.get('max_lot', 100))),
        lot_step=Decimal(str(symbol_config.get('lot_step', 0.01))),
        value_per_lot=Decimal(str(symbol_config.get('value_per_lot', 1))),
        min_stops_distance=Decimal(str(symbol_config.get('min_stops_distance', 0))),
        leverage=Decimal(str(symbol_config.get('leverage', 1))),
    )


def create_strategy(strategy_name: str, symbol: Symbol, config: dict):
    strats = config.get('strategies', {})
    if strategy_name == 'breakout':
        return BreakoutStrategy(symbol, strats.get('breakout', {}))
    elif strategy_name == 'mean_reversion':
        return MeanReversionStrategy(symbol, strats.get('mean_reversion', {}))
    elif strategy_name == 'momentum':
        return MomentumStrategy(symbol, strats.get('momentum', {}))
    elif strategy_name == 'vwap':
        return VWAPStrategy(symbol, strats.get('vwap', {}))
    elif strategy_name == 'kalman_regime':
        return KalmanRegimeStrategy(symbol, strats.get('kalman_regime', {}))
    elif strategy_name == 'mini_medallion':
        return MiniMedallionStrategy(symbol, strats.get('mini_medallion', {}))
    elif strategy_name == 'sbr':
        return StructureBreakRetestStrategy(symbol, strats.get('sbr', {}))
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}")


def print_results(result, strategy_name: str):
    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS — {strategy_name.upper()}")
    print("=" * 60)

    print("\nPerformance Summary:")
    print(f"  Total Return:     ${result.total_return:,.2f} ({result.total_return_pct:+.2f}%)")
    print(f"  Sharpe Ratio:     {result.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio:    {result.sortino_ratio:.2f}")
    print(f"  Max Drawdown:     ${result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)")

    print("\nTrade Statistics:")
    print(f"  Total Trades:     {result.total_trades}")
    print(f"  Winning Trades:   {result.winning_trades} ({result.win_rate:.1f}%)")
    print(f"  Losing Trades:    {result.losing_trades}")
    print(f"  Profit Factor:    {result.profit_factor:.2f}")
    print(f"  Expectancy:       ${result.expectancy:.2f}")

    print("\nTrade Details:")
    print(f"  Average Win:      ${result.avg_win:,.2f}")
    print(f"  Average Loss:     ${result.avg_loss:,.2f}")
    print(f"  Largest Win:      ${result.largest_win:,.2f}")
    print(f"  Largest Loss:     ${result.largest_loss:,.2f}")

    print("\nVerdict:")
    if result.total_trades == 0:
        print("  [!] Zero trades — strategy filters too strict or data range too short")
        return

    if result.sharpe_ratio > 2:
        print("  [+] Excellent Sharpe ratio (>2)")
    elif result.sharpe_ratio > 1:
        print("  [+] Good Sharpe ratio (1-2)")
    elif result.sharpe_ratio > 0:
        print("  [~] Low Sharpe ratio (<1)")
    else:
        print("  [-] Negative Sharpe (losing strategy)")

    if result.win_rate > 50:
        print(f"  [+] Win rate {result.win_rate:.1f}% > 50%")
    else:
        print(f"  [~] Win rate {result.win_rate:.1f}% < 50%")

    if result.profit_factor > 1.5:
        print(f"  [+] Good profit factor ({result.profit_factor:.2f})")
    elif result.profit_factor > 1:
        print(f"  [~] Marginal profit factor ({result.profit_factor:.2f})")
    else:
        print(f"  [-] Poor profit factor ({result.profit_factor:.2f})")

    if abs(result.max_drawdown_pct) < 10:
        print(f"  [+] Low drawdown ({abs(result.max_drawdown_pct):.1f}%)")
    elif abs(result.max_drawdown_pct) < 20:
        print(f"  [~] Moderate drawdown ({abs(result.max_drawdown_pct):.1f}%)")
    else:
        print(f"  [-] High drawdown ({abs(result.max_drawdown_pct):.1f}%)")


def save_results(result, output_prefix: str, strategy_name: str):
    output_path = Path(output_prefix)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stem = f"{output_path.stem}_{strategy_name}"

    equity_file = output_path.with_name(f"{stem}.csv")
    result.equity_curve.to_csv(equity_file)

    trades_file = output_path.with_name(f"{stem}_trades.csv")
    trades_df = pd.DataFrame(result.trades)
    trades_df.to_csv(trades_file, index=False)

    summary_file = output_path.with_name(f"{stem}_summary.txt")
    with open(summary_file, 'w') as f:
        f.write(f"BACKTEST RESULTS — {strategy_name.upper()}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total Return: ${result.total_return:,.2f} ({result.total_return_pct:+.2f}%)\n")
        f.write(f"Sharpe Ratio: {result.sharpe_ratio:.2f}\n")
        f.write(f"Max Drawdown: {result.max_drawdown_pct:.2f}%\n")
        f.write(f"Win Rate: {result.win_rate:.1f}%\n")
        f.write(f"Total Trades: {result.total_trades}\n")
        f.write(f"Profit Factor: {result.profit_factor:.2f}\n")
    print(f"  Results saved to: data/backtests/{stem}*")


def run_single(strategy_name: str, symbol: Symbol, bars: pd.DataFrame, config: dict,
               initial_capital: Decimal, args) -> object:
    print(f"\n>>> Running {strategy_name} ...")
    strategy = create_strategy(strategy_name, symbol, config)

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=initial_capital,
        risk_config=config,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
        bypass_risk_limits=not args.enforce_risk
    )

    result = engine.run(
        bars=bars,
        start_date=args.start,
        end_date=args.end
    )

    print_results(result, strategy_name)
    save_results(result, args.output, strategy_name)
    return result


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument('--strategy', default='all', choices=STRATEGY_CHOICES,
                        help='Strategy to test (default: all)')
    parser.add_argument('--symbol', default='XAUUSD', help='Symbol to test (default: XAUUSD)')
    parser.add_argument('--timeframe', default='5m', help='Timeframe (default: 5m)')
    parser.add_argument('--config', default='config/config_live_50000.yaml',
                        help='Config file (default: config/config_live_50000.yaml)')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=None,
                        help='Initial capital (default: from config)')
    parser.add_argument('--commission', type=float, default=0, help='Commission per trade')
    parser.add_argument('--slippage', default='realistic',
                        choices=['fixed', 'realistic', 'aggressive'], help='Slippage model')
    parser.add_argument('--output', default='data/backtests/backtest_result',
                        help='Output file prefix')
    parser.add_argument('--enforce-risk', action='store_true', default=False,
                        help='Enforce kill-switch/circuit-breaker during backtest (default: bypassed)')

    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {args.config}")
        sys.exit(1)

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Initial capital: CLI arg > config > fallback
    if args.capital is not None:
        initial_capital = Decimal(str(args.capital))
    else:
        initial_capital = Decimal(str(config.get('account', {}).get('initial_balance', 10000)))

    print("=" * 60)
    print("BACKTEST CONFIGURATION")
    print("=" * 60)
    print(f"  Config:           {args.config}")
    print(f"  Strategy:         {args.strategy}")
    print(f"  Symbol:           {args.symbol}")
    print(f"  Timeframe:        {args.timeframe}")
    print(f"  Initial Capital:  ${initial_capital:,.2f}")
    print(f"  Commission:       ${args.commission:.2f}")
    print(f"  Slippage Model:   {args.slippage}")
    if args.start:
        print(f"  Start Date:       {args.start}")
    if args.end:
        print(f"  End Date:         {args.end}")
    print("=" * 60)

    symbol = create_symbol(args.symbol, config)

    print("\nLoading historical data...")
    bars = load_historical_data(args.symbol, args.timeframe)
    print(f"  Loaded {len(bars)} bars")
    print(f"  Date range: {bars.index.min()} to {bars.index.max()}")

    strategies_to_run = (
        ['breakout', 'momentum', 'kalman_regime', 'vwap', 'mini_medallion', 'sbr']
        if args.strategy == 'all'
        else [args.strategy]
    )

    results = {}
    for strat in strategies_to_run:
        try:
            results[strat] = run_single(strat, symbol, bars, config, initial_capital, args)
        except Exception as e:
            print(f"  [ERROR] {strat} failed: {e}")
            import traceback
            traceback.print_exc()

    # Summary table when running all
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("SUMMARY TABLE")
        print("=" * 60)
        print(f"{'Strategy':<20} {'Trades':>7} {'WinRate':>8} {'PF':>6} {'Return':>10} {'Sharpe':>7} {'MaxDD':>8}")
        print("-" * 60)
        for name, r in results.items():
            print(
                f"{name:<20} {r.total_trades:>7} {r.win_rate:>7.1f}%"
                f" {r.profit_factor:>6.2f} {r.total_return_pct:>+9.2f}%"
                f" {r.sharpe_ratio:>7.2f} {r.max_drawdown_pct:>7.2f}%"
            )
        print("=" * 60)

    print("\nBacktest complete!")


if __name__ == "__main__":
    main()
