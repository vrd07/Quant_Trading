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
from typing import Dict, Tuple
import pandas as pd

# Suppress verbose per-bar strategy logging during backtest runs
# (strategies log INFO for every "no signal" reason change, which floods output)
logging.disable(logging.INFO)

# Add project root to path for proper imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.backtest.grid_loader import load_grid_for
from src.backtest.tiered_retune import TieredRetune, Gates
from src.backtest.news_replay import NewsBlackoutReplay
from src.backtest.walk_forward_driver import WalkForwardDriver
from src.backtest.ensemble_engine import EnsembleBacktestEngine, print_ensemble_report
from src.backtest import report as bt_report
from src.strategies.breakout_strategy import BreakoutStrategy
from src.strategies.mean_reversion_strategy import MeanReversionStrategy
from src.strategies.momentum_strategy import MomentumStrategy
from src.strategies.vwap_strategy import VWAPStrategy
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
from src.strategies.mini_medallion_strategy import MiniMedallionStrategy
from src.strategies.structure_break_retest import StructureBreakRetestStrategy
from src.strategies.supply_demand_strategy import SupplyDemandStrategy
from src.strategies.asia_range_fade_strategy import AsiaRangeFadeStrategy
from src.strategies.descending_channel_breakout_strategy import DescendingChannelBreakoutStrategy
from src.strategies.smc_ob_strategy import SMCOrderBlockStrategy
from src.strategies.fibonacci_retracement_strategy import FibonacciRetracementStrategy
from src.strategies.continuation_breakout_strategy import ContinuationBreakoutStrategy
from src.core.types import Symbol
import yaml

STRATEGY_CHOICES = ['breakout', 'mean_reversion', 'momentum', 'vwap', 'kalman_regime', 'mini_medallion', 'sbr', 'supply_demand', 'asia_range_fade', 'descending_channel_breakout', 'smc_ob', 'fibonacci_retracement', 'continuation_breakout', 'all']

STRATEGY_CLASS_MAP = {
    'breakout': BreakoutStrategy,
    'mean_reversion': MeanReversionStrategy,
    'momentum': MomentumStrategy,
    'vwap': VWAPStrategy,
    'kalman_regime': KalmanRegimeStrategy,
    'mini_medallion': MiniMedallionStrategy,
    'sbr': StructureBreakRetestStrategy,
    'supply_demand': SupplyDemandStrategy,
    'asia_range_fade': AsiaRangeFadeStrategy,
    'descending_channel_breakout': DescendingChannelBreakoutStrategy,
    'smc_ob': SMCOrderBlockStrategy,
    'fibonacci_retracement': FibonacciRetracementStrategy,
    'continuation_breakout': ContinuationBreakoutStrategy,
}


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
        cfg = dict(strats.get('vwap', {})); cfg['enabled'] = True
        return VWAPStrategy(symbol, cfg)
    elif strategy_name == 'kalman_regime':
        return KalmanRegimeStrategy(symbol, strats.get('kalman_regime', {}))
    elif strategy_name == 'mini_medallion':
        return MiniMedallionStrategy(symbol, strats.get('mini_medallion', {}))
    elif strategy_name == 'sbr':
        return StructureBreakRetestStrategy(symbol, strats.get('sbr', {}))
    elif strategy_name == 'supply_demand':
        cfg = dict(strats.get('supply_demand', {}))
        cfg['enabled'] = True  # Force-enable for backtest
        return SupplyDemandStrategy(symbol, cfg)
    elif strategy_name == 'asia_range_fade':
        cfg = dict(strats.get('asia_range_fade', {}))
        cfg['enabled'] = True
        return AsiaRangeFadeStrategy(symbol, cfg)
    elif strategy_name == 'descending_channel_breakout':
        cfg = dict(strats.get('descending_channel_breakout', {}))
        cfg['enabled'] = True
        return DescendingChannelBreakoutStrategy(symbol, cfg)
    elif strategy_name == 'smc_ob':
        cfg = dict(strats.get('smc_ob', {}))
        cfg['enabled'] = True  # Force-enable for backtest
        return SMCOrderBlockStrategy(symbol, cfg)
    elif strategy_name == 'fibonacci_retracement':
        cfg = dict(strats.get('fibonacci_retracement', {}))
        cfg['enabled'] = True  # Force-enable for backtest
        return FibonacciRetracementStrategy(symbol, cfg)
    elif strategy_name == 'continuation_breakout':
        cfg = dict(strats.get('continuation_breakout', {}))
        cfg['enabled'] = True  # Force-enable for backtest
        return ContinuationBreakoutStrategy(symbol, cfg)
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


