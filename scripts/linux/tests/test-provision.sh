#!/usr/bin/env bash
# Hermetic tests for scripts/linux/provision.sh.
#
# Each case builds a fake root directory the script reads and writes
# through DOMOVOI_PROVISION_ROOT (/etc, /proc, /var/lib, /usr/local, the
# service user's home), clones a throwaway origin repository that carries
# just the files the script reads (the pin manifest, docs/LINUX_HOST.md's
# Ollama pin, the shipped units, the real os-jobs.sh and gpu-runtime.sh, a
# lock header, a stub install-update-unit.sh), and puts PATH shims in front
# of everything that would touch the machine: apt-get, dpkg-query, apt-mark,
# curl, gpg, systemctl, docker, useradd, usermod, passwd, id, getent,
# runuser, install, chown, flock, mount, umount, mountpoint, findmnt,
# timedatectl, readlink, visudo, sudo, journalctl, tar, nvidia-smi,
# nvidia-ctk, python3 and the venv's python. Every shim
# logs its call and can be told to fail. git, sha256sum and the rest of
# coreutils are real. Nothing touches the host, so this runs under Git Bash
# as well as on Linux.
#
# The shims model just enough to test the script's decisions:
#   * dpkg-query answers for what the apt-get shim "installed", at the
#     version it was asked for (pkg=ver), else "1"; `apt-get install -s`
#     prints the "Inst" lines of a simulation (plus apt-simulate-extra's);
#   * a GPU is the case's to give: gpu_wsl puts /dev/dxg and WSL's
#     libcuda.so.1 in the root, gpu_bare has nvidia-smi list one (it fails
#     otherwise); nvidia-ctk writes the nvidia runtime into the root's
#     daemon.json, which dockerd (the systemctl shim) reads at each start
#     and `docker info` lists, or refuses to start with while
#     docker-rejects-nvidia is set;
#   * curl serves a fake Ollama tarball for the release URL (or a tampered
#     one when ollama-tampered is set), the Docker and NVIDIA keys (and a
#     second key in the file when docker-extra-key / nvidia-extra-key is
#     set), Ollama's API
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
# The real flock(1), for a wait on a descriptor (tidy_checkout's on the
# checkout lock), which the shim below hands over to it; empty without one.
SHIM_REAL_FLOCK=$(command -v flock || true)
SHIM_REAL_READLINK=$(command -v readlink)
SHIM_REAL_GIT=$(command -v git)
SHIM_REAL_TAR=$(command -v tar)
export SHIM_REAL_INSTALL SHIM_REAL_READLINK SHIM_REAL_GIT SHIM_REAL_TAR SHIM_REAL_FLOCK

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
  # The real helpers: provision.sh runs them from the checkout, under the
  # same harness root.
  cp "$REAL_REPO/scripts/linux/os-jobs.sh" "$o/scripts/linux/os-jobs.sh"
  cp "$REAL_REPO/scripts/linux/gpu-runtime.sh" "$o/scripts/linux/gpu-runtime.sh"
  cp "$REAL_REPO/scripts/linux/db-diagnostics.sh" "$REAL_REPO/scripts/linux/db-half-made.sh" "$o/scripts/linux/"
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
  cat >"$o/requirements-linux-py314-no-deps.lock" <<'LOCK'
# Installed with --no-deps (requirements-no-deps.in says why).
faster-whisper==1.2.1 \
    --hash=sha256:44
    # via -r requirements-no-deps.in
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
  # A branch without the no-deps lock (faster-whisper, resemblyzer).
  git -C "$o" checkout -q -b no-nodeps-lock
  git -C "$o" rm -q requirements-linux-py314-no-deps.lock
  git -C "$o" commit -q -m "no no-deps lock"
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
  # install -s (a simulation) prints apt's "Inst <pkg> (<ver> ...)" lines
  # for what it was asked, plus the lines in apt-simulate-extra (a driver
  # that would come along), and installs nothing.
  cat >"$bin/apt-get" <<'SH'
