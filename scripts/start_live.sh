#!/bin/bash
# ============================================================
# Quant Trading Bot — $50K GFT Account Launcher
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

CONFIG="config/config_live_50000.yaml"
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

echo ""
echo "============================================================"
echo "  Quant Trading Bot — GFT \$50,000 Account"
echo "  Config: $CONFIG"
echo "  Time:   $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "============================================================"
echo ""

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

if [ "$FORCE" = true ]; then
    exec python3 src/main.py --env live --config "$CONFIG" --force-live
else
    exec python3 src/main.py --env live --config "$CONFIG"
fi
