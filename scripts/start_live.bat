@echo off
:: ============================================================
:: Quant Trading Bot — Windows 11 Live Trading Launcher
:: Double-click this file to start the live trading bot.
:: ============================================================

title Quant Trading Bot - LIVE

:: Change to the project root directory (parent of this script)
cd /d "%~dp0.."

echo ============================================================
echo  WARNING: LIVE TRADING MODE - REAL MONEY
echo ============================================================
echo.
set /p CONFIRM="Are you ABSOLUTELY SURE you want to trade live? (type YES): "

if /i not "%CONFIRM%"=="YES" (
    echo Live trading cancelled.
    pause
    exit /b 0
)

echo.
echo Starting trading bot...
echo Config: config\config_live_5000.yaml
echo.

:: Use 'python' (Windows standard) not 'python3'
python src\main.py --config config\config_live_5000.yaml --env live

:: If the bot exits, pause so you can read any error messages
echo.
echo Bot stopped. Press any key to close...
pause > nul
