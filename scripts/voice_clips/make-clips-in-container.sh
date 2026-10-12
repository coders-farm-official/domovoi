#!/usr/bin/env bash
# Render the shipped voices' built-in sample clips (domovoi/voice_clips/) in
# a throwaway container (removed on exit): a pinned image, opus-tools from
# Debian bookworm at a pinned version, ONNX Runtime and numpy at the
# versions requirements.lock pins.
#
#   scripts/voice_clips/make-clips-in-container.sh VOICES_DIR [--check] [--cpus 16-31]
#
# VOICES_DIR holds en_US-{norman,kristin,ljspeech}-medium.onnx(.json), the
# pinned files (make_clips.py checks them). The container needs the network
# for apt and pip. --check renders again and compares with
# domovoi/voice_clips/SHA256SUMS instead of writing.
set -euo pipefail

IMG="python@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89"  # python:3.11-slim-bookworm
OPUS_TOOLS="0.2-1+b1"
ORT="1.28.0"
NUMPY="2.4.6"
PYDANTIC="2.13.4"
PYDANTIC_SETTINGS="2.14.2"

usage() { echo "usage: $0 VOICES_DIR [--check] [--cpus LIST]" >&2; exit 2; }
[ $# -ge 1 ] || usage
VOICES="$1"; shift
CHECK=""
CPUS="16-31"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK="--check"; shift ;;
    --cpus) [ $# -ge 2 ] || usage; CPUS="$2"; shift 2 ;;
    *) usage ;;
  esac
done

# Docker Desktop on Windows wants C:/... paths; Git Bash's pwd -W gives them.
abspath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }
REPO="$(abspath "$(dirname "$0")/../..")"
VOICES_ABS="$(abspath "$VOICES")"

export MSYS_NO_PATHCONV=1
exec docker run --rm --name "domovoi-voice-clips-$$" --cpuset-cpus "$CPUS" --memory 4g \
  -v "$REPO:/repo" -v "$VOICES_ABS:/voices:ro" -w /repo \
  -e PYTHONDONTWRITEBYTECODE=1 -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e DEBIAN_FRONTEND=noninteractive \
  "$IMG" sh -c "
    set -e
    apt-get update -qq >/dev/null
    apt-get install -y -qq --no-install-recommends opus-tools=$OPUS_TOOLS >/dev/null
    pip install -q --root-user-action=ignore onnxruntime==$ORT numpy==$NUMPY pydantic==$PYDANTIC pydantic-settings==$PYDANTIC_SETTINGS
    opusenc --version | head -1
    python scripts/voice_clips/make_clips.py --voices /voices $CHECK
  "
