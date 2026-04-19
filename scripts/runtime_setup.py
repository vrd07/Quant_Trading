"""
Interactive runtime setup. Prompts the user for:
  1. Which symbols to trade + broker ticker (e.g. XAUUSD.x, GOLDm)
  2. Lot size per selected symbol
  3. Max loss per trade (USD) — shows the implied stop-loss in pips per symbol
  4. Max daily loss (USD)

Writes the chosen values to config/runtime_overrides.yaml, which is merged
on top of the selected config by src/main.py at startup.

Usage:
    python scripts/runtime_setup.py --config config/config_live_50000.yaml
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

import yaml


OVERRIDE_PATH = Path("config/runtime_overrides.yaml")


def _prompt_str(prompt: str, default: str) -> str:
    raw = input(f"{prompt} [default: {default}]: ").strip()
    return raw or default


def _prompt_yn(prompt: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{d}]: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


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


def _usd_per_pip(symbol_cfg: dict, lot_size: float) -> float:
    return float(symbol_cfg["pip_value"]) * float(symbol_cfg["value_per_lot"]) * lot_size


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Base config file (for defaults)")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        return 1

    with cfg_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config:
        print(
            f"ERROR: config file loaded as empty: {cfg_path}\n"
            f"  The file exists but has no YAML content. Likely causes:\n"
            f"  - Git clone didn't transfer the file contents (try: git pull or re-clone)\n"
            f"  - File was accidentally emptied — restore with: git checkout -- {cfg_path}",
            file=sys.stderr,
        )
        return 1

    account = config.get("account") or {}
    if "initial_balance" not in account:
        print(f"ERROR: config is missing 'account.initial_balance': {cfg_path}", file=sys.stderr)
        return 1
    balance = float(account["initial_balance"])
    all_symbols = config.get("symbols") or {}

    print()
    print("=" * 60)
    print("   Runtime Trading Setup")
    print("=" * 60)
    print(f"   Account balance : ${balance:,.2f}")
    print()
    print("   Tip: your broker may add a suffix to the symbol.")
    print("        e.g. XAUUSD may show as XAUUSD.x, XAUUSDm, or GOLD.")
    print("        Check your MT5 Market Watch for the exact ticker.")
    print()

    selected: dict[str, dict] = {}  # broker_ticker -> symbol cfg (with lot_size applied)
    disabled_bases: list[str] = []

    # ── Per-symbol: select + rename + lot size ──
    print("--- Step 1: Select symbols to trade ---")
    print()
    for base_ticker, sym_cfg in all_symbols.items():
        default_on = bool(sym_cfg.get("enabled", False))
        trade_it = _prompt_yn(f"  Trade {base_ticker}?", default=default_on)
        if not trade_it:
            disabled_bases.append(base_ticker)
            print()
            continue

        while True:
            broker_ticker = _prompt_str(
                f"    Broker ticker for {base_ticker}",
                default=base_ticker,
            )
            try:
                float(broker_ticker)
                print(f"    '{broker_ticker}' looks like a number — enter a symbol name (e.g. {base_ticker}, {base_ticker}.x).")
                continue
            except ValueError:
                pass
            break

        min_lot = float(sym_cfg.get("min_lot", 0.01))
        max_lot = float(sym_cfg.get("max_lot", 1.0))
        lot = _prompt_float(
            f"    Lot size per trade ({broker_ticker})",
            default=min_lot,
            minimum=min_lot,
        )
        if lot > max_lot:
            print(f"    WARNING: {lot} exceeds config max_lot ({max_lot}) — using your value anyway.")

        pip_usd = _usd_per_pip(sym_cfg, lot)
        print(f"    => 1 pip on {lot} lots {broker_ticker} ≈ ${pip_usd:.2f}")

        new_cfg = copy.deepcopy(sym_cfg)
        new_cfg["enabled"] = True
        new_cfg["min_lot"] = lot
        new_cfg["max_lot"] = lot
        new_cfg["_user_lot"] = lot  # explicit copy for display, never read by main.py
        selected[broker_ticker] = new_cfg

        if broker_ticker != base_ticker:
            disabled_bases.append(base_ticker)
        print()

    if not selected:
        print("ERROR: no symbols selected. Aborting.", file=sys.stderr)
        return 1

    # ── Max loss per trade ──
    print("--- Step 2: Max loss per trade ---")
    default_risk_pct = float(config["risk"].get("risk_per_trade_pct", 0.003))
    default_risk_usd = round(balance * default_risk_pct, 2)
    max_loss_trade = _prompt_float(
        "  Max loss per trade (USD)",
        default=default_risk_usd,
        minimum=0.01,
    )
    print()
    print("  Implied stop-loss distance (using YOUR lot size per symbol):")
    for tkr, scfg in selected.items():
        user_lot = float(scfg["_user_lot"])
        pip_usd = _usd_per_pip(scfg, user_lot)
        if pip_usd > 0:
            pips = max_loss_trade / pip_usd
            print(f"    {tkr:12s} {user_lot} lots -> ~{pips:.0f} pip stop for ${max_loss_trade:.2f}")
    print()

    # ── Max daily loss ──
    print("--- Step 3: Max daily loss ---")
    default_daily_usd = round(
        float(config["risk"].get("absolute_max_loss_usd", balance * 0.02)), 2
    )
    max_daily_loss = _prompt_float(
        "  Max daily loss (USD)",
        default=default_daily_usd,
        minimum=max_loss_trade,
    )
    daily_pct = max_daily_loss / balance if balance else 0
    print(f"  => {max_daily_loss:.2f} / {balance:,.2f} = {daily_pct:.2%} of balance")
    print()

    # ── Max daily profit ──
    print("--- Step 4: Max daily profit (stop trading once hit) ---")
    default_profit_usd = round(
        float(config["risk"].get("max_daily_profit_usd", balance * 0.01)), 2
    )
    max_daily_profit = _prompt_float(
        "  Max daily profit target (USD, 0 to disable)",
        default=default_profit_usd,
        minimum=0.0,
    )
    if max_daily_profit > 0:
        profit_pct = max_daily_profit / balance if balance else 0
        print(f"  => {max_daily_profit:.2f} / {balance:,.2f} = {profit_pct:.2%} of balance")
    else:
        print("  => Daily profit target disabled")
    print()

    # ── Build overrides ──
    risk_per_trade_pct = max_loss_trade / balance if balance else default_risk_pct

    symbols_override: dict[str, dict] = {}
    for base in disabled_bases:
        symbols_override[base] = {"enabled": False}
    for tkr, scfg in selected.items():
        clean = {k: v for k, v in scfg.items() if not k.startswith("_")}
        symbols_override[tkr] = clean

    overrides = {
        "symbols": symbols_override,
        "risk": {
            "risk_per_trade_pct": risk_per_trade_pct,
            "risk_per_trade_usd": max_loss_trade,
            "max_daily_loss_pct": daily_pct,
            "absolute_max_loss_usd": max_daily_loss,
            "max_daily_profit_usd": max_daily_profit,
        },
    }

    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OVERRIDE_PATH.open("w") as f:
        yaml.safe_dump(overrides, f, sort_keys=False)

    print("=" * 60)
    print(f"   Saved overrides -> {OVERRIDE_PATH}")
    print(f"   Symbols         : {', '.join(selected.keys())}")
    print(f"   Max loss/trade  : ${max_loss_trade:.2f}")
    print(f"   Max daily loss  : ${max_daily_loss:.2f}")
    print(f"   Max daily profit: ${max_daily_profit:.2f}" + (" (disabled)" if max_daily_profit == 0 else ""))
    print("=" * 60)
    print()
    print("!" * 60)
    print("  IMPORTANT — MT5 chart attachment")
    print("!" * 60)
    print("  The EA only streams live quotes for the chart it's attached")
    print("  to. Before signals can fire on a new symbol, open a chart of")
    print("  that EXACT broker ticker and drag EA_FileBridge onto it:")
    print()
    for tkr in selected.keys():
        print(f"    -> Open a {tkr} chart and attach the EA")
    print()
    print("  (In MT5: File > New Chart > pick ticker, then drag the EA")
    print("   from Navigator > Expert Advisors onto the chart window.)")
    print("!" * 60)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
