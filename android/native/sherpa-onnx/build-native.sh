#!/usr/bin/env bash
# Builds sherpa-onnx's JNI library for the app, with text to speech compiled
# out, on a plain Linux x86_64 host: no Docker. F-Droid's build server runs
# this from its `build:` line; build-in-container.sh runs the same script
# inside the pinned container that made the committed libraries. See
# README.md beside this file.
#
#   cd android/app && ABIS=arm64-v8a ANDROID_NDK=/opt/android-ndk-r29 \
#     bash ../native/sherpa-onnx/build-native.sh
#
# Host tools: bash, cmake, ninja, unzip, tar, sha256sum; git when a source
# below is not given; curl or wget when ORT_AAR is not given.
#
# Environment:
#   ANDROID_NDK      the NDK r29 directory (or ANDROID_NDK_HOME); required
#   ABIS             which ABIs, space or comma separated
#                    (default: arm64-v8a x86_64 armeabi-v7a x86)
#   SHERPA_SRC       a sherpa-onnx checkout at SHERPA_COMMIT below
#   SHERPA_DEP_EIGEN, SHERPA_DEP_JSON, SHERPA_DEP_KALDI_DECODER,
#   SHERPA_DEP_KALDI_NATIVE_FBANK, SHERPA_DEP_KALDIFST, SHERPA_DEP_KISSFFT,
#   SHERPA_DEP_OPENFST, SHERPA_DEP_SIMPLE_SENTENCEPIECE
#                    checkouts of the libraries sherpa-onnx's CMake would
#                    otherwise download, at the commits in DEPS below
#   ORT_AAR          the onnxruntime-android AAR below (default: download)
#   SHERPA_OUT       where jniLibs/<abi>/, kotlin/, LICENSE and licenses/ go
#                    (default: this script's directory, which is where the
#                    app's Gradle build reads them)
#   SHERPA_WORK      scratch directory (default /work, which must exist and
#                    be writable: `sudo install -d -m 1777 /work`)
#
# The two directories have their own names, not the generic OUT and WORK
# (until 1.5.0): a build that happened to have OUT set for something else
# wrote the library there, and the APK went out without it. OUT and WORK
# are ignored now, with a note when they are set.
#
# Any source left unset is cloned here at its pinned commit. A given one
# that is a git checkout must be at that commit. Each is copied into WORK at
# the place sherpa-onnx's CMake would have unpacked it, and CMake is pointed
# there (FETCHCONTENT_SOURCE_DIR_<NAME>) and told not to download anything,
# so where the sources were does not matter.
#
# Where WORK is does matter, in one place. -ffile-prefix-map keeps it out of
# the code and the debug info, but not out of each compiled module's
# source_filename, which ThinLTO hashes into the names of the local symbols
# it promotes (name.llvm.<hash>). Those names are stripped from the library,
# but the linker's build ID is a hash of the unstripped file, so a WORK
# other than /work gives libraries that differ from the committed ones in
# their 20-byte build ID and nowhere else.
set -euo pipefail

# ── Pins ─────────────────────────────────────────────────────────────────
SHERPA_REPO=https://github.com/k2-fsa/sherpa-onnx
SHERPA_TAG=v1.13.8
SHERPA_COMMIT=11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf

# What sherpa-onnx's CMake fetches at that commit, as git commits: the
# version each cmake/*.cmake names (kaldifst through kaldi-decoder's
# cmake/kaldifst.cmake, kissfft through kaldi-native-fbank's
# cmake/kissfft.cmake). CMake pins each one's release tarball by SHA-256;
# each tarball holds exactly the files of the commit here.
#   FetchContent name | repository | tag (or commit) | commit
DEPS=(
  "eigen|https://gitlab.com/libeigen/eigen.git|5.0.1|bc3b39870ecb690a623a3f49149a358b95c5781d"
  "json|https://github.com/nlohmann/json|v3.12.0|55f93686c01528224f448c19128836e7df245f72"
  "kaldi_decoder|https://github.com/k2-fsa/kaldi-decoder|v0.3.0|62fda2d74246a90db9570e46fb16b012f813fae7"
  "kaldi_native_fbank|https://github.com/csukuangfj/kaldi-native-fbank|v1.22.3|b09e686fe2084732ddd30d1ef80acfc0f13eaf01"
  "kaldifst|https://github.com/k2-fsa/kaldifst|v1.8.0|ab5bdd013bdf13921e6aeee77db5722ebf9955fb"
  "kissfft|https://github.com/mborgerding/kissfft|febd4caeed32e33ad8b2e0bb5ea77542c40f18ec|febd4caeed32e33ad8b2e0bb5ea77542c40f18ec"
  "openfst|https://github.com/csukuangfj/openfst|v1.8.5-2026-07-09|f702a6317d600ac953fdbb4978674c3a28b6115c"
  "simple-sentencepiece|https://github.com/pkufool/simple-sentencepiece|v0.7|62dd423df1d51da5ea06f1c3a046fc04f01b4f39"
)

# NDK r29 (29.0.14206865). Google's SDK repository lists its SHA-1 as
# 87e2bb7e9be5d6a1c6cdf5ec40dd4e0c6d07c30b; the SHA-256 is of that file.
NDK_REVISION=29.0.14206865
NDK_ZIP=android-ndk-r29-linux.zip
NDK_SHA256=4abbbcdc842f3d4879206e9695d52709603e52dd68d3c1fff04b3b5e7a308ecf

# The official ONNX Runtime Android package from Maven Central (MIT). The
# app gets the same AAR through Gradle (gradle/verification-metadata.xml
# pins the same hash), so the library linked against here is the one the
# app ships.
ORT_VERSION=1.28.0
ORT_AAR_NAME=onnxruntime-android-$ORT_VERSION.aar
ORT_URL=https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/$ORT_VERSION/$ORT_AAR_NAME
ORT_SHA256=f351a0638696f54b35184290dbc001d66daae17281ad0b548d2c70347d53b8a9

# 64-bit: phones (arm64-v8a) and the emulator (x86_64). 32-bit: old and
# budget phones and Android Go (armeabi-v7a), and the 32-bit emulator image
# that tests them (x86). The 32-bit APKs have no on-device assistant (its
# LiteRT-LM is 64-bit only) but keep speech recognition and voice detection.
ALL_ABIS=(arm64-v8a x86_64 armeabi-v7a x86)
ANDROID_API=26   # the app's minSdk

# The Kotlin API files the app uses (speech to text, voice detection) and
# the ones they refer to. Nothing for text to speech.
KOTLIN_API=(OfflineRecognizer.kt OfflineStream.kt FeatureConfig.kt HomophoneReplacerConfig.kt QnnConfig.kt Vad.kt)

# `source build-native.sh --pins` reads the pins above and stops here.
if [ "${1:-}" = "--pins" ]; then return 0 2>/dev/null || exit 0; fi

# ── Inputs ───────────────────────────────────────────────────────────────
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
die() { echo "build-native.sh: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "needs $1"; }
for t in cmake ninja unzip tar sha256sum find; do need "$t"; done

NDK=${ANDROID_NDK:-${ANDROID_NDK_HOME:-}}
[ -n "$NDK" ] || die "set ANDROID_NDK (or ANDROID_NDK_HOME) to an NDK r29"
NDK=$(cd "$NDK" && pwd -P)
ndk_rev=$(sed -n 's/^Pkg\.Revision *= *//p' "$NDK/source.properties" 2>/dev/null | tr -d '\r ')
[ "$ndk_rev" = "$NDK_REVISION" ] || die "the NDK at $NDK is ${ndk_rev:-unknown}, not $NDK_REVISION (r29)"
LLVM=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
[ -x "$LLVM/clang" ] || die "no linux-x86_64 clang in $NDK"

abis_list=${ABIS:-${ALL_ABIS[*]}}
read -r -a abis <<< "${abis_list//,/ }"
[ "${#abis[@]}" -gt 0 ] || die "ABIS is empty"
for abi in "${abis[@]}"; do
  case " ${ALL_ABIS[*]} " in *" $abi "*) ;; *) die "unknown ABI $abi (one of: ${ALL_ABIS[*]})" ;; esac
