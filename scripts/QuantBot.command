#!/bin/bash
# ============================================================
# Quant Trading Bot — macOS one-click launcher
#
# Double-click this file in Finder (Terminal opens automatically).
#
# First run:  creates the venv, installs all dependencies, and
#             offers to put a shortcut on your Desktop.
# Every run:  native-dialog setup (account size, lot, max loss,
#             R:R, daily loss, …) → health check → news → regime
#             classifier → live bot + monitor pop-ups.
#
# This is the macOS equivalent of scripts\setup.bat +
# scripts\start_live.bat on Windows. All the launch logic lives
# in scripts/start_live.sh (--gui) — this file only bootstraps.
#
# If macOS refuses to open it ("unidentified developer"):
#   right-click the file → Open → Open.
# If Terminal says "permission denied":
#   chmod +x scripts/QuantBot.command
# ============================================================
set -euo pipefail

TITLE="Quant Trading Bot"

dlg_ok() {
    osascript -e "display dialog \"$1\" with title \"$TITLE\" buttons {\"OK\"} default button \"OK\"" >/dev/null 2>&1 || true
}
dlg_yn() {
    osascript -e "display dialog \"$1\" with title \"$TITLE\" buttons {\"No\", \"Yes\"} default button \"Yes\"" 2>/dev/null | grep -q "button returned:Yes"
}

# Resolve symlinks (the Desktop shortcut is one) so the script finds
# the real project root no matter where it was double-clicked from.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# ── First-time setup (macOS equivalent of scripts\setup.bat) ──
if [ ! -x "venv/bin/python3" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        dlg_ok "Python 3 is not installed.\n\n1. Download Python 3.11 from python.org/downloads\n2. Run the installer\n3. Double-click this launcher again."
        exit 1
    fi
    echo ""
    echo "============================================================"
    echo "  First-time setup — creating the virtual environment and"
    echo "  installing dependencies. 2–3 minutes of scrolling text"
    echo "  is completely normal. Do not close this window."
    echo "============================================================"
    echo ""
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
    echo ""
    echo "  ✓ Setup complete."
    echo ""

    # Desktop shortcut, same as setup.bat does on Windows.
    SHORTCUT="$HOME/Desktop/Quant Trading Bot.command"
    if [ ! -e "$SHORTCUT" ] && dlg_yn "Setup complete!\n\nPut a 'Quant Trading Bot' shortcut on your Desktop?"; then
        ln -s "$SCRIPT_DIR/QuantBot.command" "$SHORTCUT"
        echo "  ➜ Desktop shortcut created: $SHORTCUT"
    fi
fi

# Hand off to the single launch pipeline in native-dialog mode.
exec bash scripts/start_live.sh --gui
