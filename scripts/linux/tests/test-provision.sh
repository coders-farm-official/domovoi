#!/usr/bin/env bash
# Hermetic tests for scripts/linux/provision.sh.
#
# Each case builds a fake root directory the script reads and writes
# through DOMOVOI_PROVISION_ROOT (/etc, /proc, /var/lib, /usr/local, the
# service user's home), clones a throwaway origin repository that carries
# just the files the script reads (the pin manifest, docs/LINUX_HOST.md's
# Ollama pin, the shipped units, the real os-jobs.sh, a lock header, a stub
# install-update-unit.sh), and puts PATH shims in front of everything that
# would touch the machine: apt-get, dpkg-query, apt-mark, curl, gpg,
# systemctl, docker, useradd, usermod, passwd, id, getent, runuser, install,
# chown, flock, mount, umount, mountpoint, findmnt, timedatectl, readlink,
# visudo, sudo, journalctl, tar, python3 and the venv's python. Every shim
# logs its call and can be told to fail. git, sha256sum and the rest of
# coreutils are real. Nothing touches the host, so this runs under Git Bash
# as well as on Linux.
#
# The shims model just enough to test the script's decisions:
#   * dpkg-query answers for what the apt-get shim "installed", at the
#     version it was asked for (pkg=ver), else "1";
#   * curl serves a fake Ollama tarball for the release URL (or a tampered
#     one when ollama-tampered is set), the Docker key, Ollama's API
#     (/api/version, /api/tags, /api/pull streaming, the pull recording the
#     registry digest from the manifest, or a wrong one when
#     pull-wrong-digest is set) and the two health URLs (up while the unit
#     is active);
#   * systemctl keeps is-active state per unit (start, restart, enable
#     --now) and a failed mark (daemon-reload fails a running timer or
#     path unit whose unit file is a mask, as systemd does, and says so in
#     the state's journal file; reset-failed or a start clears it); docker
#     keeps built images and the search helper's state;
#   * the venv's python answers the script's tagged snippets
#     (#provision:...): env-write applies PROVISION_ENV_KEYS to the
#     checkout's domovoi/.env, piper-info / piper-download and
#     whisper-download write fake model files whose hashes the fixture
#     manifest pins (whisper-content and piper-content override them);
#     `-m pip install -r` fails when pip-fail is set.
#
# Usage: bash scripts/linux/tests/test-provision.sh [--case NAME|GLOB]... [--list]
# (case-filter.sh says how --case picks; scripts/dev/run-linux-harness.sh
# runs this in a pinned Linux container, much faster than Git Bash.)
# Exit status is non-zero if any case failed.

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$HERE/case-filter.sh"
harness_parse_args "$@"
SCRIPT=$(cd "$HERE/.." && pwd)/provision.sh
REAL_REPO=$(cd "$HERE/../../.." && pwd)
WORK=$(mktemp -d)
KEEP_WORK=${KEEP_WORK:-0}
cleanup() { if [ "$KEEP_WORK" = 1 ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi; }
trap cleanup EXIT

# Hermetic git: no user or system config (hooks, signing, autocrlf).
: >"$WORK/gitconfig"
export GIT_CONFIG_GLOBAL=$WORK/gitconfig GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=harness GIT_AUTHOR_EMAIL=harness@example.invalid
export GIT_COMMITTER_NAME=harness GIT_COMMITTER_EMAIL=harness@example.invalid
SHIM_REAL_INSTALL=$(command -v install)
SHIM_REAL_READLINK=$(command -v readlink)
SHIM_REAL_GIT=$(command -v git)
export SHIM_REAL_INSTALL SHIM_REAL_READLINK SHIM_REAL_GIT

# A remote's URL as git on Linux would print it (see the git shim).
origin_url() {
  local u
  u=$(git -C "$1" remote get-url origin)
  case $u in [A-Za-z]:/*) command -v cygpath >/dev/null 2>&1 && u=$(cygpath -u "$u") ;; esac
  printf '%s' "$u"
}

# A real Python for checking the JSON progress lines (not the shims'):
# HARNESS_PYTHON when the pytest wrapper sets it, as for
# test-apply-update.sh, else the first one on PATH.
JSON_PY=""
for p in "${HARNESS_PYTHON:-}" python3 python; do
  [ -n "$p" ] || continue
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import json' >/dev/null 2>&1; then JSON_PY=$(command -v "$p"); break; fi
done

PASSED=0
FAILED=0
SKIPPED=0
CASE_FAILED=0

fail() { echo "    FAIL: $*"; CASE_FAILED=1; }
check() { local what=$1; shift; if ! "$@"; then fail "$what"; fi; }

# ─── the origin repository (built once) ──────────────────────────────────

WHISPER_CONTENT="fake whisper model.bin"
PIPER_CONTENT="fake piper voice"
OLLAMA_CONTENT="fake ollama 0.34.4 tarball"

sha_of_text() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }

build_origin() {
  local o=$WORK/origin
  mkdir -p "$o/docs" "$o/scripts/linux/units" "$o/domovoi"
  git -C "$o" init -q -b main
  printf '%s' "$OLLAMA_CONTENT" >"$WORK/ollama.tar.zst"
  printf 'tampered' >"$WORK/ollama-tampered.tar.zst"
  {
    echo "# Running the Domovoi server on Linux (fixture)"
    echo '```bash'
    echo "OLLAMA_VERSION=0.34.4"
    echo "# arm64: 96f50a1192133028cf4e010d8c333f8af14b1505db6be7b2034c11487e7fd7e6"
    echo "OLLAMA_SHA256=$(sha256sum "$WORK/ollama.tar.zst" | cut -d' ' -f1)"
    echo '```'
  } >"$o/docs/LINUX_HOST.md"
  # The real manifest, with the Whisper pin swapped for the fake model's.
  sed "s/^whisper_model_bin_sha256=.*/whisper_model_bin_sha256=$(sha_of_text "$WHISPER_CONTENT")/" \
    "$REAL_REPO/scripts/linux/provision-manifest.conf" >"$o/scripts/linux/provision-manifest.conf"
  cp "$REAL_REPO"/scripts/linux/units/*.service "$REAL_REPO/scripts/linux/units/journald-domovoi.conf" \
    "$o/scripts/linux/units/"
  # The real helper: provision.sh runs it from the checkout, under the
  # same harness root.
  cp "$REAL_REPO/scripts/linux/os-jobs.sh" "$o/scripts/linux/os-jobs.sh"
  cat >"$o/scripts/linux/install-update-unit.sh" <<'SH'
#!/usr/bin/env bash
echo "install-update-unit $*" >>"$SHIM_STATE/calls.log"
# What the installer's pre-flight would report about the lock.
cp "$SHIM_ROOT/etc/default/domovoi-update" "$SHIM_STATE/update-defaults-seen" 2>/dev/null || :
mkdir -p "$SHIM_ROOT/etc/systemd/system" "$SHIM_ROOT/etc/sudoers.d"
echo unit >"$SHIM_ROOT/etc/systemd/system/domovoi-update.service"
echo grant >"$SHIM_ROOT/etc/sudoers.d/domovoi-update"
[ ! -f "$SHIM_STATE/update-unit-fail" ] || { echo "install-update-unit: stopped: fixture" >&2; exit 1; }
SH
  # A lock in pip-compile's shape: wheel needs packaging (its "# via"
  # names wheel), setuptools sits under the "unsafe" comment.
  cat >"$o/requirements-linux-py314.lock" <<'LOCK'
#
# This file is autogenerated by pip-compile with Python 3.14
# by the following command:
#
#    bash scripts/linux/compile-linux-lock.sh
#
--extra-index-url https://download.pytorch.org/whl/cpu

fastapi==1.0 \
    --hash=sha256:00
    # via -r requirements-linux-py314.in
packaging==26.2 \
    --hash=sha256:11 \
    --hash=sha256:12
    # via
    #   fastapi
    #   wheel
wheel==0.48.0 \
    --hash=sha256:22
    # via -r requirements-linux-py314.in

# The following packages are considered to be unsafe in a requirements file:
setuptools==81.0.0 \
    --hash=sha256:33
    # via -r requirements-linux-py314.in
LOCK
  printf '[project]\nname = "domovoi"\n' >"$o/pyproject.toml"
  printf 'FROM debian:bookworm-slim\n' >"$o/domovoi/Dockerfile.mpd"
  printf 'services: {}\n' >"$o/domovoi/docker-compose.yml"
  printf 'OLLAMA_URL=http://localhost:11434\nWHISPER_MODEL=large-v3\nMPD_HTTP_BASE=auto\n# MUSIC_DIR=\n' >"$o/domovoi/.env.example"
  git -C "$o" add -A && git -C "$o" commit -q -m "fixture A"
  FIXTURE_A=$(git -C "$o" rev-parse HEAD)
  echo "# B" >>"$o/pyproject.toml"
  git -C "$o" commit -q -am "fixture B"
  FIXTURE_B=$(git -C "$o" rev-parse HEAD)
  # A branch whose manifest lacks a key, and one with an unknown key.
  git -C "$o" checkout -q -b bad-manifest
  sed -i '/^docker_ce_cli=/d' "$o/scripts/linux/provision-manifest.conf"
  git -C "$o" commit -q -am "manifest missing docker_ce_cli"
  git -C "$o" checkout -q main
  git -C "$o" checkout -q -b odd-manifest
  echo "surprise_key=1" >>"$o/scripts/linux/provision-manifest.conf"
  git -C "$o" commit -q -am "manifest with an unknown key"
  git -C "$o" checkout -q main
  # A branch whose lock has no wheel to build with.
  git -C "$o" checkout -q -b no-wheel-lock
  sed -i '/^wheel==/,/# via/d' "$o/requirements-linux-py314.lock"
  git -C "$o" commit -q -am "lock without wheel"
  git -C "$o" checkout -q main
  # A branch not on main's history.
  git -C "$o" checkout -q --orphan elsewhere
  git -C "$o" commit -q -m "unrelated"
  FIXTURE_ELSEWHERE=$(git -C "$o" rev-parse HEAD)
  git -C "$o" checkout -q main
  git -C "$o" bundle create -q "$WORK/origin.bundle" --all 2>/dev/null || git -C "$o" bundle create "$WORK/origin.bundle" --all >/dev/null 2>&1
  ORIGIN=$o
}

# ─── shims ───────────────────────────────────────────────────────────────

write_shims() {
  local bin=$1
  mkdir -p "$bin"

  # Also notes what its descriptors point at: a child of the script must
  # not hold the run lock.
  cat >"$bin/apt-get" <<'SH'
#!/usr/bin/env bash
echo "apt-get $*" >>"$SHIM_STATE/calls.log"
for f in /proc/$$/fd/*; do readlink "$f" 2>/dev/null; done >>"$SHIM_STATE/apt-fds"
[ ! -f "$SHIM_STATE/apt-fail" ] || { echo "E: fixture failure" >&2; exit 100; }
install=0
for a in "$@"; do
  case $a in
    install) install=1 ;;
    -*) ;;
    *) if [ "$install" = 1 ]; then
         p=${a%%=*}; v=1; [[ $a == *=* ]] && v=${a#*=}
         grep -v "^$p=" "$SHIM_STATE/dpkg" >"$SHIM_STATE/dpkg.new" 2>/dev/null; echo "$p=$v" >>"$SHIM_STATE/dpkg.new"
         mv "$SHIM_STATE/dpkg.new" "$SHIM_STATE/dpkg"
       fi ;;
  esac
done
exit 0
SH

  cat >"$bin/dpkg-query" <<'SH'
#!/usr/bin/env bash
fmt=""; pkgs=()
while [ $# -gt 0 ]; do
  case $1 in -W) shift ;; -f) fmt=$2; shift 2 ;; -f=*) fmt=${1#-f=}; shift ;; *) pkgs+=("$1"); shift ;; esac
done
rc=0
for p in "${pkgs[@]}"; do
  line=$(grep "^$p=" "$SHIM_STATE/dpkg" 2>/dev/null | tail -n 1)
  if [ -z "$line" ]; then echo "dpkg-query: no packages found matching $p" >&2; rc=1; continue; fi
  case $fmt in
    *Status*) echo "$p install ok installed" ;;
    *Version*) echo "$line" ;;
  esac
done
exit $rc
SH

  cat >"$bin/apt-mark" <<'SH'
#!/usr/bin/env bash
echo "apt-mark $*" >>"$SHIM_STATE/calls.log"
SH

  # A key file is "KEY <fingerprint>" lines, one per primary key (the curl
  # shim serves Docker's, plus a second one when docker-extra-key is set).
  # --import keeps them in $GNUPGHOME, --list-keys / --show-keys print
  # them as gpg's colon listing, --export FPR prints that one key alone.
  cat >"$bin/gpg" <<'SH'
#!/usr/bin/env bash
echo "gpg $*" >>"$SHIM_STATE/calls.log"
mode="" arg=""
for a in "$@"; do
  case $a in --import) mode=import ;; --list-keys) mode=list ;; --show-keys) mode=show ;; --export) mode=export ;; -*) ;; *) arg=$a ;; esac
done
listing() {
  while read -r tag fpr; do
    [ "$tag" = KEY ] || continue
    echo "pub:-:4096:1:${fpr:24}:1487788586:::-:::scESA::::::23::0:"
    echo "fpr:::::::::$fpr:"
    # More output after a pause, like the subkey lines: a reader that stops
    # at the first fpr makes this write hit a closed pipe (SIGPIPE), and
    # under pipefail that used to throw the fingerprint away.
    sleep 0.3
    echo "sub:-:4096:1:7EA0A9C3F273FCD8:1487792064:::-:::s::::::23:"
    echo "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:"
  done
}
case $mode in
  import) grep -q '^KEY ' "$arg" || { echo "gpg: no valid OpenPGP data found." >&2; exit 2; }
          cat "$arg" >>"$GNUPGHOME/pubring.harness" ;;
  list) [ ! -f "$GNUPGHOME/pubring.harness" ] || listing <"$GNUPGHOME/pubring.harness" ;;
  show) listing <"$arg" ;;
  export) if grep -qx "KEY $arg" "$GNUPGHOME/pubring.harness" 2>/dev/null; then
            printf -- '-----BEGIN PGP PUBLIC KEY BLOCK-----\nKEY %s\n-----END PGP PUBLIC KEY BLOCK-----\n' "$arg"
          fi ;;
esac
exit 0
SH

  cat >"$bin/gpgconf" <<'SH'
#!/usr/bin/env bash
echo "gpgconf $*" >>"$SHIM_STATE/calls.log"
SH

  cat >"$bin/curl" <<'SH'
#!/usr/bin/env bash
url="" out="" data="" head=0
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift 2 ;;
    -d) data=$2; shift 2 ;;
    -H|-X|--max-time|--connect-timeout|--retry|--retry-delay|-C) shift 2 ;;
    -I|-fsSLI) head=1; shift ;;
    -*) [[ $1 == -*I* && $1 != --* ]] && head=1; shift ;;
    *) url=$1; shift ;;
  esac
done
echo "curl $url${out:+ -o $out}${data:+ -d $data}" >>"$SHIM_STATE/calls.log"
active() { [ -f "$SHIM_STATE/active/$1" ]; }
case $url in
  https://download.docker.com/linux/ubuntu/gpg)
    printf 'KEY %s\n' "$(cat "$SHIM_STATE/docker-fpr")" >"$out"
    [ ! -f "$SHIM_STATE/docker-extra-key" ] || printf 'KEY %s\n' "$(cat "$SHIM_STATE/docker-extra-key")" >>"$out" ;;
  https://github.com/ollama/ollama/releases/download/v*/ollama-linux-amd64.tar.zst)
    src=$SHIM_WORK/ollama.tar.zst
    [ ! -f "$SHIM_STATE/ollama-tampered" ] || src=$SHIM_WORK/ollama-tampered.tar.zst
    if [ "$head" = 1 ]; then printf 'HTTP/2 200\r\ncontent-length: %s\r\n\r\n' "$(stat -c %s "$src")"; exit 0; fi
    [ ! -f "$SHIM_STATE/download-fail" ] || { echo "curl: (56) fixture" >&2; exit 56; }
    cp "$src" "$out" ;;
  http://127.0.0.1:11434/api/version)
    active ollama.service || { echo "curl: (7) Failed to connect" >&2; exit 7; }
    echo '{"version":"0.34.4"}' ;;
  http://127.0.0.1:11434/api/tags)
    active ollama.service || exit 7
    printf '{"models":['
    sep=""
    while read -r tag dg; do
      [ -n "$tag" ] || continue
      printf '%s{"name":"%s","model":"%s","size":1,"digest":"%s","details":{"format":"gguf"}}' "$sep" "$tag" "$tag" "$dg"
      sep=","
    done <"$SHIM_STATE/ollama-models"
    printf ']}\n' ;;
  http://127.0.0.1:11434/api/pull)
    tag=$(printf '%s' "$data" | sed -n 's/.*"model":"\([^"]*\)".*/\1/p')
    echo '{"status":"pulling manifest"}'
    echo '{"status":"pulling aaaa","digest":"sha256:aaaa","total":1000,"completed":500}'
    echo '{"status":"pulling aaaa","digest":"sha256:aaaa","total":1000,"completed":1000}'
    echo '{"status":"verifying sha256 digest"}'
    dg=$(grep "^$tag " "$SHIM_STATE/registry" | head -n 1 | cut -d' ' -f2)
    [ ! -f "$SHIM_STATE/pull-wrong-digest" ] || dg=0000000000000000000000000000000000000000000000000000000000000000
    grep -v "^$tag " "$SHIM_STATE/ollama-models" >"$SHIM_STATE/om.new"; echo "$tag $dg" >>"$SHIM_STATE/om.new"
    mv "$SHIM_STATE/om.new" "$SHIM_STATE/ollama-models"
    echo '{"status":"success"}' ;;
  http://127.0.0.1:6370/v1/health)
    active domovoi-core.service && [ ! -f "$SHIM_STATE/core-unhealthy" ] || { echo "curl: (7)" >&2; exit 7; } ;;
  http://127.0.0.1:6369/api/health)
    active domovoi-web.service || { echo "curl: (7)" >&2; exit 7; } ;;
  *) echo "curl shim: unexpected URL $url" >&2; exit 6 ;;
