#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$CORE_DIR/.." && pwd)"

# First run only: write domovoi/.env from .env.example with a random
# Postgres password. An existing .env is never touched (exit 0 either way).
(cd "$REPO_ROOT" && python -m domovoi.env_bootstrap)
(cd "$CORE_DIR" && docker compose up -d postgres)
(cd "$CORE_DIR" && docker compose run --rm flyway)

cd "$REPO_ROOT"
exec python -m domovoi.main
