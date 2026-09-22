$ErrorActionPreference = "Stop"
$CoreDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $CoreDir "..")).Path

# First run only: write domovoi/.env from .env.example with a random
# Postgres password. An existing .env is never touched (exit 0 either way).
Push-Location $RepoRoot
try {
    python -m domovoi.env_bootstrap
} finally {
    Pop-Location
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
