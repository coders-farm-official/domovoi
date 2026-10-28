#!/usr/bin/env bash
# install-on-phone.sh — Kamron's script. Builds Domovoi with HIS signing and
# installs it on HIS iPhone.
#
# Run it in your own Mac account, in a Terminal window, with your iPhone
# plugged in (or paired over Wi-Fi). It is not run by any agent and it never
# runs as the build user: signing needs your keychain, your Apple ID and your
# team, and none of those are ever in this repository or in that account.
#
#   cd ~/domovoi && ios/scripts/install-on-phone.sh
#
# The first time, on the iPhone: Settings > Privacy & Security > Developer
# Mode, on. The phone restarts once.
set -euo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say ""; say "install-on-phone.sh: $*"; say ""; exit 1; }

usage() {
	cat <<'USAGE'
usage: install-on-phone.sh [--branch BRANCH] [--device UDID] [--no-pull] [--help]

Builds the Domovoi app for your iPhone with your own signing and installs it.

Options
  --branch BRANCH  fetch and check out this branch first. Default: whatever is
                   checked out now.
  --device UDID    install on this device. Default: the only connected iPhone,
                   or an error listing what is connected.
  --no-pull        do not fetch anything; build what is in the working tree.
  --help           this text.

Environment
  DOMOVOI_TEAM_ID   your Apple Developer team id, the ten-character one from
                    developer.apple.com > Membership. Required: automatic
                    signing cannot guess which team to use when an Apple ID
                    belongs to more than one.

What it does NOT do
  It holds no password, no certificate, no profile and no App Store Connect
  key. It asks Xcode to sign with whatever your account already has
  (-allowProvisioningUpdates), and it builds the Debug configuration, which
  carries the throwaway bundle ID org.codersfarm.domovoi.dev.kamron — never
  com.domovoi.app, so your personal team can never register the real id
  before the Coders Farm account does (plan 4.1).

  Releases are a separate step and also yours: archive, export, and upload
  with Xcode's Organizer or Transporter.
USAGE
}

branch=""
device=""
pull=1

while [ $# -gt 0 ]; do
	case "$1" in
		--help|-h) usage; exit 0 ;;
		--branch) [ $# -ge 2 ] || die "--branch needs a value"; branch="$2"; shift 2 ;;
		--branch=*) branch="${1#--branch=}"; shift ;;
		--device) [ $# -ge 2 ] || die "--device needs a value"; device="$2"; shift 2 ;;
		--device=*) device="${1#--device=}"; shift ;;
		--no-pull) pull=0; shift ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

# ---- this is not the build user's script ------------------------------------
#
# The build user has no Apple ID and no keychain to sign with, and signing over
# SSH fails on a locked keychain anyway (errSecInternalComponent). Better a
# plain sentence than that error.
me="$(id -un)"
if [ "$me" = "domovoibuild" ]; then
	die "this is Kamron's script, and you are the build user (domovoibuild).
Device builds are signed with his Apple ID in his own account. Switch to his
account at the Mac's screen and run it there. Agents build for the Simulator:
ios/scripts/build-sim.sh."
fi
if [ -n "${SSH_CONNECTION:-}" ]; then
	die "this is running over SSH. Signing needs an unlocked keychain in a
logged-in session, so run it in a Terminal window at the Mac itself."
fi

ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
repo_dir="$(cd "$ios_dir/.." && pwd)"

command -v xcodebuild >/dev/null 2>&1 || die "xcodebuild is not on PATH. Open Xcode once, then run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
command -v xcodegen >/dev/null 2>&1 || die "xcodegen is not installed. Run: brew install xcodegen"

team="${DOMOVOI_TEAM_ID:-}"
if [ -z "$team" ]; then
	die "DOMOVOI_TEAM_ID is not set. It is the ten-character team id from
developer.apple.com > Membership. Run it like this:

  DOMOVOI_TEAM_ID=ABCDE12345 ios/scripts/install-on-phone.sh"
fi

if [ "$pull" -eq 1 ]; then
	cd "$repo_dir"
	if [ -n "$branch" ]; then
		say "install-on-phone.sh: fetching $branch"
		git fetch origin "$branch"
		git checkout "$branch"
		git pull --ff-only origin "$branch"
	else
		say "install-on-phone.sh: pulling the current branch"
		git pull --ff-only
	fi
fi

cd "$ios_dir"
say "install-on-phone.sh: generating Domovoi.xcodeproj"
xcodegen generate --spec project.yml --project .

if [ -z "$device" ]; then
	say "install-on-phone.sh: looking for a connected iPhone"
	# `devicectl list devices` prints a table; the UDID is the first column of
	# a connected row. If this picks the wrong thing, pass --device.
	device="$(xcrun devicectl list devices 2>/dev/null \
		| awk '/connected/ && /iPhone/ {print $(NF-1); exit}')"
fi
if [ -z "$device" ]; then
	say ""
	say "install-on-phone.sh: no connected iPhone found. What devicectl sees:"
	xcrun devicectl list devices || true
	die "plug the phone in, unlock it, trust this Mac, and try again — or pass --device UDID."
fi

build_dir="$ios_dir/.build-device"
say "install-on-phone.sh: building Debug for $device"
xcodebuild build \
	-project Domovoi.xcodeproj \
	-scheme Domovoi \
	-configuration Debug \
	-destination "id=$device" \
	-derivedDataPath "$build_dir" \
	-allowProvisioningUpdates \
	CODE_SIGN_STYLE=Automatic \
	DEVELOPMENT_TEAM="$team"

app="$build_dir/Build/Products/Debug-iphoneos/Domovoi.app"
[ -d "$app" ] || die "the build finished but $app is not there. Read the build output above."

say "install-on-phone.sh: installing on $device"
xcrun devicectl device install app --device "$device" "$app"

say ""
say "install-on-phone.sh: installed. Open Domovoi on the phone."
say "If it will not open: Settings > General > VPN & Device Management, and"
say "trust your developer account. A free personal team's build lasts 7 days."
