#!/usr/bin/env bash
# python-runtime.sh: the CPython the venv runs on where the system's
# python3 is not the one requirements-linux-py314.lock is for (Debian 13
# ships 3.13, Ubuntu 24.04 and its derivatives 3.12; Ubuntu 26.04's own
# 3.14 needs none of this, and a box on it never runs this script).
#
#   sudo bash python-runtime.sh --manifest provision-manifest.conf            # install, print the interpreter
#   sudo bash python-runtime.sh --manifest provision-manifest.conf --check    # status 0: installed and sound
#   bash python-runtime.sh --manifest provision-manifest.conf --path          # where it goes; changes nothing
#
# Two scripts call it: provision.sh (its venv step) and apply-update.sh (a
# dashboard update whose pin manifest names another CPython than the venv
# runs on: step 4b in docs/LINUX_HOST.md, Updates from the dashboard).
#
# What it installs is the manifest's (scripts/linux/provision-manifest.conf,
# "Python, where the system's python3 is not the lock's"): uv at uv_version
# and CPython python_version as python-build-standalone release python_build
# builds it, each download checked against its SHA-256 there before anything
# in it runs. uv then installs that CPython from the checked file (a file://
# mirror, so uv downloads nothing itself, and checks the SHA-256 it carries
# for the file again) into
#
#   /usr/local/lib/domovoi-python/<python_build>/cpython-<version>-linux-x86_64-gnu/
#
# root's, readable by everyone; uv itself is only used for that and is not
# kept. One folder per build: a new pin goes in beside the old one, never
# over it, so a service still running on the old interpreter keeps its
# standard library, and an update that is rolled back finds the old one
# where it was. A folder no venv names is left for the administrator (each
# is about 100 MB); nothing here removes one.
#
# Output: progress lines on stderr; on stdout, the interpreter's path (the
# install and --path), nothing for --check. The interpreter answers to
# python3.<minor> in the folder's bin/.
#
# Options:
#   --manifest FILE     the pin manifest (default: provision-manifest.conf
#                       beside this script)
#   --cache DIR         where the downloads go while it works (default:
#                       /var/cache/domovoi-provision); they are removed
#                       after, unless --keep-downloads
#   --keep-downloads    keep the two downloads in --cache
#   --check             change nothing; status 0 when the pinned interpreter
#                       is installed and answers as that version
#   --path              print the pinned interpreter's path; change nothing
#
# Exit status: 0 done, 1 failed (a download, a checksum, uv, or the
# interpreter that came out of it), 2 bad usage.
#
# DOMOVOI_PROVISION_ROOT with DOMOVOI_PROVISION_TEST_HARNESS=1 is for the
# tests only (scripts/linux/tests/test-provision.sh and
# test-apply-update.sh), exactly as in provision.sh: a directory put in
# front of every system path this reads or writes, the interpreter's
# included (the path it prints carries it). Either one without the other
# makes it refuse to run.

set -euo pipefail
umask 022

PROG=python-runtime
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

