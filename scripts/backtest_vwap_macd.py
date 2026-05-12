#!/usr/bin/env python3
"""
VWAP + MACD Crossover Strategy — Backtest Runner
=================================================

Runs the full validation pipeline defined in backtest.md for the
vwap_macd_crossover strategy. Supports four modes:

  1. Quick single run (default)
  2. Grid search / tiered auto-retune  (--grid-search)
  3. Walk-forward 70/30 rolling  (--walk-forward)   ← backtest.md §5.1
  4. Full report output  (--report)                 ← backtest.md §9

Usage examples
--------------
# Fast sanity check on XAUUSD 15m data:
python scripts/backtest_vwap_macd.py

# Grid search across tier1/tier2/tier3 params, strict slippage:
python scripts/backtest_vwap_macd.py --grid-search --slippage strict

# Full walk-forward (production gate) on XAUUSD + EURUSD:
python scripts/backtest_vwap_macd.py --walk-forward --symbols XAUUSD,EURUSD \\
    --start 2021-01-01 --end 2025-12-31 --slippage strict --report

# Smoke-test (tiny combo caps, fast feedback):
python scripts/backtest_vwap_macd.py --walk-forward --smoke

backtest.md gate thresholds (G1–G7)
-------------------------------------
G1 Daily win-rate    ≥ 70%
G2 Worst-day floor   ≥ −2R
G3 Profit factor     ≥ 1.4
G4 Sharpe (ann.)     ≥ 1.0
G5 Max drawdown      ≤ 12% of starting equity
G6 Trades / year     ≥ 60
G7 OOS profitable    ≥ 80% of walk-forward windows
"""

import sys
import logging
from pathlib import Path
from decimal import Decimal
from typing import Dict, Optional, Tuple

import pandas as pd
import yaml

# Suppress per-bar INFO noise from strategies
logging.disable(logging.INFO)

# ── path bootstrap ──────────────────────────────────────────────────────────
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine, BacktestResult
from src.backtest.grid_loader import load_grid_for
from src.backtest.tiered_retune import TieredRetune
from src.backtest.news_replay import NewsBlackoutReplay
from src.backtest.walk_forward_driver import WalkForwardDriver
from src.backtest import report as bt_report
from src.strategies.vwap_strategy import VWAPStrategy
from src.core.types import Symbol

# ── constants ────────────────────────────────────────────────────────────────
STRATEGY_NAME  = "vwap"
DEFAULT_CONFIG = "config/config_live_10000.yaml"
GRIDS_DIR      = Path("config/backtest_grids")
DATA_DIR       = Path("data/historical")
OUTPUT_DIR     = Path("data/backtests")

# backtest.md §1 gate thresholds
GATES = {
    "G1_daily_win_rate":   0.70,   # ≥ 70% of trading days finish green
    "G2_worst_day_r":     -2.0,    # no day worse than −2R
    "G3_profit_factor":    1.4,    # gross_win / gross_loss
    "G4_sharpe":           1.0,    # annualised daily Sharpe
    "G5_max_drawdown_pct": 12.0,   # max drawdown ≤ 12% of start equity
    "G6_trades_per_year":  60,     # minimum yearly trade count
}

