@echo off
:: ============================================================
:: Quant Trading Bot — Live Account Launcher
:: Active account size is resolved from config\ACTIVE_CONFIG.
:: Runs: health check → news fetch → regime classifier → live trading
::
:: Usage:
::   Double-click this file, or run from command prompt:
::   scripts\start_live.bat              (interactive, default)
::   scripts\start_live.bat --force      (skip confirmations, for scheduled tasks)
:: ============================================================

setlocal EnableDelayedExpansion

title Quant Trading Bot - LIVE

:: Change to the project root directory (parent of this script)
cd /d "%~dp0.."

:: Single source of truth for the active live config.
set "ACTIVE_CONFIG=config\config_live_10000.yaml"
if exist "config\ACTIVE_CONFIG" (
    set /p "ACTIVE_CONFIG="<"config\ACTIVE_CONFIG"
    :: Strip any trailing CR / whitespace that crept in from the file.
    for /f "tokens=* delims= " %%i in ("!ACTIVE_CONFIG!") do set "ACTIVE_CONFIG=%%i"
)

set "CONFIG="
set "FORCE=false"

:: Parse args
for %%a in (%*) do (
    if /i "%%a"=="--force" set "FORCE=true"
)

:: Activate venv (auto-run setup if missing)
if not exist "venv\Scripts\activate.bat" (
    echo.
    echo Virtual environment not found — running first-time setup...
    echo.
    call "%~dp0setup.bat"
    if not exist "venv\Scripts\activate.bat" (
        echo ERROR: Setup did not complete. Please run scripts\setup.bat manually.
        pause
        exit /b 1
    )
)
call "venv\Scripts\activate.bat"

:: ── Account Selection ───────────────────────────────────────
if /i "!FORCE!"=="true" (
    set "CONFIG=!ACTIVE_CONFIG!"
    goto :account_selected
)

echo.
echo ============================================================
echo   Select Account Size  ^(ACTIVE_CONFIG: !ACTIVE_CONFIG!^)
echo ============================================================
echo.
echo   0) Use ACTIVE_CONFIG  ^<-- default
echo   1) $100
echo   2) $1,000
echo   3) $5,000
echo   4) $10,000
echo   5) $25,000
echo   6) $50,000
echo.
set "ACCOUNT_CHOICE="
set /p "ACCOUNT_CHOICE=  Enter choice [0-6] (default: 0): "

if "!ACCOUNT_CHOICE!"=="" set "ACCOUNT_CHOICE=0"
if "!ACCOUNT_CHOICE!"=="0" set "CONFIG=!ACTIVE_CONFIG!"
if "!ACCOUNT_CHOICE!"=="1" set "CONFIG=config\config_live_100.yaml"
if "!ACCOUNT_CHOICE!"=="2" set "CONFIG=config\config_live_1000.yaml"
if "!ACCOUNT_CHOICE!"=="3" set "CONFIG=config\config_live_5000.yaml"
if "!ACCOUNT_CHOICE!"=="4" set "CONFIG=config\config_live_10000.yaml"
if "!ACCOUNT_CHOICE!"=="5" set "CONFIG=config\config_live_25000.yaml"
if "!ACCOUNT_CHOICE!"=="6" set "CONFIG=config\config_live_50000.yaml"

if "!CONFIG!"=="" (
    echo   Invalid choice. Using ACTIVE_CONFIG ^(!ACTIVE_CONFIG!^).
    set "CONFIG=!ACTIVE_CONFIG!"
)

:account_selected

if not exist "!CONFIG!" (
    echo.
    echo ERROR: Config file not found: !CONFIG!
    echo        Check config\ACTIVE_CONFIG or pick a valid account size.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Quant Trading Bot — GFT Account
echo   Config: !CONFIG!
echo   Time:   %date% %time:~0,5%
echo ============================================================
echo.

:: ── Runtime Risk Setup (lot size / max loss per trade / daily loss) ─
if /i "!FORCE!"=="false" (
    python scripts\runtime_setup.py --config "!CONFIG!"
    if errorlevel 1 (
        echo   [WARN] Runtime setup failed or cancelled — using config defaults.
        echo.
    )
)

:: ── Step 1: Health Check ─────────────────────────────────────
echo --- [1/4] Pre-flight Health Check ---
echo.

python scripts\health_check.py --config "!CONFIG!"
set "HEALTH_RC=!errorlevel!"
if "!HEALTH_RC!"=="0" (
    echo.
    echo   [OK] Health check PASSED
    echo.
) else (
    echo.
    echo   [FAIL] Health check FAILED — fix issues above before trading
    echo.
    if /i "!FORCE!"=="false" (
        pause
        exit /b 1
    ) else (
        echo   --force flag set, continuing despite health check failure...
        echo.
    )
)

:: ── Step 2: Fetch Daily News ─────────────────────────────────
echo --- [2/4] Fetching Today's News Events ---
echo.

python scripts\fetch_daily_news.py
if !errorlevel! equ 0 (
    echo   [OK] News events fetched and configs updated
    echo.
) else (
    echo   [WARN] News fetch failed — news filter will use fallback CSV
    echo.
)

:: ── Step 3: Regime Classifier ────────────────────────────────
echo --- [3/4] Nightly Regime Classifier ---
echo.

python scripts\regime_classifier.py
if !errorlevel! equ 0 (
    echo   [OK] Regime classifier completed
    echo.
) else (
    echo   [WARN] Regime classifier failed — strategies will use default weights
    echo.
)

:: ── Step 4: Launch Live Trading ──────────────────────────────
echo --- [4/4] Starting Live Trading ---
echo.

:: Spawn the interactive live-monitor pop-up in a separate window so
:: the user can snap it next to MT5 while the bot streams state to it.
:: Uses pythonw.exe (from venv) for a clean GUI-only window — no console.
if not exist "logs" mkdir "logs"
python -c "import tkinter" >nul 2>&1
if !errorlevel! equ 0 (
    echo   [INFO] Launching live monitor pop-up...
    if exist "venv\Scripts\pythonw.exe" (
        start "QuantLiveMonitor" "venv\Scripts\pythonw.exe" scripts\live_monitor.py --refresh 1000
    ) else (
        start "QuantLiveMonitor" python scripts\live_monitor.py --refresh 1000
    )
) else (
    echo   [WARN] Tkinter not available — skipping live monitor pop-up.
)

:: ── Telegram bot scheduler (optional — needs trading_bot\.env) ──
if exist "trading_bot\.env" (
    echo   [INFO] Launching Telegram bot scheduler...
    start "QuantTelegramBot" /min cmd /c "python -m trading_bot.scheduler >> logs\telegram_bot.log 2>&1"
) else (
    echo   [WARN] trading_bot\.env not found — Telegram bot scheduler not started.
    echo          See trading_bot\README.md section 1 to enable it.
)

if /i "!FORCE!"=="true" (
    python src\main.py --env live --config "!CONFIG!" --force-live
) else (
    python src\main.py --env live --config "!CONFIG!"
)

:: ── Cleanup: stop spawned helpers when the bot exits ─────────
:: wmic is deprecated/removed on modern Windows (11 24H2+), so use
:: PowerShell + CIM to match by command line. Errors silenced — these
:: are best-effort cleanups, not gates on the script returning.
echo.
echo   [INFO] Cleaning up live monitor and Telegram scheduler processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*live_monitor.py*' -or $_.CommandLine -like '*trading_bot.scheduler*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

:: If the bot exits, pause so you can read any error messages
echo.
echo Bot stopped. Press any key to close...
pause > nul

endlocal