done

# SHERPA_OUT and SHERPA_WORK only; a generic OUT or WORK from the caller's
# environment is not read (see Environment above).
[ -z "${OUT:-}" ] || echo "   note: OUT is set ($OUT) and ignored; this script writes to SHERPA_OUT (default: $here)"
[ -z "${WORK:-}" ] || echo "   note: WORK is set ($WORK) and ignored; this script's scratch is SHERPA_WORK (default: /work)"
OUT=${SHERPA_OUT:-$here}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd -P)

WORK_CANONICAL=/work
WORK=${SHERPA_WORK:-$WORK_CANONICAL}
mkdir -p "$WORK" 2>/dev/null && [ -w "$WORK" ] \
  || die "cannot write to $WORK (create it first: sudo install -d -m 1777 $WORK)"
WORK=$(cd "$WORK" && pwd -P)
if [ "$WORK" != "$WORK_CANONICAL" ]; then
  echo "   note: SHERPA_WORK is $WORK, not $WORK_CANONICAL: the build IDs will differ from SHA256SUMS"
fi
# Only what this script makes there is cleared (clone and copy_tree below
# clear their own targets).
rm -rf "$WORK/ort" "$WORK"/build-* "$WORK"/configure-*.log

# A git checkout given as a source must be at its pinned commit.
check_commit() {  # dir commit what
  [ -e "$1/.git" ] || { echo "   $3: $1 (not a git checkout; commit not checked)"; return 0; }
  need git
  local head
  head=$(git -c safe.directory='*' -C "$1" rev-parse HEAD) || die "cannot read $3's commit in $1"
  [ "$head" = "$2" ] || die "$3 in $1 is at $head, not the pinned $2"
  echo "   $3: $1 at $2"
}
# Clone a pinned commit (by its tag, or the commit itself) into $3.
clone() {  # repo ref dir commit
  need git
  rm -rf "$3"
  git init -q "$3"
  git -C "$3" fetch -q --depth 1 "$1" "$2"
  git -C "$3" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(git -C "$3" rev-parse HEAD)" = "$4" ] || die "$1 $2 is no longer commit $4"
}
# Copy a source tree without its .git.
copy_tree() {  # from to
  rm -rf "$2"
  mkdir -p "$2"
  tar -C "$1" --exclude=./.git -cf - . | tar -C "$2" -xf -
}

echo "== sources"
if [ -n "${SHERPA_SRC:-}" ]; then
  SHERPA_SRC=$(cd "$SHERPA_SRC" && pwd -P)
  check_commit "$SHERPA_SRC" "$SHERPA_COMMIT" sherpa-onnx
  [ "$SHERPA_SRC" = "$WORK/sherpa" ] || copy_tree "$SHERPA_SRC" "$WORK/sherpa"
else
  clone "$SHERPA_REPO" "$SHERPA_COMMIT" "$WORK/sherpa" "$SHERPA_COMMIT"
  echo "   sherpa-onnx: cloned $SHERPA_TAG ($SHERPA_COMMIT)"
fi

dep_dirs=()
for d in "${DEPS[@]}"; do
  IFS='|' read -r name repo ref commit <<< "$d"
  var=SHERPA_DEP_$(echo "$name" | tr 'a-z-' 'A-Z_')
  src=${!var:-}
  if [ -n "$src" ]; then
    src=$(cd "$src" && pwd -P)
    check_commit "$src" "$commit" "$name"
  else
    src=$WORK/src/$name
    clone "$repo" "$ref" "$src" "$commit"
    echo "   $name: cloned $ref ($commit)"
  fi
  dep_dirs+=("$name|$src")
