#!/usr/bin/env bash
# apply-update.sh: apply what the dashboard pulled, or roll it back.
#
# Run as root by domovoi-update.service (docs/LINUX_HOST.md, "Updates from
# the dashboard"). When that unit is installed, the version panel's Restart
# button starts it instead of bouncing domovoi-core and domovoi-web
# directly (domovoi/self_restart.py). Start it with
# `sudo systemctl start domovoi-update.service`; don't run the script by hand
# while the unit might be running too.
#
# The "previous" SHA is the last one this script applied and saw healthy
# (applied_sha in DOMOVOI_UPDATE_DIR). Before the first run it falls back to
# the pre-pull SHA the core records at pull time (git_version.pull()), then
# to ORIG_HEAD when that is an ancestor of HEAD, then to HEAD itself.
#
# Nothing new since the previous SHA: a plain restart, which is what the
# dashboard's "Restart Domovoi" is when nothing is waiting to load. Stop web
# and core, start them, health-check them. No backup, no dependency sync, no
# MPD rebuild, and the database is left alone: domovoi-db (compose up plus
# Flyway) is restarted only when Postgres doesn't answer, when the
# checkout ships a migration its Flyway history doesn't hold as applied
# (plain_db_reason), or when the GPU step below restarted Docker. While web
# and core are stopped, Docker's GPU runtime is set up as in step 6 of an
# update; after the healthy start come steps 10-12 of an update (the search
# helper, Ubuntu's jobs, the wake-word trainer image).
#
# Before either path, touching nothing: the checkout must be the pinned
# upstream (DOMOVOI_UPSTREAM_URL / DOMOVOI_UPSTREAM_BRANCH, when set), and
# HEAD must carry an SSH signature by a key in the root-owned
# allowed-signers file while that file exists (check_signature; "Signed
# updates" below). Without the file the run warns and goes on.
#
# On either path, before anything uses docker compose (after the real
# update's pre-flight): a domovoi/.env without LETTA_TOKEN or SEARXNG_SECRET
# gets values generated for this install appended, as the service user
# (repair_env_secrets); compose refuses to run without them. A repair that
# fails aborts the run with nothing changed.
#
# Something new: a real update.
#   1. Refuse, touching nothing, if tracked files have uncommitted changes:
#      the rollback below could not restore that tree.
#   2. pg_dump -Fc the database through the compose Postgres container into
#      the backups dir, and its <db>_test twin when that exists (plugin
#      migrations are applied to both). The newest
#      DOMOVOI_UPDATE_KEEP_BACKUPS of each are kept. A failed backup aborts
#      the update before anything is stopped.
#   3. Stop domovoi-web and domovoi-core: at most DOMOVOI_UPDATE_STOP_TIMEOUT
#      seconds, then SIGKILL whatever is still stopping (stop_services).
#   4. Re-sync the venv the LINUX_HOST.md way if pyproject.toml or a
#      requirements lock changed: through pip's resolver, as before the
#      lock existed, unless DOMOVOI_USE_LOCK=1; then from the hash-pinned
#      requirements-linux-py314.lock (and requirements-linux-py314-no-deps.lock,
#      installed --no-deps) when the checkout has one for the
#      venv's Python, otherwise through the resolver as a `warn` step
#      ("lock not applied: <why>", shown on the Version card). After a
#      re-sync, packages a release stopped using are uninstalled
#      (drop_retired_packages: piper-tts and piper-phonemize, GPL;
#      edge-tts, LGPL), since pip never removes what nothing requires any
#      more.
#   5. Rebuild the MPD image the way mpd_provisioner.py does if
#      Dockerfile.mpd or mpd.conf changed, and remove the room containers so
#      the core recreates them (same data volumes) from the new image.
#   6. Docker's GPU runtime, where an NVIDIA GPU is visible
#      (reconcile_gpu_runtime, scripts/linux/gpu-runtime.sh, the helper
#      provision.sh runs too): the NVIDIA Container Toolkit installed when
#      missing (never a driver; nothing downloaded under never) and the
#      nvidia runtime in Docker's daemon.json, restarting Docker when that
#      changed. Here, with core and web stopped, because a Docker restart
#      bounces Postgres and the rooms' players (restart: unless-stopped, so
#      they come back) and step 7 restarts domovoi-db after it. Never fatal
#      and no rollback for it: a failure is a `warn` step; a step is recorded
#      only when something changed (ok) or the helper failed (warn). A
#      failure is not tried again for a day (the helper's last-failure
#      record; its "not retried" adds no step), so a box where it can't be
#      set up doesn't run apt-get update, or restart Docker, at every
#      Restart; the dashboard's "Check the GPU and trainer again" leaves
#      gpu-runtime-retry in the core's wake_trainer dir
#      (~/.domovoi/wake_trainer), and the next run tries at once (--force)
#      and removes it. DOMOVOI_MANAGE_GPU_RUNTIME=0 leaves it alone.
#   7. systemctl restart domovoi-db: compose up plus Flyway.
#   8. Start core and web; both must answer their health endpoint within
#      DOMOVOI_UPDATE_HEALTH_TIMEOUT seconds.
#   9. Every plugin that loaded before the update still loads: none that was
#      enabled and not at load_error in the plugins registry is at load_error
#      now. The core keeps a failing plugin from taking it down, so its
#      health endpoint stays green through one.
#  10. The search helper (SearXNG) follows the internet answer: started for
#      INTERNET_ACCESS=always|sometimes, stopped for never, left alone while
#      unanswered (reconcile_searxng). Never fatal: a failure is a `warn`
#      step and the run stays ok. A healthy plain restart ends with it too.
#      applied_sha is recorded BEFORE this step. Under systemd the start is
#      handed to a transient unit (domovoi-searxng-start), so a first start's
#      image download never holds the update open; without systemd it is
#      bounded by DOMOVOI_UPDATE_SEARXNG_TIMEOUT seconds (a `warn` when hit).
#  11. Ubuntu's own background jobs follow the internet answer too
#      (reconcile_os_jobs, scripts/linux/os-jobs.sh): for never the
#      updaters, news and crash-report timers are masked, for always and
#      sometimes what an earlier never masked is unmasked, and on WSL
#      wsl-pro and snapd stay off whatever the answer. Never fatal; a step
#      is recorded only when something changed (ok) or the helper failed
#      (warn). A healthy plain restart ends with it too, so an answer changed
#      in Settings takes effect at the next Restart. DOMOVOI_MANAGE_OS_JOBS=0
#      leaves the jobs alone.
#  11b. Ubuntu packages a newer Domovoi needs that this box lacks
#      (reconcile_added_packages: xorriso, for old computers' room sticks)
#      are installed from Ubuntu's archive, --no-install-recommends, not
#      held. Not under never; never fatal (a warn step); a step only when
#      something was missing. A healthy plain restart ends with it too.
#      DOMOVOI_MANAGE_PACKAGES=0 leaves them alone.
#  12. The wake-word trainer image is rebuilt when it is out of date
#      (reconcile_wake_trainer): only where it exists (a household that
#      switched training on; DOMOVOI_WAKE_TRAINER_IMAGE, else
#      WAKE_TRAINER_IMAGE in domovoi/.env, else domovoi-wake-trainer:latest)
#      and its org.domovoi.wake-trainer.src label is not the git tree id of
#      the checkout's scripts/wake_word/docker (the core labels it
#      the same way when it builds it). Built as the service user, by
#      `docker build` of that directory; under systemd in a transient unit
#      (domovoi-wake-trainer-build) the update does not wait for, without
#      it in the foreground bounded by DOMOVOI_UPDATE_TRAINER_TIMEOUT. Not
#      under never (the build downloads): a `warn` step says it is out of
#      date. A registry's image (WAKE_TRAINER_IMAGE=ghcr.io/...: a name
#      whose first part is a host) is left alone. Never fatal; a step only
#      when it did something or could not. A healthy plain restart ends
#      with it too. DOMOVOI_MANAGE_WAKE_TRAINER=0 leaves the image alone.
#   Under INTERNET_ACCESS=never (read once, at the start) an update that
#   would go online is refused before anything is touched: a dependency
#   change (pip would download) or a Dockerfile.mpd change (docker build
#   pulls the base image and runs apt). An mpd.conf-only change keeps the
#   image and just recreates the rooms.
#   On any failure in 3-9 (step 6 never fails a run, and what it set up
#   stays): stop both again, `git reset --keep` back to the
#   previous SHA, undo the dependency and MPD changes, restore the dump if
#   flyway_schema_history or any plugin's plugin_<slug>.schema_history grew
#   (the core applies plugin migrations at boot, so step 8 can run them),
#   restore the <db>_test dump if a plugin ledger there grew, switch back
#   on every plugin the failed boot's load errors switched off, restart,
#   check health and plugins again, and record the new SHA as bad_sha so the
#   version panel stops offering it.
#
# On either path, after the checks above: the core unit must start the core
# again when it ends on purpose (reconcile_core_restart). The core exits
# with status 70 when its loop watchdog finds it frozen, and the dashboard
# ends a core that stopped answering (incident 2026-10-14); only
# Restart=on-failure or Restart=always brings either back. A unit with no
# restart policy at all (Restart=no, systemd's default: a hand-made unit;
# the shipped one has on-failure) gets the drop-in
# domovoi-core.service.d/50-domovoi-restart.conf that sets on-failure (and
# RestartSec=5 unless the unit already sets its own), and systemctl
# daemon-reload. A policy the household chose (on-abnormal, say) is left
# as it is, with a warn step saying what it misses. Never fatal.
#
# Then the journal (reconcile_journal): kept on disk, so what the core
# logs about why it stopped survives a power cycle (on 2026-10-14 a box
# whose journal lived in RAM lost the record of a frozen core). The
# checkout's scripts/linux/units/journald-domovoi.conf becomes
# /etc/systemd/journald.conf.d/50-domovoi.conf (Storage=persistent and
# nothing else: the household's own size limits stay) and systemd-journald
# restarts to read it, once; provision.sh installs the same file.
# DOMOVOI_MANAGE_JOURNAL=0 leaves the journal alone. Never fatal.
#
# Every run ends by writing last-result.json into DOMOVOI_UPDATE_DIR, which
# the core serves as `last_update` from GET /v1/admin/version.
#
# Privilege: this runs as root, but everything that reads or writes the
# checkout or the venv (git, pip, docker build) runs as the service user
# through runuser. Root never executes git hooks or build steps from a tree
# the service user can edit. Root's own state lives in DOMOVOI_UPDATE_DIR, a
# root-owned dir the service user can read but not write.
#
# And root never runs this script, or anything it runs or installs, from the
# checkout (delta security review 2026-10-16, S4): domovoi-update.service's
# ExecStartPre, scripts/linux/update-verifier.sh installed root-owned as
# /usr/local/lib/domovoi-update/verify-update.sh, fetches HEAD into a bare
# mirror only root can write (every object checked), verifies its
# signature there while signed updates are enforced, and writes the commit's
# scripts/linux into /run/domovoi-update/<sha>; its ExecStart runs this
# script from that copy, with DOMOVOI_VERIFIED_SHA, DOMOVOI_VERIFIED_DIR and
# DOMOVOI_VERIFIED_MIRROR set. So os-jobs.sh, gpu-runtime.sh, the journald
# drop-in and install-update-unit.sh below are the commit's own files, and
# the signature and branch checks run in the mirror. Started any other way
# as root (a unit from before the verifier, or by hand), this script hands
# over to the verifier first (relaunch_verified), and that run moves the
# unit to the verifier (reconcile_update_unit). A HEAD that moved after the
# verifier's copy was made refuses the run.
#
# Settings come from the environment; domovoi-update.service loads
# /etc/default/domovoi-update. Every one has a default that matches
# docs/LINUX_HOST.md.

set -euo pipefail

# Every git this script runs reads objects as they are: no refs/replace and
# no grafts. Both live in the service user's .git, and with them an unsigned
# commit could pass for a signed one under its SHA (rc2 review C3).
export GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null
# And without the hint git prints for that graft file, in every git it
# starts (the service user's included): one config entry, from the environment.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=advice.graftFileDeprecated GIT_CONFIG_VALUE_0=false

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

REPO_DIR=${DOMOVOI_REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}

# The verified copy this run comes from (see "Privilege" above): set by the
# verifier's run step, which the unit runs; never by hand. Unset, a run as
# root hands over to the verifier (relaunch_verified).
VERIFIED_SHA=${DOMOVOI_VERIFIED_SHA:-}
VERIFIED_DIR=${DOMOVOI_VERIFIED_DIR:-}
VERIFIED_MIRROR=${DOMOVOI_VERIFIED_MIRROR:-}
# Where root's own git looks at commits (signature_state, check_upstream):
# the verifier's mirror, whose objects root fetched and checked itself.
# Only without one (a run that is not root's) the checkout.
GIT_ROOT_DIR=${VERIFIED_MIRROR:-$REPO_DIR}
# DOMOVOI_PROVISION_ROOT with DOMOVOI_PROVISION_TEST_HARNESS=1 (the test
# harness only, as for os-jobs.sh): the system paths below go under it.
TEST_ROOT=""
if [ "${DOMOVOI_PROVISION_TEST_HARNESS:-}" = 1 ] && [[ ${DOMOVOI_PROVISION_ROOT:-} == /?* ]]; then
  TEST_ROOT=${DOMOVOI_PROVISION_ROOT%/}