#!/usr/bin/env bash
echo "apt-get $*" >>"$SHIM_STATE/calls.log"
for f in /proc/$$/fd/*; do readlink "$f" 2>/dev/null; done >>"$SHIM_STATE/apt-fds"
[ ! -f "$SHIM_STATE/apt-fail" ] || { echo "E: fixture failure" >&2; exit 100; }
for a in "$@"; do
  case $a in
    -s|--simulate|--dry-run|--just-print)
      echo "NOTE: This is only a simulation!"
      install=0
      for b in "$@"; do
        case $b in
          install) install=1 ;;
          -*) ;;
          *) if [ "$install" = 1 ]; then p=${b%%=*}; v=1; [[ $b == *=* ]] && v=${b#*=}; echo "Inst $p ($v fixture [amd64])"; fi ;;
        esac
      done
      [ ! -f "$SHIM_STATE/apt-simulate-extra" ] || cat "$SHIM_STATE/apt-simulate-extra"
      exit 0 ;;
  esac
done
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
  https://nvidia.github.io/libnvidia-container/gpgkey)
    printf 'KEY %s\n' "$(cat "$SHIM_STATE/nvidia-fpr")" >"$out"
    [ ! -f "$SHIM_STATE/nvidia-extra-key" ] || printf 'KEY %s\n' "$(cat "$SHIM_STATE/nvidia-extra-key")" >>"$out" ;;
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
    # The core answers only with its database up (its health reads it).
    active domovoi-core.service && active domovoi-db.service && [ ! -f "$SHIM_STATE/core-unhealthy" ] \
      || { echo "curl: (7)" >&2; exit 7; } ;;
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
# dockerd reads /etc/docker/daemon.json when it starts (docker-loaded keeps
# what it read; the docker shim's `info` lists the runtimes from it). With
# docker-rejects-nvidia, a daemon.json naming the nvidia runtime keeps it
# from starting.
docker_starts() {
  local cfg=$SHIM_ROOT/etc/docker/daemon.json
  if [ -f "$SHIM_STATE/docker-rejects-nvidia" ] && grep -q '"nvidia"' "$cfg" 2>/dev/null; then return 1; fi
  if [ -f "$cfg" ]; then cp "$cfg" "$SHIM_STATE/docker-loaded"; else : >"$SHIM_STATE/docker-loaded"; fi
}
case $cmd in
  is-active)
    if [ -f "$SHIM_STATE/active/${units[0]}" ]; then [ "$quiet" = 1 ] || echo active; exit 0; fi
    if [ -f "$SHIM_STATE/activating/${units[0]}" ]; then [ "$quiet" = 1 ] || echo activating; exit 3; fi
    if [ -f "$SHIM_STATE/failed/${units[0]}" ]; then [ "$quiet" = 1 ] || echo failed; exit 3; fi
    [ "$quiet" = 1 ] || echo inactive; exit 3 ;;
  is-enabled)
    if [ -f "$SHIM_STATE/enabled/${units[0]}" ]; then echo enabled; exit 0; fi
    echo disabled; exit 1 ;;
  is-failed)
    if [ -f "$SHIM_STATE/failed/${units[0]}" ]; then [ "$quiet" = 1 ] || echo failed; exit 0; fi
    [ "$quiet" = 1 ] || echo active; exit 1 ;;
  enable)
    for u in "${units[@]}"; do
      touch "$SHIM_STATE/enabled/$u"
      [ "$now" = 0 ] || touch "$SHIM_STATE/active/$u"
      if [ "$now" = 1 ] && [ "$u" = docker.service ]; then docker_starts || true; fi
    done ;;
  disable) for u in "${units[@]}"; do rm -f "$SHIM_STATE/enabled/$u"; [ "$now" = 0 ] || rm -f "$SHIM_STATE/active/$u"; done ;;
  start|restart)
    # start-fail: every start fails; with unit names in it, only theirs.
    for u in "${units[@]}"; do
      if [ -f "$SHIM_STATE/start-fail" ] && { [ ! -s "$SHIM_STATE/start-fail" ] || grep -qx -- "$u" "$SHIM_STATE/start-fail"; }; then
        echo "Job for $u failed because the control process exited with error code." >&2; touch "$SHIM_STATE/failed/$u"; exit 1
      fi
      if [ "$u" = docker.service ] && ! docker_starts; then
        echo "Job for docker.service failed because the control process exited with error code." >&2
        rm -f "$SHIM_STATE/active/$u"; touch "$SHIM_STATE/failed/$u"; exit 1
      fi
      # start-then-fail: a --no-block start returns at once, and the unit
      # fails afterwards (domovoi-db's Flyway, say).
      if grep -qx -- "$u" "$SHIM_STATE/start-then-fail" 2>/dev/null; then
        touch "$SHIM_STATE/failed/$u"; continue
      fi
      # start-activating: a --no-block start that never finishes (a oneshot
      # still running: domovoi-db waiting for a new database's first start).
      if grep -qx -- "$u" "$SHIM_STATE/start-activating" 2>/dev/null; then
        mkdir -p "$SHIM_STATE/activating"; touch "$SHIM_STATE/activating/$u"; continue
      fi
      # start-queued: a --no-block start whose job waits (the core's, for
      # the database's): a failed mark from before stays until it runs.
      if grep -qx -- "$u" "$SHIM_STATE/start-queued" 2>/dev/null; then
        touch "$SHIM_STATE/active/$u"; continue
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
  "info "*)
    # The daemon answers while docker.service runs; its runtimes are what
    # it read from daemon.json at its last start (the systemctl shim).
    [ -f "$SHIM_STATE/active/docker.service" ] || { echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock" >&2; exit 1; }
    case " $* " in
      *Runtimes*) printf 'io.containerd.runc.v2 '; grep -q '"nvidia"' "$SHIM_STATE/docker-loaded" 2>/dev/null && printf 'nvidia '; echo 'runc ' ;;
    esac
    exit 0 ;;
  "version "*) echo 29.8.1 ;;
  # image inspect [-f '{{.Id}}'] REF: there or not; with -f, the image's
  # ID (what a build wrote in its file, else one made from the name).
  "image inspect")
    ref=${!#}; f=$SHIM_STATE/images/${ref//[:\/@]/_}
    [ -f "$f" ] || exit 1
    if [ "$3" = -f ]; then
      if [ -s "$f" ]; then cat "$f"; else echo "sha256:$(printf '%s' "$ref" | sha256sum | cut -c1-64)"; fi
    fi ;;
  # Every build gives a new image ID (as one that misses Docker's build
  # cache does: the apt layer is not reproducible).
  "build "*)
    n=$(($(cat "$SHIM_STATE/builds" 2>/dev/null || echo 0) + 1)); echo "$n" >"$SHIM_STATE/builds"
    while [ $# -gt 0 ]; do
      [ "$1" = -t ] && { echo "sha256:$(printf 'build-%s' "$n" | sha256sum | cut -c1-64)" >"$SHIM_STATE/images/${2//[:\/@]/_}"; break; }
      shift
    done ;;
  # Room containers: $SHIM_STATE/containers/NAME holds the image ID it runs.
  "ps -a") ls "$SHIM_STATE/containers" 2>/dev/null || true ;;
  "compose config") echo "$refs" ;;
  "compose pull")
    [ ! -f "$SHIM_STATE/pull-fail" ] || { echo "Error response from daemon: toomanyrequests" >&2; exit 1; }
    for r in $refs; do touch "$SHIM_STATE/images/${r//[:\/@]/_}"; done ;;
  "compose up") touch "$SHIM_STATE/searxng-running" ;;
  # The Postgres container, as scripts/linux/db-diagnostics.sh asks after
  # it: pg-state is "status|health|exit code|restarts", pg-health the
  # last health check's output, pg-logs its log (no pg-state: no container).
  # pg-volume: the volume domovoi-pgdata exists, holding what a look at
  # its files prints (db-half-made.sh: "none" without PG_VERSION, else the
  # names in base/).
  "inspect --format")
    if [ "${!#}" = domovoi-postgres ] && [ -f "$SHIM_STATE/pg-state" ]; then
      case $3 in
        *Health.Log*) cat "$SHIM_STATE/pg-health" 2>/dev/null ;;
        *Config.Env*) cat "$SHIM_STATE/pg-env" 2>/dev/null ;;
        *RestartCount*) cat "$SHIM_STATE/pg-state" ;;
        *.Image*) echo sha256:0000pgimage ;;
        *) cut -d'|' -f1 "$SHIM_STATE/pg-state" ;;
      esac
      exit 0
    fi
    echo "Error: No such object: ${!#}" >&2; exit 1 ;;
  "logs "*) [ -f "$SHIM_STATE/pg-state" ] || { echo "Error: No such container: ${!#}" >&2; exit 1; }
    # Written by perl, which (like docker logs, a Go program) dies of
    # SIGPIPE when its reader stops early, and the pipeline's status says
    # so; this image's cat (uutils) exits 0 instead and would hide that.
    [ -f "$SHIM_STATE/pg-logs" ] || exit 0
    if [ "$2" = --tail ]; then tail -n "$3" "$SHIM_STATE/pg-logs" | exec perl -pe ''; else exec perl -pe '' "$SHIM_STATE/pg-logs"; fi ;;
  "volume inspect") [ -f "$SHIM_STATE/pg-volume" ] || { echo "Error: no such volume: $3" >&2; exit 1; } ;;
  "volume rm") rm -f "$SHIM_STATE/pg-volume" ;;
  "rm -f") if [ -f "$SHIM_STATE/containers/$3" ]; then rm -f "$SHIM_STATE/containers/$3"; else rm -f "$SHIM_STATE/pg-state"; fi ;;
  "run --rm") [ -f "$SHIM_STATE/pg-volume" ] || exit 1; cat "$SHIM_STATE/pg-volume" ;;
  "inspect -f")
    if [ -f "$SHIM_STATE/containers/${!#}" ]; then cat "$SHIM_STATE/containers/${!#}"; exit 0; fi
    if [ -f "$SHIM_STATE/searxng-running" ]; then echo true; else echo false; fi ;;
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
  # flock -w SECONDS FD (a wait on a descriptor) is the real flock's.
  cat >"$bin/flock" <<'SH'
#!/usr/bin/env bash
echo "flock $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = -w ] && [[ ${3-} =~ ^[0-9]+$ ]] && [ $# -eq 3 ]; then
  [ -n "$SHIM_REAL_FLOCK" ] || exit 0
  exec "$SHIM_REAL_FLOCK" "$@"
fi
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

  # Only Ollama's tarball (--zstd) is faked; anything else (the verified
  # copy's git archive) is the real tar.
  cat >"$bin/tar" <<'SH'
#!/usr/bin/env bash
echo "tar $*" >>"$SHIM_STATE/calls.log"
case " $* " in *" --zstd "*) ;; *) exec "$SHIM_REAL_TAR" "$@" ;; esac
dest=""
while [ $# -gt 0 ]; do [ "$1" = -C ] && dest=$2; shift; done
mkdir -p "$dest/bin" "$dest/lib/ollama"
printf '#!/bin/sh\nexit 0\n' >"$dest/bin/ollama"; chmod +x "$dest/bin/ollama"
SH

  # The NVIDIA driver's nvidia-smi: the GPUs in nvidia-smi-gpus, else the
  # driver's own failure. Always on PATH here, so the host's own (Git for
  # Windows sees Windows' nvidia-smi.exe) never answers for a case.
  cat >"$bin/nvidia-smi" <<'SH'
#!/usr/bin/env bash
echo "nvidia-smi $*" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/nvidia-smi-gpus" ]; then cat "$SHIM_STATE/nvidia-smi-gpus"; exit 0; fi
echo "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver. Make sure that the latest NVIDIA driver is installed and running."
exit 9
SH

  # nvidia-ctk runtime configure --runtime=docker [--config=FILE]: the
  # nvidia runtime into Docker's daemon.json (the harness root's when no
  # --config is given), keeping what the file held; a file that names it
  # already is left as it is.
  cat >"$bin/nvidia-ctk" <<'SH'
#!/usr/bin/env bash
echo "nvidia-ctk $*" >>"$SHIM_STATE/calls.log"
[ ! -f "$SHIM_STATE/nvidia-ctk-fail" ] || { echo "nvidia-ctk: fixture failure" >&2; exit 1; }
cfg=$SHIM_ROOT/etc/docker/daemon.json
for a in "$@"; do case $a in --config=*) cfg=${a#--config=} ;; esac; done
mkdir -p "$(dirname "$cfg")"
echo "INFO[0000] Loading config from $cfg"
if [ -f "$cfg" ] && grep -q '"nvidia"' "$cfg"; then echo "INFO[0000] Wrote updated config to $cfg"; exit 0; fi
orig=""
[ ! -f "$cfg" ] || orig=$(tr -d '\n' <"$cfg")
{
  printf '{\n    "runtimes": {\n        "nvidia": {\n            "args": [],\n            "path": "nvidia-container-runtime"\n        }\n    }'
  [ -z "$orig" ] || printf ',\n    "fixture-kept": %s' "$orig"
  printf '\n}\n'
} >"$cfg"
echo "INFO[0000] Wrote updated config to $cfg"
echo "INFO[0000] It is recommended that docker daemon be restarted."
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
      # Asked only about keys the file holds as asked. With
      # core-without-ai-features, the checkout's core is one from before
      # that setting: the real snippet names a key of PROVISION_ENV_NEWER
      # its Settings lacks instead of checking it.
      while IFS= read -r kv; do
        [ -n "$kv" ] || continue
        k=${kv%%=*}
        if [ "$k" = AI_FEATURES ] && [ -f "$SHIM_STATE/core-without-ai-features" ] && [[ " ${PROVISION_ENV_NEWER-} " == *" $k "* ]]; then
          echo "not-in-this-core $k"; continue
        fi
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
  echo C95B321B61E88C1809C4F759DDCAE044F796ECB0 >"$STATE/nvidia-fpr"
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
# options every case needs. Its stdin is /dev/null, so it never asks the AI
# question (not even when the harness runs in a terminal); PROV_TTY=1 has
# it take stdin for a terminal, and PROV_STDIN names a file to read it from.
run_prov() {
  RC=0
  : >"$STATE/calls.log"
  env PATH="$CASE/bin:$PATH" DOMOVOI_PROVISION_ROOT="$ROOT" DOMOVOI_PROVISION_TEST_HARNESS=1 TMPDIR="$CASE/tmp" \
    ${PROV_TTY:+DOMOVOI_PROVISION_TEST_TTY=1} \
    bash "$SCRIPT" --repo-url "$ORIGIN" --checkout-dir "$CHECKOUT" "$@" <"${PROV_STDIN:-/dev/null}" >"$CASE/output.log" 2>&1 || RC=$?
  grep '^@@DOMOVOI ' "$CASE/output.log" | sed 's/^@@DOMOVOI //' >"$CASE/progress.jsonl" || true
}
# No --timezone: on WSL the zone follows Windows (owner decision
# 2026-10-11), and the option is ignored there with a warning.
wsl_args=(--internet always)

# gpu_wsl: Windows hands this distro an NVIDIA GPU (/dev/dxg and the
# driver's libcuda.so.1, as on the dev box's RTX cards).
gpu_wsl() {
  mkdir -p "$ROOT/dev" "$ROOT/usr/lib/wsl/lib"
  : >"$ROOT/dev/dxg"
  : >"$ROOT/usr/lib/wsl/lib/libcuda.so.1"
}
# gpu_bare NAME: the NVIDIA driver's nvidia-smi lists one GPU.
gpu_bare() { printf 'GPU 0: %s (UUID: GPU-5e1a0c1e-1111-4111-9111-111111111111)\n' "$1" >"$STATE/nvidia-smi-gpus"; }
# The four packages at the manifest's pins, as apt is asked for them.
NVIDIA_PKGS="nvidia-container-toolkit=1.20.1-1 nvidia-container-toolkit-base=1.20.1-1 libnvidia-container-tools=1.20.1-1 libnvidia-container1=1.20.1-1"

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
    grep -qv '^{"v":1,"step":"[a-z-]*","index":[0-9]*,"total":22,"state":"[a-z]*","percent":[0-9]*,"message":"' "$CASE/progress.jsonl" \
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
    assert o["v"] == 1 and o["total"] == 22, l
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
# dropped on 2026-10-12; mutagen (GPL), python-mpd2 and lameenc (LGPL) on
# 2026-10-15 (domovoi/media_tags reads the tags, domovoi/clients/mpd_protocol.py
# speaks to MPD, the system ffmpeg encodes the clips), and librosa and PyAV
# with them (faster-whisper and resemblyzer installed --no-deps), so a fresh
# venv without them must pass it.
case_import_check_names_no_dropped_package() {
  new_case import_check_names_no_dropped_package
  local mods
  mods=$(sed -n "/^PY_IMPORT_CHECK='#provision:import-check/,/^bad = \[\]/p" "$SCRIPT")
  check "found the import check" grep -qF '"onnxruntime"' <<<"$mods"
  check "no edge_tts" eq "$(grep -c 'edge_tts' <<<"$mods")" 0
  check "no miniaudio" eq "$(grep -c 'miniaudio' <<<"$mods")" 0
  check "no mutagen" eq "$(grep -c 'mutagen' <<<"$mods")" 0
  check "no librosa" eq "$(grep -c 'librosa' <<<"$mods")" 0
  check "resemblyzer is not imported (its package imports librosa)" eq "$(grep -c '"resemblyzer"' <<<"$mods")" 0
  check "faster-whisper only through Domovoi's import (no PyAV)" eq "$(grep -c '"faster_whisper"' <<<"$mods")" 0
  check "no python-mpd2" eq "$(grep -c '"mpd"' <<<"$mods")" 0
  check "no lameenc" eq "$(grep -c 'lameenc' <<<"$mods")" 0
  end_case
}

# faster-whisper's package imports PyAV (av) at load time, and a fresh venv
# has none since 2026-10-15 (installed --no-deps): the Whisper download
# must import it through domovoi.clients.whisper.import_faster_whisper,
# which stands in for av, before anything from faster_whisper. The shim
# below answers the snippet by its tag and never runs it, so this reads
# the snippet itself; a real install at 4d6b414 stopped at the whisper step
# with "No module named 'av'".
case_whisper_download_imports_without_pyav() {
  new_case whisper_download_imports_without_pyav
  local py first_fw helper
  py=$(sed -n "/^PY_WHISPER='#provision:whisper-download/,/'\$/p" "$SCRIPT")
  check "found the snippet" grep -qF 'download_model(' <<<"$py"
  helper=$(grep -n '^import_faster_whisper()$' <<<"$py" | head -n 1 | cut -d: -f1)
  first_fw=$(grep -nE '^(from|import) faster_whisper' <<<"$py" | head -n 1 | cut -d: -f1)
  check "it calls import_faster_whisper()" test -n "$helper"
  check "before its first faster_whisper import" test "${helper:-99}" -lt "${first_fw:-0}"
  check "no PyAV import of its own" eq "$(grep -cE '^(from|import) av' <<<"$py")" 0
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
  check "plan lines for every step but the preflight" eq "$(grep -c '"state":"plan"' "$CASE/progress.jsonl")" 20
  check "the GPU step: a skip, asked of the helper beside the script" \
    grep -q '"step":"gpu-runtime".*"state":"plan".*"message":"skip: no NVIDIA GPU visible (WSL: no /dev/dxg' "$CASE/progress.jsonl"
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
  # The fixture origin is a repository before its first release: no stable
  # branch, so the install follows main and says so.
  check "two warnings" eq "$(final_field warnings)" 2
  check "one: signed updates not enforced" said "signed updates are not enforced"
  check "the other: no release on stable yet" said "has no stable branch yet (no release has been published)"
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
  # No NVIDIA GPU in this distro: the GPU step is skipped, and nothing of
  # NVIDIA's is fetched, installed or configured.
  check "gpu-runtime skipped: no GPU" eq "$(state_of gpu-runtime)" skip
  check "says why" said "[gpu-runtime] skipped: no NVIDIA GPU visible (WSL: no /dev/dxg"
  check "no NVIDIA key fetched" not_called "nvidia.github.io"
  check "no toolkit" not_called "nvidia-container-toolkit"
  check "Docker not configured for it" not_called "nvidia-ctk"
  check "Docker not restarted" not_called "systemctl restart docker"
  # venv: the three LINUX_HOST.md installs, in order
  check "build tools from the lock" called "-m pip --disable-pip-version-check --no-input install --require-hashes -r $ROOT/var/cache/domovoi-provision/build-tools."
  check "not the -c form pip 25.1 refuses" not_called "-c requirements-linux-py314.lock setuptools wheel"
  check "no install option before the subcommand" not_called "--progress-bar off install"
  check "pip's progress bar off through the environment" called "PIP_PROGRESS_BAR=off"
  check "every pin, hash-checked" called "install --require-hashes --no-build-isolation -r requirements-linux-py314.lock"
  check "faster-whisper and resemblyzer hash-checked, without their deps" \
    called "install --require-hashes --no-deps -r requirements-linux-py314-no-deps.lock"
  check "after the lock" before "--no-build-isolation -r requirements-linux-py314.lock" "--no-deps -r requirements-linux-py314-no-deps.lock"
  check "domovoi itself, no deps" called "install --no-deps --no-build-isolation -e ."
  check "domovoi last" before "--no-deps -r requirements-linux-py314-no-deps.lock" "--no-deps --no-build-isolation -e ."
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

# The music player image (2026-10-21): a repair by Setup re-ran the images
# step (its inputs include the compose file, which updates change), rebuilt
# domovoi-mpd with a new image ID, and the core's container guard reported
# it as replaced outside Domovoi. A re-run now keeps the image, builds only
# when it is missing or when Dockerfile.mpd changed since this script built
# it, and records every build for the guard.
mpd_img() { cat "$STATE/images/domovoi-mpd_latest"; }
mpd_record() { cat "$ROOT/var/lib/domovoi-provision/mpd-image.json"; }
record_field() { sed -n "s/^  \"$1\": \"\([^\"]*\)\",\{0,1\}\$/\1/p" "$ROOT/var/lib/domovoi-provision/mpd-image.json"; }
json_ok() { [ -z "$JSON_PY" ] || "$JSON_PY" -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$1"; }
# A room's player, running IMAGE, and the core up (a repair of a live box).
live_room() { mkdir -p "$STATE/containers"; printf '%s\n' "$2" >"$STATE/containers/$1"; touch "$STATE/active/domovoi-core.service"; }
# The images step's inputs as they were before an update moved the checkout.
stale_images_marker() { echo stale >"$ROOT/var/lib/domovoi-provision/steps/images"; }

case_first_build_of_the_music_player_is_recorded() {
  new_case first_build_of_the_music_player_is_recorded
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "built once" eq "$(grep -c '^docker build' "$STATE/calls.log")" 1
  local rec=$ROOT/var/lib/domovoi-provision/mpd-image.json
  check "recorded" test -f "$rec"
  check "0644" eq "$(stat -c %a "$rec")" 644
  check "by the installer" has_line "$rec" '  "by": "provision",'
  check "names the image the tag names" eq "$(record_field image_id)" "$(mpd_img)"
  check "the tag" has_line "$rec" '  "tag": "domovoi-mpd:latest",'
  check "Dockerfile.mpd's hash" eq "$(record_field dockerfile_sha256)" "$(sha256sum "$CHECKOUT/domovoi/Dockerfile.mpd" | cut -d' ' -f1)"
  check "when, in UTC" grep -qE '^  "built_at": "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"$' "$rec"
  check "valid JSON" json_ok "$rec"
  check "no temp file left" absent "$rec.tmp"
  check "no core to restart on a first install" not_called "systemctl restart domovoi-core"
  end_case
}

case_repair_rerun_keeps_the_music_player_image() {
  new_case repair_rerun_keeps_the_music_player_image
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local img rec_before; img=$(mpd_img); rec_before=$(mpd_record)
  live_room domovoi-mpd-kitchen "$img"
  stale_images_marker
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "the images step ran" eq "$(state_of images)" done
  check "the database images pulled" called "docker compose pull postgres flyway"
  check "no build" not_called "docker build"
  check "said why" said "keeping domovoi-mpd:latest ($img)"
  check "the same image" eq "$(mpd_img)" "$img"
  check "the record as it was" eq "$(mpd_record)" "$rec_before"
  check "the room's player left alone" test -f "$STATE/containers/domovoi-mpd-kitchen"
  check "no container removed" not_called "docker rm"
  check "the core not restarted" not_called "systemctl restart domovoi-core"
  end_case
}

case_repair_rerun_keeps_an_image_built_elsewhere_unvouched() {
  new_case repair_rerun_keeps_an_image_built_elsewhere_unvouched
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local rec_before; rec_before=$(mpd_record)
  # A docker build by hand (or the core, or the update unit): another ID.
  local other="sha256:$(printf manual | sha256sum | cut -c1-64)"
  echo "$other" >"$STATE/images/domovoi-mpd_latest"
  # ...and the Dockerfile the record names is no longer the checkout's.
  sed -i 's/^  "dockerfile_sha256": "[^"]*"/  "dockerfile_sha256": "older"/' "$ROOT/var/lib/domovoi-provision/mpd-image.json"
  rec_before=$(mpd_record)
  stale_images_marker
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "no build" not_called "docker build"
  check "the image left as it is" eq "$(mpd_img)" "$other"
  # The record still names the installer's own build, so it vouches for
  # nothing the tag names now: the guard reports the hand-made image.
  check "the record not rewritten" eq "$(mpd_record)" "$rec_before"
  check "it doesn't name the image the tag names" test "$(record_field image_id)" != "$other"
  end_case
}

case_changed_dockerfile_rebuilds_records_and_moves_the_rooms() {
  new_case changed_dockerfile_rebuilds_records_and_moves_the_rooms
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local old; old=$(mpd_img)
  live_room domovoi-mpd-kitchen "$old"
  live_room domovoi-mpd-den "$old"
  live_room domovoi-searxng-lookalike "$old"
  # This script built the image the tag names, from another Dockerfile.mpd.
  sed -i 's/^  "dockerfile_sha256": "[^"]*"/  "dockerfile_sha256": "older"/' "$ROOT/var/lib/domovoi-provision/mpd-image.json"
  stale_images_marker
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "said why" said "building domovoi-mpd:latest: Dockerfile.mpd changed since this script built domovoi-mpd:latest"
  check "built" called "docker build --progress=plain -t domovoi-mpd:latest"
  local new; new=$(mpd_img)
  check "a new image ID" test "$new" != "$old"
  check "recorded: the new image" eq "$(record_field image_id)" "$new"
  check "recorded: this Dockerfile" eq "$(record_field dockerfile_sha256)" "$(sha256sum "$CHECKOUT/domovoi/Dockerfile.mpd" | cut -d' ' -f1)"
  check "the rooms on the old image removed" eq "$(ls "$STATE/containers")" domovoi-searxng-lookalike
  check "only Domovoi's music players" called "docker rm -f domovoi-mpd-kitchen"
  check "after the build" before "docker build" "docker rm -f domovoi-mpd-den"
  check "the core restarted to recreate them" called "systemctl restart domovoi-core.service --no-block"
  check "after the rooms went" before "docker rm -f domovoi-mpd-kitchen" "systemctl restart domovoi-core.service"
  check "said so" said "restarting domovoi-core: it recreates the rooms' players on the new image"
  end_case
}

case_missing_music_player_image_is_built_on_a_repair() {
  new_case missing_music_player_image_is_built_on_a_repair
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local old; old=$(mpd_img)
  live_room domovoi-mpd-kitchen "$old"
  rm -f "$STATE/images/domovoi-mpd_latest"
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "the images step re-ran (its check)" eq "$(state_of images)" done
  check "built" called "docker build --progress=plain -t domovoi-mpd:latest"
  check "recorded" eq "$(record_field image_id)" "$(mpd_img)"
  check "the room removed" absent "$STATE/containers/domovoi-mpd-kitchen"
  check "the core restarted" called "systemctl restart domovoi-core.service --no-block"
  end_case
}

case_a_core_still_starting_is_restarted_after_a_rebuild() {
  new_case a_core_still_starting_is_restarted_after_a_rebuild
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  live_room domovoi-mpd-kitchen "$(mpd_img)"
  # The core is starting, not active yet (review round 1).
  rm -f "$STATE/active/domovoi-core.service"
  mkdir -p "$STATE/activating"; touch "$STATE/activating/domovoi-core.service"
  rm -f "$STATE/images/domovoi-mpd_latest"
  run_prov "${wsl_args[@]}"
  check "built" called "docker build --progress=plain -t domovoi-mpd:latest"
  check "the starting core restarted" called "systemctl restart domovoi-core.service --no-block"
  end_case
}

case_a_stopped_core_is_left_stopped_after_a_rebuild() {
  new_case a_stopped_core_is_left_stopped_after_a_rebuild
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  live_room domovoi-mpd-kitchen "$(mpd_img)"
  rm -f "$STATE/active/domovoi-core.service"
  rm -f "$STATE/images/domovoi-mpd_latest"
  run_prov "${wsl_args[@]}"
  check "built" called "docker build --progress=plain -t domovoi-mpd:latest"
  check "not restarted by the images step" not_said "restarting domovoi-core: it recreates"
  check "the room removed (the core recreates it when it starts)" absent "$STATE/containers/domovoi-mpd-kitchen"
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
  check "nothing unpacked" not_called "tar --zstd"
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

case_lock_without_the_no_deps_lock_stops() {
  new_case lock_without_the_no_deps_lock_stops
  run_prov "${wsl_args[@]}" --ref no-nodeps-lock --skip-models
  check "exit 1" eq "$RC" 1
  check "venv failed" eq "$(state_of venv)" fail
  check "says why" said "the checkout has no requirements-linux-py314-no-deps.lock"
  check "the main lock went in first" called "install --require-hashes --no-build-isolation -r requirements-linux-py314.lock"
  check "domovoi itself never did" not_called "install --no-deps --no-build-isolation -e ."
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
  # With signed updates not enforced, and no stable branch yet (the fixture
  # origin has had no release).
  check "counted" eq "$(final_field warnings)" 3
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
  # A commit: root reads the installer from the verified copy of HEAD, so
  # an edit in the working tree alone changes nothing.
  echo "# changed" >>"$CHECKOUT/scripts/linux/install-update-unit.sh"
  git -C "$CHECKOUT" commit -q -am "installer changed"
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
  git -C "$CHECKOUT" commit -q -am "installer changed again"
  run_prov --internet always --skip-models
  check "symlink: exit 0" eq "$RC" 0
  check "symlink: still a link" test -L "$f"
  check "symlink: the file it points to untouched" eq "$(cat "$CASE/managed/domovoi-update")" "DOMOVOI_UPDATE_KEEP_BACKUPS=3"
  check "symlink: warned" said "/etc/default/domovoi-update is a symlink, left as it is"
  check "symlink: and no channel pin, said" said "no channel pin (DOMOVOI_UPSTREAM_BRANCH=\"stable main\") written through it"
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
  # Nothing else: a household's own size limits stay (review 2026-10-15).
  check "and no size limit of ours" bash -c '! grep -q "^SystemMaxUse" "$1"' _ "$f"
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

# The core is Type=notify: a blocking start would wait for its READY=1
# with no progress line and past --health-timeout (review 2026-10-15).
case_starts_do_not_block_on_ready() {
  new_case starts_do_not_block_on_ready
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "the start is --no-block" called "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service --no-block"
  check "and the wait reports" said "answered after"
  end_case
}

case_a_unit_that_fails_after_a_no_block_start_stops_the_wait() {
  new_case a_unit_that_fails_after_a_no_block_start_stops_the_wait
  echo domovoi-core.service >"$STATE/start-then-fail"
  local t0=$SECONDS
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "at once, not after --health-timeout" bash -c '[ "$1" -lt 60 ]' _ "$((SECONDS - t0))"
  check "names the unit" said "domovoi-core.service failed"
  check "with the diagnostics" said "systemctl status"
  end_case
}

case_the_database_failing_after_a_no_block_start_stops_the_wait() {
  new_case the_database_failing_after_a_no_block_start_stops_the_wait
  echo domovoi-db.service >"$STATE/start-then-fail"
  local t0=$SECONDS
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "at once, not after --health-timeout" bash -c '[ "$1" -lt 60 ]' _ "$((SECONDS - t0))"
  check "names the database" said "the core (/v1/health) won't answer: domovoi-db.service failed"
  end_case
}

# 2026-10-17: a first start of a new database outran Postgres's health
# check and the log said only "container domovoi-postgres is unhealthy".
# Now the failure says why: the container's state, its last health check
# and its last 100 log lines (passwords blanked) in the output, and the
# reason in the failure message Setup shows.
case_a_failed_database_says_why() {
  new_case a_failed_database_says_why
  echo domovoi-db.service >"$STATE/start-then-fail"
  echo 'running|unhealthy|0|1' >"$STATE/pg-state"
  printf '%s\n' "" "127.0.0.1:5432 - no response" >"$STATE/pg-health"
  { for i in $(seq 1 150); do echo "old line $i"; done
    echo "2026-10-17 05:14:20.100 UTC [61] LOG:  statement: ALTER ROLE domovoi PASSWORD 'hunter2-Secret'"
    echo "2026-10-17 05:14:21.200 UTC [62] FATAL:  could not write to file \"pg_wal/xlogtemp.62\": No space left on device"
    echo "running bootstrap script ... ok"; } >"$STATE/pg-logs"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "the container's state" said "Postgres container domovoi-postgres: running, health unhealthy, exit code 0, restarts 1"
  check "its last health check" said "its last health check: 127.0.0.1:5432 - no response"
  check "100 log lines asked for" called "docker logs --tail 100 domovoi-postgres"
  check "the last of them printed" said "running bootstrap script ... ok"
  check "but no more than 100" not_said "old line 53"
  check "line 54 is the first" said "old line 54"
  check "no password" not_said "hunter2-Secret"
  check "the statement without it" said "ALTER ROLE domovoi PASSWORD [hidden]"
  check "the failure message says why" said "the core (/v1/health) won't answer: domovoi-db.service failed (Postgres is unhealthy, restarted 1 time; its log: FATAL:  could not write to file"
  check "and so does the final line" bash -c 'grep "\"final\":true" "$1" | grep -q "Postgres is unhealthy"' _ "$CASE/output.log"
  end_case
}

# No container at all (compose up failed before making one): said so.
case_a_failed_database_without_a_container_says_so() {
  new_case a_failed_database_without_a_container_says_so
  echo domovoi-db.service >"$STATE/start-then-fail"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "the message says so" said "domovoi-db.service failed (there is no Postgres container (domovoi-postgres))"
  check "no docker logs without one" not_called "docker logs"
  end_case
}

# A database still making its first start when the core's wait runs out:
# the progress and the failure say the database is what it waited for.
case_a_database_still_starting_is_named() {
  new_case a_database_still_starting_is_named
  echo domovoi-db.service >"$STATE/start-activating"
  echo 'running|starting|0|0' >"$STATE/pg-state"
  printf '%s\n' "127.0.0.1:5432 - no response" >"$STATE/pg-health"
  echo "performing post-bootstrap initialization ... ok" >"$STATE/pg-logs"
  run_prov "${wsl_args[@]}" --skip-models --health-timeout 35
  check "exit 1" eq "$RC" 1
  check "the progress names the database" said '"message":"Waiting for the database to start (3'
  check "its log line too" said "domovoi-db.service is still starting (Postgres is still starting (last check: 127.0.0.1:5432 - no response))"
  check "the container's log" said "performing post-bootstrap initialization ... ok"
  check "the failure names it" said "the core (/v1/health) did not answer within 35s (http://127.0.0.1:6370/v1/health): domovoi-db.service is activating (Postgres is still starting"
  end_case
}

# A volume whose first start was cut off in initdb (a reboot during a slow
# first start): Postgres runs without the domovoi database or the network
# login line, so every Retry failed. The start step removes it, container
# first, before starting domovoi-db, which then makes it again.
case_a_half_made_database_volume_is_made_again() {
  new_case a_half_made_database_volume_is_made_again
  echo 'running|unhealthy|0|1' >"$STATE/pg-state"
  printf '%s\n' "performing post-bootstrap initialization ... ok"     "PostgreSQL Database directory appears to contain a database; Skipping initialization" >"$STATE/pg-logs"
  printf '%s\n' 1 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "says what it found" said "domovoi-pgdata is half made"
  check "the container removed" called "docker rm -f domovoi-postgres"
  check "the volume removed" called "docker volume rm domovoi-pgdata"
  check "after the container" before "docker rm -f domovoi-postgres" "docker volume rm domovoi-pgdata"
  check "before the database starts" before "docker volume rm domovoi-pgdata" "systemctl start domovoi-db.service"
  check "read with the container's image, no network, read-only" called "docker run --rm --network none --entrypoint sh -v domovoi-pgdata:/pgdata:ro sha256:0000pgimage"
  end_case
}

# Every other volume is left alone: one with a database in it (whatever
# its container says), one still in its first start, none at all.
case_a_database_volume_with_data_is_left_alone() {
  new_case a_database_volume_with_data_is_left_alone
  echo 'exited|unhealthy|1|3' >"$STATE/pg-state"
  printf '%s\n' "Skipping initialization" >"$STATE/pg-logs"
  printf '%s\n' 1 16384 16385 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "says so" said "domovoi-pgdata holds 2 database(s) besides initdb's own: left alone"
  check "no container removed" not_called "docker rm"
  check "no volume removed" not_called "docker volume rm"
  end_case
}

# Review 2026-10-17: `docker logs | grep -q` under pipefail died of SIGPIPE
# once the log passed ~64 KB (a crash-looping half-made Postgres writes
# that in about 25 minutes), and the volume was left alone again.
case_a_half_made_volume_with_a_long_log_is_made_again() {
  new_case a_half_made_volume_with_a_long_log_is_made_again
  echo 'running|unhealthy|0|9' >"$STATE/pg-state"
  { echo "performing post-bootstrap initialization ... ok"
    echo "PostgreSQL Database directory appears to contain a database; Skipping initialization"
    for i in $(seq 1 4000); do
      echo "2026-10-17 07:00:00.000 UTC [$i] FATAL:  no pg_hba.conf entry for host \"172.19.0.3\", user \"domovoi\", database \"domovoi\", no encryption"
    done; } >"$STATE/pg-logs"
  check "the log is long" bash -c '[ "$(wc -c <"$1")" -gt 200000 ]' _ "$STATE/pg-logs"
  printf '%s\n' 1 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "found it" said "domovoi-pgdata is half made"
  check "removed it" called "docker volume rm domovoi-pgdata"
  end_case
}

# A database can live in a tablespace of its own, outside base/: a volume
# with any tablespace is left alone, whatever base/ holds.
case_a_database_volume_with_a_tablespace_is_left_alone() {
  new_case a_database_volume_with_a_tablespace_is_left_alone
  echo 'exited||1|0' >"$STATE/pg-state"
  printf '%s\n' 1 4 5 "tablespaces 1" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "says so" said "domovoi-pgdata has 1 tablespace(s): left alone"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

# Review note: a paused container is left alone, half made or not.
case_a_paused_database_is_left_alone() {
  new_case a_paused_database_is_left_alone
  echo 'paused|unhealthy|0|1' >"$STATE/pg-state"
  printf '%s\n' "Skipping initialization" >"$STATE/pg-logs"
  printf '%s\n' 1 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "says so" said "domovoi-postgres is paused: left alone"
  check "nothing read" not_called "docker run --rm"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

# A database name too long for bash's integers still counts as a database.
case_a_database_oid_past_bash_integers_counts() {
  new_case a_database_oid_past_bash_integers_counts
  echo 'exited||1|0' >"$STATE/pg-state"
  printf '%s\n' 1 4 5 99999999999999999999 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "says so" said "domovoi-pgdata holds 1 database(s) besides initdb's own: left alone"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

# Review note: with domovoi-db running, the start step doesn't look at the
# volume at all.
case_a_running_database_is_not_checked() {
  new_case a_running_database_is_not_checked
  mkdir -p "$STATE/active"
  touch "$STATE/active/domovoi-db.service"
  printf '%s\n' 1 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "the start step ran" called "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service --no-block"
  check "no volume check" not_called "docker volume inspect"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

# Review 2026-10-17: every password form the reviewer's real Postgres
# logged, in any case and across a line break, and the database's own
# password as it is (from the container's environment; the .env path is
# the apply-update case's), in the report and in the reason Setup shows.
case_a_failed_database_report_blanks_every_password() {
  new_case a_failed_database_report_blanks_every_password
  echo domovoi-db.service >"$STATE/start-then-fail"
  echo 'running|unhealthy|0|0' >"$STATE/pg-state"
  printf '%s\n' "PATH=/usr/bin" "POSTGRES_PASSWORD=Pg-Env-S3cret" "POSTGRES_USER=domovoi" >"$STATE/pg-env"
  cat >"$STATE/pg-logs" <<'LOG'
STATEMENT:  ALTER ROLE nosuch PASSWORD 'Leak1-plain'
STATEMENT:  ALTER ROLE nosuch WITH ENCRYPTED PASSWORD 'Leak2-it''s-quoted'
STATEMENT:  alter role nosuch Password 'Leak3-MixedCase'
STATEMENT:  ALTER ROLE nosuch PASSWORD $$Leak4-dollar$$
STATEMENT:  ALTER ROLE nosuch PASSWORD E'Leak5-escape'
STATEMENT:  ALTER ROLE nosuch PASSWORD $tag$Leak6-tagged$tag$
STATEMENT:  SELECT dblink_connect('host=x user=y password=Leak7-conninfo')
STATEMENT:  SELECT 'postgresql://domovoi:Leak8-url@db/x'::int
STATEMENT:  ALTER ROLE nosuch PASSWORD
	'Leak9-newline'
STATEMENT:  ALTER ROLE nosuch PASSWORD 'Leak10-never-closed
LOG:  password authentication failed for user "domovoi"
ERROR:  invalid input syntax for type integer: "Pg-Env-S3cret" at character 8
LOG
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 1" eq "$RC" 1
  check "the report is there" said "the last 100 lines of docker logs domovoi-postgres"
  check "no LeakN anywhere" bash -c '! grep -qE "Leak[0-9]" "$1"' _ "$CASE/output.log"
  check "nor the database's password" not_said "Pg-Env-S3cret"
  check "mixed case kept, value blanked" said "alter role nosuch Password [hidden]"
  check "across the line break" said "ALTER ROLE nosuch PASSWORD [hidden]"
  check "a failed login still reads as one" said "password authentication failed for user"
  check "the reason Setup shows, blanked" said 'domovoi-db.service failed (Postgres is unhealthy; its log: ERROR:  invalid input syntax for type integer: "[hidden]"'
  end_case
}

case_a_first_start_still_running_is_left_alone() {
  new_case a_first_start_still_running_is_left_alone
  echo 'running|starting|0|0' >"$STATE/pg-state"
  printf '%s\n' "running bootstrap script ... ok" >"$STATE/pg-logs"
  printf '%s\n' 1 4 5 "tablespaces 0" >"$STATE/pg-volume"
  run_prov "${wsl_args[@]}" --skip-models
  check "says so" said "domovoi-postgres is running and has not restarted since its first start: left alone"
  check "nothing read" not_called "docker run --rm"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

case_no_database_volume_yet_is_left_alone() {
  new_case no_database_volume_yet_is_left_alone
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "says so" said "no volume domovoi-pgdata: a first start makes it"
  check "nothing removed" not_called "docker volume rm"
  end_case
}

# A core that failed before this run (the last provision's, say) keeps its
# failed mark while its --no-block start waits for the database's: the
# wait must not take that for this start failing. The mark is cleared
# first, and only a unit that has one.
case_a_failed_mark_from_before_is_not_this_starts() {
  new_case a_failed_mark_from_before_is_not_this_starts
  mkdir -p "$STATE/failed"
  touch "$STATE/failed/domovoi-core.service"
  echo domovoi-core.service >"$STATE/start-queued"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "the old mark cleared" called "systemctl reset-failed domovoi-core.service"
  check "before the start" before "systemctl reset-failed domovoi-core.service" \
    "systemctl start domovoi-db.service domovoi-core.service domovoi-web.service --no-block"
  check "only that unit's" not_called "systemctl reset-failed domovoi-web.service"
  check "not taken for a failure" not_said "domovoi-core.service failed"
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
  echo alex >>"$STATE/users"; echo "alex sudo" >>"$STATE/groups"; mkdir -p "$ROOT/home/alex"
  run_prov --internet always --user alex --skip-models
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
# The apt step brings xorriso too (2026-10-16): the program the core runs
# to make old computers' room sticks, an Ubuntu package like ffmpeg.
case_apt_step_installs_the_stick_tool() {
  new_case apt_step_installs_the_stick_tool
  run_prov "${wsl_args[@]}" --skip-models --stop-before user
  check "exit 0" eq "$RC" 0
  check "the apt step ran" marker apt
  check "xorriso asked for with the rest" grep -qE '^apt-get .* install -y .*\bmpg123\b.*\bxorriso\b' "$STATE/calls.log"
  check "installed" grep -qx "xorriso=1" "$STATE/dpkg"
  check "not held" not_called "apt-mark"
  end_case
}

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
  run_prov --internet sometimes --skip-models --media music=/home/alex/Music
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

# ─── AI chat and questions are optional (owner decision 2026-10-21) ──────
#
# --no-ai-models skips the models step alone: Whisper, the Piper voices and
# Ollama itself are installed as ever (turning AI on later in Settings,
# Models pulls through Ollama's API, without root). Both answers go into
# domovoi/.env as AI_FEATURES, a managed key like the models'.

AI_PULLS=('"model":"llama3.2:3b-instruct-q4_K_M"' '"model":"qwen3:8b-q4_K_M"' '"model":"qwen3-vl:2b-instruct-q4_K_M"')
WHISPER_BIN_REL=home/domovoi/.cache/huggingface/hub/models--Systran--faster-whisper-small.en/snapshots/d1d751a5f8271d482d14ca55d9e2deeebbae577f/model.bin
AI_QUESTION="Include AI chat and questions? It downloads about 6.4 GB of AI models (about 9.1 GB on a box with 24 GB of memory or more)."

case_ai_models_left_out() {
  new_case ai_models_left_out
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written m
  run_prov "${wsl_args[@]}" --no-ai-models
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "result ok" eq "$(final_field result)" '"ok"'
  check "the final line says AI is off" eq "$(final_field ai)" false
  check "and result.json" grep -qF '"ai":false' "$ROOT/var/lib/domovoi-provision/result.json"
  check "models skipped" eq "$(state_of models)" skip
  check "says why" said "[models] skipped: --no-ai-models: AI chat and questions are off; no AI models pulled (turn them on later in Settings, Models)"
  check "no models marker" no_marker models
  check "no pull at all" not_called "/api/pull"
  for m in "${AI_PULLS[@]}"; do check "not pulled: $m" not_called "$m"; done
  check "Ollama still installed" test -x "$ROOT/usr/local/bin/ollama"
  check "its step done" eq "$(state_of ollama)" done
  check "and it runs" test -f "$STATE/active/ollama.service"
  check "Whisper still downloaded" test -f "$ROOT/$WHISPER_BIN_REL"
  check "its step done" eq "$(state_of whisper)" done
  check "the Piper voices still" test -f "$ROOT/home/domovoi/.domovoi/piper_voices/en_US-norman-medium.onnx"
  check ".env: AI_FEATURES=false" has_line "$envf" "AI_FEATURES=false"
  check ".env: once" eq "$(grep -c '^AI_FEATURES=' "$envf")" 1
  check "recorded as the script's" has_line "$rec" "AI_FEATURES=false"
  check "the models still configured" has_line "$envf" "OLLAMA_MODEL=llama3.2:3b-instruct-q4_K_M"
  check "said at the start" said "AI chat and questions: left out (--no-ai-models)"
  check "said at the end" said "AI chat and questions: off (turn them on later on the dashboard: Settings, Models)"
  check "no warning of its own (the fixture's two)" eq "$(final_field warnings)" 2
  check "never asked" not_said "Include AI chat and questions?"
  end_case
}

# No option and no terminal (the Windows installer, CI): yes, as before.
case_ai_models_by_default() {
  new_case ai_models_by_default
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written m
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "models done" eq "$(state_of models)" done
  for m in "${AI_PULLS[@]}"; do check "pulled: $m" called "$m"; done
  check ".env: AI_FEATURES=true" has_line "$envf" "AI_FEATURES=true"
  check "recorded as the script's" has_line "$rec" "AI_FEATURES=true"
  check "the final line says AI is on" eq "$(final_field ai)" true
  check "said so" said "AI chat and questions: included (the default)"
  check "never asked" not_said "Include AI chat and questions?"
  # --ai-models is the same answer: nothing to do.
  run_prov "${wsl_args[@]}" --ai-models
  check "--ai-models exit 0" eq "$RC" 0
  check "env skipped" eq "$(state_of env)" skip
  check "models skipped: already done" eq "$(state_of models)" skip
  check "no pull" not_called "/api/pull"
  check "nothing restarted" not_called "systemctl restart"
  check "said where the answer came from" said "AI chat and questions: included (--ai-models)"
  end_case
}

# Left out first, then a run with no option keeps that (never asked: the
# .env has the answer), and a run with --ai-models brings AI in.
case_ai_models_turned_on_later() {
  new_case ai_models_turned_on_later
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written m
  run_prov "${wsl_args[@]}" --no-ai-models
  check "first run exit 0" eq "$RC" 0
  check "no pull" not_called "/api/pull"
  printf 'y\n' >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov "${wsl_args[@]}"
  check "no option exit 0" eq "$RC" 0
  check "not asked: the .env has the answer" not_said "Include AI chat and questions?"
  check "kept it" said "AI chat and questions: left out (AI_FEATURES=false in domovoi/.env)"
  check "models skipped, saying why" said "[models] skipped: AI_FEATURES=false in domovoi/.env: AI chat and questions are off"
  check "still no pull" not_called "/api/pull"
  check ".env still false" has_line "$envf" "AI_FEATURES=false"
  check "the final line says AI is off" eq "$(final_field ai)" false
  run_prov "${wsl_args[@]}" --ai-models
  check "--ai-models exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "models ran" eq "$(state_of models)" done
  for m in "${AI_PULLS[@]}"; do check "pulled: $m" called "$m"; done
  check "models marker" marker models
  check "env re-ran" eq "$(state_of env)" done
  check ".env: AI_FEATURES=true" has_line "$envf" "AI_FEATURES=true"
  check ".env: once" eq "$(grep -c '^AI_FEATURES=' "$envf")" 1
  check "recorded" has_line "$rec" "AI_FEATURES=true"
  check "no warning: false was the script's" not_said "AI_FEATURES holds a value this script did not write"
  check "core and web restarted for it" called "systemctl restart domovoi-core.service domovoi-web.service"
  check "the final line says AI is on" eq "$(final_field ai)" true
  run_prov "${wsl_args[@]}"
  check "again, no option: exit 0" eq "$RC" 0
  check "models skipped: already done" eq "$(state_of models)" skip
  check "no pull" not_called "/api/pull"
  check "kept on" said "AI chat and questions: included (AI_FEATURES=true in domovoi/.env)"
  end_case
}

# AI_FEATURES is the household's once they change it (Settings, or by
# hand): an option that says otherwise leaves it, with the usual warning,
# and a run with no option follows it.
case_ai_features_household_choice_kept() {
  new_case ai_features_household_choice_kept
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written
  run_prov "${wsl_args[@]}" --skip-models
  check "first run exit 0" eq "$RC" 0
  check ".env: AI_FEATURES=true" has_line "$envf" "AI_FEATURES=true"
  # The household turns AI off (the writer rewrites the line in place).
  sed -i 's/^AI_FEATURES=.*/AI_FEATURES=false/' "$envf"
  # A repair run with a new LAN address (the env step runs only when its
  # inputs change).
  run_prov "${wsl_args[@]}" --skip-models --ai-models --lan-ip 192.168.1.60
  check "--ai-models exit 0" eq "$RC" 0
  check "env re-ran" eq "$(state_of env)" done
  check "the household's answer kept" has_line "$envf" "AI_FEATURES=false"
  check "said so" said "domovoi/.env: AI_FEATURES holds a value this script did not write (set in Settings, or by hand); left as it is (this run would have written true)"
  check "that warning alone (a re-run)" eq "$(final_field warnings)" 1
  check "the record still the script's own" has_line "$rec" "AI_FEATURES=true"
  run_prov "${wsl_args[@]}" --lan-ip 192.168.1.60
  check "no option exit 0" eq "$RC" 0
  check "follows the household" said "AI chat and questions: left out (AI_FEATURES=false in domovoi/.env)"
  check "models skipped" eq "$(state_of models)" skip
  check "no pull" not_called "/api/pull"
  check "Whisper downloaded all the same" test -f "$ROOT/$WHISPER_BIN_REL"
  check ".env unchanged" has_line "$envf" "AI_FEATURES=false"
  check "no warning now: the run agrees" not_said "AI_FEATURES holds a value"
  check "the final line says AI is off" eq "$(final_field ai)" false
  end_case
}