esac
exit 0
SH

  cat >"$bin/systemctl" <<'SH'
#!/usr/bin/env bash
echo "systemctl $*" >>"$SHIM_STATE/calls.log"
mkdir -p "$SHIM_STATE/active" "$SHIM_STATE/enabled" "$SHIM_STATE/failed"
quiet=0 now=0 args=()
for a in "$@"; do
  case $a in --quiet|-q) quiet=1 ;; --now) now=1 ;; --*) ;; *) args+=("$a") ;; esac
done
cmd=${args[0]-}
units=("${args[@]:1}")
case $cmd in
  is-active)
    if [ -f "$SHIM_STATE/active/${units[0]}" ]; then [ "$quiet" = 1 ] || echo active; exit 0; fi
    [ "$quiet" = 1 ] || echo inactive; exit 3 ;;
  is-enabled)
    if [ -f "$SHIM_STATE/enabled/${units[0]}" ]; then echo enabled; exit 0; fi
    echo disabled; exit 1 ;;
  enable) for u in "${units[@]}"; do touch "$SHIM_STATE/enabled/$u"; [ "$now" = 0 ] || touch "$SHIM_STATE/active/$u"; done ;;
  disable) for u in "${units[@]}"; do rm -f "$SHIM_STATE/enabled/$u"; [ "$now" = 0 ] || rm -f "$SHIM_STATE/active/$u"; done ;;
  start|restart)
    # start-fail: every start fails; with unit names in it, only theirs.
    for u in "${units[@]}"; do
      if [ -f "$SHIM_STATE/start-fail" ] && { [ ! -s "$SHIM_STATE/start-fail" ] || grep -qx -- "$u" "$SHIM_STATE/start-fail"; }; then
        echo "Job for $u failed because the control process exited with error code." >&2; touch "$SHIM_STATE/failed/$u"; exit 1
      fi
      touch "$SHIM_STATE/active/$u"; rm -f "$SHIM_STATE/failed/$u"
    done ;;
  status)
    for u in "${units[@]}"; do
      st=inactive; [ ! -f "$SHIM_STATE/active/$u" ] || st=active; [ ! -f "$SHIM_STATE/failed/$u" ] || st=failed
      printf '%s\n' "x $u - fixture unit" "     Active: $st (fixture)"
    done
    exit 3 ;;
  stop) for u in "${units[@]}"; do rm -f "$SHIM_STATE/active/$u"; done ;;
  daemon-reload)
    # A running timer or path unit whose unit file is now a mask has
    # nothing left to trigger: the reload fails it ("Unit to trigger
    # vanished"), and only reset-failed (or a start) clears that; a stop
    # does not. A running service just keeps running.
    for f in "$SHIM_STATE/active/"*.timer "$SHIM_STATE/active/"*.path; do
      [ -e "$f" ] || continue
      u=${f##*/}
      if [ "$("$SHIM_REAL_READLINK" "$SHIM_ROOT/etc/systemd/system/$u" 2>/dev/null)" = /dev/null ]; then
        rm -f "$f"; touch "$SHIM_STATE/failed/$u"
        echo "$u: Unit to trigger vanished." >>"$SHIM_STATE/journal"
      fi
    done ;;
  reset-failed)
    if [ "${#units[@]}" -eq 0 ]; then rm -f "$SHIM_STATE/failed/"*
    else for u in "${units[@]}"; do rm -f "$SHIM_STATE/failed/$u"; done; fi ;;
esac
exit 0
SH

  cat >"$bin/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_STATE/calls.log"
mkdir -p "$SHIM_STATE/images"
# The digests the compose file pins (what `compose config --images` prints).
refs="postgres:16.15-trixie@sha256:ca0bd484cb98bf4b24eb1010e73fb3fcbd6714d240fbc1a10eea5b7dbecb641d
flyway/flyway:10.22.0@sha256:43592cc125eb0594d9b013e8dced2fa67d473c091abaa5dec03b22f49e682949"
case "$1 ${2-}" in
  "info "*) exit 0 ;;
  "version "*) echo 29.8.1 ;;
  "image inspect") [ -f "$SHIM_STATE/images/${3//[:\/@]/_}" ] || exit 1 ;;
  "build "*)
    while [ $# -gt 0 ]; do [ "$1" = -t ] && { touch "$SHIM_STATE/images/${2//[:\/@]/_}"; break; }; shift; done ;;
  "compose config") echo "$refs" ;;
  "compose pull")
    [ ! -f "$SHIM_STATE/pull-fail" ] || { echo "Error response from daemon: toomanyrequests" >&2; exit 1; }
    for r in $refs; do touch "$SHIM_STATE/images/${r//[:\/@]/_}"; done ;;
  "compose up") touch "$SHIM_STATE/searxng-running" ;;
  "inspect -f") if [ -f "$SHIM_STATE/searxng-running" ]; then echo true; else echo false; fi ;;
  "stop "*) rm -f "$SHIM_STATE/searxng-running" ;;
esac
exit 0
SH

  cat >"$bin/useradd" <<'SH'
#!/usr/bin/env bash
echo "useradd $*" >>"$SHIM_STATE/calls.log"
name=${!#}
echo "$name" >>"$SHIM_STATE/users"
mkdir -p "$SHIM_ROOT/home/$name"
SH

  cat >"$bin/usermod" <<'SH'
#!/usr/bin/env bash
echo "usermod $*" >>"$SHIM_STATE/calls.log"
[ "${1-}" = -aG ] && echo "$3 $2" >>"$SHIM_STATE/groups"
exit 0
SH

  cat >"$bin/passwd" <<'SH'
#!/usr/bin/env bash
echo "passwd $*" >>"$SHIM_STATE/calls.log"
SH

  cat >"$bin/id" <<'SH'
#!/usr/bin/env bash
known() { [ "$1" = root ] || grep -qx -- "$1" "$SHIM_STATE/users" 2>/dev/null; }
case "${1-}" in
  -u)
    if [ $# -eq 1 ]; then cat "$SHIM_STATE/uid" 2>/dev/null || echo 0; exit 0; fi
    known "$2" || { echo "id: '$2': no such user" >&2; exit 1; }
    [ "$2" = root ] && echo 0 || echo 1000 ;;
  -g) known "$2" || exit 1; [ "$2" = root ] && echo 0 || echo 1000 ;;
  -nG)
    known "$2" || exit 1
    printf '%s' "$2"; grep "^$2 " "$SHIM_STATE/groups" 2>/dev/null | cut -d' ' -f2 | while read -r g; do printf ' %s' "$g"; done; echo ;;
  -un) echo root ;;
  *) echo "id shim: $*" >&2; exit 1 ;;
esac
SH

  cat >"$bin/getent" <<'SH'
#!/usr/bin/env bash
[ "${1-}" = passwd ] && grep -qx -- "${2-}" "$SHIM_STATE/users" 2>/dev/null || exit 2
printf '%s:x:1000:1000::/home/%s:/bin/bash\n' "$2" "$2"
SH

  cat >"$bin/runuser" <<'SH'
#!/usr/bin/env bash
echo "runuser $*" >>"$SHIM_STATE/calls.log"
while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done
shift
exec "$@"
SH

  # Drops -o/-g (no root user to hand files to here), and a directory's
  # mode (Git Bash can't chmod a directory to 0750).
  cat >"$bin/install" <<'SH'
#!/usr/bin/env bash
echo "install $*" >>"$SHIM_STATE/calls.log"
args=() dir=0 mode=()
while [ $# -gt 0 ]; do
  case $1 in -o|-g) shift 2 ;; -d) dir=1; args+=("$1"); shift ;; -m) mode=(-m "$2"); shift 2 ;; *) args+=("$1"); shift ;; esac
done
[ "$dir" = 1 ] && mode=()
exec "$SHIM_REAL_INSTALL" "${mode[@]}" "${args[@]}"
SH

  # Git for Windows records a cloned path as C:/...: show it the way the
  # script was given it, as git on Linux does.
  cat >"$bin/git" <<'SH'
