#!/usr/bin/env python3
"""
Broker DOM probe verdict — reads mt5_dom_probe.json (written by
mt5_bridge/EA_DOMProbe.mq5) for ~60 s and prints one verdict:

  NO BOOK            broker publishes nothing (or EA not attached/heartbeat dead)
  TOP-OF-BOOK ONLY   <=2 levels or volumes never change (synthetic quote echo)
  REAL DEPTH         >2 levels with changing volumes -> a real book exists

Decision gate (spec 2026-07-16): only REAL DEPTH justifies designing a DOM
heatmap layer; the other verdicts close the resting-order question.

Usage:
    python scripts/check_dom_probe.py [--seconds 60]
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "mt5_bridge"))


def read_snapshot(path: Path) -> dict | None:
    """Read+parse one probe snapshot. The EA writes UTF-16LE with a BOM
    (MQL5 FileOpen with FILE_TXT, no FILE_ANSI) so this must decode as
    utf-16, not utf-8. Returns None on any read/parse failure (including
    a mid-write race, which the next heartbeat will win)."""
    try:
        return json.loads(path.read_text(encoding="utf-16"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None


def classify_snapshots(snapshots: list[dict]) -> str:
    """Pure verdict logic over parsed probe snapshots."""
    with_levels = [s for s in snapshots if s.get("levels")]
    if not with_levels:
        return "NO BOOK"
    max_levels = max(len(s["levels"]) for s in with_levels)
    volume_sets = {tuple(round(l["volume"], 4) for l in s["levels"])
                   for s in with_levels}
    if max_levels <= 2 or len(volume_sets) <= 1:
        return "TOP-OF-BOOK ONLY"
    return "REAL DEPTH"


def main() -> int:
    p = argparse.ArgumentParser(description="Classify the broker's DOM feed")
    p.add_argument("--seconds", type=int, default=60)
    args = p.parse_args()

    from mt5_file_client import MT5FileClient
    probe = MT5FileClient().data_dir / "mt5_dom_probe.json"
    print(f"Watching {probe} for {args.seconds}s "
          f"(EA_DOMProbe must be attached to the XAUUSDs chart)…")

    snapshots, last_mtime = [], 0.0
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        try:
            mtime = probe.stat().st_mtime
        except FileNotFoundError:
            time.sleep(1)
            continue
        if mtime > last_mtime:
            last_mtime = mtime
            snap = read_snapshot(probe)
            if snap is not None:
                snapshots.append(snap)
        time.sleep(1)

    if not snapshots:
        print("VERDICT: NO BOOK (probe file never appeared/updated — "
              "is EA_DOMProbe compiled+attached?)")
        return 1
    verdict = classify_snapshots(snapshots)
    n = max((len(s.get("levels", [])) for s in snapshots), default=0)
    print(f"VERDICT: {verdict} ({len(snapshots)} snapshots, max {n} levels)")
    if verdict == "REAL DEPTH":
        print("→ a real book exists — a DOM heatmap layer is worth designing.")
    else:
        print("→ no usable resting-order data; the DOM question is closed "
              "(spec decision gate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
