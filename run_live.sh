#!/bin/bash
exec > live_output.log 2>&1
echo "Starting live trading at $(date)"
# Use full path or rely on current venv settings
# Try default python3 first
which python3
python3 --version
python3 src/main.py --env live --force-live
echo "Process exited with code $?"
