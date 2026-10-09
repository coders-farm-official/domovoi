#!/usr/bin/env bash
# Hermetic tests for scripts/linux/provision.sh.
#
# Each case builds a fake root directory the script reads and writes
# through DOMOVOI_PROVISION_ROOT (/etc, /proc, /var/lib, /usr/local, the
# service user's home), clones a throwaway origin repository that carries
# just the files the script reads (the pin manifest, docs/LINUX_HOST.md's
# Ollama pin, the shipped units, a lock header, a stub
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
#     --now); docker keeps built images and the search helper's state;
#   * the venv's python answers the script's tagged snippets
#     (#provision:...): env-write applies PROVISION_ENV_KEYS to the
#     checkout's domovoi/.env, piper-info / piper-download and
#     whisper-download write fake model files whose hashes the fixture
#     manifest pins (whisper-content and piper-content override them);
#     `-m pip install -r` fails when pip-fail is set.
#
# Usage: bash scripts/linux/tests/test-provision.sh   (ONLY=<regex> runs some)
# Exit status is non-zero if any case failed.

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
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

# A real Python for checking the JSON progress lines (not the shims').
JSON_PY=""
for p in python3 python; do
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
  cp "$REAL_REPO"/scripts/linux/units/*.service "$o/scripts/linux/units/"
  cat >"$o/scripts/linux/install-update-unit.sh" <<'SH'
#!/usr/bin/env bash
echo "install-update-unit $*" >>"$SHIM_STATE/calls.log"
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
  printf 'OLLAMA_URL=http://localhost:11434\nWHISPER_MODEL=large-v3\n' >"$o/domovoi/.env.example"
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

  cat >"$bin/apt-get" <<'SH'
#!/usr/bin/env bash
echo "apt-get $*" >>"$SHIM_STATE/calls.log"
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

  cat >"$bin/gpg" <<'SH'
#!/usr/bin/env bash
echo "gpg $*" >>"$SHIM_STATE/calls.log"
echo "pub:-:4096:1:8D81803C0EBFCD88:1487788586:::-:::scESA::::::23::0:"
echo "fpr:::::::::$(cat "$SHIM_STATE/docker-fpr"):"
echo "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:"
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
    printf 'FAKE DOCKER KEY\n' >"$out" ;;
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
    dg=$(grep "^$tag " "$SHIM_STATE/registry" | cut -d' ' -f2)
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
mkdir -p "$SHIM_STATE/active" "$SHIM_STATE/enabled"
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
  enable) for u in "${units[@]}"; do touch "$SHIM_STATE/enabled/$u"; [ "$now" = 0 ] || touch "$SHIM_STATE/active/$u"; done ;;
  start|restart)
    for u in "${units[@]}"; do
      if [ -f "$SHIM_STATE/start-fail" ]; then echo "Job for $u failed" >&2; exit 1; fi
      touch "$SHIM_STATE/active/$u"
    done ;;
  stop) for u in "${units[@]}"; do rm -f "$SHIM_STATE/active/$u"; done ;;
esac
exit 0
SH

  cat >"$bin/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_STATE/calls.log"
mkdir -p "$SHIM_STATE/images"
case "$1 ${2-}" in
  "info "*) exit 0 ;;
  "version "*) echo 29.8.1 ;;
  "image inspect") [ -f "$SHIM_STATE/images/${3//[:\/]/_}" ] || exit 1 ;;
  "build "*)
    while [ $# -gt 0 ]; do [ "$1" = -t ] && { touch "$SHIM_STATE/images/${2//[:\/]/_}"; break; }; shift; done ;;
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

  cat >"$bin/chown" <<'SH'
#!/usr/bin/env bash
echo "chown $*" >>"$SHIM_STATE/calls.log"
SH

  cat >"$bin/flock" <<'SH'
#!/usr/bin/env bash
[ ! -f "$SHIM_STATE/locked" ]
SH

  cat >"$bin/mountpoint" <<'SH'
#!/usr/bin/env bash
grep -qx -- "${!#}" "$SHIM_STATE/mounts" 2>/dev/null
SH

  cat >"$bin/mount" <<'SH'
#!/usr/bin/env bash
echo "mount $*" >>"$SHIM_STATE/calls.log"
[ ! -f "$SHIM_STATE/mount-fail" ] || { echo "mount: fixture: No such file or directory" >&2; exit 32; }
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

  cat >"$bin/journalctl" <<'SH'
#!/usr/bin/env bash
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
    "#provision:env-write")
      while IFS= read -r kv; do
        [ -n "$kv" ] || continue
        k=${kv%%=*}
        if grep -qx -- "$kv" "$envf"; then continue; fi
        if grep -q "^$k=" "$envf"; then
          grep -v "^$k=" "$envf" >"$envf.new"; echo "$kv" >>"$envf.new"; mv "$envf.new" "$envf"
        else
          echo "$kv" >>"$envf"
        fi
        echo "$k"
      done <<<"$PROVISION_ENV_KEYS"
      exit 0 ;;
    "#provision:env-verify") exit 0 ;;
    "#provision:piper-info")
      echo en_US-lessac-medium
      echo "$HOME/.domovoi/piper_voices"
      cat "$SHIM_STATE/piper-pin"
      echo 16
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
  echo /usr/share/zoneinfo/Etc/UTC >"$ROOT/etc/localtime.harness-link"
  printf '#!/bin/sh\n' >"$ROOT/usr/bin/docker"; chmod +x "$ROOT/usr/bin/docker"
  write_shims "$CASE/bin"
  : >"$STATE/calls.log"; : >"$STATE/dpkg"; : >"$STATE/ollama-models"; : >"$STATE/users"; : >"$STATE/mounts"
  echo 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 >"$STATE/docker-fpr"
  printf '%s' "$WHISPER_CONTENT" >"$STATE/whisper-content"
  printf '%s' "$PIPER_CONTENT" >"$STATE/piper-content"
  sha_of_text "$PIPER_CONTENT" >"$STATE/piper-pin"
  # What the registry serves for each tag: the manifest's digests.
  sed -n 's/^tier_[a-z]*_\(tool\|qa\)_model=\(.*\)$/\2/p' "$ORIGIN/scripts/linux/provision-manifest.conf" >"$CASE/tags"
  : >"$STATE/registry"
  for t in floor_tool floor_qa standard_tool standard_qa; do
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
wsl_args=(--internet always --timezone America/New_York)

called() { grep -qF -- "$1" "$STATE/calls.log"; }
not_called() { ! grep -qF -- "$1" "$STATE/calls.log"; }
said() { grep -qF -- "$1" "$CASE/output.log"; }
not_said() { ! grep -qF -- "$1" "$CASE/output.log"; }
eq() { [ "$1" = "$2" ] || { echo "      expected [$2], got [$1]"; return 1; }; }
has_line() { grep -qxF -- "$2" "$1" 2>/dev/null || { echo "      no line [$2] in $1"; return 1; }; }
absent() { [ ! -e "$1" ]; }
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
    grep -qv '^{"v":1,"step":"[a-z-]*","index":[0-9]*,"total":20,"state":"[a-z]*","percent":[0-9]*,"message":"' "$CASE/progress.jsonl" \
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
    assert o["v"] == 1 and o["total"] == 20, l
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
        for k in ("result", "restart_required", "exit_code", "failed_step"):
            assert k in o, "final lacks " + k
        assert isinstance(o["restart_required"], bool)
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

STEPS=(preflight apt user wsl-config media checkout docker venv env ollama models whisper piper mpd-image searxng units timezone start update-unit)

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
  check "plans MPD_HTTP_BASE" said "MPD_HTTP_BASE=http://192.168.1.50"
  check "plan lines for every step but the preflight" eq "$(grep -c '"state":"plan"' "$CASE/progress.jsonl")" 18
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
  run_prov "${wsl_args[@]}" --media 'music=C:\Users\Public\Domovoi\My Music' --media 'videos=D:\Videos' --lan-ip 192.168.1.50
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "result ok" eq "$(final_field result)" '"ok"'
  check "restart required (wsl.conf changed)" eq "$(final_field restart_required)" true
  check "100% at the end" eq "$(final_field percent)" 100
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
  check "videos has no setting: warned" said "no Domovoi setting reads a 'videos' folder"
  # checkout
  check "cloned" test -d "$CHECKOUT/.git"
  check "on main, tracking origin/main" eq "$(git -C "$CHECKOUT" rev-parse --abbrev-ref '@{u}')" origin/main
  check "HEAD in the final line" eq "$(final_field head)" "\"$FIXTURE_B\""
  check "cloned as the service user" called "runuser -u domovoi -- env HOME=/home/domovoi"
  # docker
  check "docker key fingerprint checked" called "gpg --batch --show-keys --with-colons"
  check "docker key installed" test -f "$ROOT/etc/apt/keyrings/docker.asc"
  check "docker.sources" has_line "$ROOT/etc/apt/sources.list.d/docker.sources" "Suites: resolute"
  check "docker-ce at the pin" called "docker-ce=5:29.8.1-1~ubuntu.26.04~resolute"
  check "compose plugin at the pin" called "docker-compose-plugin=5.5.1-1~ubuntu.26.04~resolute"
  check "held" called "apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
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
  check ".env: Whisper on cpu/int8" has_line "$envf" "WHISPER_COMPUTE_TYPE=int8"
  check ".env: the template's Whisper replaced" eq "$(grep -c '^WHISPER_MODEL=' "$envf")" 1
  check ".env: Ollama by IPv4" has_line "$envf" "OLLAMA_URL=http://127.0.0.1:11434"
  check ".env: no USB adoption on WSL" has_line "$envf" "SATELLITE_ADOPTION_ENABLED=false"
  check ".env: MPD_HTTP_BASE" has_line "$envf" "MPD_HTTP_BASE=http://192.168.1.50"
  check ".env: MUSIC_DIR" has_line "$envf" "MUSIC_DIR=/srv/media/music"
  check ".env: no videos key" eq "$(grep -ci videos "$envf")" 0
  # ollama, models, whisper, piper
  check "Ollama tarball fetched" called "curl https://github.com/ollama/ollama/releases/download/v0.34.4/ollama-linux-amd64.tar.zst"
  check "Ollama binary" test -x "$ROOT/usr/local/bin/ollama"
  check "Ollama stamp" test -f "$ROOT/usr/local/lib/ollama/.domovoi-provision"
  check "tarball removed" eq "$(ls "$ROOT/var/cache/domovoi-provision" 2>/dev/null | grep -c .)" 0
  check "ollama.service is the doc's" has_line "$ROOT/etc/systemd/system/ollama.service" "ExecStart=/usr/local/bin/ollama serve"
  check "pulled the QA model" called '"model":"llama3.2:3b-instruct-q4_K_M"'
  check "pulled the router" called '"model":"qwen3:8b-q4_K_M"'
  check "download progress lines" grep -q '"step":"models".*"state":"progress".*"bytes":' "$CASE/progress.jsonl"
  check "Ollama download bytes" grep -q '"step":"ollama".*"bytes":' "$CASE/progress.jsonl"
  check "Whisper at the pinned revision" test -f "$ROOT/home/domovoi/.cache/huggingface/hub/models--Systran--faster-whisper-small.en/snapshots/d1d751a5f8271d482d14ca55d9e2deeebbae577f/model.bin"
  check "Piper voice" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-lessac-medium.onnx"
  # images, search helper
  check "MPD image built as the core does" called "docker build --progress=plain -t domovoi-mpd:latest -f $CHECKOUT/domovoi/Dockerfile.mpd $CHECKOUT/domovoi"
  check "search helper up (always)" called "docker compose up -d --no-deps searxng"
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
  # zone, start, updater
  check "time zone set" called "timedatectl set-timezone America/New_York"
  check "units started" called "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service"
  check "nothing restarted on a first run" not_called "systemctl restart domovoi"
  check "update unit installed" called "install-update-unit --repo $CHECKOUT --user domovoi"
  check "update defaults left as the doc has them" absent "$ROOT/etc/default/domovoi-update"
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
  for s in apt user checkout docker venv ollama whisper piper mpd-image units update-unit; do
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
  check "the voice is still fetched ahead" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-lessac-medium.onnx"
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

case_lock_held_refuses() {
  new_case lock_held_refuses
  touch "$STATE/locked"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "says another run holds it" said "another provision run holds"
  check "nothing ran" not_called "apt-get"
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

build_origin

CASES=(
  case_help_and_usage_errors
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
  case_model_digest_mismatch_stops
  case_docker_key_mismatch_stops
  case_manifest_missing_key_stops
  case_manifest_unknown_key_stops
  case_lock_without_build_tools_stops
  case_media_list_change_rewrites_the_block
  case_media_mount_failure_stops
  case_bare_metal
  case_internet_never_keeps_searxng_off
  case_wsl_without_systemd_stops_for_a_restart
  case_lock_held_refuses
  case_existing_checkout_is_not_moved
  case_ref_sha_follows_the_track_branch
  case_ref_off_the_track_branch_stops
  case_clone_from_a_bundle
  case_update_unit_failure_stops
  case_core_not_healthy_stops
)

for c in "${CASES[@]}"; do
  if [ -n "${ONLY:-}" ] && ! [[ $c =~ ${ONLY} ]]; then continue; fi
  "$c"
done

echo "provision harness: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[ "$FAILED" -eq 0 ]
