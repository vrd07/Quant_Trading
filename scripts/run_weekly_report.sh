#!/bin/bash
# Saturday weekly-report launchd wrapper.
#
# launchd runs jobs in a minimal environment and (when the repo lives under an
# iCloud-synced ~/Documents) the venv's site-init can transiently fail with
# "Resource deadlock avoided" (errno 11). We retry a few times — the deadlock
# is transient, so a second attempt almost always succeeds — and we use the
# venv python by absolute path so no `activate`/shell-init is needed.
set -u
REPO="/Users/varadbandekar/Documents/Quant_trading"
PY="$REPO/venv/bin/python"
cd "$REPO" || exit 1

for attempt in 1 2 3 4 5; do
    "$PY" scripts/weekly_report.py --week-offset 0
    rc=$?
    if [ "$rc" -eq 0 ]; then
        exit 0
    fi
    echo "[wrapper] attempt $attempt failed rc=$rc — retrying in 10s" >&2
    sleep 10
done
echo "[wrapper] all attempts failed" >&2
exit 1
