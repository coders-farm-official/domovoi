#!/usr/bin/env bash
# Publish a release of the Domovoi Android app: build the per-ABI release
# APKs, sign them with YOUR release key, check them the way a Domovoi
# server will, write android/release.json, commit it ("android: release
# <v> [skip ci]") and push it to the branch's upstream, then publish the
# GitHub release v<versionName> on coders-farm-official/domovoi with gh,
# tagged at THAT commit (so the tag holds the manifest naming its own
# APKs), and download each file back by the manifest's URL. A run that
# stops after the commit (a failed push, gh or download) carries on from
# the release commit when run again; nothing is built or signed again.
# Then scripts/release/finish-release.sh adds the Windows Setup, one
# SHA256SUMS and the notes, and moves the stable branch.
#
# Run by the owner on his own machine; nothing here runs in CI. The
# keystore's path and passwords are asked for here (never written to a
# file, a log, or a command line: apksigner reads the passwords from this
# script's environment for the one call that needs them). The twin of this
# script for PowerShell is release.ps1 beside it; android/README.md,
# "Publishing a release", says when to run which.
#
#   bash android/scripts/release.sh                  # build, sign, check, commit + push the manifest, publish
#   bash android/scripts/release.sh --no-publish     # build, sign, check; stop before git and GitHub
#   bash android/scripts/release.sh --skip-build     # sign what the last build left (after a failed signing)
#   bash android/scripts/release.sh --prerelease     # a plain version (1.0.1) as a pre-release too (1.0.0-rc2 always is)
#   bash android/scripts/release.sh --keystore ~/domovoi-release.jks --alias domovoi
#
# Needs: a JDK (JAVA_HOME or java on PATH), the Android SDK with
# build-tools 35.0.0, Python 3.11+ (the server's own check), git with push
# access, and gh signed in to an account that may publish releases on the
# repository.
set -euo pipefail

REPO_SLUG="coders-farm-official/domovoi"
BUILD_TOOLS="35.0.0"
ABIS=(arm64-v8a armeabi-v7a)
PACKAGE="com.domovoi.app"

publish=1
build=1
prerelease=0
keystore=""
alias_name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-publish) publish=0 ;;
    --skip-build) build=0 ;;
    --prerelease) prerelease=1 ;;
    --keystore) keystore="${2:?--keystore needs a path}"; shift ;;
    --alias) alias_name="${2:?--alias needs a name}"; shift ;;
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
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

# Fields of a manifest's release (a file, or - for stdin), "-" for none.
read_release='import json, sys
try:
    text = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8").read()
    rel = json.loads(text).get("release") or {}
except (OSError, ValueError):
    rel = {}
print(" ".join(str(rel.get(k) or "-") for k in sys.argv[2:]))'
release_fields() { "$python" -c "$read_release" "$@" | tr -d '\r'; }

# A release commit at HEAD: android/release.json, committed on top of the
# commit its APKs were built from (and nothing else changed), by an earlier
# run that stopped before it finished. Then this run carries on from there.
resume=0
read -r built_from head_version head_code head_cert <<<"$(release_fields android/release.json commit version_name version_code cert_sha256)"
if printf '%s\n' "$built_from" | grep -Eq '^[0-9a-f]{40}$' && [ "$built_from" != "$commit" ] \
    && git merge-base --is-ancestor "$built_from" "$commit" 2>/dev/null \
    && [ "$(git diff --name-only "$built_from" "$commit")" = "android/release.json" ]; then
  resume=1
fi

# The branch the release commit goes on: HEAD must be its tip on GitHub
# (the release is committed on top of it, and the tag points there).
branch=""; upstream=""
if [ "$publish" = 1 ]; then
  git fetch --quiet origin || die "git fetch origin failed"
  branch="$(git symbolic-ref --quiet --short HEAD)" || die "HEAD is not on a branch: switch to the branch the release goes on (git switch main)"
  [ "$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)" = "origin/$branch" ] \
    || die "$branch does not track origin/$branch (git branch --set-upstream-to=origin/$branch)"
  upstream="$(git rev-parse "origin/$branch")"
  if [ "$upstream" != "$commit" ] && { [ "$resume" = 0 ] || [ "$upstream" != "$built_from" ]; }; then
    if git merge-base --is-ancestor "$commit" "$upstream"; then
      die "origin/$branch has commits this checkout lacks: git pull --ff-only, then run again"
    fi
    [ "$resume" = 0 ] || die "origin/$branch moved on after this release's commit was made here: drop it (git reset --keep HEAD~1), git pull --ff-only, and run again (the APKs are built and signed again)"
    die "commit ${commit:0:12} is not on GitHub yet: push it first (the release is committed on top of it, and its tag points there)"
  fi
