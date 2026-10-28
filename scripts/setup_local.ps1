# First-time setup: creates .venv and installs everything. Safe to re-run. Takes a few minutes the first time.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet torch --index-url https://download.pytorch.org/whl/cu124
.venv\Scripts\python.exe -m pip install --quiet -e ".[ml,web,dev]"
if (-not (Test-Path .env)) { Copy-Item env.example .env }
Write-Host "Setup done. Run the task 'Start website (local)' or double-click start.bat."
