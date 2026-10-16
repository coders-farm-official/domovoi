#!/usr/bin/env bash
# provision.sh: take a fresh Ubuntu 26.04 box to a running Domovoi server,
# unattended. The same script builds a bare-metal Linux host (the Beelink
# procedure in docs/LINUX_HOST.md) and the private WSL2 distro the Windows
# installer imports from Ubuntu's official .wsl image.
#
#   sudo bash provision.sh --internet always --timezone Europe/Berlin   # bare metal
#   sudo bash provision.sh --internet never \
#       --media music='C:\Users\Public\Domovoi\Music'          # inside WSL
#   sudo bash provision.sh --dry-run ...                      # say what it would do
#
# What it does, in order (each is a step with an id, see STEP_IDS):
#   preflight    root, Ubuntu x86_64, systemd as PID 1, waits for cloud-init
#   apt          the LINUX_HOST.md "Install" prerequisites (+ curl, gnupg,
#                zstd, tzdata, sudo, which this script needs)
#   user         the service user (default domovoi): created with password
#                login locked, or an existing one left as it is (an
#                existing account in sudo, admin or wheel is refused)
#   wsl-config   WSL only: /etc/wsl.conf (systemd, default user, interop off,
#                no automount, fstab on) and the units MS Learn says to mask
#   media        --media folders: on WSL one drvfs line per folder in
#                /etc/fstab, mounted at /srv/media/<name>; on bare metal the
#                directories themselves
#   checkout     git clone of --repo-url at --ref (default: stable, the
#                latest release, which the checkout then follows; main
#                while the repository has no stable branch yet), as the
#                service user, into
#                a directory beside --checkout-dir that is renamed into place
#                only once the ref is checked out (and HEAD verified when
#                /etc/domovoi/allowed_signers exists: docs/LINUX_HOST.md
#                "Signed updates"); an existing checkout is never moved
#   docker       Docker's key (exactly one key, the manifest's fingerprint)
#                and apt repository; docker-ce at the manifest's versions
#                when it is not installed yet (not held: apt upgrade moves
#                it later, as on the Beelink); the service user in the
#                docker group
#   gpu-runtime  only where an NVIDIA GPU is visible (WSL: /dev/dxg and
#                /usr/lib/wsl/lib/libcuda.so.1; bare metal: the driver's
#                nvidia-smi lists one): scripts/linux/gpu-runtime.sh adds
#                NVIDIA's apt repository (its key checked like Docker's), the
#                NVIDIA Container Toolkit at the manifest's versions (not
#                held) and the nvidia runtime in Docker's daemon.json, then
#                restarts Docker, so containers (the wake-word trainer) can
#                use the GPU. Never a driver. The update unit runs the same
#                helper, so a box installed before it gets it at its next
#                update. DOMOVOI_MANAGE_GPU_RUNTIME=0 in
#                /etc/default/domovoi-update skips it
#   venv         the venv and the three hash-checked installs from
#                requirements-linux-py314.lock (LINUX_HOST.md "Install")
#   env          domovoi/.env: python -m domovoi.env_bootstrap [--internet],
#                then the hardware tier's keys (its vision model included),
#                the WSL keys and the media paths, through the repo's own
#                .env writer. A key is
#                written only when it is absent or still holds what this
#                script last wrote there (kept in
#                /var/lib/domovoi-provision/env-written); one the household
#                changed (Settings, or by hand) is left alone, with a warning
#   ollama       the Ollama release LINUX_HOST.md pins (version and SHA-256
#                read from that file), its systemd unit as the doc gives it.
#                Ollama runs on every GPU it finds (inside WSL too: WSL
#                hands NVIDIA cards through) and on the CPU without one; no
#                CUDA_VISIBLE_DEVICES pin. The GPUs it found go in the final
#                line ("gpu"); the tier stays a question of memory alone
#   models       the tier's two Ollama models and its vision model (pictures
#                in chat, about 1.9 GB; --no-vision-model: not pulled), each
#                checked against the manifest digest after the pull
#                                                          (--skip-models: no)
#   whisper      the Whisper model at the manifest's Hugging Face revision,
#                model.bin checked against its SHA-256; the small files
#                beside it (config.json, tokenizer.json, vocabulary.*) come
#                from that revision but are not hashed  (--skip-models: no)
#   piper        the three Piper voices (SHIPPED_VOICES), each checked against
#                PIPER_PINS; a configured voice outside them is left to the core
#   images       the database images the compose file pins, and docker build of
#                domovoi-mpd exactly as the core builds it
#   searxng      the search helper, started for always/sometimes, off for never
#   os-jobs      Ubuntu's own background jobs follow the internet answer:
#                scripts/linux/os-jobs.sh masks the updaters, news and
#                crash-report timers for never and puts them back for
#                always/sometimes; on WSL it disables and masks wsl-pro and
#                snapd whatever the answer. The update unit runs the same
#                helper after every healthy restart or update, so an answer
#                changed later in Settings follows there
#   units        the three shipped units from scripts/linux/units/, the
#                restart sudoers grant, sleep targets masked, and the
#                journal kept on disk (scripts/linux/units/journald-domovoi.conf
#                as /etc/systemd/journald.conf.d/50-domovoi.conf, then
#                systemd-journald restarted; DOMOVOI_MANAGE_JOURNAL=0 in
#                /etc/default/domovoi-update leaves the journal alone)
#   timezone     bare metal: --timezone, through timedatectl. WSL: the zone
#                follows Windows (WSL rewrites /etc/localtime and
#                /etc/timezone at every distro start), so --timezone is
#                ignored with a warning; the step only checks that the two
#                files name the same zone, and when they don't writes
#                /etc/timezone from /etc/localtime and restarts core and web
#   start        enable and start domovoi-db, domovoi-core, domovoi-web; wait
#                for /v1/health and /api/health
#   update-unit  scripts/linux/install-update-unit.sh (the dashboard's update
#                button), with DOMOVOI_USE_LOCK=1 in
#                /etc/default/domovoi-update: a box installed from the
#                hash-pinned lock re-syncs from it on every update too; and
#                DOMOVOI_UPSTREAM_BRANCH="stable main" there, so updates
#                follow one of the two release channels and nothing else
#   finish       result, setup-code presence (never its content), the GPUs
#
# Nothing of the checkout runs or is read as root but its commit's own
# files: right after the checkout step, every run (a re-run too, when that
# step is skipped), HEAD is fetched into the update unit's root-owned mirror
# (/var/lib/domovoi-update/mirror.git, every object checked), its signature
# verified there while /etc/domovoi/allowed_signers exists, and
# scripts/linux and docs/LINUX_HOST.md written out of it into
# /run/domovoi-provision/<sha> (plain files, root's; removed when the run
# ends). The pin manifest, the Ollama pin, gpu-runtime.sh, os-jobs.sh, the
# units, the journald drop-in and install-update-unit.sh are read from
# there (delta security review 2026-10-16, S4: on a re-run the working
# tree is the service user's, and so is every edit in it). The code that
# makes the copy is the verified-copy block below, the same text as
# scripts/linux/update-verifier.sh's, the update unit's verifier.
#
# Idempotent and resumable. Each finished step leaves a marker in
# /var/lib/domovoi-provision/steps/<id> holding a hash of that step's inputs
# (options, pins, the repo files it installs from). A step is skipped when
# its marker matches and a quick check says its result is still there; a
# killed run starts again at the first step without a matching marker, and
# a changed input re-runs only the steps it feeds. A second run on a
# finished box skips every step and restarts nothing.
#
# Machine-readable progress for the Windows installer's UI: every line on
# stdout that starts with "@@DOMOVOI " is one JSON object:
#   {"v":1,"step":"venv","index":9,"total":22,"state":"start","percent":25,
#    "message":"Installing Python packages"}
# state is start, progress, done, skip or fail (plan in a dry run); the
# percent never goes down; progress lines of a download add "bytes" and
# "bytes_total" (null when the size is unknown). Every run ends with exactly
# one line that has "step":"finish", "final":true and "result" (ok, failed,
# rerun_after_restart, dry_run, usage_error, busy, help, or stopped: a run
# given --stop-before STEP that got there, exit 0, STEP not run),
# "restart_required" (WSL: /etc/wsl.conf changed and takes effect only after
# `wsl --terminate <distro>`; this script never restarts the distro itself),
# "exit_code", "failed_step", "message" (why, for anything but ok) and
# "gpu": the names of the GPUs Ollama found at its last start, a JSON list,
# [] when it runs on the CPU alone, null when that is unknown (no Ollama
# start in this boot's journal yet, or a run that stopped before it).
# Only a SIGKILL leaves no final line. Everything else on stdout and stderr
# is for people. A run that got past the run lock keeps the same final
# object in /var/lib/domovoi-provision/result.json (a refused one leaves
# that file to the run that holds the lock).
#
# Exit codes: 0 done (or dry run, --help, or stopped before the step
# --stop-before named), 1 a step failed (or not
# root), 2 bad usage, 3 WSL without systemd as PID 1: /etc/wsl.conf was
# written, terminate the distro and run this again, 4 another run of this
# script holds the run lock (busy): wait for it, then run this again.
#
# Pins. Whatever the repository pins is read from the checkout at the commit
# being installed, never copied: the lock, the compose images, the MPD
# image's base, PIPER_PINS, the Ollama release in docs/LINUX_HOST.md. The
# few with no home in the repo (docker-ce versions and key, the NVIDIA
# Container Toolkit's versions and key, the Whisper revision and model.bin
# SHA-256, the tier models and their digests) are in
# scripts/linux/provision-manifest.conf, read from the same checkout.
# Checked against a pin, and a mismatch stops the run: every Python package
# (the lock's hashes), the Ollama tarball (SHA-256), Docker's apt key and
# NVIDIA's (fingerprints; apt then checks the packages against the index
# that key signs), the compose images and the MPD image's base (digests),
# each Ollama model (digest),
# Whisper's model.bin and the Piper voice (SHA-256). Not hashed by this
# script: Ubuntu's own packages (apt checks them against Ubuntu's signed
# archive, at whatever version it serves), what Dockerfile.mpd installs
# with apt inside the image build, and Whisper's small files beside
# model.bin (config.json, tokenizer.json, vocabulary.*), which come from
# the pinned Hugging Face revision over HTTPS.
#
# DOMOVOI_PROVISION_ROOT is for the tests only
# (scripts/linux/tests/test-provision.sh): a directory put in front of every
# system path this reads or writes (/etc, /var, /proc, /usr/local, ...). It
# takes effect only together with DOMOVOI_PROVISION_TEST_HARNESS=1; either one
# without the other makes the script refuse to run. Never set them on a host.

set -Eeuo pipefail
umask 022

PROG=provision
PROGRESS_PREFIX='@@DOMOVOI '
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# ─── Output and the final line ───────────────────────────────────────────
# First of all, so that every way out of this script (a usage error, a
# refused harness variable, not root, the run lock) ends with one final
# progress line.

log() { printf '%s: %s\n' "$PROG" "$*"; }
WARNINGS=0
warn() { WARNINGS=$((WARNINGS + 1)); printf '%s: WARNING: %s\n' "$PROG" "$*"; }

json_str() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  s=$(printf '%s' "$s" | LC_ALL=C tr -d '\000-\010\013\014\016-\037\177')
  printf '"%s"' "$s"
}

STEP_IDS=(preflight apt user wsl-config media checkout docker gpu-runtime venv env ollama models whisper piper images searxng os-jobs units timezone start update-unit finish)
declare -A STEP_WEIGHT=(
  [preflight]=1 [apt]=6 [user]=1 [wsl-config]=1 [media]=1 [checkout]=3 [docker]=6 [gpu-runtime]=1
  [venv]=15 [env]=1 [ollama]=8 [models]=28 [whisper]=4 [piper]=1 [images]=6
  [searxng]=4 [os-jobs]=1 [units]=1 [timezone]=1 [start]=6 [update-unit]=2 [finish]=2
)
declare -A STEP_TITLE=(
  [preflight]="Checking this machine"
  [apt]="Installing system packages"
  [user]="Creating the service user"
  [wsl-config]="Configuring the WSL distro"
  [media]="Connecting the media folders"
  [checkout]="Getting the Domovoi code"
  [docker]="Installing Docker Engine"
  [gpu-runtime]="Setting up the GPU for containers"
  [venv]="Installing Python packages"
  [env]="Writing the settings file"
  [ollama]="Installing Ollama"
  [models]="Downloading the AI models"
  [whisper]="Downloading the speech recognition model"
  [piper]="Downloading the voice"
  [images]="Getting the container images"
  [searxng]="Setting up the search helper"
  [os-jobs]="Setting Ubuntu's background jobs"
  [units]="Installing the services"
  [timezone]="Setting the time zone"
  [start]="Starting Domovoi"
  [update-unit]="Installing the updater"
  [finish]="Finishing"
)
STEP_TOTAL=${#STEP_IDS[@]}
STEP_ID=""
STEP_INDEX=0
STEP_W=0
CUM_PCT=0
LAST_PCT=0
FINAL_EMITTED=0
FAILED_STEP=""
RESTART_REQUIRED=false
LAST_ERR=""
WATCH_PID=""
LOCK_PID=""
CLONE_TMP_ACTIVE=""
COPY_ACTIVE=""
TMP_DIR=""
DRY_RUN=0
WSL=0
TIER_EFF=""
HEAD_SHA=""
# The GPUs Ollama found (detect_gpus): a JSON list of names, [] for the
# CPU alone, null while unknown.
GPU_JSON=null
# 1 once this run is past its checks and holds the run lock: only then is
# result.json its to write, and the restart question worth asking.
STARTED=0
DASHBOARD_URL=http://localhost:6369

# emit STATE MESSAGE [EXTRA]: one progress line. EXTRA is more JSON fields,
# already encoded, without the surrounding braces.
emit() {
  local state=$1 msg=$2 extra=${3-} pct
  case $state in
    done|skip) pct=$((CUM_PCT + STEP_W)) ;;
    progress) pct=${PROGRESS_PCT:-$CUM_PCT} ;;
    *) pct=$CUM_PCT ;;
  esac
  # Never backwards: a step's second wait (the dashboard after the core)
  # starts below where its first one got to.
  if [ "$state" != fail ]; then
    [ "$pct" -ge "$LAST_PCT" ] || pct=$LAST_PCT
    LAST_PCT=$pct
  fi
  printf '%s{"v":1,"step":"%s","index":%d,"total":%d,"state":"%s","percent":%d,"message":%s%s}\n' \
    "$PROGRESS_PREFIX" "$STEP_ID" "$STEP_INDEX" "$STEP_TOTAL" "$state" "$pct" "$(json_str "$msg")" "${extra:+,$extra}"
}

# emit_frac PERMILLE MESSAGE [EXTRA]: a progress line PERMILLE/1000 of the
# way through the current step.
emit_frac() {
  local f=$1 pct
  [ "$f" -le 999 ] || f=999
  [ "$f" -ge 0 ] || f=0
  pct=$((CUM_PCT + STEP_W * f / 1000))
  PROGRESS_PCT=$pct emit progress "$2" "${3-}"
}

# emit_bytes N TOTAL MESSAGE: a download's progress (TOTAL 0 = unknown).
emit_bytes() {
  local n=$1 total=$2 f=0 t=null
  if [ "$total" -gt 0 ] 2>/dev/null; then
    f=$((n * 1000 / total))
    t=$total
  fi
  emit_frac "$f" "$3" "\"bytes\":$n,\"bytes_total\":$t"
}

# final_line RESULT EXIT_CODE MESSAGE: the last progress line. A run that
# holds the run lock also keeps it in result.json.
final_line() {
  local result=$1 code=$2 msg=$3 fs=null head=null state=done pct=$LAST_PCT obj
  [ -z "$FAILED_STEP" ] || fs=$(json_str "$FAILED_STEP")
  if [ -n "$HEAD_SHA" ]; then head=$(json_str "$HEAD_SHA"); fi
  case $result in failed|usage_error|busy) state=fail ;; esac
  [ "$result" != ok ] || pct=100
  STEP_ID=finish
  STEP_INDEX=$STEP_TOTAL
  obj=$(printf '{"v":1,"step":"finish","index":%d,"total":%d,"state":"%s","percent":%d,"message":%s,"final":true,"result":"%s","restart_required":%s,"exit_code":%d,"failed_step":%s,"head":%s,"tier":%s,"wsl":%s,"gpu":%s,"warnings":%d,"dashboard":"%s"}' \
    "$STEP_TOTAL" "$STEP_TOTAL" "$state" "$pct" "$(json_str "$msg")" "$result" \
    "$RESTART_REQUIRED" "$code" "$fs" "$head" "$(json_str "$TIER_EFF")" \
    "$([ "$WSL" = 1 ] && echo true || echo false)" "$GPU_JSON" "$WARNINGS" "$DASHBOARD_URL")
  printf '%s%s\n' "$PROGRESS_PREFIX" "$obj"
  FINAL_EMITTED=1
  if [ "$STARTED" = 1 ] && [ "$DRY_RUN" = 0 ] && [ -d "$STATE_DIR" ]; then
    printf '%s\n' "$obj" >"$RESULT_FILE.tmp" && mv -f "$RESULT_FILE.tmp" "$RESULT_FILE" || true
  fi
}

# ─── Failure and exit ────────────────────────────────────────────────────

trap 'LAST_ERR="line $LINENO: $BASH_COMMAND (exit $?)"' ERR

# die MESSAGE: stop the run. The message also goes to a file, so a die in a
# subshell still names the cause in the final line.
die() {
  printf '%s: FATAL: %s\n' "$PROG" "$1" >&2
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then printf '%s' "$1" >"$TMP_DIR/fail-message"; fi
  exit 1
}

stop_watch() {
  if [ -n "$WATCH_PID" ]; then
    kill "$WATCH_PID" 2>/dev/null || true
    wait "$WATCH_PID" 2>/dev/null || true
    WATCH_PID=""
  fi
}

# The run lock's holder (take_lock) goes with this shell; ending it here
# frees the lock at once rather than at its next look at this shell.
release_lock() {
  if [ -n "$LOCK_PID" ]; then
    kill "$LOCK_PID" 2>/dev/null || true
    wait "$LOCK_PID" 2>/dev/null || true
    LOCK_PID=""
  fi
}

