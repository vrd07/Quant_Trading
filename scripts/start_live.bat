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
:: `for /f "usebackq"` opens the file in text mode, which strips CRLF properly.
:: (set /p leaves the CR behind, and a for /f over a quoted string can't strip it
::  because CR is never a line/token delimiter — bites on every CRLF-saved file.)
set "ACTIVE_CONFIG=config\config_live_10000.yaml"
if exist "config\ACTIVE_CONFIG" (
    for /f "usebackq tokens=* delims=" %%i in ("config\ACTIVE_CONFIG") do set "ACTIVE_CONFIG=%%i"
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

:: Sentiment engine API keys (optional). config\sentiment.env holds FRED_API_KEY
:: and an optional DXY_LEVEL override (see config\sentiment.env.example). Lines are
:: KEY=VALUE; '#' lines are skipped. Absent → fundamental feed stays MISSING.
if exist "config\sentiment.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("config\sentiment.env") do set "%%a=%%b"
)

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
echo   7) $100,000
echo.
set "ACCOUNT_CHOICE="
set /p "ACCOUNT_CHOICE=  Enter choice [0-7] (default: 0): "

if "!ACCOUNT_CHOICE!"=="" set "ACCOUNT_CHOICE=0"
if "!ACCOUNT_CHOICE!"=="0" set "CONFIG=!ACTIVE_CONFIG!"
if "!ACCOUNT_CHOICE!"=="1" set "CONFIG=config\config_live_100.yaml"
if "!ACCOUNT_CHOICE!"=="2" set "CONFIG=config\config_live_1000.yaml"
if "!ACCOUNT_CHOICE!"=="3" set "CONFIG=config\config_live_5000.yaml"
if "!ACCOUNT_CHOICE!"=="4" set "CONFIG=config\config_live_10000.yaml"
if "!ACCOUNT_CHOICE!"=="5" set "CONFIG=config\config_live_25000.yaml"
if "!ACCOUNT_CHOICE!"=="6" set "CONFIG=config\config_live_50000.yaml"
if "!ACCOUNT_CHOICE!"=="7" set "CONFIG=config\config_live_100000.yaml"

if "!CONFIG!"=="" (
    echo   Invalid choice. Using ACTIVE_CONFIG ^(!ACTIVE_CONFIG!^).
    set "CONFIG=!ACTIVE_CONFIG!"
)

:account_selected

:: Persist the chosen config back to ACTIVE_CONFIG so it stays the
:: "last launched" pointer for the next session and any tooling that
:: auto-resolves from it (journal viewer, dashboards, etc.).
if /i not "!CONFIG!"=="!ACTIVE_CONFIG!" (
    > "config\ACTIVE_CONFIG" echo !CONFIG!
    echo   --^> Updated ACTIVE_CONFIG to !CONFIG!
)

:: Derive the per-config state-file stem (matches state_namespacing
:: from 2026-05-14) so the monitor pairs with THIS launch's bot,
:: regardless of any later ACTIVE_CONFIG edits.
for %%F in ("!CONFIG!") do set "CONFIG_STEM=%%~nF"
set "MONITOR_STATE_FILE=data\metrics\live_monitor_state_!CONFIG_STEM!.json"

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

:: ── Runtime Risk Setup (lot size / max loss / daily loss / max positions) ─
if /i "!FORCE!"=="false" (
    python scripts\runtime_setup.py --config "!CONFIG!"
    if errorlevel 1 (
        echo   [WARN] Runtime setup failed or cancelled — using config defaults.
        echo.
    )
)

:: ── Step 1: Health Check ─────────────────────────────────────
echo --- [1/5] Pre-flight Health Check ---
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
echo --- [2/5] Fetching Today's News Events ---
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
echo --- [3/5] Nightly Regime Classifier ---
echo.

python scripts\regime_classifier.py
if !errorlevel! equ 0 (
    echo   [OK] Regime classifier completed
    echo.
) else (
    echo   [WARN] Regime classifier failed — strategies will use default weights
    echo.
)

