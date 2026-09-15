$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not $env:APP_HOST) { $env:APP_HOST = "0.0.0.0" }
if (-not $env:APP_PORT) { $env:APP_PORT = "5001" }

if (-not $env:APP_DATA_DIR) {
    $env:APP_DATA_DIR = Join-Path $ProjectRoot "instance"
}

New-Item -ItemType Directory -Force -Path $env:APP_DATA_DIR | Out-Null

Write-Host "Starting Workbook Compare"
Write-Host "APP_HOST=$($env:APP_HOST)"
Write-Host "APP_PORT=$($env:APP_PORT)"
Write-Host "APP_DATA_DIR=$($env:APP_DATA_DIR)"

python app.py