#!/usr/bin/env bash
if [[ " $* " == *" remote get-url "* ]] && command -v cygpath >/dev/null 2>&1; then
  u=$("$SHIM_REAL_GIT" "$@") || exit $?
  case $u in [A-Za-z]:/*) cygpath -u "$u" ;; *) printf '%s\n' "$u" ;; esac
  exit 0
fi
exec "$SHIM_REAL_GIT" "$@"
SH

  # Fails for a path that contains the text in chown-fail (a write that
  # stops half-way).
  cat >"$bin/chown" <<'SH'
#!/usr/bin/env bash
echo "chown $*" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/chown-fail" ]; then
  for a in "$@"; do case $a in *"$(cat "$SHIM_STATE/chown-fail")"*) echo "chown: fixture failure" >&2; exit 1 ;; esac; done
fi
exit 0
SH

  # flock [-n] [-E CODE] [-o] FILE COMMAND...: busy (exit CODE, 1 without
  # -E) when the case holds the lock, else runs COMMAND, as flock does.
  cat >"$bin/flock" <<'SH'
#!/usr/bin/env bash
echo "flock $*" >>"$SHIM_STATE/calls.log"
code=1
while [ $# -gt 0 ]; do
  case $1 in -n|-o) shift ;; -E) code=$2; shift 2 ;; *) break ;; esac
done
shift
[ ! -f "$SHIM_STATE/locked" ] || exit "$code"
[ $# -eq 0 ] || exec "$@"
SH

  cat >"$bin/mountpoint" <<'SH'
#!/usr/bin/env bash
grep -qx -- "${!#}" "$SHIM_STATE/mounts" 2>/dev/null
SH

  # mount-fail: exits 32. mount-silent-fail: exits 0 and mounts nothing,
  # which is what WSL 3.0.1's mount.drvfs does for a Windows folder that
  # is gone (seen in domovoi-t5, 2026-10-09).
  cat >"$bin/mount" <<'SH'
#!/usr/bin/env bash
echo "mount $*" >>"$SHIM_STATE/calls.log"
[ ! -f "$SHIM_STATE/mount-fail" ] || { echo "mount: fixture: No such file or directory" >&2; exit 32; }
[ ! -f "$SHIM_STATE/mount-silent-fail" ] || exit 0
echo "${!#}" >>"$SHIM_STATE/mounts"
SH

  cat >"$bin/umount" <<'SH'
#!/usr/bin/env bash
echo "umount $*" >>"$SHIM_STATE/calls.log"
grep -vx -- "${!#}" "$SHIM_STATE/mounts" >"$SHIM_STATE/mounts.new"; mv "$SHIM_STATE/mounts.new" "$SHIM_STATE/mounts"
SH

  cat >"$bin/findmnt" <<'SH'
#!/usr/bin/env bash
echo "9p drvfs"
SH

  cat >"$bin/timedatectl" <<'SH'
#!/usr/bin/env bash
echo "timedatectl $*" >>"$SHIM_STATE/calls.log"
[ "${1-}" = set-timezone ] && echo "/usr/share/zoneinfo/$2" >"$SHIM_ROOT/etc/localtime.harness-link"
exit 0
SH

  cat >"$bin/readlink" <<'SH'
#!/usr/bin/env bash
if [ -f "${!#}.harness-link" ]; then cat "${!#}.harness-link"; exit 0; fi
exec "$SHIM_REAL_READLINK" "$@"
SH

  cat >"$bin/visudo" <<'SH'
#!/usr/bin/env bash
echo "visudo $*" >>"$SHIM_STATE/calls.log"
[ ! -f "$SHIM_STATE/visudo-reject" ] || { echo "syntax error"; exit 1; }
echo "parsed OK"
SH

  cat >"$bin/sudo" <<'SH'
#!/usr/bin/env bash
echo "sudo $*" >>"$SHIM_STATE/calls.log"
exit 0
SH

  # A unit's journal: the case's text, if it set one.
  cat >"$bin/journalctl" <<'SH'
#!/usr/bin/env bash
echo "journalctl $*" >>"$SHIM_STATE/calls.log"
[ ! -f "$SHIM_STATE/journal" ] || cat "$SHIM_STATE/journal"
exit 0
SH

  cat >"$bin/tar" <<'SH'
#!/usr/bin/env bash
echo "tar $*" >>"$SHIM_STATE/calls.log"
dest=""
while [ $# -gt 0 ]; do [ "$1" = -C ] && dest=$2; shift; done
mkdir -p "$dest/bin" "$dest/lib/ollama"
printf '#!/bin/sh\nexit 0\n' >"$dest/bin/ollama"; chmod +x "$dest/bin/ollama"
SH

  cat >"$bin/python3" <<'SH'
#!/usr/bin/env bash
echo "python3 $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = -c ]; then cat "$SHIM_STATE/py-version" 2>/dev/null || echo 3.14; exit 0; fi
if [ "${1-}" = -m ] && [ "${2-}" = venv ]; then
  mkdir -p "$3/bin"; : >"$3/pyvenv.cfg"
  cp "$SHIM_VENV_TEMPLATE" "$3/bin/python"; chmod +x "$3/bin/python"; exit 0
fi
exit 1
SH

  chmod +x "$bin"/*

  # The venv's python, copied in by `python3 -m venv`.
  cat >"$WORK/venv-python" <<'SH'
#!/usr/bin/env bash
echo "venv-python $*" >>"$SHIM_STATE/calls.log"
envf=domovoi/.env
case "${1-} ${2-}" in
  "-m pip")
    # Like pip: an install option in front of the subcommand is refused.
    for a in "${@:3}"; do
      case $a in
        install|download|show|freeze|uninstall|list) break ;;
        --progress-bar*|--require-hashes|--no-build-isolation|--no-deps|-r|-c|-e) echo "no such option: $a" >&2; exit 2 ;;
      esac
    done
    prev=""
    for a in "$@"; do
      if [ "$prev" = -r ] && [[ $a == *build-tools* ]]; then cp "$a" "$SHIM_STATE/pip-build-tools"; fi
      prev=$a
    done
    if [ -f "$SHIM_STATE/pip-fail" ] && [[ " $* " == *" -r requirements-linux-py314.lock "* ]]; then echo "ERROR: fixture: hash mismatch" >&2; exit 1; fi
    exit 0 ;;
  "-m domovoi.env_bootstrap")
    if [ "${3-}" = --repair ]; then exit 0; fi
    if [ ! -f "$envf" ]; then
      cp domovoi/.env.example "$envf"
      printf 'POSTGRES_PASSWORD=generated\nLETTA_TOKEN=a\nSEARXNG_SECRET=b\n' >>"$envf"
      [ "${3-}" != --internet ] || printf 'INTERNET_ACCESS=%s\n' "$4" >>"$envf"
    fi
    exit 0 ;;
  "-m domovoi.egress")
    sed -n 's/^INTERNET_ACCESS=//p' "$envf" 2>/dev/null | tail -n 1; exit 0 ;;
esac
if [ "${1-}" = -c ]; then
  tag=$(printf '%s\n' "$2" | head -n 1)
  case $tag in
    "#provision:import-check") [ ! -f "$SHIM_STATE/import-fail" ] || { echo "import failures: torch"; exit 1; }; echo "imports OK"; exit 0 ;;
    "#provision:env-read")
      # The last KEY= line of each name asked for, quotes stripped.
      for k in $PROVISION_ENV_NAMES; do
        grep -q "^$k=" "$envf" || continue
        v=$(sed -n "s/^$k=//p" "$envf" | tail -n 1); v=${v%\"}; v=${v#\"}
        echo "$k=$v"
      done | awk '!seen[$0]++'
      exit 0 ;;
    "#provision:env-write")
      # Like config_env_writer.write_env_values: a key's line rewritten in
      # place, a new key appended.
      [ ! -f "$SHIM_STATE/env-write-fail" ] || { echo "OSError: fixture" >&2; exit 1; }
      while IFS= read -r kv; do
        [ -n "$kv" ] || continue
        k=${kv%%=*}
        if grep -q "^$k=" "$envf"; then
          awk -v k="$k" -v kv="$kv" 'index($0, k "=") == 1 {print kv; next} {print}' "$envf" >"$envf.new"; mv "$envf.new" "$envf"
        else
          echo "$kv" >>"$envf"
        fi
      done <<<"$PROVISION_ENV_KEYS"
      exit 0 ;;
    "#provision:env-verify")
      # Asked only about keys the file holds as asked.
      while IFS= read -r kv; do
        [ -n "$kv" ] || continue
        k=${kv%%=*}
        [ "$(sed -n "s/^$k=//p" "$envf" | tail -n 1)" = "${kv#*=}" ] || { echo "$k: the core would read something else"; exit 1; }
      done <<<"$PROVISION_ENV_KEYS"
      exit 0 ;;
    "#provision:piper-info")
      echo "$HOME/.domovoi/piper_voices"
      # piper-configured: what the core's settings name first, printed the
      # way the real snippet prints an unpinned voice (an empty name is
      # what a stray blank line would look like).
      if [ -f "$SHIM_STATE/piper-configured" ]; then
        cat "$SHIM_STATE/piper-configured"; echo -; echo 0
      fi
      for v in en_US-norman-medium en_US-kristin-medium en_US-ljspeech-medium; do
        echo "$v"; cat "$SHIM_STATE/piper-pin"; echo 16
      done
      exit 0 ;;
    "#provision:piper-download")
      d=$SHIM_ROOT$4; mkdir -p "$d"
      cat "$SHIM_STATE/piper-content" >"$d/$3.onnx"; echo '{}' >"$d/$3.onnx.json"; echo "$d/$3.onnx"; exit 0 ;;
    "#provision:whisper-download")
      d=$SHIM_ROOT$HOME/.cache/huggingface/hub/models--Systran--faster-whisper-small.en/snapshots/$4
      mkdir -p "$d"; cat "$SHIM_STATE/whisper-content" >"$d/model.bin"; echo "$d"; exit 0 ;;
  esac
  # check_venv's quick import
  [ ! -f "$SHIM_STATE/venv-broken" ]; exit $?
fi
exit 0
SH
  chmod +x "$WORK/venv-python"
}

# ─── per case ────────────────────────────────────────────────────────────

NEVER_UNITS=(apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service motd-news.timer
  ua-timer.timer apport-autoreport.path apport-autoreport.timer snapd.snap-repair.timer)
SNAPD_UNITS=(snapd.service snapd.socket snapd.seeded.service snapd.snap-repair.timer)
VENDOR_UNITS=(apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service motd-news.timer
  ua-timer.timer apport-autoreport.path apport-autoreport.timer snapd.snap-repair.timer
  snapd.service snapd.socket snapd.seeded.service wsl-pro.service)

# new_case NAME [--bare]: a fake root for a WSL box (or bare metal), 64 GB.
new_case() {
  CASE_NAME=$1
  CASE_FAILED=0
  CASE=$WORK/$1
  ROOT=$CASE/root
  STATE=$CASE/state
  CHECKOUT=$CASE/opt/domovoi
  mkdir -p "$ROOT/etc/systemd/system" "$ROOT/etc/sudoers.d" "$ROOT/proc/1" "$ROOT/proc/sys/kernel/random" \
    "$ROOT/usr/share/zoneinfo/America" "$ROOT/usr/share/zoneinfo/Etc" "$ROOT/usr/bin" "$ROOT/usr/local" \
    "$STATE" "$CASE/tmp" "$CASE/opt"
  printf 'ID=ubuntu\nVERSION_ID="26.04"\nPRETTY_NAME="Ubuntu 26.04.1 LTS"\n' >"$ROOT/etc/os-release"
  printf '# UNCONFIGURED FSTAB FOR BASE SYSTEM\n' >"$ROOT/etc/fstab"
  printf '[boot]\nsystemd=true\n' >"$ROOT/etc/wsl.conf"
  echo systemd >"$ROOT/proc/1/comm"
  echo "1 (systemd) S 0 1 1 0 -1 4194560 1 1 0 0 1 1 0 0 20 0 1 0 1111 1 1" >"$ROOT/proc/1/stat"
  echo 0b1d-boot >"$ROOT/proc/sys/kernel/random/boot_id"
  if [ "${2-}" = --bare ]; then echo 6.8.0-45-generic >"$ROOT/proc/sys/kernel/osrelease"
  else echo 6.6.87.2-microsoft-standard-WSL2 >"$ROOT/proc/sys/kernel/osrelease"; fi
  printf 'MemTotal:       65536000 kB\nMemFree:  1 kB\n' >"$ROOT/proc/meminfo"
  : >"$ROOT/usr/share/zoneinfo/America/New_York"
  : >"$ROOT/usr/share/zoneinfo/Etc/UTC"
  : >"$ROOT/etc/localtime"
  # WSL writes /etc/timezone at each distro start; systemd never updates it.
  echo Etc/UTC >"$ROOT/etc/timezone"
  echo /usr/share/zoneinfo/Etc/UTC >"$ROOT/etc/localtime.harness-link"
  printf '#!/bin/sh\n' >"$ROOT/usr/bin/docker"; chmod +x "$ROOT/usr/bin/docker"
  # Ubuntu's own units os-jobs.sh decides about, as the stock image ships
  # them (wsl-pro only matters on WSL, and is left alone elsewhere).
  mkdir -p "$ROOT/usr/lib/systemd/system"
  for u in "${VENDOR_UNITS[@]}"; do : >"$ROOT/usr/lib/systemd/system/$u"; done
  write_shims "$CASE/bin"
  : >"$STATE/calls.log"; : >"$STATE/dpkg"; : >"$STATE/ollama-models"; : >"$STATE/users"; : >"$STATE/mounts"
  echo 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 >"$STATE/docker-fpr"
  printf '%s' "$WHISPER_CONTENT" >"$STATE/whisper-content"
  printf '%s' "$PIPER_CONTENT" >"$STATE/piper-content"
  sha_of_text "$PIPER_CONTENT" >"$STATE/piper-pin"
  # What the registry serves for each tag: the manifest's digests.
  sed -n 's/^tier_[a-z]*_\(tool\|qa\|vision\)_model=\(.*\)$/\2/p' "$ORIGIN/scripts/linux/provision-manifest.conf" >"$CASE/tags"
  : >"$STATE/registry"
  for t in floor_tool floor_qa floor_vision standard_tool standard_qa standard_vision; do
    printf '%s %s\n' "$(sed -n "s/^tier_${t}_model=//p" "$ORIGIN/scripts/linux/provision-manifest.conf")" \
      "$(sed -n "s/^tier_${t}_digest=//p" "$ORIGIN/scripts/linux/provision-manifest.conf")" >>"$STATE/registry"
  done
  export SHIM_STATE=$STATE SHIM_ROOT=$ROOT SHIM_WORK=$WORK SHIM_VENV_TEMPLATE=$WORK/venv-python
}

# run_prov [ARGS...]: the script against the current case, with the
# options every case needs.
run_prov() {
  RC=0
  : >"$STATE/calls.log"
  env PATH="$CASE/bin:$PATH" DOMOVOI_PROVISION_ROOT="$ROOT" DOMOVOI_PROVISION_TEST_HARNESS=1 TMPDIR="$CASE/tmp" \
    bash "$SCRIPT" --repo-url "$ORIGIN" --checkout-dir "$CHECKOUT" "$@" >"$CASE/output.log" 2>&1 || RC=$?
  grep '^@@DOMOVOI ' "$CASE/output.log" | sed 's/^@@DOMOVOI //' >"$CASE/progress.jsonl" || true
}
# No --timezone: on WSL the zone follows Windows (owner decision
# 2026-10-11), and the option is ignored there with a warning.
wsl_args=(--internet always)

called() { grep -qF -- "$1" "$STATE/calls.log"; }
not_called() { ! grep -qF -- "$1" "$STATE/calls.log"; }
# before A B: the first call matching A precedes the first matching B.
before() {
  local a b
  a=$(grep -nF -- "$1" "$STATE/calls.log" | head -n 1 | cut -d: -f1)
  b=$(grep -nF -- "$2" "$STATE/calls.log" | head -n 1 | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ] || { echo "      [$1] does not come before [$2]"; return 1; }
}
said() { grep -qF -- "$1" "$CASE/output.log"; }
not_said() { ! grep -qF -- "$1" "$CASE/output.log"; }
eq() { [ "$1" = "$2" ] || { echo "      expected [$2], got [$1]"; return 1; }; }
has_line() { grep -qxF -- "$2" "$1" 2>/dev/null || { echo "      no line [$2] in $1"; return 1; }; }
absent() { [ ! -e "$1" ]; }
masked() { [ "$("$SHIM_REAL_READLINK" "$ROOT/etc/systemd/system/$1" 2>/dev/null)" = /dev/null ]; }
not_masked() { ! masked "$1"; }
marker() { [ -s "$ROOT/var/lib/domovoi-provision/steps/$1" ]; }
no_marker() { [ ! -e "$ROOT/var/lib/domovoi-provision/steps/$1" ]; }

# The state of STEP in the progress lines, the last one that names it.
state_of() { grep "\"step\":\"$1\"" "$CASE/progress.jsonl" | grep -v '"final":true' | tail -n 1 | sed -n 's/.*"state":"\([a-z]*\)".*/\1/p'; }
final_field() { grep '"final":true' "$CASE/progress.jsonl" | tail -n 1 | sed -n "s/.*\"$1\":\(\"[^\"]*\"\|[a-z0-9]*\).*/\1/p"; }

# Every progress line is a JSON object with the protocol's fields, the
# percent never goes down, and exactly one final line ends the stream.
progress_ok() {
  [ -s "$CASE/progress.jsonl" ] || { echo "      no progress lines"; return 1; }
  if [ -z "$JSON_PY" ]; then
    grep -qv '^{"v":1,"step":"[a-z-]*","index":[0-9]*,"total":21,"state":"[a-z]*","percent":[0-9]*,"message":"' "$CASE/progress.jsonl" \
      && { echo "      a progress line does not match the protocol"; return 1; }
    return 0
  fi
  "$JSON_PY" - "$CASE/progress.jsonl" <<'PY'
import json, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
last = -1
finals = 0
for n, l in enumerate(lines, 1):
    o = json.loads(l)
    for k in ("v", "step", "index", "total", "state", "percent", "message"):
        assert k in o, "line %d lacks %s: %s" % (n, k, l)
    assert o["v"] == 1 and o["total"] == 21, l
    assert o["state"] in ("start", "progress", "done", "skip", "fail", "plan"), l
    assert 0 <= o["percent"] <= 100, l
    if o["state"] != "fail" and not o.get("final"):
        assert o["percent"] >= last, "percent went down at line %d: %s" % (n, l)
        last = o["percent"]
    if "bytes" in o:
        assert isinstance(o["bytes"], int) and (o["bytes_total"] is None or isinstance(o["bytes_total"], int)), l
    if o.get("final"):
        finals += 1
        assert n == len(lines), "the final line is not the last"
        for k in ("result", "restart_required", "exit_code", "failed_step", "gpu"):
            assert k in o, "final lacks " + k
        assert isinstance(o["restart_required"], bool)
        # A list of names ([] = the CPU alone), or null while unknown.
        assert o["gpu"] is None or (isinstance(o["gpu"], list) and all(isinstance(g, str) and g for g in o["gpu"])), l
assert finals == 1, "%d final lines" % finals
PY
}

tree_sig() {
  (cd "$ROOT" && find . -path ./var/lib/domovoi-provision/result.json -prune -o -print | LC_ALL=C sort \
    && find . -type f ! -name result.json -exec cksum {} + | LC_ALL=C sort)
}

end_case() {
  if [ "$CASE_FAILED" = 0 ]; then
    PASSED=$((PASSED + 1)); echo "ok   $CASE_NAME"
  else
    FAILED=$((FAILED + 1)); echo "FAIL $CASE_NAME"
    echo "    --- calls"; sed 's/^/    /' "$STATE/calls.log" | tail -n 60
    echo "    --- output"; sed 's/^/    /' "$CASE/output.log" | tail -n 80
  fi
}

STEPS=(preflight apt user wsl-config media checkout docker venv env ollama models whisper piper images searxng os-jobs units timezone start update-unit)

# ─── cases ───────────────────────────────────────────────────────────────

case_help_and_usage_errors() {
  new_case help_and_usage_errors
  run_prov --help
  check "--help exits 0" eq "$RC" 0
  check "--help prints the options" said "--media NAME=PATH"
  run_prov --bogus; check "unknown option: 2" eq "$RC" 2
  run_prov --internet maybe; check "--internet maybe: 2" eq "$RC" 2
  run_prov --tier huge "${wsl_args[@]}"; check "--tier huge: 2" eq "$RC" 2
  run_prov --ref 'a..b' "${wsl_args[@]}"; check "--ref with ..: 2" eq "$RC" 2
  run_prov --user root "${wsl_args[@]}"; check "--user root: 2" eq "$RC" 2
  run_prov --lan-ip 300.1.1.1 "${wsl_args[@]}"; check "bad --lan-ip: 2" eq "$RC" 2
  run_prov --internet always --timezone 'Bad Zone'; check "bad --timezone: 2" eq "$RC" 2
  run_prov --media 'music=C:\' "${wsl_args[@]}"; check "a whole drive: 2" eq "$RC" 2
  check "says why" said "never a whole drive"
  run_prov --media 'music=C:\a\..\b' "${wsl_args[@]}"; check ".. in a media path: 2" eq "$RC" 2
  run_prov --media 'music=/srv/music' "${wsl_args[@]}"; check "a Linux path on WSL: 2" eq "$RC" 2
  run_prov --media 'Music=C:\M' "${wsl_args[@]}"; check "upper-case name: 2" eq "$RC" 2
  run_prov --media 'music=C:\a' --media 'music=C:\b' "${wsl_args[@]}"; check "a name twice: 2" eq "$RC" 2
  run_prov; check "WSL without --internet: 2" eq "$RC" 2
  check "says --internet is required" said "--internet is required on WSL"
  run_prov --no-wsl --media 'music=relative/dir'; check "relative Linux media path: 2" eq "$RC" 2
  check "nothing ran" not_called "apt-get"
  end_case
}