:: ── Step 4: Regime Health Sanity Check ───────────────────────
echo --- [4/5] Regime Health Sanity Check ---
echo.

python scripts\check_regime_health.py
if !errorlevel! neq 0 (
    echo.
    if /i "!FORCE!"=="false" (
        set /p REGIME_CONTINUE=  Continue with degraded regime ML? [y/N]:
        if /i not "!REGIME_CONTINUE!"=="y" if /i not "!REGIME_CONTINUE!"=="yes" (
            echo   Aborting. Fix the regime CSV ^(see scripts\refresh_historical_data.py^).
            exit /b 1
        )
        echo   Continuing as requested.
    ) else (
        echo   --force flag set, continuing despite degraded regime ML...
    )
    echo.
)

:: ── Step 5: Launch Live Trading ──────────────────────────────
echo --- [5/5] Starting Live Trading ---
echo.

:: Spawn the interactive live-monitor pop-up in a separate window so
:: the user can snap it next to MT5 while the bot streams state to it.
:: Uses pythonw.exe (from venv) for a clean GUI-only window — no console.
if not exist "logs" mkdir "logs"
python -c "import tkinter" >nul 2>&1
if !errorlevel! equ 0 (
    echo   [INFO] Launching live monitor pop-up...
    echo   [INFO] Monitor state: !MONITOR_STATE_FILE!
    if exist "venv\Scripts\pythonw.exe" (
        start "QuantLiveMonitor" "venv\Scripts\pythonw.exe" scripts\live_monitor.py --refresh 1000 --state-file "!MONITOR_STATE_FILE!"
    ) else (
        start "QuantLiveMonitor" python scripts\live_monitor.py --refresh 1000 --state-file "!MONITOR_STATE_FILE!"
    )
) else (
    echo   [WARN] Tkinter not available — skipping live monitor pop-up.
)

:: ── Market Sentiment Engine + pop-up (XAUUSD GSS) ────────────
:: Engine assembles the Gold Sentiment Score on a 15-min loop and writes
:: data\metrics\sentiment_monitor_state.json; the pop-up renders it. Both are
:: DISPLAY-ONLY — they never trade. Technical bias is live from our own 5m data;
:: fundamental (FRED) bias needs FRED_API_KEY (config\sentiment.env.example).
echo   [INFO] Launching market sentiment engine (XAUUSD GSS, 15-min loop,
echo          intraday AI decisions on opportunity — advisory, never auto-executes)...
start "QuantSentimentEngine" /min cmd /c "python scripts\run_sentiment_engine.py --loop 900 --decisions auto >> logs\sentiment_engine.log 2>&1"
if "!FRED_API_KEY!"=="" echo   [note] FRED_API_KEY not set — fundamental bias shows MISSING. See config\sentiment.env.example.
python -c "import tkinter" >nul 2>&1
if !errorlevel! equ 0 (
    echo   [INFO] Launching market sentiment pop-up...
    if exist "venv\Scripts\pythonw.exe" (
        start "QuantSentimentMonitor" "venv\Scripts\pythonw.exe" scripts\sentiment_monitor.py --refresh 2000
    ) else (
        start "QuantSentimentMonitor" python scripts\sentiment_monitor.py --refresh 2000
    )
)

:: ── Telegram bot scheduler (optional — needs trading_bot\.env) ──
if exist "trading_bot\.env" (
    REM Reap any prior scheduler so it can't keep polling the old namespaced
    REM state file. CONFIG is frozen at process start.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and $_.CommandLine -like '*trading_bot.scheduler*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
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
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and ($_.CommandLine -like '*live_monitor.py*' -or $_.CommandLine -like '*trading_bot.scheduler*' -or $_.CommandLine -like '*run_sentiment_engine.py*' -or $_.CommandLine -like '*sentiment_monitor.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1

:: If the bot exits, pause so you can read any error messages
echo.
echo Bot stopped. Press any key to close...
pause > nul

endlocal
