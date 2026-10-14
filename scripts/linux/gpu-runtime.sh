#!/usr/bin/env bash
# gpu-runtime.sh: Docker's way to the NVIDIA GPU (the NVIDIA Container
# Toolkit), set up only on a box where an NVIDIA GPU is visible. The
# wake-word trainer runs in a container and trains on the GPU through it
# (2026-10-13); without it `docker run --gpus all` fails and training falls
# back to the CPU.
#
#   sudo bash gpu-runtime.sh                     # set it up when a GPU is visible
#   sudo bash gpu-runtime.sh --internet never    # only what needs no download
#   bash gpu-runtime.sh --detect                 # status 0: an NVIDIA GPU is visible
#   bash gpu-runtime.sh --check                  # status 0: nothing to do
#
# Two scripts call it, as they call os-jobs.sh: scripts/linux/provision.sh
# (its gpu-runtime step, right after the docker step) and
# scripts/linux/apply-update.sh (on every update or restart that
# domovoi-update.service runs, while core and web are stopped). So a box
# installed before this existed gets it at its next update or Restart.
#
# An NVIDIA GPU is visible when:
#   WSL          /dev/dxg is there (Windows hands a GPU to the distro) and
#                so is /usr/lib/wsl/lib/libcuda.so.1 (Windows' NVIDIA driver
#                serves WSL). The driver is Windows': no Linux driver is ever
#                installed inside WSL.
#   bare metal   the NVIDIA driver's nvidia-smi is on PATH and `nvidia-smi -L`
#                lists at least one GPU. Installing the driver is the
#                administrator's job; this script never installs one.
# With none visible there is nothing to do, and nothing changes (a toolkit
# installed earlier stays as it is).
#
# What it does when one is visible:
#   apt          NVIDIA's apt repository, written the deb822 way to
#                /etc/apt/sources.list.d/nvidia-container-toolkit.sources (a
#                flat repository: "Suites: /" and no Components line, what
#                NVIDIA's one-line "deb [signed-by=...] <url> /" says), signed
#                by NVIDIA's key in /etc/apt/keyrings/nvidia-container-toolkit.asc.
#                The key is checked the way provision.sh checks Docker's: the
#                file NVIDIA serves goes into a throwaway keyring that must
#                then hold exactly one primary key, with the fingerprint
#                provision-manifest.conf pins, and only that key (gpg
#                --export) reaches /etc/apt/keyrings. Written when the
#                packages are to be installed, and kept current while that
#                source file is this script's. A toolkit that came some other
#                way keeps its own apt setup; another source for NVIDIA's
#                repository on a box without the toolkit is refused, since a
#                second one with another key would stop apt altogether
#                ("Conflicting values set for option Signed-By").
#   packages     nvidia-container-toolkit, nvidia-container-toolkit-base,
#                libnvidia-container-tools and libnvidia-container1, the ones
#                not installed yet, at the manifest's versions, with
#                --no-install-recommends (apt-get update first, and only
#                then). Not held: as with docker-ce, apt upgrade moves them
#                later, and a re-run never takes them back down. apt
#                simulates the install first; a driver, kernel or compute
#                package in it (nvidia-driver-*, nvidia-dkms-*,
#                nvidia-kernel-*, linux-modules-nvidia-*, libnvidia-compute-*,
#                nvidia-utils-*, ...) refuses the run, nothing installed.
#   docker       nvidia-ctk runtime configure --runtime=docker, which puts the
#                nvidia runtime in /etc/docker/daemon.json (the file as it
#                was is kept in /var/lib/domovoi-gpu-runtime/daemon.json.before),
#                then a Docker restart, only when that changed the file or the
#                running daemon does not list the nvidia runtime. A restart
#                stops every container for a moment: Domovoi's database and
#                the rooms' players are restart: unless-stopped, so they come
#                back, and systemd restarts domovoi-db.service with it
#                (Requires=docker.service). When Docker does not answer within
#                60 s after it, daemon.json goes back as it was (or goes, when
#                there was none), Docker is restarted again, and the run
#                fails saying so: never Docker left down by this script when
#                putting the file back brings it up.
# A box already set up is left alone: no download, no restart.
#
# Options:
#   --manifest FILE     the pin manifest (default: provision-manifest.conf
#                       beside this script); only its nvidia_* and
#                       libnvidia_* keys are read
#   --internet ANSWER   always | sometimes | never | "" (unanswered, the
#                       default). Under never nothing is downloaded: a toolkit
#                       not installed yet stays so (status 0), and only what
#                       needs no download is done (Docker's configuration)
#   --wsl yes|no|auto   WSL mode (default auto: the kernel says microsoft,
#                       the rule domovoi/host_kind.py uses)
#   --detect            only say whether an NVIDIA GPU is visible: status 0
#                       when one is, 1 when not
#   --check             change nothing; status 0 when there is nothing to do
#                       (set up already, or no GPU), 1 when there is
#   --dry-run           say what it would do; change nothing
#   --restart-flag FILE create FILE when this run restarts Docker (the update
#                       unit then restarts domovoi-db itself)
#   --no-restart        configure Docker but don't restart it; the summary
#                       says that a restart is needed
#
# Output: one line per thing it does, then one summary line that starts
# with "gpu-runtime: ", for example "gpu-runtime: already in place (...)",
# "gpu-runtime: NVIDIA Container Toolkit 1.20.1-1 installed, Docker
# configured and restarted", "gpu-runtime: no NVIDIA GPU visible (...);
# nothing to do", "gpu-runtime: failed: <why>" or "gpu-runtime: refused:
# <why>". Exit status: 0 done or nothing to do (--check: nothing to do;
# --detect: a GPU is visible), 1 failed or refused (--check: something to
# do; --detect: no GPU), 2 bad usage.
#
# DOMOVOI_PROVISION_ROOT with DOMOVOI_PROVISION_TEST_HARNESS=1 is for the
# tests only (scripts/linux/tests/test-provision.sh and
# test-apply-update.sh), exactly as in provision.sh: a directory put in
# front of every system path this reads or writes. Either one without the
# other makes it refuse to run.

