#!/usr/bin/env bash
# Build the Google Play App Bundle (.aab) of the Domovoi Android app, signed
# with YOUR release key: bundleRelease (all four ABIs; Play splits it per
# phone), signed with jarsigner (apksigner cannot sign a bundle), checked,
# and written with its SHA-256 to android/app/build/outputs/bundle/release-signed/.
# Uploading it is yours to do in the Play Console
# (android/play/PUBLISH-CHECKLIST.md); nothing here talks to Google or GitHub.
#
# The key is the one release.sh signs the GitHub APKs with (Play App
# Signing set to "use my own key"): when android/release.json names a
# release certificate, a bundle signed by any other key is refused.
#
# Run by the owner on his own machine; nothing here runs in CI. The
# keystore's path and passwords are asked for here (never written to a
# file, a log, or a command line: jarsigner reads the passwords from this
# script's environment for the one call that needs them). The PowerShell
# twin is play-bundle.ps1 beside it.
#
#   bash android/scripts/play-bundle.sh                  # build, sign, check
#   bash android/scripts/play-bundle.sh --skip-build     # sign what the last build left
#   bash android/scripts/play-bundle.sh --keystore ~/domovoi-release.jks --alias domovoi
#   bash android/scripts/play-bundle.sh --allow-dirty    # a test run from uncommitted changes
#
# Needs: a JDK 17+ (JAVA_HOME or java on PATH; jarsigner and keytool come
# with it) and the Android SDK.
set -euo pipefail

PACKAGE="com.domovoi.app"

build=1
dirty_ok=0
keystore=""
alias_name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-build) build=0 ;;
    --allow-dirty) dirty_ok=1 ;;
    --keystore) keystore="${2:?--keystore needs a path}"; shift ;;
    --alias) alias_name="${2:?--alias needs a name}"; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 64 ;;
  esac
  shift
done

die() { echo "play-bundle: $*" >&2; exit 1; }
say() { echo "play-bundle: $*"; }

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

# ─── tools ───────────────────────────────────────────────────────────────
jdk_bin=""
for candidate in "${JAVA_HOME:-}/bin"; do
  if [ -n "${JAVA_HOME:-}" ] && { [ -x "$candidate/jarsigner" ] || [ -x "$candidate/jarsigner.exe" ]; }; then jdk_bin="$candidate"; fi
done
if [ -z "$jdk_bin" ] && command -v jarsigner >/dev/null 2>&1; then jdk_bin="$(dirname "$(command -v jarsigner)")"; fi
[ -n "$jdk_bin" ] || die "no JDK (jarsigner): install a JDK 17+ and set JAVA_HOME"
jarsigner="$jdk_bin/jarsigner"; keytool="$jdk_bin/keytool"
[ -x "$jarsigner" ] || [ -x "$jarsigner.exe" ] || die "no jarsigner in $jdk_bin"

sdk=""
for candidate in "${ANDROID_HOME:-}" "${ANDROID_SDK_ROOT:-}"; do
  [ -n "$candidate" ] && [ -d "$candidate" ] && { sdk="$candidate"; break; }
done
if [ -z "$sdk" ] && [ -f android/local.properties ]; then
  sdk="$(sed -n 's/^sdk\.dir=//p' android/local.properties | head -n1 | sed 's/\\:/:/g; s/\\\\/\//g')"
fi
if [ -z "$sdk" ]; then
  for candidate in "${LOCALAPPDATA:-}/Android/Sdk" "$HOME/Android/Sdk" "$HOME/Library/Android/sdk"; do
    [ -d "$candidate" ] && { sdk="$candidate"; break; }
  done
fi
[ -n "$sdk" ] && [ -d "$sdk" ] || die "no Android SDK found (set ANDROID_HOME)"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# ─── the source ──────────────────────────────────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
  [ "$dirty_ok" = 1 ] || die "the checkout has uncommitted changes: commit or stash them first, so the bundle is one commit"
  say "WARNING: uncommitted changes (--allow-dirty): this bundle is not one commit; do not upload it"
fi
commit="$(git rev-parse HEAD)"