# The install's import check names only what the release installs: the
# Microsoft Edge voices' edge-tts and its MP3 decoder miniaudio were
# dropped on 2026-10-12, so a fresh venv without them must pass it.
case_import_check_names_no_dropped_package() {
  new_case import_check_names_no_dropped_package
  local mods
  mods=$(sed -n "/^PY_IMPORT_CHECK='#provision:import-check/,/^bad = \[\]/p" "$SCRIPT")
  check "found the import check" grep -qF '"onnxruntime"' <<<"$mods"
  check "no edge_tts" eq "$(grep -c 'edge_tts' <<<"$mods")" 0
  check "no miniaudio" eq "$(grep -c 'miniaudio' <<<"$mods")" 0
  end_case
}

case_harness_vars_need_each_other() {
  new_case harness_vars_need_each_other
  RC=0
  env -u DOMOVOI_PROVISION_TEST_HARNESS DOMOVOI_PROVISION_ROOT="$ROOT" PATH="$CASE/bin:$PATH" \
    bash "$SCRIPT" --dry-run >"$CASE/output.log" 2>&1 || RC=$?
  check "ROOT alone: exit 2" eq "$RC" 2
  check "says they go together" said "only together"
  end_case
}

case_not_root_refuses() {
  new_case not_root_refuses
  echo 1000 >"$STATE/uid"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "says to run as root" said "run it as root"
  check "changed nothing" not_called "apt-get"
  check "one final line" progress_ok
  end_case
}

case_dry_run_changes_nothing() {
  new_case dry_run_changes_nothing
  local sig; sig=$(tree_sig)
  run_prov --dry-run "${wsl_args[@]}" --media 'music=C:\Users\Public\Domovoi\My Music' --lan-ip 192.168.1.50
  check "exit 0" eq "$RC" 0
  check "every file as it was" eq "$(tree_sig)" "$sig"
  check "no checkout" absent "$CHECKOUT"
  check "nothing installed" not_called "apt-get"
  check "no user created" not_called "useradd"
  check "no mount" not_called "mount "
  check "plans wsl.conf" said "default=domovoi"
  check "plans interop off" said "enabled=false"
  check "plans the fstab line" said 'C:/Users/Public/Domovoi/My\040Music /srv/media/music drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0'
  check "plans MUSIC_DIR (manifest beside the script)" said "MUSIC_DIR=/srv/media/music"
  check "plans the standard tier from 64 GB" said "OLLAMA_TOOL_MODEL=qwen3:8b-q4_K_M"
  check "plans the vision model" said "OLLAMA_VISION_MODEL=qwen3-vl:2b-instruct-q4_K_M"
  check "plans its pull, by digest" said "ollama pull qwen3-vl:2b-instruct-q4_K_M ea422f1e73652a95479954d8572d3c8c6022f628ce2d38a1a04aae1b7f2d5300"
  check "plans MPD_HTTP_BASE" said "MPD_HTTP_BASE=http://192.168.1.50"
  check "plan lines for every step but the preflight" eq "$(grep -c '"state":"plan"' "$CASE/progress.jsonl")" 19
  check "the preflight runs for real" eq "$(state_of preflight)" done
  check "result dry_run" eq "$(final_field result)" '"dry_run"'
  check "progress format" progress_ok
  end_case
}

case_full_run_wsl() {
  new_case full_run_wsl
  # As if the core had written its setup code: it must never be printed.
  mkdir -p "$ROOT/home/domovoi/.domovoi"
  echo SENTINEL-SETUP-CODE >"$ROOT/home/domovoi/.domovoi/setup-code.txt"
  # Ollama's journal on a box with two NVIDIA cards (the dev box's lines of
  # 2026-10-09), after an earlier start that found only the CPU.
  printf '%s\n' \
    'time=2026-10-09T18:10:00.000-04:00 level=INFO source=routes.go:2005 msg="server config" env="map[CUDA_VISIBLE_DEVICES:]"' \
    'time=2026-10-09T18:10:01.000-04:00 level=INFO source=types.go:32 msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="62.5 GiB"' \
    'time=2026-10-09T18:20:11.627-04:00 level=INFO source=routes.go:2005 msg="server config" env="map[CUDA_VISIBLE_DEVICES:]"' \
    'time=2026-10-09T18:20:13.987-04:00 level=INFO source=types.go:32 msg="inference compute" id=0 filter_id=0 library=CUDA compute=8.9 name=CUDA0 description="NVIDIA GeForce RTX 4070 Ti SUPER" libdirs=ollama,cuda_v13 driver=13.1 pci_id=0000:01:00.0 type=discrete total="16.0 GiB" available="14.7 GiB"' \
    'time=2026-10-09T18:20:13.987-04:00 level=INFO source=types.go:32 msg="inference compute" id=1 filter_id=1 library=CUDA compute=8.9 name=CUDA1 description="NVIDIA GeForce RTX 4070 Ti SUPER" libdirs=ollama,cuda_v13 driver=13.1 pci_id=0000:06:00.0 type=discrete total="16.0 GiB" available="14.7 GiB"' \
    >"$STATE/journal"
  run_prov "${wsl_args[@]}" --media 'music=C:\Users\Public\Domovoi\My Music' --media 'videos=D:\Videos' --lan-ip 192.168.1.50
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "result ok" eq "$(final_field result)" '"ok"'
  check "restart required (wsl.conf changed)" eq "$(final_field restart_required)" true
  check "100% at the end" eq "$(final_field percent)" 100
  check "one warning" eq "$(final_field warnings)" 1
  check "the one warning: signed updates not enforced" said "signed updates are not enforced"
  # GPU: what Ollama found at its last start, for the installer to show.
  check "both GPUs in the final line" grep -qF '"gpu":["NVIDIA GeForce RTX 4070 Ti SUPER","NVIDIA GeForce RTX 4070 Ti SUPER"]' "$CASE/progress.jsonl"
  check "and in result.json" grep -qF '"gpu":["NVIDIA GeForce RTX 4070 Ti SUPER","NVIDIA GeForce RTX 4070 Ti SUPER"]' "$ROOT/var/lib/domovoi-provision/result.json"
  check "said what Ollama runs on" said "Ollama runs on: NVIDIA GeForce RTX 4070 Ti SUPER, NVIDIA GeForce RTX 4070 Ti SUPER"
  check "no GPU pinned away from Ollama" eq "$(grep -c CUDA_VISIBLE_DEVICES "$ROOT/etc/systemd/system/ollama.service")" 0
  local s
  for s in "${STEPS[@]}"; do
    check "step $s done" eq "$(state_of "$s")" done
    [ "$s" = preflight ] || check "marker for $s" marker "$s"
  done
  # WSL files
  check "wsl.conf: systemd" has_line "$ROOT/etc/wsl.conf" "systemd=true"
  check "wsl.conf: default user" has_line "$ROOT/etc/wsl.conf" "default=domovoi"
  check "wsl.conf: automount off, fstab on" has_line "$ROOT/etc/wsl.conf" "mountFsTab=true"
  check "wsl.conf: no Windows PATH" has_line "$ROOT/etc/wsl.conf" "appendWindowsPath=false"
  check "wsl.conf: interop off" eq "$(sed -n '/^\[interop\]/,/^\[/p' "$ROOT/etc/wsl.conf" | grep -c '^enabled=false')" 1
  check "wsl.conf: automount off" eq "$(sed -n '/^\[automount\]/,/^\[/p' "$ROOT/etc/wsl.conf" | grep -c '^enabled=false')" 1
  for u in systemd-resolved.service systemd-networkd.service NetworkManager.service systemd-tmpfiles-setup.service \
           systemd-tmpfiles-clean.service systemd-tmpfiles-clean.timer systemd-tmpfiles-setup-dev-early.service \
           systemd-tmpfiles-setup-dev.service tmp.mount sleep.target suspend.target hibernate.target hybrid-sleep.target; do
    check "$u masked" eq "$("$SHIM_REAL_READLINK" "$ROOT/etc/systemd/system/$u")" /dev/null
  done
  # media
  check "fstab keeps its own lines" has_line "$ROOT/etc/fstab" "# UNCONFIGURED FSTAB FOR BASE SYSTEM"
  check "fstab: music, spaces escaped, forward slashes" has_line "$ROOT/etc/fstab" \
    'C:/Users/Public/Domovoi/My\040Music /srv/media/music drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0'
  check "fstab: videos" has_line "$ROOT/etc/fstab" 'D:/Videos /srv/media/videos drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0'
  check "one block" eq "$(grep -c '^# BEGIN Domovoi media folders' "$ROOT/etc/fstab")" 1
  check "mount points made" test -d "$ROOT/srv/media/music"
  check "mounted now" called "mount /srv/media/music"
  check "videos has a setting now: no warning" not_said "no Domovoi setting reads a 'videos' folder"
  # checkout
  check "cloned" test -d "$CHECKOUT/.git"
  check "on main, tracking origin/main" eq "$(git -C "$CHECKOUT" rev-parse --abbrev-ref '@{u}')" origin/main
  check "HEAD in the final line" eq "$(final_field head)" "\"$FIXTURE_B\""
  check "cloned as the service user" called "runuser -u domovoi -- env HOME=/home/domovoi"
  check "cloned beside the checkout, then renamed into place" said "the checkout is in place at $CHECKOUT"
  check "no clone left beside it" absent "$CASE/opt/.domovoi.provision-clone"
  # user
  check "the user this run created has no password login" called "passwd -l domovoi"
  # docker
  check "docker key read through a throwaway keyring" called "gpg --batch --no-tty --quiet --import"
  check "docker key installed" test -f "$ROOT/etc/apt/keyrings/docker.asc"
  check "the pinned key alone (gpg --export)" eq "$(grep -c '^KEY ' "$ROOT/etc/apt/keyrings/docker.asc")" 1
  check "the pinned key" has_line "$ROOT/etc/apt/keyrings/docker.asc" "KEY 9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
  check "docker.sources" has_line "$ROOT/etc/apt/sources.list.d/docker.sources" "Suites: resolute"
  check "docker-ce at the pin" called "docker-ce=5:29.8.1-1~ubuntu.26.04~resolute"
  check "compose plugin at the pin" called "docker-compose-plugin=5.5.1-1~ubuntu.26.04~resolute"
  check "not held (apt upgrade moves them, as on the Beelink)" not_called "apt-mark"
  check "user in the docker group" called "usermod -aG docker domovoi"
  # venv: the three LINUX_HOST.md installs, in order
  check "build tools from the lock" called "-m pip --disable-pip-version-check --no-input install --require-hashes -r $ROOT/var/cache/domovoi-provision/build-tools."
  check "not the -c form pip 25.1 refuses" not_called "-c requirements-linux-py314.lock setuptools wheel"
  check "no install option before the subcommand" not_called "--progress-bar off install"
  check "pip's progress bar off through the environment" called "PIP_PROGRESS_BAR=off"
  check "every pin, hash-checked" called "install --require-hashes --no-build-isolation -r requirements-linux-py314.lock"
  check "domovoi itself, no deps" called "install --no-deps --no-build-isolation -e ."
  # .env
  local envf=$CHECKOUT/domovoi/.env
  check ".env: internet answer" has_line "$envf" "INTERNET_ACCESS=always"
  check ".env: tier tool model" has_line "$envf" "OLLAMA_TOOL_MODEL=qwen3:8b-q4_K_M"
  check ".env: QA model" has_line "$envf" "OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M"
  check ".env: vision model" has_line "$envf" "OLLAMA_VISION_MODEL=qwen3-vl:2b-instruct-q4_K_M"
  check ".env: Whisper on cpu/int8" has_line "$envf" "WHISPER_COMPUTE_TYPE=int8"
  check ".env: the template's Whisper replaced" eq "$(grep -c '^WHISPER_MODEL=' "$envf")" 1
  check ".env: Ollama by IPv4" has_line "$envf" "OLLAMA_URL=http://127.0.0.1:11434"
  check ".env: no USB adoption on WSL" has_line "$envf" "SATELLITE_ADOPTION_ENABLED=false"
  check ".env: MPD_HTTP_BASE" has_line "$envf" "MPD_HTTP_BASE=http://192.168.1.50"
  check ".env: MUSIC_DIR" has_line "$envf" "MUSIC_DIR=/srv/media/music"
  check ".env: VIDEOS_DIR (the core's Videos library)" has_line "$envf" "VIDEOS_DIR=/srv/media/videos"
  check ".env: VIDEOS_DIR recorded as the script's" has_line "$ROOT/var/lib/domovoi-provision/env-written" "VIDEOS_DIR=/srv/media/videos"
  check ".env: what the script wrote, recorded" has_line "$ROOT/var/lib/domovoi-provision/env-written" "OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M"
  check ".env: the record holds no secret" eq "$(grep -c -e PASSWORD -e TOKEN -e SECRET "$ROOT/var/lib/domovoi-provision/env-written")" 0
  # ollama, models, whisper, piper
  check "Ollama tarball fetched" called "curl https://github.com/ollama/ollama/releases/download/v0.34.4/ollama-linux-amd64.tar.zst"
  check "Ollama binary" test -x "$ROOT/usr/local/bin/ollama"
  check "Ollama stamp" test -f "$ROOT/usr/local/lib/ollama/.domovoi-provision"
  check "tarball removed" eq "$(ls "$ROOT/var/cache/domovoi-provision" 2>/dev/null | grep -c .)" 0
  check "ollama.service is the doc's" has_line "$ROOT/etc/systemd/system/ollama.service" "ExecStart=/usr/local/bin/ollama serve"
  check "pulled the QA model" called '"model":"llama3.2:3b-instruct-q4_K_M"'
  check "pulled the router" called '"model":"qwen3:8b-q4_K_M"'
  check "pulled the vision model" called '"model":"qwen3-vl:2b-instruct-q4_K_M"'
  check "its digest checked" said "qwen3-vl:2b-instruct-q4_K_M pulled, digest matches the pin"
  check "download progress lines" grep -q '"step":"models".*"state":"progress".*"bytes":' "$CASE/progress.jsonl"
  check "Ollama download bytes" grep -q '"step":"ollama".*"bytes":' "$CASE/progress.jsonl"
  check "Whisper at the pinned revision" test -f "$ROOT/home/domovoi/.cache/huggingface/hub/models--Systran--faster-whisper-small.en/snapshots/d1d751a5f8271d482d14ca55d9e2deeebbae577f/model.bin"
  check "Piper voice" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-norman-medium.onnx"
  check "and the other two" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-ljspeech-medium.onnx"
  check "no espeak-ng installed" not_called "espeak-ng"
  # images, search helper
  check "database images pulled as compose pins them" called "docker compose pull postgres flyway"
  check "MPD image built as the core does" called "docker build --progress=plain -t domovoi-mpd:latest -f $CHECKOUT/domovoi/Dockerfile.mpd $CHECKOUT/domovoi"
  check "search helper up (always)" called "docker compose up -d --no-deps searxng"
  # Ubuntu's own jobs: WSL turns wsl-pro and snapd off whatever the answer;
  # under always the updaters run as shipped.
  for u in wsl-pro.service "${SNAPD_UNITS[@]}"; do check "$u masked (WSL)" masked "$u"; done
  check "wsl-pro and snapd disabled and stopped first" called "systemctl disable --now wsl-pro.service"
  for u in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service motd-news.timer ua-timer.timer apport-autoreport.path apport-autoreport.timer; do
    check "$u runs as shipped (always)" not_masked "$u"
  done
  check "nothing written down for never" absent "$ROOT/var/lib/domovoi-os-jobs/masked"
  # units
  local u
  for u in domovoi-db.service domovoi-core.service domovoi-web.service; do
    check "$u installed" test -f "$ROOT/etc/systemd/system/$u"
  done
  check "core unit runs this checkout" has_line "$ROOT/etc/systemd/system/domovoi-core.service" "WorkingDirectory=$CHECKOUT"
  check "core unit keeps its user" has_line "$ROOT/etc/systemd/system/domovoi-core.service" "User=domovoi"
  check "db unit in the checkout's domovoi/" has_line "$ROOT/etc/systemd/system/domovoi-db.service" "WorkingDirectory=$CHECKOUT/domovoi"
  check "units otherwise as shipped" eq "$(sed "s#$CHECKOUT#/opt/domovoi#g" "$ROOT/etc/systemd/system/domovoi-web.service")" \
    "$(cat "$REAL_REPO/scripts/linux/units/domovoi-web.service")"
  check "restart grant" has_line "$ROOT/etc/sudoers.d/domovoi-restart" \
    "domovoi ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-core.service domovoi-web.service"
  check "grant checked by visudo first" called "visudo -cf"
  # zone (WSL: Windows'), start, updater
  check "timezone step ran" eq "$(state_of timezone)" done
  check "no zone set on WSL" not_called "timedatectl"
  check "/etc/timezone as WSL wrote it" eq "$(cat "$ROOT/etc/timezone")" Etc/UTC
  check "units started" called "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service"
  check "nothing restarted on a first run" not_called "systemctl restart domovoi"
  check "update unit installed" called "install-update-unit --repo $CHECKOUT --user domovoi"
  check "updates re-sync from the lock" has_line "$ROOT/etc/default/domovoi-update" "DOMOVOI_USE_LOCK=1"
  check "set before the installer's pre-flight read it" has_line "$STATE/update-defaults-seen" "DOMOVOI_USE_LOCK=1"
  check "said so" said "updates re-sync from the lock: DOMOVOI_USE_LOCK=1 in /etc/default/domovoi-update"
  check "build tools from the lock's stanzas, with their deps" eq "$(sed -n 's/==.*//p' "$STATE/pip-build-tools" | sort | tr '\n' ' ')" "packaging setuptools wheel "
  check "build tools hash-checked" grep -q -- '--hash=sha256:' "$STATE/pip-build-tools"
  check "nothing but the build tools in that file" eq "$(grep -c '==' "$STATE/pip-build-tools")" 3
  check "the temp requirements file removed" eq "$(ls "$ROOT/var/cache/domovoi-provision" | grep -c build-tools)" 0
  check "result.json kept" grep -q '"result":"ok"' "$ROOT/var/lib/domovoi-provision/result.json"
  check "says where the setup code is" said "the setup code is in /home/domovoi/.domovoi/setup-code.txt"
  check "never prints it" not_said SENTINEL-SETUP-CODE
  end_case
}

