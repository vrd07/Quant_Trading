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

# Sentiment engine API keys (optional). config/sentiment.env holds FRED_API_KEY
# and an optional DXY_LEVEL override (see config/sentiment.env.example). Exported
# so the sentiment engine subprocess inherits them. Absent → fundamental feed
# stays neutral/MISSING (fail-safe), the bot is unaffected.
if [ -f "config/sentiment.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source config/sentiment.env
    set +a
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
    echo "  7) \$100,000"
    echo ""
    printf "  Enter choice [0-7] (default: 0): "
    read -r ACCOUNT_CHOICE

    case "${ACCOUNT_CHOICE:-0}" in
        0) CONFIG="$ACTIVE_CONFIG" ;;
        1) CONFIG="config/config_live_100.yaml" ;;
        2) CONFIG="config/config_live_1000.yaml" ;;
        3) CONFIG="config/config_live_5000.yaml" ;;
        4) CONFIG="config/config_live_10000.yaml" ;;
        5) CONFIG="config/config_live_25000.yaml" ;;
        6) CONFIG="config/config_live_50000.yaml" ;;
        7) CONFIG="config/config_live_100000.yaml" ;;
        *)
            echo "  Invalid choice. Using ACTIVE_CONFIG ($ACTIVE_CONFIG)."
            CONFIG="$ACTIVE_CONFIG"
            ;;
    esac
fi

# Persist the chosen config back to ACTIVE_CONFIG so it stays the
# "last launched" pointer for the next session and any tooling that
# auto-resolves from it (journal viewer, dashboards, etc.).
if [ "$CONFIG" != "$ACTIVE_CONFIG" ]; then
    echo "$CONFIG" > config/ACTIVE_CONFIG
    echo "  ➜ Updated ACTIVE_CONFIG → $CONFIG"
fi

# Derive the per-config state-file stem (matches state_namespacing
# from 2026-05-14) so the monitor pairs with THIS launch's bot,
# regardless of any later ACTIVE_CONFIG edits.
CONFIG_STEM="$(basename "$CONFIG" .yaml)"
MONITOR_STATE_FILE="data/metrics/live_monitor_state_${CONFIG_STEM}.json"

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
echo "─── [3/5] Nightly Regime Classifier ───"
echo ""

if python3 scripts/regime_classifier.py; then
    echo "  ✓ Regime classifier completed"
    echo ""
else
    echo "  ⚠ Regime classifier failed — strategies will use default weights"
    echo ""
fi

# ── Step 4: Regime Health Sanity Check ───────────────────────
echo "─── [4/5] Regime Health Sanity Check ───"
echo ""

if python3 scripts/check_regime_health.py; then
    echo ""
else
    echo ""
    if [ "$FORCE" = false ]; then
        printf "  Continue with degraded regime ML? [y/N]: "
        read -r REGIME_CONTINUE
        case "${REGIME_CONTINUE:-N}" in
            y|Y|yes|YES) echo "  Continuing as requested." ;;
            *) echo "  Aborting. Fix the regime CSV (see scripts/refresh_historical_data.py)."; exit 1 ;;
        esac
    else
        echo "  --force flag set, continuing despite degraded regime ML..."
    fi
    echo ""
fi

# ── Step 5: Launch Live Trading ──────────────────────────────
echo "─── [5/5] Starting Live Trading ───"
echo ""

# Spawn the interactive live-monitor pop-up in the background so the user
# can snap it next to MT5 while the bot streams state to it via JSON.
# (Uses stdlib Tkinter — no extra deps. Skipped if Tkinter unavailable.)
MONITOR_LOG="logs/live_monitor.log"
mkdir -p logs
if python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "  ➜ Launching live monitor pop-up..."
    nohup python3 scripts/live_monitor.py --refresh 1000 \
        --state-file "$MONITOR_STATE_FILE" \
        >"$MONITOR_LOG" 2>&1 &
    MONITOR_PID=$!
    echo "    Monitor PID: $MONITOR_PID   (log: $MONITOR_LOG)"
    echo "    Monitor state: $MONITOR_STATE_FILE"
