#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$CORE_DIR/.." && pwd)"

(cd "$CORE_DIR" && docker compose up -d postgres)
(cd "$CORE_DIR" && docker compose run --rm flyway)

cd "$REPO_ROOT"
exec python -m domovoi.main
