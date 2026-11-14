#!/usr/bin/env bash
# release.sh — the owner's script. Archives Domovoi for the App Store with HIS
# signing, and exports or uploads it.
#
# Run it in your own Mac account, in a Terminal window, at the Mac itself. No
# agent runs it and it never runs as the build user: signing needs your
# keychain, your Apple ID and your team, and none of those are ever in this
# repository or in that account.
#
#   cd ~/domovoi && ios/scripts/release.sh                # export an .ipa
#   cd ~/domovoi && ios/scripts/release.sh --upload       # straight to App Store Connect
#
# What it checks before it builds anything: the working tree is clean, the
# version is three integers, and the build number is a YYMMDDNN above every
# build number used so far, iPhone or Android (one shared sequence, plan 4.4).
set -euo pipefail

say() { printf '%s\n' "$*" >&2; }
die() { say ""; say "release.sh: $*"; say ""; exit 1; }

usage() {
	cat <<'USAGE'
usage: release.sh [--build YYMMDDNN] [--upload] [--help]

Archives the Release configuration (bundle id com.domovoi.app) with your own
signing, then exports it for App Store Connect.

Options
  --build YYMMDDNN  the build number (CFBundleVersion). Default: today's date
                    and the next free counter, e.g. 26111501, then 26111502.
                    Refused unless it is above every build number used so far:
                    android/release.json, android/app/build.gradle.kts,
                    ios/project.yml and this Mac's own last release.
  --upload          send the build to App Store Connect (the Apple ID signed in
                    to Xcode > Settings > Accounts). Default: export an .ipa
                    to ios/.release/ and upload it yourself with Transporter.
  --help            this text.

Environment, or ios/release.local (one KEY=value per line, never committed)
  DOMOVOI_TEAM_ID   the Coders Farm team id, the ten-character one from
                    developer.apple.com > Membership. Required.

The version (CFBundleShortVersionString) is MARKETING_VERSION in
ios/project.yml, and is the iPhone's own: it does not follow Android's
versionName (owner decision, 2026-11-14). This script never changes it.

What it does NOT do
  It holds no password, no certificate, no profile and no App Store Connect
  key. It asks Xcode to sign with what your account already has
  (-allowProvisioningUpdates). It does not submit anything for review.
USAGE
}

build=""
destination="export"

while [ $# -gt 0 ]; do
	case "$1" in
		--help|-h) usage; exit 0 ;;
		--build) [ $# -ge 2 ] || die "--build needs a value"; build="$2"; shift 2 ;;
		--build=*) build="${1#--build=}"; shift ;;
		--upload) destination="upload"; shift ;;
		*) die "unknown option: $1 (try --help)" ;;
	esac
done

# ---- this is not the build user's script ------------------------------------
me="$(id -un)"
if [ "$me" = "domovoibuild" ]; then
	die "this is the owner's script, and you are the build user (domovoibuild).
Releases are signed with his Apple ID in his own account."
fi
if [ -n "${SSH_CONNECTION:-}" ]; then
	die "this is running over SSH. Signing needs an unlocked keychain in a
logged-in session, so run it in a Terminal window at the Mac itself."
fi

ios_dir="$(cd "$(dirname "$0")/.." && pwd)"
repo_dir="$(cd "$ios_dir/.." && pwd)"
spec="$ios_dir/project.yml"

for tool in git xcodebuild xcodegen plutil; do
	command -v "$tool" >/dev/null 2>&1 || case "$tool" in
		xcodegen) die "xcodegen is not installed. Run: brew install xcodegen" ;;
		xcodebuild) die "xcodebuild is not on PATH. Open Xcode once, then run: sudo xcode-select -s /Applications/Xcode.app/Contents/Developer" ;;
		*) die "$tool is not on PATH." ;;
	esac
done

# ---- the team ---------------------------------------------------------------
#
# ios/release.local is read as data, KEY=value, and never sourced: nothing in
# it runs. It is in .gitignore.
local_file="$ios_dir/release.local"
team="${DOMOVOI_TEAM_ID:-}"
if [ -z "$team" ] && [ -f "$local_file" ]; then
	team="$(sed -n 's/^[[:space:]]*DOMOVOI_TEAM_ID[[:space:]]*=[[:space:]]*//p' "$local_file" | tail -n 1 | tr -d '"'"'"' \r')"
