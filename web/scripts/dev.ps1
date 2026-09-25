# Run the web backend locally on Windows. Mirrors the core service's
# domovoi/scripts/dev.ps1 pattern.
#
# Usage:
#   .\web\scripts\dev.ps1            # default 0.0.0.0:6369
#   $env:WEB_PORT=9000; .\web\scripts\dev.ps1
#
# The core service's Postgres must be running (docker compose up -d
# postgres flyway from the domovoi/ directory). If it's not,
# the web backend boots fine but /api/health returns degraded.

$ErrorActionPreference = "Stop"

# Run from the repo root so `python -m web.backend.main` finds the
# package. The script lives at web/scripts/, so go up two levels.
Set-Location (Join-Path $PSScriptRoot "..\..")

# Windows PowerShell 5.1 has no `??`, so spell the fallbacks out; they
# match web/backend/main.py's own defaults.
$WebHost = if ($env:WEB_HOST) { $env:WEB_HOST } else { "0.0.0.0" }
$WebPort = if ($env:WEB_PORT) { $env:WEB_PORT } else { "6369" }
Write-Host "Starting Domovoi Web on ${WebHost}:${WebPort}"
python -m web.backend.main
