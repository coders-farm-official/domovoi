#!/usr/bin/env bash
set -euo pipefail
CORE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$CORE_DIR/.." && pwd)"

# First run only: write domovoi/.env from .env.example with a random
# Postgres password, and with the example's STRICT satellite pairing
# (CORE-9) — a fresh household has nothing paired, so it starts closed.
# An existing .env is NEVER touched (exit 0 either way): that file is the
# household's posture, including the satellites it has already let in.
(cd "$REPO_ROOT" && python -m domovoi.env_bootstrap)
# The one thing ever added to an existing .env: the two helper-container
# secrets docker-compose.yml requires (LETTA_TOKEN, SEARXNG_SECRET), when a
# file from before they were generated lacks them. Appended, nothing else
# changes; a no-op once they are there. Without them every compose command
# below refuses.
(cd "$REPO_ROOT" && python -m domovoi.env_bootstrap --repair)

(cd "$CORE_DIR" && docker compose up -d postgres)
(cd "$CORE_DIR" && docker compose run --rm flyway)

# Web answers (the weather, "check that online", news topics) go through the
# local search helper, SearXNG. Start it when this box is answered Yes or
# Sometimes (INTERNET_ACCESS; docs/INTERNET.md); unanswered or No leaves it
# as it is. A failure is only a warning. DOMOVOI_MANAGE_SEARXNG=0 skips this.
case "$(printf '%s' "${DOMOVOI_MANAGE_SEARXNG:-1}" | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off) ;;
  *)
    INTERNET_POLICY="$(cd "$REPO_ROOT" && python -m domovoi.egress --print-policy 2>/dev/null | tr -d '[:space:]' || true)"
    case "$INTERNET_POLICY" in
      always|sometimes)
        (cd "$CORE_DIR" && docker compose up -d searxng) \
          || echo "warning: could not start the search helper (SearXNG); web answers stay off until it runs: (cd domovoi && docker compose up -d searxng)" >&2
        ;;
    esac
    ;;
esac

cd "$REPO_ROOT"
exec python -m domovoi.main
