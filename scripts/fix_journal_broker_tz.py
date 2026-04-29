#!/usr/bin/env python3
"""
One-shot retro-fix for trade_journal.csv rows whose exit_time was stamped from
the MT5 deal-history `time` field (broker-server seconds) but mislabeled as UTC.

Bug context: src/portfolio/portfolio_engine.py used
    datetime.fromtimestamp(deal['time'], tz=timezone.utc)
for matching-deal closes. MT5 deal times are broker-local seconds, not real
UNIX UTC, so labelling them UTC shifted them forward by `broker_offset`. The
live fix subtracts connector.broker_offset on new closes; this script repairs
historical rows.

Discriminator: buggy rows have second-precision exit_time (no microseconds),
because int(matching_deal['time']) strips subseconds. Correctly-stamped rows
came from datetime.now(timezone.utc) and always carry microseconds.

Usage:
    python scripts/fix_journal_broker_tz.py --broker-offset-hours 2 --dry-run
    python scripts/fix_journal_broker_tz.py --broker-offset-hours 2 --apply
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_buggy_exit(exit_time: datetime) -> bool:
    """Buggy rows have integer-second precision (microsecond == 0)."""
    return exit_time.microsecond == 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", default="data/logs/trade_journal.csv",
                   help="Path to trade_journal.csv")
    p.add_argument("--broker-offset-hours", type=float, required=True,
                   help="Broker server offset in hours (e.g. 2 for GMT+2). "
                        "Subtracted from exit_time to recover real UTC.")
    p.add_argument("--apply", action="store_true",
                   help="Write changes back. Without this flag, runs in dry-run mode.")
    p.add_argument("--backup-suffix", default=".bak",
                   help="Suffix for backup file (default: .bak)")
    args = p.parse_args()

    path = Path(args.journal)
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 1

    offset = timedelta(hours=args.broker_offset_hours)

    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if "exit_time" not in fieldnames or "entry_time" not in fieldnames:
        print(f"error: CSV missing required columns; got {fieldnames}", file=sys.stderr)
        return 1

    fixed = 0
    skipped_microsec = 0
    skipped_unparsable = 0
    skipped_closed_on_broker = 0

    for row in rows:
        exit_dt = _parse_iso(row.get("exit_time", ""))
        entry_dt = _parse_iso(row.get("entry_time", ""))
        if exit_dt is None or entry_dt is None:
            skipped_unparsable += 1
            continue

        # closed_on_broker came from the fallback path which uses
        # datetime.now(timezone.utc) — already correct, skip even if it
        # somehow lacks microseconds (rare race).
        if (row.get("exit_reason") or "").strip() == "closed_on_broker":
            skipped_closed_on_broker += 1
            continue

        if not _is_buggy_exit(exit_dt):
            skipped_microsec += 1
            continue

        corrected = exit_dt - offset
        duration_sec = max(0.0, (corrected - entry_dt).total_seconds())

        # Preserve the original ISO format (with timezone) but at second precision.
        row["exit_time"] = corrected.isoformat()
        row["duration_seconds"] = f"{duration_sec:.6f}"
        fixed += 1

    summary = (
        f"rows total           : {len(rows)}\n"
        f"rows fixed           : {fixed}\n"
        f"skipped (had microsec): {skipped_microsec}\n"
        f"skipped (closed_on_broker): {skipped_closed_on_broker}\n"
        f"skipped (unparsable) : {skipped_unparsable}\n"
        f"broker offset applied: -{offset}"
    )
    print(summary)

    if not args.apply:
        print("\n[dry-run] no changes written. Re-run with --apply to write.")
        return 0

    if fixed == 0:
        print("\nNothing to apply.")
        return 0

    backup = path.with_suffix(path.suffix + args.backup_suffix)
    shutil.copy2(path, backup)
    print(f"\nbackup written       : {backup}")

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"journal rewritten    : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
