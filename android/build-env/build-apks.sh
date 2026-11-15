#!/usr/bin/env bash
# Runs INSIDE the build-env image (android/build-env/Dockerfile), never on a
# host: builds the app's unsigned release APKs from the checkout SRC into OUT.
#
#   build-apks.sh SRC OUT [ABIS ...]
#
# Each ABIS argument is one Gradle run, from clean, with a fresh output:
#   arm64-v8a,armeabi-v7a   what a release publishes (the default): one APK per ABI
#   arm64-v8a               one ABI alone, how F-Droid builds each of them
# With one ABIS argument the APKs land in OUT itself; with several, each run's
# in OUT/<abis with + for ,>/. Beside them: mapping.txt (R8's, for stack
# traces) and BUILD-ENV.txt (what this image is, /etc/domovoi-build-env.txt).
#
# The environment decides nothing about the output: that is what CI's two
# builds check (.github/workflows/ci.yml, android-repro-build), one with the
# image's defaults (UTC, C.UTF-8, Temurin) and one with another time zone,
# locale, umask, core count and JDK. GRADLE_USER_HOME is the caller's to set;
# by default a fresh one under /tmp, so nothing cached from another build is
# used.
set -euo pipefail

src=${1:?usage: build-apks.sh SRC OUT [ABIS ...]}
out=${2:?usage: build-apks.sh SRC OUT [ABIS ...]}
shift 2
[ $# -gt 0 ] || set -- arm64-v8a,armeabi-v7a

export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$(mktemp -d /tmp/gradle-home.XXXXXX)}"
[ -f "$src/android/gradlew" ] || { echo "build-apks: $src is not a checkout of the repo" >&2; exit 2; }
mkdir -p "$out"

echo "== build env"
cat /etc/domovoi-build-env.txt
echo "java: $(command -v java) ($JAVA_HOME)"
echo "TZ=${TZ:-} LANG=${LANG:-} LC_ALL=${LC_ALL:-} umask $(umask) cpus $(nproc)"
echo "source: $src at $(git -C "$src" rev-parse HEAD 2>/dev/null || echo '(no git)')"

cd "$src/android"
several=0; [ $# -gt 1 ] && several=1
for abis in "$@"; do
  dest="$out"
  [ "$several" = 0 ] || dest="$out/${abis//,/+}"
  echo "== :app:assembleRelease, ABIs $abis"
  bash ./gradlew --no-daemon --no-build-cache --no-configuration-cache --console=plain --warning-mode=summary \
    clean :app:assembleRelease -Pdomovoi.apkPerAbi "-Pdomovoi.abis=$abis"
  mkdir -p "$dest"
  cp app/build/outputs/apk/release/*-release-unsigned.apk "$dest/"
  cp app/build/outputs/mapping/release/mapping.txt "$dest/"
  (cd "$dest" && sha256sum -- *.apk)
done
cp /etc/domovoi-build-env.txt "$out/BUILD-ENV.txt"
echo "== done"
