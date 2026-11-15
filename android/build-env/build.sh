#!/usr/bin/env bash
# Builds the app's unsigned release APKs from one commit inside the pinned
# build-env image (Dockerfile beside this file), the way CI's reproducibility
# check and F-Droid build them: a clean Linux checkout of exactly that commit,
# so no line ending, path, locale, JDK or SDK of this machine reaches the APK.
# release.sh runs it; its PowerShell twin is build.ps1, which release.ps1 runs.
#
#   bash android/build-env/build.sh                    HEAD, both release ABIs
#   bash android/build-env/build.sh --commit SHA       another commit
#   bash android/build-env/build.sh --out DIR [ABI ...]   (each ABI its own Gradle run)
#   bash android/build-env/build.sh --image-only       only make sure the image exists;
#                                                      print its name
#
# Writes to --out (default android/app/build/outputs/apk/release, where the
# release scripts sign from): app-<abi>-release-unsigned.apk, mapping.txt and
# BUILD-ENV.txt; with the default --out, mapping.txt also goes where Gradle
# puts it (android/app/build/outputs/mapping/release/). Only committed files
# are built: the commit is fetched from this checkout's .git, read-only.
#
# Runs on Linux, macOS or Git Bash with Docker (a Linux engine). Environment:
#   DOMOVOI_BUILD_CPUS           --cpuset-cpus for the container (default: 16-31 on
#                                a machine with 32 or more, a cap of 16 of the CPUs
#                                Docker's VM has, so half stays free; not the host's
#                                E-cores, which the VM doesn't see; else all)
#   DOMOVOI_BUILD_GRADLE_VOLUME  a Docker volume for Gradle's downloads between runs
#                                (default domovoi-android-gradle; "none" for a fresh one;
#                                gradle/verification-metadata.xml checks them either way)
set -euo pipefail

commit=HEAD
out=""
image_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) commit="${2:?--commit needs a commit}"; shift ;;
    --out) out="${2:?--out needs a directory}"; shift ;;
    --image-only) image_only=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
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
if [ "$image_only" = 0 ]; then mkdir -p "$out"; out="$(cd "$out" && pwd)"; fi

# Docker Desktop on Windows wants C:/... paths; Git Bash must not rewrite the
# container-side ones.
hostpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

# The image, built from the commit's own android/build-env and named by its
# git tree, so the same files always make the same image.
tree="$(git rev-parse --verify --quiet "$sha:android/build-env")" \
  || die "commit ${sha:0:12} has no android/build-env"
image="domovoi-android-build-env:${tree:0:12}"
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "build.sh: building the image $image (once per android/build-env)…" >&2
  # No line-ending conversion: the image's scripts run in Linux.
  git -c core.autocrlf=false archive --format=tar "$sha:android/build-env" | docker build -q -t "$image" - >/dev/null \
    || die "building the image failed"
fi
if [ "$image_only" = 1 ]; then echo "$image"; exit 0; fi

cpus=()
if [ -n "${DOMOVOI_BUILD_CPUS:-}" ]; then
  cpus=(--cpuset-cpus "$DOMOVOI_BUILD_CPUS")
elif [ "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)" -ge 32 ]; then
  cpus=(--cpuset-cpus 16-31)
fi
volume="${DOMOVOI_BUILD_GRADLE_VOLUME:-domovoi-android-gradle}"
gradle=()
[ "$volume" = none ] || gradle=(-v "$volume:/gradle" -e GRADLE_USER_HOME=/gradle)

# The folder Docker mounts is under android/app/build, not the temp folder:
# a packaged app's redirected temp folder is one Docker can't see.
mkdir -p android/app/build
work="$(mktemp -d "$repo/android/app/build/build-env-XXXXXX")"
name="domovoi-android-build-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$work"' EXIT

echo "build.sh: building ${sha:0:12} in $image…"
# Inside: a shallow fetch of exactly that commit from this checkout's .git
# (read-only), then that commit's own build-apks.sh (build-commit.sh).
MSYS_NO_PATHCONV=1 docker run --rm --name "$name" "${cpus[@]}" "${gradle[@]}" \
  --mount "type=bind,source=$(hostpath "$gitdir"),target=/git,readonly" \
  --mount "type=bind,source=$(hostpath "$work"),target=/out" \
  "$image" domovoi-build-commit "$sha" "$@" \
  || die "the build failed"

if [ "$default_out" = 1 ]; then
  rm -f "$out"/*.apk
  mkdir -p android/app/build/outputs/mapping/release
  cp "$work/mapping.txt" android/app/build/outputs/mapping/release/mapping.txt
fi
cp -R "$work"/. "$out"/
# Which commit these are, for release.sh --skip-build's check.
printf '%s\n' "$sha" > "$out/COMMIT"
echo "build.sh: done: $out"
(cd "$out" && ls -l -- *.apk 2>/dev/null || true)
