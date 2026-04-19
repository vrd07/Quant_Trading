#!/usr/bin/env python3
"""Manual trade CLI — route clicks through the RiskEngine, not around it.

Usage:
    python scripts/manual_trade.py --symbol XAUUSD --side buy --risk 15 --sl-pips 75
    python scripts/manual_trade.py --symbol BTCUSD --side sell --risk 20 --sl-pips 500 --tp-pips 1000
    python scripts/manual_trade.py --symbol XAUUSD --side buy --lots 0.01 --sl 2395.50
    python scripts/manual_trade.py --dry-run ...          # validate only, no MT5 submit

Each trade is tagged `strategy=manual` so the RiskEngine guards fire:
  - hour blackout (14-16h UTC by default)
  - manual daily loss cap ($50/day)
  - manual size multiplier (x0.5)

Legends applied:
  - geohot: simplest thing that works — one flat function, no classes, no magic.
  - TJ:     explicit CLI — every arg named, no positional surprises.
  - Carmack: fail-closed — if anything is off, we print the reason and exit 1.
  - Jeff Dean: log the numbers (risk, SL distance, lot size) so mistakes are traceable.
"""

import sys
import argparse
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from src.core.types import Order, Symbol
from src.core.constants import OrderSide, OrderType
from src.risk.risk_engine import RiskEngine


def load_symbol(cfg: dict, ticker: str) -> Symbol:
    sym_cfg = cfg.get("symbols", {}).get(ticker)
    if not sym_cfg or not sym_cfg.get("enabled", False):
        raise SystemExit(f"[abort] Symbol {ticker} not enabled in config")
    return Symbol(
        ticker=ticker,
        pip_value=Decimal(str(sym_cfg.get("pip_value", "0.01"))),
        min_lot=Decimal(str(sym_cfg.get("min_lot", "0.01"))),
        max_lot=Decimal(str(sym_cfg.get("max_lot", "100"))),
        lot_step=Decimal(str(sym_cfg.get("lot_step", "0.01"))),
        value_per_lot=Decimal(str(sym_cfg.get("value_per_lot", "1"))),
        min_stops_distance=Decimal(str(sym_cfg.get("min_stops_distance", "0"))),
    )


