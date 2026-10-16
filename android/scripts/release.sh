#!/usr/bin/env bash
# Publish a release of the Domovoi Android app: build the per-ABI release
# APKs, sign them with YOUR release key, check them the way a Domovoi
# server will, publish a GitHub release on coders-farm-official/domovoi
# with gh, and write android/release.json for you to commit.
#
# Run by the owner on his own machine; nothing here runs in CI. The
# keystore's path and passwords are asked for here (never written to a
# file, a log, or a command line: apksigner reads the passwords from this
# script's environment for the one call that needs them). The twin of this
# script for PowerShell is release.ps1 beside it; android/README.md,
# "Publishing a release", says when to run which.
#
#   bash android/scripts/release.sh                  # build, sign, check, publish, write release.json
#   bash android/scripts/release.sh --no-publish     # build, sign, check; stop before GitHub
#   bash android/scripts/release.sh --skip-build     # sign what the last build left (after a failed signing)
#   bash android/scripts/release.sh --keystore ~/domovoi-release.jks --alias domovoi
#
# Needs: a JDK (JAVA_HOME or java on PATH), the Android SDK with
# build-tools 35.0.0, Python 3.11+ (the server's own check), git, and gh
# signed in to an account that may publish releases on the repository.
set -euo pipefail

REPO_SLUG="coders-farm-official/domovoi"
BUILD_TOOLS="35.0.0"
ABIS=(arm64-v8a armeabi-v7a)
PACKAGE="com.domovoi.app"

publish=1
build=1
keystore=""
alias_name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-publish) publish=0 ;;
    --skip-build) build=0 ;;
    --keystore) keystore="${2:?--keystore needs a path}"; shift ;;
    --alias) alias_name="${2:?--alias needs a name}"; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 64 ;;
  esac
  shift
done

die() { echo "release: $*" >&2; exit 1; }
say() { echo "release: $*"; }

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

# ─── tools ───────────────────────────────────────────────────────────────
if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ] || [ -x "${JAVA_HOME:-}/bin/java.exe" ]; then
  java="$JAVA_HOME/bin/java"
else
  java="$(command -v java || true)"
fi
[ -n "$java" ] || die "no Java: install a JDK 17+ and set JAVA_HOME"

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
bt="$sdk/build-tools/$BUILD_TOOLS"
apksigner_jar="$bt/lib/apksigner.jar"
[ -f "$apksigner_jar" ] || die "no build-tools $BUILD_TOOLS in $sdk (sdkmanager \"build-tools;$BUILD_TOOLS\")"
zipalign="$bt/zipalign"; aapt2="$bt/aapt2"

python=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    python="$candidate"; break
  fi
done
[ -n "$python" ] || die "no Python 3.11+ (it runs the server's own check of the APKs)"
if [ "$publish" = 1 ]; then
  command -v gh >/dev/null 2>&1 || die "no gh (GitHub CLI): install it and run gh auth login, or use --no-publish"
  command -v curl >/dev/null 2>&1 || die "no curl (it checks the published files)"
fi

