"""
Interactive runtime setup. Prompts the user for:
  1. Which symbols to trade + broker ticker (e.g. XAUUSD.x, GOLDm)
  2. Lot size per selected symbol
  3. Max loss per trade (USD) — shows the implied stop-loss in pips per symbol
  4. Take-profit reward:risk ratio (TP = rr × the max-loss stop) + optional
     fixed $ TP override (0 = use the RR)
  5. Max daily loss (USD)
  6. Max total drawdown (USD)
  7. Max daily profit (USD)
  8. Max concurrent positions

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


def _prompt_int(prompt: str, default: int, minimum: int | None = None) -> int:
    while True:
        raw = input(f"{prompt} [default: {default}]: ").strip()
        if raw == "":
            return default
        try:
            val = int(raw)
        except ValueError:
            print("  Invalid integer, try again.")
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

    # ── Take-profit: reward:risk ratio ──
    print("--- Step 3: Take-profit reward:risk ratio ---")
    print("  Your max-loss above defines each trade's stop-loss distance; the")
    print("  take-profit is placed at this reward:risk multiple of that stop.")
    print("  e.g. 1.0 = 1:1, 2.0 = 1:2, 3.0 = 1:3. Applies to every strategy")
    print("  (squeeze_breakout / stoch_pullback keep their own validated TP).")
    default_rr = float(config["risk"].get("reward_risk_ratio", 2.0) or 2.0)
    reward_risk_ratio = _prompt_float(
        "  Reward:risk ratio (e.g. 1, 2, 3)",
        default=default_rr,
        minimum=0.1,
    )
    tp_usd_at_rr = max_loss_trade * reward_risk_ratio
    print()
    print(f"  => TP placed at 1:{reward_risk_ratio:g} — banks ~${tp_usd_at_rr:.2f} if hit (risking ${max_loss_trade:.2f})")
    print("  Implied take-profit distance (using YOUR lot size per symbol):")
    for tkr, scfg in selected.items():
        user_lot = float(scfg["_user_lot"])
        pip_usd = _usd_per_pip(scfg, user_lot)
        if pip_usd > 0:
            pips = tp_usd_at_rr / pip_usd
            print(f"    {tkr:12s} {user_lot} lots -> ~{pips:.0f} pip TP (RR 1:{reward_risk_ratio:g})")
    print()

    # ── Optional: fixed $ take-profit override ──
    print("--- Step 3b: Fixed $ take-profit override (optional) ---")
    print("  Leave 0 to use the reward:risk TP above. If > 0, the bot instead")
    print("  rewrites every trade's TP to bank exactly this many USD, ignoring")
    print("  the RR ratio (strategies that keep their own TP are unaffected).")
    default_tp_usd = round(float(config["risk"].get("take_profit_usd", 0) or 0), 2)
    take_profit_usd = _prompt_float(
        "  Fixed take-profit per trade (USD, 0 = use RR)",
        default=default_tp_usd,
        minimum=0.0,
    )
    if take_profit_usd > 0:
        print()
        print("  Implied take-profit distance (using YOUR lot size per symbol):")
        for tkr, scfg in selected.items():
            user_lot = float(scfg["_user_lot"])
            pip_usd = _usd_per_pip(scfg, user_lot)
            if pip_usd > 0:
                pips = take_profit_usd / pip_usd
                print(f"    {tkr:12s} {user_lot} lots -> ~{pips:.0f} pip TP for ${take_profit_usd:.2f}")
        if take_profit_usd <= max_loss_trade:
            print(
                f"  [note] TP ${take_profit_usd:.2f} <= max loss ${max_loss_trade:.2f}"
                f" per trade — reward-to-risk is below 1:1."
            )
    else:
        print("  => Fixed $ TP disabled — using the reward:risk ratio above")
    print()

    # ── Max daily loss ──
    print("--- Step 4: Max daily loss ---")
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

    # ── Max total drawdown ──
    print("--- Step 5: Max total drawdown ---")
    default_dd_pct = float(config["risk"].get("max_drawdown_pct", 0.07) or 0.07)
    default_dd_usd = round(balance * default_dd_pct, 2)
    max_drawdown_usd = _prompt_float(
        "  Max total drawdown (USD)",
        default=default_dd_usd,
        minimum=max_daily_loss,
    )
    dd_pct = max_drawdown_usd / balance if balance else default_dd_pct
    print(f"  => {max_drawdown_usd:.2f} / {balance:,.2f} = {dd_pct:.2%} of balance")
    print("     (risk engine enforces this as a % of the equity high-water-mark;")
    print("      the dashboard displays the USD value you entered.)")
    print()

    # ── Max daily profit ──
    print("--- Step 6: Max daily profit (stop trading once hit) ---")
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

    # ── Max concurrent positions ──
    print("--- Step 7: Max concurrent positions ---")
    default_max_positions = int(config["risk"].get("max_positions", 1) or 1)
    max_positions = _prompt_int(
        "  Max open positions at once",
        default=default_max_positions,
        minimum=1,
    )
    print(f"  => Risk engine will reject new orders once {max_positions} position(s) are open")
    print()

    # ── Directional lock (The5ers no-hedge rule) ──
    print("--- Step 8: Directional lock (no hedging) ---")
    print("  When ON, the bot refuses a SELL while a BUY is open (and vice-versa),")
    print("  so you never hold opposing positions. Turn OFF to allow both directions")
    print("  open at once (hedging) up to your max-positions limit.")
    default_dir_lock = bool(config["risk"].get("directional_lock", True))
    directional_lock = _prompt_yn("  Enable directional lock?", default=default_dir_lock)
    print(f"  => Directional lock {'ON (no hedging)' if directional_lock else 'OFF (hedging allowed)'}")
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
            "reward_risk_ratio": reward_risk_ratio,
            "take_profit_usd": take_profit_usd,
            "max_daily_loss_pct": daily_pct,
            "absolute_max_loss_usd": max_daily_loss,
            "max_drawdown_pct": dd_pct,
            "absolute_max_drawdown_usd": max_drawdown_usd,
            "max_daily_profit_usd": max_daily_profit,
            "max_positions": max_positions,
            "directional_lock": directional_lock,
        },
    }

    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OVERRIDE_PATH.open("w") as f:
        yaml.safe_dump(overrides, f, sort_keys=False)

    print("=" * 60)
    print(f"   Saved overrides -> {OVERRIDE_PATH}")
    print(f"   Symbols         : {', '.join(selected.keys())}")
    print(f"   Max loss/trade  : ${max_loss_trade:.2f}")
    print(f"   Reward:risk     : 1:{reward_risk_ratio:g}" + (" (overridden by fixed TP)" if take_profit_usd > 0 else ""))
    print(f"   Fixed TP/trade  : ${take_profit_usd:.2f}" + (" (disabled — using RR)" if take_profit_usd == 0 else ""))
    print(f"   Max daily loss  : ${max_daily_loss:.2f}")
    print(f"   Max drawdown    : ${max_drawdown_usd:.2f}")
    print(f"   Max daily profit: ${max_daily_profit:.2f}" + (" (disabled)" if max_daily_profit == 0 else ""))
    print(f"   Max positions   : {max_positions}")
    print(f"   Directional lock: {'ON (no hedging)' if directional_lock else 'OFF (hedging allowed)'}")
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
