"""
Interactive runtime setup. Prompts the user for:
  1. Lot size per trade
  2. Max loss per trade (USD) — shows the implied stop-loss in pips
  3. Max daily loss (USD)

Writes the chosen values to config/runtime_overrides.yaml, which is merged
on top of the selected config by src/main.py at startup.

Usage:
    python scripts/runtime_setup.py --config config/config_live_50000.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


OVERRIDE_PATH = Path("config/runtime_overrides.yaml")


def _prompt_float(prompt: str, default: float, minimum: float | None = None) -> float:
    while True:
        raw = input(f"{prompt} [default: {default}]: ").strip()
        if raw == "":
            return default
        try:
            val = float(raw)
        except ValueError:
            print("  Invalid number, try again.")
            continue
        if minimum is not None and val < minimum:
            print(f"  Must be >= {minimum}.")
            continue
        return val


def _primary_symbol(config: dict) -> tuple[str, dict]:
    for ticker, cfg in config.get("symbols", {}).items():
        if cfg.get("enabled"):
            return ticker, cfg
    ticker = next(iter(config.get("symbols", {})))
    return ticker, config["symbols"][ticker]


def _usd_per_pip(symbol_cfg: dict, lot_size: float) -> float:
    # USD value of 1 pip = pip_value * value_per_lot * lot_size
    return float(symbol_cfg["pip_value"]) * float(symbol_cfg["value_per_lot"]) * lot_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Base config file (for defaults)")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 1

    with cfg_path.open("r") as f:
        config = yaml.safe_load(f)

    balance = float(config["account"]["initial_balance"])
    symbol, sym_cfg = _primary_symbol(config)
    min_lot = float(sym_cfg.get("min_lot", 0.01))
    max_lot = float(sym_cfg.get("max_lot", 1.0))
    lot_step = float(sym_cfg.get("lot_step", 0.01))

    default_lot = min_lot
    default_risk_pct = float(config["risk"].get("risk_per_trade_pct", 0.003))
    default_risk_usd = round(balance * default_risk_pct, 2)
    default_daily_pct = float(config["risk"].get("max_daily_loss_pct", 0.02))
    default_daily_usd = round(
        float(config["risk"].get("absolute_max_loss_usd", balance * default_daily_pct)), 2
    )

    print()
    print("=" * 60)
    print("   Runtime Risk Setup")
    print("=" * 60)
    print(f"   Account balance : ${balance:,.2f}")
    print(f"   Primary symbol  : {symbol}")
    print(f"   Lot range       : {min_lot} - {max_lot} (step {lot_step})")
    print()

    # 1. Lot size
    lot_size = _prompt_float(
        f"1) Lot size per trade ({symbol})",
        default=default_lot,
        minimum=min_lot,
    )
    if lot_size > max_lot:
        print(f"   Clamped to max_lot {max_lot}")
        lot_size = max_lot

    usd_per_pip = _usd_per_pip(sym_cfg, lot_size)
    print(f"   => 1 pip on {lot_size} lots {symbol} ≈ ${usd_per_pip:.2f}")
    print()

    # 2. Max loss per trade
    max_loss_trade = _prompt_float(
        "2) Max loss per trade (USD)",
        default=default_risk_usd,
        minimum=0.01,
    )
    if usd_per_pip > 0:
        implied_pips = max_loss_trade / usd_per_pip
        print(
            f"   => With {lot_size} lots, ${max_loss_trade:.2f} max loss "
            f"= stop of ~{implied_pips:.0f} pips"
        )
    print()

    # 3. Max daily loss
    max_daily_loss = _prompt_float(
        "3) Max daily loss (USD)",
        default=default_daily_usd,
        minimum=max_loss_trade,
    )
    daily_pct = max_daily_loss / balance if balance else 0
    print(f"   => {max_daily_loss:.2f} / {balance:,.2f} = {daily_pct:.2%} of balance")
    print()

    # Build overrides
    risk_per_trade_pct = max_loss_trade / balance if balance else default_risk_pct
    overrides = {
        "symbols": {
            symbol: {
                "min_lot": lot_size,
                "max_lot": lot_size,
            }
        },
        "risk": {
            "risk_per_trade_pct": risk_per_trade_pct,
            "risk_per_trade_usd": max_loss_trade,
            "max_daily_loss_pct": daily_pct,
            "absolute_max_loss_usd": max_daily_loss,
            "fixed_lot_size": lot_size,
        },
    }

    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OVERRIDE_PATH.open("w") as f:
        yaml.safe_dump(overrides, f, sort_keys=False)

    print("=" * 60)
    print(f"   Saved overrides -> {OVERRIDE_PATH}")
    print(f"   Lot size        : {lot_size}")
    print(f"   Max loss/trade  : ${max_loss_trade:.2f}")
    print(f"   Max daily loss  : ${max_daily_loss:.2f}")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