case_ai_models_both_options_is_a_usage_error() {
  new_case ai_models_both_options_is_a_usage_error
  run_prov "${wsl_args[@]}" --ai-models --no-ai-models
  check "exit 2" eq "$RC" 2
  check "says why" said "--ai-models and --no-ai-models: give one of them, not both"
  check "result usage_error" eq "$(final_field result)" '"usage_error"'
  check "AI unsettled in the final line" eq "$(final_field ai)" null
  check "one final line" progress_ok
  run_prov "${wsl_args[@]}" --no-ai-models --skip-models --ai-models
  check "the other way round: exit 2" eq "$RC" 2
  run_prov "${wsl_args[@]}" --no-ai-models=yes
  check "--no-ai-models takes no value: 2" eq "$RC" 2
  check "nothing ran" not_called "apt-get"
  run_prov --help
  check "--help names both" said "--no-ai-models        leave AI chat and questions out"
  check "--help gives the sizes" said "about 6.4 GB (about 9.1 GB on"
  check "--help: Ollama stays" said "Ollama itself is installed"
  end_case
}

# The one question, asked only at a terminal with neither option (a
# hermetic run has no terminal: DOMOVOI_PROVISION_TEST_TTY=1 stands in).
case_ai_question_at_a_terminal() {
  new_case ai_question_at_a_terminal
  local envf=$CHECKOUT/domovoi/.env
  printf 'n\n' >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov --dry-run "${wsl_args[@]}"
  check "a dry run never asks" not_said "Include AI chat and questions?"
  check "and takes yes" eq "$(final_field ai)" true
  printf 'maybe\nN\n' >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "asked, with the sizes" said "$AI_QUESTION"
  check "says it all works either way" said "Voice commands, music, timers and everything else work either way. [Y/n]"
  check "asked again after an answer it can't read" eq "$(grep -cF "$AI_QUESTION" "$CASE/output.log")" 2
  check "and said how to answer" said "Please answer y (yes) or n (no)."
  check "the question is not a progress line" eq "$(grep -c 'Include AI' "$CASE/progress.jsonl")" 0
  check "N: left out" said "AI chat and questions: left out (answered no)"
  check "models skipped, saying why" said "[models] skipped: answered no: AI chat and questions are off"
  check "no pull" not_called "/api/pull"
  check ".env: AI_FEATURES=false" has_line "$envf" "AI_FEATURES=false"
  printf 'y\n' >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov "${wsl_args[@]}"
  check "re-run exit 0" eq "$RC" 0
  check "not asked again" not_said "Include AI chat and questions?"
  check "still off" has_line "$envf" "AI_FEATURES=false"
  # A fresh .env (no answer in it yet): Enter is yes, and so is the end of
  # the input.
  rm -f "$envf"
  printf '\n' >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov "${wsl_args[@]}" --skip-models
  check "Enter: exit 0" eq "$RC" 0
  check "Enter: asked once" eq "$(grep -cF "$AI_QUESTION" "$CASE/output.log")" 1
  check "Enter: yes" said "AI chat and questions: included (answered yes)"
  check "Enter: AI_FEATURES=true" has_line "$envf" "AI_FEATURES=true"
  rm -f "$envf"
  : >"$CASE/answers"
  PROV_TTY=1 PROV_STDIN=$CASE/answers run_prov "${wsl_args[@]}" --skip-models
  check "no input: exit 0" eq "$RC" 0
  check "no input: yes" said "AI chat and questions: included (answered yes)"
  check "no input: AI_FEATURES=true" has_line "$envf" "AI_FEATURES=true"
  end_case
}