ROOT=""
if [ -n "${DOMOVOI_PROVISION_ROOT:-}" ] || [ -n "${DOMOVOI_PROVISION_TEST_HARNESS:-}" ]; then
  if [ "${DOMOVOI_PROVISION_TEST_HARNESS:-}" != 1 ] || [[ ${DOMOVOI_PROVISION_ROOT:-} != /* ]] \
      || [ ! -d "$DOMOVOI_PROVISION_ROOT" ] || [ "$(cd "$DOMOVOI_PROVISION_ROOT" && pwd -P)" = / ]; then
    printf '%s: DOMOVOI_PROVISION_ROOT and DOMOVOI_PROVISION_TEST_HARNESS are for the test harnesses only, and only together\n' "$PROG" >&2
    exit 2
  fi
  ROOT=${DOMOVOI_PROVISION_ROOT%/}
else
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export PATH
fi
export LANG=C.UTF-8

# As the venv's pyvenv.cfg names it (plus the harness root).
PYTHON_DIR=$ROOT/usr/local/lib/domovoi-python
UV_URL_BASE=https://github.com/astral-sh/uv/releases/download
PBS_URL_BASE=https://github.com/astral-sh/python-build-standalone/releases/download
UV_ASSET=uv-x86_64-unknown-linux-gnu.tar.gz
MANIFEST_KEYS=(uv_version uv_sha256 python_version python_build python_sha256)

MANIFEST=$SCRIPT_DIR/provision-manifest.conf
CACHE=$ROOT/var/cache/domovoi-provision
KEEP=0
MODE=install

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
usage_error() { printf '%s: %s\n' "$PROG" "$1" >&2; exit 2; }
say() { printf '%s: %s\n' "$PROG" "$*" >&2; }
fail() { printf '%s: failed: %s\n' "$PROG" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case $1 in
    --manifest) [ $# -ge 2 ] || usage_error "--manifest needs a file"; MANIFEST=$2; shift 2 ;;
    --manifest=*) MANIFEST=${1#*=}; shift ;;
    --cache) [ $# -ge 2 ] || usage_error "--cache needs a directory"; CACHE=$2; shift 2 ;;
    --cache=*) CACHE=${1#*=}; shift ;;
    --keep-downloads) KEEP=1; shift ;;
    --check) MODE=check; shift ;;
    --path) MODE=path; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage_error "unknown option: $1" ;;
  esac
done
[[ $CACHE == /* ]] || usage_error "--cache must be an absolute path: $CACHE"

# The five keys, from the manifest (a key it doesn't know is someone
# else's: provision.sh checks the whole file).
declare -A M=()
[ -f "$MANIFEST" ] || fail "no pin manifest at $MANIFEST"
n=0
while IFS= read -r line || [ -n "$line" ]; do
  n=$((n + 1))
  line=${line%$'\r'}
  case $line in ''|'#'*) continue ;; esac
  key=${line%%=*}
  case " ${MANIFEST_KEYS[*]} " in *" $key "*) ;; *) continue ;; esac
  [[ $line =~ ^[a-z][a-z0-9_]*=([^[:space:]]+)$ ]] || fail "$MANIFEST line $n is not key=value: $line"
  [ -z "${M[$key]+x}" ] || fail "$MANIFEST line $n: $key is set twice"
  M[$key]=${BASH_REMATCH[1]}
done <"$MANIFEST"
for k in "${MANIFEST_KEYS[@]}"; do [ -n "${M[$k]+x}" ] || fail "$MANIFEST: $k is missing"; done
[[ ${M[uv_version]} =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "$MANIFEST: uv_version is not a version (x.y.z)"
[[ ${M[python_version]} =~ ^3\.[0-9]+\.[0-9]+$ ]] || fail "$MANIFEST: python_version is not a CPython 3 version (3.y.z)"
[[ ${M[python_build]} =~ ^[0-9]{8}$ ]] || fail "$MANIFEST: python_build is not a python-build-standalone release (YYYYMMDD)"
for k in uv_sha256 python_sha256; do
  [[ ${M[$k]} =~ ^[0-9a-f]{64}$ ]] || fail "$MANIFEST: $k is not a SHA-256"
done

VER=${M[python_version]}
MINOR=${VER%.*}
BUILD=${M[python_build]}
# uv's name for the install, and the folder it makes for it.
KEY=cpython-$VER-linux-x86_64-gnu
DEST=$PYTHON_DIR/$BUILD
PY=$DEST/$KEY/bin/python$MINOR
PBS_FILE=cpython-$VER+$BUILD-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
PBS_URL=$PBS_URL_BASE/$BUILD/cpython-$VER%2B$BUILD-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
UV_URL=$UV_URL_BASE/${M[uv_version]}/$UV_ASSET

# The interpreter is there and is what the pin says: the exact version,
# and the parts of the standard library a venv and pip need.
sound() {
  local v
  [ -x "$PY" ] || return 1
  v=$("$PY" -I -c 'import sys, ssl, sqlite3, ctypes, zlib, venv, ensurepip; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || return 1
  [ "$v" = "$VER" ]
}

case $MODE in
  path) printf '%s\n' "$PY"; exit 0 ;;
  check) sound; exit $? ;;
esac

if sound; then
  say "CPython $VER ($BUILD) is in place: $PY"
  printf '%s\n' "$PY"
  exit 0
fi
[ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || fail "run it as root: it installs into ${PYTHON_DIR}"

sha_ok() { [ -f "$1" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]; }

# fetch URL FILE SHA256: FILE holds the download, checked; a copy already
# in the cache that matches is used as it is.
fetch() {
  local url=$1 file=$2 sha=$3
  if sha_ok "$file" "$sha"; then say "$(basename "$file"): already downloaded, SHA-256 matches"; return 0; fi
  rm -f "$file"
  say "downloading $url"
  curl -fsSL --proto '=https' --retry 3 -o "$file.part" "$url" </dev/null || { rm -f "$file.part"; fail "could not download $url"; }
  if ! sha_ok "$file.part" "$sha"; then
    rm -f "$file.part"
    fail "$url does not match its pin (SHA-256 $sha in $MANIFEST); nothing from it was run"
  fi
  mv -f "$file.part" "$file"
}

install -d -m 0755 "$CACHE" "$PYTHON_DIR"
UV_TGZ=$CACHE/uv-${M[uv_version]}-$UV_ASSET
PBS_TGZ=$CACHE/$PBS_FILE
fetch "$UV_URL" "$UV_TGZ" "${M[uv_sha256]}"
fetch "$PBS_URL" "$PBS_TGZ" "${M[python_sha256]}"

WORK=$(mktemp -d "$CACHE/python-runtime.XXXXXX")
cleanup() {
  rm -rf "$WORK"
  if [ "$KEEP" = 0 ]; then rm -f "$UV_TGZ" "$PBS_TGZ"; fi
}
trap cleanup EXIT

tar -xzf "$UV_TGZ" -C "$WORK" || fail "could not unpack $UV_TGZ"
UV=$WORK/uv-x86_64-unknown-linux-gnu/uv
[ -x "$UV" ] || fail "$UV_TGZ holds no uv-x86_64-unknown-linux-gnu/uv"
uv_said=$("$UV" --version 2>/dev/null) || uv_said=""
[[ $uv_said == "uv ${M[uv_version]}"* ]] || fail "the uv in $UV_TGZ says '${uv_said:-nothing}', not uv ${M[uv_version]}"

# The mirror uv reads instead of GitHub: <release>/<file>, under both
# spellings of the file's "+" (uv puts the URL's %2B in the path).
mkdir -p "$WORK/mirror/$BUILD"
ln -s "$PBS_TGZ" "$WORK/mirror/$BUILD/$PBS_FILE"
ln -s "$PBS_TGZ" "$WORK/mirror/$BUILD/cpython-$VER%2B$BUILD-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"

# A folder left by a run killed half-way, or one that is not sound: uv
# starts it again from nothing. (A sound one returned above.)
if [ -e "$DEST/$KEY" ] || [ -L "$DEST/$KEY" ]; then
  say "$DEST/$KEY is there but not a sound CPython $VER: installing it again"
  rm -rf "${DEST:?}/$KEY"
fi
install -d -m 0755 "$DEST"
say "installing CPython $VER ($BUILD) with uv ${M[uv_version]} into $DEST"
# No configuration but this: no uv.toml, no network (the mirror is a
# folder), its cache and any launchers it makes in the work folder.
if ! env -i PATH="$PATH" HOME="$WORK" LANG=C.UTF-8 UV_NO_CONFIG=1 \
    UV_CACHE_DIR="$WORK/cache" UV_PYTHON_INSTALL_DIR="$DEST" UV_PYTHON_BIN_DIR="$WORK/bin" \
    UV_PYTHON_INSTALL_MIRROR="file://$WORK/mirror" \
    "$UV" python install "$KEY" </dev/null >&2; then
  fail "uv python install $KEY failed (the lines above say why)"
fi
chmod -R go+rX,go-w "$DEST" 2>/dev/null || true
sound || fail "uv installed $KEY, but $PY does not answer as CPython $VER with ssl, sqlite3, ctypes, zlib, venv and ensurepip"
say "CPython $VER ($BUILD) installed: $PY"
printf '%s\n' "$PY"
