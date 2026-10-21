#!/usr/bin/env bash
# Run the hermetic harnesses of scripts/linux/tests/ in a pinned Linux
# container (scripts/dev/linux-harness.Dockerfile: Ubuntu 26.04 by digest,
# packages from a fixed snapshot).
#
# Why: the harnesses fork thousands of small processes, and process creation
# is what Git Bash (MSYS) is slowest at; the same cases in a Linux container
# take a fraction of the time, on the same coreutils (uutils) the server
# runs. On a Linux box it also keeps the harness off the host's own tools.
# Git Bash still runs them directly, as before:
#   bash scripts/linux/tests/test-apply-update.sh [--case NAME|GLOB]... [--list]
#
# Usage:
#   scripts/dev/run-linux-harness.sh [options] HARNESS [--case NAME|GLOB]... [--list]
#
#   HARNESS   apply-update | provision | install-update-unit | printing, or
#             `all` (the four at once, each in its own container; no --case).
#             printing runs in its own image (printing-harness.Dockerfile:
#             the same base plus ippeveprinter, for tests only).
#   --case, --list  passed to the harness (scripts/linux/tests/case-filter.sh).
#   --src DIR       the tree to test (default: the checkout holding this
#                   script). Its git-tracked and untracked-but-not-ignored
#                   files are copied into the container; nothing in it is
#                   written to, the results directory aside.
#   --results DIR   where the logs go (default: <src>/.test-results/linux-harness,
#                   ignored by git).
#   --rebuild       build the image even if a copy is cached.
#
# Exit status: the harness's (0 = every selected case passed); with `all`,
# non-zero if any harness failed. 2 for a usage error.
#
# One script for both hosts on purpose: it is bash, like the harnesses, so it
# runs as is on a Linux box and in Git Bash on Windows (which every harness
# run on Windows already needs). From PowerShell, call Git Bash by its path:
# `bash` there is WSL's launcher. Needs Docker (Docker Desktop on Windows);
# the container gets no network and only sees a copy of the tree.

set -uo pipefail

# Git Bash would rewrite the container-side paths below (/tmp, /usr/bin/...)
# into Windows paths before docker.exe sees them.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
IMAGE_REPO=domovoi-linux-harness
HARNESSES=(apply-update provision install-update-unit printing)

# The Dockerfile a harness's image is built from: printing needs
# ippeveprinter (printing-harness.Dockerfile), the others only the base set.
dockerfile_for() {
  if [ "$1" = printing ]; then
    echo "$HERE/printing-harness.Dockerfile"
  else
    echo "$HERE/linux-harness.Dockerfile"
  fi
}

usage() { sed -n '/^# Usage:/,/^# Exit status/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; }
die() { echo "run-linux-harness: $*" >&2; exit 2; }

SRC=""
RESULTS=""
REBUILD=0
HARNESS=""
PASS=()
LISTING=0
while [ $# -gt 0 ]; do
  case $1 in
    --src) [ $# -ge 2 ] || die "--src needs a directory"; SRC=$2; shift 2 ;;
    --results) [ $# -ge 2 ] || die "--results needs a directory"; RESULTS=$2; shift 2 ;;
    --rebuild) REBUILD=1; shift ;;
    --case) [ $# -ge 2 ] || die "--case needs a case name or glob"; PASS+=(--case "$2"); shift 2 ;;
    --case=?*) PASS+=("$1"); shift ;;
    --list) PASS+=(--list); LISTING=1; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) die "unknown option: $1 (--help)" ;;
    *)
      [ -z "$HARNESS" ] || die "one harness at a time (or all), got: $HARNESS and $1"
      HARNESS=$1; shift ;;
  esac
