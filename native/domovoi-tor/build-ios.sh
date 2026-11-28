#!/usr/bin/env bash
# Builds away chat's Tor library (Arti, an embedded onion client, plus the
# dtor_* C calls the app makes) as static libraries for the iPhone and the
# Simulator, and packages them with the C header as
# ios/Vendor/domovoi-tor/DomovoiTor.xcframework, which DomovoiKit links
# (its Package.swift, binary target DomovoiTor). See README.md beside this
# file.
#
#   bash native/domovoi-tor/build-ios.sh            # build, compare with SHA256SUMS
#   bash native/domovoi-tor/build-ios.sh --install  # ...and replace the committed files
#
# Environment:
#   DOMOVOI_TOR_IOS_OUT  where DomovoiTor.xcframework, SHA256SUMS and
#                        BUILD-INFO.txt go (default: ios/Vendor/domovoi-tor)
#
# Runs on a Mac with Xcode (xcodebuild -create-xcframework, lipo).
set -euo pipefail

# ── Pins ─────────────────────────────────────────────────────────────────
RUST_VERSION=1.100.1          # rustc and cargo, exactly (as build-android.sh)
# The app's deployment target (ios/project.yml): the C code built into the
# library (SQLite, ring) and Rust's own objects say this minimum, so the
# app's link has nothing newer in it than the app itself.
IOS_DEPLOYMENT_TARGET=18.0
DEVICE_TARGET=aarch64-apple-ios
# Simulator slice: both Mac architectures, as the app's Simulator builds
# are (and as ONNX Runtime's xcframework is).
SIM_TARGETS=(aarch64-apple-ios-sim x86_64-apple-ios)
# A fixed time for anything that stamps one.
export SOURCE_DATE_EPOCH=1764201600   # 2025-11-27T00:00:00Z
export ZERO_AR_DATE=1                 # Apple's ar/libtool: no member dates

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd "$here/../.." && pwd -P)
out=${DOMOVOI_TOR_IOS_OUT:-$repo/ios/Vendor/domovoi-tor}
install=no
[ "${1:-}" = "--install" ] && install=yes
die() { echo "build-ios.sh: $*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || die "needs a Mac with Xcode"
rustc --version | grep -q "^rustc $RUST_VERSION " || die "needs rustc $RUST_VERSION (rustup toolchain install $RUST_VERSION; found: $(rustc --version))"
command -v xcodebuild >/dev/null || die "needs Xcode (xcodebuild)"
grep -q "IPHONEOS_DEPLOYMENT_TARGET: \"$IOS_DEPLOYMENT_TARGET\"" "$repo/ios/project.yml" \
  || die "ios/project.yml's IPHONEOS_DEPLOYMENT_TARGET is not $IOS_DEPLOYMENT_TARGET: change the pin here to match"
installed=$(rustup target list --installed 2>/dev/null || true)
for t in "$DEVICE_TARGET" "${SIM_TARGETS[@]}"; do
  grep -qx "$t" <<< "$installed" || die "needs the Rust target $t (rustup target add $t)"
done

cargo_home=${CARGO_HOME:-$HOME/.cargo}
# Reproducible: no build path or home folder in the library.
export RUSTFLAGS="--remap-path-prefix=$here=/domovoi-tor --remap-path-prefix=$cargo_home=/cargo"
# The same for the C compiler cc-rs runs (SQLite, ring's C and assembly).
export CFLAGS="-ffile-prefix-map=$here=/domovoi-tor -ffile-prefix-map=$cargo_home=/cargo"
export IPHONEOS_DEPLOYMENT_TARGET=$IOS_DEPLOYMENT_TARGET
export CARGO_TARGET_DIR=$here/target

cd "$here"
built=$(mktemp -d)
trap 'rm -rf "$built"' EXIT

# Only the static library (cargo rustc --crate-type): the crate's cdylib is
# Android's, and would be a second LTO pass for nothing. A static library
# isn't linked, so the profile's `strip` doesn't reach it: strip -S -x takes
# the debug info and the local symbols out here (about a third of its size;
# the app's own link and Xcode's strip would drop them anyway). What it needs
# from the system is in DomovoiKit's Package.swift (linkerSettings), from
# rustc's native-static-libs note printed below.
lib() {
  echo "== $1"
  cargo rustc --release --locked --lib --target "$1" --crate-type staticlib -- --print native-static-libs 2> "$built/$1.log" \
    || { cat "$built/$1.log" >&2; die "cargo failed for $1"; }
  grep 'native-static-libs\|Finished' "$built/$1.log" || true
  mkdir -p "$built/$1"
  xcrun strip -S -x "$CARGO_TARGET_DIR/$1/release/libdomovoi_tor.a" -o "$built/$1/libdomovoi_tor.a" 2>/dev/null
}

mkdir -p "$built/ios" "$built/sim"
lib "$DEVICE_TARGET"
cp "$built/$DEVICE_TARGET/libdomovoi_tor.a" "$built/ios/"
sims=()
for t in "${SIM_TARGETS[@]}"; do
  lib "$t"
  sims+=("$built/$t/libdomovoi_tor.a")
done
lipo -create "${sims[@]}" -output "$built/sim/libdomovoi_tor.a"

xcodebuild -create-xcframework \
  -library "$built/ios/libdomovoi_tor.a" -headers "$here/include" \
  -library "$built/sim/libdomovoi_tor.a" -headers "$here/include" \
  -output "$built/DomovoiTor.xcframework" >/dev/null
# xcodebuild lists the two slices in either order from one run to the next;
# written out here, in a fixed one, so the bytes don't change.
cat > "$built/DomovoiTor.xcframework/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AvailableLibraries</key>
	<array>
		<dict>
			<key>BinaryPath</key>
			<string>libdomovoi_tor.a</string>
			<key>HeadersPath</key>
			<string>Headers</string>
			<key>LibraryIdentifier</key>
			<string>ios-arm64</string>
			<key>LibraryPath</key>
			<string>libdomovoi_tor.a</string>
			<key>SupportedArchitectures</key>
			<array>
				<string>arm64</string>
			</array>
			<key>SupportedPlatform</key>
			<string>ios</string>
		</dict>
		<dict>
			<key>BinaryPath</key>
			<string>libdomovoi_tor.a</string>
			<key>HeadersPath</key>
			<string>Headers</string>
			<key>LibraryIdentifier</key>
			<string>ios-arm64_x86_64-simulator</string>
			<key>LibraryPath</key>
			<string>libdomovoi_tor.a</string>
			<key>SupportedArchitectures</key>
			<array>
				<string>arm64</string>
				<string>x86_64</string>
			</array>
			<key>SupportedPlatform</key>
			<string>ios</string>
			<key>SupportedPlatformVariant</key>
			<string>simulator</string>
		</dict>
	</array>
	<key>CFBundlePackageType</key>
	<string>XFWK</string>
	<key>XCFrameworkFormatVersion</key>
	<string>1.0</string>
</dict>
</plist>
PLIST

sums() { ( cd "$1" && find DomovoiTor.xcframework -type f | LC_ALL=C sort | xargs shasum -a 256 ); }
sums "$built" > "$built/SHA256SUMS"
cat "$built/SHA256SUMS"
( cd "$built" && find DomovoiTor.xcframework -name '*.a' | LC_ALL=C sort | while read -r a; do
    printf '%s: %s bytes (%s)\n' "$a" "$(stat -f %z "$a")" "$(lipo -archs "$a")"
  done )

if [ "$install" = yes ]; then
  mkdir -p "$out"
  rm -rf "$out/DomovoiTor.xcframework"
  cp -R "$built/DomovoiTor.xcframework" "$out/"
  cp "$built/SHA256SUMS" "$out/SHA256SUMS"
  {
    echo "DomovoiTor.xcframework (libdomovoi_tor.a), built by native/domovoi-tor/build-ios.sh"
    echo "rustc: $(rustc --version)"
    echo "Xcode: $(xcodebuild -version | tr '\n' ' ' | sed 's/ $//')"
    echo "arti-client: $(grep -A1 '^name = "arti-client"' Cargo.lock | sed -n 's/^version = "\(.*\)"/\1/p')"
    echo "targets: $DEVICE_TARGET; ${SIM_TARGETS[*]} (one universal Simulator library)"
    echo "IPHONEOS_DEPLOYMENT_TARGET: $IOS_DEPLOYMENT_TARGET"
  } > "$out/BUILD-INFO.txt"
  echo "installed into $out"
elif [ -f "$out/SHA256SUMS" ]; then
  if diff "$out/SHA256SUMS" "$built/SHA256SUMS"; then
    echo "same as the committed xcframework"
  else
    die "differs from $out/SHA256SUMS"
  fi
fi
