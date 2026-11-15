#!/usr/bin/env bash
# Builds the app's unsigned release APKs from one commit inside the pinned
# build-env image (Dockerfile beside this file), the way CI's reproducibility
# check and F-Droid build them: a clean Linux checkout of exactly that commit,
# so no line ending, path, locale, JDK or SDK of this machine reaches the APK.
# release.sh runs it; its PowerShell twin is build.ps1, which release.ps1 runs.
#
#   bash android/build-env/build.sh                    HEAD, both release ABIs
#   bash android/build-env/build.sh --commit SHA       another commit
#   bash android/build-env/build.sh --out DIR [ABIS ...]
#
# Writes to --out (default android/app/build/outputs/apk/release, where the
# release scripts sign from): app-<abi>-release-unsigned.apk, mapping.txt and
# BUILD-ENV.txt; with the default --out, mapping.txt also goes where Gradle
# puts it (android/app/build/outputs/mapping/release/). Only committed files
# are built: the commit is fetched from this checkout's .git, read-only.
#
# Runs on Linux, macOS or Git Bash with Docker (a Linux engine). Environment:
#   DOMOVOI_BUILD_CPUS           --cpuset-cpus for the container (default: 16-31 on
#                                a machine with 32 or more, the owner's E-cores; else all)
#   DOMOVOI_BUILD_GRADLE_VOLUME  a Docker volume for Gradle's downloads between runs
#                                (default domovoi-android-gradle; "none" for a fresh one;
#                                gradle/verification-metadata.xml checks them either way)
set -euo pipefail

commit=HEAD
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) commit="${2:?--commit needs a commit}"; shift ;;
    --out) out="${2:?--out needs a directory}"; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    --) shift; break ;;
    -*) echo "build.sh: unknown option $1 (see --help)" >&2; exit 64 ;;
    *) break ;;
  esac
  shift
done

die() { echo "build.sh: $*" >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

command -v docker >/dev/null 2>&1 || die "no docker: install Docker (a Linux engine), or build without it (android/README.md)"
[ "$(docker info --format '{{.OSType}}' 2>/dev/null || true)" = linux ] \
  || die "docker info fails or the engine is not a Linux one: start Docker Desktop (Linux containers)"

sha="$(git rev-parse --verify "$commit^{commit}")" || die "no commit $commit"
gitdir="$(git rev-parse --path-format=absolute --git-common-dir)"
default_out=0
if [ -z "$out" ]; then out="android/app/build/outputs/apk/release"; default_out=1; fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"

# Docker Desktop on Windows wants C:/... paths; Git Bash must not rewrite the
# container-side ones.
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

# The image, built from the commit's own Dockerfile and named by its hash.
dockerfile_sha="$(git show "$sha:android/build-env/Dockerfile" | sha256sum | cut -c1-12)" \
  || die "commit ${sha:0:12} has no android/build-env/Dockerfile"
image="domovoi-android-build-env:$dockerfile_sha"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "build.sh: building the image $image (once per Dockerfile)…"
  git archive --format=tar "$sha" android/build-env | docker build -q -t "$image" -f android/build-env/Dockerfile - >/dev/null \
    || die "building the image failed"
fi

cpus=()
if [ -n "${DOMOVOI_BUILD_CPUS:-}" ]; then
  cpus=(--cpuset-cpus "$DOMOVOI_BUILD_CPUS")
elif [ "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)" -ge 32 ]; then
  cpus=(--cpuset-cpus 16-31)
fi
volume="${DOMOVOI_BUILD_GRADLE_VOLUME:-domovoi-android-gradle}"
gradle=()
[ "$volume" = none ] || gradle=(-v "$volume:/gradle" -e GRADLE_USER_HOME=/gradle)

work="$(mktemp -d)"
name="domovoi-android-build-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

# Inside: a shallow fetch of exactly that commit from this checkout's .git
# (read-only), then that commit's own build-apks.sh.
inner='set -euo pipefail
sha=$1; shift
git config --global --add safe.directory "*"
git init -q /build/domovoi
git -C /build/domovoi fetch -q --depth 1 --no-tags \
  --upload-pack="git -c uploadpack.allowAnySHA1InWant=true upload-pack" file:///git "$sha"
git -C /build/domovoi -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
[ "$(git -C /build/domovoi rev-parse HEAD)" = "$sha" ]
exec bash /build/domovoi/android/build-env/build-apks.sh /build/domovoi /out "$@"'

echo "build.sh: building ${sha:0:12} in $image…"
MSYS_NO_PATHCONV=1 docker run --rm --name "$name" "${cpus[@]}" "${gradle[@]}" \
  --mount "type=bind,source=$(hostpath "$gitdir"),target=/git,readonly" \
  --mount "type=bind,source=$(hostpath "$work"),target=/out" \
  "$image" bash -c "$inner" build "$sha" "$@" \
  || die "the build failed"

if [ "$default_out" = 1 ]; then
  rm -f "$out"/*.apk
  mkdir -p android/app/build/outputs/mapping/release
  cp "$work/mapping.txt" android/app/build/outputs/mapping/release/mapping.txt
fi
cp -R "$work"/. "$out"/
echo "build.sh: done: $out"
(cd "$out" && ls -l -- *.apk 2>/dev/null || true)