case_ai_models_dry_run_plans_both_answers() {
  new_case ai_models_dry_run_plans_both_answers
  local sig; sig=$(tree_sig)
  run_prov --dry-run "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "says what either answer does" said "dry run, either answer: --ai-models pulls the tier's AI models and writes AI_FEATURES=true; --no-ai-models skips the models step and writes AI_FEATURES=false"
  check "plans the pulls" said "ollama pull llama3.2:3b-instruct-q4_K_M"
  check "and what No would pull" said "(--no-ai-models: none of these is pulled; Ollama itself is still installed)"
  check "plans AI_FEATURES=true" said "AI_FEATURES=true"
  check "and the other value" said "(AI_FEATURES=false instead with --no-ai-models)"
  check "the final line: on" eq "$(final_field ai)" true
  run_prov --dry-run "${wsl_args[@]}" --no-ai-models
  check "--no-ai-models exit 0" eq "$RC" 0
  check "the models step a skip, saying why" \
    grep -q '"step":"models".*"state":"plan".*"message":"skip: --no-ai-models: AI chat and questions are off' "$CASE/progress.jsonl"
  check "no pull planned" not_said "ollama pull llama3.2:3b-instruct-q4_K_M"
  check "Ollama still planned" grep -q '"step":"ollama".*"state":"plan".*"would":"run"' "$CASE/progress.jsonl"
  check "plans AI_FEATURES=false" said "AI_FEATURES=false"
  check "and the other value" said "(AI_FEATURES=true instead with --ai-models)"
  check "the final line: off" eq "$(final_field ai)" false
  check "every file as it was" eq "$(tree_sig)" "$sig"
  end_case
}

# A checkout whose core predates the setting (an older release, --ref):
# its Settings ignore AI_FEATURES, so the verify step names the key rather
# than failing, and a No says what that means.
case_ai_features_on_an_older_core() {
  new_case ai_features_on_an_older_core
  : >"$STATE/core-without-ai-features"
  run_prov "${wsl_args[@]}" --skip-models --no-ai-models
  check "exit 0" eq "$RC" 0
  check ".env: AI_FEATURES=false all the same" has_line "$CHECKOUT/domovoi/.env" "AI_FEATURES=false"
  check "warns what that means" said "AI_FEATURES=false is written, but the core of this checkout has no such setting yet"
  check "one warning more than the fixture's two" eq "$(final_field warnings)" 3
  end_case
}

