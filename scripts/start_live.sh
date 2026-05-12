#!/bin/bash
# ============================================================
# Quant Trading Bot — Live Account Launcher
# Active account size is resolved from config/ACTIVE_CONFIG.
# Runs: health check → news fetch → regime classifier → live trading
#
# Usage:
#   chmod +x scripts/start_live.sh
#   ./scripts/start_live.sh              # interactive (default)
#   ./scripts/start_live.sh --force      # skip confirmations (for cron/restart)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Single source of truth for the active live config.
ACTIVE_CONFIG="$(head -n 1 config/ACTIVE_CONFIG 2>/dev/null | tr -d '[:space:]')"
ACTIVE_CONFIG="${ACTIVE_CONFIG:-config/config_live_10000.yaml}"

CONFIG=""
FORCE=false

# Parse args
for arg in "$@"; do
    case $arg in
        --force) FORCE=true ;;
    esac
done

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "ERROR: venv not found. Run: python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

# ── Account Selection ───────────────────────────────────────
if [ "$FORCE" = true ]; then
    CONFIG="$ACTIVE_CONFIG"
else
    echo ""
    echo "============================================================"
    echo "  Select Account Size  (ACTIVE_CONFIG → $ACTIVE_CONFIG)"
    echo "============================================================"
    echo ""
    echo "  0) Use ACTIVE_CONFIG  ← default"
    echo "  1) \$100"
    echo "  2) \$1,000"
    echo "  3) \$5,000"
    echo "  4) \$10,000"
    echo "  5) \$25,000"
    echo "  6) \$50,000"
    echo ""
    printf "  Enter choice [0-6] (default: 0): "
    read -r ACCOUNT_CHOICE

    case "${ACCOUNT_CHOICE:-0}" in
        0) CONFIG="$ACTIVE_CONFIG" ;;
        1) CONFIG="config/config_live_100.yaml" ;;
        2) CONFIG="config/config_live_1000.yaml" ;;
        3) CONFIG="config/config_live_5000.yaml" ;;
        4) CONFIG="config/config_live_10000.yaml" ;;
        5) CONFIG="config/config_live_25000.yaml" ;;
        6) CONFIG="config/config_live_50000.yaml" ;;
        *)
            echo "  Invalid choice. Using ACTIVE_CONFIG ($ACTIVE_CONFIG)."
            CONFIG="$ACTIVE_CONFIG"
            ;;
    esac
fi

echo ""
echo "============================================================"
echo "  Quant Trading Bot — GFT Account"
echo "  Config: $CONFIG"
echo "  Time:   $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "============================================================"
echo ""

# ── Runtime Risk Setup ───────────────────────────────────────
if [ "$FORCE" = false ]; then
    python3 scripts/runtime_setup.py --config "$CONFIG" || \
        echo "  [WARN] Runtime setup cancelled — using config defaults."
fi

# ── Step 1: Health Check ─────────────────────────────────────
echo "─── [1/4] Pre-flight Health Check ───"
echo ""

if python3 scripts/health_check.py --config "$CONFIG"; then
    echo ""
    echo "  ✓ Health check PASSED"
    echo ""
else
    echo ""
    echo "  ✗ Health check FAILED — fix issues above before trading"
    echo ""
    if [ "$FORCE" = false ]; then
        exit 1
    else
        echo "  --force flag set, continuing despite health check failure..."
        echo ""
    fi
fi

# ── Step 2: Fetch Daily News ─────────────────────────────────
echo "─── [2/4] Fetching Today's News Events ───"
echo ""

if python3 scripts/fetch_daily_news.py; then
    echo "  ✓ News events fetched and configs updated"
    echo ""
else
    echo "  ⚠ News fetch failed — news filter will use fallback CSV"
    echo ""
fi

# ── Step 3: Regime Classifier ────────────────────────────────
echo "─── [3/4] Nightly Regime Classifier ───"
echo ""

if python3 scripts/regime_classifier.py; then
    echo "  ✓ Regime classifier completed"
    echo ""
else
    echo "  ⚠ Regime classifier failed — strategies will use default weights"
    echo ""
fi

# ── Step 4: Launch Live Trading ──────────────────────────────
echo "─── [4/4] Starting Live Trading ───"
echo ""

# Spawn the interactive live-monitor pop-up in the background so the user
# can snap it next to MT5 while the bot streams state to it via JSON.
# (Uses stdlib Tkinter — no extra deps. Skipped if Tkinter unavailable.)
MONITOR_LOG="logs/live_monitor.log"
mkdir -p logs
if python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "  ➜ Launching live monitor pop-up..."
    nohup python3 scripts/live_monitor.py --refresh 1000 \
        >"$MONITOR_LOG" 2>&1 &
    MONITOR_PID=$!
    echo "    Monitor PID: $MONITOR_PID   (log: $MONITOR_LOG)"
else
    echo "  ⚠ Tkinter not available — skipping live monitor pop-up."
fi

# ── Telegram bot scheduler (optional — needs trading_bot/.env) ──
TG_LOG="logs/telegram_bot.log"
if [ -f "trading_bot/.env" ]; then
    echo "  ➜ Launching Telegram bot scheduler..."
    nohup python3 -m trading_bot.scheduler >"$TG_LOG" 2>&1 &
    TG_PID=$!
    echo "    Telegram bot PID: $TG_PID   (log: $TG_LOG)"
else
    echo "  ⚠ trading_bot/.env not found — Telegram bot scheduler not started."
    echo "    See trading_bot/README.md §1 to enable it."
fi

# Make sure background helpers die when the main bot exits.
trap 'kill ${MONITOR_PID:-} ${TG_PID:-} 2>/dev/null || true' EXIT INT TERM

if [ "$FORCE" = true ]; then
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG" --force-live
else
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG"
fi
