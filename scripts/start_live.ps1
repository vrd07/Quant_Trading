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

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Select Account Size Config"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  1) `$100"
Write-Host "  2) `$1,000"
Write-Host "  3) `$5,000"
Write-Host "  4) `$10,000"
Write-Host "  5) `$25,000"
Write-Host ""
$choice = Read-Host "Enter choice (1-5) [Default: 3]"

$config_file = "config\config_live_5000.yaml"
switch ($choice) {
    "1" { $config_file = "config\config_live_100.yaml" }
    "2" { $config_file = "config\config_live_1000.yaml" }
    "3" { $config_file = "config\config_live_5000.yaml" }
    "4" { $config_file = "config\config_live_10000.yaml" }
    "5" { $config_file = "config\config_live_25000.yaml" }
}

Write-Host ""
Write-Host "✅ Starting trading bot..." -ForegroundColor Green
Write-Host "   Config  : $config_file" -ForegroundColor Cyan
Write-Host "   Log dir : data\logs\" -ForegroundColor Cyan
Write-Host ""

# Runtime risk setup: lot size, max loss per trade, max daily loss
python scripts\runtime_setup.py --config $config_file
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [WARN] Runtime setup cancelled — using config defaults." -ForegroundColor Yellow
}

# Run the bot
python src\main.py --config $config_file --env live

Write-Host ""
Write-Host "Bot stopped. Press Enter to close..." -ForegroundColor Yellow
Read-Host