gradle_file="android/app/build.gradle.kts"
version_code="$(sed -n 's/^[[:space:]]*versionCode[[:space:]]*=[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' "$gradle_file" | tr -d '\r' | head -n1)"
version_name="$(sed -n 's/^[[:space:]]*versionName[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$gradle_file" | tr -d '\r' | head -n1)"
app_id="$(sed -n 's/^[[:space:]]*applicationId[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$gradle_file" | tr -d '\r' | head -n1)"
[ -n "$version_code" ] || die "no versionCode in $gradle_file"
[ -n "$version_name" ] || die "no versionName in $gradle_file"
[ "$app_id" = "$PACKAGE" ] || die "the applicationId is not $PACKAGE"
say "Domovoi $version_name (versionCode $version_code), commit ${commit:0:12}"

# versionCode is YYMMDDNN: the build's date and that day's number, one
# sequence for the GitHub APKs and this bundle (release.sh checks the same
# shape).
printf '%s\n' "$version_code" | grep -Eq '^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$' \
  || die "versionCode $version_code is not YYMMDDNN (the build's date and that day's number, e.g. 26101501): set it in $gradle_file"

# The last GitHub release (android/release.json, when this checkout has one).
prev_code=0; prev_cert="-"
if [ -f android/release.json ]; then
  py=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys' >/dev/null 2>&1; then py="$candidate"; break; fi
  done
  [ -n "$py" ] || die "android/release.json is here but there is no Python to read it"
  prev="$("$py" - <<'PY'
import json
rel = json.load(open("android/release.json", encoding="utf-8")).get("release") or {}
print(rel.get("version_code") or 0, rel.get("cert_sha256") or "-")
PY
)" || die "android/release.json can't be read"
  prev="${prev//$'\r'/}"
  prev_code="${prev%% *}"; prev_cert="${prev##* }"
fi
if [ "$prev_code" -gt 0 ] && [ "$version_code" -lt "$prev_code" ]; then
  die "versionCode $version_code is below the last GitHub release's ($prev_code): one sequence for both (YYMMDDNN), raise it"
fi

# ─── build ───────────────────────────────────────────────────────────────
unsigned="android/app/build/outputs/bundle/release/app-release.aab"
if [ "$build" = 1 ]; then
  say "building the App Bundle (all four ABIs)…"
  gradlew="./gradlew"; [ -f android/gradlew.bat ] && [ -n "${WINDIR:-}" ] && gradlew="./gradlew.bat"
  (cd android && "$gradlew" :app:bundleRelease --console=plain) || die "the build failed"
fi
[ -f "$unsigned" ] || die "no $unsigned: build first (drop --skip-build)"

# The release build is minified (android/app/build.gradle.kts): the bundle
# must carry its R8 mapping, which Play uses to turn crash reports back into
# class and method names.
map_entry="BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map"
"$jdk_bin/jar" tf "$unsigned" | tr -d '\r' | grep -qx "$map_entry" \
  || die "$unsigned carries no R8 mapping ($map_entry): is the release build still minified?"

# ─── sign ────────────────────────────────────────────────────────────────
if [ -z "$keystore" ]; then
  default="$HOME/domovoi-release.jks"
  read -r -p "Keystore file [$default]: " keystore </dev/tty || true
  keystore="${keystore:-$default}"
