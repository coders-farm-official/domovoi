#!/usr/bin/env bash
# Rebuilds the app's speech-recognition library, libsherpa-onnx-jni.so, from
# source inside a pinned Docker container, with sherpa-onnx's text to speech
# compiled out (espeak-ng is GPL-3.0; the app speaks with Android's own
# TextToSpeech instead). See README.md beside this file.
#
#   bash android/native/sherpa-onnx/build.sh             build, compare with SHA256SUMS
#   bash android/native/sherpa-onnx/build.sh --install   ...and copy the result in
#
# Runs on Linux or Git Bash with Docker. Environment:
#   SHERPA_BUILD_CPUS   --cpuset-cpus for the container (default 16-31)
#   SHERPA_BUILD_CACHE  a host directory for the NDK and ONNX Runtime
#                       downloads (still checked by SHA-256 when reused)
set -euo pipefail

# ubuntu:24.04, the multi-arch index digest of the image published 2026-09-18.
IMAGE=ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

here=$(cd "$(dirname "$0")" && pwd)
install=0
[ "${1:-}" = "--install" ] && install=1

out=$(mktemp -d)
name=domovoi-sherpa-build-$$
# Docker Desktop on Windows wants C:/... paths, and Git Bash must not
# rewrite the container-side ones.
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
mounts=(-v "$(hostpath "$here"):/src:ro" -v "$(hostpath "$out"):/out")
if [ -n "${SHERPA_BUILD_CACHE:-}" ]; then
  mkdir -p "$SHERPA_BUILD_CACHE"
  mounts+=(-v "$(hostpath "$SHERPA_BUILD_CACHE"):/cache")
fi

trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
MSYS_NO_PATHCONV=1 docker run --rm --name "$name" \
  --cpuset-cpus "${SHERPA_BUILD_CPUS:-16-31}" \
  "${mounts[@]}" "$IMAGE" bash /src/build-in-container.sh \
  || { rm -rf "$out"; echo "build failed" >&2; exit 1; }

echo
echo "built into $out"
if diff <(grep jniLibs "$here/SHA256SUMS" 2>/dev/null) <(grep jniLibs "$out/SHA256SUMS") >/dev/null; then
  echo "the libraries match the committed SHA256SUMS: reproduced"
else
  echo "the libraries differ from the committed SHA256SUMS:"
  diff <(grep jniLibs "$here/SHA256SUMS" 2>/dev/null) <(grep jniLibs "$out/SHA256SUMS") || true
fi

if [ "$install" = 1 ]; then
  rm -rf "$here/jniLibs" "$here/kotlin" "$here/licenses"
  cp -R "$out/jniLibs" "$out/kotlin" "$out/licenses" "$here/"
  cp "$out/LICENSE" "$out/BUILD-INFO.txt" "$out/SHA256SUMS" "$here/"
  echo "installed into $here"
fi
