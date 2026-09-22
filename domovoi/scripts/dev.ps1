$ErrorActionPreference = "Stop"
$CoreDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $CoreDir "..")).Path

# A FRESH install gets its .env from the example, which starts with strict
# satellite pairing on (CORE-9). An existing .env is NEVER touched: the
# posture of a running household is its own, and a script that rewrote it
# could refuse the satellites already in the house on the next restart.
$EnvFile = Join-Path $CoreDir ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $CoreDir ".env.example") $EnvFile
    Write-Host "created $EnvFile from .env.example"
    Write-Host "  satellite pairing is STRICT: a new satellite parks for approval"
    Write-Host "  on the dashboard. Set SATELLITE_PAIRING_STRICT=false for the older"
    Write-Host "  trust-on-first-use behaviour."
}

Push-Location $CoreDir
try {
    docker compose up -d postgres
    docker compose run --rm flyway
} finally {
    Pop-Location
}

Set-Location $RepoRoot
python -m domovoi.main
