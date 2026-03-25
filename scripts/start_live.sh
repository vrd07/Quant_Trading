#!/bin/bash
# ============================================================
# Quant Trading Bot — macOS/Linux Launcher
# Run: chmod +x scripts/start_live.sh && ./scripts/start_live.sh
# ============================================================

# Get project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Check for venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "============================================================"
echo -e "\033[31m  ⚠️  LIVE TRADING MODE — REAL MONEY ⚠️\033[0m"
echo "============================================================"
echo ""

read -p "Are you ABSOLUTELY SURE you want to trade live? (type YES): " confirm

if [ "$confirm" != "YES" ]; then
    echo "Live trading cancelled."
    exit 0
fi

# exec replaces the shell process with Python — no zombie shell parent, O(1) process start
exec python3 -OO src/main.py --env live
