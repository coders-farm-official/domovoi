#!/usr/bin/env bash
# Run the web backend locally. Bash twin of web/scripts/dev.ps1.
#
# Usage:
#   ./web/scripts/dev.sh             # default 0.0.0.0:6369
#   WEB_PORT=9000 ./web/scripts/dev.sh
#
# The core service's Postgres must be running (docker compose up -d
# postgres flyway from the domovoi/ directory). If it's not, the web
# backend boots fine but /api/health returns degraded.
set -euo pipefail

# Run from the repo root so `python -m web.backend.main` finds the
# package. The script lives at web/scripts/, so go up two levels.
cd "$(cd "$(dirname "$0")/../.." && pwd)"

echo "Starting Domovoi Web on ${WEB_HOST:-0.0.0.0}:${WEB_PORT:-6369}"
exec python -m web.backend.main
