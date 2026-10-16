#!/usr/bin/env bash
# compile-linux-lock.sh: regenerate requirements-linux-py314.lock, the
# hash-pinned dependency set a Linux server installs (docs/LINUX_HOST.md,
# "Install"; apply-update.sh sync_deps), and beside it
# requirements-linux-py314-no-deps.lock: the packages of
# requirements-no-deps.in (faster-whisper, resemblyzer), pinned and hashed
# but installed without their declared dependencies (pip install --no-deps),
# at versions that agree with the main lock.
#
# pip-compile runs inside a throwaway python:3.14-slim container, pinned by
# digest, so the resolver sees Linux and CPython 3.14 whatever machine runs
# this (Windows with Docker Desktop included). The checkout is mounted
# read-only; only the two lock files are written back, and only when
# pip-compile succeeded.
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
# Extra arguments go to pip-compile (the main lock's run). Needs Docker and
# network access.

set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
LOCK=requirements-linux-py314.lock
NODEPS_LOCK=requirements-linux-py314-no-deps.lock
# docker.io/library/python:3.14-slim, the image index digest of 2026-10-07.
IMAGE=${DOMOVOI_LOCK_IMAGE:-python:3.14-slim@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2}
# pip-tools 7.6.0 imports pip internals that pip 26.2 removed: pin the pair.
PIP_TOOLS="pip==26.1 pip-tools==7.6.0"
TORCH_INDEX=https://download.pytorch.org/whl/cpu
EXTRAS=(real-clients voice-profile)
SPLIT='#### compile-linux-lock: no-deps lock follows ####'

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
  -e SPLIT="$SPLIT" \
  "$IMAGE" sh -euc '
    lock=$1; nodeps=$2; shift 2
    pip install -q --root-user-action=ignore $PIP_TOOLS >&2
    mkdir /work && cd /work
    cp /src/pyproject.toml /src/requirements-linux-py314.in /src/requirements-no-deps.in .
    # Start from the seed, else the committed pins, so a plain run moves
    # nothing that still resolves.
    if [ -f /seed.txt ]; then cp /seed.txt "$lock"
    elif [ -f "/src/$lock" ]; then cp "/src/$lock" .; fi
    pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras \
      --extra-index-url "$TORCH_INDEX" \
      --output-file "$lock" "$@" pyproject.toml requirements-linux-py314.in >&2
    # The no-deps packages, resolved against the main lock (its pins as
    # constraints) so what they do use agrees with it; then only their own
    # stanzas are kept: what they declare beyond the main lock (PyAV,
    # librosa, ...) is exactly what is not installed.
    sed -n "s/^\([A-Za-z0-9._-]*==[^ ]*\).*/\1/p" "$lock" >constraints.txt
    if [ -f "/src/$nodeps" ]; then cp "/src/$nodeps" nodeps.full; fi
    pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras \
      --extra-index-url "$TORCH_INDEX" -c constraints.txt \
      --output-file nodeps.full requirements-no-deps.in >&2
    python3 - requirements-no-deps.in nodeps.full >"$nodeps" <<"PY"
import re, sys
want = set()
for line in open(sys.argv[1]):
    line = line.split("#", 1)[0].strip()
    if line:
        want.add(re.split(r"[\s<>=!~\[;]", line, maxsplit=1)[0].lower().replace("_", "-"))
lines = open(sys.argv[2]).read().split("\n")
head, stanzas, cur = [], [], None
for line in lines:
    m = re.match(r"^([A-Za-z0-9._-]+)==", line)
    if m:
        cur = [m.group(1).lower().replace("_", "-"), [line]]
        stanzas.append(cur)
    elif cur is not None and line.startswith("    "):
        cur[1].append(line)
    elif not stanzas:
        head.append(line)          # the header, up to the first pin
    else:
        cur = None                 # between stanzas, and the trailing "unsafe" note
kept = [s for s in stanzas if s[0] in want]
missing = want - {s[0] for s in kept}
if missing:
    sys.exit("compile-linux-lock: not in the no-deps resolution: " + ", ".join(sorted(missing)))
print("\n".join(l for l in head if not l.startswith("--extra-index-url")).rstrip("\n"))
print("# Installed with --no-deps (requirements-no-deps.in says why): only")
print("# these packages, their dependencies come from requirements-linux-py314.lock.")
print()
for _, block in kept:
    print("\n".join(block))
PY
    cat "$lock"
    echo "$SPLIT"
    cat "$nodeps"
  ' sh "$LOCK" "$NODEPS_LOCK" "${args[@]}" >"$tmp"

[ -s "$tmp" ] || { echo "compile-linux-lock: pip-compile wrote nothing" >&2; exit 1; }
grep -qxF "$SPLIT" "$tmp" || { echo "compile-linux-lock: no no-deps lock in the output" >&2; exit 1; }
# LF endings whatever the host's git would do with them.
tr -d '\r' <"$tmp" | sed "/^$SPLIT\$/,\$d" >"$REPO/$LOCK"
tr -d '\r' <"$tmp" | sed "1,/^$SPLIT\$/d" >"$REPO/$NODEPS_LOCK"
echo "wrote $LOCK ($(grep -c '^[A-Za-z0-9]' "$REPO/$LOCK") pins) and $NODEPS_LOCK ($(grep -c '^[A-Za-z0-9]' "$REPO/$NODEPS_LOCK") pins)" >&2