fi
# The root-owned verifier domovoi-update.service runs (install-update-unit.sh
# puts it there; reconcile_update_unit keeps it the verified copy's).
VERIFIER_INSTALLED=$TEST_ROOT/usr/local/lib/domovoi-update/verify-update.sh
UPDATE_UNIT_FILE=$TEST_ROOT/etc/systemd/system/domovoi-update.service
# Without DOMOVOI_VENV, resolve_venv may move this to the core unit's venv.
VENV_DIR=${DOMOVOI_VENV:-$REPO_DIR/.venv}
# `-` rather than `:-`: an explicitly empty value is honoured (no extras; no
# CPU-torch step). No `dev`: a server does not run the test suite.
PIP_EXTRAS=${DOMOVOI_PIP_EXTRAS-real-clients,voice-profile}
TORCH_INDEX_URL=${DOMOVOI_TORCH_INDEX_URL-https://download.pytorch.org/whl/cpu}
# Install from the hash-pinned lock only when this is 1. Off by default, so
# a box re-syncs the way it always has until its owner has re-seeded the
# lock from the box's own `pip freeze` and opted in (docs/LINUX_HOST.md,
# "The hash-pinned lock"): the first locked re-sync moves every package to
# the lock's version.
USE_LOCK=${DOMOVOI_USE_LOCK:-0}
# The hash-pinned production lock, relative to the checkout
# (sync_deps_locked). Empty: never use one, always the resolver.
DEPS_LOCK=${DOMOVOI_DEPS_LOCK-requirements-linux-py314.lock}
# Beside it, the packages installed without their declared dependencies
# (faster-whisper, resemblyzer: requirements-no-deps.in says why), pinned
# and hashed by the same compile. A checkout from before 2026-10-15 has
# neither file; then that step is skipped.
DEPS_NODEPS_LOCK=${DOMOVOI_DEPS_NODEPS_LOCK-requirements-linux-py314-no-deps.lock}
NODEPS_REQS=requirements-no-deps.in
# The extras that lock was compiled with (scripts/linux/compile-linux-lock.sh).
LOCK_EXTRAS=real-clients,voice-profile
UPDATE_DIR=${DOMOVOI_UPDATE_DIR:-/var/lib/domovoi-update}
BACKUP_DIR=${DOMOVOI_UPDATE_BACKUP_DIR:-$UPDATE_DIR/backups}
KEEP_BACKUPS=${DOMOVOI_UPDATE_KEEP_BACKUPS:-5}
# How many of each database's replaced copies (<db>_failed_<ts>, left by a
# rollback's restore) stay in Postgres for inspection; older ones are
# dropped after the next restore. At least 1.
KEEP_FAILED_DBS=${DOMOVOI_UPDATE_KEEP_FAILED_DBS:-1}
REQUIRE_BACKUP=${DOMOVOI_UPDATE_REQUIRE_BACKUP:-1}
HEALTH_TIMEOUT=${DOMOVOI_UPDATE_HEALTH_TIMEOUT:-120}
HEALTH_INTERVAL=${DOMOVOI_UPDATE_HEALTH_INTERVAL:-2}
# How long the stop step waits for web and core before it SIGKILLs whatever
# is still stopping, and then how long for that to land. Above the units'
# documented TimeoutStopSec (30), so normally systemd's own kill comes first;
# well under systemd's 90 s default, which is what a unit without the
# setting gets (docs/LINUX_HOST.md).
STOP_TIMEOUT=${DOMOVOI_UPDATE_STOP_TIMEOUT:-40}
STOP_KILL_WAIT=${DOMOVOI_UPDATE_STOP_KILL_WAIT:-10}
CORE_HEALTH_URL=${DOMOVOI_CORE_HEALTH_URL:-http://127.0.0.1:6370/v1/health}
WEB_HEALTH_URL=${DOMOVOI_WEB_HEALTH_URL:-http://127.0.0.1:6369/api/health}
PG_CONTAINER=${DOMOVOI_PG_CONTAINER:-domovoi-postgres}
PG_USER=${DOMOVOI_PG_USER:-domovoi}
PG_DB=${DOMOVOI_PG_DB:-domovoi}
# The plugin runtime applies every plugin migration to the database and
# then to its <db>_test twin (default_database_urls() in
# domovoi/plugins_runtime/migrations.py), so that twin is backed up and
# restored too when it exists. A database already named *_test has none.
case $PG_DB in
  *_test) TEST_DB="" ;;
  *) TEST_DB=${PG_DB}_test ;;
esac

CORE_UNIT=domovoi-core.service
WEB_UNIT=domovoi-web.service
DB_UNIT=domovoi-db.service
# Where reconcile_core_restart writes its drop-in (the harness points it at
# a scratch directory).
CORE_DROPIN_DIR=${DOMOVOI_CORE_DROPIN_DIR:-/etc/systemd/system/$CORE_UNIT.d}
CORE_RESTART_DROPIN=$CORE_DROPIN_DIR/50-domovoi-restart.conf
# The journal on disk (reconcile_journal); the harness points the drop-in
# directory at a scratch one.
MANAGE_JOURNAL=${DOMOVOI_MANAGE_JOURNAL:-1}
JOURNALD_DROPIN_DIR=${DOMOVOI_JOURNALD_DROPIN_DIR:-/etc/systemd/journald.conf.d}
JOURNALD_DROPIN=$JOURNALD_DROPIN_DIR/50-domovoi.conf
JOURNALD_SRC=$SCRIPT_DIR/units/journald-domovoi.conf

# The search helper (docs/INTERNET.md): the compose service and the
# container it runs as. DOMOVOI_MANAGE_SEARXNG=0 (or false/no/off) in
# /etc/default/domovoi-update leaves the container alone, for a host that
# runs SearXNG some other way.
SEARXNG_SERVICE=searxng
SEARXNG_CONTAINER=domovoi-searxng
MANAGE_SEARXNG=${DOMOVOI_MANAGE_SEARXNG:-1}
# The first start downloads the image (a few hundred MB). Under systemd
# (auto) the start runs in a transient unit of its own and the update does
# not wait for it; 1 forces that (systemd-run must exist), 0 runs it in the
# foreground, bounded by DOMOVOI_UPDATE_SEARXNG_TIMEOUT seconds.
SEARXNG_DETACH=${DOMOVOI_UPDATE_SEARXNG_DETACH:-auto}
SEARXNG_TIMEOUT=${DOMOVOI_UPDATE_SEARXNG_TIMEOUT:-300}
SEARXNG_START_UNIT=domovoi-searxng-start

# Ubuntu's own background jobs (scripts/linux/os-jobs.sh, beside this
# script). DOMOVOI_MANAGE_OS_JOBS=0 (or false/no/off) leaves them alone,
# for an administrator who manages apt's timers themselves.
MANAGE_OS_JOBS=${DOMOVOI_MANAGE_OS_JOBS:-1}
OS_JOBS=$SCRIPT_DIR/os-jobs.sh

# Docker's way to an NVIDIA GPU (scripts/linux/gpu-runtime.sh, beside this
# script). DOMOVOI_MANAGE_GPU_RUNTIME=0 (or false/no/off) leaves it alone,
# for an administrator who sets the NVIDIA Container Toolkit up themselves.
MANAGE_GPU_RUNTIME=${DOMOVOI_MANAGE_GPU_RUNTIME:-1}
GPU_RUNTIME=$SCRIPT_DIR/gpu-runtime.sh
# Why domovoi-db failed (scripts/linux/db-diagnostics.sh, beside this
# script; read-only): restart_db runs it when the database's restart fails.
DB_DIAGNOSTICS=$SCRIPT_DIR/db-diagnostics.sh
# 1 once the GPU step restarted Docker in this run (domovoi-db then restarts).
GPU_DOCKER_RESTARTED=0

# Ubuntu packages a newer Domovoi needs that a box installed before them
# lacks (provision.sh's apt step puts them on a new one): xorriso, the
# program the core runs to make old computers' room sticks (2026-10-16,
# domovoi/usb_stick/). DOMOVOI_MANAGE_PACKAGES=0 (or false/no/off) leaves
# them to the administrator.
MANAGE_PACKAGES=${DOMOVOI_MANAGE_PACKAGES:-1}
ADDED_PACKAGES=(xorriso)

# The wake-word trainer image (scripts/wake_word/docker), rebuilt when it is
# out of date and only where a household built it (switched training on).
# DOMOVOI_MANAGE_WAKE_TRAINER=0 (or false/no/off) leaves it alone. The build
# takes minutes (about 5 with its base image cached, 8 with the 3 GB base
# to pull): under systemd (auto) it runs in a transient unit of its own
# that the update does not wait for; 1 forces that, 0 runs it in the
# foreground, bounded by DOMOVOI_UPDATE_TRAINER_TIMEOUT seconds.
MANAGE_WAKE_TRAINER=${DOMOVOI_MANAGE_WAKE_TRAINER:-1}
TRAINER_DETACH=${DOMOVOI_UPDATE_TRAINER_DETACH:-auto}
TRAINER_TIMEOUT=${DOMOVOI_UPDATE_TRAINER_TIMEOUT:-1800}
TRAINER_BUILD_UNIT=domovoi-wake-trainer-build
TRAINER_DIR=scripts/wake_word/docker
# The label that names the tree an image was built from: the git tree id of
# TRAINER_DIR at the commit built (the core sets the same one).
TRAINER_LABEL=org.domovoi.wake-trainer.src

# Signed updates (docs/LINUX_HOST.md, "Signed updates"). While the
# allowed-signers file exists, HEAD must carry an SSH signature by one of
# the keys in it or the run refuses before it touches anything; without
# the file the run warns and goes on. An optional revocation file
# (ssh-keygen -Y verify -r) retires a key. The upstream pins, when set,
# refuse a checkout whose origin URL or tracking branch is not the one
# named, before anything else.
ALLOWED_SIGNERS=${DOMOVOI_ALLOWED_SIGNERS:-/etc/domovoi/allowed_signers}
REVOKED_SIGNERS=${DOMOVOI_REVOKED_SIGNERS:-}
UPSTREAM_URL=${DOMOVOI_UPSTREAM_URL:-}
UPSTREAM_BRANCH=${DOMOVOI_UPSTREAM_BRANCH:-}

RESULT_FILE=$UPDATE_DIR/last-result.json
APPLIED_FILE=$UPDATE_DIR/applied_sha
BAD_FILE=$UPDATE_DIR/bad_sha
FREEZE_FILE=$UPDATE_DIR/pip-freeze-pre.txt

# What counts as "the dependencies changed" / "the MPD image changed".
# Git pathspecs, relative to the repo root. pyproject.toml counts too, but
# not a change to [project]'s version line alone (deps_changed): every
# release bumps it, and a box whose internet answer is "never" must still
# be able to take a release that changes nothing else there.
DEPS_PATHS=('requirements*.lock' 'requirements*.in' 'plugins/*/requirements*.lock')
PYPROJECT_PATH=pyproject.toml
MPD_PATHS=(domovoi/Dockerfile.mpd domovoi/mpd.conf)
# The compose file whose `image:` pins decide what domovoi-db (and the
# search helper) pull: a moved digest is a download.
COMPOSE_PATH=domovoi/docker-compose.yml

# Run state, filled in as the run goes.
SERVICE_USER=""
# The service user's group: last-result.json is readable by it (the core
# reads the file as that user) and by no other account.
RESULT_GROUP=""
CORE_STATE_DIR=""
MPD_TAG=""
MPD_PREFIX=""
RUN_AS_ROOT=0
HEAD_SHA=""
PREV_SHA=""
PREV_SOURCE=""
BAD_SHA=""
MODE=""
STARTED_AT=""
START_MS=0
DEPS_CHANGED=0
MPD_CHANGED=0
MPD_IMAGE_CHANGED=0
# An `image:` line in the compose file changed: the next compose up pulls.
IMAGES_CHANGED=0
# The internet answer as this run found it at the start ("" when
# unanswered, or when the checkout can't say).
POLICY_AT_START=""
# The update itself succeeded and applied_sha was written; set before the
# search-helper step, so an interruption there can't report a failure.
APPLIED_OK=0
MIGRATIONS_BEFORE=""
MIGRATIONS_AFTER=""
# Why a plain restart restarts domovoi-db ("" when it leaves it alone).
DB_REASON=""
LEDGERS_BEFORE=""
LEDGERS_AFTER=""
PLUGINS_BEFORE=""
TEST_LEDGERS_BEFORE=""
TEST_LEDGERS_AFTER=""
BACKUP_FILE=""
TEST_BACKUP_FILE=""
DB_RESTORED=0
TEST_DB_RESTORED=0
# The last sync_deps installed from DEPS_LOCK (so restore_pins has nothing
# left to put back).
DEPS_FROM_LOCK=0
STEPS=()
# A step function may leave a note here for its step's detail on success.
STEP_DETAIL=""
# A step function that succeeded but fell short of what was asked of it
# (the opted-in lock could not be used) sets this to `warn`: the step is
# recorded as warn, the run goes on, and the core serves that step's detail
# on the Version card (git_version._WARN_DETAIL_STEPS).
STEP_STATUS=""
LAST_ERROR=""
RESULT_READY=0
FINAL_WRITTEN=0
FINAL_STATUS=""
SERVICES_STOPPED=0
# HEAD's signature state (signature_state): verified, unsigned or
# unverified; who signed it; whether the signers file is there.
SIG_STATUS=""
SIG_SIGNER=""
SIG_KEY=""
SIG_ENFORCED=0
SIG_DETAIL=""

log() { printf 'apply-update: %s\n' "$*"; }

# Wall-clock milliseconds since the epoch, for the durations in the result.
#
# Bash's own clock first: $EPOCHREALTIME (bash 5 and later, no fork) is the
# seconds, the locale's decimal point ("." or ","), then six digits of
# microseconds. `date +%s%3N` only when it prints exactly 13 digits: the
# uutils date(1) that Ubuntu 26.04 ships as coreutils ignores the 3 and
# prints the nanoseconds unpadded, anything from 11 to 19 digits, and every
# duration worked out from those is garbage (a 102 s run was recorded as
# 101766807.450 s). A date(1) without %N prints it literally. Failing both,
# whole seconds.
now_ms() {
  local t=${EPOCHREALTIME-}
  if [ "${BASH_VERSINFO[0]}" -ge 5 ] && [[ $t =~ ^([0-9]+)[.,]([0-9]{3}) ]]; then
    printf '%s%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    return
  fi
  t=$(date +%s%3N 2>/dev/null) || t=""
  if [[ $t =~ ^[0-9]{13}$ ]]; then printf '%s' "$t"; else printf '%s000' "$(date +%s)"; fi
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Milliseconds as seconds, printed straight into the result JSON. The
# durations are wall-clock differences, so a clock stepped back during the
# run (NTP correcting a clock that ran fast) makes one negative, and printf
# would write "-89.-412": not JSON, and the core then drops the whole
# result, bad_sha and all. A duration that went backwards is 0.
fmt_sec() {
  local ms=$1
  [[ $ms =~ ^[0-9]+$ ]] || ms=0
  printf '%d.%03d' $((10#$ms / 1000)) $((10#$ms % 1000))
}

# JSON string literal for $1: control characters dropped, the rest escaped.
json_str() {
  local s
  s=$(printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037')
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

json_str_or_null() {
  if [ -n "$1" ]; then json_str "$1"; else printf 'null'; fi
}

json_int_or_null() {
  if [[ $1 =~ ^[0-9]+$ ]]; then printf '%s' "$1"; else printf 'null'; fi
}

json_bool() {
  if [ "$1" = 1 ]; then printf 'true'; else printf 'false'; fi
}

# HEAD's signature state as the result carries it, null before it is known.
json_signature() {
  if [ -z "$SIG_STATUS" ]; then printf 'null'; return; fi
  printf '{"status": %s, "signer": %s, "key": %s, "enforced": %s, "allowed_signers": %s}' \
    "$(json_str "$SIG_STATUS")" "$(json_str_or_null "$SIG_SIGNER")" "$(json_str_or_null "$SIG_KEY")" \
    "$(json_bool "$SIG_ENFORCED")" "$(json_str "$ALLOWED_SIGNERS")"
}

# The first $2 characters of $1. Characters, not bytes: cut(1) on
# Debian/Ubuntu counts bytes and can split pip's "╰─>" mid-character, which
# leaves invalid UTF-8 in the result JSON. Bash counts by the locale, so
# set a UTF-8 one for just this expansion.
trunc() (
  LC_ALL=C.UTF-8
  printf '%s' "${1:0:$2}"
) 2>/dev/null

# The last $2 lines of file $1, each cut to $3 characters.
tail_lines() {
  local line out=""
  while IFS= read -r line || [ -n "$line" ]; do
    out+="$(trunc "$line" "$3")"$'\n'
  done < <(tail -n "$2" "$1")
  printf '%s' "${out%$'\n'}"
}

# Write "$2" to "$1" by rename, so a reader never sees half a file and a
# symlink planted at the target is replaced rather than followed. Mode 0644,
# or with a group in $3: that group and mode 0640, set before the rename so
# the file is never briefly readable by everyone (0644 when the chgrp
# fails, so the core can still read it).
write_atomic() {
  local target=$1 content=$2 group=${3-} tmp
  tmp=$(mktemp "$(dirname "$target")/.$(basename "$target").XXXXXX")
  printf '%s' "$content" >"$tmp"
  if [ -n "$group" ] && chgrp -- "$group" "$tmp" 2>/dev/null; then
    chmod 0640 "$tmp"
  else
    chmod 0644 "$tmp"
  fi
  mv -f "$tmp" "$target"
}

write_result() {
  local status=$1 err=${2-} steps=""
  [ "$RESULT_READY" = 1 ] || return 0
  if [ "${#STEPS[@]}" -gt 0 ]; then
    steps=$(IFS=,; printf '%s' "${STEPS[*]}")
  fi
  # The steps carry the tail of each failed command's output (pip, docker,
  # git, pg_dump), so the file is for the core's user, not every account.
  write_atomic "$RESULT_FILE" "$(
    printf '{\n'
    printf '  "status": %s,\n' "$(json_str "$status")"
    printf '  "mode": %s,\n' "$(json_str_or_null "$MODE")"
    printf '  "from_sha": %s,\n' "$(json_str_or_null "$PREV_SHA")"
    printf '  "to_sha": %s,\n' "$(json_str_or_null "$HEAD_SHA")"
    printf '  "prev_source": %s,\n' "$(json_str_or_null "$PREV_SOURCE")"
    printf '  "bad_sha": %s,\n' "$(json_str_or_null "$BAD_SHA")"
    printf '  "started_at": %s,\n' "$(json_str "$STARTED_AT")"
    if [ "$status" = running ]; then
      printf '  "finished_at": null,\n'
      printf '  "duration_sec": null,\n'
    else
      printf '  "finished_at": %s,\n' "$(json_str "$(now_iso)")"
      printf '  "duration_sec": %s,\n' "$(fmt_sec $(($(now_ms) - START_MS)))"
    fi
    printf '  "deps_changed": %s,\n' "$(json_bool "$DEPS_CHANGED")"
    printf '  "mpd_changed": %s,\n' "$(json_bool "$MPD_CHANGED")"
    printf '  "signature": %s,\n' "$(json_signature)"
    printf '  "migrations_before": %s,\n' "$(json_int_or_null "$MIGRATIONS_BEFORE")"
    printf '  "migrations_after": %s,\n' "$(json_int_or_null "$MIGRATIONS_AFTER")"
    printf '  "plugin_migrations_before": %s,\n' "$(json_int_or_null "$(ledger_total "$LEDGERS_BEFORE")")"
    printf '  "plugin_migrations_after": %s,\n' "$(json_int_or_null "$(ledger_total "$LEDGERS_AFTER")")"
    printf '  "backup": %s,\n' "$(json_str_or_null "$BACKUP_FILE")"
    printf '  "test_backup": %s,\n' "$(json_str_or_null "$TEST_BACKUP_FILE")"
    printf '  "db_restored": %s,\n' "$(json_bool "$DB_RESTORED")"
    printf '  "test_db_restored": %s,\n' "$(json_bool "$TEST_DB_RESTORED")"
    printf '  "error": %s,\n' "$(json_str_or_null "$err")"
    printf '  "steps": [%s]\n' "$steps"
    printf '}'
  )"$'\n' "$RESULT_GROUP"
}

finish() {
  write_result "$1" "${2-}"
  FINAL_STATUS=$1
  FINAL_WRITTEN=1
  log "result: $1${2:+ ($2)}"
}

add_step() {  # name status t0_ms detail
  local dur=$(($(now_ms) - $3))
  STEPS+=("{\"name\": $(json_str "$1"), \"status\": $(json_str "$2"), \"duration_sec\": $(fmt_sec "$dur"), \"detail\": $(json_str_or_null "$4")}")
}

# run_step NAME CMD...: run CMD (a command or a function of this script)
# with its output replayed into the journal, and record the step with its
# timing and, on failure, the tail of that output. Returns CMD's status.
run_step() {
  local name=$1 t0 out rc=0
  shift
  t0=$(now_ms)
  out=$(mktemp)
  log "step $name"
  STEP_DETAIL=""
  STEP_STATUS=""
  set +e
  "$@" >"$out" 2>&1
  rc=$?
  set -e
  sed 's/^/    /' "$out"
  if [ "$rc" -eq 0 ]; then
    add_step "$name" "${STEP_STATUS:-ok}" "$t0" "$STEP_DETAIL"
    if [ -n "$STEP_DETAIL" ]; then log "step $name${STEP_STATUS:+ ($STEP_STATUS)}: $STEP_DETAIL"; fi
  else
    add_step "$name" failed "$t0" "$(tail_lines "$out" 15 300)"
    LAST_ERROR="$name failed (exit $rc): $(trunc "$(tail -n 3 "$out" | tr '\n' ' ')" 400 | sed 's/[[:space:]]*$//')"
    log "step $name failed (exit $rc)"
  fi
  rm -f "$out"
  return "$rc"
}

# run_soft_step NAME CMD...: run_step for a step that must never fail the
# run. Success is an `ok` step as usual; a failure is recorded as a `warn`
# step with the tail of its output, LAST_ERROR is left alone, and it
# returns 0, so the run's own result is unaffected.
run_soft_step() {
  local name=$1 t0 out rc=0
  shift
  t0=$(now_ms)
  out=$(mktemp)
  log "step $name"
  STEP_DETAIL=""
  set +e
  "$@" >"$out" 2>&1
  rc=$?
  set -e
  sed 's/^/    /' "$out"
  if [ "$rc" -eq 0 ]; then
    add_step "$name" ok "$t0" "$STEP_DETAIL"
    if [ -n "$STEP_DETAIL" ]; then log "step $name: $STEP_DETAIL"; fi
  else
    add_step "$name" warn "$t0" "$(tail_lines "$out" 15 300)"
    log "step $name failed (exit $rc); not fatal, the run goes on"
  fi
  rm -f "$out"
  return 0
}

on_exit() {
  local rc=$?
  if [ "$FINAL_WRITTEN" != 1 ]; then
    if [ "$APPLIED_OK" = 1 ]; then
      # Stopped during the search-helper step, after the update itself was
      # healthy and recorded: that is still a good update.
      write_result ok || true
      return
    fi
    # Something this script did not expect. Don't leave the house without
    # its services: start whatever this run stopped, then say what happened.
    if [ "$SERVICES_STOPPED" = 1 ]; then
      systemctl start "$CORE_UNIT" "$WEB_UNIT" --no-block || true
    fi
    write_result failed "${LAST_ERROR:-update script exited unexpectedly (exit $rc)}" || true
  fi
}

# ─── Who owns the checkout, and where things live ────────────────────────

# KEY's value in domovoi/.env as the core reads it, or empty: through the
# checkout's own reader (python -m domovoi.config_env_writer --get KEY,
# python-dotenv, as the service user, like internet_policy), so a quoted
# value that spans lines is one value here too, never a KEY=value line of
# its own (review of 2026-10-13: a bot name carrying a WAKE_TRAINER_IMAGE
# line named the image root's update rebuilt). Needs VENV_DIR resolved.
# Only when that reader can't answer (a checkout from before it, a venv
# that doesn't run) is the file read line by line, as before, and the
# journal says so: the last KEY=value, quotes stripped.
dotenv_get() {
  local f=$REPO_DIR/domovoi/.env v
  [ -r "$f" ] || return 0
  if v=$(as_user "$VENV_DIR/bin/python" -m domovoi.config_env_writer --get "$1" 2>/dev/null); then
    printf '%s' "$v"
    return 0
  fi
  log "domovoi/.env: $1 read line by line: this checkout's python -m domovoi.config_env_writer --get could not read it" >&2
  # As the service user, like the rest of the checkout: a link planted at
  # domovoi/.env must not have root read the file it points at.
  v=$(as_user sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$f" | tail -n 1 | tr -d '\r')
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  printf '%s' "$v"
}

resolve_service_user() {
  SERVICE_USER=${DOMOVOI_USER:-}
  if [ -z "$SERVICE_USER" ] && command -v systemctl >/dev/null 2>&1; then
    SERVICE_USER=$(systemctl show -p User --value "$CORE_UNIT" 2>/dev/null || true)
  fi
  if [ -z "$SERVICE_USER" ]; then
    SERVICE_USER=$(stat -c %U "$REPO_DIR" 2>/dev/null || true)
  fi
  if [ "$(id -u)" = 0 ]; then
    RUN_AS_ROOT=1
    if [ -z "$SERVICE_USER" ]; then
      LAST_ERROR="cannot tell which user owns $REPO_DIR; set DOMOVOI_USER in /etc/default/domovoi-update"
      return 1
    fi
    RESULT_GROUP=$(id -gn "$SERVICE_USER" 2>/dev/null) || RESULT_GROUP=""
  fi
}

# The core records the pre-pull SHA in settings.update_state_dir, which
# defaults to ~/.domovoi/update of the user it runs as.
resolve_core_state_dir() {
  local d=${DOMOVOI_CORE_STATE_DIR:-} home=""
  if [ -z "$d" ]; then d=$(dotenv_get UPDATE_STATE_DIR); fi
  if [ -z "$d" ] || [[ $d == "~"* ]]; then
    if [ -n "$SERVICE_USER" ] && command -v getent >/dev/null 2>&1; then
      home=$(getent passwd "$SERVICE_USER" | cut -d: -f6 || true)
    fi
    if [ -z "$home" ]; then return 0; fi
    if [ -z "$d" ]; then d=$home/.domovoi/update; else d=$home${d#\~}; fi
  fi
  CORE_STATE_DIR=$d
}

# Without DOMOVOI_VENV: the venv domovoi-core.service actually runs from
# (its ExecStart interpreter), so a layout other than <repo>/.venv needs no
# setting. Kept at <repo>/.venv when the unit can't say, or its interpreter
# is not inside a venv.
resolve_venv() {
  local exe d
  [ -z "${DOMOVOI_VENV:-}" ] || return 0
  command -v systemctl >/dev/null 2>&1 || return 0
  exe=$(systemctl show -p ExecStart --value "$CORE_UNIT" 2>/dev/null \
        | sed -n 's/^{ path=\([^ ;]*\) ;.*/\1/p' | head -n 1) || return 0
  case $exe in
    */bin/python*) d=${exe%/bin/python*} ;;
    *) return 0 ;;
  esac
  if [ -f "$d/pyvenv.cfg" ]; then VENV_DIR=$d; fi
}

resolve_mpd_names() {
  MPD_TAG=${DOMOVOI_MPD_IMAGE_TAG:-$(dotenv_get MPD_IMAGE_TAG)}
  MPD_TAG=${MPD_TAG:-domovoi-mpd:latest}
  MPD_PREFIX=${DOMOVOI_MPD_CONTAINER_PREFIX:-$(dotenv_get MPD_CONTAINER_PREFIX)}
  MPD_PREFIX=${MPD_PREFIX:-domovoi-mpd-}
}

# Run a command as the service user (a no-op wrapper when not root).
as_user() {
  if [ "$RUN_AS_ROOT" = 1 ] && [ "$SERVICE_USER" != root ]; then
    runuser -u "$SERVICE_USER" -- "$@"
  else
    "$@"
  fi
}

git_as() { as_user git -C "$REPO_DIR" "$@"; }

is_sha() { [[ $1 =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; }

# A full SHA read from file $1 (as the service user, since the core's state
# dir is theirs), or failure. The content is never echoed into the journal.
read_sha_file() {
  local f=$1 s
  s=$(as_user head -c 200 "$f" 2>/dev/null | tr -d '[:space:]') || return 1
  is_sha "$s" || return 1
  printf '%s' "$s"
}

is_commit() { git_as cat-file -e "$1^{commit}" 2>/dev/null; }

resolve_prev() {
  local s
  if s=$(read_sha_file "$APPLIED_FILE") && is_commit "$s"; then
    PREV_SHA=$s; PREV_SOURCE=applied; return 0
  fi
  if [ -n "$CORE_STATE_DIR" ] && s=$(read_sha_file "$CORE_STATE_DIR/prev_sha") && is_commit "$s"; then
    PREV_SHA=$s; PREV_SOURCE=pull; return 0
  fi
  if s=$(git_as rev-parse -q --verify 'ORIG_HEAD^{commit}' 2>/dev/null) && is_sha "$s" \
      && [ "$s" != "$HEAD_SHA" ] && git_as merge-base --is-ancestor "$s" "$HEAD_SHA" 2>/dev/null; then
    PREV_SHA=$s; PREV_SOURCE=orig_head; return 0
  fi
  PREV_SHA=$HEAD_SHA; PREV_SOURCE=head
}

# Did any of these pathspecs change between PREV_SHA and HEAD_SHA? A diff
# that errors counts as "changed": the cost of a needless sync is minutes,
# the cost of a missed one is a broken boot.
paths_changed() {
  local rc=0
  git_as diff --quiet "$PREV_SHA" "$HEAD_SHA" -- "$@" || rc=$?
  [ "$rc" -ne 0 ]
}

# pyproject.toml on stdin, with [project]'s first `version =` line
# replaced by one placeholder: two files that differ only there come out
# the same. A version line in any other table is left as it is.
without_project_version() {
  awk '
    /^[[:space:]]*\[/ { in_project = ($0 ~ /^[[:space:]]*\[project\][[:space:]]*(#.*)?$/) }
    in_project && !seen && /^[[:space:]]*version[[:space:]]*=/ { print "version = (any)"; seen = 1; next }
    { print }
  '
}

# Did pyproject.toml change between PREV_SHA and HEAD_SHA in anything but
# [project]'s version line? Either side missing, or git or awk failing,
# counts as changed, the same rule as paths_changed.
pyproject_deps_changed() {
  local a b
  paths_changed "$PYPROJECT_PATH" || return 1
  a=$(git_as show "$PREV_SHA:$PYPROJECT_PATH" 2>/dev/null | without_project_version) || return 0
  b=$(git_as show "$HEAD_SHA:$PYPROJECT_PATH" 2>/dev/null | without_project_version) || return 0
  [ "$a" != "$b" ]
}

# The dependencies changed: a lock or a requirements file, or pyproject.toml
# beyond a version bump.
deps_changed() { paths_changed "${DEPS_PATHS[@]}" || pyproject_deps_changed; }

# Did an `image:` line of the compose file change between PREV_SHA and
# HEAD_SHA? Every image there is pinned by digest, so a changed line is a
# pull on the next `docker compose up` (domovoi-db pulls Postgres and
# Flyway). Comments and other keys don't count. A diff that errors counts
# as changed, the same rule as paths_changed.
images_changed() {
  local diff
  diff=$(git_as diff --no-color -U0 "$PREV_SHA" "$HEAD_SHA" -- "$COMPOSE_PATH") || return 0
  printf '%s\n' "$diff" | grep -Eq '^[+-][[:space:]]*image:'
}

# ─── Signed updates ──────────────────────────────────────────────────────
#
# Root is about to run what the checkout holds: this script, then the
# services, the MPD image build and pip. While ALLOWED_SIGNERS exists, HEAD
# has to carry an SSH signature by one of the keys in it (git
# verify-commit), or the run refuses before it touches anything. Without
# the file the run warns and goes on, so a box that has not set signing up
# keeps updating.
#
# The verdict is root's own. The core verified the tip before it pulled,
# but the core runs as the service user and root does not take its word
# for it: git runs as root here, not through runuser, with every program
# git could be told to run pinned on the command line, because the
# checkout's .git/config belongs to the service user and a gpg.ssh.program
# in it would otherwise choose what root executes. safe.directory is pinned
# the same way (git refuses another user's checkout without it). Only a
# file root owns and only root can write counts as the signers file.
#
# Run as the verified copy (the unit's ExecStartPre made it), the gate
# before anything of a commit runs as root is the verifier's: a tree moved
# to an unverified commit by a pull that skipped the core (a by-hand `git
# pull` as the service user) is refused there, before this script runs, and
# this check (in the verifier's mirror) only repeats its verdict for the
# result. Run any other way, this script hands over to the verifier first.

# git as root, for the signature verdict alone. Nothing here writes.
git_root() {
  local -a cfg=(-c "safe.directory=$GIT_ROOT_DIR" -c "gpg.ssh.allowedSignersFile=$ALLOWED_SIGNERS"
                -c gpg.ssh.program=ssh-keygen -c gpg.program=gpg
                -c gpg.openpgp.program=gpg -c gpg.x509.program=gpgsm)
  if [ -n "$REVOKED_SIGNERS" ]; then cfg+=(-c "gpg.ssh.revocationFile=$REVOKED_SIGNERS"); fi
  # In the verifier's mirror (GIT_ROOT_DIR) when this run is the verified
  # copy: root's own repository, not the service user's. Never through a
  # replacement object or a graft (rc2 review C3), which could stand
  # another commit in for the one HEAD_SHA names.
  GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null git -c advice.graftFileDeprecated=false "${cfg[@]}" -C "$GIT_ROOT_DIR" "$@"
}

# Why ALLOWED_SIGNERS cannot be trusted, printed; nothing when it can. A
# file another user can change would let them choose the keys. Ownership
# is judged only when this runs as root (a by-hand run as another user
# cannot own files as root, and the unit never runs that way).
signers_file_problem() {
  local st
  if [ -L "$ALLOWED_SIGNERS" ]; then printf '%s is a symlink' "$ALLOWED_SIGNERS"; return 0; fi
  if [ ! -f "$ALLOWED_SIGNERS" ]; then printf '%s is not a regular file' "$ALLOWED_SIGNERS"; return 0; fi
  [ "$RUN_AS_ROOT" = 1 ] || return 0
  st=$(stat -c '%u %a' "$ALLOWED_SIGNERS" 2>/dev/null) || st=""
  if [ -z "$st" ]; then printf 'stat cannot read %s' "$ALLOWED_SIGNERS"; return 0; fi
  if ! [[ $st =~ ^0\ ([0-7]{3,4})$ ]]; then printf '%s is not owned by root' "$ALLOWED_SIGNERS"; return 0; fi
  if (( 8#${BASH_REMATCH[1]} & 8#022 )); then
    printf '%s can be written by a user other than root (mode %s)' "$ALLOWED_SIGNERS" "${BASH_REMATCH[1]}"
  fi
}

# HEAD's signature state into SIG_*: verified (signed by a key in the
# signers file), unsigned, or unverified (signed, but not by a listed key,
# or nothing to check it against). SIG_ENFORCED says whether the signers
# file is there. Returns 0 when a run under enforcement may go on.
signature_state() {
  local problem mark info err
  SIG_STATUS="" SIG_SIGNER="" SIG_KEY="" SIG_DETAIL="" SIG_ENFORCED=0
  if [ ! -e "$ALLOWED_SIGNERS" ] && [ ! -L "$ALLOWED_SIGNERS" ]; then
    # Nothing to check against. Say whether HEAD is signed at all: a read
    # of the commit object, as the service user like every other look at
    # the tree. A signature shows as a gpgsig header.
    if git_as cat-file commit "$HEAD_SHA" 2>/dev/null | grep -q '^gpgsig'; then
      SIG_STATUS=unverified
      SIG_DETAIL="HEAD is signed, but there is no $ALLOWED_SIGNERS to check it against"
    else
      SIG_STATUS=unsigned
      SIG_DETAIL="HEAD is not signed, and there is no $ALLOWED_SIGNERS"
    fi
    return 0
  fi
  SIG_ENFORCED=1
  problem=$(signers_file_problem)
  if [ -n "$problem" ]; then
    SIG_STATUS=unverified
    SIG_DETAIL="the allowed-signers file cannot be trusted: $problem"
    return 1
  fi
  err=$(mktemp)
  if git_root verify-commit "$HEAD_SHA" >/dev/null 2>"$err"; then
    info=$(git_root log -1 --format='%GS%n%GK' "$HEAD_SHA" 2>/dev/null) || info=""
    SIG_SIGNER=$(printf '%s\n' "$info" | sed -n 1p)
    SIG_KEY=$(printf '%s\n' "$info" | sed -n 2p)
    SIG_STATUS=verified
    SIG_DETAIL="HEAD is signed by ${SIG_SIGNER:-an allowed key}${SIG_KEY:+ ($SIG_KEY)}, listed in $ALLOWED_SIGNERS"
    rm -f "$err"
    return 0
  fi
  # N: no signature at all. Anything else is a signature that did not
  # verify (a key not in the file, a revoked key, a format with no keyring).
  mark=$(git_root log -1 --format='%G?' "$HEAD_SHA" 2>/dev/null) || mark=""
  if [ "$mark" = N ]; then
    SIG_STATUS=unsigned
    SIG_DETAIL="HEAD is not signed"
  else
    SIG_STATUS=unverified
    SIG_DETAIL="the signature on HEAD is not by a key in $ALLOWED_SIGNERS: $(tail_lines "$err" 2 200 | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  fi
  rm -f "$err"
  return 1
}

# The signature step, before anything is touched, on both paths. The
# verdict goes into the result as a step and as `signature`. Under
# enforcement a HEAD that does not verify refuses the run; without it the
# run goes on and says so, loudly, every time.
check_signature() {
  local t0 rc=0
  t0=$(now_ms)
  signature_state || rc=$?
  if [ "$SIG_ENFORCED" = 1 ] && [ "$rc" -eq 0 ]; then
    add_step signature ok "$t0" "$SIG_DETAIL"
    log "signature: $SIG_DETAIL"
    return 0
  fi
  if [ "$SIG_ENFORCED" = 1 ]; then
    add_step signature refused "$t0" "$SIG_DETAIL"
    finish refused "$SIG_DETAIL; signed updates are enforced by $ALLOWED_SIGNERS, so nothing was changed. Pull a commit signed by a listed key, or remove that file to turn enforcement off (docs/LINUX_HOST.md, Signed updates)."
    return 1
  fi
  add_step signature warn "$t0" "not enforced: $SIG_DETAIL"
  log "WARNING: signed updates are not enforced on this box: $SIG_DETAIL. Root runs whatever the checkout holds. To bound that to commits you signed, install the allowed-signers file (docs/LINUX_HOST.md, Signed updates)."
  return 0
}

# A remote URL for comparing: no trailing slash or .git.
norm_url() {
  local u=$1
  u=${u%/}; u=${u%.git}; u=${u%/}
  printf '%s' "$u"
}

# The checkout's origin and tracking branch against the pins in the root
# env file (DOMOVOI_UPSTREAM_URL, DOMOVOI_UPSTREAM_BRANCH). Nothing is
# checked without a pin. Refuses before anything is touched: a checkout
# that follows another remote or branch is not the one the owner signs.
# DOMOVOI_UPSTREAM_BRANCH names one branch, or several separated by spaces
# or commas: provision.sh pins the two release channels, "stable main", so
# the dashboard's channel switch (Settings > Version) works and no other
# branch does.
check_upstream() {
  local t0 url up want b ok applied warned
  local -a branches=()
  [ -n "$UPSTREAM_URL" ] || [ -n "$UPSTREAM_BRANCH" ] || return 0
  t0=$(now_ms)
  if [ -n "$UPSTREAM_URL" ]; then
    url=$(git_as remote get-url origin 2>/dev/null) || url=""
    if [ "$(norm_url "$url")" != "$(norm_url "$UPSTREAM_URL")" ]; then
      add_step upstream refused "$t0" "origin is ${url:-not set}; the pin is $UPSTREAM_URL"
      finish refused "the checkout's origin is ${url:-not set}, not the pinned $UPSTREAM_URL (DOMOVOI_UPSTREAM_URL in /etc/default/domovoi-update), so nothing was changed"
      return 1
    fi
  fi
  if [ -n "$UPSTREAM_BRANCH" ]; then
    read -r -a branches <<<"${UPSTREAM_BRANCH//,/ }"
    up=$(git_as rev-parse --symbolic-full-name '@{u}' 2>/dev/null) || up=""
    ok=0
    for b in ${branches[@]+"${branches[@]}"}; do
      if [ "$up" = "refs/remotes/origin/$b" ]; then ok=1; fi
    done
    if [ "$ok" = 0 ]; then
      want=$(printf 'origin/%s, ' ${branches[@]+"${branches[@]}"} | sed 's/, $//')
      add_step upstream refused "$t0" "HEAD tracks ${up:-no upstream branch}; the pin is $want"
      if [ "${#branches[@]}" -gt 1 ]; then want="one of the pinned branches ($want)"; else want="the pinned $want"; fi
      finish refused "the checkout tracks ${up:-no upstream branch}, not $want (DOMOVOI_UPSTREAM_BRANCH in /etc/default/domovoi-update), so nothing was changed"
      return 1
    fi
    # And the code itself, by root's own git (delta security review
    # 2026-10-16): the tracking branch is the service user's config, so
    # HEAD must also be on one of the pinned branches as this box last
    # fetched them. A box on stable past the latest release (switched from
    # main) is on origin/main, which provision.sh's pin includes.
    ok=0
    for b in ${branches[@]+"${branches[@]}"}; do
      if git_root merge-base --is-ancestor "$HEAD_SHA" "refs/remotes/origin/$b" 2>/dev/null; then ok=1; fi
    done
    if [ "$ok" = 0 ]; then
      want=$(printf 'origin/%s, ' ${branches[@]+"${branches[@]}"} | sed 's/, $//')
      # The commit this box last applied, and so already ran: a pinned
      # branch rewound past it upstream (a force-push) must not stop a plain
      # restart for ever (rc2 review C2). It restarts with a warning; an
      # update to anything else is still refused below.
      applied=$(read_sha_file "$APPLIED_FILE" 2>/dev/null) || applied=""
      if [ -n "$applied" ] && [ "$applied" = "$HEAD_SHA" ]; then
        warned="HEAD ${HEAD_SHA:0:12} is on none of the pinned branches ($want) as this box last fetched them, upstream rewritten?; it is the commit this box last applied, so this restart goes on, and no update will until a pinned branch holds it again"
        log "WARNING: $warned"
        add_step upstream warn "$t0" "$warned"
        return 0
      fi
      add_step upstream refused "$t0" "HEAD ${HEAD_SHA:0:12} is on none of $want"
      finish refused "HEAD ${HEAD_SHA:0:12} is on none of the pinned branches ($want) as this box last fetched them (DOMOVOI_UPSTREAM_BRANCH in /etc/default/domovoi-update), so nothing was changed"
      return 1
    fi
  fi
  add_step upstream ok "$t0" "origin ${UPSTREAM_URL:-unpinned}, branch ${UPSTREAM_BRANCH:-unpinned}"
}

# ─── The steps ───────────────────────────────────────────────────────────

# Whether UNIT is down: inactive, or failed (a unit systemd had to kill ends
# up failed, and that is still stopped).
unit_stopped() {
  case $(systemctl is-active "$1" 2>/dev/null) in
    inactive|failed|"") return 0 ;;
    *) return 1 ;;
  esac
}

# What happened to UNIT's last stop, for the step detail: "<unit> 0.412 s"
# when it stopped by itself, plus how it ended when it did not: systemd's
# SIGKILL after TimeoutStopSec, this script's SIGKILL, or an error exit (the
# core's own shutdown deadline, domovoi/lifecycle.py).
stop_note() {
  local unit=$1 killed_here=$2 line key val result="" t_exit="" t_in="" note=$1
  if [ "${3-}" = down ]; then
    printf '%s was not running' "$unit"
    return
  fi
  while IFS= read -r line; do
    key=${line%%=*}; val=${line#*=}
    case $key in
      Result) result=$val ;;
      ActiveExitTimestampMonotonic) t_exit=$val ;;
      InactiveEnterTimestampMonotonic) t_in=$val ;;
    esac
  done < <(systemctl show "$unit" -p Result -p ActiveExitTimestampMonotonic \
             -p InactiveEnterTimestampMonotonic 2>/dev/null)
  if [[ $t_exit =~ ^[0-9]+$ ]] && [[ $t_in =~ ^[0-9]+$ ]] && [ "$t_exit" -gt 0 ] \
      && [ "$t_in" -ge "$t_exit" ]; then
    note+=" $(fmt_sec $(((t_in - t_exit) / 1000))) s"
  fi
  if [ "$killed_here" = 1 ]; then
    note+=" (still stopping after ${STOP_TIMEOUT}s: SIGKILLed by this script)"
  else
    case $result in
      success|"") ;;
      timeout) note+=" (systemd SIGKILLed it after TimeoutStopSec)" ;;
      exit-code) note+=" (exited with an error while stopping)" ;;
      *) note+=" (result: $result)" ;;
    esac
  fi
  printf '%s' "$note"
}

# Stop web and core, bounded. A bare `systemctl stop` waits out each unit's
# TimeoutStopSec and then SIGKILLs it: on 2026-09-30 that cost every update
# 90 s (the core swallowed SIGTERM) and showed only as a slow step. Here the
# wait is at most STOP_TIMEOUT; a unit still stopping then is SIGKILLed, and
# one that is still up STOP_KILL_WAIT later fails the step (the update then
# rolls back, as for any failed stop). The step's detail says how long each
# unit took and how it ended, so a slow stop shows in the version panel.
stop_services() {
  local rc=0 unit waited=0 notes="" k
  local -a units=("$WEB_UNIT" "$CORE_UNIT") killed=() still=() down=()
  for unit in "${units[@]}"; do
    if unit_stopped "$unit"; then down+=("$unit"); fi
  done
  timeout "$STOP_TIMEOUT" systemctl stop "${units[@]}" || rc=$?
  if [ "$rc" -eq 124 ]; then
    for unit in "${units[@]}"; do
      if ! unit_stopped "$unit"; then
        echo "$unit still stopping after ${STOP_TIMEOUT}s; sending SIGKILL"
        systemctl kill --signal=SIGKILL "$unit" || true
        killed+=("$unit")
      fi
    done
    while [ "$waited" -lt "$((STOP_KILL_WAIT * 2))" ]; do
      still=()
      for unit in ${killed[@]+"${killed[@]}"}; do
        unit_stopped "$unit" || still+=("$unit")
      done
      [ "${#still[@]}" -eq 0 ] && break
      sleep 0.5
      waited=$((waited + 1))
    done
  elif [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  for unit in "${units[@]}"; do
    k=0
    if [[ " ${killed[*]-} " == *" $unit "* ]]; then k=1; fi
    if [[ " ${down[*]-} " == *" $unit "* ]]; then
      notes+="${notes:+; }$(stop_note "$unit" "$k" down)"
    else
      notes+="${notes:+; }$(stop_note "$unit" "$k")"
    fi
  done
  STEP_DETAIL=$notes
  if [ "${#still[@]}" -gt 0 ]; then
    echo "still running after SIGKILL: ${still[*]}"
    return 1
  fi
  return 0
}

# --no-block: queue the starts and let wait_healthy bound the wait. The
# shipped core is Type=notify, so a blocking start would wait for its
# READY=1 for up to its TimeoutStartSec (15 min) instead of the health
# check's DOMOVOI_UPDATE_HEALTH_TIMEOUT. (After the unit names, where
# systemctl takes options too.)
start_services() { systemctl start "$CORE_UNIT" "$WEB_UNIT" --no-block; }

# domovoi-db is a oneshot: compose up -d postgres, then compose run flyway.
# When it fails, the Postgres container's side follows (DB_DIAGNOSTICS: its
# state, last health check and last 100 log lines, passwords blanked) into
# the update's journal, and its one-line reason is the last line, which is
# what the step's error in the result says (2026-10-17: "container is
# unhealthy" alone never said why).
restart_db() {
  systemctl restart "$DB_UNIT" && return 0
  local rc=$?
  if [ -f "$DB_DIAGNOSTICS" ]; then
    echo "the Postgres container ($DB_DIAGNOSTICS):"
    bash "$DB_DIAGNOSTICS" 2>&1 | sed '$d' | sed 's/^/  /' || true
    echo "$DB_UNIT failed: $(bash "$DB_DIAGNOSTICS" --summary 2>&1 | tail -n 1)"
  fi
  return "$rc"
}

pg_exec() { docker exec "$PG_CONTAINER" "$@"; }

psql_admin() { pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d postgres -tAc "$1"; }

migration_count() {
  local n
  n=$(pg_exec psql -U "$PG_USER" -d "$PG_DB" -tAc 'SELECT count(*) FROM flyway_schema_history' 2>/dev/null | tr -d '[:space:]') || return 1
  [[ $n =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$n"
}

# The versioned SQL migrations Flyway's history holds as applied: what a
# plain restart compares the checkout's migration files with. A baseline or
# schema-creation row is not one of ours, a failed one never applied, and a
# repeatable one has no version.
APPLIED_SQL="SELECT count(*) FROM flyway_schema_history WHERE success AND type = 'SQL' AND version IS NOT NULL"

applied_count() {
  local n
  n=$(pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$APPLIED_SQL" 2>/dev/null | tr -d '[:space:]') || return 1
  [[ $n =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$n"
}

# How many versioned migrations the checkout ships: the files domovoi-db's
# Flyway reads (compose mounts domovoi/db/migrations), named the way Flyway
# names a versioned migration. Only names are read.
migration_files() {
  local f n=0
  for f in "$REPO_DIR"/domovoi/db/migrations/V*__*.sql; do
    [ -e "$f" ] && n=$((n + 1))
  done
  printf '%s' "$n"
}

# Why a plain restart has to restart domovoi-db, printed; nothing when it
# can leave the database alone. Postgres answering with every migration the
# checkout ships applied is the one case it skips. A database that is down,
# unreadable or behind gets what every plain restart did before
# 2026-10-05: the restart that brings Postgres up and runs Flyway.
plain_db_reason() {
  local applied files
  if ! applied=$(applied_count); then
    printf 'Postgres did not answer with its Flyway history'
    return 0
  fi
  files=$(migration_files)
  if [ "$applied" -lt "$files" ]; then
    printf '%s of the %s migrations in the checkout applied' "$applied" "$files"
  fi
}

# The plain restart's domovoi-db step, with the reason it ran as its detail.
plain_restart_db() {
  restart_db || return
  STEP_DETAIL=$DB_REASON
}

# Plugins keep their own migration ledgers, one per plugin schema
# (plugin_<slug>.schema_history, a row per applied file), and the core
# applies pending plugin migrations when it boots, so starting the new code
# can grow them without Flyway noticing. One "plugin_<slug> <rows>" line per
# ledger; query_to_xml runs the count for each schema the catalog lists, so
# one query covers any number of plugins.
LEDGER_SQL="SELECT n.nspname || ' ' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.schema_history', n.nspname), false, true, '')))[1]::text FROM pg_namespace n JOIN pg_class c ON c.relnamespace = n.oid WHERE n.nspname ~ '^plugin_[a-z][a-z0-9_]*$' AND c.relname = 'schema_history' AND c.relkind IN ('r', 'p') ORDER BY 1"

# The plugin ledgers of database $1, after a "ledgers" line that tells an
# empty answer (no plugin has migrations) from a failed read.
ledger_snapshot() {
  local out line
  out=$(pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$1" -tAc "$LEDGER_SQL" 2>/dev/null) || return 1
  while IFS= read -r line; do
    [ -z "$line" ] || [[ $line =~ ^plugin_[a-z0-9_]+\ [0-9]+$ ]] || return 1
  done <<<"$out"
  printf 'ledgers\n%s' "$out"
}

# Did any plugin ledger gain rows between snapshots $1 and $2? A ledger
# only $2 has counts from zero. Either snapshot unknown: no, the same rule
# migrations_grew applies to Flyway.
ledgers_grew() {
  [ "${1%%$'\n'*}" = ledgers ] && [ "${2%%$'\n'*}" = ledgers ] || return 1
  { printf '%s\n' "$1" | sed '1d; s/^/B /'; printf '%s\n' "$2" | sed '1d; s/^/A /'; } \
    | awk '$1 == "B" { before[$2] = $3; next }
           $1 == "A" && $3 + 0 > before[$2] + 0 { grew = 1 }
           END { exit grew ? 0 : 1 }'
}

# Total rows across a snapshot's ledgers, or nothing when it's unknown.
ledger_total() {
  [ "${1%%$'\n'*}" = ledgers ] || return 0
  printf '%s\n' "$1" | sed '1d' | awk '{ n += $2 } END { print n + 0 }'
}

# The plugins registry, one "slug|t|status|last_error" line per row (t or f
# for enabled; last_error on one line and cut short). A single text column,
# so what psql -tA prints is exactly the value.
PLUGINS_SQL="SELECT slug || '|' || CASE WHEN enabled THEN 't' ELSE 'f' END || '|' || status || '|' || left(regexp_replace(coalesce(last_error, ''), '[[:space:]]+', ' ', 'g'), 200) FROM plugins ORDER BY slug"

# Followed by the quoted slugs, parenthesised. Only rows a load error
# switched off; a plugin somebody disabled by hand stays disabled.
REENABLE_SQL="UPDATE plugins SET enabled = true, updated_at = now() WHERE NOT enabled AND status = 'load_error' AND slug IN"

# The registry after a "plugins" line (the same trick as ledger_snapshot).
# The core writes each plugin's status into it as it loads the plugin, and
# it loads them before it starts answering /v1/health (discovery runs inside
# its startup), so after a passing health check the rows are this boot's.
# Read through the Postgres container like the migration counts: no admin
# credential, and nothing that depends on the shape of an HTTP answer.
plugin_states() {
  local out
  out=$(pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$PLUGINS_SQL" 2>/dev/null) || return 1
  printf 'plugins\n%s' "$out"
}

# Plugins that loaded before the update (enabled and not at load_error in
# registry snapshot $1) and are at load_error in snapshot $2. With $3 =
# errors, one "slug: last_error" line each; with $3 = off, the slugs of
# those the load error also switched off. Nothing when either snapshot is
# unknown. A plugin that was already failing or disabled before the update
# isn't this update's doing, and a plugin new with it had nothing to lose.
plugins_broken() {
  [ "${1%%$'\n'*}" = plugins ] && [ "${2%%$'\n'*}" = plugins ] || return 0
  { printf '%s\n' "$1" | sed '1d; s/^/B|/'; printf '%s\n' "$2" | sed '1d; s/^/A|/'; } \
    | awk -F'|' -v mode="$3" '
        $1 == "B" { if ($3 == "t" && $4 != "load_error" && $4 != "uninstalled") up[$2] = 1; next }
        $1 != "A" || !($2 in up) || $4 != "load_error" { next }
        mode == "off" { if ($3 == "f") print $2; next }
        { err = $0; for (i = 0; i < 4; i++) sub(/^[^|]*[|]/, "", err)
          print $2 (err == "" ? "" : ": " err) }'
}

# After the health check: every plugin that loaded before the update still
# loads. The loader keeps plugin failures from taking the core down, so the
# health endpoints stay green through them; without this a plugin the new
# code breaks would vanish from the house under an "ok" update.
check_plugins() {
  local now broken line list=""
  if [ "${PLUGINS_BEFORE%%$'\n'*}" != plugins ]; then
    echo "no pre-update plugin snapshot to compare against; not checked"
    return 0
  fi
  now=$(plugin_states) || { echo "cannot read the plugins registry"; return 1; }
  broken=$(plugins_broken "$PLUGINS_BEFORE" "$now" errors)
  if [ -z "$broken" ]; then
    echo "every plugin that loaded before the update still loads"
    return 0
  fi
  while IFS= read -r line; do list+="${list:+; }$line"; done <<<"$broken"
  echo "plugins that loaded before the update are at load_error now: $list"
  return 1
}

# The loader switches a plugin off when its import, register() or contract
# check fails (a failed migration catch-up leaves it on), and a boot skips a
# switched-off plugin. Rolling the code back alone would leave every plugin
# the new code broke off for good, so switch each one back on (slugs in $@)
# while the core is stopped, and the previous SHA's boot loads it again.
reenable_plugins() {
  local slug in=""
  for slug in "$@"; do
    [[ $slug =~ ^[a-z][a-z0-9_]{1,31}$ ]] || { echo "not a plugin slug: $slug"; return 1; }
    in+="${in:+, }'$slug'"
  done
  echo "switching back on what the failed boot switched off: $*"
  pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d "$PG_DB" -tAc "$REENABLE_SQL ($in)"
}

# Does the <db>_test twin exist? Without it there is nothing to back up or
# restore, and the plugin runtime has nothing to migrate there either.
resolve_test_db() {
  [ -n "$TEST_DB" ] || return 0
  if [ "$(psql_admin "SELECT 1 FROM pg_database WHERE datname = '$TEST_DB'" 2>/dev/null)" = 1 ]; then
    log "$TEST_DB exists: it is backed up and restored with $PG_DB"
  else
    TEST_DB=""
  fi
}

# pg_dump -Fc database $1 into file $2, and check pg_restore can read it.
# On failure no file is left behind.
dump_db() {
  local db=$1 f=$2 partial=$2.partial
  echo "dumping $db from container $PG_CONTAINER to $f"
  if ! (umask 077; pg_exec pg_dump -Fc -U "$PG_USER" "$db" >"$partial"); then
    rm -f "$partial"; return 1
  fi
  if [ ! -s "$partial" ]; then
    echo "pg_dump produced an empty file"; rm -f "$partial"; return 1
  fi
  # A dump that pg_restore can't read is no backup at all.
  if ! docker exec -i "$PG_CONTAINER" pg_restore --list <"$partial" >/dev/null; then
    echo "pg_restore cannot read the dump"; rm -f "$partial"; return 1
  fi
  mv -f "$partial" "$f" || { rm -f "$partial"; return 1; }
}

backup_db() {
  local base
  mkdir -p "$BACKUP_DIR" || return 1
  # Dumps hold every secret in the database. Each one is 0600 regardless
  # (umask in dump_db); the dir is closed too where the filesystem allows it.
  chmod 0700 "$BACKUP_DIR" || echo "warning: could not chmod 0700 $BACKUP_DIR"
  base=$BACKUP_DIR/pre-${HEAD_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ)
  dump_db "$PG_DB" "$base.dump" || return 1
  BACKUP_FILE=$base.dump
  if [ -n "$TEST_DB" ]; then
    dump_db "$TEST_DB" "$base.test.dump" || return 1
    TEST_BACKUP_FILE=$base.test.dump
  fi
  prune_backups
}

# Two series, each pruned to the newest KEEP_BACKUPS: the database's
# pre-<sha>-<timestamp>.dump and the test twin's .test.dump beside it.
prune_backups() {
  [[ $KEEP_BACKUPS =~ ^[0-9]+$ ]] && [ "$KEEP_BACKUPS" -ge 1 ] || return 0
  prune_series 'pre-*Z.dump'
  prune_series 'pre-*Z.test.dump'
}

prune_series() {
  local old name
  # Newest first; everything past the first KEEP_BACKUPS goes. Names carry
  # no whitespace. $1 is a glob, deliberately unquoted.
  old=$(cd "$BACKUP_DIR" && ls -1t -- $1 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))") || return 0
  for name in $old; do
    echo "pruning old backup $name"
    rm -f -- "${BACKUP_DIR:?}/$name"
  done
}

# The replaced copies of database $1 (<db>_failed_<14-digit UTC time>),
# newest first.
failed_dbs() {
  psql_admin "SELECT datname FROM pg_database WHERE datname ~ '^${1}_failed_[0-9]{14}\$' ORDER BY datname DESC"
}

# Keep the newest KEEP_FAILED_DBS replaced copies of database $1 and drop
# the rest. Each holds everything the database held (transcripts, voice
# embeddings, credential hashes, the household token), so they don't pile
# up. Best effort: a copy that can't be listed or dropped stays, and says so.
prune_failed_dbs() {
  local db=$1 names name n=0
  [[ $KEEP_FAILED_DBS =~ ^[0-9]+$ ]] && [ "$KEEP_FAILED_DBS" -ge 1 ] || KEEP_FAILED_DBS=1
  if ! names=$(failed_dbs "$db" 2>/dev/null); then
    echo "could not list the ${db}_failed_* databases; none dropped"
    return 0
  fi
  while IFS= read -r name; do
    [[ $name =~ ^${db}_failed_[0-9]{14}$ ]] || continue
    n=$((n + 1))
    [ "$n" -gt "$KEEP_FAILED_DBS" ] || continue
    echo "dropping $name (the newest $KEEP_FAILED_DBS replaced copies of $db are kept)"
    psql_admin "DROP DATABASE IF EXISTS \"$name\"" || echo "could not drop $name"
  done <<<"$names"
  return 0
}

# Restore dump $2 into a fresh database, then swap it in for database $1 by
# rename. `pg_restore --clean` into the live database would leave behind
# every object a failed migration CREATED (it only drops what the dump
# contains), and the re-run of that migration after a fix would then fail on
# "already exists". The replaced database is kept as <db>_failed_<ts> for
# inspection; only the newest KEEP_FAILED_DBS of those stay (prune_failed_dbs).
restore_into() {
  local db=$1 dump=$2 ts tmpdb olddb
  [ -n "$dump" ] && [ -s "$dump" ] || { echo "no backup of $db to restore"; return 1; }
  ts=$(date -u +%Y%m%d%H%M%S)
  tmpdb=${db}_restore_$ts
  olddb=${db}_failed_$ts
  psql_admin "CREATE DATABASE \"$tmpdb\" OWNER \"$PG_USER\"" || return 1
  if ! docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$tmpdb" \
      --single-transaction --exit-on-error <"$dump"; then
    psql_admin "DROP DATABASE IF EXISTS \"$tmpdb\"" || true
    return 1
  fi
  psql_admin "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid()" >/dev/null || true
  if ! psql_admin "ALTER DATABASE \"$db\" RENAME TO \"$olddb\""; then
    psql_admin "DROP DATABASE IF EXISTS \"$tmpdb\"" || true
    return 1
  fi
  if ! psql_admin "ALTER DATABASE \"$tmpdb\" RENAME TO \"$db\""; then
    psql_admin "ALTER DATABASE \"$olddb\" RENAME TO \"$db\"" || true
    return 1
  fi
  echo "restored $dump; the replaced database is kept as $olddb"
  prune_failed_dbs "$db"
}

restore_db() { restore_into "$PG_DB" "$BACKUP_FILE" && DB_RESTORED=1; }

restore_test_db() { restore_into "$TEST_DB" "$TEST_BACKUP_FILE" && TEST_DB_RESTORED=1; }

pip_as() {
  as_user "$VENV_DIR/bin/python" -m pip --disable-pip-version-check --no-input "$@"
}

# Can the service user change the venv that sync_deps would change? Asked
# before anything is touched: a venv that isn't there, or that another user
# owns (one built by an admin before the checkout was handed to the service
# user), aborts the update instead of failing half-way through and then
# failing the rollback's re-sync the same way.
venv_writable() {
  local purelib d
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "no venv interpreter at $VENV_DIR/bin/python (set DOMOVOI_VENV)"; return 1
  fi
  purelib=$(as_user "$VENV_DIR/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' 2>/dev/null) || purelib=""
  for d in "$VENV_DIR" "$VENV_DIR/bin" ${purelib:+"$purelib"}; do
    if ! as_user test -w "$d"; then
      echo "$d is not writable by ${SERVICE_USER:-the service user}"; return 1
    fi
  done
}

# ─── The helper-container secrets (2026-10 review REV-16) ────────────────

# docker-compose.yml takes LETTA_TOKEN and SEARXNG_SECRET from domovoi/.env
# and refuses every command (domovoi-db's compose up included) without
# them: there is no longer a fallback to the values every install shared.
# A .env written before they were generated gets its own, appended by the
# checkout's `python -m domovoi.env_bootstrap --repair` as the service user
# (the file is that user's; root never edits it), before anything here uses
# compose. Nothing else in the file changes, and with both set it writes
# nothing. No step is recorded then: the result only says something when a
# value was added (ok; a `warn` when a running Letta still has the old
# password) or when the repair failed (the run then aborts, nothing changed).
repair_env_secrets() {
  local t0 out rc=0 added
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    log "no venv interpreter at $VENV_DIR/bin/python: domovoi/.env's helper secrets not checked"
    return 0
  fi
  t0=$(now_ms)
  set +e
  out=$(as_user "$VENV_DIR/bin/python" -m domovoi.env_bootstrap --repair 2>&1)
  rc=$?
  set -e
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/    /'; fi
  if [ "$rc" -ne 0 ]; then
    add_step env-secrets failed "$t0" "$(trunc "$out" 300)"
    LAST_ERROR="env-secrets failed (exit $rc): $(trunc "$(printf '%s' "$out" | tail -n 3 | tr '\n' ' ')" 400 | sed 's/[[:space:]]*$//')"
    log "could not give domovoi/.env its helper secrets (exit $rc)"
    return 1
  fi
  added=$(printf '%s\n' "$out" | sed -n 's/^.*: added \([A-Z_][A-Z_, ]*\) (.*$/\1/p' | head -n 1)
  [ -n "$added" ] || return 0
  if [[ $added == *LETTA_TOKEN* ]] && letta_running; then
    add_step env-secrets warn "$t0" "added $added to domovoi/.env; domovoi-letta still runs with the old shared password, so chat mode fails until it is recreated: docker compose --profile chat up -d letta (docs/LINUX_HOST.md)"
    log "WARNING: LETTA_TOKEN added while domovoi-letta runs with the old password; recreate it: docker compose --profile chat up -d letta"
  else
    add_step env-secrets ok "$t0" "added $added to domovoi/.env"
  fi
  log "domovoi/.env: added $added (generated for this install)"
}

letta_running() {
  [ "$(docker inspect -f '{{.State.Running}}' domovoi-letta 2>/dev/null)" = true ]
}

# ─── Dependency sync: the hash-pinned lock (OPS-9) ───────────────────────

# The venv the way docs/LINUX_HOST.md builds it. Without DOMOVOI_USE_LOCK=1,
# with pip's resolver as before the lock existed (sync_deps_resolved), and
# the step says the lock is there to opt into. With it, from the checkout's
# hash-pinned lock whenever it has one for the venv's Python
# (sync_deps_locked); otherwise, loudly, with the resolver: the step is a
# `warn` whose detail ("lock not applied: <why>") the core keeps for the
# Version card, so an owner who opted in sees the day the lock stops being
# used (the venv moved to another Python, the lock was renamed). A package
# that does not match its hash is not "unusable": pip refuses it, the step
# fails and the update rolls back.
sync_deps() {
  local why
  DEPS_FROM_LOCK=0
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "no venv interpreter at $VENV_DIR/bin/python (set DOMOVOI_VENV)"; return 1
  fi
  if [ "$USE_LOCK" != 1 ]; then
    sync_deps_resolved || return 1
    if [ -n "$DEPS_LOCK" ] && [ -f "$REPO_DIR/$DEPS_LOCK" ]; then
      STEP_DETAIL="resolved from the index, as before; $DEPS_LOCK is in the checkout and opt-in (DOMOVOI_USE_LOCK=1, docs/LINUX_HOST.md)"
    fi
    return 0
  fi
  if why=$(lock_usable); then
    sync_deps_locked
  else
    log "WARNING: not installing from a hash-pinned lock ($why): pip resolves the dependencies from the index and checks no hashes"
    sync_deps_resolved || return 1
    STEP_STATUS=warn
    STEP_DETAIL="lock not applied: $why; resolved from the index without hash checks"
  fi
}

# lock_usable: whether sync_deps installs from DEPS_LOCK. When it can't,
# the reason is on stdout and the status is 1. The lock is in the service
# user's checkout, so it is looked at as that user, the way the installer's
# pre-flight reads it: root never opens a path that user can point anywhere.
lock_usable() {
  local lock=$REPO_DIR/$DEPS_LOCK want have
  if [ -z "$DEPS_LOCK" ]; then echo "DOMOVOI_DEPS_LOCK is empty"; return 1; fi
  if ! as_user test -f "$lock"; then echo "the checkout has no $DEPS_LOCK"; return 1; fi
  want=$(as_user sed -n 's/^# This file is autogenerated by pip-compile with Python \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' "$lock" | head -n 1)
  if [ -z "$want" ]; then echo "$DEPS_LOCK does not say which Python it was compiled for"; return 1; fi
  have=$(as_user "$VENV_DIR/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || have=""
  if [ "$have" != "$want" ]; then
    echo "$DEPS_LOCK is for Python $want and the venv runs Python ${have:-unknown}"; return 1
  fi
}

# extras_beyond_lock: the PIP_EXTRAS the lock does not cover, comma-joined.
extras_beyond_lock() {
  local e IFS=,
  local -a out=()
  for e in $PIP_EXTRAS; do
    e=${e//[[:space:]]/}
    [ -n "$e" ] || continue
    case ",$LOCK_EXTRAS," in *,"$e",*) ;; *) out+=("$e") ;; esac
  done
  printf '%s' "${out[*]}"
}

# The lock's requirement lines (name==version and every --hash) for the
# packages named in roots= and everything they need, found through
# pip-compile's "# via" lines to a fixed point (wheel needs packaging, for
# one). The same program as provision.sh's, character for character
# (domovoi/tests/test_provision_script.py holds the two together): kept
# here rather than in a file of its own, because a rollback's re-sync runs
# against the previous checkout, which may not have that file, while this
# script is already read whole.
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

# From the lock, as docs/LINUX_HOST.md "Install" does it by hand and
# provision.sh's venv step does it: the build tools first, as the lock's
# own stanzas for setuptools, wheel and what they need, each with its
# hashes, in a requirements file (-r). Not "-c <lock> setuptools wheel":
# the pip that Ubuntu 26.04's venv ships (25.1) takes no hashes from a
# constraints file and refuses that. The file is root's, in the update
# dir, readable by the service user who runs pip (the lock itself is read
# as that user). Then every pin with --require-hashes, the sdist-only ones
# built against those tools rather than in an isolated build env that
# would fetch an unchecked setuptools; then the checkout itself, adding
# nothing. The lock names its own CPU torch index, so there is no torch
# step. Extras the lock does not cover (DOMOVOI_PIP_EXTRAS beyond
# LOCK_EXTRAS: cuda, fastlane, ...) go through the resolver afterwards, and
# the step says so.
sync_deps_locked() {
  local lock=$REPO_DIR/$DEPS_LOCK beyond tools reqs rc=0
  tools=$(as_user awk -v roots="setuptools wheel" "$LOCK_CLOSURE_AWK" "$lock") || tools=""
  if [[ $'\n'$tools != *$'\n'setuptools==* || $'\n'$tools != *$'\n'wheel==* || $tools != *--hash=sha256:* ]]; then
    echo "$DEPS_LOCK has no hash-pinned setuptools and wheel to build with"
    return 1
  fi
  echo "build tools from $DEPS_LOCK: $(printf '%s\n' "$tools" | sed -n 's/^\([A-Za-z0-9._-]*==[^ ]*\).*/\1/p' | tr '\n' ' ')"
  reqs=$(mktemp "$UPDATE_DIR/build-tools.XXXXXX") || return 1
  if ! { printf '%s\n' "$tools" >"$reqs" && chmod 0644 "$reqs"; }; then rm -f "$reqs"; return 1; fi
  (cd "$REPO_DIR" && pip_as install --require-hashes -r "$reqs") || rc=$?
  rm -f "$reqs"
  [ "$rc" -eq 0 ] || return 1
  (cd "$REPO_DIR" && pip_as install --require-hashes --no-build-isolation -r "$lock") || return 1
  if [ -n "$DEPS_NODEPS_LOCK" ] && as_user test -f "$REPO_DIR/$DEPS_NODEPS_LOCK"; then
    (cd "$REPO_DIR" && pip_as install --require-hashes --no-deps -r "$REPO_DIR/$DEPS_NODEPS_LOCK") || return 1
  fi
  (cd "$REPO_DIR" && pip_as install --no-deps --no-build-isolation -e .) || return 1
  DEPS_FROM_LOCK=1
  STEP_DETAIL="installed from $DEPS_LOCK, every package hash-checked"
  beyond=$(extras_beyond_lock)
  if [ -n "$beyond" ]; then
    log "WARNING: extras outside $DEPS_LOCK ($beyond) come from pip's resolver, without hash checks"
    (cd "$REPO_DIR" && pip_as install -e ".[$beyond]") || return 1
    STEP_DETAIL="$STEP_DETAIL; extras outside it ($beyond) resolved from the index without hash checks"
  fi
}

# Without a usable lock: CPU torch first when the extras pull torch in (so
# pip never fetches the CUDA build), then the editable install with the
# extras, then (with real-clients or voice-profile) faster-whisper and
# resemblyzer without their declared dependencies, as requirements-no-deps.in
# lists them.
sync_deps_resolved() {
  local target=.
  if [ -n "$TORCH_INDEX_URL" ] && [[ ",$PIP_EXTRAS," == *,voice-profile,* ]]; then
    (cd "$REPO_DIR" && pip_as install torch --index-url "$TORCH_INDEX_URL") || return 1
  fi
  if [ -n "$PIP_EXTRAS" ]; then target=".[$PIP_EXTRAS]"; fi
  (cd "$REPO_DIR" && pip_as install -e "$target") || return 1
  if [[ ",$PIP_EXTRAS," == *,real-clients,* || ",$PIP_EXTRAS," == *,voice-profile,* ]] \
    && [ -f "$REPO_DIR/$NODEPS_REQS" ]; then
    (cd "$REPO_DIR" && pip_as install --no-deps -r "$NODEPS_REQS") || return 1
  fi
}

# ─── Packages a release stopped using ────────────────────────────────────

# pip never removes a package nothing requires any more, so a venv keeps
# whatever an older pyproject pulled in. These are uninstalled after a
# forward re-sync, never a rollback's (the previous checkout still needs
# them, and its re-sync installs them again): piper-tts, GPL-3.0 since 1.3
# with espeak-ng (GPL-3.0) inside, which Domovoi's own text-to-phoneme
# converter and ONNX Runtime replaced in 2026-10 (domovoi/phonemizer), and
# piper-phonemize, its older espeak-ng wrapper; edge-tts (LGPL-3.0), the
# Microsoft Edge online voices, dropped on 2026-10-12. A step is recorded
# only when something was removed; a failure is a `warn` and the update
# stands (nothing imports them any more).
RETIRED_PACKAGES=(piper-tts piper-phonemize edge-tts)
drop_retired_packages() {
  local t0 out rc=0 gone
  t0=$(now_ms)
  set +e
  out=$(pip_as uninstall -y "${RETIRED_PACKAGES[@]}" 2>&1)
  rc=$?
  set -e
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/    /'; fi
  if [ "$rc" -ne 0 ]; then
    add_step drop-retired-packages warn "$t0" "$(trunc "$out" 300)"
    log "could not uninstall ${RETIRED_PACKAGES[*]} (exit $rc); not fatal, nothing uses them"
    return 0
  fi
  gone=$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*Successfully uninstalled \([^[:space:]]*\).*$/\1/p' | tr '\n' ' ')
  gone=${gone% }
  [ -n "$gone" ] || return 0
  add_step drop-retired-packages ok "$t0" "uninstalled $gone (no longer used)"
  log "uninstalled $gone (no longer used)"
}

# ─────────────────────────────────────────────────────────────────────────

snapshot_venv() {
  pip_as freeze --exclude-editable >"$FREEZE_FILE.tmp" && chmod 0644 "$FREEZE_FILE.tmp" \
    && mv -f "$FREEZE_FILE.tmp" "$FREEZE_FILE"
}

# After the rolled-back sync_deps: put every package back at the exact
# version it had before the update. A plain install from the old pyproject
# keeps any newer version that still satisfies it; the snapshot doesn't.
restore_pins() {
  local pins=$UPDATE_DIR/pip-restore.txt extra=()
  if [ "$DEPS_FROM_LOCK" = 1 ]; then
    # The rolled-back checkout's lock already put back exact, hash-checked
    # versions; the snapshot would only reinstall them unchecked.
    STEP_DETAIL="skipped: $DEPS_LOCK put back the exact versions"
    return 0
  fi
  [ -s "$FREEZE_FILE" ] || { echo "no pre-update snapshot"; return 0; }
  # Direct references (name @ url) may point at files long gone.
  grep -v -e ' @ ' -e '^-e ' -e '^#' "$FREEZE_FILE" >"$pins" || true
  chmod 0644 "$pins"
  if [ -n "$TORCH_INDEX_URL" ]; then extra=(--extra-index-url "$TORCH_INDEX_URL"); fi
  (cd "$REPO_DIR" && pip_as install "${extra[@]}" -r "$pins")
}

# Build exactly as mpd_provisioner._build_image does, under the tag the core
# runs. The provisioner never recreates a running room on an image change
# (and mpd.conf is a single-file bind mount, pinned to the old inode), so
# remove the room containers; the core's startup warm (warm_known_rooms)
# recreates each from its mpd_rooms row with the same named data volume.
rebuild_mpd() {
  local names n
  if [ "$POLICY_AT_START" = never ] && [ "$MPD_IMAGE_CHANGED" = 0 ]; then
    # Only mpd.conf changed, and the build would at most re-pull the base
    # image: keep the image (internet access is turned off) and just
    # recreate the rooms so they mount the new conf.
    echo "internet access is turned off: keeping the $MPD_TAG image (Dockerfile.mpd unchanged)"
  else
    # As the service user (in the docker group, as the core that builds it
    # too): root's docker CLI would read the Dockerfile through whatever
    # link the checkout holds there.
    as_user docker build -t "$MPD_TAG" -f "$REPO_DIR/domovoi/Dockerfile.mpd" "$REPO_DIR/domovoi" || return 1
  fi
  names=$(docker ps -a --format '{{.Names}}') || return 1
  for n in $names; do
    if [[ $n == "$MPD_PREFIX"* ]]; then
      echo "removing $n (recreated by the core with the new image; data volume kept)"
      docker rm -f "$n" || return 1
    fi
  done
}

wait_healthy() {
  local deadline core_ok=0 web_ok=0
  deadline=$(($(date +%s) + HEALTH_TIMEOUT))
  while :; do
    if [ "$core_ok" = 0 ] && curl -fsS -o /dev/null --max-time 5 "$CORE_HEALTH_URL"; then core_ok=1; fi
    if [ "$web_ok" = 0 ] && curl -fsS -o /dev/null --max-time 5 "$WEB_HEALTH_URL"; then web_ok=1; fi
    if [ "$core_ok" = 1 ] && [ "$web_ok" = 1 ]; then
      echo "core and web healthy"; return 0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then break; fi
    sleep "$HEALTH_INTERVAL"
  done
  echo "not healthy after ${HEALTH_TIMEOUT}s: core $([ "$core_ok" = 1 ] && echo up || echo down), web $([ "$web_ok" = 1 ] && echo up || echo down)"
  return 1
}

# The internet answer, as the core reads it (INTERNET_ACCESS from the
# environment, then domovoi/.env): always, sometimes, never, or empty while
# unanswered. Fails when the checkout's code can't say (a tree from before
# the setting existed).
internet_policy() {
  local p
  p=$(as_user "$VENV_DIR/bin/python" -m domovoi.egress --print-policy 2>/dev/null) || return 1
  printf '%s' "$p" | tr -d '[:space:]'
}

# Whether the search helper's start goes to a transient systemd unit
# (DOMOVOI_UPDATE_SEARXNG_DETACH: auto = when systemd runs this host).
searxng_detach() {
  case "$(printf '%s' "$SEARXNG_DETACH" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) return 1 ;;
    1|true|yes|on) command -v systemd-run >/dev/null 2>&1 ;;
    *) [ -d /run/systemd/system ] && command -v systemd-run >/dev/null 2>&1 ;;
  esac
}

# searxng_start POLICY COMPOSE_FILE: `docker compose up -d` the search
# helper. Detached (a transient unit the update does not wait for, which
# also survives the update unit's own exit), or in the foreground bounded
# by SEARXNG_TIMEOUT: the first start pulls the image, and on a slow or
# metered line that must not hold the update open, let alone let the
# unit's TimeoutStartSec kill it into a "failed" result.
#
# As the service user, either way (delta review of the root verifier,
# 2026-10-16): root's own docker CLI would read the project's .env, and
# whatever else compose opens, from the checkout. That user is in the
# docker group already, so it loses nothing; the compose file is the
# verified copy's (searxng_compose_file), the project directory (its .env,
# ./searxng) the checkout's, read by that user.
searxng_start() {
  local policy=$1 compose_file=$2 rc=0 group="" home=""
  local -a cmd=(docker compose -f "$compose_file" --project-directory "$REPO_DIR/domovoi"
                up -d --no-deps "$SEARXNG_SERVICE")
  local -a sd=(systemd-run --no-block --collect --quiet --unit="$SEARXNG_START_UNIT") as=()
  if [ "$RUN_AS_ROOT" = 1 ] && [ -n "$SERVICE_USER" ] && [ "$SERVICE_USER" != root ]; then
    group=${RESULT_GROUP:-$(id -gn "$SERVICE_USER" 2>/dev/null || true)}
    home=$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6) || home=""
    sd+=(--uid="$SERVICE_USER")
    if [ -n "$group" ]; then sd+=(--gid="$group"); fi
    if [ -n "$home" ]; then sd+=(--setenv=HOME="$home"); fi
    sd+=(-p WorkingDirectory="$REPO_DIR")
    as=(runuser -u "$SERVICE_USER" --)
  fi
  if searxng_detach; then
    if "${sd[@]}" "${cmd[@]}"; then
      STEP_DETAIL="starting $SEARXNG_CONTAINER in the background ($SEARXNG_START_UNIT; internet answer: $policy)"
      return 0
    fi
    if systemctl is-active --quiet "$SEARXNG_START_UNIT" 2>/dev/null; then
      STEP_DETAIL="$SEARXNG_CONTAINER is already starting in the background ($SEARXNG_START_UNIT)"
      return 0
    fi
    return 1
  fi
  if command -v timeout >/dev/null 2>&1; then
    timeout "$SEARXNG_TIMEOUT" ${as[@]+"${as[@]}"} "${cmd[@]}" || rc=$?
  else
    ${as[@]+"${as[@]}"} "${cmd[@]}" || rc=$?
  fi
  if [ "$rc" -eq 124 ]; then
    echo "the search helper did not start within ${SEARXNG_TIMEOUT}s (DOMOVOI_UPDATE_SEARXNG_TIMEOUT); start it again from Settings > Internet"
    return 1
  fi
  [ "$rc" -eq 0 ] || return 1
  STEP_DETAIL="$SEARXNG_CONTAINER up (internet answer: $policy)"
}

# The compose file the search helper starts from: the verified copy's,
# put where the service user can read it and where it outlives this run (a
# detached start may come after RuntimeDirectory= removed the copy):
# DOMOVOI_UPDATE_DIR/searxng-compose.yml, root's, 0644. Only a copy from a
# verifier older than the compose file in it, or a run that isn't root's,
# names the checkout's own (read by the service user, like everything of
# the checkout).
searxng_compose_file() {
  local src=${VERIFIED_DIR:+$VERIFIED_DIR/domovoi/docker-compose.yml} dest=$UPDATE_DIR/searxng-compose.yml tmp
  if [ -n "$src" ] && [ -f "$src" ] && [ ! -L "$src" ] \
      && tmp=$(mktemp "$UPDATE_DIR/.searxng-compose.yml.XXXXXX") \
      && cp "$src" "$tmp" && chmod 0644 "$tmp" && mv -f "$tmp" "$dest"; then
    printf '%s' "$dest"
    return 0
  fi
  rm -f "${tmp:-}"
  printf '%s' "$REPO_DIR/domovoi/docker-compose.yml"
}

# Start or stop the search helper (SearXNG) to match the internet answer:
# Yes or Sometimes start it (the first start pulls the pinned image), No
# stops it if it runs, unanswered leaves it as it is. Run as a soft step.
reconcile_searxng() {
  local policy compose_file
  case "$(printf '%s' "$MANAGE_SEARXNG" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off)
      STEP_DETAIL="skipped: DOMOVOI_MANAGE_SEARXNG=$MANAGE_SEARXNG"
      return 0 ;;
  esac
  if ! policy=$(internet_policy); then
    STEP_DETAIL="skipped: this checkout can't say the internet answer"
    return 0
  fi
  case "$policy" in
    always|sometimes)
      compose_file=$(searxng_compose_file)
      searxng_start "$policy" "$compose_file" || return 1 ;;
    never)
      if [ "$(docker inspect -f '{{.State.Running}}' "$SEARXNG_CONTAINER" 2>/dev/null)" = true ]; then
        docker stop "$SEARXNG_CONTAINER" || return 1
        STEP_DETAIL="$SEARXNG_CONTAINER stopped (internet answer: never)"
      else
        STEP_DETAIL="$SEARXNG_CONTAINER not running (internet answer: never)"
      fi ;;
    *)
      STEP_DETAIL="left as it is: the internet question isn't answered" ;;
  esac
}

# Ubuntu's own background jobs, set to the internet answer by
# scripts/linux/os-jobs.sh (the helper provision.sh runs too): for never
# the updaters, news and crash-report timers are masked; for always and
# sometimes what an earlier never masked is unmasked; on WSL wsl-pro and
# snapd stay off. So an answer changed in Settings takes effect at the
# next healthy restart or update. It never fails the run, and records a
# step only when the helper changed something (ok) or failed (warn); a
# run with nothing to change adds nothing to the result.
reconcile_os_jobs() {
  local policy t0 out rc=0 summary line detail=""
  case "$(printf '%s' "$MANAGE_OS_JOBS" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) log "Ubuntu's background jobs left alone (DOMOVOI_MANAGE_OS_JOBS=$MANAGE_OS_JOBS)"; return 0 ;;
  esac
  if [ ! -f "$OS_JOBS" ]; then
    log "no $OS_JOBS: Ubuntu's background jobs left as they are"
    return 0
  fi
  if ! policy=$(internet_policy); then
    log "this checkout can't say the internet answer: Ubuntu's background jobs left as they are"
    return 0
  fi
  t0=$(now_ms)
  log "step os-jobs"
  set +e
  out=$(bash "$OS_JOBS" --internet "$policy" 2>&1)
  rc=$?
  set -e
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/    /'; fi
  summary=$(printf '%s\n' "$out" | sed -n 's/^os-jobs: //p' | tail -n 1)
  if [ "$rc" -ne 0 ]; then
    # The core shows this detail on the Version card (git_version
    # _WARN_DETAIL_STEPS), so it is built from the helper's own lines
    # ("could not mask X", its summary), never a tool's raw output.
    while IFS= read -r line; do
      case $line in "could not "*|"os-jobs: "*) detail+="${detail:+; }${line#os-jobs: }" ;; esac
    done <<<"$out"
    add_step os-jobs warn "$t0" "$(trunc "Ubuntu's background jobs: ${detail:-the helper stopped (exit $rc)}" 600)"
    log "step os-jobs failed (exit $rc); not fatal, the run goes on"
  elif [[ $summary != "nothing to change"* ]]; then
    add_step os-jobs ok "$t0" "$(trunc "$summary" 600)"
  fi
  return 0
}

# The ADDED_PACKAGES that are missing, installed from Ubuntu's own archive
# (apt checks them against Ubuntu's signed index), --no-install-recommends,
# not held, at a healthy update or Restart, like os-jobs: first with the
# package lists apt already has, then once more after apt-get update. Not
# under never (a download: the journal says so, no step). It never fails
# the run: a failure is a warn step (the Version card shows its detail);
# a run with nothing missing adds nothing to the result.
reconcile_added_packages() {
  local p t0 out rc=0 policy
  # A short wait for apt's lock (unattended-upgrades may hold it): the
  # Restart must not hang on it; the next update or Restart tries again.
  local -a missing=() apt=(apt-get -o Dpkg::Use-Pty=0 -o DPkg::Lock::Timeout=20 -q)
  case "$(printf '%s' "$MANAGE_PACKAGES" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) log "added system packages left to the administrator (DOMOVOI_MANAGE_PACKAGES=$MANAGE_PACKAGES)"; return 0 ;;
  esac
  for p in "${ADDED_PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}\n' "$p" 2>/dev/null | grep -q 'install ok installed' || missing+=("$p")
  done
  [ "${#missing[@]}" -gt 0 ] || return 0
  policy=$(internet_policy 2>/dev/null || true)
  if [ "$policy" = never ]; then
    log "not installed while internet access is turned off: ${missing[*]} (USB sticks for old computers need xorriso)"
    return 0
  fi
  t0=$(now_ms)
  log "step added-packages: ${missing[*]}"
  set +e
  out=$(DEBIAN_FRONTEND=noninteractive "${apt[@]}" install -y --no-install-recommends "${missing[@]}" 2>&1)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    out=$({ DEBIAN_FRONTEND=noninteractive "${apt[@]}" update \
      && DEBIAN_FRONTEND=noninteractive "${apt[@]}" install -y --no-install-recommends "${missing[@]}"; } 2>&1)
    rc=$?
  fi
  set -e
  if [ -n "$out" ]; then printf '%s\n' "$out" | tail -n 20 | sed 's/^/    /'; fi
  if [ "$rc" -ne 0 ]; then
    # The core shows this detail on the Version card: our words, not apt's.
    add_step added-packages warn "$t0" "not installed: ${missing[*]} (apt-get failed, exit $rc, or another program was using apt; journalctl -u domovoi-update says why). The next update or Restart tries again. USB sticks for old computers need xorriso"
    log "step added-packages failed (exit $rc); not fatal, the run goes on"
  else
    add_step added-packages ok "$t0" "installed ${missing[*]}"
  fi
  return 0
}

# Where the dashboard's "Check the GPU and trainer again" leaves its flag
# (gpu_runtime_retry_flag in domovoi/wake_trainer_setup.py): gpu-runtime-retry
# in the core's wake_trainer dir, beside its wake_models dir (WAKE_MODELS_DIR
# in domovoi/.env, else ~/.domovoi/wake_models of the service user). Empty
# when that can't be told.
gpu_retry_flag() {
  local d home=""
  d=$(dotenv_get WAKE_MODELS_DIR)
  if [ -z "$d" ] || [[ $d == "~"* ]]; then
    home=$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6) || home=""
    [ -n "$home" ] || return 0
    if [ -z "$d" ]; then d=$home/.domovoi/wake_models; else d=$home${d#\~}; fi
  fi
  [[ $d == /* ]] || return 0
  d=${d%/}
  printf '%s/wake_trainer/gpu-runtime-retry' "${d%/*}"
}

# Docker's way to an NVIDIA GPU (the NVIDIA Container Toolkit), set up by
# scripts/linux/gpu-runtime.sh, the helper provision.sh runs too, where an
# NVIDIA GPU is visible: so a box installed before it existed gets it at
# its next update or Restart. The caller runs it while core and web are
# stopped and before domovoi-db's restart, because a Docker restart bounces
# Postgres and the rooms' players; GPU_DOCKER_RESTARTED says whether this
# run restarted Docker. The helper downloads nothing under never. It never
# fails the run and is never rolled back; a step is recorded only when the
# helper changed something (ok) or failed (warn, with the helper's own
# summary line as the detail, never a tool's raw output). No GPU, set up
# already, or a recent failure the helper does not retry yet, adds nothing
# to the result. The dashboard's retry flag (gpu_retry_flag) makes it try
# at once. Callers run it as `reconcile_gpu_runtime || true`, so nothing in
# it can end the run while the services are stopped.
reconcile_gpu_runtime() {
  local policy t0 out rc=0 summary flag=$UPDATE_DIR/gpu-runtime-restarted retry
  local -a force=()
  GPU_DOCKER_RESTARTED=0
  case "$(printf '%s' "$MANAGE_GPU_RUNTIME" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) log "Docker's GPU runtime left alone (DOMOVOI_MANAGE_GPU_RUNTIME=$MANAGE_GPU_RUNTIME)"; return 0 ;;
  esac
  if [ ! -f "$GPU_RUNTIME" ]; then
    log "no $GPU_RUNTIME: Docker's GPU runtime left as it is"
    return 0
  fi
  # First the question that needs nothing else: most boxes have no NVIDIA
  # GPU, and then nothing more happens (not even a look at the answer).
  if ! out=$(bash "$GPU_RUNTIME" --detect 2>&1); then
    log "$(printf '%s\n' "$out" | tail -n 1)"
    return 0
  fi
  if ! policy=$(internet_policy); then
    log "this checkout can't say the internet answer: Docker's GPU runtime left as it is"
    return 0
  fi
  # Root only asks whether the flag is there; the service user's own file
  # is removed as that user, once the helper ran.
  retry=$(gpu_retry_flag)
  if [ -n "$retry" ] && [ -e "$retry" ]; then
    force=(--force)
    log "the dashboard asked to check the GPU again ($retry): Docker's GPU runtime is tried now, whatever failed before"
  fi
  t0=$(now_ms)
  log "step gpu-runtime"
  rm -f "$flag"
  set +e
  out=$(bash "$GPU_RUNTIME" --internet "$policy" --restart-flag "$flag" ${force[@]+"${force[@]}"} 2>&1)
  rc=$?
  set -e
  if [ -n "$out" ]; then printf '%s\n' "$out" | sed 's/^/    /'; fi
  if [ -e "$flag" ]; then GPU_DOCKER_RESTARTED=1; fi
  rm -f "$flag"
  if [ "${#force[@]}" -gt 0 ] && ! as_user rm -f -- "$retry"; then
    log "could not remove $retry: the next update or Restart tries Docker's GPU runtime at once again"
  fi
  summary=$(printf '%s\n' "$out" | sed -n 's/^gpu-runtime: //p' | tail -n 1)
  if [ "$rc" -ne 0 ]; then
    add_step gpu-runtime warn "$t0" "$(trunc "Docker's GPU runtime: ${summary:-the helper stopped (exit $rc)}" 600)"
    log "step gpu-runtime failed (exit $rc); not fatal, the run goes on"
  else
    case $summary in
      "no NVIDIA GPU visible"*|"already in place"*|"not installed: "*|"not retried: "*) ;;
      *) add_step gpu-runtime ok "$t0" "$(trunc "${summary:-done}" 600)" ;;
    esac
  fi
  if [ "$GPU_DOCKER_RESTARTED" = 1 ]; then log "Docker was restarted: domovoi-db is restarted after it"; fi
  return 0
}

# Whether the trainer build goes to a transient systemd unit
# (DOMOVOI_UPDATE_TRAINER_DETACH: auto = when systemd runs this host).
trainer_detach() {
  case "$(printf '%s' "$TRAINER_DETACH" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) return 1 ;;
    1|true|yes|on) command -v systemd-run >/dev/null 2>&1 ;;
    *) [ -d /run/systemd/system ] && command -v systemd-run >/dev/null 2>&1 ;;
  esac
}

# The wake-word trainer image, rebuilt when the checkout's
# scripts/wake_word/docker is not what it was built from, the way the MPD
# image follows Dockerfile.mpd. Only where the image exists: it is built
# when a household switches training on (Settings, Wake Words), so a box
# that never did pays nothing. What it was built from is its
# org.domovoi.wake-trainer.src label, the git tree id of that directory
# (the core sets the same label when it builds the image); a different or
# missing one is a rebuild, as the service user (docker build of that
# directory, tagged and labelled as the core does). A registry's image
# (WAKE_TRAINER_IMAGE set to a published tag) is left alone: it is pulled,
# never built here. Under never nothing is built (the build downloads its
# base image and packages): a `warn` step says the image is out of date.
# Under systemd the build is handed to a transient unit
# (domovoi-wake-trainer-build) the update does not wait for, as the search
# helper's first start is; without it, it runs in the foreground, bounded
# by TRAINER_TIMEOUT. Never fatal; a step only when it built, started a
# build, or could not.
reconcile_wake_trainer() {
  local image tree label policy t0 out rc=0 home group tmp
  local -a build sd run
  case "$(printf '%s' "$MANAGE_WAKE_TRAINER" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) log "the wake-word trainer image left alone (DOMOVOI_MANAGE_WAKE_TRAINER=$MANAGE_WAKE_TRAINER)"; return 0 ;;
  esac
  image=${DOMOVOI_WAKE_TRAINER_IMAGE:-$(dotenv_get WAKE_TRAINER_IMAGE)}
  image=${image:-domovoi-wake-trainer:latest}
  if ! [[ $image =~ ^[a-z0-9][a-z0-9._/:@-]*$ ]]; then
    add_step wake-trainer warn "$(now_ms)" "$(trunc "not rebuilt: '$image' (WAKE_TRAINER_IMAGE) is not an image name" 300)"
    return 0
  fi
  # A name in a registry (its first part a host: a dot, a port or
  # localhost, the way docker reads a reference) is a published image the
  # household pulls (DOCKER_TRAINER.md): never rebuilt under its name here.
  if [[ $image == */* ]]; then
    case ${image%%/*} in
      *.*|*:*|localhost)
        log "$image is a registry's image: not rebuilt here (pull a newer one when scripts/wake_word/docker changes)"
        return 0 ;;
    esac
  fi
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    log "no $image image: wake-word training is not switched on here, nothing to rebuild"
    return 0
  fi
  # HEAD:<path> names the directory's tree (a peel such as ^{tree} would be
  # read as part of the path).
  if ! tree=$(git_as rev-parse -q --verify "HEAD:$TRAINER_DIR" 2>/dev/null) || [ -z "$tree" ] \
      || [ "$(git_as cat-file -t "$tree" 2>/dev/null)" != tree ]; then
    log "this checkout has no $TRAINER_DIR: $image left as it is"
    return 0
  fi
  label=$(docker image inspect -f "{{ index .Config.Labels \"$TRAINER_LABEL\" }}" "$image" 2>/dev/null) || label=""
  [ "$label" != "<no value>" ] || label=""
  if [ "$label" = "$tree" ]; then
    log "$image is built from this checkout's $TRAINER_DIR: nothing to rebuild"
    return 0
  fi
  t0=$(now_ms)
  log "step wake-trainer"
  if ! policy=$(internet_policy); then
    log "this checkout can't say the internet answer: $image not rebuilt"
    return 0
  fi
  if [ "$policy" = never ]; then
    add_step wake-trainer warn "$t0" "$image is out of date ($TRAINER_DIR changed) and was not rebuilt: the build downloads, and internet access is turned off; wake-word training may fail until it is rebuilt"
    return 0
  fi
  build=(docker build --label "$TRAINER_LABEL=$tree" -t "$image" "$REPO_DIR/$TRAINER_DIR")
  if trainer_detach; then
    sd=(systemd-run --no-block --collect --quiet --unit="$TRAINER_BUILD_UNIT")
    if [ -n "$SERVICE_USER" ] && [ "$SERVICE_USER" != root ]; then
      group=${RESULT_GROUP:-$(id -gn "$SERVICE_USER" 2>/dev/null || true)}
      home=$(getent passwd "$SERVICE_USER" 2>/dev/null | cut -d: -f6) || home=""
      sd+=(--uid="$SERVICE_USER")
      if [ -n "$group" ]; then sd+=(--gid="$group"); fi
      if [ -n "$home" ]; then sd+=(--setenv=HOME="$home"); fi
    fi
    sd+=(-p WorkingDirectory="$REPO_DIR")
    if "${sd[@]}" "${build[@]}"; then
      add_step wake-trainer ok "$t0" "rebuilding $image in the background ($TRAINER_BUILD_UNIT; journalctl -u $TRAINER_BUILD_UNIT): $TRAINER_DIR changed"
    elif systemctl is-active --quiet "$TRAINER_BUILD_UNIT" 2>/dev/null; then
      add_step wake-trainer ok "$t0" "$image is already rebuilding in the background ($TRAINER_BUILD_UNIT)"
    else
      add_step wake-trainer warn "$t0" "could not start the rebuild of $image ($TRAINER_BUILD_UNIT)"
    fi
    return 0
  fi
  run=()
  if command -v timeout >/dev/null 2>&1; then run=(timeout "$TRAINER_TIMEOUT"); fi
  if [ "$RUN_AS_ROOT" = 1 ] && [ "$SERVICE_USER" != root ]; then run+=(runuser -u "$SERVICE_USER" --); fi
  tmp=$(mktemp) || return 0
  set +e
  (cd "$REPO_DIR" && ${run[@]+"${run[@]}"} "${build[@]}") >"$tmp" 2>&1
  rc=$?
  set -e
  sed 's/^/    /' "$tmp"
  if [ "$rc" -eq 124 ]; then
    add_step wake-trainer warn "$t0" "the rebuild of $image did not finish within ${TRAINER_TIMEOUT}s (DOMOVOI_UPDATE_TRAINER_TIMEOUT); the next update or Restart tries again"
  elif [ "$rc" -ne 0 ]; then
    # docker build's output stays in the journal (printed above); the
    # dashboard's Version card shows this sentence.
    add_step wake-trainer warn "$t0" "the rebuild of $image failed (exit $rc): docker build's output is in the update's journal (journalctl -u domovoi-update); the next update or Restart tries again"
  else
    add_step wake-trainer ok "$t0" "$image rebuilt from this checkout's $TRAINER_DIR"
  fi
  rm -f "$tmp"
  return 0
}

# The drop-in reconcile_core_restart writes; "keep-sec" leaves RestartSec=
# out (the unit sets its own).
core_restart_dropin() {
  cat <<'EOF'
# Written by scripts/linux/apply-update.sh (domovoi-update.service). The
# core exits on purpose when it finds itself frozen (status 70, its loop
# watchdog: domovoi/loop_watchdog.py), and the dashboard ends a core that
# stopped answering (web/backend/core_restart.py). Either way systemd has
# to start it again, and this unit had no restart policy at all
# (docs/LINUX_HOST.md, The loop watchdog). To decide Restart= yourself,
# set it in a drop-in that sorts after this one, or delete this file and
# set it in the unit; a policy of your own is never replaced.
[Service]
Restart=on-failure
EOF
  [ "${1-}" = keep-sec ] || printf 'RestartSec=5\n'
}

# See the header: Restart=on-failure for a core unit with no restart policy
# (Restart=no), and only for that. Records a step only when it wrote the
# drop-in (ok), or when the core would still stay down after its watchdog's
# exit (warn: a policy of the household's own that doesn't cover it, a
# drop-in of theirs overriding ours, or the write failed).
reconcile_core_restart() {
  local t0 current sec want
  command -v systemctl >/dev/null 2>&1 || return 0
  current=$(systemctl show -p Restart --value "$CORE_UNIT" 2>/dev/null || true)
  case $current in
    always|on-failure) return 0 ;;
    "") log "systemd can't say $CORE_UNIT's Restart=: left as it is"; return 0 ;;
  esac
  t0=$(now_ms)
  log "step core-restart: $CORE_UNIT has Restart=$current"
  if [ "$current" != no ]; then
    add_step core-restart warn "$t0" "$CORE_UNIT has Restart=$current, a policy of its own, left as it is: it does not start the core again after its loop watchdog ends a frozen core (status 70); Restart=on-failure would"
    return 0
  fi
  if [ -f "$CORE_RESTART_DROPIN" ]; then
    add_step core-restart warn "$t0" "$CORE_UNIT has Restart=no although $CORE_RESTART_DROPIN sets on-failure (a drop-in after it, or the unit itself, overrides it), so a core that ends itself when frozen stays down"
    return 0
  fi
  # systemd's default RestartSec is 100ms: anything else is the unit's own.
  sec=$(systemctl show -p RestartUSec --value "$CORE_UNIT" 2>/dev/null || true)
  if [ -n "$sec" ] && [ "$sec" != 100ms ]; then
    want=$(core_restart_dropin keep-sec)$'\n'
  else
    want=$(core_restart_dropin)$'\n'
  fi
  if mkdir -p "$CORE_DROPIN_DIR" && write_atomic "$CORE_RESTART_DROPIN" "$want" \
      && systemctl daemon-reload; then
    add_step core-restart ok "$t0" "$CORE_UNIT had no restart policy (Restart=no); $CORE_RESTART_DROPIN sets on-failure, so a core that ends itself when frozen starts again"
  else
    add_step core-restart warn "$t0" "$CORE_UNIT has Restart=no and $CORE_RESTART_DROPIN could not be written, so a core that ends itself when frozen stays down"
    log "step core-restart failed; not fatal, the run goes on"
  fi
  return 0
}

# See the header. A step only when it installed the drop-in (ok) or could
# not (warn); one already in place is nothing to do, whatever journald
# made of it.
reconcile_journal() {
  local t0 want
  case "$(printf '%s' "$MANAGE_JOURNAL" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off) log "the journal left alone (DOMOVOI_MANAGE_JOURNAL=$MANAGE_JOURNAL)"; return 0 ;;
  esac
  if [ ! -f "$JOURNALD_SRC" ]; then
    log "no $JOURNALD_SRC: the journal left as it is"
    return 0
  fi
  want=$(cat "$JOURNALD_SRC"; printf x)
  want=${want%x}
  if [ -f "$JOURNALD_DROPIN" ] && [ "$(cat "$JOURNALD_DROPIN"; printf x)" = "${want}x" ]; then
    return 0
  fi
  t0=$(now_ms)
  log "step journal: keeping the journal on disk ($JOURNALD_DROPIN)"
  if ! { mkdir -p "$JOURNALD_DROPIN_DIR" && write_atomic "$JOURNALD_DROPIN" "$want"; }; then
    add_step journal warn "$t0" "$JOURNALD_DROPIN could not be written, so the journal may still be lost at a power cycle"
    log "step journal failed; not fatal, the run goes on"
    return 0
  fi
  if command -v systemctl >/dev/null 2>&1 && systemctl restart systemd-journald.service; then
    journalctl --flush >/dev/null 2>&1 || true
    add_step journal ok "$t0" "the journal is kept on disk now ($JOURNALD_DROPIN), so what the core logs survives a power cycle"
  else
    add_step journal warn "$t0" "$JOURNALD_DROPIN is in place but systemd-journald did not restart: the journal moves to disk at the next boot"
    log "step journal: systemd-journald did not restart; not fatal, the run goes on"
  fi
  return 0
}

migrations_grew() {
  [[ $MIGRATIONS_BEFORE =~ ^[0-9]+$ ]] && [[ $MIGRATIONS_AFTER =~ ^[0-9]+$ ]] \
    && [ "$MIGRATIONS_AFTER" -gt "$MIGRATIONS_BEFORE" ]
}

# The tracked files git status skips, one "<tag> <path>" line each: git
# ls-files -v tags a skip-worktree file S and an assume-unchanged one with
# a lower-case letter. Nothing when there are none; status 1 when git
# can't say.
hidden_from_status() {
  local out
  out=$(git_as ls-files -v) || return 1
  printf '%s\n' "$out" | grep -E '^([a-z]|S) ' || true
}

# ─── The verified copy (scripts/linux/update-verifier.sh) ────────────────

# Started as root without the verifier (domovoi-update.service from before
# it, whose ExecStart ran the checkout's copy of this script, or a run by
# hand): hand over to it, so this run too goes on from the verified copy of
# HEAD, and nothing below runs from the checkout. The installed verifier
# when there is one; else, once, the one beside this script, which is
# exactly as trusted as this script itself (it is the one already running).
relaunch_verified() {
  local v=$VERIFIER_INSTALLED
  if [ ! -f "$v" ] || [ -L "$v" ]; then v=$SCRIPT_DIR/update-verifier.sh; fi
  if [ ! -f "$v" ]; then
    log "no update verifier ($VERIFIER_INSTALLED, or $SCRIPT_DIR/update-verifier.sh): nothing can be run as root"
    return 1
  fi
  log "started without the update verifier: handing over to $v, which runs the verified copy of HEAD"
  exec env DOMOVOI_REPO_DIR="$REPO_DIR" bash "$v" relaunch
}

# The unit as install-update-unit.sh wrote it before the verifier, for this
# checkout: moved to the new one as it is; any other unit without the
# verifier is moved too, but kept beside it first.
legacy_unit_text() {
  printf '%s\n' '[Unit]' \
    'Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)' \
    'After=docker.service network-online.target' \
    'Wants=network-online.target' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    'EnvironmentFile=-/etc/default/domovoi-update' \
    "ExecStart=/bin/bash $REPO_DIR/scripts/linux/apply-update.sh" \
    'TimeoutStartSec=30min'
}

# Can no one but root write directory $1 and the one above it? (The
# verifier's own file goes there; the rest of the path is the system's.)
root_dir() {
  local p st uids="0"
  if [ -n "$TEST_ROOT" ]; then uids="0 $EUID"; fi
  for p in "$1" "$(dirname "$1")"; do
    [ -d "$p" ] && [ ! -L "$p" ] || return 1
    st=$(stat -c '%u %a' -- "$p" 2>/dev/null) || return 1
    [[ $st =~ ^([0-9]+)\ ([0-7]{3,4})$ ]] || return 1
    [[ " $uids " == *" ${BASH_REMATCH[1]} "* ]] || return 1
    (( 8#${BASH_REMATCH[2]} & 8#022 )) && return 1
  done
  return 0
}

# Keep domovoi-update.service on the verifier: its file the verified copy's
# (scripts/linux/update-verifier.sh), and a unit from before the verifier
# (exactly legacy_unit_text) replaced by the one install-update-unit.sh
# writes now, then daemon-reload; the next run starts with ExecStartPre.
# Only from a verified copy, as root, under the unit (no unit file: a run
# by hand, nothing to keep). A unit someone changed by hand is replaced
# too, after a copy of it is kept as <unit>.bak-<time> (a warn step says
# where), since otherwise every run of it starts with the checkout's own
# copy of this script as root. Never fatal.
reconcile_update_unit() {
  local t0 src=$SCRIPT_DIR/update-verifier.sh dir have want tmp did=() why="" note="" kept
  [ "$RUN_AS_ROOT" = 1 ] && [ -n "$VERIFIED_DIR" ] || return 0
  [ -f "$UPDATE_UNIT_FILE" ] && [ ! -L "$UPDATE_UNIT_FILE" ] && [ -s "$UPDATE_UNIT_FILE" ] || return 0
  [ -f "$src" ] || return 0
  t0=$(now_ms)
  dir=$(dirname "$VERIFIER_INSTALLED")
  if ! cmp -s "$src" "$VERIFIER_INSTALLED"; then
    if [ ! -d "$dir" ]; then mkdir -p "$dir" && chmod 0755 "$dir" || true; fi
    if ! root_dir "$dir"; then
      why="$dir is not a directory only root can write, so the verifier was not put there"
    elif tmp=$(mktemp "$dir/.verify-update.sh.XXXXXX") && cp "$src" "$tmp" && chmod 0755 "$tmp" \
        && mv -f "$tmp" "$VERIFIER_INSTALLED"; then
      did+=("the verifier $VERIFIER_INSTALLED is this commit's")
    else
      rm -f "${tmp:-}"
      why="$VERIFIER_INSTALLED could not be written"
    fi
  fi
  have=$(cat "$UPDATE_UNIT_FILE" 2>/dev/null) || have=""
  if [[ $have != *"verify-update.sh prepare"* ]]; then
    want=$(bash "$SCRIPT_DIR/install-update-unit.sh" --print-unit --repo "$REPO_DIR" 2>/dev/null) || want=""
    if [ -z "$why" ] && [ -n "$want" ] && [ -f "$VERIFIER_INSTALLED" ]; then
      # Not the unit the installer wrote (changed by hand): replaced all the
      # same, since every run of it would start with the checkout's own
      # copy of this script as root, and kept beside it, under a name
      # systemd ignores, for its changes to be put back as a drop-in.
      if [ "$have" != "$(legacy_unit_text)" ]; then
        kept=$UPDATE_UNIT_FILE.bak-$(date -u +%Y%m%dT%H%M%SZ)
        if cp -p "$UPDATE_UNIT_FILE" "$kept"; then
          note="it was not the unit install-update-unit.sh wrote, so it is kept as $kept: put any change of yours back in a drop-in (/etc/systemd/system/domovoi-update.service.d/)"
        else
          why="${why:+$why; }$UPDATE_UNIT_FILE was changed by hand and no copy of it could be kept, so it was left as it is: root runs this script from the checkout first, at every update, until install-update-unit.sh replaces it"
        fi
      fi
      if [ -z "$why" ]; then
        if write_atomic "$UPDATE_UNIT_FILE" "$want"$'\n' && systemctl daemon-reload; then
          did+=("domovoi-update.service runs it before every update (ExecStartPre), and root runs only the verified copy of each commit")
        else
          why="${why:+$why; }$UPDATE_UNIT_FILE could not be replaced"
        fi
      fi
    elif [ -z "$why" ]; then
      why="$UPDATE_UNIT_FILE could not be moved to the verifier (install-update-unit.sh --print-unit gave nothing)"
    fi
  fi
  if [ -n "$why" ]; then
    add_step update-unit warn "$t0" "$(trunc "$why" 600)"
    log "step update-unit: $why"
  elif [ -n "$note" ]; then
    add_step update-unit warn "$t0" "$(trunc "$(IFS=';'; printf '%s' "${did[*]}" | sed 's/;/; /g'); $note" 600)"
    log "step update-unit: $note"
  elif [ "${#did[@]}" -gt 0 ]; then
    add_step update-unit ok "$t0" "$(IFS=';'; printf '%s' "${did[*]}" | sed 's/;/; /g')"
    log "step update-unit: $(IFS=';'; printf '%s' "${did[*]}")"
  fi
  return 0
}

# ─── The two paths ───────────────────────────────────────────────────────

plain_restart() {
  local failed=""
  MODE=restart
  write_result running
  log "nothing new since ${PREV_SHA:0:12} ($PREV_SOURCE): plain restart"
  if ! repair_env_secrets; then
    finish aborted "domovoi/.env lacks LETTA_TOKEN or SEARXNG_SECRET and could not be given them, so nothing was restarted (docker compose refuses to run without them; docs/LINUX_HOST.md, Helper-container secrets): $LAST_ERROR"
    return
  fi
  MIGRATIONS_BEFORE=$(migration_count || true)
  DB_REASON=$(plain_db_reason)
  SERVICES_STOPPED=1
  run_step stop-services stop_services || failed=stop-services
  # While core and web are stopped: a Docker restart bounces Postgres, and
  # domovoi-db is then restarted before they start again.
  if [ -z "$failed" ]; then reconcile_gpu_runtime || true; fi
  if [ "$GPU_DOCKER_RESTARTED" = 1 ] && [ -z "$DB_REASON" ]; then
    DB_REASON="Docker was restarted for its GPU runtime"
  fi
  if [ -n "$DB_REASON" ]; then
    log "restarting domovoi-db: $DB_REASON"
    run_step migrate plain_restart_db || failed=${failed:-migrate}
  else
    log "Postgres answers with every migration in the checkout applied: domovoi-db left alone"
  fi
  run_step start-services start_services || failed=${failed:-start-services}
  SERVICES_STOPPED=0
  run_step health wait_healthy || failed=${failed:-health}
  # Only Flyway writes its history, so it moved only if domovoi-db ran.
  if [ -n "$DB_REASON" ]; then
    MIGRATIONS_AFTER=$(migration_count || true)
  else
    MIGRATIONS_AFTER=$MIGRATIONS_BEFORE
  fi
  if [ -z "$failed" ]; then
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
    APPLIED_OK=1
    run_soft_step searxng reconcile_searxng
    reconcile_os_jobs
    reconcile_added_packages
    reconcile_wake_trainer || true
    finish ok
  else
    finish failed "$LAST_ERROR"
  fi
}

full_update() {
  local dirty failed="" t0 why
  MODE=update
  log "updating ${PREV_SHA:0:12} ($PREV_SOURCE) -> ${HEAD_SHA:0:12}"

  t0=$(now_ms)
  if ! dirty=$(git_as status --porcelain --untracked-files=no); then
    add_step preflight failed "$t0" "git status failed"
    finish failed "git status failed in $REPO_DIR"
    return
  fi
  if [ -n "$dirty" ]; then
    add_step preflight refused "$t0" "$(printf '%s\n' "$dirty" | head -n 10)"
    finish refused "tracked files have uncommitted changes, so a rollback could not restore this tree; commit or stash them, then restart again: $(printf '%s\n' "$dirty" | head -n 5 | tr '\n' ' ')"
    return
  fi
  # git status doesn't look at a file flagged skip-worktree or
  # assume-unchanged (git update-index), so an edit there is invisible to
  # the check above (delta security review 2026-10-16, S4). Root runs none
  # of it any more (the verified copy), but a rollback could not restore it
  # either.
  if ! dirty=$(hidden_from_status); then
    add_step preflight failed "$t0" "git ls-files -v failed"
    finish failed "git ls-files -v failed in $REPO_DIR"
    return
  fi
  if [ -n "$dirty" ]; then
    add_step preflight refused "$t0" "$(printf '%s\n' "$dirty" | head -n 10)"
    finish refused "tracked files are hidden from git status (skip-worktree or assume-unchanged), so local changes to them can't be seen and a rollback could not restore this tree; as $SERVICE_USER, clear the flags (git update-index --no-skip-worktree --no-assume-unchanged FILE), commit or stash any changes, then restart again: $(printf '%s\n' "$dirty" | head -n 5 | tr '\n' ' ')"
    return
  fi
  if deps_changed; then DEPS_CHANGED=1; fi
  if paths_changed "${MPD_PATHS[@]}"; then MPD_CHANGED=1; fi
  if paths_changed domovoi/Dockerfile.mpd; then MPD_IMAGE_CHANGED=1; fi
  if images_changed; then IMAGES_CHANGED=1; fi
  if [ "$DEPS_CHANGED" = 1 ] || [ "$MPD_CHANGED" = 1 ] || [ "$IMAGES_CHANGED" = 1 ]; then
    # Read once, before anything is touched: what this run may download
    # depends on it (the rollback's re-sync and rebuild use it too).
    POLICY_AT_START=$(internet_policy 2>/dev/null || true)
  fi
  if [ "$POLICY_AT_START" = never ] && { [ "$DEPS_CHANGED" = 1 ] || [ "$MPD_IMAGE_CHANGED" = 1 ] || [ "$IMAGES_CHANGED" = 1 ]; }; then
    local -a what=()
    if [ "$DEPS_CHANGED" = 1 ]; then what+=("the Python dependencies (pip would download them)"); fi
    if [ "$MPD_IMAGE_CHANGED" = 1 ]; then what+=("the music player image (docker build downloads it)"); fi
    if [ "$IMAGES_CHANGED" = 1 ]; then what+=("the container images (docker compose would pull them)"); fi
    why="this update changes ${what[0]}"
    if [ "${#what[@]}" -eq 2 ]; then why="$why and ${what[1]}"; fi
    if [ "${#what[@]}" -eq 3 ]; then why="$why, ${what[1]} and ${what[2]}"; fi
    add_step preflight refused "$t0" "$why; internet access is turned off for this box"
    finish aborted "$why, and internet access is turned off for this box (Settings > Internet), so nothing was changed. Switch the answer to Sometimes, restart again, then switch it back to No and restart once more, so Ubuntu's own updaters are masked again (docs/INTERNET.md)."
    return
  fi
  if [ "$DEPS_CHANGED" = 1 ] && ! why=$(venv_writable); then
    add_step preflight refused "$t0" "$why"
    finish aborted "the dependencies changed but the venv can't be re-synced, so nothing was changed: $why"
    return
  fi
  add_step preflight ok "$t0" "deps_changed=$DEPS_CHANGED mpd_changed=$MPD_CHANGED"
  write_result running
  # Before the backup and the stop: domovoi-db's compose up (step 7) and the
  # search helper refuse to run without these.
  if ! repair_env_secrets; then
    finish aborted "domovoi/.env lacks LETTA_TOKEN or SEARXNG_SECRET and could not be given them, so nothing was changed (docker compose refuses to run without them; docs/LINUX_HOST.md, Helper-container secrets): $LAST_ERROR"
    return
  fi
  resolve_test_db

  if ! run_step backup backup_db; then
    if [ "$REQUIRE_BACKUP" = 1 ]; then
      finish aborted "the pre-update backup failed, so nothing was changed: $LAST_ERROR"
      return
    fi
    log "continuing without a backup (DOMOVOI_UPDATE_REQUIRE_BACKUP=$REQUIRE_BACKUP)"
  fi
  MIGRATIONS_BEFORE=$(migration_count || true)
  LEDGERS_BEFORE=$(ledger_snapshot "$PG_DB" || true)
  if [ -n "$TEST_DB" ]; then TEST_LEDGERS_BEFORE=$(ledger_snapshot "$TEST_DB" || true); fi
  PLUGINS_BEFORE=$(plugin_states || true)
  if [ "$DEPS_CHANGED" = 1 ]; then
    run_step snapshot-venv snapshot_venv || rm -f "$FREEZE_FILE"
  fi

  SERVICES_STOPPED=1
  run_step stop-services stop_services || failed=stop-services
  if [ -z "$failed" ] && [ "$DEPS_CHANGED" = 1 ]; then
    run_step sync-deps sync_deps || failed=sync-deps
    if [ -z "$failed" ]; then drop_retired_packages; fi
  fi
  if [ -z "$failed" ] && [ "$MPD_CHANGED" = 1 ]; then
    run_step rebuild-mpd rebuild_mpd || failed=rebuild-mpd
  fi
  # While core and web are stopped, before domovoi-db's restart (which
  # brings Postgres back after a Docker restart in any case).
  if [ -z "$failed" ]; then reconcile_gpu_runtime || true; fi
  if [ -z "$failed" ]; then run_step migrate restart_db || failed=migrate; fi
  if [ -z "$failed" ]; then run_step start-services start_services || failed=start-services; fi
  if [ -z "$failed" ]; then
    SERVICES_STOPPED=0
    run_step health wait_healthy || failed=health
  fi
  if [ -z "$failed" ]; then run_step plugins check_plugins || failed=plugins; fi
  MIGRATIONS_AFTER=$(migration_count || true)
  LEDGERS_AFTER=$(ledger_snapshot "$PG_DB" || true)

  if [ -z "$failed" ]; then
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
    rm -f "$BAD_FILE"
    BAD_SHA=""
    APPLIED_OK=1
    run_soft_step searxng reconcile_searxng
    reconcile_os_jobs
    reconcile_added_packages
    reconcile_wake_trainer || true
    finish ok
    return
  fi
  rollback "$failed" "$LAST_ERROR"
}

# rb_failed STEP: remember the first rollback step that failed, and its
# error, before a later step's error replaces LAST_ERROR. Sets rollback()'s
# locals (bash scoping is dynamic).
rb_failed() {
  if [ -z "$rb" ]; then rb=$1; rb_err=$LAST_ERROR; fi
}

rollback() {
  local failed_step=$1 cause=$2 rb="" rb_err="" now off
  local -a offs=()
  log "update failed at $failed_step; rolling back to ${PREV_SHA:0:12}"
  SERVICES_STOPPED=1
  run_step rollback-stop stop_services || true
  if run_step rollback-checkout git_as reset --keep "$PREV_SHA"; then
    if [ "$DEPS_CHANGED" = 1 ]; then
      run_step rollback-deps sync_deps || rb_failed rollback-deps
      # Best effort: a pin that can't be restored leaves a newer but
      # compatible version, which is what sync_deps alone would give.
      run_step rollback-pins restore_pins || true
    fi
    if [ "$MPD_CHANGED" = 1 ]; then
      run_step rollback-mpd rebuild_mpd || rb_failed rollback-mpd
    fi
    # With the core stopped, so these are final: Flyway's count, and every
    # plugin's ledger (the new code's boot may have applied its plugin
    # migrations even though it then failed its health check).
    MIGRATIONS_AFTER=$(migration_count || true)
    LEDGERS_AFTER=$(ledger_snapshot "$PG_DB" || true)
    if migrations_grew || ledgers_grew "$LEDGERS_BEFORE" "$LEDGERS_AFTER"; then
      run_step restore-db restore_db || rb_failed restore-db
    fi
    # The plugin runtime applied the same migrations to the test twin, after
    # the database itself; that one can have grown on its own.
    if [ -n "$TEST_DB" ]; then
      TEST_LEDGERS_AFTER=$(ledger_snapshot "$TEST_DB" || true)
      if ledgers_grew "$TEST_LEDGERS_BEFORE" "$TEST_LEDGERS_AFTER"; then
        run_step restore-test-db restore_test_db || rb_failed restore-test-db
      fi
    fi
    # Plugins the failed boot switched off (a restored database has them on
    # already, and then there are none).
    if now=$(plugin_states); then
      # A plain assignment under set -e: a failing awk here must cost the
      # re-enable, not the rest of the rollback.
      off=$(plugins_broken "$PLUGINS_BEFORE" "$now" off) || off=""
      if [ -n "$off" ]; then
        # One slug per line, one argument each, never globbed.
        mapfile -t offs <<<"$off"
        run_step reenable-plugins reenable_plugins "${offs[@]}" || rb_failed reenable-plugins
      fi
    fi
  else
    # The tree could not go back, so neither may the database: old data
    # under new code is worse than new data under new code.
    rb_failed rollback-checkout
  fi
  run_step rollback-migrate restart_db || rb_failed rollback-migrate
  run_step rollback-start start_services || rb_failed rollback-start
  SERVICES_STOPPED=0
  if run_step rollback-health wait_healthy; then
    # Back on the previous SHA, whatever loaded before the update loads again.
    run_step rollback-plugins check_plugins || rb_failed rollback-plugins
  else
    rb_failed rollback-health
  fi

  BAD_SHA=$HEAD_SHA
  write_atomic "$BAD_FILE" "$HEAD_SHA"$'\n'
  if [ -z "$rb" ]; then
    # Healthy again on the previous SHA: that is the known-good baseline now.
    write_atomic "$APPLIED_FILE" "$PREV_SHA"$'\n'
    finish rolled_back "update to ${HEAD_SHA:0:12} failed at $failed_step and was rolled back to ${PREV_SHA:0:12}: $cause"
  else
    finish rollback_failed "update to ${HEAD_SHA:0:12} failed at $failed_step ($cause); the rollback then failed at $rb: $rb_err"
  fi
}

main() {
  umask 022
  STARTED_AT=$(now_iso)
  START_MS=$(now_ms)
  # As root, only ever the verified copy: anything else hands over to the
  # verifier here, before this script has touched a thing.
  if [ -z "$VERIFIED_SHA" ] && [ "$(id -u)" = 0 ]; then
    if ! relaunch_verified; then
      install -d -m 0755 "$UPDATE_DIR"
      RESULT_READY=1
      finish failed "the update verifier is missing, so nothing was run as root (reinstall the update unit: sudo bash scripts/linux/install-update-unit.sh)"
      exit 1
    fi
  fi
  if [ -n "$VERIFIED_DIR" ] && [ "$(cd "$VERIFIED_DIR/scripts/linux" 2>/dev/null && pwd -P)" != "$(cd "$SCRIPT_DIR" && pwd -P)" ]; then
    log "DOMOVOI_VERIFIED_DIR is $VERIFIED_DIR, but this script runs from $SCRIPT_DIR: not the verified copy; nothing was done"
    exit 1
  fi
  install -d -m 0755 "$UPDATE_DIR"
  RESULT_READY=1
  trap on_exit EXIT

  if command -v flock >/dev/null 2>&1; then
    exec 9>"$UPDATE_DIR/.lock"
    if ! flock -n 9; then
      log "another update is already running"
      FINAL_WRITTEN=1
      exit 1
    fi
  fi

  if ! resolve_service_user; then
    finish failed "$LAST_ERROR"
    exit 1
  fi
  # The unit starts in /, and the service user's commands (python -m pip
  # puts the cwd on sys.path) should run somewhere they can read.
  cd "$REPO_DIR"
  # The venv first: dotenv_get reads domovoi/.env through it.
  resolve_venv
  resolve_core_state_dir
  resolve_mpd_names

  if ! HEAD_SHA=$(git_as rev-parse --verify HEAD) || ! is_sha "$HEAD_SHA"; then
    HEAD_SHA=""
    finish failed "cannot read HEAD in $REPO_DIR"
    exit 1
  fi
  resolve_prev
  BAD_SHA=$(read_sha_file "$BAD_FILE" || true)

  # The commit the verifier copied is the one this run applies: a HEAD that
  # moved since (a pull in between) would put new code under the old copy's
  # checks. Restarting again verifies the new HEAD.
  if [ -n "$VERIFIED_SHA" ] && [ "$HEAD_SHA" != "$VERIFIED_SHA" ]; then
    add_step verify refused "$(now_ms)" "HEAD is ${HEAD_SHA:0:12}, the verified copy is of ${VERIFIED_SHA:0:12}"
    finish refused "the checkout moved to ${HEAD_SHA:0:12} after the update verifier copied ${VERIFIED_SHA:0:12}, so nothing was changed; restart again to apply what is checked out now"
    exit 1
  fi

  # Before either path, touching nothing: the pinned upstream, then HEAD's
  # signature ("Signed updates" above).
  if ! check_upstream; then exit 1; fi
  if ! check_signature; then exit 1; fi
  reconcile_update_unit
  reconcile_core_restart
  reconcile_journal

  if [ "$PREV_SHA" = "$HEAD_SHA" ]; then
    plain_restart
  else
    full_update
  fi
  if [ "$FINAL_STATUS" = ok ]; then exit 0; fi
  exit 1
}

# main is one function, so bash has parsed the whole script before the
# rollback's `git reset` can rewrite this file underneath it.
main "$@"; exit $?