sha256() { "$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

# ─── the source ──────────────────────────────────────────────────────────
[ -z "$(git status --porcelain)" ] || die "the checkout has uncommitted changes: commit or stash them first, so the release is one commit"
commit="$(git rev-parse HEAD)"
if [ "$publish" = 1 ]; then
  git fetch --quiet origin || die "git fetch origin failed"
  [ -n "$(git branch -r --contains "$commit")" ] || die "commit ${commit:0:12} is not on GitHub yet: push it first (the release's tag points at it)"
fi

# ─── build ───────────────────────────────────────────────────────────────
out_unsigned="android/app/build/outputs/apk/release"
if [ "$build" = 1 ]; then
  say "building the release APKs (${ABIS[*]})…"
  gradlew="./gradlew"; [ -f android/gradlew.bat ] && [ -n "${WINDIR:-}" ] && gradlew="./gradlew.bat"
  (cd android && "$gradlew" :app:assembleRelease -Pdomovoi.apkPerAbi "-Pdomovoi.abis=$(IFS=,; echo "${ABIS[*]}")") \
    || die "the build failed"
fi

version_name=""; version_code=""
for abi in "${ABIS[@]}"; do
  apk="$out_unsigned/app-$abi-release-unsigned.apk"
  [ -f "$apk" ] || die "no $apk: build first (drop --skip-build)"
  badging="$("$aapt2" dump badging "$apk" 2>/dev/null)" || die "aapt2 can't read $apk"
  badging="${badging//$'\r'/}"
  pkg="$(printf '%s\n' "$badging" | sed -n "s/^package: name='\([^']*\)'.*/\1/p")"
  vc="$(printf '%s\n' "$badging" | sed -n "s/^package: .* versionCode='\([0-9]*\)'.*/\1/p")"
  vn="$(printf '%s\n' "$badging" | sed -n "s/^package: .* versionName='\([^']*\)'.*/\1/p")"
  native="$(printf '%s\n' "$badging" | sed -n "s/^native-code: //p")"
  [ "$pkg" = "$PACKAGE" ] || die "$apk is $pkg, not $PACKAGE"
  [ "$native" = "'$abi'" ] || die "$apk carries native code for $native, not '$abi' only"
  if [ -z "$version_name" ]; then version_name="$vn"; version_code="$vc"; fi
  [ "$vn" = "$version_name" ] && [ "$vc" = "$version_code" ] || die "the APKs disagree on their version"
done
tag="android-v$version_name"
say "Domovoi $version_name (versionCode $version_code), commit ${commit:0:12}"

# versionCode is YYMMDDNN (android/app/build.gradle.kts): the build's date
# and that day's number, one sequence for these APKs and the Play bundle
# (play-bundle.sh checks the same shape).
printf '%s\n' "$version_code" | grep -Eq '^[0-9]{2}(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])(0[1-9]|[1-9][0-9])$' \
  || die "versionCode $version_code is not YYMMDDNN (the build's date and that day's number, e.g. 26101501): set it in android/app/build.gradle.kts, commit, and run again"

prev="$("$python" - <<'PY'
import json
try:
    rel = json.load(open("android/release.json", encoding="utf-8")).get("release") or {}
except (OSError, ValueError):
    rel = {}
print(rel.get("version_code") or 0, rel.get("cert_sha256") or "-")
PY
)"
prev="${prev//$'\r'/}"
prev_code="${prev%% *}"; prev_cert="${prev##* }"
[ "$version_code" -gt "$prev_code" ] || die "versionCode $version_code is not above the last release's ($prev_code): raise versionCode (YYMMDDNN: the build's date and that day's number) and versionName in android/app/build.gradle.kts, commit, and run again"
if [ "$publish" = 1 ] && gh release view "$tag" --repo "$REPO_SLUG" >/dev/null 2>&1; then
  die "a release $tag already exists on $REPO_SLUG"
fi

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

signed="android/app/build/outputs/apk/release-signed"
rm -rf "$signed"; mkdir -p "$signed"
for abi in "${ABIS[@]}"; do
  name="domovoi-$version_name-$abi.apk"
  say "signing $name…"
  if ! err="$("$java" -jar "$apksigner_jar" sign --ks "$keystore" --ks-key-alias "$alias_name" \
      --ks-pass env:DOMOVOI_RELEASE_KS_PASS --key-pass env:DOMOVOI_RELEASE_KEY_PASS \
      --v4-signing-enabled false --out "$signed/$name" "$out_unsigned/app-$abi-release-unsigned.apk" 2>&1 >/dev/null)"; then
    # apksigner's own first line says why (it never prints a password).
    printf '%s\n' "$err" | grep -m1 -E 'ERROR|Exception|Failed' >&2 || true
    die "signing $name failed (wrong password or alias?)"
  fi
done
unset DOMOVOI_RELEASE_KS_PASS DOMOVOI_RELEASE_KEY_PASS