# The household's saved answer wins on a re-run (review round 1): an
# explicit --ai-models (what the Windows Setup passes for every Yes) on a
# box whose household turned AI off in Settings keeps AI_FEATURES=false,
# pulls nothing, and the final line says what the .env holds.
case_ai_flag_over_household_off() {
  new_case ai_flag_over_household_off
  local envf=$CHECKOUT/domovoi/.env rec=$ROOT/var/lib/domovoi-provision/env-written m
  local skipped="[models] skipped: AI chat and questions are off in this home's settings (AI_FEATURES=false in domovoi/.env, kept over --ai-models): no AI models pulled (turn them on in Settings, Models)"
  run_prov "${wsl_args[@]}" --skip-models
  check "first run exit 0" eq "$RC" 0
  sed -i 's/^AI_FEATURES=.*/AI_FEATURES=false/' "$envf"
  run_prov "${wsl_args[@]}" --ai-models --lan-ip 192.168.1.60
  check "--ai-models exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "household's AI_FEATURES=false kept" has_line "$envf" "AI_FEATURES=false"
  check "the usual warning" said "domovoi/.env: AI_FEATURES holds a value this script did not write"
  check "no model pulled while AI stays off" not_called "/api/pull"
  for m in "${AI_PULLS[@]}"; do check "not pulled: $m" not_called "$m"; done
  check "models step skipped" eq "$(state_of models)" skip
  check "says why, plainly" said "$skipped"
  check "said when it settled" said "AI chat and questions: off in this home's settings (AI_FEATURES=false in domovoi/.env), kept over --ai-models: no AI models are pulled"
  check "no models marker" no_marker models
  check "the final line says AI is off" eq "$(final_field ai)" false
  check "and result.json" grep -qF '"ai":false' "$ROOT/var/lib/domovoi-provision/result.json"
  check "said at the end" said "AI chat and questions: off (turn them on later on the dashboard: Settings, Models)"
  # The same options again: the env step is skipped (inputs unchanged), and
  # the .env still decides.
  run_prov "${wsl_args[@]}" --ai-models --lan-ip 192.168.1.60
  check "again: exit 0" eq "$RC" 0
  check "again: env skipped" eq "$(state_of env)" skip
  check "again: no pull" not_called "/api/pull"
  check "again: says why" said "$skipped"
  check "again: AI off" eq "$(final_field ai)" false
  # A run with no option follows the household, and leaves the answer
  # theirs: a later --ai-models still keeps it.
  run_prov "${wsl_args[@]}" --lan-ip 192.168.1.60
  check "no option: exit 0" eq "$RC" 0
  check "no option: no pull" not_called "/api/pull"
  check "no option: the record still the script's own" has_line "$rec" "AI_FEATURES=true"
  run_prov "${wsl_args[@]}" --ai-models --lan-ip 192.168.1.60
  check "--ai-models after that: exit 0" eq "$RC" 0
  check "--ai-models after that: still false" has_line "$envf" "AI_FEATURES=false"
  check "--ai-models after that: no pull" not_called "/api/pull"
  check "--ai-models after that: AI off" eq "$(final_field ai)" false
  end_case
}

# Left out with --no-ai-models, then a re-run with no option and no
# terminal (the Windows installer's repair): still off, nothing pulled.
case_ai_off_rerun_without_option() {
  new_case ai_off_rerun_without_option
  local envf=$CHECKOUT/domovoi/.env
  run_prov "${wsl_args[@]}" --no-ai-models
  check "first run exit 0" eq "$RC" 0
  run_prov "${wsl_args[@]}"
  check "re-run exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "kept it" said "AI chat and questions: left out (AI_FEATURES=false in domovoi/.env)"
  check "models skipped" eq "$(state_of models)" skip
  check "says why" said "[models] skipped: AI_FEATURES=false in domovoi/.env: AI chat and questions are off"
  check "no pull" not_called "/api/pull"
  check "no models marker" no_marker models
  check ".env still false" has_line "$envf" "AI_FEATURES=false"
  check "the final line says AI is off" eq "$(final_field ai)" false
  check "and result.json" grep -qF '"ai":false' "$ROOT/var/lib/domovoi-provision/result.json"
  end_case
}

# Left out at install, then the household turns AI on in Settings: the next
# run with no option pulls the models (the skipped step left no marker),
# and a later --no-ai-models keeps their answer.
case_ai_turned_on_in_settings_after_no() {
  new_case ai_turned_on_in_settings_after_no
  local envf=$CHECKOUT/domovoi/.env m
  run_prov "${wsl_args[@]}" --no-ai-models
  check "first run exit 0" eq "$RC" 0
  check "no pull" not_called "/api/pull"
  sed -i 's/^AI_FEATURES=.*/AI_FEATURES=true/' "$envf"
  run_prov "${wsl_args[@]}"
  check "no option exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "follows the household" said "AI chat and questions: included (AI_FEATURES=true in domovoi/.env)"
  check "models ran" eq "$(state_of models)" done
  for m in "${AI_PULLS[@]}"; do check "pulled: $m" called "$m"; done
  check "models marker" marker models
  check ".env still true" has_line "$envf" "AI_FEATURES=true"
  check "the final line says AI is on" eq "$(final_field ai)" true
  run_prov "${wsl_args[@]}" --no-ai-models --lan-ip 192.168.1.60
  check "--no-ai-models exit 0" eq "$RC" 0
  check "the household's answer kept" has_line "$envf" "AI_FEATURES=true"
  check "the usual warning" said "domovoi/.env: AI_FEATURES holds a value this script did not write"
  check "said when it settled" said "AI chat and questions: on in this home's settings (AI_FEATURES=true in domovoi/.env), kept over --no-ai-models: the AI models are pulled"
  check "models skipped: already done" eq "$(state_of models)" skip
  check "no pull: they are there" not_called "/api/pull"
  check "the final line says AI is on" eq "$(final_field ai)" true
  end_case
}

# ─── Docker's GPU runtime (wake-word training on the GPU, 2026-10-13) ────
#
# The spike's t7 distro: without the NVIDIA Container Toolkit, `docker run
# --gpus all` fails and training falls back to the CPU. scripts/linux/
# gpu-runtime.sh (which the update unit runs too) adds it where an NVIDIA
# GPU is visible, and nowhere else.

# A WSL distro that sees an NVIDIA GPU: NVIDIA's key checked like Docker's,
# the deb822 flat-repository source, the four packages at the pins without
# recommends after a simulation, the nvidia runtime in daemon.json, one
# Docker restart. The next run skips the step and restarts nothing.
case_gpu_runtime_on_wsl() {
  new_case gpu_runtime_on_wsl
  gpu_wsl
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "result ok" eq "$(final_field result)" '"ok"'
  check "gpu-runtime done" eq "$(state_of gpu-runtime)" done
  check "marker" marker gpu-runtime
  check "after the docker step" before "usermod -aG docker domovoi" "curl https://nvidia.github.io/libnvidia-container/gpgkey"
  check "before the venv" before "nvidia-ctk runtime configure" "python3 -m venv"
  check "the key read through a throwaway keyring" called "--quiet --import $CASE/tmp/tmp."
  check "the file NVIDIA serves" called "/nvidia-download.asc"
  check "NVIDIA's key alone in the keyring" eq "$(grep -c '^KEY ' "$ROOT/etc/apt/keyrings/nvidia-container-toolkit.asc")" 1
  check "the pinned key" has_line "$ROOT/etc/apt/keyrings/nvidia-container-toolkit.asc" "KEY C95B321B61E88C1809C4F759DDCAE044F796ECB0"
  check "the deb822 source of a flat repository" eq "$(cat "$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.sources")" \
    "$(printf 'Types: deb\nURIs: https://nvidia.github.io/libnvidia-container/stable/deb/amd64\nSuites: /\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/nvidia-container-toolkit.asc')"
  check "the key checked before apt reads the source" before "/nvidia-download.asc" "apt-get -o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=300 -q update"
  check "apt simulated the install first" before "install -s --no-install-recommends $NVIDIA_PKGS" "install -y --no-install-recommends $NVIDIA_PKGS"
  check "the four packages at the pins, no recommends" called "-q install -y --no-install-recommends $NVIDIA_PKGS"
  check "installed" grep -qx "libnvidia-container1=1.20.1-1" "$STATE/dpkg"
  check "not held" not_called "apt-mark"
  check "no driver" not_called "nvidia-driver"
  check "Docker configured" called "nvidia-ctk runtime configure --runtime=docker --config=$ROOT/etc/docker/daemon.json"
  check "daemon.json has the nvidia runtime" grep -q '"nvidia"' "$ROOT/etc/docker/daemon.json"
  check "Docker restarted after it" before "nvidia-ctk runtime configure" "systemctl restart docker.service"
  check "once" eq "$(grep -c 'systemctl restart docker.service' "$STATE/calls.log")" 1
  check "and the daemon loaded the runtime" grep -q '"nvidia"' "$STATE/docker-loaded"
  check "the step says what it did" grep -q '"step":"gpu-runtime".*"state":"done".*"message":"NVIDIA Container Toolkit 1.20.1-1 installed, Docker configured and restarted' "$CASE/progress.jsonl"
  local sig; sig=$(tree_sig)
  run_prov "${wsl_args[@]}" --skip-models
  check "second run exit 0" eq "$RC" 0
  check "second run: gpu-runtime skipped" eq "$(state_of gpu-runtime)" skip
  check "second run: no download" not_called "curl https://"
  check "second run: no apt" not_called "apt-get"
  check "second run: Docker not configured again" not_called "nvidia-ctk"
  check "second run: nothing restarted" not_called "systemctl restart"
  check "second run: every file as it was" eq "$(tree_sig)" "$sig"
  end_case
}

# /dev/dxg alone is any GPU Windows hands through (AMD, Intel): without the
# NVIDIA driver's libcuda.so.1 there is nothing for the toolkit to do.
case_gpu_runtime_needs_nvidias_libcuda_on_wsl() {
  new_case gpu_runtime_needs_nvidias_libcuda_on_wsl
  mkdir -p "$ROOT/dev"; : >"$ROOT/dev/dxg"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "gpu-runtime skipped" eq "$(state_of gpu-runtime)" skip
  check "says why" said "no /usr/lib/wsl/lib/libcuda.so.1"
  check "no NVIDIA key fetched" not_called "nvidia.github.io"
  check "no toolkit" not_called "nvidia-container-toolkit"
  check "Docker not configured for it" not_called "nvidia-ctk"
  check "no daemon.json" absent "$ROOT/etc/docker/daemon.json"
  check "WSL asks no nvidia-smi" not_called "nvidia-smi"
  check "no marker" no_marker gpu-runtime
  end_case
}

# Bare metal without the NVIDIA driver (the Beelink): nothing.
case_gpu_runtime_skipped_on_bare_metal_without_a_driver() {
  new_case gpu_runtime_skipped_on_bare_metal_without_a_driver --bare
  run_prov --internet sometimes --skip-models
  check "exit 0" eq "$RC" 0
  check "gpu-runtime skipped" eq "$(state_of gpu-runtime)" skip
  check "asked the driver" called "nvidia-smi -L"
  check "says why" said "[gpu-runtime] skipped: no NVIDIA GPU visible (nvidia-smi -L failed: NVIDIA-SMI has failed"
  check "no NVIDIA key fetched" not_called "nvidia.github.io"
  check "no toolkit" not_called "nvidia-container-toolkit"
  check "Docker not configured for it" not_called "nvidia-ctk"
  check "Docker not restarted" not_called "systemctl restart docker"
  end_case
}

# DOMOVOI_MANAGE_GPU_RUNTIME=0 in /etc/default/domovoi-update leaves
# Docker's GPU runtime to the administrator, here as in the update unit.
case_gpu_runtime_opt_out_is_honoured() {
  new_case gpu_runtime_opt_out_is_honoured
  gpu_wsl
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_MANAGE_GPU_RUNTIME=off\n' >"$ROOT/etc/default/domovoi-update"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "gpu-runtime skipped" eq "$(state_of gpu-runtime)" skip
  check "says why" said "DOMOVOI_MANAGE_GPU_RUNTIME=off in /etc/default/domovoi-update: Docker's GPU runtime is left to the administrator"
  check "no NVIDIA key fetched" not_called "nvidia.github.io"
  check "Docker not configured for it" not_called "nvidia-ctk"
  end_case
}

# Bare metal whose NVIDIA driver lists a GPU: the same install as on WSL.
case_gpu_runtime_on_bare_metal() {
  new_case gpu_runtime_on_bare_metal --bare
  gpu_bare "NVIDIA GeForce RTX 3060"
  run_prov --internet sometimes --skip-models
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "gpu-runtime done" eq "$(state_of gpu-runtime)" done
  check "says which GPU" said "NVIDIA GPU visible (nvidia-smi: NVIDIA GeForce RTX 3060)"
  check "the four packages at the pins, no recommends" called "-q install -y --no-install-recommends $NVIDIA_PKGS"
  check "Docker configured" called "nvidia-ctk runtime configure --runtime=docker"
  check "Docker restarted" called "systemctl restart docker.service"
  check "no driver" not_called "nvidia-driver"
  end_case
}

# Like Docker's (review finding 3): a key file with a second primary key
# is refused, and so is a key with another fingerprint. Nothing installed.
case_gpu_runtime_key_file_with_a_second_key_only_warns() {
  new_case gpu_runtime_key_file_with_a_second_key_only_warns
  gpu_wsl
  echo 0123456789ABCDEF0123456789ABCDEF01234567 >"$STATE/nvidia-extra-key"
  run_prov "${wsl_args[@]}" --skip-models
  # A warning, not the end of the install: training is optional.
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "final ok" eq "$(final_field result)" '"ok"'
  check "gpu-runtime done, not set up" eq "$(state_of gpu-runtime)" done
  check "the step says so" grep -q '"step":"gpu-runtime".*"state":"done".*GPU for containers not set up' "$CASE/progress.jsonl"
  check "a warning counted" test "$(final_field warnings)" -ge 1
  check "says why" said "holds 2 primary keys"
  check "key not installed" absent "$ROOT/etc/apt/keyrings/nvidia-container-toolkit.asc"
  check "no source" absent "$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.sources"
  check "no toolkit" not_called "install -y --no-install-recommends nvidia"
  check "Docker left alone" not_called "nvidia-ctk"
  check "the helper recorded the failure" grep -q "^reason=failed: NVIDIA's apt key" "$ROOT/var/lib/domovoi-gpu-runtime/last-failure"
  # The next run tries again (its check fails while the runtime is missing):
  # a provision run passes --force, so the fresh record holds nothing off.
  rm -f "$STATE/nvidia-extra-key"
  run_prov "${wsl_args[@]}" --skip-models
  check "next run exit 0" eq "$RC" 0
  check "next run sets it up" called "install -y --no-install-recommends $NVIDIA_PKGS"
  check "next run: the record removed" absent "$ROOT/var/lib/domovoi-gpu-runtime/last-failure"
  end_case
}

case_gpu_runtime_key_mismatch_only_warns() {
  new_case gpu_runtime_key_mismatch_only_warns
  gpu_wsl
  echo 0000000000000000000000000000000000000000 >"$STATE/nvidia-fpr"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "gpu-runtime done, not set up" eq "$(state_of gpu-runtime)" done
  check "says the fingerprint" said "the manifest pins C95B321B61E88C1809C4F759DDCAE044F796ECB0"
  check "in the step's message" grep -q '"step":"gpu-runtime".*not set up.*the manifest pins C95B321B61E88C1809C4F759DDCAE044F796ECB0' "$CASE/progress.jsonl"
  check "key not installed" absent "$ROOT/etc/apt/keyrings/nvidia-container-toolkit.asc"
  check "no toolkit" not_called "install -y --no-install-recommends nvidia"
  end_case
}

