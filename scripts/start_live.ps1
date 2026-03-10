# ============================================================
# Quant Trading Bot — Windows 11 PowerShell Launcher
# Run: Right-click → "Run with PowerShell"
# Or from terminal: powershell -ExecutionPolicy Bypass -File start_live.ps1
# ============================================================

$Host.UI.RawUI.WindowTitle = "Quant Trading Bot - LIVE"

# Change to project root (parent of scripts/)
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "============================================================" -ForegroundColor Red
Write-Host "  ⚠️  LIVE TRADING MODE — REAL MONEY ⚠️" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Red
Write-Host ""

$confirm = Read-Host "Are you ABSOLUTELY SURE you want to trade live? (type YES)"

if ($confirm -ne "YES") {
    Write-Host "Live trading cancelled." -ForegroundColor Green
    Read-Host "Press Enter to exit"
    exit 0
}

Write-Host ""
Write-Host "✅ Starting trading bot..." -ForegroundColor Green
Write-Host "   Config  : config\config_live_5000.yaml" -ForegroundColor Cyan
Write-Host "   Log dir : data\logs\" -ForegroundColor Cyan
Write-Host ""

# Run the bot
python src\main.py --config config\config_live_5000.yaml --env live

Write-Host ""
Write-Host "Bot stopped. Press Enter to close..." -ForegroundColor Yellow
Read-Host