# backtest.md §5.5 stress days
STRESS_DAYS = [
    "2022-02-24",  # Russia-Ukraine invasion shock
    "2022-03-08",  # Nickel / commodities short squeeze
    "2022-09-26",  # UK gilt blow-up
    "2023-03-10",  # SVB collapse → flight-to-gold
    "2024-08-05",  # Yen carry unwind
    "2024-11-05",  # US election
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def _make_symbol(symbol_name: str, config: dict) -> Symbol:
    sc = config.get("symbols", {}).get(symbol_name, {})
    return Symbol(
        ticker=symbol_name,
        pip_value=Decimal(str(sc.get("pip_value", 0.01))),
        min_lot=Decimal(str(sc.get("min_lot", 0.01))),
        max_lot=Decimal(str(sc.get("max_lot", 100))),
        lot_step=Decimal(str(sc.get("lot_step", 0.01))),
        value_per_lot=Decimal(str(sc.get("value_per_lot", 1))),
        min_stops_distance=Decimal(str(sc.get("min_stops_distance", 0))),
        leverage=Decimal(str(sc.get("leverage", 1))),
    )


def _load_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    Load OHLCV bars from disk.

    Tries real data first, then falls back to sample / generated files.
    Raises SystemExit if no data is found.
    """
    candidates = [
        DATA_DIR / f"{symbol}_{timeframe}_real.csv",
        DATA_DIR / f"{symbol}_{timeframe}.csv",
        DATA_DIR / f"{symbol}_{timeframe}_trending.csv",
        DATA_DIR / f"{symbol}_{timeframe}_ranging.csv",
    ]
    for path in candidates:
        if path.exists():
            print(f"  Loading {path}")
            df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
            return df
    print(f"\n[ERROR] No data found for {symbol} {timeframe}")
    print("Searched:")
    for c in candidates:
        print(f"  {c}")
    print("\nGenerate sample data with:  python scripts/generate_sample_data.py")
    sys.exit(1)


def _build_strategy(symbol: Symbol, config: dict, overrides: Optional[dict] = None) -> VWAPStrategy:
    """Build a VWAPStrategy from the live config, optionally overriding keys."""
    cfg = dict(config.get("strategies", {}).get("vwap", {}))
    cfg["enabled"] = True
    if overrides:
        cfg.update(overrides)
    return VWAPStrategy(symbol, cfg)


def _build_news_replay(csv_paths: Optional[str]) -> Optional[NewsBlackoutReplay]:
    if not csv_paths:
        return None
    paths = [p.strip() for p in csv_paths.split(",") if p.strip()]
    replay = NewsBlackoutReplay.from_csv(paths)
    print(f"  News blackout: {len(replay)} high-impact events from {len(paths)} CSV(s)")
    return replay


def _check_gates(result: BacktestResult, initial_capital: float) -> Dict[str, bool]:
    """
    Evaluate backtest.md §1 gates G1–G6 against a BacktestResult.

    G7 (OOS-profitable window %) is computed by the WalkForwardDriver;
    it is not available from a single-run result.

    Returns a dict gate_name → passed (bool).
    """
    n_years = max(result.trading_days / 252, 0.01)
    trades_per_year = result.total_trades / n_years

    return {
        "G1_daily_win_rate":   result.daily_win_rate >= GATES["G1_daily_win_rate"],
        "G2_worst_day_r":      result.worst_day_r   >= GATES["G2_worst_day_r"],
        "G3_profit_factor":    result.profit_factor >= GATES["G3_profit_factor"],
        "G4_sharpe":           result.sharpe_ratio  >= GATES["G4_sharpe"],
        "G5_max_drawdown_pct": abs(result.max_drawdown_pct) <= GATES["G5_max_drawdown_pct"],
        "G6_trades_per_year":  trades_per_year       >= GATES["G6_trades_per_year"],
    }


def _stress_day_pnl(trades: list) -> Dict[str, float]:
    """
    Compute net P&L on backtest.md §5.5 stress days.

    Returns a dict stress_date → net_pnl (only dates with at least one trade).
    """
    result: Dict[str, float] = {}
    for stress_date in STRESS_DAYS:
        day_trades = [
            t for t in trades
            if isinstance(t.get("timestamp"), str) and t["timestamp"].startswith(stress_date)
        ]
        if day_trades:
            result[stress_date] = sum(t.get("pnl", 0) for t in day_trades)
    return result


# ── printing ─────────────────────────────────────────────────────────────────

def _print_result(result: BacktestResult, initial_capital: float, symbol: str) -> None:
    gates = _check_gates(result, initial_capital)
    all_pass = all(gates.values())
    verdict = "✅ ALL GATES PASS" if all_pass else "❌ GATE FAILURE"

    n_years = max(result.trading_days / 252, 0.01)
    trades_per_year = result.total_trades / n_years

    print("\n" + "=" * 68)
    print(f"  VWAP + MACD CROSSOVER BACKTEST — {symbol}")
    print("=" * 68)
    print(f"\n  {'Capital':20} ${initial_capital:,.2f}")
    print(f"  {'Trades':20} {result.total_trades}  ({trades_per_year:.0f}/year)")
    print(f"  {'Trading days':20} {result.trading_days}")

    print("\n  ── Performance ────────────────────────────────────────")
    print(f"  {'Return':20} ${result.total_return:+,.2f}  ({result.total_return_pct:+.2f}%)")
    print(f"  {'Sharpe':20} {result.sharpe_ratio:.2f}")
    print(f"  {'Sortino':20} {result.sortino_ratio:.2f}")
    print(f"  {'Max drawdown':20} {result.max_drawdown_pct:.2f}%")
    print(f"  {'Win rate (trade)':20} {result.win_rate:.1f}%")
    print(f"  {'Win rate (daily)':20} {result.daily_win_rate * 100:.1f}%")
    print(f"  {'Profit factor':20} {result.profit_factor:.2f}")
    print(f"  {'Expectancy':20} ${result.expectancy:.2f}")
    print(f"  {'Avg win':20} ${result.avg_win:,.2f}")
    print(f"  {'Avg loss':20} ${result.avg_loss:,.2f}")
    print(f"  {'Largest win':20} ${result.largest_win:,.2f}")
    print(f"  {'Largest loss':20} ${result.largest_loss:,.2f}")
    print(f"  {'Worst day (R)':20} {result.worst_day_r:.2f}R")

    print("\n  ── backtest.md §1 Gates ───────────────────────────────")
    thresholds = {
        "G1_daily_win_rate":   f"≥ {GATES['G1_daily_win_rate']*100:.0f}%  got {result.daily_win_rate*100:.1f}%",
        "G2_worst_day_r":      f"≥ {GATES['G2_worst_day_r']:.1f}R  got {result.worst_day_r:.2f}R",
        "G3_profit_factor":    f"≥ {GATES['G3_profit_factor']:.1f}   got {result.profit_factor:.2f}",
        "G4_sharpe":           f"≥ {GATES['G4_sharpe']:.1f}   got {result.sharpe_ratio:.2f}",
        "G5_max_drawdown_pct": f"≤ {GATES['G5_max_drawdown_pct']:.0f}%  got {abs(result.max_drawdown_pct):.1f}%",
        "G6_trades_per_year":  f"≥ {GATES['G6_trades_per_year']}   got {trades_per_year:.0f}/yr",
    }
    for gate, passed in gates.items():
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {gate:26} {thresholds[gate]}")

    # Stress-day P&L table
    stress = _stress_day_pnl(result.trades)
    if stress:
        print("\n  ── §5.5 Stress-Day P&L ────────────────────────────────")
        for date, pnl in sorted(stress.items()):
            mark = "+" if pnl >= 0 else "-"
            print(f"  [{mark}] {date}   ${pnl:+,.2f}")

    print(f"\n  {verdict}")
    print("=" * 68 + "\n")


# ── modes ────────────────────────────────────────────────────────────────────

def run_single(
    symbol: Symbol,
    bars: pd.DataFrame,
    config: dict,
    initial_capital: Decimal,
    args,
) -> BacktestResult:
    """Quick single-pass backtest — no parameter sweep."""
    # Build strategy overrides from CLI flags
    overrides: dict = {}
    if getattr(args, 'all_sessions', False):
        # Disable all session/hour filters so every bar is eligible
        overrides['kill_zones_enabled'] = False
        overrides['allowed_hours']       = None

    strategy = _build_strategy(symbol, config, overrides)
    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=initial_capital,
        risk_config=config,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
        bypass_risk_limits=not args.enforce_risk,
        news_replay=_build_news_replay(getattr(args, "news_blackout", None)),
    )

    # Fixed lot-size override: monkey-patch the risk engine's sizer so every
    # trade uses exactly `fixed_lots` regardless of account balance / ATR.
    fixed_lots = getattr(args, 'fixed_lots', None)
    if fixed_lots is not None:
        _lot = Decimal(str(fixed_lots))
        engine.risk_engine.calculate_position_size = lambda **_kw: _lot

    result = engine.run(bars=bars, start_date=args.start, end_date=args.end)
    _print_result(result, float(initial_capital), symbol.ticker)

    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / f"vwap_macd_{symbol.ticker}"
    result.equity_curve.to_csv(f"{stem}_equity.csv")
    pd.DataFrame(result.trades).to_csv(f"{stem}_trades.csv", index=False)
    print(f"  Saved: {stem}_equity.csv  |  {stem}_trades.csv")
    return result


def run_grid_search(
    symbol: Symbol,
    bars: pd.DataFrame,
    config: dict,
    initial_capital: Decimal,
    args,
) -> None:
    """Tiered auto-retune (backtest.md §7) on a single symbol."""
    grid = load_grid_for(STRATEGY_NAME, grids_dir=GRIDS_DIR)

    if args.smoke:
        grid.max_combos.update({"tier1": 3, "tier2": 3, "tier3": 3})
        print("  [SMOKE] grid caps overridden: 3 combos per tier")

    idx_tz = bars.index.tz
    def _ts(s):
        t = pd.Timestamp(s)
        return t.tz_localize(idx_tz) if (idx_tz and t.tz is None) else t

    if args.start:
        bars = bars[bars.index >= _ts(args.start)]
    if args.end:
        bars = bars[bars.index <= _ts(args.end)]

    split_idx = int(len(bars) * (1 - args.oos_ratio))
    is_bars  = bars.iloc[:split_idx].copy()
    oos_bars = bars.iloc[split_idx:].copy()

    print("\n" + "=" * 70)
    print(f"  GRID SEARCH — vwap_macd_crossover × {symbol.ticker}")
    print("=" * 70)
    print(f"  IS  bars: {len(is_bars):>7}  ({is_bars.index.min().date()} → {is_bars.index.max().date()})")
    print(f"  OOS bars: {len(oos_bars):>7}  ({oos_bars.index.min().date()} → {oos_bars.index.max().date()})")
    print(f"  Tier1 combos cap: {grid.max_combos.get('tier1', 200)}")
    print("=" * 70)

    retune = TieredRetune(
        strategy_class=VWAPStrategy,
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
    print("  RETUNE RESULT")
    print("=" * 70)
    print(f"  {result.summary}")
    if result.gate_status:
        print("\n  Gates (G1..G6):")
        for gate, ok in result.gate_status.items():
            print(f"    [{'PASS' if ok else 'FAIL'}] {gate}")
    label = "Winning" if result.passed else "Best-effort"
    print(f"\n  {label} params (tier {result.tier}):")
    for k, v in sorted(result.winning_params.items()):
        print(f"    {k}: {v}")
    if not result.passed:
        print(f"\n  Reason: {result.reason}")
    print("=" * 70)


def run_walk_forward(
    symbol: Symbol,
    bars: pd.DataFrame,
    config: dict,
    initial_capital: Decimal,
    args,
) -> None:
    """backtest.md §5.1 walk-forward: rolling 70/30, monthly roll, TieredRetune per window."""
    grid = load_grid_for(STRATEGY_NAME, grids_dir=GRIDS_DIR)

    if args.smoke:
        grid.max_combos.update({"tier1": 3, "tier2": 3, "tier3": 3})
        print("  [SMOKE] grid caps overridden: 3 combos per tier")

    idx_tz = bars.index.tz
    def _ts(s):
        t = pd.Timestamp(s)
        return t.tz_localize(idx_tz) if (idx_tz and t.tz is None) else t

    if args.start:
        bars = bars[bars.index >= _ts(args.start)]
    if args.end:
        bars = bars[bars.index <= _ts(args.end)]

    print("\n" + "=" * 72)
    print(f"  WALK-FORWARD — vwap_macd_crossover × {symbol.ticker}")
    print("=" * 72)
    print(f"  Span:    {bars.index.min()} → {bars.index.max()}  ({len(bars):,} bars)")
    print(f"  Window:  IS={args.wf_is_months}mo  OOS={args.wf_oos_months}mo  roll={args.wf_roll_months}mo")
    print(f"  Slippage: {args.slippage}")
    print("=" * 72)

    driver = WalkForwardDriver(
        strategy_class=VWAPStrategy,
        symbol=symbol,
        bars=bars,
        grid=grid,
        full_config=config,
        initial_capital=initial_capital,
        commission_per_trade=Decimal(str(args.commission)),
        slippage_model=args.slippage,
        news_replay=_build_news_replay(getattr(args, "news_blackout", None)),
        is_months=args.wf_is_months,
        oos_months=args.wf_oos_months,
        roll_months=args.wf_roll_months,
    )
    wf_result = driver.run(max_windows=getattr(args, "wf_max_windows", None))
    WalkForwardDriver.print_report(wf_result)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="VWAP + MACD Crossover strategy backtest (backtest.md compliant)"
    )

    # ── Data & config ────────────────────────────────────────────────────────
    parser.add_argument("--config",   default=DEFAULT_CONFIG,
                        help=f"Live config YAML (default: {DEFAULT_CONFIG})")
    parser.add_argument("--symbol",   default="XAUUSD",
                        help="Single symbol (default: XAUUSD)")
    parser.add_argument("--symbols",  default=None,
                        help="Comma-separated symbols, e.g. XAUUSD,EURUSD  (overrides --symbol)")
    parser.add_argument("--timeframe", default="15m",
                        help="Bar timeframe matching historical data filename (default: 15m)")
    parser.add_argument("--start",    default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",      default=None, help="End date YYYY-MM-DD (default: 2025-12-31)")

    # ── Execution realism (backtest.md §3) ───────────────────────────────────
    parser.add_argument("--slippage", default="realistic",
                        choices=["fixed", "realistic", "aggressive", "strict"],
                        help="'strict' = backtest.md §3 production gate (default: realistic)")
    parser.add_argument("--commission", type=float, default=0,
                        help="Commission per trade in account currency (default: 0, "
                             "per-lot commissions applied via symbol spec)")
    parser.add_argument("--news-blackout", default=None,
                        help="Comma-separated ForexFactory CSV path(s) for §3.4 news blackout replay")
    parser.add_argument("--enforce-risk", action="store_true", default=False,
                        help="Enforce kill-switch / circuit-breaker during backtest")

    # ── Mode ────────────────────────────────────────────────────────────────
    parser.add_argument("--grid-search",   action="store_true", default=False,
                        help="Run backtest.md §7 tiered auto-retune on one symbol")
    parser.add_argument("--walk-forward",  action="store_true", default=False,
                        help="Run backtest.md §5.1 walk-forward (production gate)")
    parser.add_argument("--report",        action="store_true", default=False,
                        help="Write §9 report tree under reports/backtest_<date>_<sha>/")
    parser.add_argument("--smoke",         action="store_true", default=False,
                        help="Smoke-test: cap all grid tiers to 3 combos for fast feedback")

    # ── Walk-forward tuning ──────────────────────────────────────────────────
    parser.add_argument("--oos-ratio",      type=float, default=0.30,
                        help="OOS fraction for grid-search simple split (default: 0.30)")
    parser.add_argument("--wf-is-months",   type=float, default=8.4,
                        help="IS window in months (default: 8.4 = 70%% of 12mo)")
    parser.add_argument("--wf-oos-months",  type=float, default=4.1,
                        help="OOS window in months (default: 4.1 = 30%%)")
    parser.add_argument("--wf-roll-months", type=float, default=1.0,
                        help="Roll step in months (default: 1.0 per spec)")
    parser.add_argument("--wf-max-windows", type=int,   default=None,
                        help="Cap number of walk-forward windows (smoke / debug)")

    parser.add_argument("--fixed-lots", type=float, default=None,
                        help="Override risk engine sizing: use this exact lot size for every trade "
                             "(e.g. 0.5). Useful for clean backtest comparison independent of "
                             "account-relative sizing.")
    parser.add_argument("--all-sessions", action="store_true", default=False,
                        help="Disable kill zones (07–10, 12–15 UTC) and allowed_hours filter so "
                             "every bar of the day is eligible for entry. Use for full-data backtest.")

    # ── Capital ─────────────────────────────────────────────────────────────
    parser.add_argument("--capital", type=float, default=None,
                        help="Override initial capital (default: from config account.initial_balance)")

    args = parser.parse_args()

    # ── Load config ──────────────────────────────────────────────────────────
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"[ERROR] Config not found: {args.config}")
        sys.exit(1)
    config = _load_config(args.config)

    # Initial capital: CLI > config > fallback
    if args.capital is not None:
        initial_capital = Decimal(str(args.capital))
    else:
        initial_capital = Decimal(str(
            config.get("account", {}).get("initial_balance", 10_000)
        ))

    # Symbol list
    if args.symbols:
        symbol_names = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbol_names = [args.symbol]

    # ── Header ───────────────────────────────────────────────────────────────
    print("=" * 68)
    print("  VWAP + MACD CROSSOVER — backtest.md compliant runner")
    print("=" * 68)
    print(f"  Config:      {args.config}")
    print(f"  Symbols:     {', '.join(symbol_names)}")
    print(f"  Timeframe:   {args.timeframe}")
    print(f"  Capital:     ${initial_capital:,.2f}")
    print(f"  Slippage:    {args.slippage}")
    mode = "walk-forward" if args.walk_forward else ("grid-search" if args.grid_search else "single")
    print(f"  Mode:        {mode}")
    if args.start:
        print(f"  Start:       {args.start}")
    if args.end:
        print(f"  End:         {args.end}")
    print("=" * 68)

    # ── Per-symbol execution ─────────────────────────────────────────────────
    all_results: Dict[Tuple[str, str], BacktestResult] = {}

    for sym_name in symbol_names:
        symbol = _make_symbol(sym_name, config)
        print(f"\nLoading {sym_name} {args.timeframe} bars ...")
        bars = _load_bars(sym_name, args.timeframe)
        print(f"  {len(bars):,} bars  ({bars.index.min()} → {bars.index.max()})")

        if args.walk_forward:
            run_walk_forward(symbol, bars, config, initial_capital, args)

        elif args.grid_search:
            run_grid_search(symbol, bars, config, initial_capital, args)

        else:
            result = run_single(symbol, bars, config, initial_capital, args)
            all_results[(sym_name, STRATEGY_NAME)] = result

    # ── Optional §9 report ───────────────────────────────────────────────────
    if args.report and all_results:
        ctx = bt_report.ReportContext.create()
        per_strategy: Dict[str, BacktestResult] = {}
        rows = list(all_results.values())
        if rows:
            # Weakest-symbol grading (backtest.md §2)
            per_strategy[STRATEGY_NAME] = min(rows, key=lambda r: r.profit_factor)
        bt_report.write_summary_md(ctx, per_strategy, config_path=args.config)
        for name, r in per_strategy.items():
            bt_report.write_per_strategy_md(ctx, name, r)
        bt_report.write_failures_log(ctx, per_strategy)
        bt_report.write_equity_curves_png(ctx, per_strategy)
        all_trades = []
        for (sym, strat), r in all_results.items():
            for t in (r.trades or []):
                row = dict(t)
                row.setdefault("symbol", sym)
                row.setdefault("strategy", strat)
                all_trades.append(row)
        bt_report.write_trade_log(ctx, all_trades)
        print(f"\n  Report written to: {ctx.out_dir}")

    print("\nDone.")


if __name__ == "__main__":
    main()