# Never a Linux NVIDIA driver inside WSL: an install that would bring one
# (or its kernel modules, or its compute libraries) is refused after apt's
# simulation, nothing installed.
case_gpu_runtime_refuses_a_driver_on_wsl() {
  new_case gpu_runtime_refuses_a_driver_on_wsl
  gpu_wsl
  printf '%s\n' "Inst nvidia-driver-580 (580.95.05-0ubuntu1 Ubuntu:26.04/resolute [amd64])" \
    "Inst libnvidia-compute-580 (580.95.05-0ubuntu1 Ubuntu:26.04/resolute [amd64])" >"$STATE/apt-simulate-extra"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "gpu-runtime done, not set up" eq "$(state_of gpu-runtime)" done
  check "simulated" called "install -s --no-install-recommends $NVIDIA_PKGS"
  check "says what it refused" said "refused: installing the NVIDIA Container Toolkit would also install nvidia-driver-580 libnvidia-compute-580"
  check "and whose driver it is" said "the driver is Windows' on WSL"
  check "nothing installed" not_called "install -y --no-install-recommends nvidia"
  check "no NVIDIA package" eq "$(grep -c nvidia "$STATE/dpkg")" 0
  check "Docker left alone" not_called "nvidia-ctk"
  check "not restarted" not_called "systemctl restart docker"
  end_case
}

case_gpu_runtime_dry_run_plans_it() {
  new_case gpu_runtime_dry_run_plans_it
  gpu_wsl
  local sig; sig=$(tree_sig)
  run_prov --dry-run "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "every file as it was" eq "$(tree_sig)" "$sig"
  check "planned to run" grep -q '"step":"gpu-runtime".*"state":"plan".*"would":"run"' "$CASE/progress.jsonl"
  check "plans the key check" said "exactly one primary key, fingerprint C95B321B61E88C1809C4F759DDCAE044F796ECB0"
  check "plans the pinned packages" said "nvidia-container-toolkit=1.20.1-1 nvidia-container-toolkit-base=1.20.1-1"
  check "no download" not_called "nvidia.github.io"
  check "no apt" not_called "apt-get"
  check "Docker left alone" not_called "nvidia-ctk"
  end_case
}

# Docker that will not start with the nvidia runtime configured gets its
# daemon.json back as it was and is started again: never left down by this
# step. A later run, once Docker takes it, configures and restarts it.
case_gpu_runtime_puts_daemon_json_back_when_docker_will_not_start() {
  new_case gpu_runtime_puts_daemon_json_back_when_docker_will_not_start
  gpu_wsl
  mkdir -p "$ROOT/etc/docker"
  printf '{"log-driver": "local"}\n' >"$ROOT/etc/docker/daemon.json"
  touch "$STATE/docker-rejects-nvidia"
  run_prov "${wsl_args[@]}" --skip-models
  check "exit 0" eq "$RC" 0
  check "progress format" progress_ok
  check "gpu-runtime done, not set up" eq "$(state_of gpu-runtime)" done
  check "says so" said "Docker did not start with the nvidia runtime configured; /etc/docker/daemon.json is back as it was and Docker runs again"
  check "daemon.json as it was" eq "$(cat "$ROOT/etc/docker/daemon.json")" '{"log-driver": "local"}'
  check "its failed mark cleared" called "systemctl reset-failed docker.service docker.socket"
  check "restarted again with it" eq "$(grep -c 'systemctl restart docker.service' "$STATE/calls.log")" 2
  check "Docker runs" test -f "$STATE/active/docker.service"
  check "on the file as it was" eq "$(cat "$STATE/docker-loaded")" '{"log-driver": "local"}'
  rm -f "$STATE/docker-rejects-nvidia"
  run_prov "${wsl_args[@]}" --skip-models
  check "next run exit 0" eq "$RC" 0
  check "next run: gpu-runtime done" eq "$(state_of gpu-runtime)" done
  check "next run: the toolkit stays installed" not_called "install -y --no-install-recommends nvidia"
  check "next run: the household's setting kept" grep -q '"fixture-kept": {"log-driver": "local"}' "$ROOT/etc/docker/daemon.json"
  check "next run: the file before it kept" eq "$(cat "$ROOT/var/lib/domovoi-gpu-runtime/daemon.json.before")" '{"log-driver": "local"}'
  check "next run: Docker restarted once" eq "$(grep -c 'systemctl restart docker.service' "$STATE/calls.log")" 1
  check "next run: the runtime loaded" grep -q '"nvidia"' "$STATE/docker-loaded"
  end_case
}

# The helper's own interface, as the update unit and an administrator call
# it: --detect, --check, --internet never, --dry-run, --no-restart,
# --restart-flag, idempotence and usage errors.
run_gpu() {
  RC=0
  : >"$STATE/calls.log"
  env PATH="$CASE/bin:$PATH" DOMOVOI_PROVISION_ROOT="$ROOT" DOMOVOI_PROVISION_TEST_HARNESS=1 TMPDIR="$CASE/tmp" \
    bash "$REAL_REPO/scripts/linux/gpu-runtime.sh" "$@" >"$CASE/output.log" 2>&1 || RC=$?
  LAST=$(tail -n 1 "$CASE/output.log")
}
case_gpu_runtime_helper_interface() {
  new_case gpu_runtime_helper_interface
  mkdir -p "$STATE/active"; touch "$STATE/active/docker.service"; : >"$STATE/docker-loaded"
  run_gpu --detect
  check "no GPU: --detect 1" eq "$RC" 1
  check "no GPU: says why" eq "$LAST" "gpu-runtime: no NVIDIA GPU visible (WSL: no /dev/dxg, so Windows hands this distro no GPU)"
  run_gpu --check
  check "no GPU: --check 0" eq "$RC" 0
  run_gpu
  check "no GPU: a run, 0" eq "$RC" 0
  check "no GPU: nothing to do" eq "$LAST" "gpu-runtime: no NVIDIA GPU visible (WSL: no /dev/dxg, so Windows hands this distro no GPU); nothing to do"
  gpu_wsl
  run_gpu --detect
  check "GPU: --detect 0" eq "$RC" 0
  check "GPU: says so" eq "$LAST" "gpu-runtime: NVIDIA GPU visible (WSL: /dev/dxg and /usr/lib/wsl/lib/libcuda.so.1, Windows' NVIDIA driver)"
  run_gpu --check
  check "not set up: --check 1" eq "$RC" 1
  check "not set up: what is to do" eq "$LAST" "gpu-runtime: not in place: install $NVIDIA_PKGS; put the nvidia runtime in /etc/docker/daemon.json; restart Docker to load the nvidia runtime (WSL: yes; internet: unanswered)"
  run_gpu --internet never
  check "never: 0" eq "$RC" 0
  check "never: not installed, and why" eq "${LAST%% (*}" "gpu-runtime: not installed: the NVIDIA Container Toolkit is a download and internet access is turned off, so nothing changed"
  check "never: no download" not_called "curl"
  check "never: no apt" not_called "apt-get"
  run_gpu --dry-run
  check "dry run: 0" eq "$RC" 0
  check "dry run: says so" eq "${LAST%% (*}" "gpu-runtime: dry run, nothing changed"
  check "dry run: plans the install" said "would apt-get install --no-install-recommends $NVIDIA_PKGS"
  check "dry run: nothing written" absent "$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.sources"
  check "dry run: no apt" not_called "apt-get"
  # NVIDIA's own one-line install (another key file): a second source for
  # the same repository would stop apt, so the install is refused.
  mkdir -p "$ROOT/etc/apt/sources.list.d"
  printf 'deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /\n' \
    >"$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.list"
  run_gpu --internet sometimes
  check "another source for it: 1" eq "$RC" 1
  check "another source for it: refused, naming it" eq "${LAST%%, not by Domovoi*}" \
    "gpu-runtime: refused: NVIDIA's apt repository is already set up in /etc/apt/sources.list.d/nvidia-container-toolkit.list"
  check "another source for it: nothing written" absent "$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.sources"
  check "another source for it: no download" not_called "curl"
  check "another source for it: no apt" not_called "apt-get"
  local rec=$ROOT/var/lib/domovoi-gpu-runtime/last-failure
  check "another source for it: recorded" grep -q "^reason=refused: NVIDIA's apt repository is already set up in /etc/apt/sources.list.d/nvidia-container-toolkit.list" "$rec"
  rm -f "$ROOT/etc/apt/sources.list.d/nvidia-container-toolkit.list"
  # The record holds the same pins off for a day (--retry-after), --check
  # included; --force, or --retry-after 0, tries.
  run_gpu --internet sometimes
  check "held off: 0" eq "$RC" 0
  check "held off: the summary" eq "${LAST%%: the last try*}" "gpu-runtime: not retried"
  check "held off: since when" said "gpu-runtime: not retried: the last try was refused at "
  check "held off: why" said "(NVIDIA's apt repository is already set up in /etc/apt/sources.list.d/nvidia-container-toolkit.list"
  check "held off: until when" said "; it is tried again at the first update or Restart after "
  check "held off: no download" not_called "curl"
  check "held off: no apt" not_called "apt-get"
  run_gpu --check
  check "held off: --check 0, nothing to do now" eq "$RC" 0
  check "held off: --check says why" eq "${LAST%%: the last try*}" "gpu-runtime: not retried"
  run_gpu --check --force
  check "--check --force: 1" eq "$RC" 1
  check "--check --force: what is to do" eq "${LAST%% (*}" "gpu-runtime: not in place: install $NVIDIA_PKGS; put the nvidia runtime in /etc/docker/daemon.json; restart Docker to load the nvidia runtime"
  run_gpu --internet sometimes --retry-after 0 --dry-run
  check "--retry-after 0: tried (a dry run here)" eq "${LAST%% (*}" "gpu-runtime: dry run, nothing changed"
  run_gpu --internet sometimes --no-restart --force
  check "--no-restart: 0" eq "$RC" 0
  check "--no-restart: says a restart is needed" eq "$LAST" "gpu-runtime: NVIDIA Container Toolkit 1.20.1-1 installed, Docker configured; Docker not restarted (--no-restart): restart it to load the nvidia runtime (WSL: yes; internet: sometimes)"
  check "--no-restart: not restarted" not_called "systemctl restart"
  check "set up: the record removed" absent "$rec"
  run_gpu --check
  check "not loaded yet: --check 1" eq "$RC" 1
  check "not loaded yet: what is to do" eq "$LAST" "gpu-runtime: not in place: restart Docker to load the nvidia runtime (WSL: yes; internet: unanswered)"
  run_gpu --internet never --restart-flag "$CASE/restarted"
  check "the restart, under never too: 0" eq "$RC" 0
  check "the restart: says so" eq "$LAST" "gpu-runtime: Docker restarted to load the nvidia runtime (WSL: yes; internet: never)"
  check "the restart: the flag" test -f "$CASE/restarted"
  check "the restart: no download" not_called "curl"
  check "the restart: no apt" not_called "apt-get"
  run_gpu --check
  check "set up: --check 0" eq "$RC" 0
  run_gpu --restart-flag "$CASE/again"
  check "set up: 0" eq "$RC" 0
  check "set up: already in place" eq "$LAST" "gpu-runtime: already in place (NVIDIA Container Toolkit 1.20.1-1; Docker has the nvidia runtime; WSL: yes; internet: unanswered)"
  check "set up: no flag" absent "$CASE/again"
  check "set up: no download" not_called "curl"
  check "set up: no apt" not_called "apt-get"
  check "set up: Docker left alone" not_called "nvidia-ctk"
  check "set up: nothing restarted" not_called "systemctl restart"
  run_gpu --internet maybe
  check "bad --internet: 2" eq "$RC" 2
  run_gpu --wsl perhaps
  check "bad --wsl: 2" eq "$RC" 2
  run_gpu --detect --check
  check "two modes: 2" eq "$RC" 2
  run_gpu --retry-after soon
  check "bad --retry-after: 2" eq "$RC" 2
  run_gpu --bogus
  check "unknown option: 2" eq "$RC" 2
  run_gpu --help
  check "--help: 0" eq "$RC" 0
  check "--help: the options" said "--restart-flag FILE"
  end_case
}

# ─── release channels (docs/LINUX_HOST.md, Release channels; 2026-10-16) ─
#
# New installs follow stable, the branch that only moves to a release's
# commit; main carries every change. The fixture origin has no stable
# branch (as the real one before its first release); release_origin makes
# this case's own copy with one.

# release_origin [STABLE_AT]: a bare copy of the fixture origin with
# v1.0.0 tagged at FIXTURE_A and stable at STABLE_AT (default: there too,
# the first release). Prints its path.
release_origin() {
  local o=$CASE/origin.git
  git clone -q --bare "$ORIGIN" "$o"
  git -C "$o" tag v1.0.0 "$FIXTURE_A"
  git -C "$o" branch stable "${1:-$FIXTURE_A}"
  printf '%s' "$o"
}
tracks() { eq "$(git -C "$CHECKOUT" rev-parse --abbrev-ref HEAD) $(git -C "$CHECKOUT" rev-parse --abbrev-ref '@{u}')" "$1 origin/$1"; }
PIN_LINE='DOMOVOI_UPSTREAM_BRANCH="stable main"'

case_new_install_follows_stable() {
  new_case new_install_follows_stable
  local o; o=$(release_origin)
  run_prov "${wsl_args[@]}" --repo-url "$o"
  check "exit 0" eq "$RC" 0
  check "HEAD is the release" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "on stable, tracking origin/stable" tracks stable
  check "no fallback" not_said "no stable branch yet"
  check "nothing to remember" eq "$(git -C "$CHECKOUT" config domovoi.intendedChannel || echo unset)" unset
  check "the channel pin written" has_line "$ROOT/etc/default/domovoi-update" "$PIN_LINE"
  check "beside the lock line" has_line "$ROOT/etc/default/domovoi-update" "DOMOVOI_USE_LOCK=1"
  check "before the update unit's installer ran" grep -qxF "$PIN_LINE" "$STATE/update-defaults-seen"
  run_prov "${wsl_args[@]}" --repo-url "$o"
  check "second run: update-unit skipped" eq "$(state_of update-unit)" skip
  check "still one pin" eq "$(grep -c '^DOMOVOI_UPSTREAM_BRANCH=' "$ROOT/etc/default/domovoi-update")" 1
  end_case
}

case_before_the_first_release_installs_follow_main() {
  new_case before_the_first_release_installs_follow_main
  run_prov "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "HEAD is main's" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_B"
  check "on main, tracking origin/main" tracks main
  check "says why" said "has no stable branch yet (no release has been published): installing main"
  check "and how to move later" said "switch to stable in Settings > About & updates after the first release"
  check "the checkout keeps what was asked for" eq "$(git -C "$CHECKOUT" config domovoi.intendedChannel)" stable
  check "the channel pin written (main is one)" has_line "$ROOT/etc/default/domovoi-update" "$PIN_LINE"
  end_case
}

# The Windows installer's way: a pinned release commit (or its tag),
# following stable.
case_a_release_on_stable_by_tag_or_sha() {
  new_case a_release_on_stable_by_tag_or_sha
  local o; o=$(release_origin)
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref v1.0.0 --track-branch stable
  check "tag: exit 0" eq "$RC" 0
  check "tag: HEAD is the release" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "tag: on stable" tracks stable
  rm -rf "$CHECKOUT"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref "$FIXTURE_A" --track-branch stable
  check "sha: exit 0" eq "$RC" 0
  check "sha: on stable" tracks stable
  check "sha: no warning about the channel" not_said "newer than the latest release"
  end_case
}