on_exit() {
  local rc=$? msg=""
  set +e
  trap - ERR
  stop_watch
  if [ "$FINAL_EMITTED" = 0 ]; then
    # Every deliberate exit writes its own final line first; anything else
    # (a failed command under set -e, a signal) is a failure.
    [ "$rc" != 0 ] || rc=1
    if [ -n "$TMP_DIR" ] && [ -s "$TMP_DIR/fail-message" ]; then msg=$(cat "$TMP_DIR/fail-message"); fi
    [ -n "$msg" ] || msg=${LAST_ERR:-"stopped (exit $rc)"}
    if [ -n "$STEP_ID" ] && [ "$STEP_ID" != finish ]; then
      FAILED_STEP=$STEP_ID
      emit fail "$msg"
    fi
    if [ "$STARTED" = 1 ]; then compute_restart_required; fi
    final_line failed "$rc" "$msg"
  fi
  # A clone that never reached its place (do_checkout): the next run starts
  # it again from nothing.
  if [ -n "$CLONE_TMP_ACTIVE" ]; then rm -rf -- "$CLONE_TMP_ACTIVE"; fi
  # The verified copy this run read the checkout from.
  if [ -n "$COPY_ACTIVE" ]; then rm -rf -- "$COPY_ACTIVE"; fi
  if [ -n "$TMP_DIR" ]; then rm -rf "$TMP_DIR"; fi
  release_lock
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# refuse CODE RESULT MESSAGE: stop before doing anything, saying why.
refuse() {
  printf '%s: %s\n' "$PROG" "$3" >&2
  final_line "$2" "$1" "$3"
  exit "$1"
}

# ─── The test harness's root ─────────────────────────────────────────────

ROOT=""
if [ -n "${DOMOVOI_PROVISION_ROOT:-}" ] || [ -n "${DOMOVOI_PROVISION_TEST_HARNESS:-}" ]; then
  if [ "${DOMOVOI_PROVISION_TEST_HARNESS:-}" != 1 ] || [[ ${DOMOVOI_PROVISION_ROOT:-} != /* ]] \
      || [ ! -d "$DOMOVOI_PROVISION_ROOT" ] || [ "$(cd "$DOMOVOI_PROVISION_ROOT" && pwd -P)" = / ]; then
    refuse 2 usage_error "DOMOVOI_PROVISION_ROOT and DOMOVOI_PROVISION_TEST_HARNESS are for its test harness only, and only together; unset both and run it again"
  fi
  ROOT=${DOMOVOI_PROVISION_ROOT%/}
  SVC_PATH=$PATH
else
  # Root runs what it finds on PATH: take it from no one's shell setup.
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export PATH
  SVC_PATH=$PATH
fi
export LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive
# Ubuntu's needrestart would otherwise restart services (docker, ollama) in
# the middle of a run when apt upgrades a library they use.
export NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1

# ─── Paths (system paths carry the test harness's root) ──────────────────

STATE_DIR=$ROOT/var/lib/domovoi-provision
STEP_DIR=$STATE_DIR/steps
RESULT_FILE=$STATE_DIR/result.json
LOCK_FILE=$STATE_DIR/lock
PENDING_FILE=$STATE_DIR/wsl-conf-pending
UNITS_CHANGED_FILE=$STATE_DIR/units-changed
# The env step's own record: what it last wrote into domovoi/.env (KEY=VALUE
# lines), the .env's hash when it last changed it (the start step's input),
# and a note that it is creating the .env (a run killed then still owns it).
ENV_RECORD=$STATE_DIR/env-written
ENV_STAMP=$STATE_DIR/env-stamp
ENV_CREATING=$STATE_DIR/env-creating
# The settings domovoi-update.service reads (docs/LINUX_HOST.md, Updates
# from the dashboard): this script writes DOMOVOI_USE_LOCK there and reads
# DOMOVOI_MANAGE_OS_JOBS and DOMOVOI_MANAGE_GPU_RUNTIME.
UPDATE_DEFAULTS=$ROOT/etc/default/domovoi-update
CACHE_DIR=$ROOT/var/cache/domovoi-provision
PROC=$ROOT/proc
OS_RELEASE=$ROOT/etc/os-release
WSL_CONF=$ROOT/etc/wsl.conf
FSTAB=$ROOT/etc/fstab
UNIT_DIR=$ROOT/etc/systemd/system
SUDOERS_DIR=$ROOT/etc/sudoers.d
APT_KEYRING=$ROOT/etc/apt/keyrings/docker.asc
APT_SOURCE=$ROOT/etc/apt/sources.list.d/docker.sources
SIGNERS_FILE=$ROOT/etc/domovoi/allowed_signers
ZONEINFO=$ROOT/usr/share/zoneinfo
LOCALTIME=$ROOT/etc/localtime
OLLAMA_PREFIX=$ROOT/usr/local
OLLAMA_BIN=$OLLAMA_PREFIX/bin/ollama
OLLAMA_STAMP=$OLLAMA_PREFIX/lib/ollama/.domovoi-provision
# As the box sees them (fstab lines, .env values): no harness root.
MEDIA_BASE=/srv/media
SYSTEMCTL_PATH=/usr/bin/systemctl
OLLAMA_API=http://127.0.0.1:11434
CORE_HEALTH_URL=http://127.0.0.1:6370/v1/health
WEB_HEALTH_URL=http://127.0.0.1:6369/api/health
LOCK_NAME=requirements-linux-py314.lock
# faster-whisper and resemblyzer, installed without their declared
# dependencies (requirements-no-deps.in says why).
NODEPS_LOCK_NAME=requirements-linux-py314-no-deps.lock

# ─── Options ─────────────────────────────────────────────────────────────

REPO_URL=https://github.com/coders-farm-official/domovoi
# The release channel (docs/LINUX_HOST.md, Release channels): stable moves
# only to a release's commit, main carries every change. New installs
# follow stable. Before the first release origin has no stable branch, and
# the defaults then take main instead, saying so (checkout_ref).
REF=stable
TRACK_BRANCH=stable
REF_GIVEN=0
TRACK_GIVEN=0
CHECKOUT=/opt/domovoi
SVC_USER=domovoi
INTERNET=""
TIER=auto
TIMEZONE=""
LAN_IP=""
MEDIA_ARGS=()
WSL_OPT=auto
SKIP_MODELS=0
PULL_VISION=1
DRY_RUN=0
HEALTH_TIMEOUT=900
ALLOWED_SIGNERS=""
KEEP_DOWNLOADS=0
STOP_BEFORE=""

usage() {
  cat <<'EOF'
Usage: sudo bash provision.sh [options]

Takes a fresh Ubuntu 26.04 box (bare metal, or the Windows installer's WSL2
distro) to a running Domovoi server. Safe to run again: finished steps are
skipped, a killed run resumes, a changed option re-runs what it feeds.

  --repo-url URL        the repository to clone (default:
                        https://github.com/coders-farm-official/domovoi; a
                        local path or git bundle works too)
  --ref REF             a branch, tag or full commit SHA to install (default:
                        stable, the latest release; main while the
                        repository has no stable branch yet)
  --track-branch NAME   for a tag or SHA: the branch the checkout follows for
                        updates (default: stable; main without one). REF must
                        be on its history, or newer than it on the same line
                        (then updates resume with the next release that
                        includes REF)
  --checkout-dir DIR    where the checkout goes (default: /opt/domovoi)
  --user NAME           the service user (default: domovoi). Created with no
                        password login; an existing account is used as it
                        is, and one in sudo, admin or wheel is refused
  --internet ANSWER     always | sometimes | never: the household's internet
                        answer, written into a NEW domovoi/.env (python -m
                        domovoi.env_bootstrap --internet). Required on WSL.
  --tier TIER           auto | floor | standard: the hardware tier's models
                        (default: auto, from this box's MemTotal)
  --timezone ZONE       bare metal: an IANA zone (America/New_York) to set
                        with timedatectl. Ignored on WSL, with a warning:
                        there the zone follows Windows (WSL sets it at
                        every distro start)
  --lan-ip IP           write MPD_HTTP_BASE=http://IP (default: the repo's
                        "auto", which finds this host's LAN address itself)
  --media NAME=PATH     a media folder (repeatable). On WSL PATH is a Windows
                        folder (C:\Users\Public\Domovoi\Music), mounted alone
                        at /srv/media/NAME through /etc/fstab; on bare metal a
                        Linux path outside other users' homes. music,
                        pictures, documents, audiobooks, podcasts and
                        videos set MUSIC_DIR, PICTURES_DIR, ... VIDEOS_DIR
                        in .env.
                        The .env keys this script sets (models, Whisper,
                        MPD_HTTP_BASE, media folders) are written only while
                        they still hold what it last wrote: a value changed
                        in Settings or by hand is kept, with a warning.
  --wsl, --no-wsl       force WSL mode on or off (default: detected from the
                        kernel, the rule domovoi/host_kind.py uses)
  --no-vision-model     don't pull the tier's vision model, which reads
                        pictures in chat (about 1.9 GB). It is still the one
                        configured: chat then says it isn't installed, and
                        an admin can install it later on the dashboard
                        (Settings, Models)
  --skip-models         no Ollama pulls and no Whisper download (tests)
  --allowed-signers F   install F as /etc/domovoi/allowed_signers first, so
                        HEAD and every later update must verify against it
  --health-timeout SEC  how long the first start may take (default: 900)
  --keep-downloads      keep the Ollama tarball in /var/cache/domovoi-provision
  --stop-before STEP    run the steps before STEP (one of the step ids at the
                        top of this file, apt to update-unit) and stop there,
                        exit 0, result "stopped". The Windows installer stops
                        before "start" to check the PC's ports, then runs
                        this again without it
  --dry-run             say what each step would do; change nothing
  -h, --help            this text

Progress for programs: lines starting with "@@DOMOVOI " are JSON objects;
see the comment at the top of this file. Exit codes: 0 done, 1 failed,
2 bad usage, 3 restart the WSL distro and run again, 4 another run is busy.
EOF
}

usage_error() {
  printf '%s: %s\n\n' "$PROG" "$1" >&2
  usage >&2
  final_line usage_error 2 "$1"
  exit 2
}

while [ $# -gt 0 ]; do
  opt=$1
  val=""
  has_val=0
  case $opt in
    --*=*) val=${opt#*=}; opt=${opt%%=*}; has_val=1 ;;
  esac
  case $opt in
    --repo-url|--ref|--track-branch|--checkout-dir|--user|--internet|--tier|--timezone|--lan-ip|--media|--health-timeout|--allowed-signers|--stop-before)
      if [ "$has_val" = 0 ]; then
        [ $# -ge 2 ] || usage_error "$opt needs a value"
        val=$2
        shift 2
      else
        shift
      fi
      [ -n "$val" ] || usage_error "$opt needs a value"
      case $opt in
        --repo-url) REPO_URL=$val ;;
        --ref) REF=$val; REF_GIVEN=1 ;;
        --track-branch) TRACK_BRANCH=$val; TRACK_GIVEN=1 ;;
        --checkout-dir) CHECKOUT=${val%/} ;;
        --user) SVC_USER=$val ;;
        --internet) INTERNET=$val ;;
        --tier) TIER=$val ;;
        --timezone) TIMEZONE=$val ;;
        --lan-ip) LAN_IP=$val ;;
        --media) MEDIA_ARGS+=("$val") ;;
        --health-timeout) HEALTH_TIMEOUT=$val ;;
        --allowed-signers) ALLOWED_SIGNERS=$val ;;
        --stop-before) STOP_BEFORE=$val ;;
      esac
      ;;
    --wsl|--no-wsl|--skip-models|--no-vision-model|--dry-run|--keep-downloads|-h|--help)
      [ "$has_val" = 0 ] || usage_error "$opt takes no value"
      case $opt in
        --wsl) WSL_OPT=yes ;;
        --no-wsl) WSL_OPT=no ;;
        --skip-models) SKIP_MODELS=1 ;;
        --no-vision-model) PULL_VISION=0 ;;
        --dry-run) DRY_RUN=1 ;;
        --keep-downloads) KEEP_DOWNLOADS=1 ;;
        -h|--help) usage; final_line help 0 "Usage printed; nothing was done"; exit 0 ;;
      esac
      shift
      ;;
    *) usage_error "unknown option: $1" ;;
  esac
done
if [ -n "$STOP_BEFORE" ]; then
  # A step that has a "before" inside the run: not preflight (nothing runs
  # before it) and not finish (that is the end anyway).
  case " ${STEP_IDS[*]:1:${#STEP_IDS[@]}-2} " in
    *" $STOP_BEFORE "*) ;;
    *) usage_error "--stop-before takes a step id from apt to update-unit, not: $STOP_BEFORE" ;;
  esac
fi

# ─── Small helpers ───────────────────────────────────────────────────────

dry() { [ "$DRY_RUN" = 1 ]; }

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

sha_ok() { [ -f "$1" ] && [ "$(sha256_of "$1")" = "$2" ]; }

# A file's hash for a step's inputs, or "absent".
file_sig() { if [ -f "$1" ]; then sha256_of "$1"; else echo "absent:$1"; fi; }

systemd_running() { [ "$(cat "$PROC/1/comm" 2>/dev/null)" = systemd ]; }

# Which run of the distro this is: the kernel's boot and PID 1's start
# time. wsl --terminate restarts PID 1 even when the VM (and so boot_id)
# stays up for another distro.
instance_id() {
  printf '%s %s\n' "$(cat "$PROC/sys/kernel/random/boot_id" 2>/dev/null)" \
    "$(awk '{print $22}' "$PROC/1/stat" 2>/dev/null)"
}

svc_home() { getent passwd "$SVC_USER" 2>/dev/null | cut -d: -f6; }

# sed_repl TEXT: TEXT as the right-hand side of a sed s#...#...# command.
sed_repl() { printf '%s' "$1" | sed 's/[#&\\]/\\&/g'; }

# Journal and log text this script prints: never the setup code. Since
# 2026-10-11 the core prints its first-boot banner (domovoi/admin_auth.py,
# "Setup code: <eight words joined by hyphens>") only when its stdout is a
# terminal, which under systemd it never is; an older checkout still put
# it in the journal. So any line naming it still goes, and anything shaped
# like a code is hidden wherever it appears.
# Journal lines for people: no line about the setup code, nothing shaped
# like one, and no password in a URL (scheme://user:password@host).
scrub_log() {
  LC_ALL=C sed -E -e '/setup[ _-]?code/Id' -e 's/[A-Za-z]+(-[A-Za-z]+){7,}/[hidden]/g' \
    -e 's#(://[^:/@[:space:]]+:)[^@/[:space:]]+@#\1[hidden]@#g'
}

# Run a command as the service user with a clean environment. runuser
# calls initgroups, so a group added moments ago (docker) is live.
as_svc() {
  runuser -u "$SVC_USER" -- env HOME="$SVC_HOME" PATH="$SVC_PATH" LANG=C.UTF-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 PIP_PROGRESS_BAR=off HF_HUB_DISABLE_TELEMETRY=1 "$@"
}

# The checkout's python, as the service user, from the checkout (python -m
# puts the cwd on sys.path; root's own cwd may be closed to that user).
svc_py() { (cd "$CHECKOUT" && as_svc "$VENV_PY" "$@"); }

apt_get() { apt-get -o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=600 -q "$@"; }

# write_if_changed DEST MODE < CONTENT: put CONTENT in DEST (root-owned,
# MODE) by rename from a dot-named temp file beside it, which neither sudo
# nor systemd reads. Status 0 when it wrote, 1 when DEST already held it.
# Give it CONTENT by redirection (< file, or < <(command)), never at the end
# of a pipe: there it runs in a subshell, its die can't stop the run, and a
# failed write would pass for "already held it".
write_if_changed() {
  local dest=$1 mode=$2 dir tmp
  dir=$(dirname "$dest")
  mkdir -p "$dir" || die "can't create $dir"
  tmp=$(mktemp "$dir/.$(basename "$dest").XXXXXX") || die "can't write in $dir"
  if ! cat >"$tmp"; then rm -f "$tmp"; die "can't write $tmp"; fi
  if [ -f "$dest" ] && [ ! -L "$dest" ] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    return 1
  fi
  chmod "$mode" "$tmp" && chown root:root "$tmp" && mv -f "$tmp" "$dest" || { rm -f "$tmp"; die "can't install $dest"; }
  return 0
}

# The last KEY=value in the checkout's domovoi/.env, quotes stripped. Read
# as the service user, whose file it is: root never follows a link there.
dotenv_get() {
  local f=$CHECKOUT/domovoi/.env v
  [ -f "$f" ] || return 0
  v=$(as_svc sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$f" | tail -n 1 | tr -d '\r')
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  printf '%s' "$v"
}

path_bytes() {
  if [ -d "$1" ]; then du -sb "$1" 2>/dev/null | cut -f1
  elif [ -f "$1" ]; then stat -c %s "$1" 2>/dev/null || echo 0
  else echo 0
  fi
}

# start_watch PATH TOTAL MESSAGE: progress lines every few seconds while a
# download fills PATH (a file or a directory).
start_watch() {
  stop_watch
  (
    set +e
    trap - ERR
    last=-1
    while sleep 3; do
      n=$(path_bytes "$1")
      [ -n "$n" ] || n=0
      if [ "$n" != "$last" ]; then
        last=$n
        emit_bytes "$n" "$2" "$3"
      fi
    done
  ) &
  WATCH_PID=$!
}

valid_ipv4() {
  local o
  [[ $1 =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  IFS=. read -r -a o <<<"$1"
  for x in "${o[@]}"; do [ "$x" -le 255 ] || return 1; done
}

# ─── Validation ──────────────────────────────────────────────────────────

[[ $REPO_URL =~ ^(https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~+-]+)*/?|(file://)?/[A-Za-z0-9._/@+-]+)$ ]] \
  || usage_error "--repo-url must be an https:// URL or an absolute local path (a git bundle works): $REPO_URL"
[[ $REF =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$ ]] && [[ $REF != *..* ]] \
  || usage_error "--ref must be a branch, tag or commit SHA: $REF"
[[ $TRACK_BRANCH =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$ ]] && [[ $TRACK_BRANCH != *..* ]] \
  || usage_error "--track-branch must be a branch name: $TRACK_BRANCH"
if ! [[ $CHECKOUT =~ ^/[A-Za-z0-9._/@+-]+$ ]] || [[ $CHECKOUT/ == *//* || $CHECKOUT/ == */./* || $CHECKOUT/ == */../* ]]; then
  usage_error "--checkout-dir must be absolute and plain (letters, digits and . _ / @ + -): $CHECKOUT"
fi
[[ $SVC_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] && [ "$SVC_USER" != root ] \
  || usage_error "--user must be a plain user name other than root: $SVC_USER"
# The services' account does nothing else (docs/LINUX_HOST.md, Make it an
# appliance): an existing administrator's login (the Beelink's own user,
# say) is refused rather than handed to them.
if id -u "$SVC_USER" >/dev/null 2>&1; then
  for g in $(id -nG "$SVC_USER" 2>/dev/null || true); do
    case $g in
      sudo|admin|wheel) usage_error "--user $SVC_USER is an administrator's account (in the $g group); Domovoi's services need an account of their own that does nothing else: leave --user out (domovoi) or name a new one" ;;
    esac
  done
fi
case $INTERNET in
  ""|always|sometimes|never) ;;
  *) usage_error "--internet must be always, sometimes or never: $INTERNET" ;;
esac
case $TIER in
  auto|floor|standard) ;;
  *) usage_error "--tier must be auto, floor or standard: $TIER" ;;
esac
if [ -n "$TIMEZONE" ]; then
  [[ $TIMEZONE =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,3}$ ]] || usage_error "--timezone must be an IANA zone such as America/New_York: $TIMEZONE"
fi
if [ -n "$LAN_IP" ]; then
  valid_ipv4 "$LAN_IP" || usage_error "--lan-ip must be an IPv4 address: $LAN_IP"
fi
[[ $HEALTH_TIMEOUT =~ ^[0-9]{2,5}$ ]] || usage_error "--health-timeout must be a number of seconds (10-99999): $HEALTH_TIMEOUT"
if [ -n "$ALLOWED_SIGNERS" ] && [ ! -f "$ALLOWED_SIGNERS" ]; then
  usage_error "--allowed-signers: no such file: $ALLOWED_SIGNERS"
fi

# WSL: the kernel says so, the rule domovoi/host_kind.py uses (not
# WSLInterop, which goes away with interop off).
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
  *) WSL=$(detect_wsl) ;;
esac
if [ "$WSL" = 1 ] && [ -z "$INTERNET" ] && [ "$DRY_RUN" = 0 ]; then
  usage_error "--internet is required on WSL (the Windows installer always has the household's answer)"
fi
# On WSL the zone is Windows' (owner decision 2026-10-11): WSL rewrites
# /etc/localtime and /etc/timezone from it at every distro start
# ([time] useWindowsTimezone, default on, seen with interop off too), so a
# zone set here would only hold until then. Said once the run has started.
TIMEZONE_IGNORED=""
if [ "$WSL" = 1 ] && [ -n "$TIMEZONE" ]; then
  TIMEZONE_IGNORED=$TIMEZONE
  TIMEZONE=""
fi