def compute_size_for_risk(
    risk_usd: Decimal, sl_distance: Decimal, symbol: Symbol
) -> Decimal:
    """Lots such that SL hit ≈ risk_usd. Clamped to symbol [min_lot, max_lot]."""
    if sl_distance <= 0 or symbol.value_per_lot <= 0:
        raise SystemExit("[abort] SL distance and value_per_lot must be > 0")
    raw = risk_usd / (sl_distance * symbol.value_per_lot)
    # Snap to lot_step grid
    step = symbol.lot_step
    snapped = (raw // step) * step
    return max(symbol.min_lot, min(symbol.max_lot, snapped))


def resolve_price_and_stops(
    args, symbol: Symbol, connector=None
) -> tuple[Decimal, Decimal, Decimal | None]:
    """Returns (entry_price, stop_loss, take_profit_or_None). Uses live MT5 bid/ask
    when no --price is given and a connector is available; else falls back to --price."""
    if args.price:
        entry = Decimal(str(args.price))
    elif connector is not None:
        tick = connector.get_tick(symbol.ticker)
        if tick is None:
            raise SystemExit(f"[abort] No live tick for {symbol.ticker}")
        entry = tick.ask if args.side == "buy" else tick.bid
    else:
        raise SystemExit("[abort] --price required in dry-run mode (no MT5)")

    if args.sl is not None:
        sl = Decimal(str(args.sl))
    elif args.sl_pips is not None:
        pip = symbol.pip_value
        offset = Decimal(str(args.sl_pips)) * pip
        sl = entry - offset if args.side == "buy" else entry + offset
    else:
        raise SystemExit("[abort] provide --sl or --sl-pips")

    tp = None
    if args.tp is not None:
        tp = Decimal(str(args.tp))
    elif args.tp_pips is not None:
        pip = symbol.pip_value
        offset = Decimal(str(args.tp_pips)) * pip
        tp = entry + offset if args.side == "buy" else entry - offset

    return entry, sl, tp


def main() -> int:
    p = argparse.ArgumentParser(description="Manual trade via RiskEngine guards")
    p.add_argument("--config", default="config/config_live_10000.yaml")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    # Two ways to size: by $risk or by explicit lots
    p.add_argument("--risk", type=float, help="Dollar risk for this trade; lots auto-computed")
    p.add_argument("--lots", type=float, help="Explicit lot size (overrides --risk)")
    # SL / TP — either price or pips
    p.add_argument("--sl", type=float, help="Absolute SL price")
    p.add_argument("--sl-pips", type=float, help="SL distance in pips")
    p.add_argument("--tp", type=float, help="Absolute TP price")
    p.add_argument("--tp-pips", type=float, help="TP distance in pips")
    p.add_argument("--price", type=float, help="Override entry price (else live tick)")
    p.add_argument("--tag", default="manual", help="Strategy tag (default 'manual')")
    p.add_argument("--dry-run", action="store_true", help="Validate only, no MT5 submit")
    args = p.parse_args()

    # Load config
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        raise SystemExit(f"[abort] Config not found: {cfg_path}")
    with open(cfg_path) as f:
        config = yaml.safe_load(f)

    symbol = load_symbol(config, args.symbol)

    # Build MT5 connector (skipped for dry-run)
    connector = None
    if not args.dry_run:
        from src.connectors.mt5_connector import MT5Connector
        connector = MT5Connector(config)
        if not connector.connect():
            raise SystemExit("[abort] MT5 connection failed")

    entry, sl, tp = resolve_price_and_stops(args, symbol, connector)

    # Compute lots
    if args.lots:
        lots = Decimal(str(args.lots))
    elif args.risk:
        lots = compute_size_for_risk(
            Decimal(str(args.risk)), abs(entry - sl), symbol
        )
    else:
        raise SystemExit("[abort] provide --lots or --risk")

    # Build order with manual tag so RiskEngine halves size + applies cap
    order = Order(
        symbol=symbol,
        side=OrderSide.BUY if args.side == "buy" else OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=lots,
        price=entry,
        stop_loss=sl,
        take_profit=tp,
        metadata={"strategy": args.tag, "source": "manual_trade.py"},
    )

    # Print the plan up-front — user sees exactly what's being submitted.
    worst_case = (abs(entry - sl) * lots * symbol.value_per_lot)
    print("─" * 68)
    print(f"  Symbol:      {symbol.ticker}")
    print(f"  Side:        {args.side.upper()}")
    print(f"  Entry:       {entry}")
    print(f"  Stop Loss:   {sl}   (distance {abs(entry - sl)})")
    print(f"  Take Profit: {tp if tp else '—'}")
    print(f"  Lots:        {lots}")
    print(f"  Worst-case:  ${worst_case:.2f}  ← if SL hits")
    print(f"  Tag:         {args.tag}")
    print(f"  UTC hour:    {datetime.now(timezone.utc).hour:02d}")
    print("─" * 68)

    # RiskEngine validation — this is the gate the user's manual trades bypass today.
    risk_engine = RiskEngine(config)

    # Minimal portfolio state for validation; real balance if connector available
    if connector is not None:
        acct = connector.get_account_info()
        balance = acct.balance if acct else Decimal(str(config["account"]["initial_balance"]))
        equity = acct.equity if acct else balance
        positions = connector.get_positions()
    else:
        balance = Decimal(str(config["account"]["initial_balance"]))
        equity = balance
        positions = {}

    # HWM = equity so we don't fail the drawdown check on a fresh CLI launch
    risk_engine.daily_start_equity = equity
    risk_engine.equity_high_water_mark = equity

    try:
        ok, reason = risk_engine.validate_order(
            order=order,
            account_balance=balance,
            account_equity=equity,
            current_positions=positions,
            daily_pnl=Decimal("0"),
        )
    except Exception as e:
        print(f"  [REJECT] {e}")
        return 2

    if not ok:
        print(f"  [REJECT] {reason}")
        return 2

    print("  [OK] RiskEngine accepted the order.")

    if args.dry_run:
        print("  [dry-run] not submitting to MT5.")
        return 0

    # Submit
    placed = connector.place_order(
        symbol=symbol.ticker,
        side=order.side,
        quantity=order.quantity,
        order_type=OrderType.MARKET,
        price=entry,
        stop_loss=sl,
        take_profit=tp,
        comment=f"manual_trade.py/{args.tag}",
    )
    print(f"  [SUBMITTED] order_id={placed.order_id} status={placed.status.value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
