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
  --destination DEST     an xcodebuild -destination string.
                         Default: "platform=iOS Simulator,name=iPhone 16"
  --configuration CONFIG Debug or Release. Default: Debug.
  --help                 this text.

Environment
  DOMOVOI_SIM_DESTINATION   same as --destination.
  DOMOVOI_DERIVED_DATA      where to put build products.
                            Default: ~/Library/Developer/Xcode/DerivedData
USAGE
}

destination="${DOMOVOI_SIM_DESTINATION:-platform=iOS Simulator,name=iPhone 16}"
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
