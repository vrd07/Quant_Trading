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

set CONFIG=
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

:: ── Account Selection ───────────────────────────────────────
if "%FORCE%"=="true" (
    set CONFIG=config\config_live_50000.yaml
    goto :account_selected
)

echo.
echo ============================================================
echo   Select Account Size
echo ============================================================
echo.
echo   1) $100
echo   2) $1,000
echo   3) $5,000
echo   4) $10,000
echo   5) $25,000
echo   6) $50,000
echo.
set /p ACCOUNT_CHOICE="  Enter choice [1-6] (default: 6): "

if "%ACCOUNT_CHOICE%"=="" set ACCOUNT_CHOICE=6
if "%ACCOUNT_CHOICE%"=="1" set CONFIG=config\config_live_100.yaml
if "%ACCOUNT_CHOICE%"=="2" set CONFIG=config\config_live_1000.yaml
if "%ACCOUNT_CHOICE%"=="3" set CONFIG=config\config_live_5000.yaml
if "%ACCOUNT_CHOICE%"=="4" set CONFIG=config\config_live_10000.yaml
if "%ACCOUNT_CHOICE%"=="5" set CONFIG=config\config_live_25000.yaml
if "%ACCOUNT_CHOICE%"=="6" set CONFIG=config\config_live_50000.yaml

if "%CONFIG%"=="" (
    echo   Invalid choice. Using default $50,000 account.
    set CONFIG=config\config_live_50000.yaml
)

:account_selected

echo.
echo ============================================================
echo   Quant Trading Bot — GFT Account
echo   Config: %CONFIG%
echo   Time:   %date% %time:~0,5% UTC
echo ============================================================
echo.

:: ── Runtime Risk Setup (lot size / max loss per trade / daily loss) ─
if "%FORCE%"=="false" (
    python scripts\runtime_setup.py --config %CONFIG%
    if errorlevel 1 (
        echo   [WARN] Runtime setup failed or cancelled — using config defaults.
        echo.
    )
)

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