def _build_news_replay(args) -> 'Optional[NewsBlackoutReplay]':
    """Build a NewsBlackoutReplay from --news-blackout if provided."""
    paths = getattr(args, 'news_blackout', None)
    if not paths:
        return None
    csv_paths = [p.strip() for p in paths.split(',') if p.strip()]
    replay = NewsBlackoutReplay.from_csv(csv_paths)
    print(f"  News blackout: loaded {len(replay)} high-impact events from {len(csv_paths)} CSV(s)")
    return replay


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
        bypass_risk_limits=not args.enforce_risk,
        news_replay=_build_news_replay(args),
        disable_sl_exits=getattr(args, 'disable_sl', False),
    )

    result = engine.run(
        bars=bars,
        start_date=args.start,
        end_date=args.end
    )

    print_results(result, strategy_name)
    save_results(result, args.output, strategy_name)
    return result


def run_grid_search(strategy_name: str, symbol: Symbol, bars: pd.DataFrame,
                    config: dict, initial_capital: Decimal, args) -> None:
    """Run backtest.md §7 tiered auto-retune for one strategy on one symbol."""
    if strategy_name not in STRATEGY_CLASS_MAP:
        print(f"[ERROR] Grid search not supported for strategy '{strategy_name}'")
        sys.exit(1)
    strategy_class = STRATEGY_CLASS_MAP[strategy_name]

    grids_dir = Path(args.grids_dir) if args.grids_dir else Path("config/backtest_grids")
    grid_path = grids_dir / f"{strategy_name}.yaml"
    if not grid_path.exists():
        print(f"[ERROR] No grid file at {grid_path}")
        sys.exit(1)
    grid = load_grid_for(strategy_name, grids_dir=grids_dir)

    if args.smoke:
        grid.max_combos['tier1'] = 3
        grid.max_combos['tier2'] = 3
        grid.max_combos['tier3'] = 3
        print("  [SMOKE] Caps overridden: tier1=3, tier2=3, tier3=3")

    idx_tz = bars.index.tz
    def _ts(s: str) -> pd.Timestamp:
        t = pd.Timestamp(s)
        if idx_tz is not None and t.tz is None:
            t = t.tz_localize(idx_tz)
        return t
    if args.start:
        bars = bars[bars.index >= _ts(args.start)]
    if args.end:
        bars = bars[bars.index <= _ts(args.end)]
    if len(bars) < 1000:
        print(f"[ERROR] Only {len(bars)} bars after filtering — need >=1000 for IS/OOS split")
        sys.exit(1)

    split_idx = int(len(bars) * (1 - args.oos_ratio))
    is_bars = bars.iloc[:split_idx].copy()
    oos_bars = bars.iloc[split_idx:].copy()

    print("\n" + "=" * 70)
    print(f"GRID SEARCH — {strategy_name.upper()} × {symbol.ticker}")
    print("=" * 70)
    print(f"  Grid file:        {grid_path}")
    print(f"  IS bars:          {len(is_bars):>6}  ({is_bars.index.min().date()} -> {is_bars.index.max().date()})")
    print(f"  OOS bars:         {len(oos_bars):>6}  ({oos_bars.index.min().date()} -> {oos_bars.index.max().date()})")
    print(f"  Tier1 combo cap:  {grid.max_combos.get('tier1', 200)}")
    print(f"  Tier1 grid keys:  {list(grid.tier1_entry.keys())}")
    print("=" * 70)

    retune = TieredRetune(
        strategy_class=strategy_class,
        symbol=symbol,
        is_bars=is_bars,
        oos_bars=oos_bars,
        grid=grid,
        full_config=config,
        initial_capital=initial_capital,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
    )
    result = retune.run()

    print("\n" + "=" * 70)
    print("RETUNE RESULT")
    print("=" * 70)
    print(f"  {result.summary}")
    if result.gate_status:
        print(f"\n  Gate status (G1..G6):")
        for gate, ok in result.gate_status.items():
            mark = "PASS" if ok else "FAIL"
            print(f"    [{mark}] {gate}")
    if result.passed:
        print(f"\n  Winning params (anchor + combo, tier {result.tier}):")
        for k, v in sorted(result.winning_params.items()):
            print(f"    {k}: {v}")
    else:
        print(f"\n  Best-effort params (all tiers failed):")
        for k, v in sorted(result.winning_params.items()):
            print(f"    {k}: {v}")
        print(f"\n  Reason: {result.reason}")
    print("=" * 70)


