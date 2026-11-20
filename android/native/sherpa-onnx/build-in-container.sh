#!/usr/bin/env bash
# Runs INSIDE the pinned container that build.sh starts; never on a host.
# Installs the pinned build tools, the NDK and the ONNX Runtime AAR, then
# runs build-native.sh, the same build F-Droid runs on its own server, and
# writes to /out:
#
#   jniLibs/<abi>/libsherpa-onnx-jni.so   the libraries, stripped
#   kotlin/com/k2fsa/sherpa/onnx/*.kt     the Kotlin API they bind to
#   LICENSE, licenses/                    sherpa-onnx's licence and each
#                                         compiled-in dependency's
#   BUILD-INFO.txt, SHA256SUMS            what built it, and the hashes
#
# Every input is pinned: the image (build.sh), the apt snapshot and package
# versions here, and in build-native.sh the NDK zip and the ONNX Runtime AAR
# by SHA-256 and sherpa-onnx and each library its CMake would fetch by git
# commit.
set -euo pipefail

# The pins shared with build-native.sh: SHERPA_*, DEPS, NDK_*, ORT_*.
# shellcheck source=build-native.sh
source /src/build-native.sh --pins

APT_SNAPSHOT=20261001T000000Z
APT_PACKAGES=(
  cmake=3.28.3-1build7
  ninja-build=1.11.1-2
  git=1:2.43.0-1ubuntu7.3
  curl=8.5.0-2ubuntu10.15
  unzip=6.0-28ubuntu4.1
)

# ── Tools ────────────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
echo "== tools (apt snapshot $APT_SNAPSHOT)"
# The snapshot service is HTTPS-only, so the CA bundle comes first from the
# image's own archive. It only decides which TLS certificates are trusted;
# nothing from it reaches the build output.
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates >/dev/null
sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/$APT_SNAPSHOT/#" \
  /etc/apt/sources.list.d/ubuntu.sources
apt-get update -qq
apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}" >/dev/null

mkdir -p /work /out
cd /work

# Fetch $1 to $2 unless /cache already has it; either way it must hash to $3.
fetch() {
  local url=$1 name=$2 sha=$3
  if [ -f "/cache/$name" ]; then
    cp "/cache/$name" "/work/$name"
  else
    curl -fsSL --retry 3 -o "/work/$name" "$url"
    if [ -d /cache ] && [ -w /cache ]; then cp "/work/$name" "/cache/$name"; fi
  fi
  echo "$sha  /work/$name" | sha256sum -c --quiet -
  echo "   $name: sha256 ok"
}

echo "== NDK"
fetch "https://dl.google.com/android/repository/$NDK_ZIP" "$NDK_ZIP" "$NDK_SHA256"
unzip -q "/work/$NDK_ZIP" -d /opt
rm "/work/$NDK_ZIP"
NDK=$(echo /opt/android-ndk-*)
LLVM=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin

echo "== ONNX Runtime $ORT_VERSION"
fetch "$ORT_URL" "$ORT_AAR_NAME" "$ORT_SHA256"

tag_now=$(git ls-remote "$SHERPA_REPO" "refs/tags/$SHERPA_TAG" | cut -f1)
if [ "$tag_now" != "$SHERPA_COMMIT" ]; then
  echo "   note: tag $SHERPA_TAG now names ${tag_now:-nothing}; building the pinned commit anyway"
fi

# ── Build ────────────────────────────────────────────────────────────────
# Sources unset, so build-native.sh clones each at its pinned commit, as
# F-Droid's srclibs are.
ANDROID_NDK=$NDK ORT_AAR=/work/$ORT_AAR_NAME WORK=/work OUT=/out \
  ABIS="${ALL_ABIS[*]}" bash /src/build-native.sh

# ── Record ───────────────────────────────────────────────────────────────
{
  echo "sherpa-onnx $SHERPA_TAG, commit $SHERPA_COMMIT, text to speech OFF, speaker diarization OFF"
  echo "NDK $(grep Pkg.Revision "$NDK/source.properties" | cut -d= -f2 | tr -d ' ') ($NDK_ZIP, sha256 $NDK_SHA256)"
  echo "clang $("$LLVM/clang" --version | head -1)"
  echo "ONNX Runtime $ORT_VERSION, $ORT_URL (sha256 $ORT_SHA256)"
  echo "android-$ANDROID_API, ABIs ${ALL_ABIS[*]}"
  echo "apt snapshot $APT_SNAPSHOT: ${APT_PACKAGES[*]}"
  echo "fetched by sherpa-onnx's CMake: $(cd /work/build-arm64-v8a/_deps && ls -d -- *-src | sed 's/-src$//' | tr '\n' ' ')"
} > /out/BUILD-INFO.txt

(cd /out && find . -type f ! -name SHA256SUMS | sed 's#^\./##' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
echo "== done"
cat /out/BUILD-INFO.txt
grep jniLibs /out/SHA256SUMS
ls -l /out/jniLibs/*/
