#!/usr/bin/env bash
# Builds away chat's Tor library (libdomovoi_tor.so: Arti, an embedded onion
# client, plus the JNI calls the app makes) for every ABI the Android app
# ships, into android/native/domovoi-tor/jniLibs/<abi>/, and writes the
# SHA256SUMS beside them. F-Droid runs this from its build line; see
# README.md beside this file.
#
#   bash native/domovoi-tor/build-android.sh            # build, compare with SHA256SUMS
#   bash native/domovoi-tor/build-android.sh --install  # ...and replace the committed files
#
# Environment:
#   ANDROID_NDK_HOME  the NDK below (default: $ANDROID_HOME/ndk/<revision>,
#                     or ~/Library/Android/sdk/ndk/<revision>)
#   ABIS              which ABIs (default: all four the app ships)
#   DOMOVOI_TOR_OUT   where jniLibs/<abi>/ and SHA256SUMS go (default:
#                     android/native/domovoi-tor, what the app's Gradle reads)
set -euo pipefail

# ── Pins ─────────────────────────────────────────────────────────────────
RUST_VERSION=1.100.1          # rustc and cargo, exactly
CARGO_NDK_VERSION=4.1.2
NDK_REVISION=29.0.14206865    # NDK r29
ANDROID_API=26                # the app's minSdk
ALL_ABIS=(arm64-v8a armeabi-v7a x86_64 x86)
# A fixed time for anything that stamps one.
export SOURCE_DATE_EPOCH=1764201600   # 2025-11-27T00:00:00Z

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd "$here/../.." && pwd -P)
out=${DOMOVOI_TOR_OUT:-$repo/android/native/domovoi-tor}
install=no
[ "${1:-}" = "--install" ] && install=yes
die() { echo "build-android.sh: $*" >&2; exit 1; }

rustc --version | grep -q "^rustc $RUST_VERSION " || die "needs rustc $RUST_VERSION (rustup toolchain install $RUST_VERSION; found: $(rustc --version))"
cargo ndk --version | grep -q "^cargo-ndk $CARGO_NDK_VERSION" || die "needs cargo-ndk $CARGO_NDK_VERSION (cargo install cargo-ndk --version $CARGO_NDK_VERSION --locked)"
ndk=${ANDROID_NDK_HOME:-}
if [ -z "$ndk" ]; then
  for c in "${ANDROID_HOME:-}/ndk/$NDK_REVISION" "${ANDROID_SDK_ROOT:-}/ndk/$NDK_REVISION" "$HOME/Library/Android/sdk/ndk/$NDK_REVISION" "$HOME/Android/Sdk/ndk/$NDK_REVISION"; do
    [ -d "$c" ] && { ndk=$c; break; }
  done
fi
[ -n "$ndk" ] && [ -f "$ndk/source.properties" ] || die "needs NDK $NDK_REVISION (set ANDROID_NDK_HOME)"
grep -q "Pkg.Revision = $NDK_REVISION" "$ndk/source.properties" || die "$ndk is not NDK $NDK_REVISION"
export ANDROID_NDK_HOME=$ndk

read -r -a abis <<< "$(echo "${ABIS:-${ALL_ABIS[*]}}" | tr ',' ' ')"
cargo_home=${CARGO_HOME:-$HOME/.cargo}
# Reproducible: no build path or home folder in the library, 16 KB pages
# for the 64-bit ones (Android 15+; the app's checkNativeAlignment).
export RUSTFLAGS="--remap-path-prefix=$here=/domovoi-tor --remap-path-prefix=$cargo_home=/cargo -C link-arg=-Wl,-z,max-page-size=16384"
export CARGO_TARGET_DIR=$here/target

cd "$here"
built=$(mktemp -d)
trap 'rm -rf "$built"' EXIT
for abi in "${abis[@]}"; do
  case "$abi" in
    arm64-v8a) triple=aarch64-linux-android ;;
    armeabi-v7a) triple=armv7-linux-androideabi ;;
    x86_64) triple=x86_64-linux-android ;;
    x86) triple=i686-linux-android ;;
    *) die "unknown ABI $abi" ;;
  esac
  echo "== $abi"
  cargo ndk --platform "$ANDROID_API" -t "$abi" build --release --locked --lib
  mkdir -p "$built/jniLibs/$abi"
  cp "$CARGO_TARGET_DIR/$triple/release/libdomovoi_tor.so" "$built/jniLibs/$abi/"
done

( cd "$built" && find jniLibs -name '*.so' | LC_ALL=C sort | xargs shasum -a 256 ) > "$built/SHA256SUMS"
cat "$built/SHA256SUMS"
if [ "$install" = yes ]; then
  mkdir -p "$out"
  for abi in "${abis[@]}"; do
    mkdir -p "$out/jniLibs/$abi"
    cp "$built/jniLibs/$abi/libdomovoi_tor.so" "$out/jniLibs/$abi/"
  done
  ( cd "$out" && find jniLibs -name '*.so' | LC_ALL=C sort | xargs shasum -a 256 ) > "$out/SHA256SUMS"
  {
    echo "libdomovoi_tor.so, built by native/domovoi-tor/build-android.sh"
    echo "rustc: $(rustc --version)"
    echo "cargo-ndk: $(cargo ndk --version | head -1)"
    echo "NDK: $NDK_REVISION"
    echo "arti-client: $(grep -A1 '^name = "arti-client"' Cargo.lock | sed -n 's/^version = "\(.*\)"/\1/p')"
    echo "API: $ANDROID_API"
  } > "$out/BUILD-INFO.txt"
  echo "installed into $out"
elif [ -f "$out/SHA256SUMS" ]; then
  if diff <(grep -E "$(printf '%s|' "${abis[@]}" | sed 's/|$//')" "$out/SHA256SUMS") "$built/SHA256SUMS"; then
    echo "same as the committed libraries"
  else
    die "differs from $out/SHA256SUMS"
  fi
fi
