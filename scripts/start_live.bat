@echo off
:: ============================================================
:: Quant Trading Bot — $50K GFT Account Launcher
:: Runs: health check → news fetch → regime classifier → live trading
::
:: Usage:
::   Double-click this file, or run from command prompt:
::   scripts\start_live.bat              (interactive, default)
::   scripts\start_live.bat --force      (skip confirmations, for scheduled tasks)
:: ============================================================

title Quant Trading Bot - LIVE

:: Change to the project root directory (parent of this script)
cd /d "%~dp0.."

set CONFIG=config\config_live_50000.yaml
set FORCE=false

:: Parse args
for %%a in (%*) do (
    if /i "%%a"=="--force" set FORCE=true
)

:: Activate venv
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo ERROR: venv not found. Run: python -m venv venv ^&^& pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Quant Trading Bot — GFT $50,000 Account
echo   Config: %CONFIG%
echo   Time:   %date% %time:~0,5% UTC
echo ============================================================
echo.

:: ── Step 1: Health Check ─────────────────────────────────────
echo --- [1/4] Pre-flight Health Check ---
echo.

python scripts\health_check.py --config %CONFIG%
if %errorlevel%==0 (
    echo.
    echo   [OK] Health check PASSED
    echo.
) else (
    echo.
    echo   [FAIL] Health check FAILED — fix issues above before trading
    echo.
    if "%FORCE%"=="false" (
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
if %errorlevel%==0 (
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
if %errorlevel%==0 (
    echo   [OK] Regime classifier completed
    echo.
) else (
    echo   [WARN] Regime classifier failed — strategies will use default weights
    echo.
)

:: ── Step 4: Launch Live Trading ──────────────────────────────
echo --- [4/4] Starting Live Trading ---
echo.

if "%FORCE%"=="true" (
    python src\main.py --env live --config %CONFIG% --force-live
) else (
    python src\main.py --env live --config %CONFIG%
)

:: If the bot exits, pause so you can read any error messages
echo.
echo Bot stopped. Press any key to close...
pause > nul
