#!/bin/bash
# Daily gold-sentiment Instagram content: refresh the snapshot, then build the
# caption + Reel script + branded card. Run by com.quanttrading.sentiment-content
# (launchd) each morning, or by hand:  bash scripts/run_daily_content.sh
set -uo pipefail

REPO="/Users/varadbandekar/Documents/Quant_trading"
PY="$REPO/venv/bin/python"
cd "$REPO" || exit 1

# Load API keys (FRED / Alpha Vantage / Myfxbook) and CONTENT_HANDLE if present.
if [ -f config/sentiment.env ]; then set -a; . config/sentiment.env; set +a; fi

mkdir -p "$REPO/data/sentiment/content"
LOG="$REPO/data/sentiment/content/daily.log"
{
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') daily content run ==="
    # 1) Refresh the sentiment snapshot — scoring/display only (no AI decision,
    #    no Telegram, no paper trade). Falls back to the last snapshot on failure.
    "$PY" scripts/run_sentiment_engine.py --decisions off --notify off --paper off \
        || echo "[warn] engine refresh failed — generating from the last snapshot"
    # 2) Build caption + Reel script + PNG card from the snapshot.
    "$PY" scripts/sentiment_content.py --handle "${CONTENT_HANDLE:-@yourhandle}"
    echo "--- done; files in data/sentiment/content/ ---"
} >> "$LOG" 2>&1
