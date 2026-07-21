$ErrorActionPreference = "Stop"
$CoreDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $CoreDir "..")).Path

Push-Location $CoreDir
try {
    docker compose up -d postgres
    docker compose run --rm flyway
} finally {
    Pop-Location
}

Set-Location $RepoRoot
python -m domovoi.main
