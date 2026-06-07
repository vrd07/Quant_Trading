#!/usr/bin/env python3
"""Single-process daily content runner for launchd.

launchd must invoke THIS via venv/bin/python directly (not a bash wrapper):
/bin/bash has no Full Disk Access, so it can't even read a script under
~/Documents (TCC blocks it — "Operation not permitted"). The venv python is the
binary that holds FDA, so running it directly is what the scheduled job needs —
same pattern as the other com.quanttrading.* jobs.

Steps: load config/sentiment.env into the environment (launchd won't source it),
refresh the sentiment snapshot (scoring only), then build caption + reel + card.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# launchd starts with a bare environment — load the API keys + CONTENT_HANDLE.
env_file = ROOT / "config" / "sentiment.env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            os.environ.setdefault(k, v)

py = sys.executable
log = ROOT / "data" / "sentiment" / "content" / "daily.log"
log.parent.mkdir(parents=True, exist_ok=True)

with open(log, "a", encoding="utf-8") as f:
    f.write(f"\n=== {__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S} "
            f"launchd content run (pid {os.getpid()}) ===\n")
    f.flush()
    # 1) refresh snapshot — scoring/display only (no AI decision / Telegram / paper)
    subprocess.run([py, str(ROOT / "scripts" / "run_sentiment_engine.py"),
                    "--decisions", "off", "--notify", "off", "--paper", "off"],
                   cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, check=False)
    # 2) build caption + reel + card
    subprocess.run([py, str(ROOT / "scripts" / "sentiment_content.py"),
                    "--handle", os.environ.get("CONTENT_HANDLE", "@varad_fx")],
                   cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT, check=False)
    f.write("--- done ---\n")
