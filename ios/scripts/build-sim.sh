#!/usr/bin/env bash
# build-sim.sh — generate the Xcode project and build the app for the
# Simulator. No signing, no device, no account.
#
# Runs ON THE MAC, as the build user. From this PC:
#   ./remote.sh -- domovoi/ios/scripts/build-sim.sh
set -euo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say "build-sim.sh: $*"; exit 1; }

usage() {
	cat <<'USAGE'
usage: build-sim.sh [--destination DEST] [--configuration CONFIG] [--help]

Runs `xcodegen generate` and then `xcodebuild build` for the iOS Simulator,
with CODE_SIGNING_ALLOWED=NO. Agents never sign anything.

Options
  --destination DEST     an xcodebuild -destination string. The default is
                         generic, so it needs no simulator device to exist.
                         Default: "generic/platform=iOS Simulator"
  --configuration CONFIG Debug or Release. Default: Debug.
  --help                 this text.

Environment
  DOMOVOI_SIM_DESTINATION   same as --destination.
  DOMOVOI_DERIVED_DATA      where to put build products.
                            Default: ~/Library/Developer/Xcode/DerivedData
USAGE
}

# `generic/platform=iOS Simulator` and NOT a device name, because this
# script only ever BUILDS. A bare `name=iPhone 16` is resolved by
# xcodebuild as `OS:latest`, so the moment Xcode gains a newer runtime
# than that device has a simulator for, the build fails with "Unable to
# find a device matching the provided destination specifier" and names a
# device the list plainly shows (measured 2026-11-02: iPhone 16 exists at
# 18.3.1, the newest runtime was 26.4, and the two never meet). A generic
# destination needs no device at all, which is what a compile check wants.
# `test.sh` and `stub_run.sh` do need a real one, and `stub_run.sh`
# resolves a UDID by name for exactly this reason.
destination="${DOMOVOI_SIM_DESTINATION:-generic/platform=iOS Simulator}"
configuration="Debug"

while [ $# -gt 0 ]; do
	case "$1" in
		--help|-h) usage; exit 0 ;;
		--destination) [ $# -ge 2 ] || die "--destination needs a value"; destination="$2"; shift 2 ;;
		--destination=*) destination="${1#--destination=}"; shift ;;
		--configuration) [ $# -ge 2 ] || die "--configuration needs a value"; configuration="$2"; shift 2 ;;
		--configuration=*) configuration="${1#--configuration=}"; shift ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

case "$configuration" in
	Debug|Release) ;;
	*) die "--configuration takes Debug or Release, not '$configuration'" ;;
esac

ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ios_dir"

# Homebrew is not on a non-interactive ssh PATH (which is /usr/bin:/bin:
# /usr/sbin:/sbin and nothing else), and the build user's profile does not add
# it either — so `xcodegen` installed by Homebrew looked uninstalled to every
# `ssh ... build-sim.sh`. Look where Homebrew puts things before giving up.
if ! command -v xcodegen >/dev/null 2>&1; then
	for brew_bin in /opt/homebrew/bin /usr/local/bin; do
		if [ -x "$brew_bin/xcodegen" ]; then
			PATH="$brew_bin:$PATH"
			export PATH
			break
		fi
	done
fi
command -v xcodegen >/dev/null 2>&1 || die "xcodegen is not installed. On the Mac: brew install xcodegen"
command -v xcodebuild >/dev/null 2>&1 || die "xcodebuild is not on PATH. Xcode is not set up for this user yet; see design-notes/IOS-SESSION-START.md, part A2."

say "build-sim.sh: generating Domovoi.xcodeproj from project.yml"
xcodegen generate --spec project.yml --project .

args="-project Domovoi.xcodeproj -scheme Domovoi -configuration $configuration"
if [ -n "${DOMOVOI_DERIVED_DATA:-}" ]; then
	args="$args -derivedDataPath $DOMOVOI_DERIVED_DATA"
fi

say "build-sim.sh: building $configuration for $destination"
# shellcheck disable=SC2086
xcodebuild build $args -destination "$destination" CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO

say "build-sim.sh: built"
