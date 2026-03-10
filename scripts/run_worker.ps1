# Start the Wi-Fi Worker Agent (FastAPI)
# Usage: .\scripts\run_worker.ps1 [-Port 8080]

param(
    [int]$Port = 8080,
    [string]$Host = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Push-Location $ProjectRoot
try {
    if (Test-Path ".venv\Scripts\Activate.ps1") {
        . ".venv\Scripts\Activate.ps1"
    }
    Write-Host "Starting Wi-Fi Worker on ${Host}:${Port} ..."
    uvicorn worker.app:app --host $Host --port $Port
}
finally {
    Pop-Location
}