# A release candidate, or the installer's release before stable has moved
# to it: newer than stable, on main. It follows stable and waits for the
# next release, which holds it.
case_a_commit_newer_than_stable_waits_for_the_next_release() {
  new_case a_commit_newer_than_stable_waits_for_the_next_release
  local o; o=$(release_origin)
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref "$FIXTURE_B" --track-branch stable
  check "exit 0" eq "$RC" 0
  check "HEAD is the commit" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_B"
  check "on stable" tracks stable
  check "says so" said "is newer than the latest release on stable; this box follows stable, and updates resume with the next release"
  end_case
}

# Newer than stable but not on main (another branch's commit): no release
# would ever hold it, so no update could fast-forward.
case_a_commit_off_main_never_follows_stable() {
  new_case a_commit_off_main_never_follows_stable
  local o side; o=$(release_origin)
  side=$(git -C "$ORIGIN" rev-parse bad-manifest)
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref "$side" --track-branch stable
  check "exit 1" eq "$RC" 1
  check "says why" said "is not on origin/stable's history, so an update could never fast-forward from it"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref "$FIXTURE_ELSEWHERE" --track-branch stable
  check "unrelated: exit 1" eq "$RC" 1
  end_case
}

# The installer's arguments before the first release: its pinned commit,
# following main instead, with the same warning.
case_the_installer_before_the_first_release_follows_main() {
  new_case the_installer_before_the_first_release_follows_main
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A" --track-branch stable
  check "exit 0" eq "$RC" 0
  check "HEAD is the pinned commit" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "on main" tracks main
  check "says why" said "has no stable branch yet (no release has been published): this box follows main"
  check "the checkout keeps what was asked for" eq "$(git -C "$CHECKOUT" config domovoi.intendedChannel)" stable
  end_case
}

# --ref stable asked for by name has no fallback.
case_ref_stable_without_one_stops() {
  new_case ref_stable_without_one_stops
  run_prov "${wsl_args[@]}" --ref stable
  check "exit 1" eq "$RC" 1
  check "says why" said "has no branch stable (--ref takes a branch, a tag such as v1.0.0, or a full commit SHA)"
  end_case
}

# A box someone pointed at a branch of their own keeps following it: no pin.
case_a_branch_of_its_own_gets_no_channel_pin() {
  new_case a_branch_of_its_own_gets_no_channel_pin
  local o; o=$(release_origin)
  git -C "$o" branch feature "$FIXTURE_B"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref feature
  check "exit 0" eq "$RC" 0
  check "on feature" tracks feature
  check "no pin" eq "$(grep -c '^DOMOVOI_UPSTREAM_BRANCH' "$ROOT/etc/default/domovoi-update")" 0
  check "warned" said "not a release channel (stable or main): no branch pin written"
  end_case
}

# An administrator's own pin stays; a box installed before the pin existed
# gets it at its next run (the installer's repair).
case_the_channel_pin_respects_the_administrator() {
  new_case the_channel_pin_respects_the_administrator
  local o f=$ROOT/etc/default/domovoi-update; o=$(release_origin)
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPSTREAM_BRANCH=stable\n' >"$f"
  run_prov "${wsl_args[@]}" --repo-url "$o"
  check "exit 0" eq "$RC" 0
  check "the admin's pin kept" has_line "$f" "DOMOVOI_UPSTREAM_BRANCH=stable"
  check "no second pin" eq "$(grep -c '^DOMOVOI_UPSTREAM_BRANCH' "$f")" 1
  check "said" said "updates follow what /etc/default/domovoi-update pins (DOMOVOI_UPSTREAM_BRANCH=stable)"
  # From before the pin: the file holds the lock line only.
  printf 'DOMOVOI_USE_LOCK=1\n' >"$f"
  run_prov "${wsl_args[@]}" --repo-url "$o"
  check "repair: exit 0" eq "$RC" 0
  check "repair: update-unit re-ran" eq "$(state_of update-unit)" done
  check "repair: the pin added" has_line "$f" "$PIN_LINE"
  check "repair: the lock line still once" eq "$(grep -c '^DOMOVOI_USE_LOCK=' "$f")" 1
  end_case
}

# Delta security review 2026-10-16, S3: --ref is read by its shape (a full
# SHA is the commit, v<digit>... is a tag), never as a branch of the same
# name someone upstream could create; and HEAD must be the commit it named.
case_a_branch_named_like_the_pinned_commit_is_not_used() {
  new_case a_branch_named_like_the_pinned_commit_is_not_used
  local o; o=$(release_origin)
  git -C "$o" branch "$FIXTURE_A" "$FIXTURE_ELSEWHERE" 2>/dev/null
  check "the look-alike branch is there" git -C "$o" rev-parse -q --verify "refs/heads/$FIXTURE_A"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref "$FIXTURE_A" --track-branch main
  check "exit 0" eq "$RC" 0
  check "HEAD is the pinned commit" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "on main, not the look-alike branch" tracks main
  end_case
}

case_a_branch_named_like_a_tag_is_not_used() {
  new_case a_branch_named_like_a_tag_is_not_used
  local o; o=$(release_origin)
  git -C "$o" branch v1.0.0 "$FIXTURE_ELSEWHERE"
  git -C "$o" branch v9.9.9 "$FIXTURE_ELSEWHERE"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref v1.0.0 --track-branch stable
  check "exit 0" eq "$RC" 0
  check "HEAD is the tag's commit" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "on stable" tracks stable
  check "said which commit the tag is" said "the tag v1.0.0 is commit $FIXTURE_A"
  check "recorded it" eq "$(cat "$ROOT/var/lib/domovoi-provision/checkout-ref")" "tag v1.0.0 $FIXTURE_A"
  rm -rf "$CHECKOUT"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref v9.9.9 --track-branch stable
  check "no such tag, only a branch: exit 1" eq "$RC" 1
  check "says there is no such tag" said "has no tag v9.9.9"
  check "nothing installed" absent "$CHECKOUT/.git"
  end_case
}

# A published tag never moves: a later clone (a resumed install) that finds
# it at another commit stops, and installs nothing.
case_a_tag_that_moved_since_the_first_run_stops() {
  new_case a_tag_that_moved_since_the_first_run_stops
  local o; o=$(release_origin)
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref v1.0.0 --track-branch stable
  check "first run: exit 0" eq "$RC" 0
  rm -rf "$CHECKOUT"
  git -C "$o" tag -f v1.0.0 "$FIXTURE_B" >/dev/null
  git -C "$o" branch -f stable "$FIXTURE_B"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref v1.0.0 --track-branch stable
  check "moved: exit 1" eq "$RC" 1
  check "says so" said "the tag v1.0.0 was commit $FIXTURE_A when this box was first set up from it and is $FIXTURE_B now"
  check "nothing installed" absent "$CHECKOUT/.git"
  end_case
}


# ─── The verified copy (delta security review 2026-10-16, S4) ────────────
#
# A re-run used to install the units, run os-jobs.sh, gpu-runtime.sh and
# install-update-unit.sh, read the pin manifest and copy the journald
# drop-in from the working tree, which by then is the service user's.

have_ssh_signing() { local out; out=$(ssh-keygen -Y 2>&1); [[ $out == *"requires an argument"* ]]; }
not() { ! "$@"; }

# tamper_checkout: what code running as the service user can do to its own
# checkout without a commit: edit files, swap one for a link, hide an edit
# from git status.
tamper_checkout() {
  local co=$CHECKOUT/scripts/linux
  sed -i '/^User=/d; s#^ExecStart=.*#ExecStart=/bin/sh -c "id > /root/owned"#' "$co/units/domovoi-core.service"
  sed -i "2i echo planted >>$CASE/ran-os-jobs" "$co/os-jobs.sh"
  sed -i "2i echo planted >>$CASE/ran-gpu-runtime" "$co/gpu-runtime.sh"
  sed -i "2i echo planted >>$CASE/ran-install-update-unit" "$co/install-update-unit.sh"
  sed -i 's/^docker_key_fpr=.*/docker_key_fpr=0000000000000000000000000000000000000000/' "$co/provision-manifest.conf"
  printf 'secret-line\n' >"$CASE/secret"
  rm "$co/units/journald-domovoi.conf"
  ln -s "$CASE/secret" "$co/units/journald-domovoi.conf"
  git -C "$CHECKOUT" update-index --skip-worktree scripts/linux/os-jobs.sh
}

case_rerun_takes_nothing_from_the_working_tree() {
  new_case rerun_takes_nothing_from_the_working_tree
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  local head; head=$(git -C "$CHECKOUT" rev-parse HEAD)
  check "the first run's copy is gone" test ! -e "$ROOT/run/domovoi-provision/$head"
  tamper_checkout
  # Gone, so the steps that install them run again.
  rm -f "$ROOT/etc/systemd/system/domovoi-core.service" "$ROOT/etc/systemd/journald.conf.d/50-domovoi.conf" \
    "$ROOT/etc/systemd/system/domovoi-update.service"
  run_prov "${wsl_args[@]}"
  local unit=$ROOT/etc/systemd/system/domovoi-core.service drop=$ROOT/etc/systemd/journald.conf.d/50-domovoi.conf
  check "re-run exit 0" eq "$RC" 0
  check "the checkout read by upload-pack as the service user" called "runuser -u domovoi -- git upload-pack"
  check "said where it reads from" said "the checkout's files are read from $ROOT/run/domovoi-provision/$head"
  check "the units step ran" eq "$(state_of units)" done
  check "the core unit is the commit's (User= kept)" grep -qx 'User=domovoi' "$unit"
  check "not the edited one" not grep -q '/root/owned' "$unit"
  check "the drop-in is the commit's" cmp -s "$drop" "$REAL_REPO/scripts/linux/units/journald-domovoi.conf"
  check "nothing of the link's target" not grep -q secret-line "$drop"
  check "the edited os-jobs.sh never ran" test ! -e "$CASE/ran-os-jobs"
  check "the edited gpu-runtime.sh never ran" test ! -e "$CASE/ran-gpu-runtime"
  check "the update-unit step ran" eq "$(state_of update-unit)" done
  check "the commit's installer" called "install-update-unit --repo $CHECKOUT"
  check "the edited one never ran" test ! -e "$CASE/ran-install-update-unit"
  check "the pins are the commit's" said "pins from $ROOT/run/domovoi-provision/$head/scripts/linux/provision-manifest.conf"
  check "so Docker's step is unchanged" eq "$(state_of docker)" skip
  check "the copy is gone after the run" test ! -e "$ROOT/run/domovoi-provision/$head"
  check "the mirror is root's state" test -d "$ROOT/var/lib/domovoi-update/mirror.git"
  check "files of the checkout it only hashes, hashed as the service user"     grep -q "^runuser -u domovoi -- .* sha256sum -- $CHECKOUT/domovoi/docker-compose.yml" "$STATE/calls.log"
  check "progress format" progress_ok
  end_case
}

case_rerun_refuses_an_unverified_head() {
  new_case rerun_refuses_an_unverified_head
  if ! have_ssh_signing; then skip_case "no ssh-keygen -Y on this box"; return; fi
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  mkdir -p "$CASE/keys" "$ROOT/etc/domovoi"
  ssh-keygen -q -t ed25519 -N '' -C owner -f "$CASE/keys/owner" >/dev/null
  printf 'owner@example.invalid namespaces="git" %s\n' "$(cut -d' ' -f1,2 "$CASE/keys/owner.pub")" >"$ROOT/etc/domovoi/allowed_signers"
  tamper_checkout
  rm -f "$ROOT/etc/systemd/system/domovoi-core.service"
  run_prov "${wsl_args[@]}"
  check "exit 1" eq "$RC" 1
  check "says why" said "signed updates are enforced by /etc/domovoi/allowed_signers, but HEAD is not signed"
  check "the final line says failed" eq "$(final_field result)" '"failed"'
  check "the core unit was not reinstalled" test ! -e "$ROOT/etc/systemd/system/domovoi-core.service"
  check "nothing of the checkout ran" test ! -e "$CASE/ran-os-jobs"
  check "nor the gpu helper" test ! -e "$CASE/ran-gpu-runtime"
  check "progress format" progress_ok
  end_case
}

case_dry_run_with_a_checkout_changes_nothing() {
  new_case dry_run_with_a_checkout_changes_nothing
  run_prov "${wsl_args[@]}"
  check "first run exit 0" eq "$RC" 0
  tamper_checkout
  local sig; sig=$(tree_sig)
  run_prov --dry-run "${wsl_args[@]}"
  check "exit 0" eq "$RC" 0
  check "every file under the root as it was" eq "$(tree_sig)" "$sig"
  check "the helpers asked are the commit's" test ! -e "$CASE/ran-gpu-runtime"
  check "result dry_run" eq "$(final_field result)" '"dry_run"'
  end_case
}

# rc2 review C7: a tag whose name doesn't start with v is that tag, not a
# branch of the same name; stable and main stay the branches whatever tags
# exist; refs/heads/NAME asks for the branch.
case_a_tag_not_named_v_wins_over_a_branch() {
  new_case a_tag_not_named_v_wins_over_a_branch
  local o; o=$(release_origin)
  git -C "$o" tag release-1 "$FIXTURE_A"
  git -C "$o" branch release-1 "$FIXTURE_ELSEWHERE"
  git -C "$o" tag stable "$FIXTURE_ELSEWHERE"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref release-1
  check "the tag: exit 0" eq "$RC" 0
  check "HEAD is the tag's commit" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  # (Plain rev-parse --abbrev-ref would say heads/stable: a tag is named stable here.)
  check "on stable" eq "$(git -C "$CHECKOUT" symbolic-ref HEAD) $(git -C "$CHECKOUT" config branch.stable.merge)" "refs/heads/stable refs/heads/stable"
  check "said which commit" said "the tag release-1 is commit $FIXTURE_A"
  rm -rf "$CHECKOUT"
  run_prov "${wsl_args[@]}" --repo-url "$o"
  check "default: exit 0" eq "$RC" 0
  check "stable is the branch, not the tag named stable" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  rm -rf "$CHECKOUT"
  run_prov "${wsl_args[@]}" --repo-url "$o" --ref refs/heads/release-1
  check "refs/heads/: exit 0" eq "$RC" 0
  check "refs/heads/ is the branch, asked for by name" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_ELSEWHERE"
  check "and it follows it" eq "$(git -C "$CHECKOUT" symbolic-ref HEAD) $(git -C "$CHECKOUT" config branch.release-1.merge)" "refs/heads/release-1 refs/heads/release-1"
  end_case
}

# ─── a git killed in the existing checkout (tidy_*) ──────────────────────
#
# A household whose dashboard pull is stuck on what a killed git left (a
# stale index.lock, files a killed fast-forward rewrote) can't get the code
# that cleans up at a pull through that pull: a repair by Setup re-runs
# this script, which cleans up at every run (tidy_checkout).

# old_lock FILE [SECONDS]: a lock file last touched SECONDS (600) ago.
old_lock() { : >"$1"; touch -d "@$(( $(date +%s) - ${2:-600} ))" "$1"; }

case_tidy_a_stale_lock_in_the_checkout_is_removed() {
  new_case tidy_a_stale_lock_in_the_checkout_is_removed
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the lock is gone" absent "$CHECKOUT/.git/index.lock"
  check "says so" said "removed a stale git lock index.lock in $CHECKOUT"
  check "as the service user" grep -q "^runuser -u domovoi -- .* rm -f -- $CHECKOUT/.git/index.lock" "$STATE/calls.log"
  check "HEAD not moved" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "the dashboard's pull can go now" git -C "$CHECKOUT" merge -q --ff-only origin/main
  end_case
}