# Media folders: NAME=PATH.
MEDIA_NAMES=()
declare -A MEDIA_SRC=()
for m in "${MEDIA_ARGS[@]}"; do
  [[ $m == *=* ]] || usage_error "--media takes NAME=PATH: $m"
  name=${m%%=*}
  path=${m#*=}
  [[ $name =~ ^[a-z][a-z0-9_-]{0,31}$ ]] || usage_error "--media NAME must be lower-case letters, digits, - or _: $name"
  [ -z "${MEDIA_SRC[$name]+x}" ] || usage_error "--media $name given twice"
  [ -n "$path" ] || usage_error "--media $name needs a path"
  [[ $path != *[[:cntrl:]]* ]] || usage_error "--media $name: control characters in the path"
  if [ "$WSL" = 1 ]; then
    # A Windows folder on a local drive, never the drive itself.
    [[ $path =~ ^[A-Za-z]:[\\/] ]] || usage_error "--media $name: on WSL the path is a Windows folder such as C:\\Users\\Public\\Domovoi\\Music: $path"
    rest=${path:3}
    rest=${rest//\\//}
    rest=${rest%/}
    [ -n "$rest" ] || usage_error "--media $name: mount a folder, never a whole drive: $path"
    [[ /$rest/ != *"/../"* && /$rest/ != *"/./"* && $rest != *//* ]] || usage_error "--media $name: no . or .. in the path: $path"
    [[ $rest != *[\"\*\?\<\>\|]* ]] || usage_error "--media $name: not a valid Windows path: $path"
  else
    [[ $path == /?* ]] || usage_error "--media $name: on Linux the path is absolute: $path"
    [[ $path/ != *"/../"* && $path/ != *"/./"* && $path != *//* ]] || usage_error "--media $name: no . or .. in the path: $path"
    [[ $path != *[[:space:]]* ]] || usage_error "--media $name: no spaces in a Linux media path: $path"
    [[ $path != *[\"\'\\]* ]] || usage_error "--media $name: no quotes or backslashes in a Linux media path: $path"
    # The units hide every home but the service user's (ProtectHome=tmpfs,
    # BindPaths= that one): a folder in another one would look empty to
    # the services.
    home=$(svc_home) || home=""
    [ -n "$home" ] || home=/home/$SVC_USER
    case $path/ in
      "$home"/*) ;;
      /home/*|/root/*|/run/user/*)
        usage_error "--media $name: $path is in another user's home, which the services can't see (ProtectHome= in scripts/linux/units/*.service hides /home, /root and /run/user, all but $home): move the folder somewhere like /srv/$name, or under $home" ;;
    esac
  fi
  MEDIA_NAMES+=("$name")
  MEDIA_SRC[$name]=$path
done

# The .env key each media name sets (domovoi/config.py); empty: none does.
media_env_key() {
  case $1 in
    music) echo MUSIC_DIR ;;
    pictures) echo PICTURES_DIR ;;
    documents) echo DOCUMENTS_DIR ;;
    audiobooks) echo AUDIOBOOKS_DIR ;;
    podcasts) echo PODCASTS_DIR ;;
    videos) echo VIDEOS_DIR ;;
    *) echo "" ;;
  esac
}

# Where a media folder appears on the box.
media_path() { if [ "$WSL" = 1 ]; then echo "$MEDIA_BASE/$1"; else echo "${MEDIA_SRC[$1]}"; fi; }

# C:\Users\Public\Music -> C:/Users/Public/Music, with fstab's octal
# escapes for blanks (fstab(5)); forward slashes so a folder named like an
# octal escape (\123) can't be misread.
fstab_source() {
  local p=$1 drive
  drive=${p:0:1}
  p=${drive^^}:/${p:3}
  p=${p//\\//}
  p=${p%/}
  p=${p// /\\040}
  p=${p//$'\t'/\\011}
  printf '%s' "$p"
}

VENV_PY=$CHECKOUT/.venv/bin/python
SVC_HOME=""
MEM_MIB=0
declare -A M=()

# ─── The pin manifest ────────────────────────────────────────────────────

MANIFEST_KEYS=(manifest_version docker_key_url docker_key_fpr docker_repo_url docker_suite
  docker_ce docker_ce_cli containerd_io docker_buildx_plugin docker_compose_plugin
  nvidia_key_url nvidia_key_fpr nvidia_repo_url
  nvidia_container_toolkit nvidia_container_toolkit_base libnvidia_container_tools libnvidia_container1
  whisper_model whisper_repo whisper_revision whisper_model_bin_sha256 whisper_model_bin_bytes
  tier_standard_min_mem_mib
  tier_floor_tool_model tier_floor_tool_digest tier_floor_qa_model tier_floor_qa_digest
  tier_floor_vision_model tier_floor_vision_digest
  tier_standard_tool_model tier_standard_tool_digest tier_standard_qa_model tier_standard_qa_digest
  tier_standard_vision_model tier_standard_vision_digest)
MANIFEST_FILE=""

load_manifest() {
  local file=$1 line key val n=0 k
  [ -f "$file" ] || die "no pin manifest at $file"
  M=()
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=${line%$'\r'}
    case $line in ''|'#'*) continue ;; esac
    [[ $line =~ ^([a-z][a-z0-9_]*)=([^[:space:]]+)$ ]] || die "$file line $n is not key=value: $line"
    key=${BASH_REMATCH[1]}
    val=${BASH_REMATCH[2]}
    case " ${MANIFEST_KEYS[*]} " in *" $key "*) ;; *) die "$file line $n: unknown key $key" ;; esac
    [ -z "${M[$key]+x}" ] || die "$file line $n: $key is set twice"
    M[$key]=$val
  done <"$file"
  for k in "${MANIFEST_KEYS[@]}"; do
    [ -n "${M[$k]+x}" ] || die "$file: $k is missing"
  done
  [ "${M[manifest_version]}" = 1 ] || die "$file: manifest_version ${M[manifest_version]} is not one this script reads (1)"
  for k in docker_key_fpr nvidia_key_fpr; do
    [[ ${M[$k]} =~ ^[0-9A-F]{40}$ ]] || die "$file: $k is not a 40-hex-digit fingerprint"
  done
  for k in docker_key_url docker_repo_url nvidia_key_url nvidia_repo_url; do
    [[ ${M[$k]} =~ ^https://[A-Za-z0-9./_-]+$ ]] || die "$file: $k is not an https URL"
  done
  for k in docker_ce docker_ce_cli containerd_io docker_buildx_plugin docker_compose_plugin \
      nvidia_container_toolkit nvidia_container_toolkit_base libnvidia_container_tools libnvidia_container1; do
    [[ ${M[$k]} =~ ^([0-9]+:)?[0-9][A-Za-z0-9.+~-]*$ ]] || die "$file: $k is not a Debian version"
  done
  [[ ${M[whisper_revision]} =~ ^[0-9a-f]{40}$ ]] || die "$file: whisper_revision is not a commit SHA"
  [[ ${M[whisper_repo]} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "$file: whisper_repo is not owner/name"
  [[ ${M[whisper_model]} =~ ^[A-Za-z0-9._-]+$ ]] || die "$file: whisper_model is not a model name"
  for k in whisper_model_bin_sha256 tier_floor_tool_digest tier_floor_qa_digest tier_floor_vision_digest \
      tier_standard_tool_digest tier_standard_qa_digest tier_standard_vision_digest; do
    [[ ${M[$k]} =~ ^[0-9a-f]{64}$ ]] || die "$file: $k is not a SHA-256"
  done
  for k in whisper_model_bin_bytes tier_standard_min_mem_mib; do
    [[ ${M[$k]} =~ ^[0-9]+$ ]] || die "$file: $k is not a number"
  done
  for k in tier_floor_tool_model tier_floor_qa_model tier_floor_vision_model \
      tier_standard_tool_model tier_standard_qa_model tier_standard_vision_model; do
    [[ ${M[$k]} =~ ^[a-z0-9][a-z0-9._-]*:[A-Za-z0-9._-]+$ ]] || die "$file: $k is not an exact model:tag"
  done
  MANIFEST_FILE=$file
}

# The Ollama release docs/LINUX_HOST.md pins ("Install"), read from the
# verified copy of $CHECKOUT/docs/LINUX_HOST.md: exactly one
# OLLAMA_VERSION= and one OLLAMA_SHA256= line.
OLLAMA_VERSION=""
OLLAMA_SHA256=""
load_ollama_pin() {
  local doc v s
  doc=$(src_file docs/LINUX_HOST.md)
  [ -f "$doc" ] || die "no $doc to read the Ollama pin from"
  v=$(tr -d '\r' <"$doc" | sed -n 's/^OLLAMA_VERSION=\([0-9][0-9.]*\)$/\1/p')
  s=$(tr -d '\r' <"$doc" | sed -n 's/^OLLAMA_SHA256=\([0-9a-f]\{64\}\)$/\1/p')
  [ "$(printf '%s\n' "$v" | grep -c .)" = 1 ] || die "$doc should have exactly one OLLAMA_VERSION=<version> line"
  [ "$(printf '%s\n' "$s" | grep -c .)" = 1 ] || die "$doc should have exactly one OLLAMA_SHA256=<sha256> line"
  OLLAMA_VERSION=$v
  OLLAMA_SHA256=$s
}

resolve_tier() {
  MEM_MIB=$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' "$PROC/meminfo" 2>/dev/null || echo 0)
  [ -n "$MEM_MIB" ] || MEM_MIB=0
  case $TIER in
    auto)
      if [ "$MEM_MIB" -ge "${M[tier_standard_min_mem_mib]}" ]; then TIER_EFF=standard; else TIER_EFF=floor; fi ;;
    *) TIER_EFF=$TIER ;;
  esac
}
tier_tool() { printf '%s' "${M[tier_${TIER_EFF}_tool_model]}"; }
tier_tool_digest() { printf '%s' "${M[tier_${TIER_EFF}_tool_digest]}"; }
tier_qa() { printf '%s' "${M[tier_${TIER_EFF}_qa_model]}"; }
tier_qa_digest() { printf '%s' "${M[tier_${TIER_EFF}_qa_digest]}"; }
tier_vision() { printf '%s' "${M[tier_${TIER_EFF}_vision_model]}"; }
tier_vision_digest() { printf '%s' "${M[tier_${TIER_EFF}_vision_digest]}"; }

# Read the manifest from the verified copy of the checkout (or, in a dry
# run before the clone, the copy beside this script) and settle the tier.
manifest_ready() {
  if [ -n "$MANIFEST_FILE" ]; then return 0; fi
  if [ -f "$(src_file scripts/linux/provision-manifest.conf)" ]; then
    load_manifest "$(src_file scripts/linux/provision-manifest.conf)"
  elif dry && [ -f "$SCRIPT_DIR/provision-manifest.conf" ]; then
    load_manifest "$SCRIPT_DIR/provision-manifest.conf"
  else
    return 1
  fi
  resolve_tier
}

# ─── The step runner ─────────────────────────────────────────────────────

RERUN_REASON=""

# Steps that read the checkout, and those that read the pin manifest: a
# dry run before the clone plans them without their inputs.
after_clone() { case " docker gpu-runtime venv env ollama models whisper piper images searxng os-jobs units start update-unit " in *" $1 "*) return 0 ;; esac; return 1; }
needs_manifest() { case " docker gpu-runtime env models whisper " in *" $1 "*) return 0 ;; esac; return 1; }

plan_details() {
  local id=$1 fn=${1//-/_}
  declare -F "plan_$fn" >/dev/null || return 0
  if [ -z "$MANIFEST_FILE" ] && needs_manifest "$id"; then
    echo "    (details need the pin manifest from the checkout)"
    return 0
  fi
  "plan_$fn" | sed 's/^/    /'
}

# run_step ID: skip it when it doesn't apply, or when its marker matches
# its inputs and its check passes; otherwise do it and write the marker.
# A step defines do_X, and optionally skip_X (prints why it doesn't apply,
# status 0), inputs_X (prints its inputs; no inputs = always runs),
# check_X (status 0 when its result is in place) and plan_X (dry run).
run_step() {
  local id=$1 fn=${1//-/_} why hash="" prev=""
  STEP_ID=$id
  STEP_INDEX=$((STEP_INDEX + 1))
  STEP_W=${STEP_WEIGHT[$id]}
  if declare -F "skip_$fn" >/dev/null && why=$("skip_$fn"); then
    if dry; then emit plan "skip: $why" '"would":"skip"'; log "[$id] would skip: $why"
    else emit skip "$why"; log "[$id] skipped: $why"; fi
    CUM_PCT=$((CUM_PCT + STEP_W))
    return 0
  fi
  if dry && [ ! -d "$CHECKOUT/.git" ] && after_clone "$id"; then
    emit plan "run: ${STEP_TITLE[$id]}" '"would":"run"'
    log "[$id] would run: ${STEP_TITLE[$id]}"
    plan_details "$id"
    CUM_PCT=$((CUM_PCT + STEP_W))
    return 0
  fi
  if declare -F "inputs_$fn" >/dev/null; then
    hash=$( { printf '%s\n' "$id"; "inputs_$fn"; } | sha256sum | cut -d' ' -f1) || hash=""
    if [ -f "$STEP_DIR/$id" ]; then prev=$(cat "$STEP_DIR/$id"); fi
    if [ -n "$hash" ] && [ "$prev" = "$hash" ]; then
      if ! declare -F "check_$fn" >/dev/null || "check_$fn"; then
        if dry; then emit plan "skip: already done" '"would":"skip"'; log "[$id] would skip: already done, inputs unchanged"
        else emit skip "Already done"; log "[$id] already done, inputs unchanged"; fi
        CUM_PCT=$((CUM_PCT + STEP_W))
        return 0
      fi
      RERUN_REASON=check
      log "[$id] marker matches, but its result is no longer in place: running it again"
    elif [ -n "$prev" ]; then
      RERUN_REASON=changed
      log "[$id] inputs changed since it last ran: running it again"
    else
      RERUN_REASON=new
    fi
  else
    RERUN_REASON=always
  fi
  # The preflight only reads, so a dry run does it for real.
  if dry && [ "$id" != preflight ]; then
    emit plan "run: ${STEP_TITLE[$id]}" '"would":"run"'
    log "[$id] would run: ${STEP_TITLE[$id]}"
    plan_details "$id"
    CUM_PCT=$((CUM_PCT + STEP_W))
    return 0
  fi
  emit start "${STEP_TITLE[$id]}"
  log "[$id] ${STEP_TITLE[$id]}"
  "do_$fn"
  if [ -n "$hash" ] && ! dry; then
    printf '%s\n' "$hash" >"$STEP_DIR/$id.tmp" && mv -f "$STEP_DIR/$id.tmp" "$STEP_DIR/$id"
  fi
  emit done "${STEP_DONE_MSG:-Done}"
  STEP_DONE_MSG=""
  CUM_PCT=$((CUM_PCT + STEP_W))
}

# ─── preflight ───────────────────────────────────────────────────────────

os_field() { (. "$OS_RELEASE" 2>/dev/null && eval "printf '%s' \"\${$1:-}\""); }

do_preflight() {
  local id ver arch
  arch=$(uname -m)
  [ "$arch" = x86_64 ] || die "x86_64 only (this is $arch): the lock and the Ollama pin are for x86_64"
  id=$(os_field ID)
  ver=$(os_field VERSION_ID)
  [ "$id" = ubuntu ] || die "Ubuntu only (this is ${id:-unknown}); docs/LINUX_HOST.md targets Ubuntu 26.04"
  if [ "$ver" != 26.04 ]; then
    warn "this is Ubuntu $ver; the script, the lock (Python 3.14) and the docker-ce pins are for 26.04"
  fi
  log "$(os_field PRETTY_NAME), $(uname -r); WSL: $([ "$WSL" = 1 ] && echo yes || echo no)"
  if ! systemd_running; then
    if [ "$WSL" = 1 ]; then
      log "systemd is not PID 1 yet: this run only writes /etc/wsl.conf and the service user"
    else
      die "systemd is not PID 1; Domovoi runs as systemd units (docs/LINUX_HOST.md, Make it an appliance)"
    fi
  fi
  # Ubuntu's cloud-init runs on the first boot of a fresh image and takes
  # apt's lock while it does; wait for it rather than fail on that lock.
  if systemd_running && command -v cloud-init >/dev/null 2>&1; then
    if ! dry; then
      log "waiting for cloud-init to finish its first boot (up to 10 min)"
      timeout 600 cloud-init status --wait >/dev/null 2>&1 || true
      log "cloud-init: $(cloud-init status 2>/dev/null | tr '\n' ' ')"
    fi
  fi
  MEM_MIB=$(awk '/^MemTotal:/ {printf "%d", $2 / 1024}' "$PROC/meminfo" 2>/dev/null || echo 0)
  log "memory: ${MEM_MIB} MiB (MemTotal; on WSL that is the VM's cap)"
  if [ "${MEM_MIB:-0}" -lt 9000 ]; then
    warn "less than about 9 GB of memory: the models may not fit (the Windows floor is a 16 GB PC)"
  fi
  STEP_DONE_MSG="This machine can take Domovoi"
}

# ─── apt ─────────────────────────────────────────────────────────────────

# docs/LINUX_HOST.md "Install", line for line, then what this script needs:
# curl, gnupg and ca-certificates (Docker's repository), zstd (the Ollama
# tarball; also in the doc's Ollama steps), tzdata (zone files for
# --timezone; WSL also needs them to follow Windows' zone), sudo (the
# dashboard's restart and update grants). Not espeak-ng (GPL): Domovoi's
# voices no longer need it, and the OS last-resort voice is the owner's
# choice (docs/LINUX_HOST.md).
APT_PACKAGES=(python3 python3-venv python3-dev build-essential pkg-config libasound2-dev
  git ffmpeg mpg123 libchromaprint-tools
  zstd ca-certificates curl gnupg tzdata sudo)

pkgs_installed() {
  local out
  out=$(dpkg-query -W -f='${Package} ${Status}\n' "$@" 2>/dev/null) || return 1
  [ "$(printf '%s\n' "$out" | grep -c ' install ok installed$')" -eq "$#" ]
}

inputs_apt() { printf '%s\n' "${APT_PACKAGES[@]}"; }
check_apt() { pkgs_installed "${APT_PACKAGES[@]}"; }
plan_apt() { echo "apt-get install ${APT_PACKAGES[*]}"; }
do_apt() {
  apt_get update
  apt_get install -y "${APT_PACKAGES[@]}"
  pkgs_installed "${APT_PACKAGES[@]}" || die "apt-get reported success but not every package is installed: ${APT_PACKAGES[*]}"
  STEP_DONE_MSG="System packages installed"
}

# ─── user ────────────────────────────────────────────────────────────────

inputs_user() { printf '%s\n' "$SVC_USER"; }
check_user() {
  local h
  h=$(svc_home)
  id -u "$SVC_USER" >/dev/null 2>&1 && [[ $h == /?* ]] && [ -d "$ROOT$h" ]
}
plan_user() {
  if id -u "$SVC_USER" >/dev/null 2>&1; then echo "service user $SVC_USER exists: used as it is (its password is not touched)"
  else echo "service user $SVC_USER, home /home/$SVC_USER, created with password login locked"; fi
}
do_user() {
  if ! id -u "$SVC_USER" >/dev/null 2>&1; then
    useradd --create-home --home-dir "/home/$SVC_USER" --shell /bin/bash --user-group "$SVC_USER"
    # No password login for the account this script made: the dashboard is
    # the UI, and sudo grants it exactly two NOPASSWD commands. An account
    # that was there before (an administrator's is refused up front) keeps
    # its password: locking it could lock someone out of their own box.
    passwd -l "$SVC_USER" >/dev/null 2>&1 || true
    log "created the service user $SVC_USER (password login locked)"
  else
    log "the service user $SVC_USER exists; its password and groups are left as they are"
  fi
  SVC_HOME=$(svc_home)
  [[ $SVC_HOME == /?* ]] && [ -d "$ROOT$SVC_HOME" ] || die "the home of $SVC_USER ($SVC_HOME) does not exist"
  # The home goes into the units (HOME=, BindPaths=), where a blank or a %
  # would mean something else.
  [[ $SVC_HOME =~ ^/[A-Za-z0-9._/@+-]+$ ]] || die "the home of $SVC_USER ($SVC_HOME) is not a plain path (letters, digits and . _ / @ + -) that a unit file can carry"
  if [ "$SVC_HOME" != "/home/$SVC_USER" ]; then
    warn "$SVC_USER's home is $SVC_HOME; the units are written with that home"
  fi
  STEP_DONE_MSG="Service user $SVC_USER ready"
}

# ─── wsl-config ──────────────────────────────────────────────────────────

# https://learn.microsoft.com/windows/wsl/build-custom-distro ("Systemd
# recommendations", updated 2026-06-02): units known to cause issues in WSL.
WSL_MASKS=(systemd-resolved.service systemd-networkd.service NetworkManager.service
  systemd-tmpfiles-setup.service systemd-tmpfiles-clean.service systemd-tmpfiles-clean.timer
  systemd-tmpfiles-setup-dev-early.service systemd-tmpfiles-setup-dev.service tmp.mount)

# windows-installer PLAN.md 4.7 step 3. Keys and defaults:
# https://learn.microsoft.com/windows/wsl/wsl-config ("wsl.conf"). They take
# effect at the distro's next start (wsl --terminate <distro>).
wsl_conf_text() {
  cat <<EOF
# Written by scripts/linux/provision.sh (Domovoi): the Windows installer's
# distro. Changes take effect at the distro's next start
# (wsl --terminate <distro>, then start it again).
[boot]
systemd=true

[user]
default=$SVC_USER

[interop]
# No Windows programs from inside the distro, and none of Windows' PATH.
enabled=false
appendWindowsPath=false

[automount]
# No drives under /mnt: only the media folders /etc/fstab names.
enabled=false
mountFsTab=true
EOF
}

skip_wsl_config() { if [ "$WSL" = 1 ]; then return 1; fi; echo "not a WSL host"; }
inputs_wsl_config() { wsl_conf_text; printf '%s\n' "${WSL_MASKS[@]}"; }
masks_in_place() {
  local u
  for u in "$@"; do [ "$(readlink "$UNIT_DIR/$u" 2>/dev/null)" = /dev/null ] || return 1; done
}
check_wsl_config() { [ -f "$WSL_CONF" ] && wsl_conf_text | cmp -s - "$WSL_CONF" && masks_in_place "${WSL_MASKS[@]}"; }
plan_wsl_config() { echo "/etc/wsl.conf:"; wsl_conf_text | sed 's/^/  /'; echo "mask: ${WSL_MASKS[*]}"; }

# mask_units UNIT...: what systemctl mask does (a link to /dev/null in
# /etc/systemd/system), which also works before systemd runs. A real file
# in the way is the administrator's; stop rather than replace it.
mask_units() {
  local u t n=0
  mkdir -p "$UNIT_DIR"
  for u in "$@"; do
    t=$UNIT_DIR/$u
    [ "$(readlink "$t" 2>/dev/null)" != /dev/null ] || continue
    if [ -e "$t" ] || [ -L "$t" ]; then die "$t exists and is not a mask; move it aside to let this script mask $u"; fi
    ln -s /dev/null "$t"
    n=$((n + 1))
  done
  if [ "$n" -gt 0 ]; then
    log "masked $n unit(s): $*"
    if systemd_running; then systemctl daemon-reload; fi
  fi
}

do_wsl_config() {
  # The restart note goes first: a run killed between the two still says a
  # restart is needed (and the next run writes the file and the note again).
  if [ ! -f "$WSL_CONF" ] || [ -L "$WSL_CONF" ] || ! wsl_conf_text | cmp -s - "$WSL_CONF"; then
    instance_id >"$PENDING_FILE"
  fi
  if write_if_changed "$WSL_CONF" 0644 < <(wsl_conf_text); then
    log "wrote /etc/wsl.conf; it takes effect after: wsl --terminate <this distro>"
  else
    log "/etc/wsl.conf is already this script's"
  fi
  mask_units "${WSL_MASKS[@]}"
  STEP_DONE_MSG="WSL configured"
}

# ─── media ───────────────────────────────────────────────────────────────

FSTAB_BEGIN="# BEGIN Domovoi media folders (scripts/linux/provision.sh rewrites this block)"
FSTAB_END="# END Domovoi media folders"

# The block's lines. drvfs options (https://learn.microsoft.com/windows/wsl/wsl-config,
# "Automount options"): metadata keeps Linux owners and modes on files the
# server writes; uid/gid make the household's files the service user's;
# umask=022 and fmask=133 show files Windows wrote (they carry no Linux
# metadata) as 0644 and folders as 0755 rather than 0777, so no other
# account in the distro can change them; nofail lets the distro start when
# a folder (a USB drive) is missing, and then the empty mount point is
# root's, so the services can't write into it by mistake.
fstab_lines() {
  local name uid gid
  [ "${#MEDIA_NAMES[@]}" -gt 0 ] || return 0
  uid=$(id -u "$SVC_USER" 2>/dev/null || echo 1000)
  gid=$(id -g "$SVC_USER" 2>/dev/null || echo 1000)
  for name in "${MEDIA_NAMES[@]}"; do
    printf '%s %s drvfs metadata,uid=%s,gid=%s,umask=022,fmask=133,noatime,nofail 0 0\n' \
      "$(fstab_source "${MEDIA_SRC[$name]}")" "$MEDIA_BASE/$name" "$uid" "$gid"
  done
}

# The block's current line for mount point $1, or nothing.
fstab_block_line() {
  [ -f "$FSTAB" ] || return 0
  awk -v b="$FSTAB_BEGIN" -v e="$FSTAB_END" -v mp="$1" '$0==b{in_b=1; next} $0==e{in_b=0; next} in_b && $2==mp {print; exit}' "$FSTAB"
}

# /etc/fstab with this script's block replaced by CONTENT on stdin (no
# block at all when it is empty).
fstab_with_block() {
  local block
  block=$(cat)
  if [ -f "$FSTAB" ]; then
    awk -v b="$FSTAB_BEGIN" -v e="$FSTAB_END" '$0==b{skip=1; next} $0==e{skip=0; next} !skip' "$FSTAB"
  fi
  if [ -n "$block" ]; then
    printf '%s\n%s\n%s\n' "$FSTAB_BEGIN" "$block" "$FSTAB_END"
  fi
}

# Mount points of the block now in /etc/fstab.
fstab_block_targets() {
  [ -f "$FSTAB" ] || return 0
  awk -v b="$FSTAB_BEGIN" -v e="$FSTAB_END" '$0==b{in_b=1; next} $0==e{in_b=0; next} in_b && NF>=2 {print $2}' "$FSTAB"
}

skip_media() {
  if [ "$WSL" = 1 ] || [ "${#MEDIA_NAMES[@]}" -gt 0 ]; then return 1; fi
  echo "no media folders given"
}
inputs_media() {
  local name
  echo "wsl=$WSL"
  for name in "${MEDIA_NAMES[@]}"; do echo "$name=${MEDIA_SRC[$name]}"; done
  if [ "$WSL" = 1 ]; then fstab_lines; fi
}
check_media() {
  local name
  if [ "$WSL" = 1 ]; then
    fstab_lines | fstab_with_block | cmp -s - "$FSTAB" || return 1
    for name in "${MEDIA_NAMES[@]}"; do mountpoint -q "$MEDIA_BASE/$name" || return 1; done
  else
    for name in "${MEDIA_NAMES[@]}"; do [ -d "$ROOT${MEDIA_SRC[$name]}" ] || return 1; done
  fi
}
plan_media() {
  local name key
  if [ "$WSL" = 1 ]; then
    echo "/etc/fstab block:"
    fstab_lines | sed 's/^/  /'
  fi
  for name in "${MEDIA_NAMES[@]}"; do
    key=$(media_env_key "$name")
    if [ -n "$key" ]; then echo "$key=$(media_path "$name")"
    else echo "$name: mounted only; no Domovoi setting reads a '$name' folder"
    fi
  done
}
do_media() {
  local name old t mp
  if [ "$WSL" = 0 ]; then
    for name in "${MEDIA_NAMES[@]}"; do
      if [ ! -d "$ROOT${MEDIA_SRC[$name]}" ]; then
        install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$ROOT${MEDIA_SRC[$name]}"
        log "created ${MEDIA_SRC[$name]} for $SVC_USER"
      fi
    done
    STEP_DONE_MSG="Media folders ready"
    return 0
  fi
  old=$(fstab_block_targets)
  declare -A old_line=()
  for name in "${MEDIA_NAMES[@]}"; do
    old_line[$name]=$(fstab_block_line "$MEDIA_BASE/$name")
    # Never chown/chmod a mounted Windows folder: only the empty mount point.
    mountpoint -q "$MEDIA_BASE/$name" || install -d -m 0755 -o root -g root "$ROOT$MEDIA_BASE/$name"
  done
  if write_if_changed "$FSTAB" 0644 < <(fstab_lines | fstab_with_block); then
    log "updated the media block in /etc/fstab"
  fi
  # A folder already mounted under another line (another source or other
  # options): mount it again as the line now says. A busy one (a room's
  # player has it open) keeps its old mount until the distro restarts.
  for name in "${MEDIA_NAMES[@]}"; do
    mp=$MEDIA_BASE/$name
    if mountpoint -q "$mp" && [ "${old_line[$name]}" != "$(fstab_block_line "$mp")" ]; then
      if umount "$mp"; then log "remounting $mp: its /etc/fstab line changed"
      else warn "$mp is busy; its new /etc/fstab line applies at the distro's next start"; fi
    fi
  done
  # A folder dropped from the list: unmount it (its line is gone).
  for t in $old; do
    case " ${MEDIA_NAMES[*]} " in *" ${t#"$MEDIA_BASE"/} "*) continue ;; esac
    if mountpoint -q "$t"; then umount "$t" && log "unmounted $t (no longer in the list)" || warn "could not unmount $t"; fi
  done
  for name in "${MEDIA_NAMES[@]}"; do
    mp=$MEDIA_BASE/$name
    # mount's status alone is not enough: on WSL 3.0.1, `mount <point>`
    # of a drvfs line whose Windows folder is gone exits 0 and mounts
    # nothing (mount.drvfs is WSL's /init); the same line fails with 32
    # at boot. So the mount point itself says whether it worked.
    if ! mountpoint -q "$mp" && ! { mount "$mp" && mountpoint -q "$mp"; }; then
      # A folder this box has had all along (its line is unchanged) that is
      # missing now, a USB drive unplugged say, doesn't stop a repair run:
      # the server runs without it, and it mounts again at the distro's
      # next start. A folder new to the list has to be there.
      if [ -n "${old_line[$name]}" ] && [ "${old_line[$name]}" = "$(fstab_block_line "$mp")" ]; then
        warn "could not mount ${MEDIA_SRC[$name]} at $mp (is the folder there, its drive connected?); Domovoi runs without it until then. It mounts at the distro's next start, or now with: mount $mp"
        continue
      fi
      die "could not mount ${MEDIA_SRC[$name]} at $mp: does the Windows folder exist?"
    fi
    log "$mp: $(findmnt -no FSTYPE,SOURCE "$mp" 2>/dev/null | tr -s ' ') <- ${MEDIA_SRC[$name]}"
    [ -n "$(media_env_key "$name")" ] || warn "no Domovoi setting reads a '$name' folder: $mp is mounted, nothing points at it"
  done
  STEP_DONE_MSG="Media folders connected"
}

# >>> verified-copy block >>>
# The same text in scripts/linux/provision.sh, character for character
# (domovoi/tests/test_update_verifier.py holds the two together): provision.sh
# runs alone inside the Windows installer's distro, with no copy of this file
# beside it, and needs the same copy before it reads anything of a checkout.
#
# The caller sets VX_REPO (the checkout), VX_USER (the service user, who owns
# it), VX_MIRROR (a bare repository only root can write), VX_SIGNERS (the
# allowed-signers file; signed updates are enforced while it exists),
# VX_REVOKED (a revocation file, or empty), VX_ROOT_UIDS (the owners that
# count as root: 0, and under a test harness its own user) and VX_TOP (where
# the walk up a path for anyone else's write access stops: /, or the
# harness's case directory). A vx_ function that fails returns 1 with the
# reason in VX_ERROR.

VX_PATHS=(scripts/linux docs/LINUX_HOST.md)
VX_SKIP=scripts/linux/tests/
VX_ERROR=""
VX_SHA=""
VX_DIR=""
VX_SIG_STATUS=""
VX_SIG_SIGNER=""
VX_SIG_KEY=""
VX_SIG_DETAIL=""
VX_SIG_ENFORCED=0

# git as root, in the mirror only, with nothing from the environment or the
# system's config deciding what it runs (the mirror's own config is root's).
# No replacement objects and no grafts (rc2 review C3): a refs/replace or an
# info/grafts entry could make git read another commit or tree than the one
# a SHA names, and verify or copy that. The fetch never brings refs/replace
# in, and the mirror has no alternates: its objects are its own.
vx_git() {
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_NAMESPACE -u GIT_CONFIG -u GIT_CONFIG_PARAMETERS \
    -u GIT_CONFIG_COUNT -u GIT_EXEC_PATH -u GIT_TEMPLATE_DIR -u GIT_REPLACE_REF_BASE \
    -u GIT_SSH -u GIT_SSH_COMMAND -u GIT_ASKPASS -u GIT_PROXY_COMMAND -u GIT_EXTERNAL_DIFF \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="${GIT_CONFIG_GLOBAL:-/dev/null}" \
    GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null GIT_TERMINAL_PROMPT=0 \
    git -C "$VX_MIRROR" -c core.hooksPath=/dev/null -c core.fsmonitor=false "$@"
}

vx_root_uid() {
  local u
  for u in $VX_ROOT_UIDS; do [ "$1" = "$u" ] && return 0; done
  return 1
}

# vx_root_only PATH: PATH and every directory above it (to VX_TOP) belong to
# root, and no one else can write them. Root writes there by rename, and a
# directory anyone else could write would let them swap a file or a link in
# between root's steps. Above PATH itself a sticky directory (/tmp) is
# fine: no one else can rename or remove root's entry in it.
vx_root_only() {
  local p st mode first=1
  p=$(readlink -f -- "$1" 2>/dev/null) || p=""
  if [ -z "$p" ] || [ ! -e "$p" ]; then VX_ERROR="$1 is not there"; return 1; fi
  while :; do
    st=$(stat -c '%u %a' -- "$p" 2>/dev/null) || st=""
    if ! [[ $st =~ ^([0-9]+)\ ([0-7]{3,4})$ ]] || ! vx_root_uid "${BASH_REMATCH[1]}"; then
      VX_ERROR="$p can be changed by a user other than root (owner and mode: ${st:-unknown})"
      return 1
    fi
    mode=$((8#${BASH_REMATCH[2]}))
    if (( mode & 8#022 )) && { [ "$first" = 1 ] || ! (( mode & 8#1000 )); }; then
      VX_ERROR="$p can be changed by a user other than root (owner and mode: $st)"
      return 1
    fi
    first=0
    if [ "$p" = / ] || [ "$p" = "$VX_TOP" ]; then break; fi
    p=$(dirname -- "$p")
  done
}

# The mirror: a bare repository, created on first use. Its config has
# transfer.fsckObjects, so every object a fetch brings in is checked (its
# hash, and that it is well-formed) before it is kept.
vx_mirror() {
  if [ -L "$VX_MIRROR" ] || { [ -e "$VX_MIRROR" ] && [ ! -d "$VX_MIRROR" ]; }; then
    VX_ERROR="$VX_MIRROR is not a directory"; return 1
  fi
  if [ ! -d "$VX_MIRROR" ]; then
    mkdir -m 0700 -- "$VX_MIRROR" || { VX_ERROR="can't create $VX_MIRROR"; return 1; }
  fi
  vx_root_only "$VX_MIRROR" || return 1
  if [ ! -f "$VX_MIRROR/HEAD" ]; then
    vx_git -c init.defaultBranch=main init -q --bare >/dev/null || { VX_ERROR="git init --bare $VX_MIRROR failed"; return 1; }
  fi
  vx_git config transfer.fsckObjects true && vx_git config fetch.fsckObjects true \
    && vx_git config gc.autoDetach false && vx_git config core.logAllRefUpdates false \
    || { VX_ERROR="can't configure $VX_MIRROR"; return 1; }
}

# HEAD of the checkout into the mirror (refs/domovoi/head), and the
# branches the checkout last fetched (refs/remotes/origin/*), which the
# branch pin is checked against. git upload-pack, the side that reads the
# checkout, runs as the service user; root's side only takes in its pack.
# Sets VX_SHA.
vx_fetch() {
  local up out
  VX_SHA=""
  if [ "$VX_USER" = root ]; then up="git upload-pack"; else up="runuser -u $VX_USER -- git upload-pack"; fi
  if ! out=$(vx_git -c protocol.file.allow=always fetch --quiet --no-tags --no-recurse-submodules \
      --no-write-fetch-head --no-auto-gc --prune --upload-pack="$up" "$VX_REPO" \
      '+HEAD:refs/domovoi/head' '+refs/remotes/origin/*:refs/remotes/origin/*' 2>&1); then
    VX_ERROR="git fetch of $VX_REPO into $VX_MIRROR failed: $(printf '%s\n' "$out" | tail -n 3 | tr '\n' ' ' | cut -c1-400)"
    return 1
  fi
  vx_git gc --auto --quiet >/dev/null 2>&1 || true
  VX_SHA=$(vx_git rev-parse -q --verify 'refs/domovoi/head^{commit}' 2>/dev/null) || VX_SHA=""
  if ! [[ $VX_SHA =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; then
    VX_ERROR="HEAD of $VX_REPO is not a commit"; VX_SHA=""; return 1
  fi
}

# Why VX_SIGNERS can't be trusted, printed; nothing when it can.
vx_signers_problem() {
  local st
  if [ -L "$VX_SIGNERS" ]; then printf '%s is a symlink' "$VX_SIGNERS"; return 0; fi
  if [ ! -f "$VX_SIGNERS" ]; then printf '%s is not a regular file' "$VX_SIGNERS"; return 0; fi
  st=$(stat -c '%u %a' -- "$VX_SIGNERS" 2>/dev/null) || st=""
  if ! [[ $st =~ ^([0-9]+)\ ([0-7]{3,4})$ ]] || ! vx_root_uid "${BASH_REMATCH[1]}"; then
    printf '%s is not owned by root' "$VX_SIGNERS"; return 0
  fi
  if (( 8#${BASH_REMATCH[2]} & 8#022 )); then
    printf '%s can be written by a user other than root (mode %s)' "$VX_SIGNERS" "${BASH_REMATCH[2]}"
  fi
}

# VX_SHA's signature, in the mirror, into VX_SIG_*. Not enforced (no
# signers file): status 0, VX_SIG_ENFORCED=0, nothing checked. Enforced:
# status 0 only for a signature by a listed key.
vx_verify() {
  local problem err mark info
  local -a cfg=(-c "gpg.ssh.allowedSignersFile=$VX_SIGNERS" -c gpg.ssh.program=ssh-keygen
                -c gpg.program=gpg -c gpg.openpgp.program=gpg -c gpg.x509.program=gpgsm)
  VX_SIG_STATUS="" VX_SIG_SIGNER="" VX_SIG_KEY="" VX_SIG_DETAIL="" VX_SIG_ENFORCED=0
  if [ ! -e "$VX_SIGNERS" ] && [ ! -L "$VX_SIGNERS" ]; then return 0; fi
  VX_SIG_ENFORCED=1
  problem=$(vx_signers_problem)
  if [ -n "$problem" ]; then
    VX_SIG_STATUS=unverified
    VX_SIG_DETAIL="the allowed-signers file cannot be trusted: $problem"
    VX_ERROR=$VX_SIG_DETAIL
    return 1
  fi
  if [ -n "$VX_REVOKED" ]; then cfg+=(-c "gpg.ssh.revocationFile=$VX_REVOKED"); fi
  if err=$(vx_git "${cfg[@]}" verify-commit "$VX_SHA" 2>&1 >/dev/null); then
    info=$(vx_git "${cfg[@]}" log -1 --format='%GS%n%GK' "$VX_SHA" 2>/dev/null) || info=""
    VX_SIG_SIGNER=$(printf '%s\n' "$info" | sed -n 1p)
    VX_SIG_KEY=$(printf '%s\n' "$info" | sed -n 2p)
    VX_SIG_STATUS=verified
    VX_SIG_DETAIL="HEAD is signed by ${VX_SIG_SIGNER:-an allowed key}${VX_SIG_KEY:+ ($VX_SIG_KEY)}, listed in $VX_SIGNERS"
    return 0
  fi
  mark=$(vx_git "${cfg[@]}" log -1 --format='%G?' "$VX_SHA" 2>/dev/null) || mark=""
  if [ "$mark" = N ]; then
    VX_SIG_STATUS=unsigned
    VX_SIG_DETAIL="HEAD is not signed"
  else
    VX_SIG_STATUS=unverified
    VX_SIG_DETAIL="the signature on HEAD is not by a key in $VX_SIGNERS: $(printf '%s\n' "$err" | tail -n 2 | tr '\n' ' ' | sed 's/[[:space:]]*$//' | cut -c1-200)"
  fi
  VX_ERROR=$VX_SIG_DETAIL
  return 1
}

# vx_export PARENT: VX_SHA's own copy of the files root runs or installs,
# written out of the mirror into PARENT/VX_SHA (VX_DIR). Plain files only:
# the commit may hold no symbolic link and no submodule there (root would
# follow a link wherever it pointed). Root-owned, directories 0755, nothing
# writable by anyone else.
vx_export() {
  local parent=$1 p stage list entry mode path odd
  local -a want=()
  VX_DIR=""
  for p in "${VX_PATHS[@]}"; do
    if vx_git cat-file -e "$VX_SHA:$p" 2>/dev/null; then want+=("$p"); fi
  done
  case " ${want[*]-} " in
    *" scripts/linux "*) ;;
    *) VX_ERROR="commit $VX_SHA has no scripts/linux"; return 1 ;;
  esac
  vx_root_only "$parent" || return 1
  stage=$(mktemp -d "$parent/.stage-XXXXXX") || { VX_ERROR="can't write in $parent"; return 1; }
  list=$stage.list
  if ! vx_git ls-tree -r -z --full-tree "$VX_SHA" -- "${want[@]}" >"$list"; then
    rm -rf -- "$stage" "$list"; VX_ERROR="git ls-tree $VX_SHA failed"; return 1
  fi
  odd=""
  while IFS= read -r -d '' entry; do
    mode=${entry%% *}
    path=${entry#*$'\t'}
    case $path in "$VX_SKIP"*) continue ;; esac
    case $mode in
      100644|100755) ;;
      120000) odd="$path is a symbolic link"; break ;;
      160000) odd="$path is a submodule"; break ;;
      *) odd="$path has mode $mode"; break ;;
    esac
  done <"$list"
  rm -f -- "$list"
  if [ -n "$odd" ]; then
    rm -rf -- "$stage"
    VX_ERROR="commit $VX_SHA: $odd; the copy root runs from holds plain files only"
    return 1
  fi
  if ! vx_git -c tar.umask=0022 archive --format=tar "$VX_SHA" -- "${want[@]}" ":(exclude)${VX_SKIP%/}" \
      | tar -x --no-same-owner --no-same-permissions -C "$stage" -f -; then
    rm -rf -- "$stage"; VX_ERROR="git archive of $VX_SHA into $stage failed"; return 1
  fi
  odd=$(find "$stage" -mindepth 1 ! -type f ! -type d -print -quit 2>/dev/null) || odd="?"
  if [ -n "$odd" ]; then
    rm -rf -- "$stage"; VX_ERROR="the copy of $VX_SHA holds something other than plain files ($odd)"; return 1
  fi
  chmod -R u+rwX,go-w,go+rX "$stage" && chmod 0755 "$stage" || { rm -rf -- "$stage"; VX_ERROR="can't set the modes in $stage"; return 1; }
  if [ -e "$parent/$VX_SHA" ] || [ -L "$parent/$VX_SHA" ]; then rm -rf -- "${parent:?}/$VX_SHA"; fi
  mv -T -- "$stage" "$parent/$VX_SHA" || { rm -rf -- "$stage"; VX_ERROR="can't move the copy to $parent/$VX_SHA"; return 1; }
  VX_DIR=$parent/$VX_SHA
  vx_root_only "$VX_DIR" || return 1
}
# <<< verified-copy block <<<

# ─── The verified copy ───────────────────────────────────────────────────
#
# See the header: what the steps after the checkout read of it, from the
# commit itself. SRC is the copy; src_file names a file in it (in the
# checkout itself only before there is a copy: a dry run before the clone,
# or one that isn't root's). A dry run makes the mirror and the copy in its
# own temp dir, which goes with it: nothing is changed.
SRC=""
src_file() { printf '%s/%s' "${SRC:-$CHECKOUT}" "$1"; }

make_verified_copy() {
  local upd parent
  [ -d "$CHECKOUT/.git" ] && [ -z "$SRC" ] || return 0
  if dry && [ -z "$ROOT" ] && [ "$EUID" != 0 ]; then
    log "a dry run as a user other than root reads the checkout's files as they are"
    return 0
  fi
  VX_REPO=$CHECKOUT
  VX_USER=$SVC_USER
  VX_SIGNERS=$SIGNERS_FILE
  VX_REVOKED=""
  if [ -n "$ROOT" ]; then VX_ROOT_UIDS="0 $EUID"; VX_TOP=$(dirname "$ROOT"); else VX_ROOT_UIDS=0; VX_TOP=/; fi
  if dry; then
    VX_MIRROR=$TMP_DIR/mirror.git
    parent=$TMP_DIR/copy
  else
    upd=$(update_default DOMOVOI_UPDATE_DIR)
    upd=$ROOT${upd:-/var/lib/domovoi-update}
    [ -d "$upd" ] || install -d -m 0755 -o root -g root "$upd" || die "can't create $upd"
    vx_root_only "$upd" || die "$VX_ERROR: root keeps the update unit's verified mirror there"
    VX_MIRROR=$upd/mirror.git
    parent=$ROOT/run/domovoi-provision
  fi
  vx_mirror || die "$VX_ERROR"
  vx_fetch || die "$VX_ERROR"
  if [ -n "$HEAD_SHA" ] && [ "$VX_SHA" != "$HEAD_SHA" ]; then
    die "HEAD of $CHECKOUT moved from $HEAD_SHA to $VX_SHA during this run; nothing of it was used"
  fi
  if ! vx_verify; then
    die "signed updates are enforced by /etc/domovoi/allowed_signers, but $VX_SIG_DETAIL (commit $VX_SHA); nothing of it was used"
  fi
  [ -d "$parent" ] || install -d -m 0755 -o root -g root "$parent" || die "can't create $parent"
  vx_export "$parent" || die "$VX_ERROR"
  SRC=$VX_DIR
  if [ "$DRY_RUN" = 0 ]; then COPY_ACTIVE=$SRC; fi
  log "the checkout's files are read from $SRC: the verified copy of $VX_SHA$([ "$VX_SIG_ENFORCED" = 1 ] && printf ', %s' "$VX_SIG_DETAIL")"
}

# ─── checkout ────────────────────────────────────────────────────────────

g() { as_svc git -C "$CHECKOUT" "$@"; }
# gd DIR ARGS...: git in another directory, as the service user.
gd() { local d=$1; shift; as_svc git -C "$d" "$@"; }

# Where a new clone is made and checked out before it is renamed to
# --checkout-dir: beside it (the same file system), dot-named. Only a
# finished, verified checkout ever appears at --checkout-dir, so a failed
# or killed clone is never taken for one; the next run starts it again.
clone_tmp() { printf '%s/.%s.provision-clone' "$(dirname "$CHECKOUT")" "$(basename "$CHECKOUT")"; }

inputs_checkout() {
  printf '%s\n' "$REPO_URL" "$REF" "$TRACK_BRANCH" "$CHECKOUT" "$SVC_USER"
  if [ -n "$ALLOWED_SIGNERS" ]; then file_sig "$ALLOWED_SIGNERS"; fi
}
check_checkout() { [ -d "$CHECKOUT/.git" ] && [ "$(g remote get-url origin 2>/dev/null)" = "$REPO_URL" ]; }
plan_checkout() { echo "git clone $REPO_URL at $REF into $(clone_tmp), as $SVC_USER; renamed to $CHECKOUT once checked out"; }

# verify_head DIR: HEAD against the allowed-signers file, as root, with
# every program git could be told to run pinned on the command line (the
# checkout's .git/config is the service user's): what apply-update.sh does.
verify_head() {
  local dir=$1
  local -a cfg=(-c "safe.directory=$dir" -c "gpg.ssh.allowedSignersFile=$SIGNERS_FILE"
                -c gpg.ssh.program=ssh-keygen -c gpg.program=gpg
                -c gpg.openpgp.program=gpg -c gpg.x509.program=gpgsm)
  if ! git "${cfg[@]}" -C "$dir" verify-commit HEAD; then
    die "signed updates are enforced by /etc/domovoi/allowed_signers, but HEAD $HEAD_SHA does not verify against it"
  fi
  log "HEAD $HEAD_SHA is signed by $(git "${cfg[@]}" -C "$dir" log -1 --format=%GS HEAD)"
}

# ref_kind REF: how --ref is read, by its shape alone, so a branch can never
# stand in for a tag or a commit of the same name (delta security review
# 2026-10-16, S3: a branch named v1.0.0, or like the pinned SHA, used to be
# preferred). Prints "sha SHA", "tag NAME" or "branch NAME":
#   a full commit SHA (40 or 64 hex digits)     the commit object, nothing else
#   refs/tags/NAME, tags/NAME, or v<digit>...   the tag refs/tags/NAME only
#   refs/heads/NAME, or any other name           origin's branch NAME only
ref_kind() {
  local r=$1
  if [[ $r =~ ^[0-9a-f]{40}$ ]] || [[ $r =~ ^[0-9a-f]{64}$ ]]; then echo "sha $r"
  elif [[ $r == refs/tags/* ]]; then echo "tag ${r#refs/tags/}"
  elif [[ $r == tags/* ]]; then echo "tag ${r#tags/}"
  elif [[ $r == refs/heads/* ]]; then echo "branch ${r#refs/heads/}"
  elif [[ $r =~ ^v[0-9] ]]; then echo "tag $r"
  else echo "branch $r"; fi
}

# A tag this box was installed from, and its commit, kept beside the step
# markers: a published tag never moves, so a later run (a resumed clone)
# that finds it at another commit stops instead of installing that one.
REF_RECORD=$STATE_DIR/checkout-ref
check_tag_record() {
  local name=$1 sha=$2 kind was_name was_sha
  [ -f "$REF_RECORD" ] || return 0
  read -r kind was_name was_sha <"$REF_RECORD" || return 0
  if [ "$kind" = tag ] && [ "$was_name" = "$name" ] && [ "$was_sha" != "$sha" ]; then
    die "the tag $name was commit $was_sha when this box was first set up from it and is $sha now: a published tag never moves, so nothing was installed (ask the publisher; $REF_RECORD holds the first answer)"
  fi
}

# checkout_ref DIR: check --ref out in the fresh clone DIR, on a branch that
# follows origin (a tag or SHA: --track-branch, which must contain it, or
# be behind it on the same line), then make sure HEAD is the commit --ref
# named.
#
# The release channel stable may not exist yet: origin has it from the
# first release on. Until then the defaults take main (and a tag or SHA
# given with --track-branch stable, the Windows installer's, follows main),
# with a warning; the household can switch to stable from Settings >
# Version once there is one. A --ref stable given on the command line has
# no such fallback. The switch is the household's (Settings > Version);
# nothing moves the box to stable by itself. The checkout keeps what was
# asked for (git config domovoi.intendedChannel stable), and the Version
# card says so once the first release is out.
checkout_ref() {
  local d=$1 sha ref=$REF track=$TRACK_BRANCH kind name head intended=""
  if ! gd "$d" rev-parse -q --verify "refs/remotes/origin/stable" >/dev/null; then
    if [ "$ref" = stable ] && [ "$REF_GIVEN" = 0 ]; then
      ref=main
      [ "$TRACK_GIVEN" = 1 ] || track=main
      intended=stable
      warn "$REPO_URL has no stable branch yet (no release has been published): installing main, and this box follows main; switch to stable in Settings > Version after the first release"
    fi
    if [ "$track" = stable ] && [ "$ref" != stable ]; then
      track=main
      intended=stable
      warn "$REPO_URL has no stable branch yet (no release has been published): this box follows main; switch to stable in Settings > Version after the first release"
    fi
  fi
  read -r kind name <<<"$(ref_kind "$ref")"
  case $kind in
    branch)
      sha=$(gd "$d" rev-parse -q --verify "refs/remotes/origin/$name^{commit}") \
        || die "$REPO_URL has no branch $name (--ref takes a branch, a tag such as v1.0.0, or a full commit SHA)"
      gd "$d" checkout -q -B "$name" --track "origin/$name" || die "checkout of $name failed"
      ;;
    sha|tag)
      if [ "$kind" = sha ]; then
        gd "$d" cat-file -e "$name^{commit}" 2>/dev/null || die "$name is not a commit of $REPO_URL"
        sha=$name
      else
        sha=$(gd "$d" rev-parse -q --verify "refs/tags/$name^{commit}") || die "$REPO_URL has no tag $name"
        check_tag_record "$name" "$sha"
        log "the tag $name is commit $sha"
      fi
      gd "$d" rev-parse -q --verify "refs/remotes/origin/$track" >/dev/null \
        || die "$REPO_URL has no branch $track for updates to follow (--track-branch)"
      if ! gd "$d" merge-base --is-ancestor "$sha" "origin/$track"; then
        # Newer than stable, and on main: a release candidate, or the Windows
        # installer's pinned release before stable has moved to it. Releases
        # are cut from main, so the next one holds this commit, and a pull
        # fast-forwards then. Any other branch must contain REF.
        { [ "$track" = stable ] \
            && gd "$d" merge-base --is-ancestor "origin/stable" "$sha" \
            && gd "$d" rev-parse -q --verify "refs/remotes/origin/main" >/dev/null \
            && gd "$d" merge-base --is-ancestor "$sha" "origin/main"; } \
          || die "$ref is not on origin/$track's history, so an update could never fast-forward from it"
        warn "$ref is newer than the latest release on stable; this box follows stable, and updates resume with the next release, which includes $ref"
      fi
      gd "$d" checkout -q -B "$track" "$sha" || die "checkout of $ref failed"
      gd "$d" branch -q --set-upstream-to "origin/$track" || die "can't make $track track origin/$track"
      ;;
  esac
  head=$(gd "$d" rev-parse -q --verify HEAD) || die "can't read HEAD after checking $ref out"
  [ "$head" = "$sha" ] || die "HEAD is $head after checking $ref out, not $sha; nothing was installed"
  if [ -n "$intended" ]; then
    gd "$d" config domovoi.intendedChannel "$intended" || warn "can't record the channel asked for (git config domovoi.intendedChannel $intended)"
  fi
  if [ "$DRY_RUN" = 0 ]; then
    mkdir -p "$STATE_DIR"
    printf '%s %s %s\n' "$kind" "$name" "$sha" >"$REF_RECORD.tmp" && mv -f "$REF_RECORD.tmp" "$REF_RECORD" \
      || warn "can't record which commit --ref $ref named in $REF_RECORD"
  fi
}

do_checkout() {
  local origin tmp dir
  if [ -n "$ALLOWED_SIGNERS" ]; then
    install -d -m 0755 -o root -g root "$(dirname "$SIGNERS_FILE")"
    install -m 0644 -o root -g root "$ALLOWED_SIGNERS" "$SIGNERS_FILE"
    log "installed /etc/domovoi/allowed_signers"
  fi
  tmp=$(clone_tmp)
  if [ -d "$CHECKOUT/.git" ]; then
    origin=$(g remote get-url origin 2>/dev/null) || origin=""
    [ "$origin" = "$REPO_URL" ] || die "$CHECKOUT is a checkout of '${origin:-no origin}', not $REPO_URL"
    log "the checkout exists; left where it is (updates are the dashboard's job)"
    dir=$CHECKOUT
  else
    if [ -e "$CHECKOUT" ] && [ -n "$(ls -A "$CHECKOUT" 2>/dev/null)" ]; then
      die "$CHECKOUT exists, is not empty and is not a git checkout"
    fi
    if [ -e "$tmp" ] || [ -L "$tmp" ]; then
      rm -rf -- "$tmp"
      log "removed an unfinished clone from an earlier run ($tmp)"
    fi
    install -d -m 0755 -o "$SVC_USER" -g "$SVC_USER" "$tmp"
    CLONE_TMP_ACTIVE=$tmp
    log "cloning $REPO_URL (into $tmp until it is checked out)"
    (cd / && as_svc git clone --quiet --no-checkout "$REPO_URL" "$tmp") || die "git clone of $REPO_URL failed"
    checkout_ref "$tmp"
    dir=$tmp
  fi
  HEAD_SHA=$(gd "$dir" rev-parse HEAD)
  log "HEAD $HEAD_SHA ($(gd "$dir" log -1 --format='%cd %s' --date=short HEAD)) on $(gd "$dir" rev-parse --abbrev-ref HEAD), tracking $(gd "$dir" rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo nothing)"
  if [ -e "$SIGNERS_FILE" ]; then
    verify_head "$dir"
  else
    warn "signed updates are not enforced (no /etc/domovoi/allowed_signers): docs/LINUX_HOST.md, Signed updates"
  fi
  if [ "$dir" = "$tmp" ]; then
    # Checked out and verified: now it takes its place, in one rename.
    if [ -d "$CHECKOUT" ]; then rmdir "$CHECKOUT" || die "can't replace the empty $CHECKOUT"; fi
    mv -T "$tmp" "$CHECKOUT" || die "can't move the new checkout from $tmp to $CHECKOUT"
    CLONE_TMP_ACTIVE=""
    log "the checkout is in place at $CHECKOUT"
  fi
  STEP_DONE_MSG="Domovoi ${HEAD_SHA:0:12}"
}

# ─── docker ──────────────────────────────────────────────────────────────

DOCKER_PKGS=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
docker_wanted() {
  printf '%s\n' "containerd.io=${M[containerd_io]}" "docker-buildx-plugin=${M[docker_buildx_plugin]}" \
    "docker-ce-cli=${M[docker_ce_cli]}" "docker-ce=${M[docker_ce]}" "docker-compose-plugin=${M[docker_compose_plugin]}" | LC_ALL=C sort
}
docker_installed() { dpkg-query -W -f='${Package}=${Version}\n' "${DOCKER_PKGS[@]}" 2>/dev/null | LC_ALL=C sort || true; }
# The packages not installed yet, as pkg=<the manifest's version>. One that
# is installed counts at whatever version it is: these are not held, and an
# ordinary apt upgrade moves them later, as on the Beelink; a run of this
# script never takes them back down to the pins.
docker_missing() {
  local have p
  have=$(dpkg-query -W -f='${Package} ${Status}\n' "${DOCKER_PKGS[@]}" 2>/dev/null | awk '/ install ok installed$/ {print $1}') || true
  for p in "${DOCKER_PKGS[@]}"; do
    printf '%s\n' "$have" | grep -qxF -- "$p" || printf '%s=%s\n' "$p" "${M[${p//[-.]/_}]}"
  done
}
in_group() { id -nG "$SVC_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$1"; }

docker_sources_text() {
  cat <<EOF
Types: deb
URIs: ${M[docker_repo_url]}
Suites: ${M[docker_suite]}
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF
}

# docker_key_export SRC DEST: the pinned key alone, armored, from the file
# Docker serves. SRC goes into a throwaway keyring that must then hold
# exactly one primary key, with the manifest's fingerprint; DEST gets
# `gpg --export <that fingerprint>`, so no other key in a download (apt would
# trust every key in the file it is given) ever reaches /etc/apt/keyrings.
docker_key_export() {
  local src=$1 dest=$2 gh listing n fpr
  gh=$(mktemp -d) || die "can't make a temporary keyring"
  if ! GNUPGHOME=$gh gpg --batch --no-tty --quiet --import "$src" >/dev/null 2>&1; then
    rm -rf "$gh"
    die "Docker's apt key from ${M[docker_key_url]} is not a key gpg can read"
  fi
  # Read to the end (no early-quitting reader under pipefail).
  listing=$(GNUPGHOME=$gh gpg --batch --no-tty --with-colons --fixed-list-mode --list-keys 2>/dev/null) || listing=""
  n=$(printf '%s\n' "$listing" | awk -F: '$1=="pub" {n++} END {print n+0}')
  fpr=$(printf '%s\n' "$listing" | awk -F: '$1=="pub" {want=1; next} want && $1=="fpr" {print $10; want=0}')
  if [ "$n" = 1 ] && [ "$fpr" = "${M[docker_key_fpr]}" ]; then
    GNUPGHOME=$gh gpg --batch --no-tty --armor --export "${M[docker_key_fpr]}" >"$dest" 2>/dev/null || : >"$dest"
  fi
  if command -v gpgconf >/dev/null 2>&1; then GNUPGHOME=$gh gpgconf --kill all >/dev/null 2>&1 || true; fi
  rm -rf "$gh"
  [ "$n" = 1 ] || die "Docker's apt key file from ${M[docker_key_url]} holds $n primary keys; apt would trust every one, and only ${M[docker_key_fpr]} is pinned"
  [ "$fpr" = "${M[docker_key_fpr]}" ] || die "Docker's apt key has fingerprint '${fpr:-none}', the manifest pins ${M[docker_key_fpr]}"
  [ -s "$dest" ] || die "gpg exported nothing for ${M[docker_key_fpr]}"
}

inputs_docker() {
  local k
  for k in docker_key_url docker_key_fpr docker_repo_url docker_suite docker_ce docker_ce_cli containerd_io docker_buildx_plugin docker_compose_plugin; do
    echo "$k=${M[$k]}"
  done
  echo "$SVC_USER"
}
check_docker() {
  [ -z "$(docker_missing)" ] && systemctl is-active --quiet docker.service && in_group docker
}
plan_docker() {
  echo "Docker's key: exactly one primary key, fingerprint ${M[docker_key_fpr]}, exported alone to /etc/apt/keyrings/docker.asc"
  docker_wanted | sed 's/^/apt-get install /; s/$/   (when not installed yet; not held)/'
}
do_docker() {
  local key missing
  # https://docs.docker.com/engine/install/ubuntu/ "Install using the apt
  # repository" (deb822 docker.sources), with the key checked against the
  # manifest before apt trusts it.
  key=$TMP_DIR/docker-download.asc
  curl -fsSL --retry 3 -o "$key" "${M[docker_key_url]}" || die "could not download Docker's apt key from ${M[docker_key_url]}"
  docker_key_export "$key" "$TMP_DIR/docker.asc"
  write_if_changed "$APT_KEYRING" 0644 <"$TMP_DIR/docker.asc" || true
  write_if_changed "$APT_SOURCE" 0644 < <(docker_sources_text) || true
  missing=$(docker_missing)
  if [ -n "$missing" ]; then
    apt_get update
    # The manifest's versions for a first install. Not held (apt-mark): on
    # the Beelink docker-ce moves with apt upgrade like any package, and so
    # it does here.
    # shellcheck disable=SC2086
    apt_get install -y $missing
    [ -z "$(docker_missing)" ] || die "docker packages missing after the install: $(docker_missing | tr '\n' ' ')"
  fi
  log "docker packages: $(docker_installed | tr '\n' ' ')"
  systemctl enable --now containerd.service docker.service
  if ! in_group docker; then
    usermod -aG docker "$SVC_USER"
    log "added $SVC_USER to the docker group (root-equivalent: docs/LINUX_HOST.md, Install)"
  fi
  as_svc docker info >/dev/null || die "$SVC_USER cannot use docker"
  STEP_DONE_MSG="Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo installed)"
}

# ─── gpu-runtime ─────────────────────────────────────────────────────────

# Docker's way to an NVIDIA GPU (the NVIDIA Container Toolkit), by the
# helper the update unit runs too: scripts/linux/gpu-runtime.sh says when a
# GPU counts as visible, what it installs (never a driver: on WSL it is
# Windows', on bare metal the administrator's) and how it restarts Docker.
# Skipped where none is visible, so a CPU-only box (the Beelink) gets
# nothing. Run from the verified copy, as os-jobs.sh is, with the manifest
# the commit pins.
#
# The internet answer is not passed: like every other step here (Docker,
# the models, the images), the install downloads what the box needs, under
# never too; never only governs what the box does by itself afterwards, and
# the update unit passes it.
#
# A Docker restart stops every container for a moment and, through
# Requires=docker.service, restarts domovoi-db (and with it a running
# domovoi-core) when they run: on a first install nothing runs yet; on a
# re-run the start step then waits for the core's health as usual.
#
# A helper that fails or refuses (a key that isn't the pinned one, apt
# that would bring a driver, Docker that won't start with the runtime) is a
# warning, not the end of the install: wake-word training is optional and
# then runs on the CPU, nothing was installed or daemon.json is back as it
# was. The helper records the failure, and the update unit
# (apply-update.sh) tries again a day later, or at once when the
# dashboard's "Check the GPU and trainer again" asks. A provision run is
# asked for, so it always tries (--force, in the step's check too): the
# step runs again on the next provision run while the runtime is missing,
# whatever the record says.
gpu_runtime_script() { src_file scripts/linux/gpu-runtime.sh; }
# The helper to ask: the verified copy's, or in a dry run before the clone
# the one beside this script.
gpu_runtime_helper() {
  if [ -f "$(gpu_runtime_script)" ]; then gpu_runtime_script
  elif dry && [ -f "$SCRIPT_DIR/gpu-runtime.sh" ]; then printf '%s' "$SCRIPT_DIR/gpu-runtime.sh"
  else return 1
  fi
}
gpu_runtime() {
  local h
  h=$(gpu_runtime_helper) || return 1
  bash "$h" --wsl "$([ "$WSL" = 1 ] && echo yes || echo no)" "$@"
}
skip_gpu_runtime() {
  local v out
  v=$(update_default DOMOVOI_MANAGE_GPU_RUNTIME)
  case ${v,,} in
    0|false|no|off) echo "DOMOVOI_MANAGE_GPU_RUNTIME=$v in /etc/default/domovoi-update: Docker's GPU runtime is left to the administrator"; return 0 ;;
  esac
  if ! gpu_runtime_helper >/dev/null; then
    # A dry run before the clone with no copy beside this script: planned.
    if dry; then return 1; fi
    echo "this checkout has no scripts/linux/gpu-runtime.sh"
    return 0
  fi
  if out=$(gpu_runtime --detect 2>&1); then return 1; fi
  printf '%s\n' "$out" | sed -n 's/^gpu-runtime: //p' | tail -n 1
}
inputs_gpu_runtime() {
  local k
  for k in nvidia_key_url nvidia_key_fpr nvidia_repo_url nvidia_container_toolkit nvidia_container_toolkit_base \
      libnvidia_container_tools libnvidia_container1; do
    echo "$k=${M[$k]}"
  done
  echo "wsl=$WSL"
  file_sig "$(gpu_runtime_script)"
}
# --force: a recorded failure is no "nothing to do" here.
check_gpu_runtime() { gpu_runtime --check --force --manifest "$MANIFEST_FILE" >/dev/null 2>&1; }
plan_gpu_runtime() {
  echo "NVIDIA's key: exactly one primary key, fingerprint ${M[nvidia_key_fpr]}, exported alone to /etc/apt/keyrings/nvidia-container-toolkit.asc"
  echo "/etc/apt/sources.list.d/nvidia-container-toolkit.sources: ${M[nvidia_repo_url]} /"
  echo "apt-get install --no-install-recommends nvidia-container-toolkit=${M[nvidia_container_toolkit]} nvidia-container-toolkit-base=${M[nvidia_container_toolkit_base]} libnvidia-container-tools=${M[libnvidia_container_tools]} libnvidia-container1=${M[libnvidia_container1]}   (when not installed yet; not held; refused if apt would bring a driver)"
  echo "nvidia-ctk runtime configure --runtime=docker, then restart Docker when that changed /etc/docker/daemon.json"
}
do_gpu_runtime() {
  local out=$TMP_DIR/gpu-runtime.out rc summary
  [ -f "$(gpu_runtime_script)" ] || die "the checkout has no scripts/linux/gpu-runtime.sh"
  # Its lines go to the log as they come; its summary (the last line) is
  # this step's message, or the reason it stopped.
  { rc=0; gpu_runtime --force --manifest "$MANIFEST_FILE" || rc=$?; echo "$rc" >"$out.rc"; } 2>&1 | tee "$out"
  rc=$(cat "$out.rc" 2>/dev/null || echo 1)
  summary=$(sed -n 's/^gpu-runtime: //p' "$out" | tail -n 1)
  if [ "$rc" != 0 ]; then
    warn "Docker's GPU runtime is not set up (scripts/linux/gpu-runtime.sh: ${summary:-stopped, exit $rc}); wake-word training uses the CPU until a run of this script or an update sets it up"
    STEP_DONE_MSG="GPU for containers not set up: ${summary:-the helper stopped (exit $rc)}"
    return 0
  fi
  summary=${summary:-Docker can use the GPU}
  STEP_DONE_MSG=${summary^}
}

# ─── venv ────────────────────────────────────────────────────────────────

# No reader that quits early in these pipes: under pipefail the writer's
# SIGPIPE would fail the whole substitution.
lock_python() {
  sed -n '/^# This file is autogenerated by pip-compile with Python [0-9]/{s/^# This file is autogenerated by pip-compile with Python \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p;q}' "$CHECKOUT/$LOCK_NAME"
}
system_python() { python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null; }

# lock_stanzas NAME...: the lock's requirement lines (name==version and
# every --hash) for those packages and everything they need, found through
# pip-compile's "# via" lines to a fixed point (wheel needs packaging, for
# one). Read as the service user: the checkout is that user's, and root
# never opens a path in it that the user could point elsewhere.
LOCK_CLOSURE_AWK='
function flush() { if (cur != "") { body[cur] = buf; vias[cur] = via } cur = "" }
/^[A-Za-z0-9]/ {
  flush()
  cur = tolower($1); sub(/[=<>!~;\[ ].*/, "", cur)
  buf = $0 "\n"; via = " "; invia = 0
  next
}
cur != "" && /^[ \t]+--hash/ { buf = buf $0 "\n"; next }
cur != "" && /^[ \t]+# via/ {
  invia = 1
  rest = $0; sub(/^[ \t]+# via[ \t]*/, "", rest)
  if (rest != "") { sub(/[ \t].*/, "", rest); via = via tolower(rest) " " }
  next
}
cur != "" && invia && /^[ \t]+#[ \t]+[^ \t]/ {
  v = $0; sub(/^[ \t]+#[ \t]+/, "", v); sub(/[ \t\[].*/, "", v)
  via = via tolower(v) " "
  next
}
/^#/ || /^[ \t]*$/ { flush(); next }
END {
  flush()
  n = split(roots, r, " ")
  for (i = 1; i <= n; i++) sel[tolower(r[i])] = 1
  changed = 1
  while (changed) {
    changed = 0
    for (p in vias) {
      if (p in sel) continue
      for (s in sel) if (index(vias[p], " " s " ")) { sel[p] = 1; changed = 1; break }
    }
  }
  for (p in sel) if (p in body) printf "%s", body[p]
}'
lock_stanzas() { as_svc awk -v roots="$*" "$LOCK_CLOSURE_AWK" "$CHECKOUT/$LOCK_NAME"; }

# --progress-bar is an install option, not a global one: it goes in the
# environment (PIP_PROGRESS_BAR, set by as_svc) so it can't land in front
# of the subcommand, where pip refuses it.
pip_svc() { svc_py -m pip --disable-pip-version-check --no-input "$@"; }

inputs_venv() {
  file_sig "$CHECKOUT/$LOCK_NAME"
  file_sig "$CHECKOUT/$NODEPS_LOCK_NAME"
  file_sig "$CHECKOUT/pyproject.toml"
  system_python
  echo "$CHECKOUT $SVC_USER"
}
check_venv() { [ -x "$VENV_PY" ] && svc_py -c 'import domovoi.config, fastapi; from domovoi.clients.whisper import import_faster_whisper; import_faster_whisper()' >/dev/null 2>&1; }
plan_venv() { echo "venv $CHECKOUT/.venv; pip install --require-hashes from $LOCK_NAME and $NODEPS_LOCK_NAME (four installs, docs/LINUX_HOST.md Install)"; }

PY_IMPORT_CHECK='#provision:import-check
import importlib, sys
mods = ["fastapi", "uvicorn", "sqlalchemy", "asyncpg", "pydantic_settings", "ollama",
        "argon2", "PIL", "psutil", "httpx", "ctranslate2",
        "onnxruntime", "domovoi.phonemizer", "acoustid",
        "feedparser", "torch", "scipy", "domovoi.config",
        "domovoi.clients.whisper", "domovoi.clients.speaker_encoder"]
bad = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append("%s: %s" % (m, e))
# faster-whisper, without PyAV; the speaker encoder weights, without
# importing resemblyzer (whose package imports librosa).
try:
    from domovoi.clients.whisper import import_faster_whisper
    import_faster_whisper().WhisperModel
except Exception as e:
    bad.append("faster_whisper: %s" % e)
try:
    from domovoi.clients.speaker_encoder import weights_path
    weights_path()
except Exception as e:
    bad.append("speaker encoder weights: %s" % e)
import torch
if torch.version.cuda is not None:
    bad.append("torch is a CUDA build; the lock pins the +cpu wheel")
if bad:
    print("import failures:\n  " + "\n  ".join(bad))
    sys.exit(1)
print("imports OK: %d modules, torch %s" % (len(mods), torch.__version__))'

do_venv() {
  local want have tools reqs
  [ -f "$CHECKOUT/$LOCK_NAME" ] || die "the checkout has no $LOCK_NAME"
  want=$(lock_python)
  have=$(system_python) || have=""
  [ -n "$want" ] || die "$LOCK_NAME does not say which Python it was compiled for"
  [ "$want" = "$have" ] || die "$LOCK_NAME is for Python $want and this box's python3 is ${have:-missing}: every package is installed hash-checked from the lock, and there is no lock for $have"
  if [ ! -x "$VENV_PY" ]; then
    (cd "$CHECKOUT" && as_svc python3 -m venv .venv) || die "python3 -m venv failed"
    log "created $CHECKOUT/.venv"
  fi
  # docs/LINUX_HOST.md "Install from the hash-pinned lock": the build tools
  # from the lock, every pin (sdist-only ones built with those tools), the
  # no-deps lock's packages without their dependencies, then Domovoi itself
  # and nothing else. The build tools go in as their own
  # stanzas of the lock (name==version and every --hash) in a requirements
  # file: the pip that Ubuntu 26.04's venv ships (25.1) takes no hashes
  # from a constraints file, so "-c <lock> setuptools wheel" is refused
  # there. The file is root's, in root's cache directory, readable by the
  # service user (who can't open a root pipe as /dev/stdin).
  emit_frac 50 "Installing build tools"
  tools=$(lock_stanzas setuptools wheel) || tools=""
  printf '%s\n' "$tools" | grep -q '^setuptools==' && printf '%s\n' "$tools" | grep -q '^wheel==' \
    && [[ $tools == *--hash=sha256:* ]] \
    || die "$LOCK_NAME has no hash-pinned setuptools and wheel to build with"
  log "build tools from the lock: $(printf '%s\n' "$tools" | sed -n 's/^\([A-Za-z0-9._-]*==[^ ]*\).*/\1/p' | tr '\n' ' ')"
  install -d -m 0755 "$CACHE_DIR"
  reqs=$(mktemp "$CACHE_DIR/build-tools.XXXXXX")
  printf '%s\n' "$tools" >"$reqs"
  chmod 0644 "$reqs"
  if ! pip_svc install --require-hashes -r "$reqs"; then
    rm -f "$reqs"
    die "pip install of the build tools from $LOCK_NAME failed"
  fi
  rm -f "$reqs"
  emit_frac 100 "Installing the locked packages (CPU torch, Whisper, ONNX Runtime, ...)"
  pip_svc install --require-hashes --no-build-isolation -r "$LOCK_NAME" || die "pip install -r $LOCK_NAME failed"
  # faster-whisper and resemblyzer, pinned and hashed, without what they
  # declare (PyAV, librosa: requirements-no-deps.in).
  [ -f "$CHECKOUT/$NODEPS_LOCK_NAME" ] || die "the checkout has no $NODEPS_LOCK_NAME"
  pip_svc install --require-hashes --no-deps -r "$NODEPS_LOCK_NAME" || die "pip install --no-deps -r $NODEPS_LOCK_NAME failed"
  emit_frac 900 "Installing Domovoi"
  pip_svc install --no-deps --no-build-isolation -e . || die "pip install -e . failed"
  svc_py -c "$PY_IMPORT_CHECK" || die "the venv does not import what the services need"
  STEP_DONE_MSG="Python packages installed"
}

# ─── env ─────────────────────────────────────────────────────────────────

# KEY=VALUE lines for domovoi/.env (windows-installer PLAN.md 4.9; names
# from domovoi/config.py).
env_keys() {
  local name key
  echo "WHISPER_MODEL=${M[whisper_model]}"
  echo "WHISPER_DEVICE=cpu"
  echo "WHISPER_COMPUTE_TYPE=int8"
  # The IPv4 literal: Ollama listens on 127.0.0.1 only, and "localhost"
  # would try ::1 first.
  echo "OLLAMA_URL=$OLLAMA_API"
  echo "OLLAMA_MODEL=$(tier_qa)"
  echo "OLLAMA_TOOL_MODEL=$(tier_tool)"
  echo "OLLAMA_TOOL_THINK=false"
  # Written whether it is pulled or not (--no-vision-model): chat then
  # names it as the one to install.
  echo "OLLAMA_VISION_MODEL=$(tier_vision)"
  if [ "$WSL" = 1 ]; then
    # WSL sees no removable drives, so USB adoption has nothing to scan.
    echo "SATELLITE_ADOPTION_ENABLED=false"
  fi
  if [ -n "$LAN_IP" ]; then echo "MPD_HTTP_BASE=http://$LAN_IP"; fi
  for name in "${MEDIA_NAMES[@]}"; do
    key=$(media_env_key "$name")
    if [ -n "$key" ]; then echo "$key=$(media_path "$name")"; fi
  done
}
# Every key env_keys can write, whatever the options of this run: a .env
# this script creates has the template's value of each recorded as the
# script's own, so a later run with another option still replaces it.
ENV_MANAGED_KEYS=(WHISPER_MODEL WHISPER_DEVICE WHISPER_COMPUTE_TYPE OLLAMA_URL OLLAMA_MODEL
  OLLAMA_TOOL_MODEL OLLAMA_TOOL_THINK OLLAMA_VISION_MODEL SATELLITE_ADOPTION_ENABLED MPD_HTTP_BASE
  MUSIC_DIR PICTURES_DIR DOCUMENTS_DIR AUDIOBOOKS_DIR PODCASTS_DIR VIDEOS_DIR)

# What domovoi/.env assigns now to the keys in PROVISION_ENV_NAMES, as
# KEY=VALUE lines, read the way the core reads it (env_bootstrap.env_values:
# quotes stripped, the last line wins; key case ignored, as pydantic does).
PY_ENV_READ='#provision:env-read
import os
from domovoi import config_env_writer, env_bootstrap
have = {}
for k, v in env_bootstrap.env_values(config_env_writer._ENV_FILE.read_text(encoding="utf-8")).items():
    have[k.upper()] = v
for k in os.environ["PROVISION_ENV_NAMES"].split():
    if k in have:
        print("%s=%s" % (k, have[k]))'

# Write exactly the KEY=VALUE lines in PROVISION_ENV_KEYS, through the
# writer the dashboard's Settings use (the rest of the file is kept).
PY_ENV_WRITE='#provision:env-write
import os
from domovoi import config_env_writer
changes = dict(l.split("=", 1) for l in os.environ["PROVISION_ENV_KEYS"].splitlines() if l)
config_env_writer.write_env_values(changes)'

PY_ENV_VERIFY='#provision:env-verify
import os, sys
from domovoi.config import Settings
s = Settings()
bad = []
for l in os.environ["PROVISION_ENV_KEYS"].splitlines():
    if not l:
        continue
    k, v = l.split("=", 1)
    got = getattr(s, k.lower())
    if str(got).lower() != v.lower():
        bad.append("%s: the core would read %r, expected %r" % (k, got, v))
if bad:
    print("\n".join(bad))
    sys.exit(1)'

inputs_env() {
  env_keys
  echo "internet=$INTERNET"
  echo "$CHECKOUT"
}
check_env() { [ -f "$CHECKOUT/domovoi/.env" ]; }
plan_env() {
  if [ -n "$TIER_EFF" ]; then
    echo "tier $TIER_EFF (MemTotal ${MEM_MIB} MiB)"
    env_keys
    echo "(each written only when absent or still as this script last wrote it; a value changed in Settings or by hand is kept)"
  else
    echo "(the keys depend on the pin manifest, read after the clone)"
  fi
  [ -z "$INTERNET" ] || echo "INTERNET_ACCESS=$INTERNET (a new .env only)"
}

# env_py SNIPPET VAR=VALUE...: one of the snippets above, as the service
# user, from the checkout.
env_py() { local py=$1; shift; (cd "$CHECKOUT" && as_svc env "$@" "$VENV_PY" -c "$py"); }

# The record of what this script last wrote, rewritten from the
# associative array named $1 (sorted, root's, 0644: model names, URLs and
# paths, no secrets).
write_env_record() {
  local -n rec=$1
  local k
  write_if_changed "$ENV_RECORD" 0644 < <(for k in "${!rec[@]}"; do printf '%s=%s\n' "$k" "${rec[$k]}"; done | LC_ALL=C sort) || true
}

do_env() {
  local envf=$CHECKOUT/domovoi/.env policy existed=0 before after line k w out
  local -A want=() have=() last=()
  local -a order=() write=() same=() kept=()
  [ ! -f "$envf" ] || existed=1
  before=$(file_sig "$envf")
  # A NEW .env with a random Postgres password and helper secrets, and the
  # internet answer; an existing one is never rewritten (env_bootstrap's
  # contract), and --repair only appends secrets it lacks.
  if [ "$existed" = 0 ]; then
    : >"$ENV_CREATING"
    printf 'pending\n' >"$ENV_STAMP"
  fi
  if [ -n "$INTERNET" ]; then
    svc_py -m domovoi.env_bootstrap --internet "$INTERNET" || die "python -m domovoi.env_bootstrap --internet $INTERNET failed"
  else
    svc_py -m domovoi.env_bootstrap || die "python -m domovoi.env_bootstrap failed"
  fi
  svc_py -m domovoi.env_bootstrap --repair || die "python -m domovoi.env_bootstrap --repair failed"

  while IFS= read -r line; do
    [ -n "$line" ] || continue
    k=${line%%=*}; want[$k]=${line#*=}; order+=("$k")
  done < <(env_keys)
  out=$(env_py "$PY_ENV_READ" PROVISION_ENV_NAMES="${ENV_MANAGED_KEYS[*]} ${order[*]}") || die "reading domovoi/.env failed"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    have[${line%%=*}]=${line#*=}
  done <<<"$out"
  if [ -f "$ENV_RECORD" ]; then
    while IFS= read -r line; do
      [[ $line == ?*=* ]] || continue
      last[${line%%=*}]=${line#*=}
    done <"$ENV_RECORD"
  fi
  # A .env this script created (in this run, or in one killed before it got
  # this far) holds the template's values: the script's own to replace.
  if [ "$existed" = 0 ] || [ -f "$ENV_CREATING" ]; then
    last=()
    for k in "${ENV_MANAGED_KEYS[@]}" "${order[@]}"; do
      if [ -n "${have[$k]+x}" ]; then last[$k]=${have[$k]}; fi
    done
    write_env_record last
    rm -f "$ENV_CREATING"
  fi

  # Each key: written when absent or still as this script last wrote it;
  # one the household set since (Settings, or by hand) is theirs.
  for k in "${order[@]}"; do
    w=${want[$k]}
    if [ -z "${have[$k]+x}" ]; then write+=("$k")
    elif [ "${have[$k]}" = "$w" ]; then same+=("$k")
    elif [ -n "${last[$k]+x}" ] && [ "${have[$k]}" = "${last[$k]}" ]; then write+=("$k")
    else kept+=("$k")
    fi
  done
  if [ "${#write[@]}" -gt 0 ]; then
    printf 'pending\n' >"$ENV_STAMP"
    env_py "$PY_ENV_WRITE" PROVISION_ENV_KEYS="$(for k in "${write[@]}"; do printf '%s=%s\n' "$k" "${want[$k]}"; done)" \
      || die "writing domovoi/.env failed"
    log "domovoi/.env: set ${write[*]}"
  else
    log "domovoi/.env: nothing to set"
  fi
  for k in "${write[@]}" "${same[@]}"; do last[$k]=${want[$k]}; done
  write_env_record last
  for k in "${kept[@]}"; do
    warn "domovoi/.env: $k holds a value this script did not write (set in Settings, or by hand); left as it is (this run would have written ${want[$k]})"
  done
  if [ "${#write[@]}" -gt 0 ] || [ "${#same[@]}" -gt 0 ]; then
    env_py "$PY_ENV_VERIFY" PROVISION_ENV_KEYS="$(for k in "${write[@]}" "${same[@]}"; do printf '%s=%s\n' "$k" "${want[$k]}"; done)" \
      || die "the core would not read domovoi/.env as written"
  fi
  # The start step restarts core and web when this hash moves: only when
  # this script changed the file (now, or in a run killed mid-way).
  after=$(file_sig "$envf")
  if [ "$after" != "$before" ] || [ ! -f "$ENV_STAMP" ] || [ "$(cat "$ENV_STAMP")" = pending ]; then
    printf '%s\n' "$after" >"$ENV_STAMP"
  fi
  if [ -n "$INTERNET" ]; then
    policy=$(svc_py -m domovoi.egress --print-policy | tr -d '[:space:]') || policy=""
    if [ "$policy" != "$INTERNET" ]; then
      if [ "$existed" = 1 ]; then
        warn "domovoi/.env already answers the internet question with '${policy:-unanswered}'; --internet $INTERNET is not written over it (Settings, Internet changes it)"
      else
        die "domovoi/.env should answer the internet question with $INTERNET, the core reads '${policy:-unanswered}'"
      fi
    fi
  fi
  STEP_DONE_MSG="Settings written (tier $TIER_EFF)"
}

# ─── ollama ──────────────────────────────────────────────────────────────

# The unit docs/LINUX_HOST.md gives ("the unit install.sh writes"); the
# Python test holds the two together.
ollama_unit_text() {
  cat <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
}

ollama_served_version() {
  curl -fsS --max-time 5 "$OLLAMA_API/api/version" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p'
}

# fetch_pinned URL DEST SHA256 LABEL: download URL to DEST, resuming a
# partial file, with progress; DEST exists only if its SHA-256 is the pin.
fetch_pinned() {
  local url=$1 dest=$2 sha=$3 label=$4 part=$2.part total attempt got
  install -d -m 0755 "$(dirname "$dest")"
  if sha_ok "$dest" "$sha"; then log "using the cached $dest"; return 0; fi
  rm -f "$dest"
  total=$(curl -fsSLI --max-time 30 "$url" 2>/dev/null | tr -d '\r' | awk 'tolower($1)=="content-length:" {n=$2} END {print n+0}') || total=0
  for attempt in 1 2; do
    if [ -f "$part" ] && [ "$total" -gt 0 ] 2>/dev/null && [ "$(path_bytes "$part")" -ge "$total" ] && ! sha_ok "$part" "$sha"; then
      rm -f "$part"
    fi
    if ! sha_ok "$part" "$sha"; then
      log "downloading $label: $url"
      start_watch "$part" "$total" "Downloading $label"
      if ! curl -fL --retry 5 --retry-delay 5 --connect-timeout 30 -sS -C - -o "$part" "$url"; then
        stop_watch
        [ "$attempt" = 1 ] || die "the download of $label failed: $url"
        warn "the download of $label failed; starting it again from the beginning"
        rm -f "$part"
        continue
      fi
      stop_watch
    fi
    got=$(sha256_of "$part")
    if [ "$got" = "$sha" ]; then break; fi
    rm -f "$part"
    die "$label: SHA-256 mismatch, got $got, pinned $sha ($url); the download was deleted"
  done
  mv -f "$part" "$dest"
  emit_bytes "$(path_bytes "$dest")" "$(path_bytes "$dest")" "Downloaded $label"
}

inputs_ollama() {
  load_ollama_pin
  echo "$OLLAMA_VERSION $OLLAMA_SHA256"
  ollama_unit_text
}
check_ollama() {
  load_ollama_pin
  [ -x "$OLLAMA_BIN" ] && [ "$(cat "$OLLAMA_STAMP" 2>/dev/null)" = "$OLLAMA_VERSION $OLLAMA_SHA256" ] \
    && ollama_unit_text | cmp -s - "$UNIT_DIR/ollama.service" \
    && systemctl is-active --quiet ollama.service \
    && [ "$(ollama_served_version)" = "$OLLAMA_VERSION" ]
}
plan_ollama() {
  if [ -n "$OLLAMA_VERSION" ]; then echo "Ollama $OLLAMA_VERSION, SHA-256 $OLLAMA_SHA256 (docs/LINUX_HOST.md)"
  else echo "the Ollama release docs/LINUX_HOST.md pins, checked against its SHA-256"; fi
}
do_ollama() {
  local changed=0 url tarball v i
  load_ollama_pin
  if [ ! -x "$OLLAMA_BIN" ] || [ "$(cat "$OLLAMA_STAMP" 2>/dev/null)" != "$OLLAMA_VERSION $OLLAMA_SHA256" ]; then
    url=https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-linux-amd64.tar.zst
    tarball=$CACHE_DIR/ollama-linux-amd64-v$OLLAMA_VERSION.tar.zst
    fetch_pinned "$url" "$tarball" "$OLLAMA_SHA256" "Ollama $OLLAMA_VERSION"
    systemctl stop ollama.service >/dev/null 2>&1 || true
    # docs/LINUX_HOST.md: replace lib/ollama whole, then unpack bin/ and lib/.
    rm -rf "$OLLAMA_PREFIX/lib/ollama"
    tar --zstd -xf "$tarball" -C "$OLLAMA_PREFIX" || die "unpacking $tarball failed"
    [ -x "$OLLAMA_BIN" ] || die "$tarball did not contain bin/ollama"
    printf '%s %s\n' "$OLLAMA_VERSION" "$OLLAMA_SHA256" >"$OLLAMA_STAMP"
    [ "$KEEP_DOWNLOADS" = 1 ] || rm -f "$tarball"
    changed=1
  fi
  if ! id -u ollama >/dev/null 2>&1; then
    useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama
  fi
  if write_if_changed "$UNIT_DIR/ollama.service" 0644 < <(ollama_unit_text); then changed=1; fi
  systemctl daemon-reload
  systemctl enable --quiet ollama.service
  if [ "$changed" = 1 ] || ! systemctl is-active --quiet ollama.service; then
    systemctl restart ollama.service
  fi
  v=""
  for i in $(seq 1 60); do
    v=$(ollama_served_version) || v=""
    [ -z "$v" ] || break
    sleep 1
  done
  [ -n "$v" ] || die "Ollama did not answer on $OLLAMA_API (journalctl -u ollama)"
  [ "$v" = "$OLLAMA_VERSION" ] || die "Ollama serves version $v, docs/LINUX_HOST.md pins $OLLAMA_VERSION"
  log "Ollama $v on $OLLAMA_API"
  detect_gpus
  v=$OLLAMA_VERSION
  STEP_DONE_MSG="Ollama $v"
}

# What Ollama found to run on at its last start this boot. Ollama logs one
# "inference compute" line per device when it starts (a "server config"
# line opens each start); library=cpu is the CPU itself. Prints "devices N"
# (every device of that start, the CPU included), then one GPU name per
# line. N is 0 when this boot's journal has no such start (Ollama not
# started yet, or its lines rotated away): then nothing is known.
# Ollama uses every GPU it finds, inside WSL too (WSL hands NVIDIA cards
# through: two RTX 4070 Ti SUPERs on the dev box, 2026-10-09), and the CPU
# without one (owner decision 2026-10-11: no CUDA_VISIBLE_DEVICES pin).
OLLAMA_GPU_AWK='
/msg="server config"/ { n = 0; devices = 0; next }
/msg="inference compute"/ {
  devices++
  if ($0 ~ /library=[Cc][Pp][Uu]([ \t]|$)/) next
  d = ""
  if (match($0, /description="[^"]*"/)) d = substr($0, RSTART + 13, RLENGTH - 14)
  else if (match($0, /name=("[^"]*"|[^ \t]+)/)) { d = substr($0, RSTART + 5, RLENGTH - 5); gsub(/"/, "", d) }
  if (d != "") gpus[++n] = d
}
END { print "devices " devices + 0; for (i = 1; i <= n; i++) print gpus[i] }'
ollama_gpus() {
  journalctl -u ollama.service -b --no-pager -o cat 2>/dev/null | awk "$OLLAMA_GPU_AWK" || true
}

# detect_gpus: GPU_JSON from ollama_gpus (a list of names, [] for the CPU
# alone, null when unknown), and a line saying which.
detect_gpus() {
  local out devices names="" name json="" sep=""
  out=$(ollama_gpus) || out=""
  devices=$(printf '%s\n' "$out" | sed -n 's/^devices \([0-9][0-9]*\)$/\1/p')
  [ -n "$devices" ] || devices=0
  if [ "$devices" = 0 ]; then
    GPU_JSON=null
    log "what Ollama runs on is unknown: this boot's journal has no Ollama start to read it from"
    return 0
  fi
  names=$(printf '%s\n' "$out" | sed '/^devices [0-9]*$/d')
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    json+="$sep$(json_str "$name")"
    sep=,
  done <<<"$names"
  GPU_JSON="[$json]"
  if [ -n "$json" ]; then log "Ollama runs on: $(printf '%s\n' "$names" | grep . | tr '\n' ';' | sed 's/;$//; s/;/, /g')"
  else log "Ollama found no GPU: it runs on the CPU"; fi
}

# ─── models ──────────────────────────────────────────────────────────────

# The digest /api/tags reports for an exact tag, or nothing.
# Status 1 when Ollama doesn't answer at all. Right after a pull, Ollama
# 0.34.4 took 51 s to answer GET /api/tags on a fresh WSL distro (its own
# request log), so each try waits up to 3 minutes.
model_digest() {
  local tag=${1//./\\.} json i
  for i in 1 2 3; do
    if json=$(curl -fsS --max-time 180 "$OLLAMA_API/api/tags" 2>/dev/null); then
      printf '%s' "$json" | tr -d '\n' \
        | grep -o "\"name\":\"$tag\"[^}]*\"digest\":\"[0-9a-f]*\"" | head -n 1 \
        | sed 's/.*"digest":"\([0-9a-f]*\)"/\1/' || true
      return 0
    fi
    [ "$i" = 3 ] || sleep 5
  done
  return 1
}

# pull_model TAG I N: POST /api/pull, streaming, with progress lines for
# model I of N.
pull_model() {
  local tag=$1 i=$2 n=$3 line status="" last_status="" ok=0 now last=0 d sum_t sum_c f
  local re_err='"error":"([^"]*)"' re_st='"status":"([^"]*)"' re_dg='"digest":"([^"]*)"' re_t='"total":([0-9]+)' re_c='"completed":([0-9]+)'
  local -A tot=() com=()
  while IFS= read -r line; do
    if [[ $line =~ $re_err ]]; then echo "ollama: ${BASH_REMATCH[1]}" >&2; return 1; fi
    status=""
    if [[ $line =~ $re_st ]]; then status=${BASH_REMATCH[1]}; fi
    if [[ $line =~ $re_dg ]]; then
      d=${BASH_REMATCH[1]}
      if [[ $line =~ $re_t ]]; then tot[$d]=${BASH_REMATCH[1]}; fi
      if [[ $line =~ $re_c ]]; then com[$d]=${BASH_REMATCH[1]}; fi
    fi
    if [ "$status" = success ]; then ok=1; fi
    case $status in pulling\ *|"") ;; *) if [ "$status" != "$last_status" ]; then log "  $tag: $status"; last_status=$status; fi ;; esac
    now=$(date +%s)
    if [ $((now - last)) -ge 2 ] && [ "${#tot[@]}" -gt 0 ]; then
      last=$now
      sum_t=0; sum_c=0
      for d in "${!tot[@]}"; do sum_t=$((sum_t + ${tot[$d]})); sum_c=$((sum_c + ${com[$d]:-0})); done
      f=0
      [ "$sum_t" -eq 0 ] || f=$((sum_c * 1000 / sum_t))
      emit_frac $(( (i * 1000 + f) / n )) "Downloading $tag" "\"bytes\":$sum_c,\"bytes_total\":$sum_t"
    fi
  done < <(curl -sS -N --max-time 14400 -X POST "$OLLAMA_API/api/pull" \
             -H 'Content-Type: application/json' -d "{\"model\":\"$tag\",\"stream\":true}")
  [ "$ok" = 1 ]
}

# The tier's models to pull, "TAG DIGEST" per line: the QA model, the
# router and (unless --no-vision-model) the vision model, each once.
model_pairs() {
  printf '%s %s\n' "$(tier_qa)" "$(tier_qa_digest)"
  if [ "$(tier_tool)" != "$(tier_qa)" ]; then printf '%s %s\n' "$(tier_tool)" "$(tier_tool_digest)"; fi
  if [ "$PULL_VISION" = 1 ] && [ "$(tier_vision)" != "$(tier_qa)" ] && [ "$(tier_vision)" != "$(tier_tool)" ]; then
    printf '%s %s\n' "$(tier_vision)" "$(tier_vision_digest)"
  fi
}

skip_models() { if [ "$SKIP_MODELS" = 1 ]; then echo "--skip-models: no Ollama models pulled"; return 0; fi; return 1; }
inputs_models() { model_pairs; }
check_models() {
  local tag want
  while read -r tag want; do [ "$(model_digest "$tag")" = "$want" ] || return 1; done < <(model_pairs)
}
plan_models() {
  model_pairs | sed 's/^/ollama pull /'
  if [ "$PULL_VISION" = 0 ]; then echo "(--no-vision-model: $(tier_vision) is not pulled)"; fi
}
do_models() {
  local tag want got i=0 n
  n=$(model_pairs | grep -c .)
  if [ "$PULL_VISION" = 1 ]; then
    log "the vision model $(tier_vision) reads pictures in chat (about 1.9 GB; --no-vision-model leaves it out)"
  else
    log "--no-vision-model: $(tier_vision) is not pulled; chat can't read pictures until it is installed (dashboard: Settings, Models)"
  fi
  while read -r tag want; do
    got=$(model_digest "$tag") || got=""
    if [ "$got" = "$want" ]; then
      log "$tag is present (digest ${want:0:12})"
    else
      [ -z "$got" ] || warn "$tag is present with digest ${got:0:12}, not the pinned ${want:0:12}: pulling it again"
      log "pulling $tag"
      pull_model "$tag" "$i" "$n" || die "ollama pull $tag failed"
      got=$(model_digest "$tag") || die "Ollama did not list its models (GET $OLLAMA_API/api/tags) after pulling $tag"
      [ -n "$got" ] || die "$tag is not among Ollama's models after the pull"
      [ "$got" = "$want" ] || die "$tag has digest $got after the pull, the manifest pins $want: the registry now serves another build under that tag"
      log "$tag pulled, digest matches the pin"
    fi
    i=$((i + 1))
  done < <(model_pairs)
  STEP_DONE_MSG="Models $(model_pairs | cut -d' ' -f1 | tr '\n' ' ')"
}

# ─── whisper ─────────────────────────────────────────────────────────────

hf_repo_dir() { echo "$ROOT$SVC_HOME/.cache/huggingface/hub/models--${M[whisper_repo]//\//--}"; }
whisper_bin() { echo "$(hf_repo_dir)/snapshots/${M[whisper_revision]}/model.bin"; }

# faster-whisper through Domovoi's import: the venv has no PyAV (installed
# --no-deps, requirements-no-deps.in), and faster_whisper's package imports
# it at load time; domovoi.clients.whisper stands in for it.
PY_WHISPER='#provision:whisper-download
import sys
from domovoi.clients.whisper import import_faster_whisper
import_faster_whisper()
from faster_whisper.utils import download_model
print(download_model(sys.argv[1], revision=sys.argv[2]))'

skip_whisper() { if [ "$SKIP_MODELS" = 1 ]; then echo "--skip-models: the Whisper model is not downloaded"; return 0; fi; return 1; }
inputs_whisper() {
  local k
  for k in whisper_model whisper_repo whisper_revision whisper_model_bin_sha256; do echo "$k=${M[$k]}"; done
  echo "$SVC_HOME"
}
check_whisper() { sha_ok "$(whisper_bin)" "${M[whisper_model_bin_sha256]}"; }
plan_whisper() { echo "${M[whisper_repo]} at ${M[whisper_revision]}, model.bin SHA-256 ${M[whisper_model_bin_sha256]}"; }
do_whisper() {
  local dir bin got snaps
  dir=$(hf_repo_dir)
  bin=$(whisper_bin)
  start_watch "$dir" "${M[whisper_model_bin_bytes]}" "Downloading Whisper ${M[whisper_model]}"
  svc_py -c "$PY_WHISPER" "${M[whisper_model]}" "${M[whisper_revision]}" >/dev/null || { stop_watch; die "the Whisper download failed"; }
  stop_watch
  [ -f "$bin" ] || die "no model.bin at $bin after the download"
  got=$(sha256_of "$bin")
  if [ "$got" != "${M[whisper_model_bin_sha256]}" ]; then
    rm -rf "$dir/snapshots/${M[whisper_revision]}"
    die "Whisper model.bin SHA-256 mismatch: got $got, pinned ${M[whisper_model_bin_sha256]}; the snapshot was deleted"
  fi
  # One snapshot and no refs/main: the core loads that snapshot by path
  # (domovoi/clients/whisper.py, pinned_snapshot_dir).
  snaps=$(find "$dir/snapshots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | grep -c .) || snaps=0
  [ "$snaps" = 1 ] || warn "$dir holds $snaps snapshots; the core loads by path only when there is exactly one"
  [ ! -e "$dir/refs/main" ] || warn "$dir/refs/main exists; the core loads the model by name, not the pinned snapshot"
  emit_bytes "${M[whisper_model_bin_bytes]}" "${M[whisper_model_bin_bytes]}" "Downloaded Whisper ${M[whisper_model]}"
  STEP_DONE_MSG="Whisper ${M[whisper_model]} at ${M[whisper_revision]:0:12}"
}

# ─── piper ───────────────────────────────────────────────────────────────

PY_PIPER_INFO='#provision:piper-info
from domovoi.config import settings
from domovoi.clients import tts
print(settings.voice_models_dir)
wanted = [(settings.tts_piper_voice or "").strip()] + [v.model_ref for v in tts.SHIPPED_VOICES]
for voice in dict.fromkeys(v for v in wanted if v and "\n" not in v):
    pin = tts.PIPER_PINS.get(voice)
    print(voice)
    print(pin[0] if pin else "-")
    print(pin[1] if pin else 0)'

# Fetch ahead, whatever the internet answer (PLAN 4.8): provisioning is
# online by definition, so the egress gate is held open for this one call.
PY_PIPER_DOWNLOAD='#provision:piper-download
import sys
from pathlib import Path
from domovoi import egress
from domovoi.clients import tts
d = Path(sys.argv[2])
d.mkdir(parents=True, exist_ok=True)
with egress.override_policy("always"):
    print(tts._piper_download(sys.argv[1], d))'

# piper_info: PIPER_DIR, and per voice (the configured one first, then the
# three Domovoi offers) PIPER_VOICES / PIPER_SHAS ("-": no pin) / PIPER_SIZES.
# One value per line, three lines a voice, so a name with spaces (the path
# of an uploaded .onnx) stays whole; an empty name is skipped, and a size
# that isn't a number counts as 0.
PIPER_DIR="" PIPER_VOICES=() PIPER_SHAS=() PIPER_SIZES=()
piper_info() {
  local out i size
  local -a lines
  out=$(svc_py -c "$PY_PIPER_INFO") || return 1
  mapfile -t lines <<<"$out"
  PIPER_DIR=${lines[0]:-}
  PIPER_VOICES=() PIPER_SHAS=() PIPER_SIZES=()
  for ((i = 1; i + 2 < ${#lines[@]} + 1; i += 3)); do
    [ -n "${lines[$i]}" ] || continue
    size=${lines[$((i + 2))]:-0}
    [[ $size =~ ^[0-9]+$ ]] || size=0
    PIPER_VOICES+=("${lines[$i]}") PIPER_SHAS+=("${lines[$((i + 1))]:--}") PIPER_SIZES+=("$size")
  done
  [ -n "$PIPER_DIR" ] && [ ${#PIPER_VOICES[@]} -gt 0 ]
}
inputs_piper() { piper_info || return 1; printf '%s\n' "$PIPER_DIR" "${PIPER_VOICES[@]}" "${PIPER_SHAS[@]}"; }
piper_voice_ok() {  # piper_voice_ok I: voice I is on disk and matches its pin
  sha_ok "$ROOT$PIPER_DIR/${PIPER_VOICES[$1]}.onnx" "${PIPER_SHAS[$1]}" \
    && [ -f "$ROOT$PIPER_DIR/${PIPER_VOICES[$1]}.onnx.json" ]
}
check_piper() {
  local i
  piper_info || return 1
  for i in "${!PIPER_VOICES[@]}"; do
    [ "${PIPER_SHAS[$i]}" = - ] && continue
    piper_voice_ok "$i" || return 1
  done
}
do_piper() {
  local i v total=0 done_bytes=0 got=()
  piper_info || die "could not read the Piper voice setting"
  for i in "${!PIPER_VOICES[@]}"; do total=$((total + PIPER_SIZES[i])); done
  for i in "${!PIPER_VOICES[@]}"; do
    v=${PIPER_VOICES[$i]}
    if [ "${PIPER_SHAS[$i]}" = - ]; then
      warn "the Piper voice '$v' has no pin in PIPER_PINS; the core fetches it on first use, checked against Hugging Face's published digest"
      continue
    fi
    if ! piper_voice_ok "$i"; then
      start_watch "$ROOT$PIPER_DIR/.$v.onnx.part" "${PIPER_SIZES[$i]}" "Downloading the voice $v"
      svc_py -c "$PY_PIPER_DOWNLOAD" "$v" "$PIPER_DIR" >/dev/null || { stop_watch; die "the Piper voice download failed ($v)"; }
      stop_watch
    fi
    sha_ok "$ROOT$PIPER_DIR/$v.onnx" "${PIPER_SHAS[$i]}" || die "$v.onnx does not match its pin ${PIPER_SHAS[$i]}"
    done_bytes=$((done_bytes + PIPER_SIZES[i]))
    emit_bytes "$done_bytes" "$total" "Downloaded the voice $v"
    got+=("$v")
  done
  if [ ${#got[@]} -eq 0 ]; then
    STEP_DONE_MSG="Voice ${PIPER_VOICES[0]} left to the core"
  else
    STEP_DONE_MSG="Voices ${got[*]}"
  fi
}

# ─── images ──────────────────────────────────────────────────────────────

# The database's images exactly as domovoi/docker-compose.yml pins them
# (tag@digest), read from the compose file; the search helper's comes with
# the searxng step, and only for always/sometimes.
DB_SERVICES=(postgres flyway)
compose_refs() { (cd "$CHECKOUT/domovoi" && as_svc docker compose config --images "$@"); }
mpd_tag() { local t; t=$(dotenv_get MPD_IMAGE_TAG); printf '%s' "${t:-domovoi-mpd:latest}"; }
inputs_images() { file_sig "$CHECKOUT/domovoi/docker-compose.yml"; file_sig "$CHECKOUT/domovoi/Dockerfile.mpd"; mpd_tag; }
check_images() {
  local refs r
  refs=$(compose_refs "${DB_SERVICES[@]}") && [ -n "$refs" ] || return 1
  for r in $refs; do as_svc docker image inspect "$r" >/dev/null 2>&1 || return 1; done
  as_svc docker image inspect "$(mpd_tag)" >/dev/null 2>&1
}
plan_images() {
  echo "docker compose pull ${DB_SERVICES[*]} (the digests domovoi/docker-compose.yml pins)"
  echo "docker build -t $(mpd_tag) -f domovoi/Dockerfile.mpd domovoi"
}
do_images() {
  local tag
  # Pulled here rather than at domovoi-db's first start, where the download
  # would have no progress and the start step would wait on it.
  emit_frac 50 "Downloading the database images"
  (cd "$CHECKOUT/domovoi" && as_svc docker compose pull "${DB_SERVICES[@]}") || die "docker compose pull ${DB_SERVICES[*]} failed"
  tag=$(mpd_tag)
  # Exactly what domovoi/mpd_provisioner.py _build_image runs, ahead of the
  # core's first boot (which would build it with no progress, and not at
  # all on a box without internet).
  emit_frac 500 "Building the music player"
  (cd "$CHECKOUT/domovoi" && as_svc docker build --progress=plain -t "$tag" -f "$CHECKOUT/domovoi/Dockerfile.mpd" "$CHECKOUT/domovoi") \
    || die "docker build of $tag failed"
  check_images || die "the database images or $tag are not there after the pull and build"
  STEP_DONE_MSG="Images ready ($tag built)"
}

# ─── searxng ─────────────────────────────────────────────────────────────

internet_policy() { svc_py -m domovoi.egress --print-policy 2>/dev/null | tr -d '[:space:]'; }
searxng_running() { [ "$(as_svc docker inspect -f '{{.State.Running}}' domovoi-searxng 2>/dev/null)" = true ]; }

inputs_searxng() { internet_policy || echo unknown; file_sig "$CHECKOUT/domovoi/docker-compose.yml"; }
check_searxng() {
  case $(internet_policy) in
    always|sometimes) searxng_running ;;
    never) ! searxng_running ;;
    *) return 0 ;;
  esac
}
plan_searxng() { echo "started for always and sometimes (the compose file's pinned image), stopped for never"; }
do_searxng() {
  local policy
  policy=$(internet_policy) || policy=""
  case $policy in
    always|sometimes)
      # The compose file's own digest-pinned image; restart: unless-stopped
      # keeps it up across reboots (LINUX_HOST.md, Internet or not).
      (cd "$CHECKOUT/domovoi" && as_svc docker compose up -d --no-deps searxng) || die "docker compose up -d searxng failed"
      STEP_DONE_MSG="Search helper running (internet: $policy)"
      ;;
    never)
      if searxng_running; then as_svc docker stop domovoi-searxng >/dev/null; fi
      log "the internet answer is never: the search helper stays off"
      STEP_DONE_MSG="Search helper off (internet: never)"
      ;;
    *)
      log "the internet question is unanswered: the search helper is left to Settings, Internet"
      STEP_DONE_MSG="Search helper left as it is"
      ;;
  esac
}

# ─── os-jobs ─────────────────────────────────────────────────────────────

# Ubuntu's own background jobs, set to the internet answer by the helper
# the update unit runs too (scripts/linux/os-jobs.sh says what it masks and
# when): for never, the updaters, news and crash-report timers are masked;
# for always and sometimes they run as shipped; on WSL wsl-pro and snapd
# are disabled and masked whatever the answer. Run from the verified copy,
# as install-update-unit.sh is.
os_jobs_script() { src_file scripts/linux/os-jobs.sh; }

# update_default KEY: the last KEY= value in /etc/default/domovoi-update,
# quotes and blanks stripped; nothing when the file or the key is missing.
update_default() {
  [ -f "$UPDATE_DEFAULTS" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$UPDATE_DEFAULTS" | tail -n 1 | tr -d '\r"'"'"'[:space:]'
}

# DOMOVOI_MANAGE_OS_JOBS=0 (or false/no/off) in /etc/default/domovoi-update
# leaves Ubuntu's jobs to the administrator, here as in the update unit.
skip_os_jobs() {
  local v
  v=$(update_default DOMOVOI_MANAGE_OS_JOBS)
  case ${v,,} in
    0|false|no|off) echo "DOMOVOI_MANAGE_OS_JOBS=$v in /etc/default/domovoi-update: Ubuntu's background jobs are left to the administrator" ;;
    *) return 1 ;;
  esac
}
os_jobs() { bash "$(os_jobs_script)" --internet "$1" --wsl "$([ "$WSL" = 1 ] && echo yes || echo no)" "${@:2}"; }

inputs_os_jobs() { internet_policy || echo unknown; echo "wsl=$WSL"; file_sig "$(os_jobs_script)"; }
check_os_jobs() { local p; p=$(internet_policy) || p=""; [ -f "$(os_jobs_script)" ] && os_jobs "$p" --check >/dev/null 2>&1; }
plan_os_jobs() {
  echo "never: mask apt-daily, apt-daily-upgrade, unattended-upgrades, motd-news, ua-timer, apport-autoreport, snapd.snap-repair"
  echo "always/sometimes: unmask what an earlier never masked"
  if [ "$WSL" = 1 ]; then echo "WSL, any answer: disable and mask wsl-pro.service and snapd"; fi
}
do_os_jobs() {
  local policy
  [ -f "$(os_jobs_script)" ] || die "the checkout has no scripts/linux/os-jobs.sh"
  policy=$(internet_policy) || policy=""
  os_jobs "$policy" || die "scripts/linux/os-jobs.sh stopped (its output above says why)"
  case $policy in
    never) STEP_DONE_MSG="Ubuntu's updaters and reports off (internet: never)" ;;
    always|sometimes) STEP_DONE_MSG="Ubuntu's background jobs as shipped (internet: $policy)" ;;
    *) STEP_DONE_MSG="Ubuntu's background jobs left as they are (internet unanswered)" ;;
  esac
}

# ─── units ───────────────────────────────────────────────────────────────

SHIPPED_UNITS=(domovoi-db.service domovoi-core.service domovoi-web.service)
SLEEP_TARGETS=(sleep.target suspend.target hibernate.target hybrid-sleep.target)
# The journal on disk, so what the core logs (its loop watchdog's report
# included) survives a power cycle (incident 2026-10-14). The update unit
# installs the same file (apply-update.sh, reconcile_journal).
JOURNALD_DROPIN=$ROOT/etc/systemd/journald.conf.d/50-domovoi.conf
journald_dropin_src() { src_file scripts/linux/units/journald-domovoi.conf; }
# DOMOVOI_MANAGE_JOURNAL=0 (or false/no/off) in /etc/default/domovoi-update:
# the administrator decides where the journal lives, here as in updates.
manage_journal() {
  local v
  v=$(update_default DOMOVOI_MANAGE_JOURNAL)
  case ${v,,} in 0|false|no|off) return 1 ;; esac
  return 0
}

# A shipped unit with this box's checkout, home and user (docs/LINUX_HOST.md,
# Make it an appliance: "change User=, WorkingDirectory=, ExecStart=,
# Environment=HOME= and the ReadWritePaths= / BindPaths= lines").
unit_text() {
  local co home
  co=$(sed_repl "$CHECKOUT")
  home=$(sed_repl "$SVC_HOME")
  sed -e "s#/opt/domovoi#$co#g" -e "s#/home/domovoi#$home#g" -e "s#^User=domovoi\$#User=$SVC_USER#" \
    "$(src_file "scripts/linux/units/$1")"
}

restart_grant() {
  echo "$SVC_USER ALL=(root) NOPASSWD: $SYSTEMCTL_PATH --no-block restart domovoi-core.service domovoi-web.service"
}

# Media outside the paths the units already let core and web write
# (LINUX_HOST.md "Media somewhere else"): one drop-in for both.
media_dropin_text() {
  local name p extra=()
  [ "$WSL" = 0 ] || return 0
  for name in "${MEDIA_NAMES[@]}"; do
    p=${MEDIA_SRC[$name]}
    # systemd reads % as a specifier (systemd.unit(5)): %% is a plain %.
    case $p/ in "$CHECKOUT"/*|/mnt/*|/media/*|/run/media/*|/srv/*|"$SVC_HOME"/*) ;; *) extra+=("-${p//%/%%}") ;; esac
  done
  [ "${#extra[@]}" -gt 0 ] || return 0
  printf '[Service]\n# Written by scripts/linux/provision.sh for --media folders.\nReadWritePaths=%s\n' "${extra[*]}"
}

inputs_units() {
  local u
  for u in "${SHIPPED_UNITS[@]}"; do unit_text "$u"; done
  restart_grant
  media_dropin_text
  printf '%s\n' "${SLEEP_TARGETS[@]}"
  if manage_journal; then cat "$(journald_dropin_src)" 2>/dev/null; else echo "journal: left alone"; fi
}
check_units() {
  local u d
  for u in "${SHIPPED_UNITS[@]}"; do unit_text "$u" | cmp -s - "$UNIT_DIR/$u" || return 1; done
  restart_grant | cmp -s - "$SUDOERS_DIR/domovoi-restart" || return 1
  d=$(media_dropin_text)
  for u in domovoi-core.service domovoi-web.service; do
    if [ -n "$d" ]; then printf '%s\n' "$d" | cmp -s - "$UNIT_DIR/$u.d/domovoi-media.conf" || return 1
    else [ ! -e "$UNIT_DIR/$u.d/domovoi-media.conf" ] || return 1; fi
  done
  masks_in_place "${SLEEP_TARGETS[@]}" || return 1
  if manage_journal; then cmp -s "$(journald_dropin_src)" "$JOURNALD_DROPIN" || return 1; fi
}
plan_units() {
  printf '%s\n' "${SHIPPED_UNITS[@]/#//etc/systemd/system/}"
  restart_grant
  if manage_journal; then echo "/etc/systemd/journald.conf.d/50-domovoi.conf (the journal on disk), then restart systemd-journald"
  else echo "the journal left as it is (DOMOVOI_MANAGE_JOURNAL in /etc/default/domovoi-update)"; fi
}
do_units() {
  local u d out changed=()
  [ -x "$ROOT/usr/bin/docker" ] || die "/usr/bin/docker is missing; domovoi-db.service names that path"
  for u in "${SHIPPED_UNITS[@]}"; do
    [ -f "$(src_file "scripts/linux/units/$u")" ] || die "the checkout has no scripts/linux/units/$u"
    if write_if_changed "$UNIT_DIR/$u" 0644 < <(unit_text "$u"); then changed+=("$u"); log "installed /etc/systemd/system/$u"; fi
  done
  d=$(media_dropin_text)
  for u in domovoi-core.service domovoi-web.service; do
    if [ -n "$d" ]; then
      if write_if_changed "$UNIT_DIR/$u.d/domovoi-media.conf" 0644 < <(printf '%s\n' "$d"); then changed+=("$u"); fi
    elif [ -e "$UNIT_DIR/$u.d/domovoi-media.conf" ]; then
      rm -f "$UNIT_DIR/$u.d/domovoi-media.conf"
      changed+=("$u")
    fi
  done
  # docs/LINUX_HOST.md 2b: the plain restart grant, checked by visudo
  # before it goes in. The update unit's grant comes with the update-unit step.
  restart_grant >"$TMP_DIR/sudoers"
  out=$(visudo -cf "$TMP_DIR/sudoers" 2>&1) || die "visudo rejects the restart grant: $out"
  if write_if_changed "$SUDOERS_DIR/domovoi-restart" 0440 <"$TMP_DIR/sudoers"; then
    if ! out=$(visudo -c 2>&1); then
      rm -f "$SUDOERS_DIR/domovoi-restart"
      die "sudo's configuration fails visudo -c with the restart grant in place (removed again): $out"
    fi
    log "installed /etc/sudoers.d/domovoi-restart"
  fi
  # LINUX_HOST.md "Don't suspend". Inside WSL Windows decides when the box
  # sleeps; masked anyway, as on the Beelink.
  mask_units "${SLEEP_TARGETS[@]}"
  install_journald_dropin
  if [ "${#changed[@]}" -gt 0 ]; then
    printf '%s\n' "${changed[@]}" >>"$UNITS_CHANGED_FILE"
    systemctl daemon-reload
  fi
  as_svc sudo -n -l "$SYSTEMCTL_PATH" --no-block restart domovoi-core.service domovoi-web.service >/dev/null 2>&1 \
    || warn "$SVC_USER can't run the restart grant through sudo; the dashboard will show the manual command"
  STEP_DONE_MSG="Services installed"
}

# The journal on disk (JOURNALD_DROPIN above). journald reads its
# configuration at start: restarted when the drop-in is new or changed,
# then the journal so far is moved out of /run. Neither failure is fatal:
# the drop-in takes effect at the next boot anyway.
install_journald_dropin() {
  local src
  if ! manage_journal; then log "the journal left as it is (DOMOVOI_MANAGE_JOURNAL in /etc/default/domovoi-update)"; return 0; fi
  src=$(journald_dropin_src)
  [ -f "$src" ] || die "the checkout has no scripts/linux/units/journald-domovoi.conf"
  if write_if_changed "$JOURNALD_DROPIN" 0644 <"$src"; then
    log "installed /etc/systemd/journald.conf.d/50-domovoi.conf: the journal is kept on disk"
    if systemd_running; then
      systemctl restart systemd-journald.service \
        || warn "systemd-journald did not restart; the journal moves to disk at the next boot"
      journalctl --flush >/dev/null 2>&1 || true
    fi
  fi
}

# ─── timezone ────────────────────────────────────────────────────────────

current_tz() { readlink "$LOCALTIME" 2>/dev/null | sed 's#.*/zoneinfo/##'; }
etc_timezone() { tr -d '[:space:]' 2>/dev/null <"$ROOT/etc/timezone"; }
# /etc/timezone is absent or names ZONE. systemd leaves that file alone,
# and the core reads it before the /etc/localtime link
# (domovoi/host_time.py): a stale one has /v1/time name the old zone with
# the new offset (and satellites copy that name).
etc_timezone_is() { [ ! -e "$ROOT/etc/timezone" ] || [ "$(etc_timezone)" = "$1" ]; }
# WSL: the two files name the same zone (or /etc/localtime names none this
# script could compare).
zone_files_agree() { local z; z=$(current_tz); [ -z "$z" ] || etc_timezone_is "$z"; }

# Bare metal sets --timezone. On WSL the zone follows Windows (owner
# decision 2026-10-11): WSL rewrites /etc/localtime and /etc/timezone to
# Windows' zone at every distro start (wsl.conf [time] useWindowsTimezone,
# default true; seen with interop off too, 2026-10-09), so a zone set here
# would hold only until then, and --timezone is ignored. What the step
# still does there: when the two files disagree (someone ran timedatectl
# inside the distro), /etc/timezone is written from /etc/localtime and
# core and web, which take the zone at their start, restart.
skip_timezone() {
  if [ "$WSL" = 1 ] || [ -n "$TIMEZONE" ]; then return 1; fi
  echo "no --timezone: the zone stays $(current_tz)"
}
inputs_timezone() { if [ "$WSL" = 1 ]; then echo "wsl: the zone follows Windows"; else echo "$TIMEZONE"; fi; }
check_timezone() {
  if [ "$WSL" = 1 ]; then zone_files_agree; else [ "$(current_tz)" = "$TIMEZONE" ] && etc_timezone_is "$TIMEZONE"; fi
}
plan_timezone() {
  if [ "$WSL" = 1 ]; then echo "the zone follows Windows (now $(current_tz)); /etc/timezone made to agree with /etc/localtime if it doesn't"
  else echo "timedatectl set-timezone $TIMEZONE (now $(current_tz))"; fi
}
do_timezone() {
  local z old u restart=()
  if [ "$WSL" = 1 ]; then
    z=$(current_tz)
    if [ -z "$z" ]; then
      warn "/etc/localtime names no zone under /usr/share/zoneinfo: /etc/timezone left as it is"
      STEP_DONE_MSG="Time zone as Windows set it"
      return 0
    fi
    if ! etc_timezone_is "$z"; then
      old=$(etc_timezone)
      write_if_changed "$ROOT/etc/timezone" 0644 < <(printf '%s\n' "$z") || true
      log "/etc/timezone said ${old:-nothing} and /etc/localtime $z: /etc/timezone now says $z"
      for u in domovoi-core.service domovoi-web.service; do
        if systemctl is-active --quiet "$u"; then restart+=("$u"); fi
      done
      if [ "${#restart[@]}" -gt 0 ]; then
        log "restarting ${restart[*]}: they took the zone at their start"
        # --no-block: the core is Type=notify; the start step waits for it,
        # with progress lines and --health-timeout.
        systemctl restart "${restart[@]}" --no-block
      fi
    fi
    zone_files_agree || die "/etc/timezone ($(etc_timezone)) still disagrees with /etc/localtime ($z)"
    STEP_DONE_MSG="Time zone $z (Windows')"
    return 0
  fi
  [ -f "$ZONEINFO/$TIMEZONE" ] || die "unknown time zone $TIMEZONE (no /usr/share/zoneinfo/$TIMEZONE)"
  if ! timedatectl set-timezone "$TIMEZONE" 2>/dev/null; then
    ln -sfn "/usr/share/zoneinfo/$TIMEZONE" "$LOCALTIME"
  fi
  if ! etc_timezone_is "$TIMEZONE"; then printf '%s\n' "$TIMEZONE" >"$ROOT/etc/timezone"; fi
  [ "$(current_tz)" = "$TIMEZONE" ] && etc_timezone_is "$TIMEZONE" || die "the time zone is $(current_tz) after setting $TIMEZONE"
  STEP_DONE_MSG="Time zone $TIMEZONE"
}

# ─── start ───────────────────────────────────────────────────────────────

all_active() {
  local u
  for u in "${SHIPPED_UNITS[@]}"; do systemctl is-active --quiet "$u" || return 1; done
}
healthy() { curl -fsS -o /dev/null --max-time 5 "$CORE_HEALTH_URL" && curl -fsS -o /dev/null --max-time 5 "$WEB_HEALTH_URL"; }

# start_diagnostics: when the start fails, what systemd says about the
# three units and their last 200 journal lines, into this script's own
# output (the Windows installer keeps it in provision.log), so the log
# names the real cause (a port already taken, a migration that failed)
# rather than just "systemctl start failed". Without the setup code the
# core's first boot prints, and without a password in a URL (scrub_log).
start_diagnostics() {
  log "systemctl status ${SHIPPED_UNITS[*]}:"
  systemctl status --no-pager --full --lines=0 "${SHIPPED_UNITS[@]}" 2>&1 | scrub_log | sed 's/^/    /' || true
  log "the last 200 lines of journalctl -u domovoi-db -u domovoi-core -u domovoi-web:"
  journalctl -u domovoi-db.service -u domovoi-core.service -u domovoi-web.service --no-pager -n 200 -o short-iso 2>&1 \
    | scrub_log | sed 's/^/    /' || true
}

# wait_http URL TIMEOUT LABEL UNIT FROM TO: wait for URL, with progress
# lines from FROM to TO permille of the step as the time runs out (the
# core's wait, then the dashboard's, each in its own part of the step).
wait_http() {
  local url=$1 timeout=$2 label=$3 unit=$4 from=$5 to=$6 t0 now last=0
  t0=$(date +%s)
  while :; do
    if curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null; then
      log "$label answered after $(( $(date +%s) - t0 ))s"
      return 0
    fi
    now=$(date +%s)
    if [ $((now - t0)) -ge "$timeout" ]; then
      start_diagnostics
      die "$label did not answer within ${timeout}s ($url; journalctl -u $unit)"
    fi
    # The starts are --no-block: a unit (or the database it needs) that has
    # already failed won't answer, however long this waits.
    if systemctl is-failed --quiet domovoi-db.service 2>/dev/null \
        || systemctl is-failed --quiet "$unit" 2>/dev/null; then
      start_diagnostics
      die "$label won't answer: $(systemctl is-failed --quiet domovoi-db.service 2>/dev/null && echo domovoi-db.service || echo "$unit") failed (journalctl -u $unit)"
    fi
    if [ $((now - last)) -ge 30 ]; then
      last=$now
      emit_frac $(( from + (now - t0) * (to - from) / timeout )) "Waiting for $label ($((now - t0))s)"
      log "waiting for $label ($((now - t0))s): $unit is $(systemctl is-active "$unit" 2>/dev/null || true)"
    fi
    sleep 3
  done
}

inputs_start() {
  local u
  for u in "${SHIPPED_UNITS[@]}"; do file_sig "$UNIT_DIR/$u"; done
  for u in venv units timezone media; do cat "$STEP_DIR/$u" 2>/dev/null || echo "none:$u"; done
  # .env: only a change this script made (do_env's stamp), not every re-run
  # of the env step, and never the household's own edits.
  cat "$ENV_STAMP" 2>/dev/null || echo "none:env-stamp"
  # The zone in effect: Python takes the offset at the process's start, so
  # a zone changed under a running core needs a restart. Bare metal only:
  # on WSL the zone changes with Windows' at a distro start, which starts
  # core and web afresh anyway, and the timezone step restarts them itself
  # when it has to mend /etc/timezone.
  if [ "$WSL" = 0 ]; then echo "tz=$(current_tz) $(cat "$ROOT/etc/timezone" 2>/dev/null)"; fi
}
check_start() { all_active && healthy; }
plan_start() { echo "systemctl enable --now ${SHIPPED_UNITS[*]}; wait for $CORE_HEALTH_URL and $WEB_HEALTH_URL"; }
do_start() {
  local db_changed=0 restart=()
  if [ -f "$UNITS_CHANGED_FILE" ] && grep -qx domovoi-db.service "$UNITS_CHANGED_FILE"; then db_changed=1; fi
  systemctl daemon-reload
  systemctl enable "${SHIPPED_UNITS[@]}" >/dev/null 2>&1 || systemctl enable "${SHIPPED_UNITS[@]}"
  # Something core and web read changed (units, .env, venv, zone, media):
  # restart the ones already running. A unit that is down just starts.
  if [ "$RERUN_REASON" = changed ]; then
    if [ "$db_changed" = 1 ] && systemctl is-active --quiet domovoi-db.service; then restart+=(domovoi-db.service); fi
    systemctl is-active --quiet domovoi-core.service && restart+=(domovoi-core.service)
    systemctl is-active --quiet domovoi-web.service && restart+=(domovoi-web.service)
  fi
  # --no-block, both: the core is Type=notify, so a blocking start would
  # wait for its READY=1 (up to its TimeoutStartSec) with no progress line
  # and past --health-timeout. wait_http below waits instead, reporting,
  # and stops at a unit that failed; so a failed mark from before this run
  # is cleared first (a queued start leaves it until the job runs, which
  # for the core waits for the database's).
  local u
  for u in "${SHIPPED_UNITS[@]}"; do
    if systemctl is-failed --quiet "$u" 2>/dev/null; then systemctl reset-failed "$u" || true; fi
  done
  if [ "${#restart[@]}" -gt 0 ]; then
    log "restarting ${restart[*]}: what they read changed"
    systemctl restart "${restart[@]}" --no-block
  fi
  # The documented start, which is a no-op for what already runs.
  if ! systemctl start "${SHIPPED_UNITS[@]}" --no-block; then
    start_diagnostics
    die "systemctl start ${SHIPPED_UNITS[*]} failed (its status and the units' last 200 journal lines are in the output above; journalctl -u domovoi-db -u domovoi-core -u domovoi-web)"
  fi
  rm -f "$UNITS_CHANGED_FILE"
  wait_http "$CORE_HEALTH_URL" "$HEALTH_TIMEOUT" "the core (/v1/health)" domovoi-core.service 0 800
  wait_http "$WEB_HEALTH_URL" 300 "the dashboard (/api/health)" domovoi-web.service 800 990
  STEP_DONE_MSG="Domovoi is running"
}

# ─── update-unit ─────────────────────────────────────────────────────────

# This box was installed from the hash-pinned lock (the venv step), so its
# updates re-sync from it too (owner decision 2026-10-11): DOMOVOI_USE_LOCK=1
# in /etc/default/domovoi-update, the file domovoi-update.service reads
# (docs/LINUX_HOST.md, The hash-pinned lock). apply-update.sh's locked
# re-sync installs the build tools the way do_venv does, which the pip
# Ubuntu 26.04's venv ships (25.1) takes. A file without the setting gets
# it; a DOMOVOI_USE_LOCK the administrator set to something else is theirs
# and stays, with a warning. A box this script did not install (the
# Beelink) keeps the doc's default, off.
#
# The file is changed in place: the lines are added to what it holds, by
# rename from a temp file that takes the file's own mode and owner (an
# administrator's 0600 file stays 0600). A symlink there is left alone,
# with a warning: this script does not write through a link into a file
# somewhere else; add the line to the file it points at yourself.
use_lock_set() { grep -q '^[[:space:]]*DOMOVOI_USE_LOCK[[:space:]]*=' "$UPDATE_DEFAULTS" 2>/dev/null; }
use_lock_lines() {
  printf '%s\n' "# Written by scripts/linux/provision.sh: this box was installed from the" \
    "# hash-pinned lock, so its updates re-sync from it too (docs/LINUX_HOST.md," \
    "# The hash-pinned lock). Set it to 0 to re-sync through pip's resolver." \
    "DOMOVOI_USE_LOCK=1"
}
# The branches updates may follow: the two release channels (owner decision
# 2026-10-16; docs/LINUX_HOST.md, Release channels), so the dashboard's
# channel switch works and the update unit refuses any other branch. Written
# like the lock line: only into a file that doesn't set it, and only while
# the checkout follows one of them (a box someone pointed at a branch of
# their own keeps updating from it, with a warning).
channel_pin_set() { grep -q '^[[:space:]]*DOMOVOI_UPSTREAM_BRANCH[[:space:]]*=' "$UPDATE_DEFAULTS" 2>/dev/null; }
channel_pin_lines() {
  printf '%s\n' "# Written by scripts/linux/provision.sh: updates follow one of Domovoi's" \
    "# release channels, stable or main (Settings > Version switches between" \
    "# them; docs/LINUX_HOST.md, Release channels). Any other branch is refused." \
    'DOMOVOI_UPSTREAM_BRANCH="stable main"'
}
checkout_upstream() { g rev-parse --symbolic-full-name '@{u}' 2>/dev/null || true; }
follows_a_channel() {
  case $(checkout_upstream) in refs/remotes/origin/stable|refs/remotes/origin/main) return 0 ;; esac
  return 1
}
inputs_update_unit() {
  file_sig "$(src_file scripts/linux/install-update-unit.sh)"
  file_sig "$(src_file scripts/linux/update-verifier.sh)"
  echo "$CHECKOUT $SVC_USER"
  use_lock_lines
  channel_pin_lines
}
check_update_unit() {
  [ -f "$UNIT_DIR/domovoi-update.service" ] && [ -f "$SUDOERS_DIR/domovoi-update" ] \
    && { [ ! -f "$(src_file scripts/linux/update-verifier.sh)" ] \
         || grep -q 'verify-update.sh prepare' "$UNIT_DIR/domovoi-update.service"; } \
    && { [ -L "$UPDATE_DEFAULTS" ] || { use_lock_set && { channel_pin_set || ! follows_a_channel; }; }; }
}
plan_update_unit() {
  echo "DOMOVOI_USE_LOCK=1 in /etc/default/domovoi-update (unless it sets DOMOVOI_USE_LOCK already)"
  echo 'DOMOVOI_UPSTREAM_BRANCH="stable main" there too (unless it sets one already, or the checkout follows another branch)'
  echo "bash scripts/linux/install-update-unit.sh --repo $CHECKOUT --user $SVC_USER"
}
# add_update_defaults: append the lines on stdin to /etc/default/domovoi-update,
# creating it 0644, or keeping an administrator's file with its lines, mode
# and owner (by rename from a temp file beside it).
add_update_defaults() {
  local mode owner tmp lines
  lines=$(cat)
  if [ -f "$UPDATE_DEFAULTS" ]; then
    mode=$(stat -c %a "$UPDATE_DEFAULTS") && owner=$(stat -c %u:%g "$UPDATE_DEFAULTS") \
      || die "can't read the mode and owner of /etc/default/domovoi-update"
    tmp=$(mktemp "$(dirname "$UPDATE_DEFAULTS")/.domovoi-update.XXXXXX") || die "can't write in $(dirname "$UPDATE_DEFAULTS")"
    if ! { { cat "$UPDATE_DEFAULTS"; echo; printf '%s\n' "$lines"; } >"$tmp" && chmod "$mode" "$tmp" \
           && chown "$owner" "$tmp" && mv -f "$tmp" "$UPDATE_DEFAULTS"; }; then
      rm -f "$tmp"
      die "can't add to /etc/default/domovoi-update: $(printf '%s\n' "$lines" | grep -v '^#' | tr '\n' ' ')"
    fi
  else
    write_if_changed "$UPDATE_DEFAULTS" 0644 < <(printf '%s\n' "$lines") || true
  fi
}
do_update_unit() {
  local have up
  # Before the installer, so that its pre-flight reports the lock as on.
  if [ -L "$UPDATE_DEFAULTS" ]; then
    have=""
    if use_lock_set; then have=$(update_default DOMOVOI_USE_LOCK); fi
    if [ "$have" != 1 ]; then
      warn "/etc/default/domovoi-update is a symlink, left as it is: this script does not write through it, so updates re-sync through pip's resolver unless the file it points to says DOMOVOI_USE_LOCK=1 (docs/LINUX_HOST.md, The hash-pinned lock)"
    fi
  elif ! use_lock_set; then
    use_lock_lines | add_update_defaults
    log "updates re-sync from the lock: DOMOVOI_USE_LOCK=1 in /etc/default/domovoi-update"
  else
    have=$(update_default DOMOVOI_USE_LOCK)
    if [ "$have" = 1 ]; then log "updates re-sync from the lock (DOMOVOI_USE_LOCK=1 in /etc/default/domovoi-update)"
    else warn "/etc/default/domovoi-update sets DOMOVOI_USE_LOCK=$have, left as it is: updates re-sync through pip's resolver, which checks no hashes, though this box was installed from the lock"; fi
  fi
  # The channel pin, the same way (a symlinked file is left alone above).
  if [ ! -L "$UPDATE_DEFAULTS" ]; then
    up=$(checkout_upstream)
    if channel_pin_set; then
      log "updates follow what /etc/default/domovoi-update pins ($(grep '^[[:space:]]*DOMOVOI_UPSTREAM_BRANCH[[:space:]]*=' "$UPDATE_DEFAULTS" | tail -n 1 | tr -d '\r' | sed 's/^[[:space:]]*//')); the checkout tracks ${up#refs/remotes/}"
    elif follows_a_channel; then
      channel_pin_lines | add_update_defaults
      log "updates follow ${up#refs/remotes/}, one of the release channels: DOMOVOI_UPSTREAM_BRANCH=\"stable main\" in /etc/default/domovoi-update"
    else
      warn "the checkout follows ${up:-no upstream branch}, not a release channel (stable or main): no branch pin written to /etc/default/domovoi-update, so updates follow that branch"
    fi
  fi
  bash "$(src_file scripts/linux/install-update-unit.sh)" --repo "$CHECKOUT" --user "$SVC_USER" \
    || die "scripts/linux/install-update-unit.sh stopped (its output above says why)"
  STEP_DONE_MSG="Updater installed"
}

# ─── finish ──────────────────────────────────────────────────────────────

compute_restart_required() {
  RESTART_REQUIRED=false
  [ "${WSL:-0}" = 1 ] || return 0
  if ! systemd_running; then RESTART_REQUIRED=true; return 0; fi
  if [ -f "$PENDING_FILE" ]; then
    if [ "$(cat "$PENDING_FILE")" = "$(instance_id)" ]; then
      RESTART_REQUIRED=true
    elif [ "$DRY_RUN" = 0 ]; then
      rm -f "$PENDING_FILE"
    fi
  fi
}

do_finish() {
  local code_file
  compute_restart_required
  code_file=$SVC_HOME/.domovoi/setup-code.txt
  if [ -s "$ROOT$code_file" ]; then
    log "the setup code is in $code_file (root and $SVC_USER can read it; it is never printed here)"
  else
    log "no setup code file at $code_file: the admin is already claimed, or the core has not written it yet"
  fi
  log "dashboard: $DASHBOARD_URL (the core on :6370)"
  detect_gpus
  if [ "$RESTART_REQUIRED" = true ]; then
    log "restart required: /etc/wsl.conf changed. Run 'wsl --terminate <this distro>' and start it again."
  fi
}

# ─── Main ────────────────────────────────────────────────────────────────

# take_lock: one run at a time, or status 1 (another run holds it). The
# lock is held by a small helper, never by a descriptor of this shell:
# flock -o keeps it open in flock alone, and its command (tail --pid) lives
# exactly as long as this shell. So nothing this run starts (pip, apt,
# docker) inherits the lock, and a run killed even with SIGKILL frees it
# within a second, whatever of its children outlive it. on_exit
# (release_lock) frees it at once on every other way out.
take_lock() {
  local ok=$TMP_DIR/lock-held rc i
  flock -n -E 75 -o "$LOCK_FILE" sh -c ': >"$1"; exec tail --pid="$2" -f /dev/null' lock-holder "$ok" "$$" \
    </dev/null >/dev/null 2>&1 &
  LOCK_PID=$!
  for i in $(seq 1 400); do
    [ ! -e "$ok" ] || return 0
    if ! kill -0 "$LOCK_PID" 2>/dev/null; then
      rc=0
      wait "$LOCK_PID" || rc=$?
      LOCK_PID=""
      [ "$rc" != 75 ] || return 1
      die "flock on $LOCK_FILE failed (exit $rc)"
    fi
    sleep 0.05
  done
  die "flock on $LOCK_FILE did not answer within 20 s"
}

if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" != 0 ]; then
  refuse 1 failed "run it as root (sudo bash $0 ...); --dry-run and --help work without"
fi

TMP_DIR=$(mktemp -d)

if [ "$DRY_RUN" = 0 ]; then
  install -d -m 0755 "$STATE_DIR" "$STEP_DIR"
  if ! take_lock; then
    # Busy: its own exit code, and result.json stays the running run's.
    refuse 4 busy "another provision run is in progress (it holds $LOCK_FILE); wait for it to finish, then run this again"
  fi
fi
STARTED=1

[ -z "$ROOT" ] || log "(test harness: system paths under $ROOT)"
if dry; then log "dry run: nothing is changed"; fi
log "repo $REPO_URL @ $REF -> $CHECKOUT as $SVC_USER; internet ${INTERNET:-unanswered}; tier $TIER; WSL $([ "$WSL" = 1 ] && echo yes || echo no)"
if [ -n "$TIMEZONE_IGNORED" ]; then
  warn "--timezone $TIMEZONE_IGNORED is ignored on WSL: the distro's zone follows Windows, which WSL sets at every distro start (change it in Windows' settings)"
fi

SVC_HOME=$(svc_home) || SVC_HOME=""
[ -n "$SVC_HOME" ] || SVC_HOME=/home/$SVC_USER

run_step preflight

if ! systemd_running && [ "$WSL" = 1 ]; then
  # A WSL distro without systemd: write the service user and /etc/wsl.conf
  # (systemd=true among them), then stop. The distro has to restart before
  # anything else can run.
  STEP_INDEX=2
  run_step user
  SVC_HOME=$(svc_home) || SVC_HOME=/home/$SVC_USER
  STEP_INDEX=3
  run_step wsl-config
  compute_restart_required
  RESTART_REQUIRED=true
  log "systemd is not running yet: terminate the distro (wsl --terminate <name>), start it, and run this again"
  final_line rerun_after_restart 3 "Restart the distro, then run provisioning again"
  exit 3
fi

for step in "${STEP_IDS[@]:1}"; do
  [ "$step" != finish ] || break
  if [ "$step" = "$STOP_BEFORE" ]; then
    # --stop-before: everything up to here is done; the caller runs this
    # again without it (the Windows installer checks the PC's ports in
    # between). result.json says "stopped", so no one reads it as ok.
    log "stopping before the $step step (--stop-before $step)"
    compute_restart_required
    final_line stopped 0 "Stopped before: ${STEP_TITLE[$step]} (--stop-before $step)"
    exit 0
  fi
  run_step "$step"
  case $step in
    user) SVC_HOME=$(svc_home) || true; [ -n "$SVC_HOME" ] || SVC_HOME=/home/$SVC_USER ;;
    checkout)
      if [ -d "$CHECKOUT/.git" ] && [ -z "$HEAD_SHA" ]; then HEAD_SHA=$(g rev-parse HEAD 2>/dev/null || true); fi
      make_verified_copy
      if ! manifest_ready; then
        if dry; then log "no checkout yet: the steps after the clone are planned without the pin manifest"
        else die "no pin manifest in the checkout ($CHECKOUT/scripts/linux/provision-manifest.conf)"; fi
      else
        log "pins from $MANIFEST_FILE; tier $TIER_EFF (MemTotal ${MEM_MIB} MiB)"
      fi
      if [ -d "$CHECKOUT/.git" ] && [ -z "$HEAD_SHA" ]; then HEAD_SHA=$(g rev-parse HEAD 2>/dev/null || true); fi
      ;;
  esac
done

STEP_ID=finish
STEP_INDEX=$STEP_TOTAL
STEP_W=${STEP_WEIGHT[finish]}
if dry; then
  compute_restart_required
  final_line dry_run 0 "Dry run: nothing was changed"
  exit 0
fi
emit start "${STEP_TITLE[finish]}"
do_finish
CUM_PCT=100
final_line ok 0 "$([ "$RESTART_REQUIRED" = true ] && echo "Domovoi is running; restart the distro to apply /etc/wsl.conf" || echo "Domovoi is running")"
exit 0