case_second_run_skips_everything() {
  new_case second_run_skips_everything
  run_prov "${wsl_args[@]}" --media 'music=C:\M'
  check "first run exit 0" eq "$RC" 0
  local sig; sig=$(tree_sig)
  local env_before; env_before=$(cksum <"$CHECKOUT/domovoi/.env")
  run_prov "${wsl_args[@]}" --media 'music=C:\M'
  check "second run exit 0" eq "$RC" 0
  check "progress format" progress_ok
  local s
  for s in "${STEPS[@]}"; do
    [ "$s" = preflight ] && continue
    check "step $s skipped" eq "$(state_of "$s")" skip
  done
  check "only preflight and finish start" eq "$(grep -c '"state":"start"' "$CASE/progress.jsonl")" 2
  check "every file as it was" eq "$(tree_sig)" "$sig"
  check ".env untouched" eq "$(cksum <"$CHECKOUT/domovoi/.env")" "$env_before"
  check "no apt" not_called "apt-get"
  check "no pip" not_called "-m pip"
  check "no download" not_called "curl https://"
  check "no pull" not_called "/api/pull"
  check "no build" not_called "docker build"
  check "nothing started" not_called "systemctl start"
  check "nothing restarted" not_called "systemctl restart"
  check "no daemon-reload" not_called "daemon-reload"
  check "no mount" not_called "mount /srv"
  check "still needs the distro restart" eq "$(final_field restart_required)" true
  end_case
}

case_distro_restart_clears_restart_required() {
  new_case distro_restart_clears_restart_required
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  check "restart required" eq "$(final_field restart_required)" true
  # wsl --terminate: a new PID 1.
  echo "1 (systemd) S 0 1 1 0 -1 4194560 1 1 0 0 1 1 0 0 20 0 1 0 2222 1 1" >"$ROOT/proc/1/stat"
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "no restart required now" eq "$(final_field restart_required)" false
  check "pending note gone" absent "$ROOT/var/lib/domovoi-provision/wsl-conf-pending"
  end_case
}

case_resume_after_failure() {
  new_case resume_after_failure
  touch "$STATE/pip-fail"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "progress format" progress_ok
  check "venv failed" eq "$(state_of venv)" fail
  check "final names the step" eq "$(final_field failed_step)" '"venv"'
  check "final result failed" eq "$(final_field result)" '"failed"'
  check "says why" said "pip install -r requirements-linux-py314.lock failed"
  local s
  for s in apt user wsl-config media checkout docker; do check "marker for $s" marker "$s"; done
  for s in venv env ollama start; do check "no marker for $s" no_marker "$s"; done
  check "nothing started" not_called "systemctl start domovoi"
  rm -f "$STATE/pip-fail"
  run_prov "${wsl_args[@]}"
  check "the re-run exit 0" eq "$RC" 0
  for s in apt user wsl-config media checkout docker; do check "$s skipped on resume" eq "$(state_of "$s")" skip; done
  for s in venv env ollama models start; do check "$s ran on resume" eq "$(state_of "$s")" done; done
  check "no second clone" not_called "git clone"
  check "no apt on resume" not_called "apt-get"
  end_case
}

case_changed_input_reruns_its_step() {
  new_case changed_input_reruns_its_step
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  run_prov "${wsl_args[@]}" --tier floor
  check "exit 0" eq "$RC" 0
  check "env re-ran" eq "$(state_of env)" done
  check "models re-ran" eq "$(state_of models)" done
  check "start re-ran" eq "$(state_of start)" done
  local s
  for s in apt user checkout docker venv ollama whisper piper images units update-unit; do
    check "$s skipped" eq "$(state_of "$s")" skip
  done
  check "said why" said "[env] inputs changed since it last ran"
  check ".env has the floor router" has_line "$CHECKOUT/domovoi/.env" "OLLAMA_TOOL_MODEL=qwen3:4b-instruct-2507-q4_K_M"
  check "pulled the floor router" called '"model":"qwen3:4b-instruct-2507-q4_K_M"'
  check "the QA model was not pulled again" not_called '"model":"llama3.2:3b-instruct-q4_K_M"'
  check "core and web restarted" called "systemctl restart domovoi-core.service domovoi-web.service"
  check "db left alone" not_called "systemctl restart domovoi-db"
  end_case
}

case_ollama_sha_mismatch_stops() {
  new_case ollama_sha_mismatch_stops
  touch "$STATE/ollama-tampered"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "progress format" progress_ok
  check "ollama failed" eq "$(state_of ollama)" fail
  check "says mismatch" said "Ollama 0.34.4: SHA-256 mismatch"
  check "no binary" absent "$ROOT/usr/local/bin/ollama"
  check "no partial download left" eq "$(ls -A "$ROOT/var/cache/domovoi-provision" 2>/dev/null | grep -c .)" 0
  check "no marker" no_marker ollama
  check "nothing unpacked" not_called "tar "
  end_case
}

case_whisper_sha_mismatch_stops() {
  new_case whisper_sha_mismatch_stops
  printf 'tampered' >"$STATE/whisper-content"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "whisper failed" eq "$(state_of whisper)" fail
  check "says mismatch" said "Whisper model.bin SHA-256 mismatch"
  check "snapshot deleted" absent "$ROOT/home/domovoi/.cache/huggingface/hub/models--Systran--faster-whisper-small.en/snapshots/d1d751a5f8271d482d14ca55d9e2deeebbae577f"
  check "no marker" no_marker whisper
  end_case
}

# The configured voice is the path of an uploaded model, with a space in it:
# one value per line keeps it whole, it is left to the core (no pin), and
# the three shipped voices are still fetched and checked.
case_piper_unpinned_voice_with_a_space() {
  new_case piper_unpinned_voice_with_a_space
  printf '%s\n' "/srv/my voices/mine.onnx" >"$STATE/piper-configured"
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "piper done" eq "$(state_of piper)" done
  check "the whole path named" said "the Piper voice '/srv/my voices/mine.onnx' has no pin"
  check "Norman fetched" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-norman-medium.onnx"
  check "Linda fetched" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-ljspeech-medium.onnx"
  check "nothing fetched for the path" not_called "/srv/my voices/mine.onnx /home"
  end_case
}

# An empty configured voice is no voice at all: skipped, not a voice named
# "-" with pin "0", and the three shipped voices are fetched.
case_piper_empty_configured_voice() {
  new_case piper_empty_configured_voice
  printf '\n' >"$STATE/piper-configured"
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "piper done" eq "$(state_of piper)" done
  check "no warning about a nameless voice" not_said "the Piper voice '' has no pin"
  check "no voice named -" not_said "the Piper voice '-'"
  check "Kristin fetched" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-kristin-medium.onnx"
  end_case
}

case_piper_sha_mismatch_stops() {
  new_case piper_sha_mismatch_stops
  printf 'tampered' >"$STATE/piper-content"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "piper failed" eq "$(state_of piper)" fail
  check "says it does not match" said "does not match its pin"
  end_case
}

case_model_digest_mismatch_stops() {
  new_case model_digest_mismatch_stops
  touch "$STATE/pull-wrong-digest"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "models failed" eq "$(state_of models)" fail
  check "says the digest differs" said "the manifest pins a80c4f17"
  check "no marker" no_marker models
  end_case
}

case_docker_key_mismatch_stops() {
  new_case docker_key_mismatch_stops
  echo 0000000000000000000000000000000000000000 >"$STATE/docker-fpr"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "docker failed" eq "$(state_of docker)" fail
  check "says the fingerprint" said "the manifest pins 9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
  check "key not installed" absent "$ROOT/etc/apt/keyrings/docker.asc"
  check "no docker packages" not_called "docker-ce="
  end_case
}

case_manifest_missing_key_stops() {
  new_case manifest_missing_key_stops
  run_prov "${wsl_args[@]}" --ref bad-manifest
  check "exit 1" eq "$RC" 1
  check "names the key" said "docker_ce_cli is missing"
  check "nothing past the clone" not_called "gpg "
  end_case
}

case_lock_without_build_tools_stops() {
  new_case lock_without_build_tools_stops
  run_prov "${wsl_args[@]}" --ref no-wheel-lock --skip-models
  check "exit 1" eq "$RC" 1
  check "venv failed" eq "$(state_of venv)" fail
  check "says why" said "has no hash-pinned setuptools and wheel to build with"
  check "pip never ran an install" not_called "-m pip --disable-pip-version-check --no-input install"
  end_case
}

case_manifest_unknown_key_stops() {
  new_case manifest_unknown_key_stops
  run_prov "${wsl_args[@]}" --ref odd-manifest
  check "exit 1" eq "$RC" 1
  check "names it" said "unknown key surprise_key"
  end_case
}

case_media_list_change_rewrites_the_block() {
  new_case media_list_change_rewrites_the_block
  run_prov "${wsl_args[@]}" --media 'music=C:\M' --media 'pictures=C:\P'
  check "first run exit 0" eq "$RC" 0
  run_prov "${wsl_args[@]}" --media 'pictures=C:\P'
  check "exit 0" eq "$RC" 0
  check "media re-ran" eq "$(state_of media)" done
  check "music line gone" eq "$(grep -c '/srv/media/music' "$ROOT/etc/fstab")" 0
  check "pictures line kept" has_line "$ROOT/etc/fstab" 'C:/P /srv/media/pictures drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0'
  check "music unmounted" called "umount /srv/media/music"
  check "still one block" eq "$(grep -c '^# END Domovoi media folders' "$ROOT/etc/fstab")" 1
  check "PICTURES_DIR" has_line "$CHECKOUT/domovoi/.env" "PICTURES_DIR=/srv/media/pictures"
  check "an unchanged folder is not remounted" not_called "umount /srv/media/pictures"
  # The same name, another Windows folder: mounted again from the new one.
  run_prov "${wsl_args[@]}" --media 'pictures=D:\Photos'
  check "exit 0" eq "$RC" 0
  check "the new source in fstab" has_line "$ROOT/etc/fstab" 'D:/Photos /srv/media/pictures drvfs metadata,uid=1000,gid=1000,umask=022,fmask=133,noatime,nofail 0 0'
  check "unmounted the old source" called "umount /srv/media/pictures"
  check "mounted the new one" called "mount /srv/media/pictures"
  check "said so" said "remounting /srv/media/pictures"
  end_case
}

case_image_pull_failure_stops() {
  new_case image_pull_failure_stops
  touch "$STATE/pull-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "images failed" eq "$(state_of images)" fail
  check "says which" said "docker compose pull postgres flyway failed"
  check "no marker" no_marker images
  check "nothing started" not_called "systemctl start domovoi"
  end_case
}

case_media_mount_failure_stops() {
  new_case media_mount_failure_stops
  touch "$STATE/mount-fail"
  run_prov "${wsl_args[@]}" --media 'music=C:\Missing'
  check "exit 1" eq "$RC" 1
  check "media failed" eq "$(state_of media)" fail
  check "says which folder" said 'could not mount C:\Missing at /srv/media/music'
  end_case
}

case_bare_metal() {
  new_case bare_metal --bare
  run_prov --internet sometimes --media music=/srv/music --media documents=/data/docs
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "wsl-config skipped" eq "$(state_of wsl-config)" skip
  check "wsl.conf untouched" eq "$(cat "$ROOT/etc/wsl.conf")" "$(printf '[boot]\nsystemd=true')"
  check "fstab untouched" eq "$(cat "$ROOT/etc/fstab")" "# UNCONFIGURED FSTAB FOR BASE SYSTEM"
  check "no mount" not_called "mount "
  check "media dir made" test -d "$ROOT/srv/music"
  check "MUSIC_DIR as given" has_line "$CHECKOUT/domovoi/.env" "MUSIC_DIR=/srv/music"
  check "DOCUMENTS_DIR as given" has_line "$CHECKOUT/domovoi/.env" "DOCUMENTS_DIR=/data/docs"
  check "no adoption key" eq "$(grep -c SATELLITE_ADOPTION_ENABLED "$CHECKOUT/domovoi/.env")" 0
  check "drop-in for the path outside the sandbox" has_line "$ROOT/etc/systemd/system/domovoi-core.service.d/domovoi-media.conf" "ReadWritePaths=-/data/docs"
  check "same for the web" test -f "$ROOT/etc/systemd/system/domovoi-web.service.d/domovoi-media.conf"
  check "search helper up (sometimes)" called "docker compose up -d --no-deps searxng"
  check "no restart required" eq "$(final_field restart_required)" false
  check "no time zone step" eq "$(state_of timezone)" skip
  end_case
}

case_internet_never_keeps_searxng_off() {
  new_case internet_never_keeps_searxng_off
  run_prov --internet never --skip-models
  check "exit 0" eq "$RC" 0
  check ".env says never" has_line "$CHECKOUT/domovoi/.env" "INTERNET_ACCESS=never"
  check "search helper not started" not_called "docker compose up"
  check "models skipped" eq "$(state_of models)" skip
  check "whisper skipped" eq "$(state_of whisper)" skip
  check "no pull" not_called "/api/pull"
  check "the voice is still fetched ahead" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-norman-medium.onnx"
  # No internet quiets Ubuntu's own jobs (owner decision 2026-10-11).
  local u
  for u in "${NEVER_UNITS[@]}"; do check "$u masked (never)" masked "$u"; done
  for u in wsl-pro.service "${SNAPD_UNITS[@]}"; do check "$u masked (WSL)" masked "$u"; done
  check "the running timers stopped" called "systemctl stop apt-daily.timer"
  check "a service is left to finish" not_called "systemctl stop unattended-upgrades.service"
  check "what never masked is written down" has_line "$ROOT/var/lib/domovoi-os-jobs/masked" "apt-daily.timer"
  check "snapd's timer is WSL's, not never's" eq "$(grep -c snapd "$ROOT/var/lib/domovoi-os-jobs/masked")" 0
  check "os-jobs done" eq "$(state_of os-jobs)" done
  # The household answers Yes in Settings later (the .env writer), then the
  # box re-applies its configuration: here a provision run (the update
  # unit's runs are test-apply-update.sh's).
  touch "$STATE/enabled/apt-daily.timer"
  sed -i 's/^INTERNET_ACCESS=.*/INTERNET_ACCESS=always/' "$CHECKOUT/domovoi/.env"
  run_prov --internet never --skip-models
  check "re-run exit 0" eq "$RC" 0
  check "os-jobs re-ran: the answer changed" eq "$(state_of os-jobs)" done
  for u in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service motd-news.timer ua-timer.timer apport-autoreport.path apport-autoreport.timer; do
    check "$u unmasked (always)" not_masked "$u"
  done
  check "snapd stays off on WSL" masked snapd.snap-repair.timer
  check "wsl-pro stays off" masked wsl-pro.service
  check "an enabled timer starts again" called "systemctl start apt-daily.timer"
  check "a disabled one does not" not_called "systemctl start motd-news.timer"
  check "nothing left written down" absent "$ROOT/var/lib/domovoi-os-jobs/masked"
  check "the search helper follows too" called "docker compose up -d --no-deps searxng"
  run_prov --internet never --skip-models
  check "third run exit 0" eq "$RC" 0
  check "os-jobs skipped: as the answer wants" eq "$(state_of os-jobs)" skip
  end_case
}