else
    echo "  ⚠ Tkinter not available — skipping live monitor pop-up."
fi

# ── Market Sentiment Engine + pop-up (XAUUSD GSS) ───────────────
# The engine assembles the Gold Sentiment Score on a slow clock (15 min) and
# writes data/metrics/sentiment_monitor_state.json; the pop-up renders it next
# to the live monitor. Both are DISPLAY-ONLY — they never trade and never touch
# the risk engine. Technical bias is live from our own 5m data; the fundamental
# (FRED) bias needs FRED_API_KEY (see config/sentiment.env.example).
SENTIMENT_LOG="logs/sentiment_engine.log"
SENTIMENT_MON_LOG="logs/sentiment_monitor.log"
echo "  ➜ Launching market sentiment engine (XAUUSD GSS, 15-min loop,"
echo "    intraday AI decisions on opportunity — advisory, never auto-executes)..."
# Reap any running sentiment engine first — like the Telegram scheduler below,
# a leftover looper races the shared state file and multiplies the rate-limited
# feed calls (FRED/Myfxbook/Alpha Vantage). Keep exactly one: each start_live
# run cleanly replaces the previous engine, so you never kill it by hand.
if pkill -f "run_sentiment_engine.py --loop" 2>/dev/null; then
    echo "    (reaped previous sentiment engine before relaunch)"
fi
nohup python3 scripts/run_sentiment_engine.py --loop 900 --decisions auto \
    >"$SENTIMENT_LOG" 2>&1 &
SENTIMENT_PID=$!
echo "    Sentiment engine PID: $SENTIMENT_PID   (log: $SENTIMENT_LOG)"
if [ -z "${FRED_API_KEY:-}" ]; then
    echo "    [note] FRED_API_KEY not set — fundamental bias will show MISSING."
    echo "           Copy config/sentiment.env.example → config/sentiment.env to enable it."
fi
if python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "  ➜ Launching market sentiment pop-up..."
    if pkill -f "sentiment_monitor.py" 2>/dev/null; then
        echo "    (reaped previous sentiment pop-up)"
    fi
    nohup python3 scripts/sentiment_monitor.py --refresh 2000 \
        >"$SENTIMENT_MON_LOG" 2>&1 &
    SENTIMENT_MON_PID=$!
    echo "    Sentiment monitor PID: $SENTIMENT_MON_PID   (log: $SENTIMENT_MON_LOG)"
fi

# ── Telegram bot scheduler (optional — needs trading_bot/.env) ──
TG_LOG="logs/telegram_bot.log"
if [ -f "trading_bot/.env" ]; then
    # Orphaned schedulers freeze their CONFIG (incl. the namespaced
    # live_monitor_state path) at process start, so a 4-day-old scheduler will
    # silently poll a stale state file after ACTIVE_CONFIG changes. Reap any
    # existing instance before starting the new one.
    if pkill -f "python.* -m trading_bot.scheduler" 2>/dev/null; then
        echo "  ➜ Reaping stale Telegram bot scheduler(s)..."
        sleep 1
    fi
    echo "  ➜ Launching Telegram bot scheduler..."
    nohup python3 -m trading_bot.scheduler >>"$TG_LOG" 2>&1 &
    TG_PID=$!
    echo "    Telegram bot PID: $TG_PID   (log: $TG_LOG)"
else
    echo "  ⚠ trading_bot/.env not found — Telegram bot scheduler not started."
    echo "    See trading_bot/README.md §1 to enable it."
fi

# Make sure background helpers die when the main bot exits.
trap 'kill ${MONITOR_PID:-} ${TG_PID:-} ${SENTIMENT_PID:-} ${SENTIMENT_MON_PID:-} 2>/dev/null || true' EXIT INT TERM

if [ "$FORCE" = true ]; then
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG" --force-live
else
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG"
fi
