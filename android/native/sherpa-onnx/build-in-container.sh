#!/usr/bin/env bash
# Runs INSIDE the pinned container that build.sh starts; never on a host.
# Builds sherpa-onnx's JNI library for the app with text-to-speech compiled
# out (no espeak-ng, no piper-phonemize: listening only), and writes to /out:
#
#   jniLibs/<abi>/libsherpa-onnx-jni.so   the libraries, stripped
#   kotlin/com/k2fsa/sherpa/onnx/*.kt     the Kotlin API they bind to
#   LICENSE, licenses/                    sherpa-onnx's licence and each
#                                         compiled-in dependency's
#   BUILD-INFO.txt, SHA256SUMS            what built it, and the hashes
#
# Every input is pinned: the image (build.sh), the apt snapshot and package
# versions, the NDK zip and the ONNX Runtime AAR by SHA-256, sherpa-onnx by
# commit, and sherpa-onnx's own CMake fetches by the SHA-256s in its tree.
set -euo pipefail

# ── Pins ─────────────────────────────────────────────────────────────────
SHERPA_REPO=https://github.com/k2-fsa/sherpa-onnx
SHERPA_TAG=v1.13.8
SHERPA_COMMIT=11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf

# NDK r29 (29.0.14206865). Google's SDK repository lists its SHA-1 as
# 87e2bb7e9be5d6a1c6cdf5ec40dd4e0c6d07c30b; the SHA-256 is of that file.
NDK_ZIP=android-ndk-r29-linux.zip
NDK_SHA256=4abbbcdc842f3d4879206e9695d52709603e52dd68d3c1fff04b3b5e7a308ecf

# The official ONNX Runtime Android package from Maven Central (MIT). The
# app gets the same AAR through Gradle (gradle/verification-metadata.xml
# pins the same hash), so the library linked against here is the one the
# app ships.
ORT_VERSION=1.28.0
ORT_AAR=onnxruntime-android-$ORT_VERSION.aar
ORT_URL=https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/$ORT_VERSION/$ORT_AAR
ORT_SHA256=f351a0638696f54b35184290dbc001d66daae17281ad0b548d2c70347d53b8a9

APT_SNAPSHOT=20261001T000000Z
APT_PACKAGES=(
  cmake=3.28.3-1build7
  ninja-build=1.11.1-2
  git=1:2.43.0-1ubuntu7.3
  curl=8.5.0-2ubuntu10.15
  unzip=6.0-28ubuntu4.1
)

ABIS=(arm64-v8a x86_64)
ANDROID_API=26   # the app's minSdk

# The Kotlin API files the app uses (speech to text, voice detection) and
# the ones they refer to. Nothing for text to speech.
KOTLIN_API=(OfflineRecognizer.kt OfflineStream.kt FeatureConfig.kt HomophoneReplacerConfig.kt QnnConfig.kt Vad.kt)

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
fetch "$ORT_URL" "$ORT_AAR" "$ORT_SHA256"
mkdir -p /work/ort
unzip -q "/work/$ORT_AAR" 'headers/*' 'jni/*/libonnxruntime.so' -d /work/ort

echo "== sherpa-onnx $SHERPA_TAG ($SHERPA_COMMIT)"
git init -q /work/sherpa
git -C /work/sherpa fetch -q --depth 1 "$SHERPA_REPO" "$SHERPA_COMMIT"
git -C /work/sherpa -c advice.detachedHead=false checkout -q FETCH_HEAD
test "$(git -C /work/sherpa rev-parse HEAD)" = "$SHERPA_COMMIT"
tag_now=$(git ls-remote "$SHERPA_REPO" "refs/tags/$SHERPA_TAG" | cut -f1)
if [ "$tag_now" != "$SHERPA_COMMIT" ]; then
  echo "   note: tag $SHERPA_TAG now names ${tag_now:-nothing}; building the pinned commit anyway"
fi