set -euo pipefail
umask 022

PROG=gpu-runtime
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
export LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive
# Ubuntu's needrestart would otherwise restart services (docker among them)
# in the middle of the install, behind this script's back.
export NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1

# The four packages the toolkit is (the install of 2026-10-13 pulled exactly
# these, all 1.20.1-1; none depends on a driver). Each one's manifest key is
# its name with - as _.
PKGS=(nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1)
MANIFEST_KEYS=(nvidia_key_url nvidia_key_fpr nvidia_repo_url
  nvidia_container_toolkit nvidia_container_toolkit_base libnvidia_container_tools libnvidia_container1)
# What an install must never bring along: a driver, its kernel modules or
# its compute libraries. On WSL the driver is Windows' (a Linux one would
# break the GPU there); on bare metal it is the administrator's.
FORBIDDEN=('nvidia-driver*' 'nvidia-dkms*' 'nvidia-kernel*' 'nvidia-firmware*' 'nvidia-headless*'
  'nvidia-utils*' 'nvidia-compute-utils*' 'nvidia-open*' 'cuda-drivers*'
  'linux-modules-nvidia*' 'linux-objects-nvidia*' 'linux-signatures-nvidia*'
  'linux-image*' 'linux-headers*' 'linux-modules*'
  'libnvidia-compute*' 'libnvidia-gl*' 'libnvidia-decode*' 'libnvidia-encode*' 'libnvidia-cfg1*'
  'libnvidia-extra*' 'libnvidia-fbc1*' 'libnvidia-common*' 'xserver-xorg-video-nvidia*')

PROC=$ROOT/proc
DXG=$ROOT/dev/dxg
WSL_LIBCUDA=$ROOT/usr/lib/wsl/lib/libcuda.so.1
APT_ETC=$ROOT/etc/apt
# As apt reads it (Signed-By:): no harness root.
KEYRING_PATH=/etc/apt/keyrings/nvidia-container-toolkit.asc
KEYRING=$ROOT$KEYRING_PATH
SOURCES=$APT_ETC/sources.list.d/nvidia-container-toolkit.sources
DAEMON_JSON=$ROOT/etc/docker/daemon.json
STATE_DIR=$ROOT/var/lib/domovoi-gpu-runtime
DOCKER_UNIT=docker.service
# How long Docker may take to answer after a restart (the spike's took 15 s).
DOCKER_WAIT=60

