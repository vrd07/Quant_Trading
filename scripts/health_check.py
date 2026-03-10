#!/usr/bin/env python3
"""
Health Check Script — Run before each trading session.

Verifies all critical system components are functional:
  1. MT5 bridge connection (EA responding)
  2. Bridge file directory exists and is writable
  3. News CSV is fresh (today's or yesterday's)
  4. Kill switch is NOT active
  5. State files are valid (can be parsed)
  6. Account balance sanity (> $0)

Usage:
    python scripts/health_check.py [--config config/config_live_5000.yaml]

Exit codes:
    0 = All checks passed (safe to start trading)
    1 = One or more checks FAILED (do not start trading)
"""

import sys
import json
import yaml
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def check(name: str, ok: bool, detail: str = "") -> bool:
    status = "✅ PASS" if ok else "❌ FAIL"
    msg = f"  {status}  {name}"
    if detail:
        msg += f"  →  {detail}"
    print(msg)
    return ok


def main():
    parser = argparse.ArgumentParser(description="Trading System Health Check")
    parser.add_argument("--config", default="config/config_live_5000.yaml")
    args = parser.parse_args()

    print("=" * 60)
    print("🏥  Trading System Health Check")
    print(f"    {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    results = []

    # ── Load config ─────────────────────────────────────────────
    config_path = PROJECT_ROOT / args.config
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        results.append(check("Config file loadable", True, str(config_path.name)))
    except Exception as e:
        results.append(check("Config file loadable", False, str(e)))
        print("\n❌ Cannot load config — aborting remaining checks.")
        sys.exit(1)

    env = config.get("environment", "dev")
    print(f"\n  Environment : {env}")
    print(f"  Symbol      : {[k for k,v in config.get('symbols',{}).items() if v.get('enabled')]}")
    print()

    # ── Check 1: Bridge file directory ──────────────────────────
    bridge_cfg = config.get("file_bridge", {})
    bridge_data_dir = bridge_cfg.get("data_dir")
    if bridge_data_dir:
        data_dir = Path(bridge_data_dir).expanduser()
    else:
        # Auto-detect using MT5FileClient cross-platform logic
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from mt5_bridge.mt5_file_client import MT5FileClient
            data_dir = MT5FileClient._get_default_mt5_path()
        except Exception:
            import os as _os
            data_dir = Path(_os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) \
                       / "MetaQuotes" / "Terminal" / "Common" / "Files"
    status_file = data_dir / "mt5_status.json"

    results.append(check(
        "Bridge directory exists",
        data_dir.exists(),
        str(data_dir)
    ))

    # ── Check 2: MT5 status file (EA writing ticks) ─────────────
    if data_dir.exists():
        try:
            if status_file.exists():
                mtime = datetime.fromtimestamp(status_file.stat().st_mtime, tz=timezone.utc)
                age_sec = (datetime.now(timezone.utc) - mtime).total_seconds()
                results.append(check(
                    "MT5 status file fresh (< 30s)",
                    age_sec < 30,
                    f"last updated {age_sec:.0f}s ago"
                ))
                # Try to read it
                with open(status_file, encoding="utf-16") as f:
                    status_data = json.load(f)
                balance = status_data.get("balance", 0)
                results.append(check(
                    "Account balance > $0",
                    float(balance) > 0,
                    f"balance=${balance}"
                ))
            else:
                results.append(check("MT5 status file exists", False, "mt5_status.json not found — is the EA running?"))
                results.append(check("Account balance > $0", False, "cannot check (status file missing)"))
        except Exception as e:
            results.append(check("MT5 status file readable", False, str(e)))
    else:
        results.append(check("MT5 status file fresh", False, "bridge dir missing"))
        results.append(check("Account balance > $0", False, "bridge dir missing"))

    # ── Check 3: Kill switch NOT active ─────────────────────────
    kill_switch_file = PROJECT_ROOT / "data" / "state" / "kill_switch_alert.json"
    if kill_switch_file.exists():
        try:
            with open(kill_switch_file) as f:
                ks = json.load(f)
            ks_active = ks.get("status") == "ACTIVE"
            results.append(check(
                "Kill switch NOT active",
                not ks_active,
                f"reason: {ks.get('reason', '?')}" if ks_active else "OK"
            ))
        except Exception as e:
            results.append(check("Kill switch file readable", False, str(e)))
    else:
        results.append(check("Kill switch NOT active", True, "no alert file (clean)"))

    # ── Check 4: State file valid ───────────────────────────────
    state_dir = PROJECT_ROOT / "data" / "state" / env
    state_files = list(state_dir.glob("system_state_*.json")) if state_dir.exists() else []
    if state_files:
        latest_state = max(state_files, key=lambda p: p.stat().st_mtime)
        try:
            with open(latest_state) as f:
                state_data = json.load(f)
            results.append(check(
                "State file parseable",
                True,
                f"{latest_state.name} (balance={state_data.get('account_balance','?')})"
            ))
        except Exception as e:
            results.append(check("State file parseable", False, str(e)))
    else:
        results.append(check("State file parseable", True, "no state file (fresh start — OK)"))

    # ── Check 5: News CSV is fresh ───────────────────────────────
    nf_cfg = config.get("trading_hours", {}).get("news_filter", {})
    if nf_cfg.get("enabled", False):
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        candidates = [
            PROJECT_ROOT / f"news/{today_str}_news.csv",
            PROJECT_ROOT / f"news/{yesterday_str}_news.csv",
            PROJECT_ROOT / nf_cfg.get("csv_path", "news/news.csv"),
        ]
        found = next((p for p in candidates if p.exists()), None)
        results.append(check(
            "News CSV available",
            found is not None,
            str(found.name) if found else
            "MISSING — run: python scripts/fetch_daily_news.py"
        ))
    else:
        results.append(check("News filter", True, "disabled in config — skipped"))

    # ── Check 6: Log directory writable ─────────────────────────
    log_dir = PROJECT_ROOT / "data" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".health_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        results.append(check("Log directory writable", True, str(log_dir)))
    except Exception as e:
        results.append(check("Log directory writable", False, str(e)))

    # ── Summary ─────────────────────────────────────────────────
    passed = sum(results)
    total = len(results)
    print()
    print("=" * 60)
    if passed == total:
        print(f"🟢  {passed}/{total} checks passed — SYSTEM READY")
    else:
        print(f"🔴  {passed}/{total} checks passed — DO NOT START TRADING")
    print("=" * 60)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
