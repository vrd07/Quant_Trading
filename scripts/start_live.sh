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
#   ./scripts/start_live.sh --gui        # native macOS dialogs for all inputs
#                                        # (what scripts/QuantBot.command uses)
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
GUI=false

# Parse args
for arg in "$@"; do
    case $arg in
        --force) FORCE=true ;;
        --gui)   GUI=true ;;
    esac
done

# ── Native-dialog helpers (--gui mode, macOS osascript) ──────
# Fall back to the terminal prompts automatically when osascript
# is unavailable (Linux, SSH session without a GUI, etc.).
have_dialogs() { [ "$GUI" = true ] && command -v osascript >/dev/null 2>&1; }

# dlg_yn "message" "Yes|No default button" → rc 0 if user clicked Yes.
# \n inside the message renders as a line break in the dialog.
dlg_yn() {
    local out
    out=$(osascript -e "display dialog \"$1\" with title \"Quant Trading Bot\" buttons {\"No\", \"Yes\"} default button \"$2\"" 2>/dev/null) || return 1
    [[ "$out" == *"button returned:Yes"* ]]
}

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
elif have_dialogs; then
    ACTIVE_STEM="$(basename "$ACTIVE_CONFIG" .yaml)"
    CHOICE=$(osascript <<OSA 2>/dev/null || true
set opts to {"Use ACTIVE_CONFIG (${ACTIVE_STEM})", "\$100", "\$1,000", "\$5,000", "\$10,000", "\$25,000", "\$50,000", "\$100,000"}
set pick to choose from list opts with title "Quant Trading Bot" with prompt "Select account size:" default items {item 1 of opts}
if pick is false then return "CANCEL"
return item 1 of pick
OSA
)
    case "$CHOICE" in
        "CANCEL"|"")  echo "  Launch cancelled from account picker."; exit 0 ;;
        '$100')       CONFIG="config/config_live_100.yaml" ;;
        '$1,000')     CONFIG="config/config_live_1000.yaml" ;;
        '$5,000')     CONFIG="config/config_live_5000.yaml" ;;
        '$10,000')    CONFIG="config/config_live_10000.yaml" ;;
        '$25,000')    CONFIG="config/config_live_25000.yaml" ;;
        '$50,000')    CONFIG="config/config_live_50000.yaml" ;;
        '$100,000')   CONFIG="config/config_live_100000.yaml" ;;
        *)            CONFIG="$ACTIVE_CONFIG" ;;
    esac
    echo "  Account: ${CHOICE} → ${CONFIG}"
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

# ── Enabled symbols + EA-streaming warnings ──────────────────
# Single source of truth = the bot's startup symbol reconciler, so this shows
# the symbols the bot will ACTUALLY trade (a symbol-gated strategy auto-enables
# its symbols when the strategy is on) and which non-chart symbols the EA must
# carry in WatchSymbols — including a day-aware nudge for monday_drift /
# index_overnight / wednesday_drift on the days they fire. Auto-covers any
# future strategy without editing this launcher.
python3 - "$CONFIG" <<'PY' || echo "  (symbol-reconciler check skipped)"
import sys, yaml
from datetime import datetime, timezone
sys.path.insert(0, '.')
try:
    from src.strategies.symbol_reconciler import (
        reconcile_enabled_symbols, streaming_warning, streaming_reminder,
    )
    cfg = yaml.safe_load(open(sys.argv[1]))
    auto, missing = reconcile_enabled_symbols(cfg)
    enabled = [s for s, c in (cfg.get('symbols') or {}).items() if (c or {}).get('enabled')]
    print("  Symbols the bot will trade: " + (' '.join(enabled) or 'none'))
    if auto:
        print("  (auto-enabled for active strategies: " + ', '.join(auto) + ")")
    if missing:
        print("  ⚠ strategies need symbols with NO config block: " + ', '.join(missing))
    for line in streaming_warning(cfg):
        print("  " + line)
    rem = streaming_reminder(cfg, datetime.now(timezone.utc).weekday())
    if rem:
        print("")
        for line in rem:
            print("  " + line)
except Exception as e:
    print("  (symbol-reconciler check error: %s)" % e)
PY
echo ""

