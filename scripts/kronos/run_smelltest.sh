#!/bin/bash
# Detached end-to-end Kronos IC smell-test: forecasts -> analysis -> reports -> desktop
# notification. Survives the launching terminal closing (run under nohup).
#
#   nohup bash scripts/kronos/run_smelltest.sh > /tmp/kronos_smelltest.log 2>&1 &
#
# Idempotent: a forecast whose .npz cache already exists is skipped, so re-running after an
# interruption resumes at instrument granularity rather than redoing finished work.
#
# Run config is the ctx-256/paths-5 operating point (see the SDD ledger): spec fidelity
# (ctx 512 / paths 15) costs ~10 s/window = ~15h for both instruments on this M1.
# --max-windows 0 means NO CAP: a cap slices idxs[:N] and would silently drop the most
# recent year, which is the slice the contamination guard depends on.

REPO="/Users/varadbandekar/Documents/Quant_trading"
cd "$REPO" || exit 1

PY_TORCH="$REPO/venv_kronos/bin/python"
PY_PROD="$REPO/venv/bin/python"
FORECAST="$REPO/scripts/kronos/kronos_forecast.py"
ANALYSIS="$REPO/scripts/kronos/kronos_ic.py"

notify() {  # $1 = title, $2 = message
    /usr/bin/osascript -e "display notification \"$2\" with title \"$1\" sound name \"Glass\"" 2>/dev/null
}

forecast() {  # $1 = symbol, $2 = stride
    local sym="$1" stride="$2" cache="data/kronos_cache_$1.npz"
    if [ -f "$cache" ]; then
        echo "=== $sym: cache exists, skipping forecast ($(date)) ==="
        return 0
    fi
    echo "=== $sym forecast start $(date) ==="
    "$PY_TORCH" "$FORECAST" --symbol "$sym" --ctx 256 --n-paths 5 \
        --stride "$stride" --max-windows 0 --horizon 4 --out "$cache"
    local rc=$?
    echo "=== $sym forecast end $(date) rc=$rc ==="
    return $rc
}

verdict_of() {  # $1 = symbol -> echoes GREEN / RED / ERROR
    local sym="$1" cache="data/kronos_cache_$1.npz"
    local report="reports/kronos_ic_smelltest_$1.md"
    [ -f "$cache" ] || { echo "ERROR"; return; }
    "$PY_PROD" "$ANALYSIS" --cache "$cache" --report-out "$report" >/dev/null 2>&1
    if [ -f "$report" ]; then
        grep -oE 'Verdict: (GREEN|RED)' "$report" | head -1 | awk '{print $2}'
    else
        echo "ERROR"
    fi
}

echo "### Kronos smell-test run started $(date) ###"
notify "Kronos smell-test" "Forecast pass started — ~3.5h for XAUUSD + BTCUSD"

forecast XAUUSD 8
forecast BTCUSD 16

echo "### Analysis $(date) ###"
V_XAU=$(verdict_of XAUUSD)
V_BTC=$(verdict_of BTCUSD)

echo "### VERDICTS: XAUUSD=$V_XAU BTCUSD=$V_BTC ($(date)) ###"
notify "Kronos verdicts in" "XAUUSD: $V_XAU | BTCUSD: $V_BTC — reports in reports/kronos_ic_smelltest_*.md"

# Breadcrumb so a later session (or a fresh Claude) can pick this up without re-deriving it.
{
    echo "run_finished: $(date)"
    echo "XAUUSD: $V_XAU"
    echo "BTCUSD: $V_BTC"
    echo "config: ctx 256, n_paths 5, stride XAU 8 / BTC 16, horizon 4, max_windows 0"
} > "$REPO/.superpowers/sdd/2026-07-25-kronos-ic-smelltest/VERDICTS.txt"
echo "### done $(date) ###"
