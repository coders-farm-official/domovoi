#!/bin/bash
# build.sh: builds DomovoiSetup-mac.pkg (mac-installer PLAN 4.11, 6 item 9).
# Runs on a Mac as an ordinary user (pkgbuild and productbuild need no root).
#
#   mac/build.sh --out DIR [options]
#
#   --out DIR            where the package and its build record go (made if missing)
#   --repo-bundle FILE   a git bundle of the repository, shipped in the package
#                        (a test build of a commit nobody pushed; its
#                        refs/heads/<--ref> is the commit to install)
#   --repo-url URL       or: provision.sh clones from URL (a release build)
#   --commit SHA         the full commit this package is built from (default:
#                        git rev-parse HEAD, when this is a checkout)
#   --ref REF            what provision.sh checks out (default: the commit)
#   --track-branch NAME  the branch the Linux side's updates follow (default stable)
#   --lima-tarball FILE  the pinned Lima tarball (default: downloaded), checked
#                        against installer/pins.conf and bundled
#   --version V          the Mac side's version (default: the tag's, else the one in Layout.swift)
#   --provision-arg ARG  passed to provision.sh as is (repeatable)
#   --no-stt             leave the speech service out (PHASE8A-STT.md): no
#                        whisper-server, and the packaged pins.conf without its
#                        lines, so the install recognises speech in the VM
#   --cmake PATH         the cmake that builds whisper-server (default: one on
#                        PATH, else the pinned CMake below, downloaded)
#
# whisper-server: cloned from the whisper.cpp tag pins.conf names into the
# staging folder (the commit checked), built there, and the clone removed with
# it. Never copy whisper.cpp's source into this repository: its examples hold
# a script named after a media provider, which the repository may not carry.
#   --tag vX.Y.Z         a release build: the commit must be the tag's, the version
#                        is the tag's, and DomovoiSetup-mac.build.json (what
#                        finish-release --mac-pkg checks) is written beside the package
#   --release-key FILE   the release key's public half the Mac side's updates trust
#                        (default: windows/installer/release-signing.pub). A
#                        throwaway key, for the update train only, needs
#                        --release-repo too: it can never check a real release
#   --release-repo O/N   where the Mac side's updates come from (default: the
#                        project's own; the update train: a throwaway fork)
#
# Unsigned: every binary carries an ad-hoc signature (Apple Silicon needs
# one); Developer ID signing and notarization are the owner's, in their own
# account (PLAN 4.11, owner answer Q4/Q5), and not in this script yet.
set -euo pipefail
MAC=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$MAC/.." && pwd)
OUT="" BUNDLE="" REPO_URL="" COMMIT="" REF="" TRACK=stable LIMA_TGZ="" VERSION="" NO_STT=0 CMAKE="" TAG="" RELEASE_KEY="" RELEASE_REPO=""
PARGS=()
while [ $# -gt 0 ]; do
  case $1 in
    --out) OUT=$2; shift 2 ;;
    --repo-bundle) BUNDLE=$2; shift 2 ;;
    --repo-url) REPO_URL=$2; shift 2 ;;
    --commit) COMMIT=$2; shift 2 ;;
    --ref) REF=$2; shift 2 ;;
    --track-branch) TRACK=$2; shift 2 ;;
    --lima-tarball) LIMA_TGZ=$2; shift 2 ;;
    --version) VERSION=$2; shift 2 ;;
    --provision-arg) PARGS+=("$2"); shift 2 ;;
    --no-stt) NO_STT=1; shift ;;
    --cmake) CMAKE=$2; shift 2 ;;
    --tag) TAG=$2; shift 2 ;;
    --release-key) RELEASE_KEY=$2; shift 2 ;;
    --release-repo) RELEASE_REPO=$2; shift 2 ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "build.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
