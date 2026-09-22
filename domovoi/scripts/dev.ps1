$ErrorActionPreference = "Stop"
$CoreDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $CoreDir "..")).Path

# First run only: write domovoi/.env from .env.example with a random
# Postgres password, and with the example's STRICT satellite pairing
# (CORE-9) — a fresh household has nothing paired, so it starts closed.
# An existing .env is NEVER touched (exit 0 either way): that file is the
# household's posture, including the satellites it has already let in.
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
