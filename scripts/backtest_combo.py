"""
Combo backtest — drives EnsembleBacktestEngine with StrategyAllowlist and
fixed-lot 0.05 sizing.

Forces ``risk.position_sizing.method = 'fixed_lot'`` and ``fixed_lot = 0.05``
on top of the active config so every order opens with 0.05 lots regardless of
risk %.

Historical note: this script originally exercised the old confluence gate's
COMBO A / B / C policy from combine_startegy.md (2026-05-14). That gate
was deleted 2026-08-13 in favour of StrategyAllowlist, a plain default-deny
net with no combo assembly and no config-driven enable toggle — the
``--gate-off`` / ``--relaxed`` flags this script used to expose no longer
have anything to control and were removed.

Usage:
    python3 scripts/backtest_combo.py
    python3 scripts/backtest_combo.py --start 2025-10-27 --end 2026-04-27
"""

from __future__ import annotations

import argparse
import logging
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

# Repo on path so `src.*` imports resolve when run from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backtest.ensemble_engine import EnsembleBacktestEngine, print_ensemble_report  # noqa: E402
from src.core.types import Symbol  # noqa: E402


def _load_active_config() -> tuple[dict, Path]:
    active_marker = ROOT / "config" / "ACTIVE_CONFIG"
    cfg_path = ROOT / active_marker.read_text().strip()
    with open(cfg_path) as fh:
        cfg = yaml.safe_load(fh)
    return cfg, cfg_path


def _force_fixed_lot(cfg: dict, lot: Decimal, symbol: str) -> dict:
    """Mutate the config so RiskEngine returns ``lot`` verbatim per trade.

    RiskEngine.calculate_position_size hits the "user-fixed lot" branch when
    ``symbol.min_lot == symbol.max_lot``. We pin both to ``lot`` so every
    order opens with exactly that size regardless of risk %.
    """
    cfg = deepcopy(cfg)
    cfg.setdefault("risk", {}).setdefault("position_sizing", {})
    cfg["risk"]["position_sizing"]["method"] = "fixed_lot"
    cfg["risk"]["position_sizing"]["fixed_lot"] = str(lot)
    cfg["risk"]["risk_per_trade_pct"] = 0.10  # 10% — uncap

    # Pin XAUUSD symbol block: min_lot == max_lot triggers the authoritative
    # user-fixed-lot path in RiskEngine. Without this, the live config's
    # operator-set lot wins.
    cfg.setdefault("symbols", {}).setdefault(symbol, {})
    cfg["symbols"][symbol]["min_lot"] = float(lot)
    cfg["symbols"][symbol]["max_lot"] = float(lot)
    cfg["symbols"][symbol]["lot_step"] = float(lot)

    # Manual-guard halving would silently cut 0.05 in half — disable for the
    # duration of the backtest.
    cfg.setdefault("risk", {}).setdefault("manual_guard", {})["enabled"] = False
    return cfg


def _build_symbol(cfg: dict, ticker: str) -> Symbol:
    sym_cfg = (cfg.get("symbols") or {}).get(ticker, {})
    return Symbol(
        ticker=ticker,
        pip_value=Decimal(str(sym_cfg.get("pip_value", "0.01"))),
        min_lot=Decimal(str(sym_cfg.get("min_lot", "0.01"))),
        max_lot=Decimal(str(sym_cfg.get("max_lot", "100"))),
        lot_step=Decimal(str(sym_cfg.get("lot_step", "0.01"))),
        value_per_lot=Decimal(str(sym_cfg.get("value_per_lot", "100"))),
        # SimulatedBroker margin check: required = price*qty*vpl / leverage.
        # Without this, 0.05-lot XAUUSD at $4700 needs $23.5K margin and
        # every order silently rejects as insufficient capital.
        leverage=Decimal(str(sym_cfg.get("leverage", "30"))),
    )


def _load_bars(symbol: str, start: str | None, end: str | None) -> pd.DataFrame:
    path = ROOT / "data" / "historical" / f"{symbol}_5m_real.csv"
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    return df


def main():
    ap = argparse.ArgumentParser(description="Combo backtest (StrategyAllowlist + fixed 0.05 lot)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", default="2025-10-27")
    ap.add_argument("--end", default="2026-04-27")
    ap.add_argument("--lot", type=str, default="0.05")
    ap.add_argument("--capital", type=str, default=None,
                    help="Override initial_balance (keeps the account solvent so "
                         "combo signals aren't starved by an early blow-up).")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg, cfg_path = _load_active_config()
    print(f"[combo-backtest] active config = {cfg_path.relative_to(ROOT)}")

    cfg = _force_fixed_lot(cfg, Decimal(args.lot), args.symbol)
    print("[combo-backtest] StrategyAllowlist active (default-deny, no config toggle)")

    symbol = _build_symbol(cfg, args.symbol)
    bars = _load_bars(args.symbol, args.start, args.end)
    if bars.empty:
        print("ERROR: no bars in requested range")
        sys.exit(2)
    print(f"[combo-backtest] {args.symbol} 5m bars: {len(bars)}  "
          f"({bars.index.min()} → {bars.index.max()})")
    print(f"[combo-backtest] fixed lot = {args.lot}  initial capital = "
          f"{cfg.get('account', {}).get('initial_balance', 10000)}")

    if args.capital is not None:
        cfg.setdefault("account", {})["initial_balance"] = args.capital
    initial_capital = Decimal(str(cfg.get("account", {}).get("initial_balance", 10000)))
    engine = EnsembleBacktestEngine(
        symbol=symbol,
        full_config=cfg,
        initial_capital=initial_capital,
        commission_per_trade=Decimal("0"),
        slippage_model="strict",
        bypass_risk_limits=True,
    )

    # Instrument StrategyAllowlist with a thin counter wrapper so we can
    # verify it is exercised and report throughput in the summary. No more
    # combo breakdown to report — the allowlist is pass/drop only.
    allowlist = engine.strategy_allowlist
    counters = {"calls": 0, "passed": 0, "suppressed": 0}
    original_filter = allowlist.filter

    def counting_filter(symbol, signals):
        counters["calls"] += 1
        signals = list(signals)
        n_in = len(signals)
        out = original_filter(symbol=symbol, signals=signals)
        counters["passed"] += len(out)
        counters["suppressed"] += max(0, n_in - len(out))
        return out

    allowlist.filter = counting_filter

    result = engine.run(bars=bars)
    print_ensemble_report(result)

    print()
    print("=" * 60)
    print("STRATEGYALLOWLIST THROUGHPUT")
    print("=" * 60)
    print(f"  filter calls           : {counters['calls']}")
    print(f"  signals passed         : {counters['passed']}")
    print(f"  signals suppressed     : {counters['suppressed']}")

    print()
    print("=" * 60)
    print("PER-STRATEGY ATTRIBUTION (executed trades)")
    print("=" * 60)
    for name, attrib in sorted(result.per_strategy.items(),
                                key=lambda kv: kv[1].gross_pnl,
                                reverse=True):
        if attrib.trades == 0:
            continue
        print(f"  {name:22s}  trades={attrib.trades:4d}  "
              f"wins={attrib.wins:4d} ({attrib.win_rate*100:5.1f}%)  "
              f"gross_pnl={float(attrib.gross_pnl):+9.2f}")


if __name__ == "__main__":
    main()