# Bare metal: never masks the same jobs; wsl-pro and snapd are the
# administrator's. A unit file of their own where a mask would go is left
# alone, every run, without failing the step.
case_os_jobs_on_bare_metal() {
  new_case os_jobs_on_bare_metal --bare
  printf '[Unit]\nDescription=the admin'"'"'s own motd-news\n' >"$ROOT/etc/systemd/system/motd-news.timer"
  run_prov --internet never --skip-models
  check "exit 0" eq "$RC" 0
  check "os-jobs done" eq "$(state_of os-jobs)" done
  local u
  for u in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service ua-timer.timer apport-autoreport.path apport-autoreport.timer snapd.snap-repair.timer; do
    check "$u masked (never)" masked "$u"
  done
  check "the admin's own motd-news.timer left as it is" grep -q "the admin's own motd-news" "$ROOT/etc/systemd/system/motd-news.timer"
  check "and said so" said "left alone, a unit file of their own in /etc/systemd/system: motd-news.timer"
  for u in wsl-pro.service snapd.service snapd.socket snapd.seeded.service; do check "$u left alone (bare metal)" not_masked "$u"; done
  check "nothing disabled" not_called "systemctl disable"
  run_prov --internet never --skip-models
  check "second run exit 0" eq "$RC" 0
  check "second run: os-jobs skipped" eq "$(state_of os-jobs)" skip
  end_case
}

# Owner decision 2026-10-11: on WSL the zone follows Windows. --timezone
# is ignored with a warning; the step only makes /etc/timezone agree with
# /etc/localtime, restarting core and web when it has to.
case_wsl_zone_follows_windows() {
  new_case wsl_zone_follows_windows
  run_prov --internet always --skip-models --timezone America/New_York
  check "exit 0" eq "$RC" 0
  check "warned that --timezone is ignored" said "--timezone America/New_York is ignored on WSL"
  check "counted" eq "$(final_field warnings)" 2
  check "no zone set" not_called "timedatectl"
  check "/etc/localtime as WSL left it" eq "$(cat "$ROOT/etc/localtime.harness-link")" /usr/share/zoneinfo/Etc/UTC
  check "/etc/timezone as WSL left it" eq "$(cat "$ROOT/etc/timezone")" Etc/UTC
  check "timezone step done" eq "$(state_of timezone)" done
  # Windows moves to New York and the distro restarts: WSL rewrites both
  # files. A re-run changes and restarts nothing for that.
  echo /usr/share/zoneinfo/America/New_York >"$ROOT/etc/localtime.harness-link"
  echo America/New_York >"$ROOT/etc/timezone"
  echo "1 (systemd) S 0 1 1 0 -1 4194560 1 1 0 0 1 1 0 0 20 0 1 0 2222 1 1" >"$ROOT/proc/1/stat"
  run_prov --internet always --skip-models
  check "after Windows' change: exit 0" eq "$RC" 0
  check "after Windows' change: timezone skipped" eq "$(state_of timezone)" skip
  check "after Windows' change: start skipped" eq "$(state_of start)" skip
  check "after Windows' change: nothing restarted" not_called "systemctl restart"
  # Someone runs timedatectl inside the distro: /etc/localtime moves,
  # /etc/timezone (which the core reads first) stays behind.
  echo /usr/share/zoneinfo/Etc/UTC >"$ROOT/etc/localtime.harness-link"
  run_prov --internet always --skip-models
  check "mismatch: exit 0" eq "$RC" 0
  check "mismatch: timezone re-ran" eq "$(state_of timezone)" done
  check "mismatch: /etc/timezone follows /etc/localtime" eq "$(cat "$ROOT/etc/timezone")" Etc/UTC
  check "mismatch: said so" said "/etc/timezone said America/New_York and /etc/localtime Etc/UTC"
  check "mismatch: core and web restarted" called "systemctl restart domovoi-core.service domovoi-web.service"
  check "mismatch: the database left alone" not_called "systemctl restart domovoi-db"
  check "mismatch: still no zone set" not_called "timedatectl"
  end_case
}

# Bare metal keeps --timezone as built.
case_bare_metal_sets_the_zone() {
  new_case bare_metal_sets_the_zone --bare
  run_prov --internet sometimes --skip-models --timezone America/New_York
  check "exit 0" eq "$RC" 0
  check "no warning about the zone" not_said "is ignored on WSL"
  check "time zone set" called "timedatectl set-timezone America/New_York"
  check "/etc/timezone follows (the core reads it first)" eq "$(cat "$ROOT/etc/timezone")" America/New_York
  run_prov --internet sometimes --skip-models --timezone America/New_York
  check "second run: timezone skipped" eq "$(state_of timezone)" skip
  run_prov --internet sometimes --skip-models --timezone Etc/UTC
  check "another zone: exit 0" eq "$RC" 0
  check "another zone: set" called "timedatectl set-timezone Etc/UTC"
  check "another zone: core and web restarted" called "systemctl restart domovoi-core.service domovoi-web.service"
  end_case
}

# A box with a CPU only: the final line's gpu list is empty. Before that,
# with no Ollama start in this boot's journal, it is unknown (null), not
# "no GPU" (review 2026-10-11, finding 8).
case_cpu_only_box_reports_no_gpu() {
  new_case cpu_only_box_reports_no_gpu
  run_prov "${wsl_args[@]}" --skip-models
  check "no journal: exit 0" eq "$RC" 0
  check "no journal: progress format" progress_ok
  check "no journal: gpu unknown (null), not an empty list" grep -qF '"gpu":null' "$CASE/progress.jsonl"
  check "no journal: said unknown" said "what Ollama runs on is unknown"
  check "no journal: not said to be the CPU" not_said "Ollama found no GPU"
  printf '%s\n' \
    'time=2026-10-11T10:00:00.000-04:00 level=INFO source=routes.go:2005 msg="server config" env="map[]"' \
    'time=2026-10-11T10:00:01.000-04:00 level=INFO source=types.go:32 msg="inference compute" id=cpu library=cpu compute="" name=cpu description=cpu total="15.5 GiB"' \
    >"$STATE/journal"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "an empty gpu list" grep -qF '"gpu":[]' "$CASE/progress.jsonl"
  check "said so" said "Ollama found no GPU: it runs on the CPU"
  # A re-run that skips the ollama step still reports it.
  run_prov "${wsl_args[@]}" --skip-models
  check "re-run: ollama skipped" eq "$(state_of ollama)" skip
  check "re-run: still an empty list" grep -qF '"gpu":[]' "$CASE/progress.jsonl"
  end_case
}

# Decision 6: a provisioned box's updates re-sync from the lock; an
# administrator's own setting stays.
case_update_defaults_opt_into_the_lock() {
  new_case update_defaults_opt_into_the_lock --bare
  mkdir -p "$ROOT/etc/default"
  local f=$ROOT/etc/default/domovoi-update owner posix=0
  printf '# mine\nDOMOVOI_UPDATE_KEEP_BACKUPS=9\n' >"$f"
  chmod 0600 "$f"
  [ "$(stat -c %a "$f")" = 600 ] && posix=1   # not on Git for Windows' NTFS view
  owner=$(stat -c %u:%g "$f")
  run_prov --internet always --skip-models
  check "exit 0" eq "$RC" 0
  check "the admin's line kept" has_line "$f" "DOMOVOI_UPDATE_KEEP_BACKUPS=9"
  check "the lock line added" has_line "$f" "DOMOVOI_USE_LOCK=1"
  check "once" eq "$(grep -c '^DOMOVOI_USE_LOCK=' "$f")" 1
  # Review 2026-10-11, finding 9: the file keeps its owner and mode.
  check "the file's owner given to the new copy" called "chown $owner $ROOT/etc/default/.domovoi-update."
  check "not handed to root:root" not_called "chown root:root $ROOT/etc/default/.domovoi-update."
  if [ "$posix" = 1 ]; then check "its mode kept (0600)" eq "$(stat -c %a "$f")" 600; fi
  check "still a plain file" test ! -L "$f"
  run_prov --internet always --skip-models
  check "second run: update-unit skipped" eq "$(state_of update-unit)" skip
  check "still once" eq "$(grep -c '^DOMOVOI_USE_LOCK=' "$ROOT/etc/default/domovoi-update")" 1
  # The administrator turns it off; a repair run (the installer changed)
  # leaves that alone and says so.
  sed -i 's/^DOMOVOI_USE_LOCK=1$/DOMOVOI_USE_LOCK=0/' "$ROOT/etc/default/domovoi-update"
  echo "# changed" >>"$CHECKOUT/scripts/linux/install-update-unit.sh"
  run_prov --internet always --skip-models
  check "repair: exit 0" eq "$RC" 0
  check "repair: update-unit re-ran" eq "$(state_of update-unit)" done
  check "repair: the admin's 0 kept" has_line "$ROOT/etc/default/domovoi-update" "DOMOVOI_USE_LOCK=0"
  check "repair: no 1 added" eq "$(grep -c '^DOMOVOI_USE_LOCK=1' "$ROOT/etc/default/domovoi-update")" 0
  check "repair: warned" said "sets DOMOVOI_USE_LOCK=0, left as it is"
  # A symlink there (configuration management, say) is left alone, with a
  # warning; the file it points to is not written through it.
  mkdir -p "$CASE/managed"
  printf 'DOMOVOI_UPDATE_KEEP_BACKUPS=3\n' >"$CASE/managed/domovoi-update"
  # winsymlinks:sys: Git Bash's own symlink kind (a plain ln -s copies the
  # file there); ignored on Linux.
  rm -f "$f"; MSYS=winsymlinks:sys ln -s "$CASE/managed/domovoi-update" "$f" 2>/dev/null || true
  if [ ! -L "$f" ]; then
    echo "    (no symlinks on this file system: the symlink part is not run)"
    end_case
    return
  fi
  echo "# changed again" >>"$CHECKOUT/scripts/linux/install-update-unit.sh"
  run_prov --internet always --skip-models
  check "symlink: exit 0" eq "$RC" 0
  check "symlink: still a link" test -L "$f"
  check "symlink: the file it points to untouched" eq "$(cat "$CASE/managed/domovoi-update")" "DOMOVOI_UPDATE_KEEP_BACKUPS=3"
  check "symlink: warned" said "/etc/default/domovoi-update is a symlink, left as it is"
  run_prov --internet always --skip-models
  check "symlink: the next run skips update-unit" eq "$(state_of update-unit)" skip
  end_case
}

# Review 2026-10-11, finding 4: DOMOVOI_MANAGE_OS_JOBS=0 in
# /etc/default/domovoi-update leaves Ubuntu's jobs alone on a provision
# run too, as it does in the update unit (WSL's wsl-pro and snapd included).
# The journal kept on disk, so what the core logs about why it stopped
# (its loop watchdog's report) survives a power cycle (incident
# 2026-10-14): the units step installs the drop-in and restarts journald
# once; a second run does nothing; an edited drop-in is put back.
case_journal_kept_on_disk() {
  new_case journal_kept_on_disk
  local f=$ROOT/etc/systemd/journald.conf.d/50-domovoi.conf
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "the shipped drop-in" cmp -s "$f" "$REAL_REPO/scripts/linux/units/journald-domovoi.conf"
  check "Storage=persistent" has_line "$f" "Storage=persistent"
  check "a size cap" has_line "$f" "SystemMaxUse=1G"
  check "journald restarted to read it" called "systemctl restart systemd-journald.service"
  check "the journal so far moved to disk" called "journalctl --flush"
  check "said so" said "the journal is kept on disk"
  run_prov "${wsl_args[@]}" --skip-models
  check "second run exit 0" eq "$RC" 0
  check "second run: units skipped" eq "$(state_of units)" skip
  check "second run: journald left alone" not_called "systemd-journald"
  printf '[Journal]\nStorage=volatile\n' >"$f"
  run_prov "${wsl_args[@]}" --skip-models
  check "an edited drop-in: units ran again" eq "$(state_of units)" done
  check "and put it back" cmp -s "$f" "$REAL_REPO/scripts/linux/units/journald-domovoi.conf"
  check "and restarted journald" called "systemctl restart systemd-journald.service"
  end_case
}

case_journal_opt_out_is_honoured() {
  new_case journal_opt_out_is_honoured
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_MANAGE_JOURNAL=0\n' >"$ROOT/etc/default/domovoi-update"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "no drop-in" absent "$ROOT/etc/systemd/journald.conf.d/50-domovoi.conf"
  check "journald left alone" not_called "systemd-journald"
  check "says why" said "the journal left as it is (DOMOVOI_MANAGE_JOURNAL in /etc/default/domovoi-update)"
  end_case
}

case_os_jobs_opt_out_is_honoured() {
  new_case os_jobs_opt_out_is_honoured
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_MANAGE_OS_JOBS=0\n' >"$ROOT/etc/default/domovoi-update"
  run_prov --internet never --skip-models
  check "exit 0" eq "$RC" 0
  check "os-jobs skipped" eq "$(state_of os-jobs)" skip
  check "says why" said "DOMOVOI_MANAGE_OS_JOBS=0 in /etc/default/domovoi-update: Ubuntu's background jobs are left to the administrator"
  local u
  for u in apt-daily.timer motd-news.timer wsl-pro.service snapd.service; do check "$u left alone" not_masked "$u"; done
  check "nothing written down" absent "$ROOT/var/lib/domovoi-os-jobs/masked"
  check "the opt-out line kept" has_line "$ROOT/etc/default/domovoi-update" "DOMOVOI_MANAGE_OS_JOBS=0"
  # The administrator takes the opt-out back: the next run sets the jobs.
  sed -i '/^DOMOVOI_MANAGE_OS_JOBS=/d' "$ROOT/etc/default/domovoi-update"
  run_prov --internet never --skip-models
  check "opted in again: os-jobs done" eq "$(state_of os-jobs)" done
  check "opted in again: apt-daily masked" masked apt-daily.timer
  end_case
}