fi

signed="android/app/build/outputs/apk/release-signed"
is_prerelease=0

# Push the release commit (unless GitHub has it), publish the release at it
# (unless it exists), check its tag is that commit, and download each file
# back by the URL android/release.json names. The manifest at HEAD is the
# release's from here on.
publish_release() {  # publish_release RELEASE_COMMIT EXISTS(0|1)
  local release_commit="$1" exists="$2" work m_commit m_cert notes flag kind tagged
  if [ "$upstream" != "$release_commit" ]; then
    say "pushing the release commit ${release_commit:0:12} to origin/$branch…"
    git push --quiet origin "$release_commit:refs/heads/$branch" \
      || die "the push failed: android/release.json is committed here but not on GitHub. Fix that and run this script again; it carries on from the release commit (nothing is signed again)"
  fi
  work="$(mktemp -d)"
  # (tr: a Windows Python ends its lines with CR LF.)
  "$python" -c 'import json
for e in json.load(open("android/release.json", encoding="utf-8"))["release"]["apks"]:
    print(e["abi"], e["file"], e["sha256"], e["url"])' | tr -d '\r' > "$work/apks"
  if [ "$exists" = 0 ]; then
    read -r m_commit m_cert <<<"$(release_fields android/release.json commit cert_sha256)"
    notes="$signed/NOTES.md"
    {
      echo "Domovoi $version_name. The Android app (versionCode $version_code), built from $m_commit; the release is tagged at $release_commit, which adds android/release.json naming these files. scripts/release/finish-release adds the Windows Setup, SHA256SUMS for every file and the release notes."
      echo
      echo "Install the app from your own Domovoi server: on the dashboard, Settings → Satellites → add a spare device (for a room) or Settings → Devices → get the Domovoi app. The server checks these files against android/release.json before it hands them out."
      echo
      echo "| file | for | SHA-256 |"
      echo "|---|---|---|"
      while read -r abi file sha url; do
        case "$abi" in arm64-v8a) who="most phones (64-bit)" ;; *) who="older or cheaper phones (32-bit)" ;; esac
        echo "| $file | $who | \`$sha\` |"
      done < "$work/apks"
      echo
      echo "Signing certificate SHA-256: \`$m_cert\` (check with \`apksigner verify --print-certs <file>\`)."
    } > "$notes"
    assets=()
    : > "$signed/SHA256SUMS"
    while read -r abi file sha url; do
      printf '%s  %s\n' "$sha" "$file" >> "$signed/SHA256SUMS"
      assets+=("$signed/$file")
    done < "$work/apks"
    # gh attaches a release to a tag that already exists, wherever it points.
    remote_tag="$(git ls-remote --tags origin "refs/tags/$tag")" || die "git ls-remote origin failed"
    remote_tag="${remote_tag%%[[:space:]]*}"
    [ -z "$remote_tag" ] || [ "$remote_tag" = "$release_commit" ] \
      || die "the tag $tag already exists on GitHub at another commit ($remote_tag): a published tag never moves"
    flag=--latest; kind="the Latest release"
    if [ "$is_prerelease" = 1 ]; then flag=--prerelease; kind="a pre-release"; fi
    say "publishing $tag on $REPO_SLUG at ${release_commit:0:12}, $kind…"
    gh release create "$tag" "${assets[@]}" "$signed/SHA256SUMS" --repo "$REPO_SLUG" --target "$release_commit" \
      --title "Domovoi v$version_name" --notes-file "$notes" "$flag" \
      || die "gh release create failed: run this script again; it carries on from the release commit"
  else
    say "$tag is already on $REPO_SLUG, so checking it"
  fi

  # The tag (GitHub makes it at --target) must be the release commit.
  git fetch --quiet origin "refs/tags/$tag:refs/tags/$tag" || die "can't fetch the tag $tag from GitHub"
  tagged="$(git rev-parse "$tag^{commit}")"
  [ "$tagged" = "$release_commit" ] || die "the tag $tag is $tagged, not the release commit $release_commit"

  say "checking the published files by their manifest URLs…"
  while read -r abi file sha url; do
    curl -fsSL --retry 3 -o "$work/$file" "$url" || die "can't download $url (run this script again to check once more)"
    [ "$(sha256 "$work/$file")" = "$sha" ] || die "$url is not the file that was signed"
  done < "$work/apks"
  rm -rf "$work"

  say "done: $tag is published at ${release_commit:0:12}, with the android/release.json naming its APKs."
  echo "Next: move the Windows Setup's pins to that commit (windows/installer/pins.iss), commit and push them, then:"
  echo "    git pull --ff-only"
  echo "    bash scripts/release/finish-release.sh --tag $tag      # or finish-release.ps1 -Tag $tag on Windows"
}

