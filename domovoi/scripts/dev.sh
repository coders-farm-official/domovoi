#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$CORE_DIR/.." && pwd)"

# A FRESH install gets its .env from the example, which starts with strict
# satellite pairing on (CORE-9). An existing .env is NEVER touched: the
# posture of a running household is its own, and a script that rewrote it
# could refuse the satellites already in the house on the next restart.
if [ ! -f "$CORE_DIR/.env" ]; then
  cp "$CORE_DIR/.env.example" "$CORE_DIR/.env"
  echo "created $CORE_DIR/.env from .env.example"
  echo "  satellite pairing is STRICT: a new satellite parks for approval"
  echo "  on the dashboard. Set SATELLITE_PAIRING_STRICT=false for the older"
  echo "  trust-on-first-use behaviour."
fi

(cd "$CORE_DIR" && docker compose up -d postgres)
(cd "$CORE_DIR" && docker compose run --rm flyway)

cd "$REPO_ROOT"
exec python -m domovoi.main