fi
if [ -z "$team" ]; then
	die "DOMOVOI_TEAM_ID is not set. It is the ten-character team id from
developer.apple.com > Membership (the Coders Farm organisation, not a personal
team). Either run:

  DOMOVOI_TEAM_ID=ABCDE12345 ios/scripts/release.sh

or put the line DOMOVOI_TEAM_ID=ABCDE12345 in ios/release.local."
fi
case "$team" in
	[A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9]) ;;
	*) die "DOMOVOI_TEAM_ID '$team' is not a ten-character team id (capital letters and digits)." ;;
esac

# ---- a clean tree -----------------------------------------------------------
#
# The archive must be exactly a commit, so the build can be traced back to it.
# Ignored files (the generated project, ios/.release/, ios/release.local) do
# not count.
cd "$repo_dir"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
	git status --short >&2
	die "the working tree is not clean (above). Commit or remove those changes
first: a release is built from a commit and nothing else."
fi
commit="$(git rev-parse --short=12 HEAD)"

# ---- the version: the iPhone's own -------------------------------------------
version="$(sed -n 's/^[[:space:]]*MARKETING_VERSION:[[:space:]]*"\{0,1\}\([^"[:space:]]*\)"\{0,1\}.*/\1/p' "$spec" | head -n 1)"
if ! printf '%s' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
	die "MARKETING_VERSION in ios/project.yml is '$version'. Apple takes three
integers and nothing else (1.0.0, not 1.0.0-rc2)."
fi

# ---- the build number: one sequence with Android -----------------------------
num_from() { # file, extended regex whose last run of digits is the number
	[ -f "$1" ] || { printf '0'; return; }
	local found
	found="$(grep -Eo "$2" "$1" | grep -Eo '[0-9]+$' | sort -n | tail -n 1 || true)"
	printf '%s' "${found:-0}"
}
android_json="$(num_from "$repo_dir/android/release.json" '"version_code"[[:space:]]*:[[:space:]]*[0-9]+')"
android_gradle="$(num_from "$repo_dir/android/app/build.gradle.kts" 'versionCode[[:space:]]*=[[:space:]]*[0-9]+')"
ios_spec="$(num_from "$spec" 'CURRENT_PROJECT_VERSION:[[:space:]]*"?[0-9]+')"
release_dir="$ios_dir/.release"
ios_local="$(num_from "$release_dir/last-build" '^[0-9]+')"

last=0
for n in "$android_json" "$android_gradle" "$ios_spec" "$ios_local"; do
	if [ "$n" -gt "$last" ]; then last="$n"; fi
done
say "release.sh: highest build number so far: $last"
say "  android/release.json $android_json, build.gradle.kts $android_gradle,"
say "  ios/project.yml $ios_spec, this Mac's last release $ios_local"

today="$(date +%y%m%d)"
if [ -z "$build" ]; then
	if [ "${last:0:6}" = "$today" ]; then
		build=$((last + 1))
	else
		build="${today}01"
	fi
fi

if ! printf '%s' "$build" | grep -Eq '^[0-9]{8}$'; then
	die "build number '$build' is not YYMMDDNN (eight digits)."
fi
yy="${build:0:2}"; mm="${build:2:2}"; dd="${build:4:2}"; nn="${build:6:2}"
if [ $((10#$mm)) -lt 1 ] || [ $((10#$mm)) -gt 12 ] || [ $((10#$dd)) -lt 1 ] || [ $((10#$dd)) -gt 31 ] || [ $((10#$nn)) -lt 1 ]; then
	die "build number $build is not a date and a counter: month $mm, day $dd, counter $nn."
fi
if [ "${yy}${mm}${dd}" -gt "$today" ]; then
	die "build number $build is dated after today ($today). A number from the
future would be above every honest build until that day comes."
fi
if [ "$build" -le "$last" ]; then
	die "build number $build is not above $last, the highest used so far.
App Store Connect and Play both refuse a build number that does not go up.
Use a later one, for example: --build $((last + 1))"
fi
if [ "${build:0:6}" != "$today" ]; then
	say "release.sh: note: $build is not dated today ($today)."
fi

# ---- generate, archive, export -----------------------------------------------
out="$release_dir/$build"
archive="$out/Domovoi-$version-$build.xcarchive"
mkdir -p "$out"

say ""
say "release.sh: Domovoi $version ($build) from $commit, team $team, destination $destination"
say ""

cd "$ios_dir"
say "release.sh: generating Domovoi.xcodeproj"
xcodegen generate --spec project.yml --project .

say "release.sh: archiving Release"
xcodebuild archive \
	-project Domovoi.xcodeproj \
	-scheme Domovoi \
	-configuration Release \
	-destination "generic/platform=iOS" \
	-archivePath "$archive" \
	-allowProvisioningUpdates \
	CODE_SIGN_STYLE=Automatic \
	DEVELOPMENT_TEAM="$team" \
	CURRENT_PROJECT_VERSION="$build"

[ -d "$archive" ] || die "the archive finished but $archive is not there. Read the build output above."

# Check what was archived says what we meant, before it goes anywhere.
app_plist="$archive/Products/Applications/Domovoi.app/Info.plist"
[ -f "$app_plist" ] || die "no Domovoi.app in the archive ($app_plist)."
got_version="$(plutil -extract CFBundleShortVersionString raw -o - "$app_plist")"
got_build="$(plutil -extract CFBundleVersion raw -o - "$app_plist")"
got_id="$(plutil -extract CFBundleIdentifier raw -o - "$app_plist")"
[ "$got_version" = "$version" ] || die "the archive says version $got_version, not $version."
[ "$got_build" = "$build" ] || die "the archive says build $got_build, not $build."
[ "$got_id" = "com.domovoi.app" ] || die "the archive's bundle id is $got_id, not com.domovoi.app."
[ -f "$archive/Products/Applications/Domovoi.app/PrivacyInfo.xcprivacy" ] \
	|| die "PrivacyInfo.xcprivacy is not in the app bundle. App Store Connect needs it."

options="$out/ExportOptions.plist"
cp "$ios_dir/scripts/ExportOptions.plist" "$options"
plutil -replace teamID -string "$team" "$options"
plutil -replace destination -string "$destination" "$options"

say "release.sh: exporting ($destination)"
xcodebuild -exportArchive \
	-archivePath "$archive" \
	-exportPath "$out/export" \
	-exportOptionsPlist "$options" \
	-allowProvisioningUpdates

# ---- record the number -------------------------------------------------------
#
# Locally at once, so a second run today takes the next counter; and in
# ios/project.yml, which is the record Android's side and every agent can see.
printf '%s\n' "$build" > "$release_dir/last-build"
sed -i '' -E "s/^([[:space:]]*CURRENT_PROJECT_VERSION:[[:space:]]*)\"?[0-9]+\"?/\1\"$build\"/" "$spec"

say ""
say "release.sh: done. Domovoi $version ($build), from $commit."
say "  archive: $archive"
if [ "$destination" = "export" ]; then
	say "  ipa:     $out/export/Domovoi.ipa"
fi
cat >&2 <<NEXT

Next, by hand:
NEXT
step=1
if [ "$destination" = "export" ]; then
	say "  $step. Upload $out/export/Domovoi.ipa with Transporter (Mac App Store),"
	say "     or open the archive in Xcode > Window > Organizer > Distribute App."
	step=$((step + 1))
fi
cat >&2 <<NEXT
  $step. Commit the build number, so the next iPhone or Android build goes above it:
       git add ios/project.yml
       git commit -m "iOS build $build ($version) [skip ci]"
       git push
  $((step + 1)). App Store Connect > Domovoi > TestFlight: wait for the build to finish
     processing. The encryption question is already answered by the app
     (ITSAppUsesNonExemptEncryption = NO). Add it to the internal group and
     try it on the iPhone before anything else.
  $((step + 2)). For the store version $version: paste the texts from ios/store/
     (details.txt there says which file goes in which field), the App Privacy
     and age-rating answers from ios/store/APP-PRIVACY-ANSWERS.md, the review
     notes from ios/store/REVIEW-NOTES.txt, attach the review video, choose
     this build, and Submit for Review.
NEXT
