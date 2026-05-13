"""
Combo backtest — drives EnsembleBacktestEngine with ConfluenceGate active and
fixed-lot 0.05 sizing.

Forces ``risk.position_sizing.method = 'fixed_lot'`` and ``fixed_lot = 0.05``
on top of the active config so every order opens with 0.05 lots regardless of
risk %. ConfluenceGate is enabled (default in the YAMLs) and exercises the
COMBO A / B / C policy from combine_startegy.md (2026-05-14).

Usage:
    python3 scripts/backtest_combo.py
    python3 scripts/backtest_combo.py --start 2025-10-27 --end 2026-04-27
    python3 scripts/backtest_combo.py --gate-off       # baseline: gate disabled
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


def _force_fixed_lot(cfg: dict, lot: Decimal) -> dict:
    """Mutate the config so RiskEngine returns ``lot`` verbatim per trade."""
    cfg = deepcopy(cfg)
    cfg.setdefault("risk", {}).setdefault("position_sizing", {})
    cfg["risk"]["position_sizing"]["method"] = "fixed_lot"
    cfg["risk"]["position_sizing"]["fixed_lot"] = str(lot)
    # Widen the per-trade dollar caps so the fixed lot isn't clamped.
    cfg["risk"]["risk_per_trade_pct"] = 0.10  # 10% — effectively uncapped for 0.05 lot
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
    ap = argparse.ArgumentParser(description="Combo backtest (ConfluenceGate + fixed 0.05 lot)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", default="2025-10-27")
    ap.add_argument("--end", default="2026-04-27")
    ap.add_argument("--lot", type=str, default="0.05")
    ap.add_argument("--gate-off", action="store_true",
                    help="Disable ConfluenceGate (baseline run). Kill-list still applies.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg, cfg_path = _load_active_config()
    print(f"[combo-backtest] active config = {cfg_path.relative_to(ROOT)}")

    cfg = _force_fixed_lot(cfg, Decimal(args.lot))
    if args.gate_off:
        cfg.setdefault("strategies", {}).setdefault("confluence_gate", {})["enabled"] = False
        print("[combo-backtest] ConfluenceGate DISABLED (baseline run — kill-list still drops)")
    else:
        gate_cfg = (cfg.get("strategies") or {}).get("confluence_gate", {}) or {}
        print(
            f"[combo-backtest] ConfluenceGate ENABLED "
            f"(window={gate_cfg.get('window_minutes', 25)}min, "
            f"sniper={gate_cfg.get('sniper_lot_multiplier', 1.5)}×)"
        )

    symbol = _build_symbol(cfg, args.symbol)
    bars = _load_bars(args.symbol, args.start, args.end)
    if bars.empty:
        print("ERROR: no bars in requested range")
        sys.exit(2)
    print(f"[combo-backtest] {args.symbol} 5m bars: {len(bars)}  "
          f"({bars.index.min()} → {bars.index.max()})")
    print(f"[combo-backtest] fixed lot = {args.lot}  initial capital = "
          f"{cfg.get('account', {}).get('initial_balance', 10000)}")

    initial_capital = Decimal(str(cfg.get("account", {}).get("initial_balance", 10000)))
    engine = EnsembleBacktestEngine(
        symbol=symbol,
        full_config=cfg,
        initial_capital=initial_capital,
        commission_per_trade=Decimal("0"),
        slippage_model="strict",
        bypass_risk_limits=True,
    )
    # Pin the user's fixed lot at the symbol boundary so RiskEngine's
    # "user-fixed lot" branch fires (min_lot == max_lot).
    engine.symbol = Symbol(
        ticker=symbol.ticker,
        pip_value=symbol.pip_value,
        min_lot=Decimal(args.lot),
        max_lot=Decimal(args.lot),
        lot_step=symbol.lot_step,
        value_per_lot=symbol.value_per_lot,
    )

    result = engine.run(bars=bars)
    print_ensemble_report(result)

    # Per-combo attribution — the gate stamps signal.metadata['combo']
    # before execution, so trade records carry it through.
    print()
    print("=" * 60)
    print("PER-STRATEGY ATTRIBUTION (post-ConfluenceGate)")
    print("=" * 60)
    for name, attrib in sorted(result.per_strategy.items(),
                                key=lambda kv: kv[1].net_pnl,
                                reverse=True):
        if attrib.trades == 0:
            continue
        print(f"  {name:22s}  trades={attrib.trades:4d}  "
              f"wins={attrib.wins:4d} ({attrib.win_rate*100:5.1f}%)  "
              f"net_pnl={float(attrib.net_pnl):+9.2f}")


if __name__ == "__main__":
    main()