MANIFEST=$SCRIPT_DIR/provision-manifest.conf
INTERNET=""
WSL_OPT=auto
DETECT=0
CHECK=0
DRY_RUN=0
FLAG=""
NO_RESTART=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
usage_error() { printf '%s: %s\n' "$PROG" "$1" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case $1 in
    --manifest) [ $# -ge 2 ] || usage_error "--manifest needs a file"; MANIFEST=$2; shift 2 ;;
    --manifest=*) MANIFEST=${1#*=}; shift ;;
    --internet) [ $# -ge 2 ] || usage_error "--internet needs a value (always, sometimes, never, or \"\")"; INTERNET=$2; shift 2 ;;
    --internet=*) INTERNET=${1#*=}; shift ;;
    --wsl) [ $# -ge 2 ] || usage_error "--wsl needs yes, no or auto"; WSL_OPT=$2; shift 2 ;;
    --wsl=*) WSL_OPT=${1#*=}; shift ;;
    --restart-flag) [ $# -ge 2 ] && [ -n "$2" ] || usage_error "--restart-flag needs a file"; FLAG=$2; shift 2 ;;
    --restart-flag=?*) FLAG=${1#*=}; shift ;;
    --detect) DETECT=1; shift ;;
    --check) CHECK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-restart) NO_RESTART=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage_error "unknown option: $1" ;;
  esac
done
case $INTERNET in ""|always|sometimes|never) ;; *) usage_error "--internet must be always, sometimes, never or empty: $INTERNET" ;; esac
[ $((DETECT + CHECK + DRY_RUN)) -le 1 ] || usage_error "--detect, --check and --dry-run go one at a time"

detect_wsl() {
  local f k
  for f in "$PROC/sys/kernel/osrelease" "$PROC/version"; do
    if k=$(cat "$f" 2>/dev/null); then
      case ${k,,} in *microsoft*) echo 1 ;; *) echo 0 ;; esac
      return 0
    fi
  done
  echo 0
}
case $WSL_OPT in
  yes) WSL=1 ;;
  no) WSL=0 ;;
  auto) WSL=$(detect_wsl) ;;
  *) usage_error "--wsl must be yes, no or auto: $WSL_OPT" ;;
esac

# ─── The summary line, on every way out ──────────────────────────────────

SAID=0
WORK=""
# finish CODE TEXT: the summary line, then exit CODE.
finish() { local code=$1; shift; printf '%s: %s\n' "$PROG" "$*"; SAID=1; exit "$code"; }
fail() { finish 1 "failed: $*"; }
on_exit() {
  local rc=$?
  if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
  # A command that failed where nothing expected it (set -e): still one
  # summary line, so a caller always has one to show.
  if [ "$SAID" = 0 ] && [ "$rc" != 0 ] && [ "$rc" != 2 ]; then
    printf '%s: failed: stopped unexpectedly (exit %s)\n' "$PROG" "$rc"
  fi
}
trap on_exit EXIT

where() { printf 'WSL: %s; internet: %s' "$([ "$WSL" = 1 ] && echo yes || echo no)" "${INTERNET:-unanswered}"; }

# ─── Is an NVIDIA GPU visible? ───────────────────────────────────────────