done

echo "== ONNX Runtime $ORT_VERSION"
if [ -z "${ORT_AAR:-}" ]; then
  ORT_AAR=$WORK/$ORT_AAR_NAME
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$ORT_AAR" "$ORT_URL"
  else
    need wget
    wget -q -O "$ORT_AAR" "$ORT_URL"
  fi
fi
echo "$ORT_SHA256  $ORT_AAR" | sha256sum -c --quiet - || die "$ORT_AAR is not $ORT_AAR_NAME"
rm -rf "$WORK/ort"
mkdir -p "$WORK/ort"
unzip -q "$ORT_AAR" 'headers/*' 'jni/*/libonnxruntime.so' -d "$WORK/ort"
echo "   $ORT_AAR_NAME: sha256 ok"

# ── Build ────────────────────────────────────────────────────────────────
# -ffile-prefix-map keeps WORK out of the binaries (sherpa-onnx's log lines
# carry __FILE__), so the output does not depend on where it was built.
# The NDK's own path is in the debug info of the unstripped link (its
# headers), and so in the build ID the linker hashes from it, which
# survives stripping: it is mapped to where build-in-container.sh unpacks
# the NDK, so an NDK anywhere else gives the same build ID.
NDK_CANONICAL=/opt/android-ndk-r29
prefix_maps="-ffile-prefix-map=$WORK/= -ffile-prefix-map=$NDK=$NDK_CANONICAL"
fc_args=()
for d in "${DEPS[@]}"; do
  IFS='|' read -r name _ <<< "$d"
  fc_args+=("-DFETCHCONTENT_SOURCE_DIR_$(echo "$name" | tr 'a-z' 'A-Z')=@BUILD@/_deps/$name-src")
done
for abi in "${abis[@]}"; do
  echo "== build $abi"
  build=$WORK/build-$abi
  rm -rf "$build"
  # Where FetchContent would have unpacked each one (sherpa-onnx's CMake
  # patches simple-sentencepiece there, so the given trees stay untouched).
  for dd in "${dep_dirs[@]}"; do
    IFS='|' read -r name src <<< "$dd"
    copy_tree "$src" "$build/_deps/$name-src"
  done
  SHERPA_ONNXRUNTIME_LIB_DIR=$WORK/ort/jni/$abi \
  SHERPA_ONNXRUNTIME_INCLUDE_DIR=$WORK/ort/headers \
  cmake -S "$WORK/sherpa" -B "$build" -G Ninja \
    "${fc_args[@]//@BUILD@/$build}" \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$abi" \
    -DANDROID_PLATFORM="android-$ANDROID_API" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="$prefix_maps" \
    -DCMAKE_CXX_FLAGS="$prefix_maps" \
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
    > "$WORK/configure-$abi.log" 2>&1 || { tail -40 "$WORK/configure-$abi.log"; exit 1; }
  # It must link the ONNX Runtime unpacked above, never download its own,
  # and want no source beyond those above (FETCHCONTENT_FULLY_DISCONNECTED
  # already stops it downloading one).
  if grep -q "Downloading onnxruntime" "$WORK/configure-$abi.log"; then
    die "sherpa-onnx tried to download its own ONNX Runtime"
  fi
  # sherpa-onnx turns on link-time optimisation (ThinLTO) when CMake's
  # check_ipo_supported passes. With CMake 3.25 (Debian bookworm's 3.25.1)
  # that check's test project loses the NDK settings and links with
  # -fuse-ld=gold, which NDK r29 does not have, so it fails and the library
  # comes out different (no LTO). 3.28.3 and 3.31.6 pass it.
  grep -q "IPO is enabled" "$WORK/configure-$abi.log" \
    || die "CMake $(cmake --version | head -1 | awk '{print $3}') turned off sherpa-onnx's link-time optimisation; use CMake 3.28 or later"
  extra=$(cd "$build/_deps" && find . -maxdepth 1 -name '*-src' | sed 's#^\./##; s#-src$##' | \
    grep -v -x -F "$(printf '%s\n' "${DEPS[@]}" | cut -d'|' -f1)" || true)
  [ -z "$extra" ] || die "sherpa-onnx's CMake wants a source not pinned here: $extra"
  cmake --build "$build" --target sherpa-onnx-jni -j "$(nproc)" > "$WORK/build-$abi.log" 2>&1 \
    || { tail -40 "$WORK/build-$abi.log"; exit 1; }
  mkdir -p "$OUT/jniLibs/$abi"
  "$LLVM/llvm-strip" --strip-unneeded -o "$OUT/jniLibs/$abi/libsherpa-onnx-jni.so" \
    "$(find "$build" -name libsherpa-onnx-jni.so -type f | head -1)"