done
[ -n "$HARNESS" ] || { usage >&2; exit 2; }
if [ "$HARNESS" = all ]; then
  [ ${#PASS[@]} -eq 0 ] || die "--case and --list name one harness's cases; run that harness instead of all"
else
  ok=0
  for h in "${HARNESSES[@]}"; do [ "$h" = "$HARNESS" ] && ok=1; done
  [ "$ok" = 1 ] || die "unknown harness: $HARNESS (apply-update, provision, install-update-unit, printing or all)"
fi

SRC=$(cd "${SRC:-$HERE/../..}" 2>/dev/null && pwd) || die "no such directory: ${SRC:-}"
[ -f "$SRC/scripts/linux/tests/test-apply-update.sh" ] || die "$SRC is not a Domovoi checkout"
RESULTS=${RESULTS:-$SRC/.test-results/linux-harness}
mkdir -p "$RESULTS" || die "can't create $RESULTS"
RESULTS=$(cd "$RESULTS" && pwd)

command -v docker >/dev/null 2>&1 || die "docker isn't on PATH"
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
  || die "Docker isn't answering (start Docker Desktop / the docker service)"

# ─── the image: built once per Dockerfile revision, then cached ─────────────

# A hash of the Dockerfile's text (line endings aside) names the image, so
# a changed file gets a new image and an unchanged one is never rebuilt.
# image_for HARNESS prints that harness's image name, building it first
# when no copy is cached (each harness's Dockerfile: dockerfile_for).
image_for() {
  local dockerfile tag image
  dockerfile=$(dockerfile_for "$1")
  tag=$(tr -d '\r' <"$dockerfile" | sha256sum | cut -c1-12)
  image=$IMAGE_REPO:$tag
  if [ "$REBUILD" = 1 ] || ! docker image inspect "$image" >/dev/null 2>&1; then
    echo "run-linux-harness: building $image (once per Dockerfile change)..." >&2
    # The Dockerfile goes in on stdin: no build context, nothing of the tree
    # is sent to the builder.
    tr -d '\r' <"$dockerfile" | docker build -q -t "$image" - >/dev/null \
      || die "building $image failed (rerun with --rebuild after fixing; docker build output above)"
  fi
  echo "$image"
}
# Build what this run needs up front (and once): `all` needs both images.
if [ "$HARNESS" = all ]; then
  for h in "${HARNESSES[@]}"; do image_for "$h" >/dev/null; done
else
  image_for "$HARNESS" >/dev/null
fi
REBUILD=0

# ─── the copy of the tree ───────────────────────────────────────────────────

WORK=$(mktemp -d)
CONTAINERS=()
cleanup() {
  local c
  for c in ${CONTAINERS[@]+"${CONTAINERS[@]}"}; do docker rm -f "$c" >/dev/null 2>&1; done
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# What a checkout would hold: tracked files (as they are on disk, edits
# included) and new files git isn't told to ignore. Deleted ones are left
# out. Outside a git checkout, the whole directory but .git and .venv.
# git runs from inside $SRC, not with -C "$SRC": with the path conversion
# switched off above, Git Bash would hand git.exe a /c/... path it cannot
# open, so every Windows run fell back to the copy-everything branch below
# (build outputs, Gradle's held lock files and all).
if (cd "$SRC" && git rev-parse --is-inside-work-tree) >/dev/null 2>&1; then
  (cd "$SRC" && git ls-files -z --cached --others --exclude-standard) \
    | while IFS= read -r -d '' f; do [ -f "$SRC/$f" ] && printf '%s\0' "$f"; done >"$WORK/files"
else
  (cd "$SRC" && find . \( -name .git -o -name .venv -o -name .test-results \) -prune -o -type f -print0) >"$WORK/files"
fi
tar -C "$SRC" --null -T "$WORK/files" -cf "$WORK/tree.tar" || die "copying $SRC failed"

# ─── one harness, one container ─────────────────────────────────────────────

# run_one NAME HARNESS [harness args]: stream the tree in, run the harness,
# its output on stdout. NAME is the container's, so cleanup can find it.
run_one() {
  local name=$1 h=$2 image
  shift 2
  image=$(image_for "$h") || exit 2
  # --network none: the harnesses are hermetic, so nothing may reach out.
  # /tmp is a tmpfs: every case's throwaway repositories live there.
  docker run --rm -i --name "$name" --label org.domovoi.harness="$h" \
    --network none --tmpfs /tmp:rw,exec,nosuid,mode=1777,size=2g \
    -e HARNESS_PYTHON=/usr/bin/python3 \
    "$image" bash -c 'mkdir -p src && tar -xf - -C src && cd src && exec bash "scripts/linux/tests/test-$0.sh" "$@"' \
    "$h" "$@" <"$WORK/tree.tar" 2>&1
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
if [ "$HARNESS" != all ]; then
  log=$RESULTS/$HARNESS-$stamp.log
  [ "$LISTING" = 0 ] || log=/dev/null   # a listing is not a run worth keeping
  name=domovoi-harness-$HARNESS-$$
  CONTAINERS+=("$name")
  t0=$(date +%s)
  run_one "$name" "$HARNESS" ${PASS[@]+"${PASS[@]}"} | tee "$log"
  rc=${PIPESTATUS[0]}
  if [ "$LISTING" = 0 ]; then
    echo "run-linux-harness: $HARNESS exit $rc in $(($(date +%s) - t0))s ($(image_for "$HARNESS")); log: $log" >&2
  fi
  exit "$rc"
fi

t0=$(date +%s)
declare -A PIDS LOGS
for h in "${HARNESSES[@]}"; do
  LOGS[$h]=$RESULTS/$h-$stamp.log
  CONTAINERS+=("domovoi-harness-$h-$$")
  run_one "domovoi-harness-$h-$$" "$h" >"${LOGS[$h]}" &
  PIDS[$h]=$!
done
echo "run-linux-harness: running ${HARNESSES[*]} side by side..." >&2
worst=0
for h in "${HARNESSES[@]}"; do
  wait "${PIDS[$h]}"
  rc=$?
  [ "$rc" = 0 ] || worst=1
  printf '%-20s exit %-3s %s\n%-20s log: %s\n' "$h" "$rc" "$(tail -n 1 "${LOGS[$h]}")" "" "${LOGS[$h]}"
done
echo "run-linux-harness: all done in $(($(date +%s) - t0))s" >&2
exit "$worst"
