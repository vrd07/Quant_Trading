#!/usr/bin/env python3
"""Verify every SMC OB config key is read by the strategy at runtime."""
import sys
from pathlib import Path
from decimal import Decimal
import yaml

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.strategies.smc_ob_strategy import SMCOrderBlockStrategy
from src.core.types import Symbol

EXPECTED = {
    "enabled": True, "timeframe": "1m", "long_only": True,
    "swing_lookback": 5, "min_impulse_atr_mult": 0.5,
    "ob_max_age_bars": 120, "ob_touch_tolerance_atr": 1.0,
    "cooldown_bars": 3, "adx_min_threshold": 8,
    "use_ema_trend_filter": True, "ema_trend_period": 50,
    "liquidity_premium_mult": 15.0, "min_liquidity_premium_mult": 8.0,
    "max_sl_atr": 4.0, "min_sl_atr": 0.05,
    "require_fvg_confluence": True, "fvg_ob_proximity_atr": 2.0,
    "fvg_max_age_bars": 50,
}

# Maps config-key -> attribute name on the strategy instance (when different)
ATTR_MAP = {
    "min_liquidity_premium_mult": "min_liquidity_premium_mult",
}

def mk_symbol():
    return Symbol(ticker="XAUUSD",
                  pip_value=Decimal("0.01"), min_lot=Decimal("0.01"),
                  max_lot=Decimal("100"), lot_step=Decimal("0.01"),
                  value_per_lot=Decimal("1"), min_stops_distance=Decimal("0"),
                  leverage=Decimal("1"))

configs = sorted(project_root.glob("config/config_live*.yaml"))
print(f"Checking {len(configs)} live configs...\n")

for cfg_path in configs:
    with open(cfg_path) as f:
        full = yaml.safe_load(f)
    smc = full.get("strategies", {}).get("smc_ob", {})

    print(f"── {cfg_path.name} ─────────────────")
    # 1) file-level: does the YAML have each expected key/value?
    file_ok = True
    for k, expected in EXPECTED.items():
        got = smc.get(k, "<MISSING>")
        mark = "✓" if got == expected else "✗"
        if got != expected:
            file_ok = False
        print(f"   yaml  {mark} {k:28s} = {got}")

    # 2) runtime: instantiate and check the actual attribute
    inst = SMCOrderBlockStrategy(mk_symbol(), smc)
    runtime_checks = {
        "enabled":                 inst.enabled,
        "long_only":               inst.long_only,
        "swing_lookback":          inst.swing_lookback,
        "min_impulse_atr_mult":    inst.min_impulse_atr_mult,
        "ob_max_age_bars":         inst.ob_max_age_bars,
        "ob_touch_tolerance_atr":  inst.ob_touch_tolerance_atr,
        "cooldown_bars":           inst.cooldown_bars,
        "adx_min_threshold":       inst.adx_min_threshold,
        "use_ema_trend_filter":    inst.use_ema_trend_filter,
        "ema_trend_period":        inst.ema_trend_period,
        "liquidity_premium_mult":  inst.liquidity_premium_mult,
        "min_liquidity_premium_mult": inst.min_liquidity_premium_mult,
        "max_sl_atr":              inst.max_sl_atr,
        "min_sl_atr":              inst.min_sl_atr,
        "require_fvg_confluence":  inst.require_fvg_confluence,
        "fvg_ob_proximity_atr":    inst.fvg_ob_proximity_atr,
        "fvg_max_age_bars":        inst.fvg_max_age_bars,
    }
    print("   --- runtime (after __init__) ---")
    for k, actual in runtime_checks.items():
        want = EXPECTED[k]
        ok = actual == want
        mark = "✓" if ok else "✗"
        print(f"   live  {mark} {k:28s} = {actual!r}  (want {want!r})")

    # 3) timeframe caveat — only respected via main.py dispatcher
    primary = full.get("strategies", {}).get("primary_timeframe", "5m")
    tf_note = f"strategy.timeframe key={smc.get('timeframe')!r} (NOT read by strategy ctor — main.py routes by primary_timeframe='{primary}')"
    print(f"   note  ⚠  {tf_note}")
    print()

print("Done. ✓ = value reached the code. ✗ = divergence.")