# ── Build ────────────────────────────────────────────────────────────────
# -ffile-prefix-map keeps /work out of the binaries (sherpa-onnx's log lines
# carry __FILE__), so the output does not depend on where it was built.
for abi in "${ABIS[@]}"; do
  echo "== build $abi"
  build=/work/build-$abi
  SHERPA_ONNXRUNTIME_LIB_DIR=/work/ort/jni/$abi \
  SHERPA_ONNXRUNTIME_INCLUDE_DIR=/work/ort/headers \
  cmake -S /work/sherpa -B "$build" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$abi" \
    -DANDROID_PLATFORM="android-$ANDROID_API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-ffile-prefix-map=/work/=" \
    -DCMAKE_CXX_FLAGS="-ffile-prefix-map=/work/=" \
    -DBUILD_SHARED_LIBS=ON \
    -DSHERPA_ONNX_ENABLE_TTS=OFF \
    -DSHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION=OFF \
    -DSHERPA_ONNX_ENABLE_JNI=ON \
    -DSHERPA_ONNX_ENABLE_C_API=OFF \
    -DSHERPA_ONNX_ENABLE_BINARY=OFF \
    -DSHERPA_ONNX_ENABLE_PYTHON=OFF \
    -DSHERPA_ONNX_ENABLE_TESTS=OFF \
    -DSHERPA_ONNX_ENABLE_CHECK=OFF \
    -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF \
    -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF \
    -DSHERPA_ONNX_ENABLE_RKNN=OFF \
    -DSHERPA_ONNX_ENABLE_QNN=OFF \
    -DSHERPA_ONNX_LINK_LIBSTDCPP_STATICALLY=OFF \
    > "/work/configure-$abi.log" 2>&1 || { tail -40 "/work/configure-$abi.log"; exit 1; }
  # It must link the ONNX Runtime unpacked above, never download its own.
  if grep -q "Downloading onnxruntime" "/work/configure-$abi.log"; then
    echo "sherpa-onnx tried to download its own ONNX Runtime" >&2
    exit 1
  fi
  cmake --build "$build" --target sherpa-onnx-jni -j "$(nproc)" > "/work/build-$abi.log" 2>&1 \
    || { tail -40 "/work/build-$abi.log"; exit 1; }
  mkdir -p "/out/jniLibs/$abi"
  "$LLVM/llvm-strip" --strip-unneeded -o "/out/jniLibs/$abi/libsherpa-onnx-jni.so" \
    "$(find "$build" -name libsherpa-onnx-jni.so -type f | head -1)"
done

# ── Kotlin API and licences ─────────────────────────────────────────────
mkdir -p /out/kotlin/com/k2fsa/sherpa/onnx /out/licenses
for f in "${KOTLIN_API[@]}"; do
  cp "/work/sherpa/sherpa-onnx/kotlin-api/$f" /out/kotlin/com/k2fsa/sherpa/onnx/
done
cp /work/sherpa/LICENSE /out/LICENSE
# Every dependency sherpa-onnx's CMake fetched for the arm64 build (the
# x86_64 build fetches the same set).
# Every licence file at the top of each one (Eigen, for one, has several).
for src in /work/build-arm64-v8a/_deps/*-src; do
  dep=$(basename "$src" -src)
  mkdir -p "/out/licenses/$dep"
  find "$src" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
    -exec cp {} "/out/licenses/$dep/" \;
  if [ -z "$(ls -A "/out/licenses/$dep")" ]; then echo "(no licence file found)" > "/out/licenses/$dep/NONE"; fi
done

# ── Checks and record ────────────────────────────────────────────────────
# Nothing of the text-to-speech stack may be in the output. These strings
# come from espeak-ng's own code (its data files, its environment variable,
# its temp files and messages), piper-phonemize and sherpa-onnx's TTS JNI;
# the release AAR's library holds 95 of them. (Case matters: a plain
# case-blind "espeak" also matches sherpa-onnx's "OfflineSpeakerDiarization"
# and "wespeaker".)
TTS_MARKERS='espeak-ng|eSpeak-ng|ESPEAK_DATA_PATH|espeakXXXXXX|phontab|phonindex|phondata|piper-phonemize|PiperPhonemes|OfflineTts'
for so in /out/jniLibs/*/libsherpa-onnx-jni.so; do
  if grep -a -q -E "$TTS_MARKERS" "$so"; then
    echo "text-to-speech code found in $so:" >&2
    grep -a -o -E "$TTS_MARKERS" "$so" | sort | uniq -c >&2
    exit 1
  fi
done

{
  echo "sherpa-onnx $SHERPA_TAG, commit $SHERPA_COMMIT, text to speech OFF, speaker diarization OFF"
  echo "NDK $(grep Pkg.Revision "$NDK/source.properties" | cut -d= -f2 | tr -d ' ') ($NDK_ZIP, sha256 $NDK_SHA256)"
  echo "clang $("$LLVM/clang" --version | head -1)"
  echo "ONNX Runtime $ORT_VERSION, $ORT_URL (sha256 $ORT_SHA256)"
  echo "android-$ANDROID_API, ABIs ${ABIS[*]}"
  echo "apt snapshot $APT_SNAPSHOT: ${APT_PACKAGES[*]}"
  echo "fetched by sherpa-onnx's CMake: $(cd /work/build-arm64-v8a/_deps && ls -d -- *-src | sed 's/-src$//' | tr '\n' ' ')"
} > /out/BUILD-INFO.txt

(cd /out && find . -type f ! -name SHA256SUMS | sed 's#^\./##' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
echo "== done"
cat /out/BUILD-INFO.txt
grep jniLibs /out/SHA256SUMS
ls -l /out/jniLibs/*/
