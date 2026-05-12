# ============================================================
# Quant Trading Bot — Windows 11 PowerShell Launcher
# Run: Right-click → "Run with PowerShell"
# Or from terminal: powershell -ExecutionPolicy Bypass -File start_live.ps1
# ============================================================

$Host.UI.RawUI.WindowTitle = "Quant Trading Bot - LIVE"

# Change to project root (parent of scripts/)
Set-Location (Split-Path $PSScriptRoot -Parent)

# Single source of truth for the active live config.
$active_config = "config\config_live_10000.yaml"
if (Test-Path "config\ACTIVE_CONFIG") {
    $raw = (Get-Content "config\ACTIVE_CONFIG" -TotalCount 1).Trim()
    if ($raw) { $active_config = $raw }
}

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
Write-Host "  Select Account Size Config  (ACTIVE_CONFIG: $active_config)"
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  0) Use ACTIVE_CONFIG  <-- default"
Write-Host "  1) `$100"
Write-Host "  2) `$1,000"
Write-Host "  3) `$5,000"
Write-Host "  4) `$10,000"
Write-Host "  5) `$25,000"
Write-Host "  6) `$50,000"
Write-Host ""
$choice = Read-Host "Enter choice (0-6) [Default: 0]"

$config_file = $active_config
switch ($choice) {
    "0" { $config_file = $active_config }
    "1" { $config_file = "config\config_live_100.yaml" }
    "2" { $config_file = "config\config_live_1000.yaml" }
    "3" { $config_file = "config\config_live_5000.yaml" }
    "4" { $config_file = "config\config_live_10000.yaml" }
    "5" { $config_file = "config\config_live_25000.yaml" }
    "6" { $config_file = "config\config_live_50000.yaml" }
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