# Status 0 when one is; GPU_WHY says what was found, or what was missing.
GPU_WHY=""
gpu_visible() {
  local out n line names=""
  if [ "$WSL" = 1 ]; then
    if [ ! -e "$DXG" ]; then GPU_WHY="WSL: no /dev/dxg, so Windows hands this distro no GPU"; return 1; fi
    if [ ! -e "$WSL_LIBCUDA" ]; then
      GPU_WHY="WSL: /dev/dxg but no /usr/lib/wsl/lib/libcuda.so.1, so Windows has no NVIDIA driver serving WSL"
      return 1
    fi
    GPU_WHY="WSL: /dev/dxg and /usr/lib/wsl/lib/libcuda.so.1, Windows' NVIDIA driver"
    return 0
  fi
  if ! command -v nvidia-smi >/dev/null 2>&1; then GPU_WHY="no nvidia-smi: the NVIDIA driver is not installed"; return 1; fi
  if ! out=$(timeout 30 nvidia-smi -L 2>&1); then
    GPU_WHY="nvidia-smi -L failed: $(printf '%s\n' "$out" | head -n 1)"
    return 1
  fi
  n=$(printf '%s\n' "$out" | grep -c '^GPU ') || n=0
  if [ "$n" -eq 0 ]; then GPU_WHY="nvidia-smi -L lists no GPU"; return 1; fi
  # "GPU 0: NVIDIA GeForce RTX 4070 Ti SUPER (UUID: GPU-...)": the names.
  while IFS= read -r line; do
    line=${line#GPU }; line=${line#*: }; line=${line% (UUID:*}
    names+="${names:+, }$line"
  done < <(printf '%s\n' "$out" | grep '^GPU ')
  GPU_WHY="nvidia-smi: $names"
}

if [ "$DETECT" = 1 ]; then
  if gpu_visible; then finish 0 "NVIDIA GPU visible ($GPU_WHY)"; fi
  finish 1 "no NVIDIA GPU visible ($GPU_WHY)"
fi
if ! gpu_visible; then
  finish 0 "no NVIDIA GPU visible ($GPU_WHY); nothing to do"
fi
[ "$CHECK" = 1 ] || echo "NVIDIA GPU visible ($GPU_WHY)"

# ─── The pins ────────────────────────────────────────────────────────────

# Only this script's keys; every other line is provision.sh's business.
declare -A M=()
load_manifest() {
  local line key n=0 k
  [ -f "$MANIFEST" ] || fail "no pin manifest at $MANIFEST"
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    key=${line%%=*}
    case " ${MANIFEST_KEYS[*]} " in *" $key "*) ;; *) continue ;; esac
    [[ $line =~ ^[a-z][a-z0-9_]*=([^[:space:]]+)$ ]] || fail "$MANIFEST line $n is not key=value: $line"
    [ -z "${M[$key]+x}" ] || fail "$MANIFEST line $n: $key is set twice"
    M[$key]=${BASH_REMATCH[1]}
  done <"$MANIFEST"
  for k in "${MANIFEST_KEYS[@]}"; do
    [ -n "${M[$k]+x}" ] || fail "$MANIFEST: $k is missing"
  done
  [[ ${M[nvidia_key_fpr]} =~ ^[0-9A-F]{40}$ ]] || fail "$MANIFEST: nvidia_key_fpr is not a 40-hex-digit fingerprint"
  for k in nvidia_key_url nvidia_repo_url; do
    [[ ${M[$k]} =~ ^https://[A-Za-z0-9./_-]+$ ]] || fail "$MANIFEST: $k is not an https URL"
  done
  for k in nvidia_container_toolkit nvidia_container_toolkit_base libnvidia_container_tools libnvidia_container1; do
    [[ ${M[$k]} =~ ^([0-9]+:)?[0-9][A-Za-z0-9.+~-]*$ ]] || fail "$MANIFEST: $k is not a Debian version"
  done
}
load_manifest

# ─── What is there ───────────────────────────────────────────────────────

# The packages not installed yet, as pkg=<the manifest's version>. One that
# is installed counts at whatever version it is (not held; apt upgrade
# moves it), as provision.sh counts docker-ce.
pkgs_missing() {
  local have p
  have=$(dpkg-query -W -f='${Package} ${Status}\n' "${PKGS[@]}" 2>/dev/null | awk '/ install ok installed$/ {print $1}') || true
  for p in "${PKGS[@]}"; do
    printf '%s\n' "$have" | grep -qxF -- "$p" || printf '%s=%s\n' "$p" "${M[${p//-/_}]}"
  done
}
toolkit_version() {
  dpkg-query -W -f='${Package}=${Version}\n' nvidia-container-toolkit 2>/dev/null | sed -n 's/^nvidia-container-toolkit=//p' | head -n 1 || true
}

sources_text() {
  cat <<EOF
Types: deb
URIs: ${M[nvidia_repo_url]}
Suites: /
Architectures: amd64
Signed-By: $KEYRING_PATH
EOF
}

# key_check FILE [DEST]: FILE, read into a throwaway keyring, holds exactly
# one primary key and it is the pinned one (KEY_PROBLEM says why not). With
# DEST, that key alone goes there, armored (gpg --export of the pinned
# fingerprint): apt trusts every key in the file it is given, so no other
# key in a download ever reaches /etc/apt/keyrings.
KEY_PROBLEM=""
key_check() {
  local src=$1 dest=${2-} gh listing n fpr rc=0
  KEY_PROBLEM=""
  command -v gpg >/dev/null 2>&1 || { KEY_PROBLEM="gpg is not installed (it checks NVIDIA's key before apt trusts it)"; return 1; }
  gh=$(mktemp -d) || { KEY_PROBLEM="can't make a temporary keyring"; return 1; }
  if ! GNUPGHOME=$gh gpg --batch --no-tty --quiet --import "$src" >/dev/null 2>&1; then
    rm -rf "$gh"
    KEY_PROBLEM="${src#"$ROOT"} is not a key gpg can read"
    return 1
  fi
  # Read to the end (no early-quitting reader under pipefail).
  listing=$(GNUPGHOME=$gh gpg --batch --no-tty --with-colons --fixed-list-mode --list-keys 2>/dev/null) || listing=""
  n=$(printf '%s\n' "$listing" | awk -F: '$1=="pub" {n++} END {print n+0}')
  fpr=$(printf '%s\n' "$listing" | awk -F: '$1=="pub" {want=1; next} want && $1=="fpr" {print $10; want=0}')
  if [ "$n" != 1 ]; then
    KEY_PROBLEM="it holds $n primary keys; apt would trust every one, and only ${M[nvidia_key_fpr]} is pinned"; rc=1
  elif [ "$fpr" != "${M[nvidia_key_fpr]}" ]; then
    KEY_PROBLEM="its key has fingerprint '${fpr:-none}', the manifest pins ${M[nvidia_key_fpr]}"; rc=1
  elif [ -n "$dest" ]; then
    GNUPGHOME=$gh gpg --batch --no-tty --armor --export "${M[nvidia_key_fpr]}" >"$dest" 2>/dev/null || : >"$dest"
    [ -s "$dest" ] || { KEY_PROBLEM="gpg exported nothing for ${M[nvidia_key_fpr]}"; rc=1; }
  fi
  if command -v gpgconf >/dev/null 2>&1; then GNUPGHOME=$gh gpgconf --kill all >/dev/null 2>&1 || true; fi
  rm -rf "$gh"
  return "$rc"
}

# This script's source file and key are as the manifest says. A box whose
# source file is not this script's (none there) has nothing to keep current.
apt_current() {
  [ -f "$SOURCES" ] || return 0
  sources_text | cmp -s - "$SOURCES" && [ -f "$KEYRING" ] && key_check "$KEYRING"
}

# Other apt sources for NVIDIA's repository (its own one-line install
# writes nvidia-container-toolkit.list), by the path apt reads.
foreign_sources() {
  local f
  for f in "$APT_ETC/sources.list" "$APT_ETC"/sources.list.d/*.list "$APT_ETC"/sources.list.d/*.sources; do
    [ -f "$f" ] && [ "$f" != "$SOURCES" ] || continue
    if grep -qE '^[^#]*nvidia\.github\.io/libnvidia-container' "$f" 2>/dev/null; then printf '%s\n' "${f#"$ROOT"}"; fi
  done
}

daemon_has_nvidia() { [ -f "$DAEMON_JSON" ] && grep -q '"nvidia"' "$DAEMON_JSON"; }

# The running daemon lists the nvidia runtime (it read daemon.json since).
runtime_loaded() {
  local out
  command -v docker >/dev/null 2>&1 || return 1
  out=$(timeout 20 docker info --format '{{range $k, $v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null) || return 1
  [[ " $out " == *" nvidia "* ]]
}

systemd_running() { [ "$(cat "$PROC/1/comm" 2>/dev/null)" = systemd ]; }

MISSING=$(pkgs_missing)
APT_OK=1; apt_current || APT_OK=0
DAEMON_OK=1; daemon_has_nvidia || DAEMON_OK=0
LOADED_OK=1; runtime_loaded || LOADED_OK=0

if [ -z "$MISSING" ] && [ "$APT_OK" = 1 ] && [ "$DAEMON_OK" = 1 ] && [ "$LOADED_OK" = 1 ]; then
  finish 0 "already in place (NVIDIA Container Toolkit $(toolkit_version); Docker has the nvidia runtime; $(where))"
fi

if [ "$CHECK" = 1 ]; then
  needs=()
  [ -z "$MISSING" ] || needs+=("install $(printf '%s\n' "$MISSING" | tr '\n' ' ' | sed 's/ $//')")
  [ "$APT_OK" = 1 ] || needs+=("refresh NVIDIA's apt key and source")
  [ "$DAEMON_OK" = 1 ] || needs+=("put the nvidia runtime in /etc/docker/daemon.json")
  [ "$LOADED_OK" = 1 ] || needs+=("restart Docker to load the nvidia runtime")
  finish 1 "not in place: $(IFS=';'; printf '%s' "${needs[*]}" | sed 's/;/; /g') ($(where))"
fi

if [ "$DRY_RUN" = 1 ]; then
  if [ -n "$MISSING" ]; then
    if [ "$INTERNET" = never ]; then
      echo "would install nothing: the NVIDIA Container Toolkit is a download, and internet access is turned off"
    else
      [ -f "$SOURCES" ] && [ "$APT_OK" = 1 ] || echo "would check NVIDIA's key (exactly one primary key, ${M[nvidia_key_fpr]}) and write ${KEYRING_PATH} and ${SOURCES#"$ROOT"}"
      echo "would apt-get install --no-install-recommends $(printf '%s\n' "$MISSING" | tr '\n' ' ')(after a simulated install shows no driver package)"
    fi
  elif [ "$APT_OK" = 0 ]; then
    echo "would refresh NVIDIA's apt key and source"
  fi
  if [ -z "$MISSING" ] || [ "$INTERNET" != never ]; then
    [ "$DAEMON_OK" = 1 ] || echo "would run nvidia-ctk runtime configure --runtime=docker"
    [ "$LOADED_OK" = 1 ] && [ "$DAEMON_OK" = 1 ] || echo "would restart Docker to load the nvidia runtime"
  fi
  finish 0 "dry run, nothing changed ($GPU_WHY; $(where))"
fi

if [ -z "$ROOT" ] && [ "$(id -u)" != 0 ]; then
  fail "run it as root: it installs packages and restarts Docker"
fi
command -v docker >/dev/null 2>&1 || fail "Docker Engine is not installed (scripts/linux/provision.sh installs it before this)"

WORK=$(mktemp -d)

# write_if_changed DEST < CONTENT: DEST (0644, root's: this runs as root) by
# rename from a dot-named temp file beside it. Status 0 when it wrote, 1
# when DEST already held it.
write_if_changed() {
  local dest=$1 dir tmp
  dir=$(dirname "$dest")
  mkdir -p "$dir" || fail "can't create ${dir#"$ROOT"}"
  tmp=$(mktemp "$dir/.$(basename "$dest").XXXXXX") || fail "can't write in ${dir#"$ROOT"}"
  if ! cat >"$tmp"; then rm -f "$tmp"; fail "can't write ${tmp#"$ROOT"}"; fi
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    return 1
  fi
  chmod 0644 "$tmp" && mv -f "$tmp" "$dest" || { rm -f "$tmp"; fail "can't install ${dest#"$ROOT"}"; }
  return 0
}

# NVIDIA's key, checked, then the source that names it (in that order: the
# source never points at a key that isn't there).
write_apt_files() {
  curl -fsSL --retry 3 -o "$WORK/nvidia-download.asc" "${M[nvidia_key_url]}" \
    || fail "could not download NVIDIA's apt key from ${M[nvidia_key_url]}"
  key_check "$WORK/nvidia-download.asc" "$WORK/nvidia.asc" \
    || fail "NVIDIA's apt key from ${M[nvidia_key_url]}: $KEY_PROBLEM"
  if write_if_changed "$KEYRING" <"$WORK/nvidia.asc"; then echo "wrote $KEYRING_PATH (NVIDIA's key alone, ${M[nvidia_key_fpr]})"; fi
  if write_if_changed "$SOURCES" < <(sources_text); then echo "wrote ${SOURCES#"$ROOT"} (${M[nvidia_repo_url]} /)"; fi
}

apt_get() { apt-get -o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=300 "$@"; }

forbidden() {
  local p
  for p in "${FORBIDDEN[@]}"; do
    # shellcheck disable=SC2053  # a glob on purpose
    [[ $1 == $p ]] && return 0
  done
  return 1
}

INSTALLED=0
APT_REFRESHED=0
APT_SKIPPED=0
if [ -n "$MISSING" ]; then
  if [ "$INTERNET" = never ]; then
    finish 0 "not installed: the NVIDIA Container Toolkit is a download and internet access is turned off, so nothing changed ($GPU_WHY; $(where))"
  fi
  if [ ! -f "$SOURCES" ]; then
    other=$(foreign_sources | tr '\n' ' ' | sed 's/ $//')
    if [ -n "$other" ]; then
      finish 1 "refused: NVIDIA's apt repository is already set up in $other, not by Domovoi; a second source for it with another key would stop apt, so nothing was installed (install nvidia-container-toolkit from that source yourself, or remove it and run this again)"
    fi
  fi
  if [ "$APT_OK" = 0 ] || [ ! -f "$SOURCES" ]; then write_apt_files; fi
  apt_get -q update || fail "apt-get update failed"
  # shellcheck disable=SC2086  # pkg=ver words, no blanks in them
  if ! sim=$(LC_ALL=C apt_get install -s --no-install-recommends $MISSING 2>&1); then
    # apt's own words go to the log; the summary (shown on the dashboard's
    # Version card) stays this script's.
    printf '%s\n' "$sim" | grep '^E:' | head -n 3 || true
    fail "apt can't install $(printf '%s\n' "$MISSING" | tr '\n' ' ' | sed 's/ $//') (apt's reason is in the lines above)"
  fi
  bad=()
  while IFS= read -r p; do
    if [ -n "$p" ] && forbidden "$p"; then bad+=("$p"); fi
  done < <(printf '%s\n' "$sim" | awk '$1 == "Inst" {print $2}')
  if [ "${#bad[@]}" -gt 0 ]; then
    finish 1 "refused: installing the NVIDIA Container Toolkit would also install ${bad[*]}; Domovoi never installs a GPU driver, kernel or compute package (the driver is $([ "$WSL" = 1 ] && echo "Windows' on WSL" || echo "the administrator's")), so nothing was installed"
  fi
  # shellcheck disable=SC2086
  apt_get -q install -y --no-install-recommends $MISSING || fail "apt-get install of $(printf '%s\n' "$MISSING" | tr '\n' ' ' | sed 's/ $//') failed"
  [ -z "$(pkgs_missing)" ] || fail "packages missing after the install: $(pkgs_missing | tr '\n' ' ' | sed 's/ $//')"
  INSTALLED=1
  echo "installed $(printf '%s\n' "$MISSING" | tr '\n' ' ' | sed 's/ $//') (not held: apt upgrade moves them, as it does docker-ce)"
elif [ "$APT_OK" = 0 ]; then
  if [ "$INTERNET" = never ]; then
    APT_SKIPPED=1
    echo "NVIDIA's apt key and source are not refreshed: that is a download, and internet access is turned off"
  else
    write_apt_files
    APT_REFRESHED=1
  fi
fi
VER=$(toolkit_version)

# ─── Docker ──────────────────────────────────────────────────────────────

command -v nvidia-ctk >/dev/null 2>&1 || fail "nvidia-ctk is missing although nvidia-container-toolkit is installed"

HAD_DAEMON=0
BACKUP=$WORK/daemon.json.before
restore_daemon_json() {
  if [ "$HAD_DAEMON" = 1 ]; then
    cp -p "$BACKUP" "$DAEMON_JSON.domovoi-restore" && mv -f "$DAEMON_JSON.domovoi-restore" "$DAEMON_JSON"
  else
    rm -f "$DAEMON_JSON"
  fi
}

if [ -f "$DAEMON_JSON" ]; then
  HAD_DAEMON=1
  cp -p "$DAEMON_JSON" "$BACKUP" || fail "can't back up /etc/docker/daemon.json"
fi
mkdir -p "$(dirname "$DAEMON_JSON")" || fail "can't create /etc/docker"
# The configured path given explicitly: it is Docker's default one, and the
# tests' root goes in front of it.
if ! out=$(nvidia-ctk runtime configure --runtime=docker --config="$DAEMON_JSON" 2>&1); then
  restore_daemon_json || true
  printf '%s\n' "$out" | tail -n 3
  fail "nvidia-ctk runtime configure --runtime=docker failed, /etc/docker/daemon.json left as it was (its output is in the lines above)"
fi
if [ -n "$out" ]; then printf '%s\n' "$out"; fi
CHANGED=0
if [ "$HAD_DAEMON" = 1 ]; then cmp -s "$BACKUP" "$DAEMON_JSON" || CHANGED=1
elif [ -f "$DAEMON_JSON" ]; then CHANGED=1; fi
daemon_has_nvidia || { restore_daemon_json || true; fail "nvidia-ctk left no nvidia runtime in /etc/docker/daemon.json; the file is back as it was"; }
if [ "$CHANGED" = 1 ]; then
  if [ "$HAD_DAEMON" = 1 ]; then
    mkdir -p "$STATE_DIR" && cp -p "$BACKUP" "$STATE_DIR/daemon.json.before" \
      && echo "configured Docker: the nvidia runtime is in /etc/docker/daemon.json (the file as it was: ${STATE_DIR#"$ROOT"}/daemon.json.before)"
  else
    echo "configured Docker: /etc/docker/daemon.json written with the nvidia runtime (there was none)"
  fi
fi

# Up: active, and answering.
docker_up() {
  [ "$(systemctl is-active "$DOCKER_UNIT" 2>/dev/null || true)" = active ] && timeout 20 docker info >/dev/null 2>&1
}
# restart_docker: status 0 when Docker answers within DOCKER_WAIT seconds.
# A restart that failed and left the unit down is not waited for.
restart_docker() {
  local rc=0 t=0
  if [ -n "$FLAG" ]; then : >"$FLAG" 2>/dev/null || echo "could not create $FLAG"; fi
  systemctl restart "$DOCKER_UNIT" || rc=$?
  while :; do
    docker_up && return 0
    if [ "$rc" != 0 ]; then
      case $(systemctl is-active "$DOCKER_UNIT" 2>/dev/null || true) in failed|inactive|"") return 1 ;; esac
    fi
    [ "$t" -lt "$DOCKER_WAIT" ] || return 1
    sleep 2
    t=$((t + 2))
  done
}

RESTARTED=0
RESTART_NOTE=""
if [ "$CHANGED" = 1 ] || ! runtime_loaded; then
  if [ "$NO_RESTART" = 1 ]; then
    RESTART_NOTE="Docker not restarted (--no-restart): restart it to load the nvidia runtime"
  elif ! systemd_running; then
    RESTART_NOTE="Docker not restarted (systemd is not running here): restart it to load the nvidia runtime"
  else
    echo "restarting Docker to load the nvidia runtime (its containers stop for a moment; restart: unless-stopped brings them back)"
    if ! restart_docker; then
      echo "Docker did not answer within ${DOCKER_WAIT}s with the nvidia runtime configured; putting /etc/docker/daemon.json back as it was"
      restore_daemon_json || echo "could not put /etc/docker/daemon.json back"
      systemctl reset-failed "$DOCKER_UNIT" docker.socket >/dev/null 2>&1 || true
      if restart_docker; then
        fail "Docker did not start with the nvidia runtime configured; /etc/docker/daemon.json is back as it was and Docker runs again (journalctl -u docker.service says why)"
      fi
      fail "Docker did not start with the nvidia runtime configured, nor after /etc/docker/daemon.json was put back: Docker is down (systemctl status docker.service; journalctl -u docker.service)"
    fi
    RESTARTED=1
    runtime_loaded || fail "Docker restarted but does not list the nvidia runtime (docker info); /etc/docker/daemon.json has it"
  fi
fi

# ─── The summary ─────────────────────────────────────────────────────────

what=()
[ "$INSTALLED" = 0 ] || what+=("NVIDIA Container Toolkit $VER installed")
[ "$APT_REFRESHED" = 0 ] || what+=("NVIDIA's apt key and source refreshed")
docker_part=""
if [ "$RESTARTED" = 1 ]; then
  if [ "$CHANGED" = 1 ]; then docker_part="Docker configured and restarted"
  else docker_part="Docker restarted to load the nvidia runtime"; fi
elif [ -n "$RESTART_NOTE" ]; then
  docker_part="${RESTART_NOTE}"
  [ "$CHANGED" = 0 ] || docker_part="Docker configured; $RESTART_NOTE"
elif [ "$CHANGED" = 1 ]; then
  docker_part="Docker configured (it already ran the nvidia runtime)"
fi
[ -z "$docker_part" ] || what+=("$docker_part")
if [ "${#what[@]}" -eq 0 ]; then
  note=""
  [ "$APT_SKIPPED" = 0 ] || note="; NVIDIA's apt key and source not refreshed: internet access is turned off"
  finish 0 "already in place (NVIDIA Container Toolkit $VER; Docker has the nvidia runtime; $(where))$note"
fi
summary=$(IFS='|'; printf '%s' "${what[*]}" | sed 's/|/, /g')
[ "$APT_SKIPPED" = 0 ] || summary+="; NVIDIA's apt key and source not refreshed: internet access is turned off"
finish 0 "$summary ($(where))"
