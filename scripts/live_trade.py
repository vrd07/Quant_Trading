#!/usr/bin/env python3
"""
Live Trading Script — account size resolved from config/ACTIVE_CONFIG.

Runs health check → regime classifier → live trading in sequence.
Python alternative to start_live.sh for Windows or direct invocation.

Usage:
    python scripts/live_trade.py                  # interactive
    python scripts/live_trade.py --force           # skip confirmations
"""

import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _active_config() -> str:
    marker = PROJECT_ROOT / "config" / "ACTIVE_CONFIG"
    try:
        path = marker.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if path:
            return path
    except (OSError, IndexError):
        pass
    return "config/config_live_10000.yaml"


CONFIG = _active_config()


def run_step(description: str, cmd: list, allow_failure: bool = False) -> bool:
    """Run a subprocess step, return True if it succeeded."""
    print(f"\n{'─' * 60}")
    print(f"  {description}")
    print(f"{'─' * 60}\n")

    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode == 0:
        print(f"\n  ✓ {description} — PASSED\n")
        return True
    else:
        if allow_failure:
            print(f"\n  ⚠ {description} — FAILED (continuing)\n")
            return False
        else:
            print(f"\n  ✗ {description} — FAILED\n")
            return False


def main():
    force = "--force" in sys.argv

    print("\n" + "=" * 60)
    print("  Quant Trading Bot — Live Trading")
    print(f"  Config: {CONFIG}")
    print("=" * 60)

    # Step 1: Health Check
    health_ok = run_step(
        "[1/3] Pre-flight Health Check",
        [sys.executable, "scripts/health_check.py", "--config", CONFIG],
    )
    if not health_ok and not force:
        print("  Fix health check issues before trading. Use --force to override.")
        sys.exit(1)

    # Step 2: Regime Classifier
    run_step(
        "[2/3] Nightly Regime Classifier",
        [sys.executable, "scripts/regime_classifier.py"],
        allow_failure=True,
    )

    # Step 3: Live Trading
    print(f"\n{'─' * 60}")
    print("  [3/3] Starting Live Trading")
    print(f"{'─' * 60}\n")

    live_cmd = [sys.executable, "src/main.py", "--env", "live", "--config", CONFIG]
    if force:
        live_cmd.append("--force-live")

    # Replace this process with the trading system
    import os
    os.execv(sys.executable, live_cmd)


if __name__ == "__main__":
    main()
