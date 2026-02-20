#!/bin/bash
set -x
exec > live_debug.log 2>&1
echo "Starting debug run at $(date)"
export PYTHONUNBUFFERED=1
which python3
python3 --version
ls -l src/main.py
# Try running with timeout to capture initial output
timeout 10s python3 -u src/main.py --env live --force-live
echo "Process exited with code $?"