# Real WSL run 2026-10-11: masking timers that were running left
# apt-daily, apt-daily-upgrade and motd-news "failed" ("Unit to trigger
# vanished") and the box "degraded" until the next start, after the first
# provision and after each switch to never. os-jobs.sh stops a running
# timer or path unit after its mask and before systemd reloads, and clears
# the failed mark of every unit it changed (reset-failed), never by
# stopping the shutdown helper. (A helper that stops after the reload, or
# not at all, or never resets, fails this case.)
case_os_jobs_never_leaves_the_box_degraded() {
  new_case os_jobs_never_leaves_the_box_degraded
  mkdir -p "$STATE/active" "$STATE/failed"
  local u running=(apt-daily.timer apt-daily-upgrade.timer motd-news.timer apport-autoreport.path)
  for u in "${running[@]}" unattended-upgrades.service snapd.snap-repair.timer snapd.socket; do touch "$STATE/active/$u"; done
  # Failed before this run (a reload somebody else ran, say): masked now,
  # it would stay listed failed until the next boot.
  touch "$STATE/failed/ua-timer.timer"
  run_prov --internet never --skip-models
  check "never: exit 0" eq "$RC" 0
  check "never: os-jobs done" eq "$(state_of os-jobs)" done
  for u in "${running[@]}"; do
    check "never: $u masked" masked "$u"
    check "never: $u stopped" test ! -e "$STATE/active/$u"
  done
  check "never: no unit failed at a reload" eq "$(cat "$STATE/journal" 2>/dev/null)" ""
  check "never: nothing left failed (not degraded)" eq "$(ls -A "$STATE/failed")" ""
  for u in "${NEVER_UNITS[@]}"; do
    [ "$u" = snapd.snap-repair.timer ] || check "never: $u's failed mark cleared" called "systemctl reset-failed $u"
  done
  check "never: WSL's units' too" called "systemctl reset-failed snapd.snap-repair.timer"
  check "never: the shutdown helper left running" test -e "$STATE/active/unattended-upgrades.service"
  check "never: and never stopped" not_called "systemctl stop unattended-upgrades.service"
  # The household answers Yes in Settings later (the .env writer), then the
  # box re-applies its configuration. An older helper left two of the
  # timers failed: an enabled one, which starts again, and a disabled one.
  touch "$STATE/enabled/apt-daily.timer" "$STATE/failed/apt-daily.timer" "$STATE/failed/motd-news.timer"
  sed -i 's/^INTERNET_ACCESS=.*/INTERNET_ACCESS=always/' "$CHECKOUT/domovoi/.env"
  run_prov --internet never --skip-models
  check "always: exit 0" eq "$RC" 0
  check "always: os-jobs done" eq "$(state_of os-jobs)" done
  check "always: nothing left failed" eq "$(ls -A "$STATE/failed")" ""
  check "always: cleared before it starts" before "systemctl reset-failed apt-daily.timer" "systemctl start apt-daily.timer"
  check "always: the disabled one's cleared too" called "systemctl reset-failed motd-news.timer"
  check "always: the enabled timer runs again" test -e "$STATE/active/apt-daily.timer"
  run_prov --internet never --skip-models
  check "again: os-jobs skipped" eq "$(state_of os-jobs)" skip
  check "again: nothing reset" not_called "systemctl reset-failed"
  end_case
}

case_wsl_without_systemd_stops_for_a_restart() {
  new_case wsl_without_systemd_stops_for_a_restart
  echo init >"$ROOT/proc/1/comm"
  run_prov "${wsl_args[@]}"
  check "exit 3" eq "$RC" 3
  check "progress format" progress_ok
  check "result rerun_after_restart" eq "$(final_field result)" '"rerun_after_restart"'
  check "restart required" eq "$(final_field restart_required)" true
  check "wsl.conf written" has_line "$ROOT/etc/wsl.conf" "default=domovoi"
  check "user created" called "useradd"
  check "nothing else" not_called "apt-get"
  check "masked without systemctl" eq "$("$SHIM_REAL_READLINK" "$ROOT/etc/systemd/system/tmp.mount")" /dev/null
  end_case
}

# Review finding 10: a run refused by the lock has its own exit code (4),
# says why in its final line, and leaves result.json to the running run.
case_lock_busy_has_its_own_exit_code() {
  new_case lock_busy_has_its_own_exit_code
  run_prov "${wsl_args[@]}" --skip-models
  check "first run exit 0" eq "$RC" 0
  local res=$ROOT/var/lib/domovoi-provision/result.json before
  before=$(cat "$res")
  touch "$STATE/locked"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 4" eq "$RC" 4
  check "progress format, one final line" progress_ok
  check "result busy" eq "$(final_field result)" '"busy"'
  check "exit_code 4 in the final line" eq "$(final_field exit_code)" 4
  check "the final line says why" grep -q '"message":"another provision run is in progress' "$CASE/progress.jsonl"
  check "result.json left to the run that holds the lock" eq "$(cat "$res")" "$before"
  check "nothing ran" not_called "apt-get"
  end_case
}

# Review finding 10: nothing the run starts inherits the run lock (an
# orphaned pip held it after a SIGKILL).
case_children_do_not_hold_the_lock() {
  new_case children_do_not_hold_the_lock
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "the lock is held by flock -o, not by the script's own descriptor" called "flock -n -E 75 -o $ROOT/var/lib/domovoi-provision/lock"
  check "apt-get noted its descriptors" test -s "$STATE/apt-fds"
  check "apt-get did not inherit the lock" eq "$(grep -c 'domovoi-provision/lock' "$STATE/apt-fds")" 0
  end_case
}

case_existing_checkout_is_not_moved() {
  new_case existing_checkout_is_not_moved
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local head; head=$(git -C "$CHECKOUT" rev-parse HEAD)
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "checkout step re-ran" eq "$(state_of checkout)" done
  check "HEAD not moved" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$head"
  check "says so" said "left where it is"
  end_case
}

case_ref_sha_follows_the_track_branch() {
  new_case ref_sha_follows_the_track_branch
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "HEAD is the SHA" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "on main" eq "$(git -C "$CHECKOUT" rev-parse --abbrev-ref HEAD)" main
  check "tracking origin/main" eq "$(git -C "$CHECKOUT" rev-parse --abbrev-ref '@{u}')" origin/main
  end_case
}

case_ref_off_the_track_branch_stops() {
  new_case ref_off_the_track_branch_stops
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_ELSEWHERE"
  check "exit 1" eq "$RC" 1
  check "says why" said "is not on origin/main's history"
  end_case
}

case_clone_from_a_bundle() {
  new_case clone_from_a_bundle
  RC=0
  : >"$STATE/calls.log"
  env PATH="$CASE/bin:$PATH" DOMOVOI_PROVISION_ROOT="$ROOT" DOMOVOI_PROVISION_TEST_HARNESS=1 TMPDIR="$CASE/tmp" \
    bash "$SCRIPT" --repo-url "$WORK/origin.bundle" --checkout-dir "$CHECKOUT" --ref main "${wsl_args[@]}" --skip-models \
    >"$CASE/output.log" 2>&1 || RC=$?
  grep '^@@DOMOVOI ' "$CASE/output.log" | sed 's/^@@DOMOVOI //' >"$CASE/progress.jsonl" || true
  check "exit 0" eq "$RC" 0
  check "HEAD is main's" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_B"
  check "origin is the bundle" eq "$(origin_url "$CHECKOUT")" "$WORK/origin.bundle"
  end_case
}

case_update_unit_failure_stops() {
  new_case update_unit_failure_stops
  touch "$STATE/update-unit-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "update-unit failed" eq "$(state_of update-unit)" fail
  check "points at its output" said "install-update-unit.sh stopped"
  end_case
}

case_core_not_healthy_stops() {
  new_case core_not_healthy_stops
  touch "$STATE/core-unhealthy"
  run_prov "${wsl_args[@]}" --skip-models --health-timeout 10
  check "exit 1" eq "$RC" 1
  check "start failed" eq "$(state_of start)" fail
  check "says which" said "the core (/v1/health) did not answer within 10s"
  check "no marker" no_marker start
  end_case
}

# ─── cases for the review findings of 2026-10-09 (REVIEW.md) ─────────────

# Finding 1: a re-run never undoes the household's own settings.
case_env_keeps_household_settings() {
  new_case env_keeps_household_settings
  local envf=$CHECKOUT/domovoi/.env
  # A run that stops between creating .env and writing its keys: the next
  # one still treats the template's values as the script's own.
  touch "$STATE/env-write-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "the interrupted run exit 1" eq "$RC" 1
  check "env failed" eq "$(state_of env)" fail
  rm -f "$STATE/env-write-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "resumed run exit 0" eq "$RC" 0
  check "the tier's Whisper over the template's" has_line "$envf" "WHISPER_MODEL=small.en"
  check "the tier's router written" has_line "$envf" "OLLAMA_TOOL_MODEL=qwen3:8b-q4_K_M"
  check "no --lan-ip: the template's MPD_HTTP_BASE stays" has_line "$envf" "MPD_HTTP_BASE=auto"
  # The household picks other models in Settings (the writer rewrites the
  # lines in place).
  sed -i 's/^OLLAMA_MODEL=.*/OLLAMA_MODEL=mistral:7b/; s/^WHISPER_MODEL=.*/WHISPER_MODEL=medium.en/' "$envf"
  # A repair run with a new LAN address.
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.60
  check "exit 0" eq "$RC" 0
  check "env re-ran" eq "$(state_of env)" done
  check "the household's QA model kept" has_line "$envf" "OLLAMA_MODEL=mistral:7b"
  check "the household's Whisper kept" has_line "$envf" "WHISPER_MODEL=medium.en"
  check "MPD_HTTP_BASE written (the template's value was the script's)" has_line "$envf" "MPD_HTTP_BASE=http://192.168.1.60"
  check "says it kept OLLAMA_MODEL" said "OLLAMA_MODEL holds a value this script did not write"
  check "says it kept WHISPER_MODEL" said "WHISPER_MODEL holds a value this script did not write"
  check "both in the final line's warnings" eq "$(final_field warnings)" 2
  check "core and web restarted: MPD_HTTP_BASE changed" called "systemctl restart domovoi-core.service domovoi-web.service"
  # Another tier: the router is still the script's, so it moves; the QA
  # model stays the household's.
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.60 --tier floor
  check "tier change exit 0" eq "$RC" 0
  check "the floor router written" has_line "$envf" "OLLAMA_TOOL_MODEL=qwen3:4b-instruct-2507-q4_K_M"
  check "the household's QA model still kept" has_line "$envf" "OLLAMA_MODEL=mistral:7b"
  # The household sets MPD_HTTP_BASE too: a new --lan-ip then changes
  # nothing in .env, and nothing restarts.
  sed -i 's#^MPD_HTTP_BASE=.*#MPD_HTTP_BASE=http://10.0.0.9#' "$envf"
  local env_before; env_before=$(cksum <"$envf")
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.61 --tier floor
  check "exit 0 again" eq "$RC" 0
  check "env re-ran (its inputs changed)" eq "$(state_of env)" done
  check "the household's MPD_HTTP_BASE kept" has_line "$envf" "MPD_HTTP_BASE=http://10.0.0.9"
  check ".env untouched" eq "$(cksum <"$envf")" "$env_before"
  check "start skipped: .env did not change" eq "$(state_of start)" skip
  check "nothing restarted" not_called "systemctl restart"
  end_case
}

# Finding 2: an existing account's password is never locked; an
# administrator's account is refused.
case_existing_user_is_not_locked() {
  new_case existing_user_is_not_locked --bare
  echo kamron >>"$STATE/users"; echo "kamron sudo" >>"$STATE/groups"; mkdir -p "$ROOT/home/kamron"
  run_prov --internet always --user kamron --skip-models
  check "an administrator's account: exit 2" eq "$RC" 2
  check "says why" said "is an administrator's account (in the sudo group)"
  check "one final line, usage_error" eq "$(final_field result)" '"usage_error"'
  check "its password untouched" not_called "passwd"
  check "nothing ran" not_called "apt-get"
  echo svc >>"$STATE/users"; mkdir -p "$ROOT/home/svc"
  run_prov --internet always --user svc --skip-models
  check "a plain existing account: exit 0" eq "$RC" 0
  check "used as it is" said "the service user svc exists; its password and groups are left as they are"
  check "its password not locked" not_called "passwd"
  check "not created again" not_called "useradd --create-home --home-dir /home/svc"
  end_case
}

# Finding 3: a key file holding Docker's key and another one is refused.
case_docker_key_file_with_a_second_key_stops() {
  new_case docker_key_file_with_a_second_key_stops
  echo 0123456789ABCDEF0123456789ABCDEF01234567 >"$STATE/docker-extra-key"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "docker failed" eq "$(state_of docker)" fail
  check "says why" said "holds 2 primary keys"
  check "key not installed" absent "$ROOT/etc/apt/keyrings/docker.asc"
  check "no docker packages" not_called "docker-ce="
  end_case
}

# Finding 4: a failed or interrupted clone is repaired by the next run.
case_failed_checkout_is_repaired() {
  new_case failed_checkout_is_repaired
  run_prov "${wsl_args[@]}" --skip-models --ref no-such-ref
  check "a ref that isn't there: exit 1" eq "$RC" 1
  check "checkout failed" eq "$(state_of checkout)" fail
  check "nothing at the checkout dir" absent "$CHECKOUT"
  check "no clone left beside it" absent "$CASE/opt/.domovoi.provision-clone"
  # As a SIGKILL mid-clone leaves it.
  mkdir -p "$CASE/opt/.domovoi.provision-clone/.git"; echo junk >"$CASE/opt/.domovoi.provision-clone/.git/HEAD"
  run_prov "${wsl_args[@]}" --skip-models
  check "the run with the default ref: exit 0" eq "$RC" 0
  check "said it cleared the unfinished clone" said "removed an unfinished clone from an earlier run"
  check "HEAD is main's" eq "$(git -C "$CHECKOUT" rev-parse HEAD 2>/dev/null)" "$FIXTURE_B"
  check "a working tree" test -f "$CHECKOUT/scripts/linux/provision-manifest.conf"
  check "nothing left beside it" absent "$CASE/opt/.domovoi.provision-clone"
  end_case
}

# Finding 5: a folder that was there before and can't mount now (a USB
# drive unplugged) is a warning; a new one still has to mount.
case_media_missing_on_a_rerun_warns() {
  new_case media_missing_on_a_rerun_warns
  run_prov "${wsl_args[@]}" --skip-models --media 'music=C:\M' --media 'pictures=C:\P'
  check "first run exit 0" eq "$RC" 0
  grep -vx /srv/media/music "$STATE/mounts" >"$STATE/mounts.new"; mv "$STATE/mounts.new" "$STATE/mounts"
  touch "$STATE/mount-fail"
  run_prov "${wsl_args[@]}" --skip-models --media 'music=C:\M' --media 'pictures=C:\P'
  check "exit 0" eq "$RC" 0
  check "media ran again and is done" eq "$(state_of media)" done
  check "warned" said 'could not mount C:\M at /srv/media/music'
  check "in the final line's warnings" eq "$(final_field warnings)" 1
  check "result ok" eq "$(final_field result)" '"ok"'
  check "nothing restarted" not_called "systemctl restart"
  # WSL's own way: mount exits 0 and mounts nothing. Still a warning.
  rm -f "$STATE/mount-fail"; touch "$STATE/mount-silent-fail"
  run_prov "${wsl_args[@]}" --skip-models --media 'music=C:\M' --media 'pictures=C:\P'
  check "silent mount failure: exit 0" eq "$RC" 0
  check "silent mount failure: warned" said 'could not mount C:\M at /srv/media/music'
  check "silent mount failure: in the warnings" eq "$(final_field warnings)" 1
  run_prov "${wsl_args[@]}" --skip-models --media 'music=C:\M' --media 'pictures=C:\P' --media 'documents=C:\D'
  check "a new folder that can't mount: exit 1" eq "$RC" 1
  check "media failed" eq "$(state_of media)" fail
  check "says which" said 'could not mount C:\D at /srv/media/documents'
  end_case
}

# Finding 7: docker-ce is not held, and a re-run never takes it back down.
case_docker_is_not_held() {
  new_case docker_is_not_held
  run_prov "${wsl_args[@]}" --skip-models
  check "first run exit 0" eq "$RC" 0
  check "not held" not_called "apt-mark"
  # apt upgrade moved docker-ce on; docker is down at the next run.
  sed -i 's/^docker-ce=.*/docker-ce=5:29.9.0-1~ubuntu.26.04~resolute/' "$STATE/dpkg"
  rm -f "$STATE/active/docker.service"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "docker ran again" eq "$(state_of docker)" done
  check "nothing installed: no downgrade" not_called "apt-get"
  check "docker-ce stays where apt put it" grep -qx "docker-ce=5:29.9.0-1~ubuntu.26.04~resolute" "$STATE/dpkg"
  check "still not held" not_called "apt-mark"
  check "docker started" called "systemctl enable --now containerd.service docker.service"
  end_case
}

