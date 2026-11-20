#!/usr/bin/env bash
# Runs INSIDE the build-env image (android/build-env/Dockerfile), never on a
# host: builds the app's unsigned release APKs from the checkout SRC into OUT.
#
#   build-apks.sh SRC OUT [ABI ...]
#
# Each ABI is its own Gradle run, from clean (default: arm64-v8a, then
# armeabi-v7a, a release's two APKs), with -Pdomovoi.apkPerAbi
# -Pdomovoi.abis=<that ABI>: exactly how F-Droid builds each of them, one
# build per versionCode. One run for both ABIs would not do: the manifest
# AGP merges for several ABIs is one line longer, so the arm64-v8a APK's
# binary manifest records other source line numbers than the one built
# alone (CI's first double build, 2026-11-14).
#
# Into OUT: app-<abi>-release-unsigned.apk for each, mapping-<abi>.txt (R8's,
# for stack traces), mapping.txt (the first ABI's) and BUILD-ENV.txt (what
# this image is, /etc/domovoi-build-env.txt).
#
# The environment decides nothing about the output: that is what CI's two
# builds check (.github/workflows/ci.yml, android-repro-build), one with the
# image's defaults (UTC, C.UTF-8, Temurin) and one with another time zone,
# locale, umask, core count and JDK. GRADLE_USER_HOME is the caller's to set;
# by default a fresh one under /tmp, so nothing cached from another build is
# used.
set -euo pipefail

src=${1:?usage: build-apks.sh SRC OUT [ABI ...]}
out=${2:?usage: build-apks.sh SRC OUT [ABI ...]}
shift 2
[ $# -gt 0 ] || set -- arm64-v8a armeabi-v7a

export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$(mktemp -d /tmp/gradle-home.XXXXXX)}"
[ -f "$src/android/gradlew" ] || { echo "build-apks: $src is not a checkout of the repo" >&2; exit 2; }
mkdir -p "$out"

echo "== build env"
cat /etc/domovoi-build-env.txt
echo "java: $(command -v java) ($JAVA_HOME)"
echo "TZ=${TZ:-} LANG=${LANG:-} LC_ALL=${LC_ALL:-} umask $(umask) cpus $(nproc)"
echo "source: $src at $(git -C "$src" rev-parse HEAD 2>/dev/null || echo '(no git)')"

# Every run starts from an empty Gradle home, so it downloads every library
# again, and Maven Central sometimes fails a few of those requests: Gradle
# then says "Could not find" for libraries Central does hold, and the build
# stops (CI's repro builds, three runs in a row, 2026-11-19/20). A run that
# failed on downloads is tried again, up to three times, asking for every
# file afresh. Each file is still checked against
# android/gradle/verification-metadata.xml.
gradle_run() {
  local try log status refresh=()
  for try in 1 2 3; do
    log=$(mktemp)
    status=0
    bash ./gradlew --no-daemon --no-build-cache --no-configuration-cache --console=plain --warning-mode=summary \
      "${refresh[@]}" "$@" 2>&1 | tee "$log" || status=$?
    if [ "$status" -eq 0 ]; then
      rm -f "$log"
      return 0
    fi
    if [ "$try" -eq 3 ] || ! grep -qE "Could not (resolve|find|GET|HEAD|download)|Received status code|Read timed out|Connection reset" "$log"; then
      rm -f "$log"
      return "$status"
    fi
    rm -f "$log"
    echo "== downloads failed (try $try of 3); trying again in $((try * 30)) s"
    sleep $((try * 30))
    refresh=(--refresh-dependencies)
  done
}

cd "$src/android"
first=""
for abi in "$@"; do
  case "$abi" in *,*) echo "build-apks: one ABI per run, not $abi" >&2; exit 2 ;; esac
  echo "== :app:assembleRelease, ABI $abi"
  gradle_run clean :app:assembleRelease -Pdomovoi.apkPerAbi "-Pdomovoi.abis=$abi"
  cp "app/build/outputs/apk/release/app-$abi-release-unsigned.apk" "$out/"
  cp app/build/outputs/mapping/release/mapping.txt "$out/mapping-$abi.txt"
  [ -n "$first" ] || first="$abi"
  sha256sum -- "$out/app-$abi-release-unsigned.apk"
done
cp "$out/mapping-$first.txt" "$out/mapping.txt"
cp /etc/domovoi-build-env.txt "$out/BUILD-ENV.txt"
echo "== done"