fi
keystore="${keystore/#\~/$HOME}"
[ -f "$keystore" ] || die "no keystore at $keystore (android/README.md says how to make one, once)"
case "$(cd "$(dirname "$keystore")" && pwd)/" in
  "$repo"/*) die "the keystore is inside the checkout: keep it outside (and backed up)" ;;
esac
if [ -z "$alias_name" ]; then
  read -r -p "Key alias [domovoi]: " alias_name </dev/tty || true
  alias_name="${alias_name:-domovoi}"
fi
# Passwords: from the terminal, echo off; from stdin when there is none
# (a scripted test run). Never on a command line, never in a file.
read_secret() {
  local prompt="$1" value=""
  if [ -r /dev/tty ] && [ -t 0 ]; then
    read -r -s -p "$prompt" value </dev/tty; echo >&2
  else
    IFS= read -r value || true
  fi
  printf '%s' "$value"
}
DOMOVOI_RELEASE_KS_PASS="$(read_secret 'Keystore password: ')"
DOMOVOI_RELEASE_KEY_PASS="$(read_secret 'Key password (Enter if the same): ')"
[ -n "$DOMOVOI_RELEASE_KEY_PASS" ] || DOMOVOI_RELEASE_KEY_PASS="$DOMOVOI_RELEASE_KS_PASS"
export DOMOVOI_RELEASE_KS_PASS DOMOVOI_RELEASE_KEY_PASS
trap 'unset DOMOVOI_RELEASE_KS_PASS DOMOVOI_RELEASE_KEY_PASS' EXIT

out_dir="android/app/build/outputs/bundle/release-signed"
rm -rf "$out_dir"; mkdir -p "$out_dir"
name="domovoi-$version_name-$version_code.aab"
signed="$out_dir/$name"
say "signing $name…"
if ! err="$("$jarsigner" -keystore "$keystore" -storepass:env DOMOVOI_RELEASE_KS_PASS \
    -keypass:env DOMOVOI_RELEASE_KEY_PASS -sigalg SHA256withRSA -digestalg SHA-256 \
    -signedjar "$signed" "$unsigned" "$alias_name" 2>&1)"; then
  # jarsigner's own first line says why (it never prints a password).
  printf '%s\n' "$err" | tr -d '\r' | grep -m1 -iE 'error|exception|failed' >&2 || true
  die "signing $name failed (wrong password or alias, or not an RSA key?)"
fi
unset DOMOVOI_RELEASE_KS_PASS DOMOVOI_RELEASE_KEY_PASS

# ─── check ───────────────────────────────────────────────────────────────
verify="$("$jarsigner" -verify "$signed" 2>&1 | tr -d '\r')" || die "jarsigner does not verify $signed"
printf '%s\n' "$verify" | grep -q '^jar verified\.' || die "jarsigner does not verify $signed"
certs="$("$keytool" -printcert -jarfile "$signed" 2>&1 | tr -d '\r')" || die "keytool can't read the signature of $signed"
signers="$(printf '%s\n' "$certs" | grep -c '^Signer #[0-9]*:' || true)"
[ "$signers" = 1 ] || die "$signed must have exactly one signer (has $signers)"
if printf '%s\n' "$certs" | grep -q 'CN=Android Debug'; then die "this is the debug key, not the release key"; fi
printf '%s\n' "$certs" | grep -q 'Subject Public Key Algorithm: .*RSA' \
  || die "the release key must be an RSA key (what android/README.md makes): the household server checks RSA signatures only"
cert="$(printf '%s\n' "$certs" | sed -n 's/^[[:space:]]*SHA256:[[:space:]]*\([0-9A-Fa-f:]\{95\}\)[[:space:]]*$/\1/p' | head -n1 | tr -d ':' | tr 'A-F' 'a-f')"
[ -n "$cert" ] || die "keytool printed no SHA-256 for the signing certificate"
if [ "$prev_cert" != "-" ] && [ "$prev_cert" != "$cert" ]; then
  die "this key ($cert) is not the GitHub release's ($prev_cert): use the same key for Play, or phones can't move between them"
fi

sha="$(sha256 "$signed")"
printf '%s  %s\n' "$sha" "$name" > "$signed.sha256"
size="$(wc -c < "$signed" | tr -d ' ')"
# The same mapping beside the bundle, for the release's records (Play has
# its own copy from the bundle).
mapping="android/app/build/outputs/mapping/release/mapping.txt"
kept_map="$out_dir/domovoi-$version_name-$version_code-mapping.txt"
if [ -f "$mapping" ]; then cp "$mapping" "$kept_map"; else kept_map="(none: the build left no mapping.txt)"; fi

say "done."
echo "  bundle:      $signed ($((size / 1048576)) MB)"
echo "  SHA-256:     $sha"
echo "  certificate: $cert (SHA-256 of the signing certificate)"
echo "  version:     $version_name ($version_code), commit $commit"
echo "  R8 mapping:  in the bundle (Play reads it), and $kept_map"
echo
echo "  Next: Play Console -> Test and release -> Testing -> Internal testing -> Create new release, upload the .aab"
echo "  (android/play/PUBLISH-CHECKLIST.md; the first upload needs App signing set up first)."