# Finding 8: journal text the script prints never carries the setup code.
case_setup_code_kept_out_of_the_journal_it_prints() {
  new_case setup_code_kept_out_of_the_journal_it_prints
  printf '%s\n' "Oct 09 domovoi-core[71]: ======" \
    "Oct 09 domovoi-core[71]:  Setup code: acorn-apple-arrow-autumn-badge-baker-basil-beach" \
    "Oct 09 domovoi-core[71]:  (also written to /home/domovoi/.domovoi/setup-code.txt)" \
    "Oct 09 domovoi-core[71]: echoed acorn-apple-arrow-autumn-badge-baker-basil-beach elsewhere" \
    "Oct 09 domovoi-core[71]: an ordinary line" >"$STATE/journal"
  touch "$STATE/core-unhealthy"
  run_prov "${wsl_args[@]}" --skip-models --health-timeout 10
  check "exit 1" eq "$RC" 1
  check "start failed" eq "$(state_of start)" fail
  check "the journal is shown" said "an ordinary line"
  check "no setup code" not_said "acorn-apple-arrow"
  check "no Setup code line" not_said "Setup code:"
  end_case
}

# Windows installer fixes 2026-10-12, item 3: a failed start leaves the
# real cause in the log (provision.log on Windows): systemd's status of the
# three units and their last 200 journal lines, without the setup code or
# a password in a URL.
case_failed_start_logs_status_and_journal() {
  new_case failed_start_logs_status_and_journal
  echo domovoi-db.service >"$STATE/start-fail"
  printf '%s\n' "Oct 12 docker[90]: Error starting userland proxy: listen tcp4 127.0.0.1:6432: bind: address already in use" \
    "Oct 12 domovoi-core[71]:  Setup code: acorn-apple-arrow-autumn-badge-baker-basil-beach" \
    "Oct 12 domovoi-core[71]: connecting to postgresql://domovoi:s3cret-Pass@127.0.0.1:6432/domovoi" \
    "Oct 12 domovoi-web[72]: an ordinary line" >"$STATE/journal"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "start failed" eq "$(state_of start)" fail
  check "final names the step" eq "$(final_field failed_step)" '"start"'
  check "systemd's status of all three" called "systemctl status --no-pager --full --lines=0 domovoi-db.service domovoi-core.service domovoi-web.service"
  check "the status is printed" said "x domovoi-db.service - fixture unit"
  check "it says the unit failed" said "Active: failed (fixture)"
  check "200 journal lines of all three" called "journalctl -u domovoi-db.service -u domovoi-core.service -u domovoi-web.service --no-pager -n 200"
  check "the cause is in the output" said "bind: address already in use"
  check "the ordinary line too" said "an ordinary line"
  check "no setup code" not_said "acorn-apple-arrow"
  check "no Setup code line" not_said "Setup code:"
  check "no password" not_said "s3cret-Pass"
  check "the URL without it" said "postgresql://domovoi:[hidden]@127.0.0.1:6432/domovoi"
  check "the message points at it" said "the units' last 200 journal lines are in the output above"
  check "one final line" progress_ok
  check "no marker" no_marker start
  end_case
}

# A health wait that runs out logs the same, for all three units.
case_unhealthy_start_logs_all_three_units() {
  new_case unhealthy_start_logs_all_three_units
  touch "$STATE/core-unhealthy"
  printf '%s\n' "Oct 12 domovoi-core[71]: alembic: migration failed" >"$STATE/journal"
  run_prov "${wsl_args[@]}" --skip-models --health-timeout 10
  check "exit 1" eq "$RC" 1
  check "status of all three" called "systemctl status --no-pager --full --lines=0 domovoi-db.service domovoi-core.service domovoi-web.service"
  check "journal of all three" called "journalctl -u domovoi-db.service -u domovoi-core.service -u domovoi-web.service --no-pager -n 200"
  check "the cause is in the output" said "migration failed"
  end_case
}

# Item 2: --stop-before start runs everything before the start step and
# stops (exit 0, result "stopped"), so the Windows installer can check the
# PC's ports; the next run without it starts Domovoi.
case_stop_before_start() {
  new_case stop_before_start
  run_prov "${wsl_args[@]}" --skip-models --stop-before start
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "result stopped" eq "$(final_field result)" '"stopped"'
  check "exit_code 0" eq "$(final_field exit_code)" 0
  check "says where it stopped" said "Stopped before: Starting Domovoi (--stop-before start)"
  check "the step before ran" marker timezone
  check "start did not run" no_marker start
  check "start never reported" eq "$(state_of start)" ""
  check "no unit started" not_called "systemctl start domovoi-db.service"
  check "result.json says stopped" grep -q '"result":"stopped"' "$ROOT/var/lib/domovoi-provision/result.json"
  run_prov "${wsl_args[@]}" --skip-models
  check "second run exit 0" eq "$RC" 0
  check "result ok" eq "$(final_field result)" '"ok"'
  check "the steps before are skipped" eq "$(state_of timezone)" skip
  check "start ran" eq "$(state_of start)" done
  check "units started" called "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service"
  end_case
}

case_stop_before_takes_a_step_inside_the_run() {
  new_case stop_before_takes_a_step_inside_the_run
  local bad
  for bad in preflight finish bogus; do
    run_prov "${wsl_args[@]}" --stop-before "$bad"
    check "--stop-before $bad: 2" eq "$RC" 2
    check "says which ($bad)" said "--stop-before takes a step id from apt to update-unit, not: $bad"
  done
  run_prov "${wsl_args[@]}" --stop-before ""
  check "--stop-before with nothing: 2" eq "$RC" 2
  check "nothing ran" not_called "apt-get"
  run_prov --help
  check "--help names it" said "--stop-before STEP"
  end_case
}

# Finding 9: every way out ends with exactly one final line.
case_every_exit_has_one_final_line() {
  new_case every_exit_has_one_final_line
  run_prov --bogus
  check "unknown option: exit 2" eq "$RC" 2
  check "unknown option: one final line" progress_ok
  check "unknown option: usage_error" eq "$(final_field result)" '"usage_error"'
  check "unknown option: exit_code 2" eq "$(final_field exit_code)" 2
  run_prov
  check "no --internet on WSL: one final line" progress_ok
  check "no --internet on WSL: usage_error" eq "$(final_field result)" '"usage_error"'
  run_prov --help
  check "--help: exit 0" eq "$RC" 0
  check "--help: one final line" progress_ok
  check "--help: help" eq "$(final_field result)" '"help"'
  echo 1000 >"$STATE/uid"
  run_prov "${wsl_args[@]}"
  check "not root: exit 1" eq "$RC" 1
  check "not root: one final line" progress_ok
  check "not root: failed" eq "$(final_field result)" '"failed"'
  rm -f "$STATE/uid"
  RC=0
  env -u DOMOVOI_PROVISION_TEST_HARNESS DOMOVOI_PROVISION_ROOT="$ROOT" PATH="$CASE/bin:$PATH" \
    bash "$SCRIPT" --dry-run >"$CASE/output.log" 2>&1 || RC=$?
  grep '^@@DOMOVOI ' "$CASE/output.log" | sed 's/^@@DOMOVOI //' >"$CASE/progress.jsonl" || true
  check "harness variable alone: exit 2" eq "$RC" 2
  check "harness variable alone: one final line" progress_ok
  check "harness variable alone: usage_error" eq "$(final_field result)" '"usage_error"'
  check "none of them wrote result.json" absent "$ROOT/var/lib/domovoi-provision/result.json"
  end_case
}

# Finding 11: the restart note is written before /etc/wsl.conf.
case_wsl_conf_restart_note_comes_first() {
  new_case wsl_conf_restart_note_comes_first
  echo '.wsl.conf.' >"$STATE/chown-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "wsl-config failed" eq "$(state_of wsl-config)" fail
  check "the restart note is there" test -f "$ROOT/var/lib/domovoi-provision/wsl-conf-pending"
  check "and reported" eq "$(final_field restart_required)" true
  rm -f "$STATE/chown-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "the next run exit 0" eq "$RC" 0
  check "wsl.conf written" has_line "$ROOT/etc/wsl.conf" "default=domovoi"
  check "restart still required" eq "$(final_field restart_required)" true
  end_case
}

# Finding 13: bare-metal media paths the services could not see, or that
# systemd would misread.
case_bare_metal_media_paths() {
  new_case bare_metal_media_paths --bare
  run_prov --internet sometimes --skip-models --media music=/home/kamron/Music
  check "another user's home: exit 2" eq "$RC" 2
  check "says why" said "is in another user's home, which the services can't see"
  check "nothing ran" not_called "apt-get"
  run_prov --internet sometimes --skip-models --media documents=/data/100%docs --media music=/home/domovoi/Music
  check "exit 0" eq "$RC" 0
  check "% escaped for systemd" has_line "$ROOT/etc/systemd/system/domovoi-core.service.d/domovoi-media.conf" "ReadWritePaths=-/data/100%%docs"
  check "the service user's own home is fine" has_line "$CHECKOUT/domovoi/.env" "MUSIC_DIR=/home/domovoi/Music"
  end_case
}

# Owner decision 2026-10-12: the tier's vision model (pictures in chat) is
# written to .env and pulled, unless --no-vision-model; it is written all
# the same then, so chat names the model to install.
case_vision_model_pull_is_optional() {
  new_case vision_model_pull_is_optional
  local envf=$CHECKOUT/domovoi/.env vision=qwen3-vl:2b-instruct-q4_K_M
  run_prov "${wsl_args[@]}" --no-vision-model=yes
  check "--no-vision-model takes no value: 2" eq "$RC" 2
  run_prov --dry-run "${wsl_args[@]}" --no-vision-model
  check "dry run exit 0" eq "$RC" 0
  check "dry run: still written" said "OLLAMA_VISION_MODEL=$vision"
  check "dry run: says it is not pulled" said "(--no-vision-model: $vision is not pulled)"
  check "dry run: no pull planned" not_said "ollama pull $vision"
  run_prov "${wsl_args[@]}" --no-vision-model
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "the vision model not pulled" not_called "\"model\":\"$vision\""
  check "the voice models pulled" called '"model":"llama3.2:3b-instruct-q4_K_M"'
  check "said so" said "--no-vision-model: $vision is not pulled"
  check ".env names it all the same" has_line "$envf" "OLLAMA_VISION_MODEL=$vision"
  # The same box, run again without the option: the vision model alone is
  # pulled, and nothing else changes.
  run_prov "${wsl_args[@]}"
  check "second run exit 0" eq "$RC" 0
  check "models re-ran" eq "$(state_of models)" done
  check "env skipped: .env already right" eq "$(state_of env)" skip
  check "the vision model pulled" called "\"model\":\"$vision\""
  check "the voice models not pulled again" not_called '"model":"llama3.2:3b-instruct-q4_K_M"'
  check "said its size" said "reads pictures in chat (about 1.9 GB"
  check "its digest checked against the pin" said "$vision pulled, digest matches the pin"
  check "nothing restarted" not_called "systemctl restart"
  end_case
}

# The vision model follows the household-settings rule like every model
# key: written when absent or still the script's, kept when the household
# set it, on a box this script set up before it wrote the key as well.
case_vision_model_respects_the_household() {
  new_case vision_model_respects_the_household
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written
  local vision=qwen3-vl:2b-instruct-q4_K_M marker=$ROOT/var/lib/domovoi-provision/steps/env
  run_prov "${wsl_args[@]}" --skip-models
  check "first run exit 0" eq "$RC" 0
  check "written" has_line "$envf" "OLLAMA_VISION_MODEL=$vision"
  check "recorded as the script's" has_line "$rec" "OLLAMA_VISION_MODEL=$vision"
  # A box with a GPU: the household picks the GPU model in Settings.
  sed -i 's/^OLLAMA_VISION_MODEL=.*/OLLAMA_VISION_MODEL=qwen2.5vl:7b/' "$envf"
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.70
  check "exit 0" eq "$RC" 0
  check "env re-ran" eq "$(state_of env)" done
  check "the household's vision model kept" has_line "$envf" "OLLAMA_VISION_MODEL=qwen2.5vl:7b"
  check "said it kept it" said "OLLAMA_VISION_MODEL holds a value this script did not write"
  # A box set up before the script wrote the key: no record of it, and the
  # env step's inputs differ from its marker. Absent: the tier's is written.
  sed -i '/^OLLAMA_VISION_MODEL=/d' "$envf" "$rec"
  echo stale >"$marker"
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.70
  check "upgrade exit 0" eq "$RC" 0
  check "absent: written" has_line "$envf" "OLLAMA_VISION_MODEL=$vision"
  check "absent: recorded" has_line "$rec" "OLLAMA_VISION_MODEL=$vision"
  # The same, where the household had set one by hand: it stays theirs.
  sed -i 's/^OLLAMA_VISION_MODEL=.*/OLLAMA_VISION_MODEL=llava:13b/' "$envf"
  sed -i '/^OLLAMA_VISION_MODEL=/d' "$rec"
  echo stale >"$marker"
  run_prov "${wsl_args[@]}" --skip-models --lan-ip 192.168.1.70
  check "upgrade with the household's value exit 0" eq "$RC" 0
  check "the household's value kept" has_line "$envf" "OLLAMA_VISION_MODEL=llava:13b"
  check "said so again" said "OLLAMA_VISION_MODEL holds a value this script did not write"
  check "not recorded as the script's" eq "$(grep -c '^OLLAMA_VISION_MODEL=' "$rec")" 0
  end_case
}

CASES=(
  case_help_and_usage_errors
  case_import_check_names_no_dropped_package
  case_harness_vars_need_each_other
  case_not_root_refuses
  case_dry_run_changes_nothing
  case_full_run_wsl
  case_second_run_skips_everything
  case_distro_restart_clears_restart_required
  case_resume_after_failure
  case_changed_input_reruns_its_step
  case_ollama_sha_mismatch_stops
  case_whisper_sha_mismatch_stops
  case_piper_sha_mismatch_stops
  case_piper_unpinned_voice_with_a_space
  case_piper_empty_configured_voice
  case_model_digest_mismatch_stops
  case_docker_key_mismatch_stops
  case_manifest_missing_key_stops
  case_manifest_unknown_key_stops
  case_lock_without_build_tools_stops
  case_media_list_change_rewrites_the_block
  case_media_mount_failure_stops
  case_image_pull_failure_stops
  case_bare_metal
  case_internet_never_keeps_searxng_off
  case_wsl_without_systemd_stops_for_a_restart
  case_lock_busy_has_its_own_exit_code
  case_existing_checkout_is_not_moved
  case_ref_sha_follows_the_track_branch
  case_ref_off_the_track_branch_stops
  case_clone_from_a_bundle
  case_update_unit_failure_stops
  case_core_not_healthy_stops
  case_env_keeps_household_settings
  case_existing_user_is_not_locked
  case_docker_key_file_with_a_second_key_stops
  case_failed_checkout_is_repaired
  case_media_missing_on_a_rerun_warns
  case_docker_is_not_held
  case_setup_code_kept_out_of_the_journal_it_prints
  case_failed_start_logs_status_and_journal
  case_unhealthy_start_logs_all_three_units
  case_stop_before_start
  case_stop_before_takes_a_step_inside_the_run
  case_every_exit_has_one_final_line
  case_children_do_not_hold_the_lock
  case_wsl_conf_restart_note_comes_first
  case_bare_metal_media_paths
  case_os_jobs_on_bare_metal
  case_wsl_zone_follows_windows
  case_bare_metal_sets_the_zone
  case_cpu_only_box_reports_no_gpu
  case_update_defaults_opt_into_the_lock
  case_journal_kept_on_disk
  case_journal_opt_out_is_honoured
  case_os_jobs_opt_out_is_honoured
  case_os_jobs_never_leaves_the_box_degraded
  case_vision_model_pull_is_optional
  case_vision_model_respects_the_household
)

# --case / --list / ONLY=<regex> (case-filter.sh) pick what runs; the suite
# runs them all. The origin every case clones is built once, and only when
# something is going to run.
harness_select
if [ ${#SELECTED_CASES[@]} -gt 0 ]; then build_origin; fi
for c in ${SELECTED_CASES[@]+"${SELECTED_CASES[@]}"}; do
  "$c"
done

echo "provision harness: $PASSED passed, $FAILED failed, $SKIPPED skipped$(harness_selection_note)"
[ "$FAILED" -eq 0 ]
