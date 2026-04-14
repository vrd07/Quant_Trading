@echo off
:: ============================================================
:: Quant Trading Bot — One-Click Windows Setup
:: Double-click this file. It will:
::   1) Find Python (python or py launcher)
::   2) Create the virtual environment (venv\)
::   3) Install all required packages
::   4) Create a "Quant Trading Bot" shortcut on your Desktop
:: ============================================================

setlocal EnableDelayedExpansion
title Quant Trading Bot - Setup

:: Jump to project root (this script lives in scripts\)
cd /d "%~dp0.."
set "PROJECT_ROOT=%CD%"

echo.
echo ============================================================
echo   Quant Trading Bot — Windows Setup
echo   Folder: %PROJECT_ROOT%
echo ============================================================
echo.

:: ── Step 1: Find Python ──────────────────────────────────────
set "PYCMD="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYCMD=python"
) else (
    where py >nul 2>nul
    if !errorlevel!==0 (
        set "PYCMD=py -3"
    )
)

if "%PYCMD%"=="" (
    echo [ERROR] Python is not installed ^(or not on PATH^).
    echo.
    echo   1^) Download Python 3.11 from https://www.python.org/downloads/
    echo   2^) Run the installer and TICK "Add Python to PATH" on the first screen
    echo   3^) Run this setup.bat again
    echo.
    pause
    exit /b 1
)

echo [1/4] Found Python: %PYCMD%
%PYCMD% --version
echo.

:: ── Step 2: Create venv ──────────────────────────────────────
if exist "venv\Scripts\python.exe" (
    echo [2/4] Virtual environment already exists, skipping.
) else (
    echo [2/4] Creating virtual environment...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. Check Python installation.
        pause
        exit /b 1
    )
)
echo.

:: ── Step 3: Install requirements ─────────────────────────────
echo [3/4] Installing packages ^(this takes 2-3 minutes^)...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package install failed. Check your internet connection.
    pause
    exit /b 1
)
echo.

:: ── Step 4: Create Desktop shortcut ──────────────────────────
echo [4/4] Creating Desktop shortcut...

set "SHORTCUT=%USERPROFILE%\Desktop\Quant Trading Bot.lnk"
set "TARGET=%PROJECT_ROOT%\scripts\start_live.bat"
set "ICON=%SystemRoot%\System32\shell32.dll"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%SHORTCUT%');" ^
    "$s.TargetPath='%TARGET%';" ^
    "$s.WorkingDirectory='%PROJECT_ROOT%';" ^
    "$s.IconLocation='%ICON%,137';" ^
    "$s.Description='Launch the Quant Trading Bot';" ^
    "$s.Save()"

if exist "%SHORTCUT%" (
    echo   [OK] Shortcut created: %SHORTCUT%
) else (
    echo   [WARN] Could not create Desktop shortcut. You can still run scripts\start_live.bat directly.
)
echo.

echo ============================================================
echo   Setup complete!
echo.
echo   To start the bot: double-click "Quant Trading Bot" on your Desktop
echo   ^(or run scripts\start_live.bat from this folder^)
echo.
echo   BEFORE STARTING: make sure MetaTrader 5 is open and
echo   EA_FileBridge is attached to the XAUUSD chart.
echo ============================================================
echo.
pause