die() { echo "build.sh: $*" >&2; exit 1; }
[ -n "$OUT" ] || die "--out DIR is required"
[ "$(uname -s)" = Darwin ] || die "builds on macOS only"
[ -n "$BUNDLE" ] || [ -n "$REPO_URL" ] || die "give --repo-bundle FILE or --repo-url URL"
[ -z "$BUNDLE" ] || [ -f "$BUNDLE" ] || die "no such bundle: $BUNDLE"
if [ -z "$COMMIT" ]; then COMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null) || die "--commit SHA (not a checkout)"; fi
echo "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "--commit must be a full SHA-1"
[ -n "$REF" ] || REF=$COMMIT
if [ -n "$TAG" ]; then
  echo "$TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]{1,40})?$' || die "--tag takes a release tag, vX.Y.Z"
  TAG_COMMIT=$(git -C "$REPO" rev-parse --verify --quiet "refs/tags/$TAG^{commit}" 2>/dev/null) || die "no tag $TAG in this checkout"
  [ "$TAG_COMMIT" = "$COMMIT" ] || die "the tag $TAG is ${TAG_COMMIT:0:12}, not the commit this package is built from (${COMMIT:0:12})"
  [ -z "$VERSION" ] || [ "$VERSION" = "${TAG#v}" ] || die "--version $VERSION is not the tag's (${TAG#v})"
  VERSION=${TAG#v}
fi
[ -n "$VERSION" ] || VERSION=$(sed -n 's/.*macSideVersion = "\(.*\)".*/\1/p' "$MAC/Sources/DomovoiMacKit/Layout.swift")
# The release key the installed Mac side trusts for its updates (PLAN 4.12).
PROJECT_KEY=$REPO/windows/installer/release-signing.pub
[ -n "$RELEASE_KEY" ] || RELEASE_KEY=$PROJECT_KEY
[ -f "$RELEASE_KEY" ] || die "no release key at $RELEASE_KEY (--release-key)"
[ "$(grep -cv '^[[:space:]]*\(#.*\)\{0,1\}$' "$RELEASE_KEY")" = 1 ] && grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+( |$)' "$RELEASE_KEY" \
  || die "$RELEASE_KEY is not one ssh-ed25519 public key line"
if [ -n "$RELEASE_REPO" ]; then
  echo "$RELEASE_REPO" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$' || die "--release-repo takes owner/name"
fi
if ! cmp -s "$RELEASE_KEY" "$PROJECT_KEY"; then
  # A throwaway key checks only the releases it signed: never the project's.
  [ -n "$RELEASE_REPO" ] && [ "$RELEASE_REPO" != coders-farm-official/domovoi ] \
    || die "--release-key that isn't the project's release key needs --release-repo (a throwaway fork)"
  [ -z "$TAG" ] || echo "note: $TAG built with a throwaway release key, for $RELEASE_REPO only"
fi
[ -n "$VERSION" ] || die "no version"
PKG_VERSION=$(echo "$VERSION" | sed 's/-.*//')   # Installer wants digits and dots
pin() { sed -n "s/^$1=//p" "$MAC/installer/pins.conf"; }
LIMA_URL=$(pin LIMA_URL); LIMA_SHA=$(pin LIMA_SHA256)
WCPP_VERSION=$(pin WHISPERCPP_VERSION); WCPP_COMMIT=$(pin WHISPERCPP_COMMIT)
[ -n "$WCPP_VERSION" ] || NO_STT=1
# A build-time tool only, never shipped (Kitware's universal macOS tarball).
CMAKE_PIN_URL=https://github.com/Kitware/CMake/releases/download/v4.3.5/cmake-4.3.5-macos-universal.tar.gz
CMAKE_PIN_SHA=6582cace312c720fc3c70f2ccbcbb26771997409bb4fda490591e07ab4084a5d

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# The staging folder is named .noindex (Spotlight leaves it out) and removed
# when the package is made: a staged Domovoi.app is a launchable copy that
# Spotlight and LaunchServices would otherwise offer beside the real one.
W=$OUT/work.noindex
rm -rf "$W" "$OUT/work"; mkdir -p "$W"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
# Never stops on a failure (set -e reaches into traps): lsregister -u says
# -10814 for an app it never registered, which is the usual case here.
cleanup() {
  if [ -d "$W/payload/Applications/Domovoi.app" ]; then "$LSREG" -u "$W/payload/Applications/Domovoi.app" >/dev/null 2>&1 || true; fi
  rm -rf "$W" || true
}
trap cleanup EXIT

echo "== swift build (release)"
(cd "$MAC" && swift build -c release --product DomovoiHelper && swift build -c release --product domovoi-uninstall && swift build -c release --product Domovoi)
BIN=$(cd "$MAC" && swift build -c release --show-bin-path)

P=$W/payload
APP=$P/Applications/Domovoi.app
AS="$P/Library/Application Support/Domovoi"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$AS/bin" "$AS/share/seed"

echo "== Domovoi.app"
cp "$BIN/Domovoi" "$APP/Contents/MacOS/Domovoi"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@PKG_VERSION@/$PKG_VERSION/g" "$MAC/installer/Info.plist" >"$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null
# The app's icon (Setup's header, the Dock, notifications; Train 1 finding
# 2): the project's mascot, docs/assets/domovoi-icon.png (1024 x 1024), cut
# to the sizes an .icns holds with sips and iconutil (both part of macOS).
# Nothing new is drawn and no generated file is committed.
ICONSRC=$REPO/docs/assets/domovoi-icon.png
[ "$(sips -g pixelWidth "$ICONSRC" | awk '/pixelWidth/{print $2}')" = 1024 ] \
  && [ "$(sips -g pixelHeight "$ICONSRC" | awk '/pixelHeight/{print $2}')" = 1024 ] \
  || die "$ICONSRC is not 1024 x 1024"
ISET=$W/AppIcon.iconset; mkdir -p "$ISET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ICONSRC" --out "$ISET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$ICONSRC" --out "$ISET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ISET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ISET"
codesign --force --sign - --identifier com.domovoi.mac.setup "$APP"

echo "== helper, uninstall"
cp "$BIN/DomovoiHelper" "$BIN/domovoi-uninstall" "$AS/bin/"
codesign --force --sign - --identifier com.domovoi.mac.helper "$AS/bin/DomovoiHelper"
codesign --force --sign - --identifier com.domovoi.mac.uninstall "$AS/bin/domovoi-uninstall"

WSHA=""
if [ "$NO_STT" = 0 ]; then
  echo "== whisper-server (whisper.cpp v$WCPP_VERSION)"
  if [ -z "$CMAKE" ]; then CMAKE=$(command -v cmake || true); fi
  if [ -z "$CMAKE" ]; then
    curl -fL --proto =https --retry 3 -sS -o "$W/cmake.tar.gz" "$CMAKE_PIN_URL"
    [ "$(shasum -a 256 "$W/cmake.tar.gz" | cut -d' ' -f1)" = "$CMAKE_PIN_SHA" ] || die "the CMake tarball is not the pinned one"
    mkdir -p "$W/cmake"; tar -xzf "$W/cmake.tar.gz" -C "$W/cmake" --strip-components 1
    CMAKE=$W/cmake/CMake.app/Contents/bin/cmake
  fi
  git clone --quiet --depth 1 --branch "v$WCPP_VERSION" https://github.com/ggml-org/whisper.cpp.git "$W/whisper.cpp"
  [ "$(git -C "$W/whisper.cpp" rev-parse HEAD)" = "$WCPP_COMMIT" ] || die "whisper.cpp v$WCPP_VERSION is not commit $WCPP_COMMIT"
  "$CMAKE" -S "$W/whisper.cpp" -B "$W/whisper-build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_SDL2=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON >/dev/null
  "$CMAKE" --build "$W/whisper-build" --target whisper-server -j 4 >/dev/null
  WBIN=$W/whisper-build/bin/whisper-server
  [ -x "$WBIN" ] || die "the whisper.cpp build made no bin/whisper-server"
  # One static program: nothing but the system's own libraries.
  bad=$(otool -L "$WBIN" | awk 'NR>1{print $1}' | grep -Ev '^(/System/Library/|/usr/lib/)' || true)
  [ -z "$bad" ] || die "whisper-server links outside the system: $bad"
  cp "$WBIN" "$AS/bin/whisper-server"
  codesign --force --sign - --identifier com.domovoi.mac.stt "$AS/bin/whisper-server"
  WSHA=$(shasum -a 256 "$AS/bin/whisper-server" | cut -d' ' -f1)
  rm -rf "$W/whisper.cpp" "$W/whisper-build"
fi

echo "== share"
S=$AS/share
cp -R "$MAC/vm" "$MAC/launchd" "$S/"
mkdir -p "$S/installer"; cp "$MAC/installer/pins.conf" "$MAC/installer/open-setup.sh" "$S/installer/"
if [ "$NO_STT" = 1 ]; then
  # Without the program, without its pins: the install then says plainly
  # that speech is recognised in the VM.
  grep -Ev '^(WHISPERCPP_|STT_MODEL_)' "$MAC/installer/pins.conf" >"$S/installer/pins.conf"
  rm -f "$S/launchd/com.domovoi.mac.stt.plist"
fi
cp "$RELEASE_KEY" "$S/installer/release-signing.pub"
cp "$REPO/scripts/linux/provision.sh" "$S/provision.sh"
BUNDLE_NAME=""
if [ -n "$BUNDLE" ]; then
  git bundle verify "$BUNDLE" >/dev/null 2>&1 || git bundle list-heads "$BUNDLE" >/dev/null || die "$BUNDLE is not a git bundle"
  git bundle list-heads "$BUNDLE" | grep -Eq " refs/heads/$REF\$|^$REF " || echo "note: the bundle has no refs/heads/$REF; provision.sh checks out $REF from it"
  cp "$BUNDLE" "$S/origin.bundle"; BUNDLE_NAME=origin.bundle
fi
if [ -z "$LIMA_TGZ" ]; then
  LIMA_TGZ=$W/$(basename "$LIMA_URL")
  curl -fL --proto =https --retry 3 -sS -o "$LIMA_TGZ" "$LIMA_URL"
fi
[ "$(shasum -a 256 "$LIMA_TGZ" | cut -d' ' -f1)" = "$LIMA_SHA" ] || die "the Lima tarball is not the pinned one ($LIMA_SHA)"
cp "$LIMA_TGZ" "$S/seed/$(basename "$LIMA_URL")"

COMMIT=$COMMIT VERSION=$VERSION REPO_URL=$REPO_URL BUNDLE_NAME=$BUNDLE_NAME REF=$REF TRACK=$TRACK WSHA=$WSHA WCPP_VERSION=$WCPP_VERSION TAG=$TAG RELEASE_REPO=$RELEASE_REPO \
  /usr/bin/python3 - "$S/build.json" "${PARGS[@]+"${PARGS[@]}"}" <<'PY'
import json, os, sys, datetime
out = {"schema": 1, "version": os.environ["VERSION"], "commit": os.environ["COMMIT"], "ref": os.environ["REF"],
       "track_branch": os.environ["TRACK"], "provision_args": sys.argv[2:],
       "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
if os.environ["BUNDLE_NAME"]: out["bundle"] = os.environ["BUNDLE_NAME"]
if os.environ["REPO_URL"]: out["repo_url"] = os.environ["REPO_URL"]
if os.environ["WSHA"]: out["whisper_server"] = {"whisper_cpp": os.environ["WCPP_VERSION"], "sha256": os.environ["WSHA"]}
if os.environ["TAG"]: out["tag"] = os.environ["TAG"]
if os.environ["RELEASE_REPO"]: out["release_repo"] = os.environ["RELEASE_REPO"]
json.dump(out, open(sys.argv[1], "w"), indent=2, sort_keys=True)
open(sys.argv[1], "a").write("\n")
PY

# Modes: folders 0755, files 0644, programs 0755 (pkgbuild --ownership
# recommended makes them root:wheel).
find "$P" -type d -exec chmod 0755 {} +
find "$P" -type f -exec chmod 0644 {} +
chmod 0755 "$APP/Contents/MacOS/Domovoi" "$AS/bin/DomovoiHelper" "$AS/bin/domovoi-uninstall" "$S/provision.sh" "$S/installer/open-setup.sh"
[ ! -f "$AS/bin/whisper-server" ] || chmod 0755 "$AS/bin/whisper-server"
xattr -cr "$P"

echo "== pkgbuild"
pkgbuild --analyze --root "$P" "$W/component.plist" >/dev/null
# Never "relocated" to another copy of the app the Installer finds (a build folder).
i=0
while /usr/libexec/PlistBuddy -c "Print :$i:RootRelativeBundlePath" "$W/component.plist" >/dev/null 2>&1; do
  /usr/libexec/PlistBuddy -c "Set :$i:BundleIsRelocatable false" "$W/component.plist"
  i=$((i+1))
done
[ "$i" -ge 1 ] || die "pkgbuild found no app bundle in the payload"
# One install script, postinstall, which never opens the app itself (opening
# Domovoi.app during the install made Installer's last touch of the bundle
# wait on App Management, P3-1): it starts a one-shot job in the signed-in
# person's GUI session that opens Setup once Installer has quit
# (installer/scripts/postinstall, share/installer/open-setup.sh). The
# conclusion page still says to open Domovoi from Applications.
mkdir -p "$W/scripts"
cp "$MAC/installer/scripts/postinstall" "$W/scripts/postinstall"
chmod 0755 "$W/scripts/postinstall"
xattr -c "$W/scripts/postinstall"
pkgbuild --root "$P" --component-plist "$W/component.plist" --identifier com.domovoi.mac --scripts "$W/scripts" \
  --version "$PKG_VERSION" --install-location / --ownership recommended "$W/DomovoiCore.pkg" >/dev/null

echo "== productbuild"
R=$W/resources; mkdir -p "$R"
cp "$MAC/installer/resources/"*.html "$R/"
cp "$REPO/LICENSE" "$R/LICENSE.txt"
sed -e "s/@PKG_VERSION@/$PKG_VERSION/g" "$MAC/installer/distribution.xml" >"$W/distribution.xml"
productbuild --distribution "$W/distribution.xml" --resources "$R" --package-path "$W" "$OUT/DomovoiSetup-mac.pkg" >/dev/null

echo "== check the package"
# The script, and the waiter it starts, are where they must be: a postinstall
# left out of the package, or a waiter left out of the payload, would make
# "open Setup after Installer quits" do nothing without a word.
X=$W/expanded
pkgutil --expand "$OUT/DomovoiSetup-mac.pkg" "$X"
[ -x "$X/DomovoiCore.pkg/Scripts/postinstall" ] || die "the package has no executable Scripts/postinstall"
[ "$(ls "$X/DomovoiCore.pkg/Scripts")" = postinstall ] || die "the package has scripts other than postinstall: $(ls "$X/DomovoiCore.pkg/Scripts")"
cmp -s "$X/DomovoiCore.pkg/Scripts/postinstall" "$MAC/installer/scripts/postinstall" || die "the packaged postinstall is not installer/scripts/postinstall"
lsbom -p fm "$X/DomovoiCore.pkg/Bom" | grep 'Domovoi/share/installer/open-setup.sh' | grep -q 100755 \
  || die "the payload has no share/installer/open-setup.sh with mode 0755"
rm -rf "$X"

SHA=$(shasum -a 256 "$OUT/DomovoiSetup-mac.pkg" | cut -d' ' -f1)
cp "$S/build.json" "$OUT/build.json"
printf '%s  %s\n' "$SHA" DomovoiSetup-mac.pkg >"$OUT/SHA256SUMS"
# The build record finish-release --mac-pkg checks (scripts/release/
# finish_release.py check_mac_build_record): this tag, its commit, this very
# file. Only for a release build: a development package names no tag.
rm -f "$OUT/DomovoiSetup-mac.build.json"
if [ -n "$TAG" ]; then
  SIZE=$(stat -f %z "$OUT/DomovoiSetup-mac.pkg")
  TAG=$TAG COMMIT=$COMMIT VERSION=$VERSION SHA=$SHA SIZE=$SIZE /usr/bin/python3 - "$OUT/DomovoiSetup-mac.build.json" <<'PY'
import json, os, sys
doc = {"kind": "domovoi-mac-setup-build", "schema": 1, "tag": os.environ["TAG"], "commit": os.environ["COMMIT"],
       "version": os.environ["VERSION"], "file": "DomovoiSetup-mac.pkg", "size": int(os.environ["SIZE"]), "sha256": os.environ["SHA"]}
open(sys.argv[1], "w").write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
fi
echo "== $OUT/DomovoiSetup-mac.pkg ($(du -h "$OUT/DomovoiSetup-mac.pkg" | cut -f1), sha256 $SHA, commit ${COMMIT:0:12}, Mac side $VERSION)"