def run_walk_forward(strategy_name: str, symbol: Symbol, bars: pd.DataFrame,
                     config: dict, initial_capital: Decimal, args) -> None:
    """Backtest.md §5.1 walk-forward driver: rolling 70/30 monthly windows with
    TieredRetune per window. The OOS union is graded against G1..G7."""
    if strategy_name not in STRATEGY_CLASS_MAP:
        print(f"[ERROR] Walk-forward not supported for strategy '{strategy_name}'")
        sys.exit(1)
    strategy_class = STRATEGY_CLASS_MAP[strategy_name]

    grids_dir = Path(args.grids_dir) if args.grids_dir else Path("config/backtest_grids")
    grid_path = grids_dir / f"{strategy_name}.yaml"
    if not grid_path.exists():
        print(f"[ERROR] No grid file at {grid_path}")
        sys.exit(1)
    grid = load_grid_for(strategy_name, grids_dir=grids_dir)

    if args.smoke:
        grid.max_combos['tier1'] = 3
        grid.max_combos['tier2'] = 3
        grid.max_combos['tier3'] = 3
        print("  [SMOKE] grid caps overridden: tier1/2/3 = 3 combos each")

    # Date filter mirrors run_grid_search so --start/--end act consistently.
    idx_tz = bars.index.tz
    def _ts(s: str) -> pd.Timestamp:
        t = pd.Timestamp(s)
        if idx_tz is not None and t.tz is None:
            t = t.tz_localize(idx_tz)
        return t
    if args.start:
        bars = bars[bars.index >= _ts(args.start)]
    if args.end:
        bars = bars[bars.index <= _ts(args.end)]

    print("\n" + "=" * 72)
    print(f"WALK-FORWARD — {strategy_name.upper()} × {symbol.ticker}")
    print("=" * 72)
    print(f"  Grid:        {grid_path}")
    print(f"  Span:        {bars.index.min()} → {bars.index.max()}  ({len(bars):,} bars)")
    print(f"  Window:      IS={args.wf_is_months}mo  OOS={args.wf_oos_months}mo  "
          f"roll={args.wf_roll_months}mo")
    print(f"  Slippage:    {args.slippage}")
    print("=" * 72)

    driver = WalkForwardDriver(
        strategy_class=strategy_class,
        symbol=symbol,
        bars=bars,
        grid=grid,
        full_config=config,
        initial_capital=initial_capital,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
        news_replay=_build_news_replay(args),
        is_months=args.wf_is_months,
        oos_months=args.wf_oos_months,
        roll_months=args.wf_roll_months,
    )
    result = driver.run(max_windows=args.wf_max_windows)
    WalkForwardDriver.print_report(result)