# ── Runtime Risk Setup ───────────────────────────────────────
if [ "$FORCE" = false ]; then
    if have_dialogs; then
        # Everyday shortcut: reuse last session's runtime_overrides.yaml
        # instead of walking the ~10 dialogs again (2-click relaunch).
        USE_LAST=false
        if [ -f "config/runtime_overrides.yaml" ]; then
            LAST_SUMMARY=$(python3 - <<'PY' 2>/dev/null || true
import yaml
try:
    o = yaml.safe_load(open("config/runtime_overrides.yaml")) or {}
    r = o.get("risk") or {}
    syms = [s for s, c in (o.get("symbols") or {}).items() if (c or {}).get("enabled")]
    parts = [
        "Symbols: " + (", ".join(syms) or "?"),
        "Max loss/trade: $%s   RR 1:%s" % (r.get("risk_per_trade_usd", "?"), r.get("reward_risk_ratio", "?")),
        "Daily loss: $%s   Max positions: %s" % (r.get("absolute_max_loss_usd", "?"), r.get("max_positions", "?")),
    ]
    print("\\n".join(parts), end="")  # literal \n — AppleScript line breaks
except Exception:
    pass
PY
)
            if dlg_yn "Use last session's settings?\n\n${LAST_SUMMARY}\n\n(No = change them in the setup dialogs)" "Yes"; then
                USE_LAST=true
                echo "  ➜ Using last session's runtime overrides."
            fi
        fi
        if [ "$USE_LAST" = false ]; then
            python3 scripts/runtime_setup.py --config "$CONFIG" --ui dialogs || \
                echo "  [WARN] Runtime setup cancelled — using config defaults."
        fi
    else
        python3 scripts/runtime_setup.py --config "$CONFIG" || \
            echo "  [WARN] Runtime setup cancelled — using config defaults."
    fi
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
        if have_dialogs && dlg_yn "Health check FAILED.\n\nFix the issues shown in the Terminal window before trading.\n\nContinue anyway? (NOT recommended)" "No"; then
            echo "  Continuing despite health-check failure (user override)."
            echo ""
        else
            exit 1
        fi
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
        if have_dialogs; then
            if dlg_yn "Regime ML health check is DEGRADED.\n\nStrategies would run on stale/default regime weights.\n\nContinue anyway?" "No"; then
                echo "  Continuing as requested."
            else
                echo "  Aborting. Fix the regime CSV (see scripts/refresh_historical_data.py)."
                exit 1
            fi
        else
            printf "  Continue with degraded regime ML? [y/N]: "
            read -r REGIME_CONTINUE
            case "${REGIME_CONTINUE:-N}" in
                y|Y|yes|YES) echo "  Continuing as requested." ;;
                *) echo "  Aborting. Fix the regime CSV (see scripts/refresh_historical_data.py)."; exit 1 ;;
            esac
        fi
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

# ── Volatility monitor (London/NY-open "Beast mode" scalp alerts) ──
# Alert-only: never trades, never touches the risk engine. Reads the EA status
# file passively (no bridge commands), so it cannot race the bot's 250ms loop.
# Headless here — alerts arrive as macOS notifications + Telegram (if
# configured) and are logged to data/volatility_alerts.jsonl. For the live
# table, run scripts/volatility_monitor.py in a terminal instead.
VOLMON_LOG="logs/volatility_monitor.log"
echo "  ➜ Launching volatility monitor (Beast-mode session-open alerts, headless)..."
if pkill -f "volatility_monitor.py" 2>/dev/null; then
    echo "    (reaped previous volatility monitor)"
fi
nohup python3 scripts/volatility_monitor.py --headless --telegram \
    --config "$CONFIG" \
    >"$VOLMON_LOG" 2>&1 &
VOLMON_PID=$!
echo "    Volatility monitor PID: $VOLMON_PID   (log: $VOLMON_LOG)"

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
trap 'kill ${MONITOR_PID:-} ${TG_PID:-} ${SENTIMENT_PID:-} ${SENTIMENT_MON_PID:-} ${VOLMON_PID:-} 2>/dev/null || true' EXIT INT TERM

if [ "$FORCE" = true ]; then
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG" --force-live
else
    exec caffeinate -ims python3 src/main.py --env live --config "$CONFIG"
fi
