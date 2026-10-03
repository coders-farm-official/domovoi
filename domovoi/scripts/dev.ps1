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

# Web answers (the weather, "check that online", news topics) go through the
# local search helper, SearXNG. Start it when this box is answered Yes or
# Sometimes (INTERNET_ACCESS; docs/INTERNET.md); unanswered or No leaves it
# as it is. A failure is only a warning. DOMOVOI_MANAGE_SEARXNG=0 skips this.
$ManageSearxng = ("$env:DOMOVOI_MANAGE_SEARXNG").Trim().ToLower()
if (@("0", "false", "no", "off") -notcontains $ManageSearxng) {
    $InternetPolicy = ""
    Push-Location $RepoRoot
    try {
        $InternetPolicy = ((python -m domovoi.egress --print-policy) | Out-String).Trim()
    } catch {
        $InternetPolicy = ""
    } finally {
        Pop-Location
    }
    if (@("always", "sometimes") -contains $InternetPolicy) {
        Push-Location $CoreDir
        try {
            docker compose up -d searxng
            if ($LASTEXITCODE -ne 0) { throw "docker compose exited with $LASTEXITCODE" }
        } catch {
            Write-Warning "could not start the search helper (SearXNG): $_. Web answers stay off until it runs: cd domovoi; docker compose up -d searxng"
        } finally {
            Pop-Location
        }
    }
}

Set-Location $RepoRoot
python -m domovoi.main
