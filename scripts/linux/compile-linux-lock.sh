#!/usr/bin/env bash
# compile-linux-lock.sh: regenerate requirements-linux-py314.lock, the
# hash-pinned dependency set a Linux server installs (docs/LINUX_HOST.md,
# "Install"; apply-update.sh sync_deps).
#
# pip-compile runs inside a throwaway python:3.14-slim container, pinned by
# digest, so the resolver sees Linux and CPython 3.14 whatever machine runs
# this (Windows with Docker Desktop included). The checkout is mounted
# read-only; only the lock file is written back, and only when pip-compile
# succeeded.
#
#   bash scripts/linux/compile-linux-lock.sh                  # keep current pins where they still resolve
#   bash scripts/linux/compile-linux-lock.sh --upgrade        # newest of everything
#   bash scripts/linux/compile-linux-lock.sh --upgrade-package urllib3
#   DOMOVOI_LOCK_SEED=beelink-freeze.txt bash scripts/linux/compile-linux-lock.sh
#
# DOMOVOI_LOCK_SEED names a file of `name==version` lines (a `pip freeze`
# of a running server, or requirements.lock, the suite's tested set) that
# pip-compile starts from instead of the committed lock: every version in
# it that still satisfies pyproject is kept, so a production box's first
# locked re-sync moves only what the floors require. It is a preference,
# not a constraint: a seed version below a raised floor moves up.
#
# Extra arguments go to pip-compile. Needs Docker and network access.

set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LOCK=requirements-linux-py314.lock
# docker.io/library/python:3.14-slim, the image index digest of 2026-10-07.
IMAGE=${DOMOVOI_LOCK_IMAGE:-python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2}
# pip-tools 7.6.0 imports pip internals that pip 26.2 removed: pin the pair.
PIP_TOOLS="pip==26.1 pip-tools==7.6.0"
TORCH_INDEX=https://download.pytorch.org/whl/cpu
EXTRAS=(real-clients voice-profile)

# Git Bash on Windows: hand docker a drive path, and stop MSYS rewriting
# the container-side paths.
mount_src=$REPO
if command -v cygpath >/dev/null 2>&1; then mount_src=$(cygpath -m "$REPO"); fi
export MSYS_NO_PATHCONV=1

args=()
for e in "${EXTRAS[@]}"; do args+=(--extra "$e"); done
args+=("$@")

seed_mount=()
if [ -n "${DOMOVOI_LOCK_SEED:-}" ]; then
  [ -f "$DOMOVOI_LOCK_SEED" ] || { echo "compile-linux-lock: no seed file at $DOMOVOI_LOCK_SEED" >&2; exit 2; }
  seed=$(cd "$(dirname "$DOMOVOI_LOCK_SEED")" && pwd)/$(basename "$DOMOVOI_LOCK_SEED")
  if command -v cygpath >/dev/null 2>&1; then seed=$(cygpath -m "$seed"); fi
  seed_mount=(-v "$seed:/seed.txt:ro")
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

# The header pip-compile writes names this script rather than the
# container's own command line.
docker run --rm \
  -v "$mount_src:/src:ro" \
  ${seed_mount[@]+"${seed_mount[@]}"} \
  -e CUSTOM_COMPILE_COMMAND="bash scripts/linux/compile-linux-lock.sh" \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -e PIP_TOOLS="$PIP_TOOLS" \
  -e TORCH_INDEX="$TORCH_INDEX" \
  "$IMAGE" sh -euc '
    lock=$1; shift
    pip install -q --root-user-action=ignore $PIP_TOOLS >&2
    mkdir /work && cd /work
    cp /src/pyproject.toml /src/requirements-linux-py314.in .
    # Start from the seed, else the committed pins, so a plain run moves
    # nothing that still resolves.
    if [ -f /seed.txt ]; then cp /seed.txt "$lock"
    elif [ -f "/src/$lock" ]; then cp "/src/$lock" .; fi
    pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras \
      --extra-index-url "$TORCH_INDEX" \
      --output-file "$lock" "$@" pyproject.toml requirements-linux-py314.in >&2
    cat "$lock"
  ' sh "$LOCK" "${args[@]}" >"$tmp"

[ -s "$tmp" ] || { echo "compile-linux-lock: pip-compile wrote nothing" >&2; exit 1; }
# LF endings whatever the host's git would do with them.
tr -d '\r' <"$tmp" >"$REPO/$LOCK"
echo "wrote $LOCK ($(grep -c '^[A-Za-z0-9]' "$REPO/$LOCK") pins)" >&2