# ─── check: apksigner, alignment, and the server's own check ─────────────
cert=""
for abi in "${ABIS[@]}"; do
  apk="$signed/domovoi-$version_name-$abi.apk"
  report="$("$java" -jar "$apksigner_jar" verify --verbose --print-certs --min-sdk-version 26 "$apk")" \
    || die "apksigner does not verify $apk"
  report="${report//$'\r'/}"
  printf '%s\n' "$report" | grep -q '^Verified using v2 scheme (APK Signature Scheme v2): true' \
    || die "$apk has no v2 signature (Android 8 needs it)"
  printf '%s\n' "$report" | grep -q '^Number of signers: 1$' || die "$apk must have exactly one signer"
  printf '%s\n' "$report" | grep -q '^Signer #1 key algorithm: RSA$' \
    || die "the release key must be an RSA key (what android/README.md makes): the server checks RSA signatures only"
  if printf '%s\n' "$report" | grep -q '^Signer #1 certificate DN: CN=Android Debug'; then
    die "$apk is signed with a debug key, not the release key"
  fi
  this="$(printf '%s\n' "$report" | sed -n 's/^Signer #1 certificate SHA-256 digest: \([0-9a-f]\{64\}\)$/\1/p')"
  [ -n "$this" ] || die "apksigner printed no certificate digest for $apk"
  [ -z "$cert" ] || [ "$cert" = "$this" ] || die "the two APKs are signed by different keys"
  cert="$this"
  "$zipalign" -c -P 16 4 "$apk" || die "$apk is not aligned for 16 KB pages"
done
if [ "$prev_cert" != "-" ] && [ "$prev_cert" != "$cert" ]; then
  die "this key ($cert) is not the last release's ($prev_cert): phones refuse an update signed by another key"
fi
say "signed by certificate SHA-256 $cert"

staged="$signed/release.json"
"$python" -m domovoi.android_app write "$staged" "$signed" --version-name "$version_name" \
  --version-code "$version_code" --cert "$cert" --commit "$commit" --published "$(date -u +%Y-%m-%d)" >/dev/null \
  || die "writing the manifest failed"
"$python" -m domovoi.android_app check "$staged" "$signed" || die "the server's own check refuses these APKs"

if [ "$publish" = 0 ]; then
  say "not published (--no-publish). Signed APKs, SHA256SUMS and the manifest it would commit: $signed/"
  exit 0
fi

# ─── publish ─────────────────────────────────────────────────────────────
notes="$signed/NOTES.md"
{
  echo "Domovoi for Android $version_name (versionCode $version_code), built from $commit."
  echo
  echo "Install it from your own Domovoi server: on the dashboard, Settings → Satellites → add a spare device (for a room) or Settings → Devices → get the Domovoi app. The server checks these files against android/release.json before it hands them out."
  echo
  echo "| file | for | SHA-256 |"
  echo "|---|---|---|"
  for abi in "${ABIS[@]}"; do
    name="domovoi-$version_name-$abi.apk"
    case "$abi" in arm64-v8a) who="most phones (64-bit)" ;; *) who="older or cheaper phones (32-bit)" ;; esac
    echo "| $name | $who | \`$(sha256 "$signed/$name")\` |"
  done
  echo
  echo "Signing certificate SHA-256: \`$cert\` (check with \`apksigner verify --print-certs <file>\`)."
} > "$notes"
say "publishing $tag on $REPO_SLUG…"
assets=()
for abi in "${ABIS[@]}"; do assets+=("$signed/domovoi-$version_name-$abi.apk"); done
gh release create "$tag" "${assets[@]}" "$signed/SHA256SUMS" --repo "$REPO_SLUG" --target "$commit" \
  --title "Domovoi for Android $version_name" --notes-file "$notes" || die "gh release create failed"

say "checking the published files by their manifest URLs…"
check_dir="$(mktemp -d)"
# (tr: a Windows Python ends its lines with CR LF.)
"$python" - "$staged" <<'PY' | tr -d '\r' > "$check_dir/urls"
import json, sys
for e in json.load(open(sys.argv[1], encoding="utf-8"))["release"]["apks"]:
    print(e["url"], e["file"], e["sha256"])
PY
while read -r url name want; do
  curl -fsSL --retry 3 -o "$check_dir/$name" "$url" || die "can't download $url"
  [ "$(sha256 "$check_dir/$name")" = "$want" ] || die "$url is not the file that was signed"
done < "$check_dir/urls"
rm -rf "$check_dir"

cp "$staged" android/release.json
say "done. Now commit the manifest so servers start handing out $version_name:"
echo "    git add android/release.json"
echo "    git commit -m \"android: release $version_name [skip ci]\""
echo "    git push"
