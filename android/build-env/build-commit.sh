#!/usr/bin/env bash
# Runs INSIDE the build-env image (the Dockerfile copies it in as
# domovoi-build-commit): a clean, shallow checkout of exactly one commit from
# the repository mounted read-only at /git (a checkout's .git, as build.sh
# and build.ps1 mount it), then that commit's own build-apks.sh into /out.
#
#   domovoi-build-commit SHA [ABIS ...]
set -euo pipefail

sha=${1:?usage: domovoi-build-commit SHA [ABIS ...]}
shift
case "$sha" in
  *[!0-9a-f]*|'') echo "domovoi-build-commit: $sha is not a full commit id" >&2; exit 2 ;;
esac
[ "${#sha}" = 40 ] || { echo "domovoi-build-commit: $sha is not a full commit id" >&2; exit 2; }

# /git belongs to another user (the host's), so git must be told it's fine.
git config --global --add safe.directory '*'
git init -q /build/domovoi
git -C /build/domovoi fetch -q --depth 1 --no-tags \
  --upload-pack='git -c uploadpack.allowAnySHA1InWant=true upload-pack' file:///git "$sha"
git -C /build/domovoi -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
[ "$(git -C /build/domovoi rev-parse HEAD)" = "$sha" ]
exec bash /build/domovoi/android/build-env/build-apks.sh /build/domovoi /out "$@"