done

# ── Kotlin API and licences ─────────────────────────────────────────────
rm -rf "$OUT/kotlin/com/k2fsa/sherpa/onnx" "$OUT/licenses"
mkdir -p "$OUT/kotlin/com/k2fsa/sherpa/onnx" "$OUT/licenses"
for f in "${KOTLIN_API[@]}"; do
  cp "$WORK/sherpa/sherpa-onnx/kotlin-api/$f" "$OUT/kotlin/com/k2fsa/sherpa/onnx/"
done
cp "$WORK/sherpa/LICENSE" "$OUT/LICENSE"
# Every licence file at the top of each compiled-in dependency (Eigen, for
# one, has several). Every ABI uses the same set.
for src in "$WORK/build-${abis[0]}"/_deps/*-src; do
  dep=$(basename "$src" -src)
  mkdir -p "$OUT/licenses/$dep"
  find "$src" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
    -exec cp {} "$OUT/licenses/$dep/" \;
  if [ -z "$(ls -A "$OUT/licenses/$dep")" ]; then echo "(no licence file found)" > "$OUT/licenses/$dep/NONE"; fi
done

# ── Checks ───────────────────────────────────────────────────────────────
# Nothing of the text-to-speech stack may be in the output. These strings
# come from espeak-ng's own code (its data files, its environment variable,
# its temp files and messages), piper-phonemize and sherpa-onnx's TTS JNI;
# the release AAR's library holds 95 of them. (Case matters: a plain
# case-blind "espeak" also matches sherpa-onnx's "OfflineSpeakerDiarization"
# and "wespeaker".)
TTS_MARKERS='espeak-ng|eSpeak-ng|ESPEAK_DATA_PATH|espeakXXXXXX|phontab|phonindex|phondata|piper-phonemize|PiperPhonemes|OfflineTts'
for abi in "${abis[@]}"; do
  so=$OUT/jniLibs/$abi/libsherpa-onnx-jni.so
  # Every ABI asked for has its library, or the build fails here (not at
  # an APK comparison much later).
  [ -s "$so" ] || die "no library at $so after the build"
  if grep -a -q -E "$TTS_MARKERS" "$so"; then
    echo "text-to-speech code found in $so:" >&2
    grep -a -o -E "$TTS_MARKERS" "$so" | sort | uniq -c >&2
    exit 1
  fi
done

echo "== done (cmake $(cmake --version | head -1 | awk '{print $3}'), ninja $(ninja --version))"
if [ "$OUT" != "$(cd "$here" && pwd -P)" ]; then
  echo "   note: the libraries are in $OUT (SHERPA_OUT), not in $here where the app's Gradle build reads them"
fi
for abi in "${abis[@]}"; do
  rel=jniLibs/$abi/libsherpa-onnx-jni.so
  sum=$(sha256sum "$OUT/$rel" | cut -d' ' -f1)
  want=$(awk -v f="$rel" '$2 == f {print $1}' "$here/SHA256SUMS" 2>/dev/null || true)
  if [ "$sum" = "$want" ]; then verdict="same as SHA256SUMS"; else verdict="DIFFERS from SHA256SUMS (${want:-none})"; fi
  echo "   $rel $sum: $verdict"
done