case_tidy_a_lock_a_running_git_holds_stays() {
  new_case tidy_a_lock_a_running_git_holds_stays
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  mkdir -p "$ROOT/proc/4242"
  echo git >"$ROOT/proc/4242/comm"
  ln -s "$CHECKOUT" "$ROOT/proc/4242/cwd"
  printf 'git\0status\0' >"$ROOT/proc/4242/cmdline"
  old_lock "$CHECKOUT/.git/index.lock" 3600
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the live lock stays" test -e "$CHECKOUT/.git/index.lock"
  check "says why" said "left alone: a git process works there"
  end_case
}

case_tidy_a_killed_fast_forward_is_put_back() {
  new_case tidy_a_killed_fast_forward_is_put_back
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  # What a pull to main killed partway leaves: B's pyproject.toml already
  # written, a file B adds there untracked, and the lock.
  git -C "$ORIGIN" show "$FIXTURE_B:pyproject.toml" >"$CHECKOUT/pyproject.toml"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the lock is gone" absent "$CHECKOUT/.git/index.lock"
  check "the tree is clean again" eq "$(git -C "$CHECKOUT" status --porcelain --untracked-files=no)" ""
  check "says so" said "an interrupted update had rewritten 1 file(s) and added 0 in $CHECKOUT"
  check "HEAD not moved" eq "$(git -C "$CHECKOUT" rev-parse HEAD)" "$FIXTURE_A"
  check "the dashboard's pull can go now" git -C "$CHECKOUT" merge -q --ff-only origin/main
  end_case
}

case_tidy_the_households_own_edit_stays() {
  new_case tidy_the_households_own_edit_stays
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  printf '[project]\nname = "my own"\n' >"$CHECKOUT/pyproject.toml"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the edit stays" grep -q 'my own' "$CHECKOUT/pyproject.toml"
  check "nothing put back" not_said "an interrupted update had rewritten"
  end_case
}

case_tidy_a_file_cut_short_is_put_back() {
  new_case tidy_a_file_cut_short_is_put_back
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  # The file the killed git was writing: the first bytes of B's version.
  git -C "$ORIGIN" show "$FIXTURE_B:pyproject.toml" | head -c 12 >"$CHECKOUT/pyproject.toml"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the tree is clean again" eq "$(git -C "$CHECKOUT" status --porcelain --untracked-files=no)" ""
  check "nothing kept aside: it held nothing of its own" not_said "were copied to"
  check "the dashboard's pull can go now" git -C "$CHECKOUT" merge -q --ff-only origin/main
  end_case
}

case_tidy_after_an_interruption_other_changes_are_kept_aside() {
  new_case tidy_after_an_interruption_other_changes_are_kept_aside
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  printf 'neither version of it\n' >"$CHECKOUT/pyproject.toml"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the tree is clean again" eq "$(git -C "$CHECKOUT" status --porcelain --untracked-files=no)" ""
  local kept
  kept=$(ls -d "$ROOT"/var/lib/domovoi-provision/checkout-kept-* 2>/dev/null | head -n 1)
  check "a copy kept in root's state" test -n "$kept"
  check "with the file as it was" grep -qx 'neither version of it' "$kept/files/1"
  check "and its path in the MANIFEST" has_line "$kept/MANIFEST" "$(printf '1\tfile\tpyproject.toml')"
  check "root's only (0700)" called "install -d -m 0700 -o root -g root $kept $kept/files"
  check "read as the service user, bounded" grep -q "^runuser -u domovoi -- .* head -c 67108865 -- $CHECKOUT/pyproject.toml" "$STATE/calls.log"
  check "said, as a warning" said "were copied to $kept before they were put back"
  check "the dashboard's pull can go now" git -C "$CHECKOUT" merge -q --ff-only origin/main
  end_case
}

# The review's probe (2026-10-20): the service user, who can write the
# checkout's .git, puts a path with ".." behind a planted link into the
# commits the checkout's upstream names, plus a stale lock as the evidence
# that lets root copy "other changes" aside. Root must never write (or
# read, or remove) anything outside the checkout and its own kept dir.
case_tidy_a_path_out_of_the_checkout_is_never_touched() {
  new_case tidy_a_path_out_of_the_checkout_is_never_touched
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  local blob t c i
  blob=$(echo "planted by the service user" | git -C "$CHECKOUT" hash-object -w --stdin)
  t=$(printf '100644 blob %s\tf\n' "$blob" | git -C "$CHECKOUT" mktree)
  t=$(printf '040000 tree %s\tescaped\n' "$t" | git -C "$CHECKOUT" mktree)
  for i in 1 2 3 4; do t=$(printf '040000 tree %s\t..\n' "$t" | git -C "$CHECKOUT" mktree); done
  t=$(printf '040000 tree %s\ts\n' "$t" | git -C "$CHECKOUT" mktree)
  t=$( { git -C "$CHECKOUT" ls-tree HEAD; git -C "$CHECKOUT" ls-tree "$t"; } | git -C "$CHECKOUT" mktree)
  c=$(git -C "$CHECKOUT" commit-tree "$t" -p HEAD -m "B, planted")
  git -C "$CHECKOUT" update-ref refs/remotes/origin/main "$c"
  # The link, and the file it leads to: s/../../../../escaped/f is this.
  mkdir -p "$CASE/deep/a/b/c/d" "$CASE/deep/escaped"
  echo "content the service user chose" >"$CASE/deep/escaped/f"
  ln -s "$CASE/deep/a/b/c/d" "$CHECKOUT/s"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  # (The run then stops at the verified copy: the mirror's fsck refuses a
  # tree with a ".." entry. The tidy before it is what is tested here.)
  check "the stale lock still goes" absent "$CHECKOUT/.git/index.lock"
  # Where the copy-aside used to land: four levels above the kept dir.
  check "nothing written where the path led" absent "$ROOT/var/escaped/f"
  check "nor anywhere above the root" test -z "$(find "$CASE" -path "$CASE/deep" -prune -o -name f -path '*escaped*' -print)"
  check "the file it named is left alone" grep -qx "content the service user chose" "$CASE/deep/escaped/f"
  check "nothing kept aside for it" not_said "were copied to"
  check "the link stays" test -L "$CHECKOUT/s"
  end_case
}

case_tidy_waits_for_the_checkout_lock() {
  new_case tidy_waits_for_the_checkout_lock
  command -v flock >/dev/null 2>&1 || { skip_case "no flock(1) here"; return 0; }
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  local lock=$ROOT/var/lib/domovoi-update/checkout.lock holder t0
  check "the update dir is there" test -d "$ROOT/var/lib/domovoi-update"
  : >>"$lock"
  # A dashboard pull holding the checkout for three seconds.
  flock "$lock" sleep 3 &
  holder=$!
  sleep 0.5
  t0=$SECONDS
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  wait "$holder" 2>/dev/null
  check "exit 0" eq "$RC" 0
  check "it waited for the pull" test $((SECONDS - t0)) -ge 2
  check "on the checkout lock" called "flock -w 120 7"
  check "and let go after" flock -n "$lock" true
  end_case
}

# plant_upstream NAME CONTENT: origin/main becomes a commit on HEAD that adds
# the file NAME (any bytes but NUL and "/"-free parts) holding CONTENT.
plant_upstream() {
  local blob t c
  blob=$(printf '%s' "$2" | git -C "$CHECKOUT" hash-object -w --stdin)
  t=$( { git -C "$CHECKOUT" ls-tree -z HEAD; printf '100644 blob %s\t%s\0' "$blob" "$1"; } | git -C "$CHECKOUT" mktree -z)
  c=$(git -C "$CHECKOUT" commit-tree "$t" -p HEAD -m "B, adds a file")
  git -C "$CHECKOUT" update-ref refs/remotes/origin/main "$c"
}

# Review round 2, p2: an ignored directory with the household's files in
# it, at a path the update makes a file, after an interruption. It must
# stay, all of it (an rm -rf took it, with nothing kept).
case_tidy_an_ignored_directory_the_update_makes_a_file_stays() {
  new_case tidy_an_ignored_directory_the_update_makes_a_file_stays
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  plant_upstream data "file now"$'\n'
  mkdir -p "$CHECKOUT/data/sub"
  echo "household data, only copy" >"$CHECKOUT/data/sub/precious.txt"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the stale lock went" absent "$CHECKOUT/.git/index.lock"
  check "the directory stays, with its file" grep -qx "household data, only copy" "$CHECKOUT/data/sub/precious.txt"
  check "nothing removed recursively" not grep -q -- "rm -rf -- $CHECKOUT/data" "$STATE/calls.log"
  end_case
}

# p3: a name with a newline and tabs would forge a MANIFEST line.
case_tidy_a_name_with_control_bytes_is_never_used() {
  new_case tidy_a_name_with_control_bytes_is_never_used
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  local evil
  evil=$(printf 'evil\n7\tfile\trestore-me-into-etc-cron.d')
  plant_upstream "$evil" "x"$'\n'
  printf 'not from git\n' >"$CHECKOUT/$evil"
  old_lock "$CHECKOUT/.git/index.lock"
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "the file is left alone" grep -qx "not from git" "$CHECKOUT/$evil"
  check "no MANIFEST line it wrote" test -z "$(cat "$ROOT"/var/lib/domovoi-provision/checkout-kept-*/MANIFEST 2>/dev/null)"
  end_case
}

# The copy aside is bounded: a file over 64 MB (a sparse one here) is not
# copied, so it stays where it is, and nothing of it is left in root's dir.
case_tidy_a_huge_file_is_left_where_it_is() {
  new_case tidy_a_huge_file_is_left_where_it_is
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  truncate -s 100M "$CHECKOUT/pyproject.toml"
  old_lock "$CHECKOUT/.git/index.lock"
  local t0=$SECONDS
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "exit 0" eq "$RC" 0
  check "promptly" test $((SECONDS - t0)) -lt 60
  check "the file stays as it was" eq "$(stat -c %s "$CHECKOUT/pyproject.toml")" 104857600
  check "said why" said "too big to copy aside"
  check "no copy left in root's dir" test -z "$(find "$ROOT"/var/lib/domovoi-provision -path '*checkout-kept*/files/*' -type f 2>/dev/null)"
  check "read bounded, as the service user" grep -q "^runuser -u domovoi -- .* head -c 67108865 -- $CHECKOUT/pyproject.toml" "$STATE/calls.log"
  end_case
}

# A FIFO where a file of the update was: never read (it would never end),
# left where it is.
case_tidy_a_fifo_is_left_where_it_is() {
  new_case tidy_a_fifo_is_left_where_it_is
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A"
  check "first run exit 0" eq "$RC" 0
  rm -f "$CHECKOUT/pyproject.toml"
  mkfifo "$CHECKOUT/pyproject.toml"
  old_lock "$CHECKOUT/.git/index.lock"
  local t0=$SECONDS
  # Up to the checkout only: later steps read pyproject.toml themselves.
  run_prov "${wsl_args[@]}" --ref "$FIXTURE_A" --stop-before docker
  check "exit 0" eq "$RC" 0
  check "promptly" test $((SECONDS - t0)) -lt 60
  check "the FIFO stays" test -p "$CHECKOUT/pyproject.toml"
  check "never read" not grep -q "head -c .* -- $CHECKOUT/pyproject.toml" "$STATE/calls.log"
  end_case
}

CASES=(
  case_rerun_takes_nothing_from_the_working_tree
  case_rerun_refuses_an_unverified_head
  case_dry_run_with_a_checkout_changes_nothing
  case_help_and_usage_errors
  case_import_check_names_no_dropped_package
  case_whisper_download_imports_without_pyav
  case_lock_without_the_no_deps_lock_stops
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
  case_tidy_a_stale_lock_in_the_checkout_is_removed
  case_tidy_a_lock_a_running_git_holds_stays
  case_tidy_a_killed_fast_forward_is_put_back
  case_tidy_the_households_own_edit_stays
  case_tidy_a_file_cut_short_is_put_back
  case_tidy_after_an_interruption_other_changes_are_kept_aside
  case_tidy_a_path_out_of_the_checkout_is_never_touched
  case_tidy_waits_for_the_checkout_lock
  case_tidy_an_ignored_directory_the_update_makes_a_file_stays
  case_tidy_a_name_with_control_bytes_is_never_used
  case_tidy_a_huge_file_is_left_where_it_is
  case_tidy_a_fifo_is_left_where_it_is
  case_ref_sha_follows_the_track_branch
  case_ref_off_the_track_branch_stops
  case_new_install_follows_stable
  case_before_the_first_release_installs_follow_main
  case_a_release_on_stable_by_tag_or_sha
  case_a_commit_newer_than_stable_waits_for_the_next_release
  case_a_commit_off_main_never_follows_stable
  case_the_installer_before_the_first_release_follows_main
  case_ref_stable_without_one_stops
  case_a_branch_of_its_own_gets_no_channel_pin
  case_the_channel_pin_respects_the_administrator
  case_a_branch_named_like_the_pinned_commit_is_not_used
  case_a_branch_named_like_a_tag_is_not_used
  case_a_tag_that_moved_since_the_first_run_stops
  case_a_tag_not_named_v_wins_over_a_branch
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
  case_first_build_of_the_music_player_is_recorded
  case_repair_rerun_keeps_the_music_player_image
  case_repair_rerun_keeps_an_image_built_elsewhere_unvouched
  case_changed_dockerfile_rebuilds_records_and_moves_the_rooms
  case_missing_music_player_image_is_built_on_a_repair
  case_a_core_still_starting_is_restarted_after_a_rebuild
  case_a_stopped_core_is_left_stopped_after_a_rebuild
  case_apt_step_installs_the_stick_tool
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
  case_starts_do_not_block_on_ready
  case_a_unit_that_fails_after_a_no_block_start_stops_the_wait
  case_the_database_failing_after_a_no_block_start_stops_the_wait
  case_a_failed_database_says_why
  case_a_failed_database_without_a_container_says_so
  case_a_database_still_starting_is_named
  case_a_half_made_database_volume_is_made_again
  case_a_database_volume_with_data_is_left_alone
  case_a_first_start_still_running_is_left_alone
  case_a_half_made_volume_with_a_long_log_is_made_again
  case_a_database_volume_with_a_tablespace_is_left_alone
  case_a_paused_database_is_left_alone
  case_a_database_oid_past_bash_integers_counts
  case_a_running_database_is_not_checked
  case_a_failed_database_report_blanks_every_password
  case_no_database_volume_yet_is_left_alone
  case_a_failed_mark_from_before_is_not_this_starts
  case_journal_opt_out_is_honoured
  case_os_jobs_opt_out_is_honoured
  case_os_jobs_never_leaves_the_box_degraded
  case_vision_model_pull_is_optional
  case_vision_model_respects_the_household
  case_ai_models_left_out
  case_ai_models_by_default
  case_ai_models_turned_on_later
  case_ai_features_household_choice_kept
  case_ai_models_both_options_is_a_usage_error
  case_ai_question_at_a_terminal
  case_ai_models_dry_run_plans_both_answers
  case_ai_features_on_an_older_core
  case_ai_flag_over_household_off
  case_ai_off_rerun_without_option
  case_ai_turned_on_in_settings_after_no
  case_gpu_runtime_on_wsl
  case_gpu_runtime_needs_nvidias_libcuda_on_wsl
  case_gpu_runtime_skipped_on_bare_metal_without_a_driver
  case_gpu_runtime_opt_out_is_honoured
  case_gpu_runtime_on_bare_metal
  case_gpu_runtime_key_file_with_a_second_key_only_warns
  case_gpu_runtime_key_mismatch_only_warns
  case_gpu_runtime_refuses_a_driver_on_wsl
  case_gpu_runtime_dry_run_plans_it
  case_gpu_runtime_puts_daemon_json_back_when_docker_will_not_start
  case_gpu_runtime_helper_interface
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
