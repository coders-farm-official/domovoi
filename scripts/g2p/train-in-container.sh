#!/usr/bin/env bash
# Rebuild the phonemizer's out-of-dictionary model from CMUdict, in a
# throwaway container (removed on exit) with a pinned image and pinned wheels.
#
#   scripts/g2p/train-in-container.sh OUTDIR [--cmudict DIR] [--wheels DIR] [--cpus 16-31]
#
# OUTDIR receives g2p.pt, g2p.onnx, g2p.int8.onnx (the shipped file),
# g2p_vocab.json, split.json, train_result.json and train.log; the log ends
# with each file's SHA-256, to compare with domovoi/phonemizer/README.md.
#
# The model trains on UNCHANGED upstream CMUdict (the repo's copy drops a few
# entries, see fetch_cmudict.py): without --cmudict, fetch_cmudict.py
# --upstream downloads it at the pinned commit into OUTDIR/cmudict-upstream;
# either way the container checks its SHA-256 before training. --wheels DIR
# installs from a folder of already-downloaded wheels with the network off
# (the hashes are still checked); without it pip downloads them. Environment:
# EPOCHS (40) and THREADS (14), the recorded run's values. About 90 minutes
# on 14 cores.
set -euo pipefail

IMG="python@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89"  # python:3.11-slim-bookworm
CMUDICT_SHA256="81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22"  # upstream, unchanged
EPOCHS="${EPOCHS:-40}"
THREADS="${THREADS:-14}"

usage() { echo "usage: $0 OUTDIR [--cmudict DIR] [--wheels DIR] [--cpus LIST]" >&2; exit 2; }
[ $# -ge 1 ] || usage
OUT="$1"; shift
WHEELS=""
CMUDICT=""
CPUS="16-31"
while [ $# -gt 0 ]; do
  case "$1" in
    --wheels) [ $# -ge 2 ] || usage; WHEELS="$2"; shift 2 ;;
    --cmudict) [ $# -ge 2 ] || usage; CMUDICT="$2"; shift 2 ;;
    --cpus) [ $# -ge 2 ] || usage; CPUS="$2"; shift 2 ;;
    *) usage ;;
  esac
done

# Docker Desktop on Windows wants C:/... paths; Git Bash's pwd -W gives them.
abspath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }
HERE="$(abspath "$(dirname "$0")")"
mkdir -p "$OUT"
OUT_ABS="$(abspath "$OUT")"
if [ -z "$CMUDICT" ]; then
  CMUDICT="$OUT/cmudict-upstream"
  PY="${PYTHON:-}"
  if [ -z "$PY" ]; then
    for c in python3 python; do
      if "$c" -c '' 2>/dev/null; then PY="$c"; break; fi
    done
  fi
  [ -n "$PY" ] || { echo "no working python3/python (set PYTHON)" >&2; exit 1; }
  "$PY" "$(dirname "$0")/fetch_cmudict.py" --upstream "$CMUDICT"
fi
CMUDICT_ABS="$(abspath "$CMUDICT")"

NET=(--network=none)
MOUNT_WHEELS=()
PIP_SRC="--no-index --find-links /wheels"
if [ -n "$WHEELS" ]; then
  MOUNT_WHEELS=(-v "$(abspath "$WHEELS"):/wheels:ro")
else
  NET=()
  PIP_SRC=""
fi

export MSYS_NO_PATHCONV=1
docker run --rm --name "domovoi-g2p-train-$$" "${NET[@]}" --cpuset-cpus "$CPUS" --memory 12g \
  -v "$HERE:/g2p:ro" -v "$CMUDICT_ABS:/cmudict:ro" -v "$OUT_ABS:/out" \
  "${MOUNT_WHEELS[@]}" \
  -e THREADS="$THREADS" -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  "$IMG" sh -c "set -e
    echo '$CMUDICT_SHA256  /cmudict/cmudict.dict' | sha256sum -c -
    pip install -q --root-user-action=ignore --no-deps --require-hashes $PIP_SRC -r /g2p/requirements-train.txt
    exec python -I /g2p/train_g2p.py /cmudict/cmudict.dict /out $EPOCHS" 2>&1 | tee "$OUT/train.log"
