#!/usr/bin/env python3
"""
Cross-platform process cleanup — finds and kills rogue trading bot instances.

Works on Windows, macOS, and Linux via psutil.
"""

import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def cleanup():
    print("🔍 Searching for rogue trading processes...")

    try:
        import psutil
    except ImportError:
        print("⚠️  psutil not installed. Install with: pip install psutil")
        print("   Falling back to platform-specific method...")
        _fallback_cleanup()
        return

    killed = 0
    current_pid = os.getpid()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "main.py" in cmdline and proc.info["pid"] != current_pid:
                print(f"   Terminating PID {proc.info['pid']}: {cmdline[:80]}")
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if killed:
        print(f"✅ Terminated {killed} process(es).")
    else:
        print("   No rogue processes found.")

    # Clean up root-level state file
    state_file = PROJECT_ROOT / "data" / "state" / "system_state.json"
    if state_file.exists():
        state_file.unlink()
        print(f"🗑️  Removed {state_file.relative_to(PROJECT_ROOT)}")
    else:
        print("   No stale state file found.")


def _fallback_cleanup():
    """POSIX-only fallback when psutil is unavailable."""
    import subprocess
    import signal as _signal

    if sys.platform == "win32":
        print("❌ On Windows, please install psutil: pip install psutil")
        return

    try:
        output = subprocess.check_output(["ps", "-ef"]).decode()
        for line in output.split("\n"):
            if "python" in line and "main.py" in line and "grep" not in line:
                parts = line.split()
                if len(parts) > 1:
                    pid = int(parts[1])
                    print(f"   Killing PID {pid}: {line.strip()[:80]}")
                    os.kill(pid, _signal.SIGTERM)
    except Exception as e:
        print(f"   Fallback cleanup error: {e}")


if __name__ == "__main__":
    cleanup()