# ─── carry on from a release commit ──────────────────────────────────────
if [ "$resume" = 1 ]; then
  version_name="$head_version"; version_code="$head_code"
  tag="v$version_name"
  is_prerelease="$prerelease"; case "$version_name" in *-*) is_prerelease=1 ;; esac
  say "Domovoi $version_name (versionCode $version_code): HEAD ${commit:0:12} is its release commit (android/release.json on top of ${built_from:0:12}, which the APKs were built from), so this run carries on from there; nothing is built or signed again"
  # The first run's checks against the release before it, on what is committed.
  read -r prev_code prev_cert <<<"$(git show "$built_from:android/release.json" 2>/dev/null | release_fields - version_code cert_sha256 || true)"
  [ "${prev_code:--}" != "-" ] || prev_code=0
  [ "$version_code" -gt "$prev_code" ] || die "android/release.json at HEAD names versionCode $version_code, not above the last release's ($prev_code)"
  [ "${prev_cert:--}" = "-" ] || [ "$prev_cert" = "$head_cert" ] || die "android/release.json at HEAD names another key ($head_cert) than the last release's ($prev_cert)"
  exists=0
  if [ "$publish" = 1 ] && gh release view "$tag" --repo "$REPO_SLUG" >/dev/null 2>&1; then exists=1; fi
  if [ "$exists" = 0 ]; then
    "$python" -m domovoi.android_app check android/release.json "$signed" \
      || die "the signed APKs android/release.json pins are not in $signed (or have changed), so they can't be published from here: drop the release commit (git reset --keep HEAD~1), raise versionCode, and release again"
  fi
  if [ "$publish" = 0 ]; then
    say "not published (--no-publish). A real run pushes the release commit if GitHub lacks it, publishes $tag at it unless it exists, and checks the files."
    exit 0
  fi
  publish_release "$commit" "$exists"
  exit 0
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
# The project-wide tag, v<versionName> (v1.0.0-rc1): domovoi.android_app's
# tag_for, which the manifest's URLs are checked against.
tag="v$version_name"
# A version with a suffix (1.0.0-rc2) is a pre-release, never Latest.
is_prerelease="$prerelease"; case "$version_name" in *-*) is_prerelease=1 ;; esac
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
if [ "$publish" = 1 ]; then
  ! gh release view "$tag" --repo "$REPO_SLUG" >/dev/null 2>&1 || die "a release $tag already exists on $REPO_SLUG"
  remote_tag="$(git ls-remote --tags origin "refs/tags/$tag")" || die "git ls-remote origin failed"
  [ -z "$remote_tag" ] || die "the tag $tag already exists on GitHub (a published tag never moves: raise the version)"
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
  kind="the Latest release"; [ "$is_prerelease" = 1 ] && kind="a pre-release"
  say "not published (--no-publish). Signed APKs, SHA256SUMS and the manifest it would commit: $signed/"
  say "A real run commits that manifest (android: release $version_name [skip ci]), pushes it, and publishes $tag at that commit as $kind."
  exit 0
fi

# ─── publish: commit the manifest, push it, tag that commit ──────────────
cp "$staged" android/release.json
git add -- android/release.json || die "git add android/release.json failed"
git commit --quiet -m "android: release $version_name [skip ci]" -- android/release.json \
  || die "git commit failed (android/release.json is written, not committed: git checkout -- android/release.json undoes it)"
release_commit="$(git rev-parse HEAD)"
say "committed android/release.json as ${release_commit:0:12} (android: release $version_name [skip ci]): the tag goes there"
publish_release "$release_commit" 0
