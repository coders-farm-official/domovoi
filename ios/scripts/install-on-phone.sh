#!/usr/bin/env bash
# install-on-phone.sh — the owner's script. Builds Domovoi with HIS signing and
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
  carries the throwaway bundle ID org.codersfarm.domovoi.dev — never
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
	die "this is the owner's script, and you are the build user (domovoibuild).
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

# find_device prints the UDID of the one iPhone or iPad that is paired with
# this Mac and reachable now (plugged in, or paired over Wi-Fi and in range).
# It reads devicectl's JSON, not its table: the table's columns and state words
# change between Xcode versions (Xcode 26 says "available (paired)", never
# "connected"). It prints the hardware UDID, which both xcodebuild's
# -destination and devicectl accept; the table's Identifier column is the
# CoreDevice id, which devicectl accepts but xcodebuild does not.
#
# Exit status: 0 with one UDID on stdout, 2 when none is reachable, 3 when
# more than one is (they are listed on stderr).
find_device() {
	local json status=0
	json="$(mktemp -t domovoi-devices)" || return 2
	if ! xcrun devicectl list devices --json-output "$json" >/dev/null 2>&1; then
		rm -f "$json"
		return 2
	fi
	/usr/bin/python3 -I - "$json" <<'PY' || status=$?
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        devices = (json.load(f).get("result") or {}).get("devices") or []
except (OSError, ValueError, AttributeError):
    sys.exit(2)  # unreadable answer: the caller shows devicectl's own table

found = []
for d in devices:
    hw = d.get("hardwareProperties") or {}
    conn = d.get("connectionProperties") or {}
    props = d.get("deviceProperties") or {}
    if hw.get("platform") != "iOS" or hw.get("reality") != "physical":
        continue
    if conn.get("pairingState") != "paired" or not hw.get("udid"):
        continue
    # "unavailable": devicectl cannot reach it now (unplugged, or out of
    # Wi-Fi range). "disconnected" only means no tunnel is open yet; the
    # build opens one.
    if conn.get("tunnelState") == "unavailable":
        continue
    model = hw.get("marketingName") or hw.get("deviceType") or "?"
    found.append((hw["udid"], props.get("name") or "?", model))

if not found:
    sys.exit(2)
if len(found) > 1:
    for udid, name, model in found:
        print(f"  {udid}  {name} ({model})", file=sys.stderr)
    sys.exit(3)
print(found[0][0])
PY
	rm -f "$json"
	return "$status"
}

if [ -z "$device" ]; then
	say "install-on-phone.sh: looking for a connected iPhone"
	find_status=0
	device="$(find_device)" || find_status=$?
	if [ "$find_status" -eq 3 ]; then
		die "more than one iPhone or iPad is reachable (listed above). Pick one
with --device UDID."
	fi
	[ -z "$device" ] || say "install-on-phone.sh: found $device"
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
