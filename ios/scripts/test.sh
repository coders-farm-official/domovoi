#!/usr/bin/env bash
# test.sh — the whole iOS test run: the packages on the Mac itself, then the
# app and the UI tests in a Simulator, then shut the Simulator down.
#
# Runs ON THE MAC, as the build user. From this PC:
#   ./remote.sh -- domovoi/ios/scripts/test.sh
#
# The split matters (plan 5.1): `swift test` needs no screen and no Simulator,
# so the logic tests are fast and reliable over SSH. The Simulator wants the
# build user logged in at the Mac's screen; Apple calls running screen-based
# code from an SSH session a bad idea, and a LaunchAgent in that session is
# the fallback if it proves flaky. --packages-only is there for when the
# Simulator is the problem and the logic is what you need.
set -euo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say "test.sh: $*"; exit 1; }

usage() {
	cat <<'USAGE'
usage: test.sh [--packages-only] [--app-only] [--destination DEST] [--keep-simulator] [--help]

Runs, in this order:
  1. `swift test` in ios/Packages/DomovoiKit (and any other package with
     tests) — on the Mac itself, no Simulator;
  2. `xcodebuild test` for Domovoi, DomovoiTests and DomovoiUITests on a
     Simulator destination, with CODE_SIGNING_ALLOWED=NO;
  3. `xcrun simctl shutdown all`, so nothing is left running on Kamron's Mac.

Options
  --packages-only    stop after step 1.
  --app-only         skip step 1.
  --destination DEST an xcodebuild -destination string.
                     Default: "platform=iOS Simulator,name=iPhone 16"
  --keep-simulator   do not shut the Simulator down at the end.
  --help             this text.

Environment
  DOMOVOI_SIM_DESTINATION   same as --destination.
  DOMOVOI_DERIVED_DATA      where to put build products.
USAGE
}

destination="${DOMOVOI_SIM_DESTINATION:-platform=iOS Simulator,name=iPhone 16}"
packages=1
app=1
shutdown_at_end=1

while [ $# -gt 0 ]; do
	case "$1" in
		--help|-h) usage; exit 0 ;;
		--packages-only) app=0; shift ;;
		--app-only) packages=0; shift ;;
		--keep-simulator) shutdown_at_end=0; shift ;;
		--destination) [ $# -ge 2 ] || die "--destination needs a value"; destination="$2"; shift 2 ;;
		--destination=*) destination="${1#--destination=}"; shift ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ios_dir"

command -v xcodebuild >/dev/null 2>&1 || die "xcodebuild is not on PATH. Xcode is not set up for this user yet; see design-notes/IOS-SESSION-START.md, part A2."

if [ "$packages" -eq 1 ]; then
	for package in Packages/*/; do
		name="$(basename "$package")"
		if [ ! -d "$package/Tests" ]; then
			say "test.sh: $name has no tests yet, skipping"
			continue
		fi
		say "test.sh: swift test in $name"
		( cd "$package" && swift test )
	done
fi

if [ "$app" -eq 1 ]; then
	command -v xcodegen >/dev/null 2>&1 || die "xcodegen is not installed. On the Mac: brew install xcodegen"
	say "test.sh: generating Domovoi.xcodeproj from project.yml"
	xcodegen generate --spec project.yml --project .

	args="-project Domovoi.xcodeproj -scheme Domovoi -configuration Debug"
	if [ -n "${DOMOVOI_DERIVED_DATA:-}" ]; then
		args="$args -derivedDataPath $DOMOVOI_DERIVED_DATA"
	fi

	status=0
	say "test.sh: xcodebuild test on $destination"
	# shellcheck disable=SC2086
	xcodebuild test $args -destination "$destination" CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO || status=$?

	if [ "$shutdown_at_end" -eq 1 ]; then
		say "test.sh: shutting the Simulator down"
		xcrun simctl shutdown all || true
	fi

	[ "$status" -eq 0 ] || exit "$status"
fi

say "test.sh: green"
