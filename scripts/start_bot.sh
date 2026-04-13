#!/bin/bash
# Starts the live trading bot with caffeinate so the Mac won't idle-sleep
# while the bot is running. When you Ctrl-C the bot, sleep prevention
# ends automatically (caffeinate exits with its child).
#
# Usage:
#   ./scripts/start_bot.sh                                  # default config
#   ./scripts/start_bot.sh config/config_live_50000.yaml    # override config

set -e

cd "$(dirname "$0")/.."

CONFIG="${1:-config/config_live_10000.yaml}"

echo "Starting trading bot with caffeinate (no idle sleep)"
echo "Config: $CONFIG"
echo "Ctrl-C to stop. Sleep prevention ends when bot exits."
echo

# -i : prevent idle sleep
# -m : prevent disk idle sleep (keeps MT5 file bridge I/O responsive)
# -s : prevent system sleep on AC power
exec caffeinate -ims venv/bin/python src/main.py --env live --config "$CONFIG"