def run_ensemble(symbol: Symbol, bars: pd.DataFrame, config: dict,
                 initial_capital: Decimal, args) -> None:
    """Phase 2 ensemble: full pipeline, all enabled strategies, one symbol."""
    engine = EnsembleBacktestEngine(
        symbol=symbol,
        full_config=config,
        initial_capital=initial_capital,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
        news_replay=_build_news_replay(args),
        bypass_risk_limits=not args.enforce_risk,
    )
    result = engine.run(
        bars=bars,
        start_date=args.start,
        end_date=args.end,
    )
    print_ensemble_report(result)


def main():
    parser = argparse.ArgumentParser(description="Run strategy backtest")
    parser.add_argument('--strategy', default='all', choices=STRATEGY_CHOICES,
                        help='Strategy to test (default: all)')
    parser.add_argument('--symbol', default='XAUUSD', help='Symbol to test (default: XAUUSD)')
    parser.add_argument('--symbols', default=None,
                        help='Comma-separated list of symbols, e.g. "XAUUSD,BTCUSD,EURUSD". '
                             'Use "all" to mean the three spec-required symbols (XAU+BTC+EUR). '
                             'Overrides --symbol when set.')
    parser.add_argument('--timeframe', default='5m', help='Timeframe (default: 5m)')
    parser.add_argument('--config', default='config/config_live_50000.yaml',
                        help='Config file (default: config/config_live_50000.yaml)')
    parser.add_argument('--start', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', help='End date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=None,
                        help='Initial capital (default: from config)')
    parser.add_argument('--commission', type=float, default=0, help='Commission per trade')
    parser.add_argument('--slippage', default='realistic',
                        choices=['fixed', 'realistic', 'aggressive', 'strict'],
                        help="Slippage model. 'strict' = backtest.md §3 (spread + "
                             "1.5x slippage + queue penalty for stops). The production gate.")
    parser.add_argument('--output', default='data/backtests/backtest_result',
                        help='Output file prefix')
    parser.add_argument('--enforce-risk', action='store_true', default=False,
                        help='Enforce kill-switch/circuit-breaker during backtest (default: bypassed)')
    parser.add_argument('--disable-sl', action='store_true', default=False,
                        help='Research: position sizing still uses SL distance, '
                             'but the simulation never closes a trade on an SL touch. '
                             'Only TP / time-stop / trailing-stop can exit. Use to '
                             'measure "what if winners always ran" upside.')
    parser.add_argument('--grid-search', action='store_true', default=False,
                        help='Run backtest.md §7 tiered auto-retune instead of single backtest')
    parser.add_argument('--grids-dir', default=None,
                        help='Directory containing per-strategy grid YAMLs (default: config/backtest_grids)')
    parser.add_argument('--oos-ratio', type=float, default=0.30,
                        help='Out-of-sample fraction for grid-search IS/OOS split (default: 0.30)')
    parser.add_argument('--smoke', action='store_true', default=False,
                        help='Smoke-test mode: cap tier1/tier2/tier3 grid sizes to 3 combos')
    parser.add_argument('--news-blackout', default=None,
                        help='Comma-separated path(s) to ForexFactory CSV(s) for news '
                             'blackout replay (backtest.md §3.4). Signals during high-impact '
                             'windows are dropped; open positions stay open; spread widens 3×.')
    parser.add_argument('--walk-forward', action='store_true', default=False,
                        help='Run the §5.1 walk-forward driver (rolling 70/30, monthly roll, '
                             'TieredRetune per window). Requires --strategy and --symbols.')
    parser.add_argument('--ensemble', action='store_true', default=False,
                        help='Phase 2 ensemble: drive full StrategyManager + RiskEngine + '
                             'SimulatedBroker pipeline. The production gate (§6 Phase 2). '
                             'Ignores --strategy; runs whatever is enabled in the config.')
    parser.add_argument('--report', action='store_true', default=False,
                        help='Emit the full §9 report tree under reports/backtest_<date>_<sha>/. '
                             'Includes summary.md (the merge gate), per_strategy/*.md, '
                             'ensemble.md when --ensemble used, equity_curves.png, and failures.log.')
    parser.add_argument('--wf-max-windows', type=int, default=None,
                        help='Cap window count for smoke-testing the walk-forward driver.')
    parser.add_argument('--wf-is-months', type=float, default=8.4,
                        help='In-sample window length in months (default: 8.4 = 70%% of 12mo).')
    parser.add_argument('--wf-oos-months', type=float, default=4.1,
                        help='Out-of-sample window length in months (default: 4.1 = 30%%).')
    parser.add_argument('--wf-roll-months', type=float, default=1.0,
                        help='Months to advance between windows (default: 1.0 per spec).')

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

    # Resolve the list of symbols. --symbols overrides --symbol; "all" expands
    # to backtest.md §2's three required symbols (XAU + BTC + EUR).
    if args.symbols:
        if args.symbols.strip().lower() == 'all':
            symbol_names = ['XAUUSD', 'BTCUSD', 'EURUSD']
        else:
            symbol_names = [s.strip() for s in args.symbols.split(',') if s.strip()]
    else:
        symbol_names = [args.symbol]

    print("=" * 60)
    print("BACKTEST CONFIGURATION")
    print("=" * 60)
    print(f"  Config:           {args.config}")
    print(f"  Strategy:         {args.strategy}")
    print(f"  Symbols:          {', '.join(symbol_names)}")
    print(f"  Timeframe:        {args.timeframe}")
    print(f"  Initial Capital:  ${initial_capital:,.2f}")
    print(f"  Commission:       ${args.commission:.2f}")
    print(f"  Slippage Model:   {args.slippage}")
    if args.start:
        print(f"  Start Date:       {args.start}")
    if args.end:
        print(f"  End Date:         {args.end}")
    print("=" * 60)

    if args.ensemble:
        for sym_name in symbol_names:
            symbol = create_symbol(sym_name, config)
            print(f"\nLoading {sym_name} {args.timeframe} bars...")
            bars = load_historical_data(sym_name, args.timeframe)
            print(f"  Loaded {len(bars)} bars  ({bars.index.min()} → {bars.index.max()})")
            run_ensemble(symbol, bars, config, initial_capital, args)
        print("\nEnsemble backtest complete!")
        return

    if args.walk_forward:
        if args.strategy == 'all':
            print("[ERROR] --walk-forward requires a specific --strategy (not 'all')")
            sys.exit(1)
        for sym_name in symbol_names:
            symbol = create_symbol(sym_name, config)
            print(f"\nLoading {sym_name} {args.timeframe} bars...")
            bars = load_historical_data(sym_name, args.timeframe)
            print(f"  Loaded {len(bars)} bars  ({bars.index.min()} → {bars.index.max()})")
            run_walk_forward(args.strategy, symbol, bars, config, initial_capital, args)
        print("\nWalk-forward complete!")
        return

    if args.grid_search:
        if args.strategy == 'all':
            print("[ERROR] --grid-search requires a specific --strategy (not 'all')")
            sys.exit(1)
        if len(symbol_names) > 1:
            print("[ERROR] --grid-search runs one symbol at a time. Use --walk-forward "
                  "for multi-symbol rolling validation.")
            sys.exit(1)
        symbol = create_symbol(symbol_names[0], config)
        print("\nLoading historical data...")
        bars = load_historical_data(symbol_names[0], args.timeframe)
        print(f"  Loaded {len(bars)} bars  ({bars.index.min()} → {bars.index.max()})")
        run_grid_search(args.strategy, symbol, bars, config, initial_capital, args)
        print("\nGrid search complete!")
        return

    strategies_to_run = (
        ['breakout', 'momentum', 'kalman_regime', 'vwap', 'mini_medallion', 'sbr']
        if args.strategy == 'all'
        else [args.strategy]
    )

    # Per-symbol per-strategy result table. The keys are kept tuple-shaped so
    # downstream code (#4 walk-forward, #6 report generator) can pivot freely.
    results: Dict[Tuple[str, str], object] = {}
    for sym_name in symbol_names:
        symbol = create_symbol(sym_name, config)
        print(f"\nLoading {sym_name} {args.timeframe} bars...")
        try:
            bars = load_historical_data(sym_name, args.timeframe)
        except SystemExit:
            print(f"  [SKIP] No data for {sym_name}")
            continue
        print(f"  Loaded {len(bars)} bars  ({bars.index.min()} → {bars.index.max()})")
        for strat in strategies_to_run:
            try:
                results[(sym_name, strat)] = run_single(
                    strat, symbol, bars, config, initial_capital, args,
                )
            except Exception as e:
                print(f"  [ERROR] {strat} on {sym_name} failed: {e}")
                import traceback
                traceback.print_exc()

    # Per-symbol summary + cross-symbol grade per strategy. The cross-symbol
    # block implements backtest.md §2's "weakest-symbol" grading.
    if len(results) > 1:
        print("\n" + "=" * 78)
        print("SUMMARY TABLE — per (symbol, strategy)")
        print("=" * 78)
        print(f"{'Symbol':<8} {'Strategy':<22} {'Trades':>7} {'WinRate':>8} {'PF':>6} "
              f"{'Return':>10} {'Sharpe':>7} {'MaxDD':>8}")
        print("-" * 78)
        for (sym, strat), r in results.items():
            print(
                f"{sym:<8} {strat:<22} {r.total_trades:>7} {r.win_rate:>7.1f}%"
                f" {r.profit_factor:>6.2f} {r.total_return_pct:>+9.2f}%"
                f" {r.sharpe_ratio:>7.2f} {r.max_drawdown_pct:>7.2f}%"
            )
        print("=" * 78)

        # Weakest-symbol grade per strategy (§2): a strategy is only as good
        # as its worst symbol — that's the column the live config gates on.
        if len(symbol_names) > 1 and len(strategies_to_run) >= 1:
            print("\nWEAKEST-SYMBOL GRADE (backtest.md §2)")
            print("-" * 78)
            print(f"{'Strategy':<22} {'Worst Symbol':<14} {'PF':>6} {'Sharpe':>7} {'WinRate':>8}")
            print("-" * 78)
            for strat in strategies_to_run:
                rows = [(sym, results[(sym, strat)]) for sym in symbol_names
                        if (sym, strat) in results]
                if not rows:
                    continue
                # Weakest = lowest profit factor (matches G3).
                worst_sym, worst_r = min(rows, key=lambda kv: kv[1].profit_factor)
                print(f"{strat:<22} {worst_sym:<14} {worst_r.profit_factor:>6.2f} "
                      f"{worst_r.sharpe_ratio:>7.2f} {worst_r.win_rate:>7.1f}%")
            print("=" * 78)

    # §9 report emission. Aggregates results across (symbol, strategy) tuples
    # into one summary.md per strategy — when multiple symbols ran the
    # weakest-symbol result is the one that gates §2 grading.
    if args.report and results:
        ctx = bt_report.ReportContext.create()
        # Pick the worst-PF result per strategy (matches §2 weakest-symbol grading).
        per_strategy: Dict[str, object] = {}
        for strat in strategies_to_run:
            rows = [results[(sym, strat)] for sym in symbol_names if (sym, strat) in results]
            if rows:
                per_strategy[strat] = min(rows, key=lambda r: r.profit_factor)
        bt_report.write_summary_md(ctx, per_strategy, config_path=args.config)
        for name, r in per_strategy.items():
            bt_report.write_per_strategy_md(ctx, name, r)
        bt_report.write_failures_log(ctx, per_strategy)
        bt_report.write_equity_curves_png(ctx, per_strategy)
        # Trade log spans every (symbol, strategy) combo.
        all_trades = []
        for (sym, strat), r in results.items():
            for t in (r.trades or []):
                row = dict(t)
                row.setdefault('symbol', sym)
                row.setdefault('strategy', strat)
                all_trades.append(row)
        bt_report.write_trade_log(ctx, all_trades)
        print(f"\nReport written to: {ctx.out_dir}")

    print("\nBacktest complete!")


if __name__ == "__main__":
    main()
