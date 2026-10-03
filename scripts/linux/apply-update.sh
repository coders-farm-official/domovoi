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
# Nothing new since the previous SHA: a plain restart. Stop web and core,
# restart domovoi-db (compose up, a cheap no-op Flyway run), start core and
# web, health-check them.
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
#      requirements lock changed.
#   5. Rebuild the MPD image the way mpd_provisioner.py does if
#      Dockerfile.mpd or mpd.conf changed, and remove the room containers so
#      the core recreates them (same data volumes) from the new image.
#   6. systemctl restart domovoi-db: compose up plus Flyway.
#   7. Start core and web; both must answer their health endpoint within
#      DOMOVOI_UPDATE_HEALTH_TIMEOUT seconds.
#   8. Every plugin that loaded before the update still loads: none that was
#      enabled and not at load_error in the plugins registry is at load_error
#      now. The core keeps a failing plugin from taking it down, so its
#      health endpoint stays green through one.
#   9. The search helper (SearXNG) follows the internet answer: started for
#      INTERNET_ACCESS=always|sometimes, stopped for never, left alone while
#      unanswered (reconcile_searxng). Never fatal: a failure is a `warn`
#      step and the run stays ok. A healthy plain restart ends with it too.
#   On any failure in 3-8: stop both again, `git reset --keep` back to the
#   previous SHA, undo the dependency and MPD changes, restore the dump if
#   flyway_schema_history or any plugin's plugin_<slug>.schema_history grew
#   (the core applies plugin migrations at boot, so step 7 can run them),
#   restore the <db>_test dump if a plugin ledger there grew, switch back
#   on every plugin the failed boot's load errors switched off, restart,
#   check health and plugins again, and record the new SHA as bad_sha so the
#   version panel stops offering it.
#
# Every run ends by writing last-result.json into DOMOVOI_UPDATE_DIR, which
# the core serves as `last_update` from GET /v1/admin/version.
#
# Privilege: this runs as root, but everything that reads or writes the
# checkout or the venv (git, pip) runs as the service user through runuser.
# Root never executes git hooks or build steps from a tree the service user
# can edit. Root's own state lives in DOMOVOI_UPDATE_DIR, a root-owned dir the
# service user can read but not write.
#
# Settings come from the environment; domovoi-update.service loads
# /etc/default/domovoi-update. Every one has a default that matches
# docs/LINUX_HOST.md.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

REPO_DIR=${DOMOVOI_REPO_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}
# Without DOMOVOI_VENV, resolve_venv may move this to the core unit's venv.
VENV_DIR=${DOMOVOI_VENV:-$REPO_DIR/.venv}
# `-` rather than `:-`: an explicitly empty value is honoured (no extras; no
# CPU-torch step).
PIP_EXTRAS=${DOMOVOI_PIP_EXTRAS-dev,real-clients,voice-profile}
TORCH_INDEX_URL=${DOMOVOI_TORCH_INDEX_URL-https://download.pytorch.org/whl/cpu}
UPDATE_DIR=${DOMOVOI_UPDATE_DIR:-/var/lib/domovoi-update}
BACKUP_DIR=${DOMOVOI_UPDATE_BACKUP_DIR:-$UPDATE_DIR/backups}
KEEP_BACKUPS=${DOMOVOI_UPDATE_KEEP_BACKUPS:-5}
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

# The search helper (docs/INTERNET.md): the compose service and the
# container it runs as. DOMOVOI_MANAGE_SEARXNG=0 (or false/no/off) in
# /etc/default/domovoi-update leaves the container alone, for a host that
# runs SearXNG some other way.
SEARXNG_SERVICE=searxng
SEARXNG_CONTAINER=domovoi-searxng
MANAGE_SEARXNG=${DOMOVOI_MANAGE_SEARXNG:-1}

RESULT_FILE=$UPDATE_DIR/last-result.json
APPLIED_FILE=$UPDATE_DIR/applied_sha
BAD_FILE=$UPDATE_DIR/bad_sha
FREEZE_FILE=$UPDATE_DIR/pip-freeze-pre.txt

# What counts as "the dependencies changed" / "the MPD image changed".
# Git pathspecs, relative to the repo root.
DEPS_PATHS=(pyproject.toml 'requirements*.lock' 'plugins/*/requirements*.lock')
MPD_PATHS=(domovoi/Dockerfile.mpd domovoi/mpd.conf)

# Run state, filled in as the run goes.
SERVICE_USER=""
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
MIGRATIONS_BEFORE=""
MIGRATIONS_AFTER=""
LEDGERS_BEFORE=""
LEDGERS_AFTER=""
PLUGINS_BEFORE=""
TEST_LEDGERS_BEFORE=""
TEST_LEDGERS_AFTER=""
BACKUP_FILE=""
TEST_BACKUP_FILE=""
DB_RESTORED=0
TEST_DB_RESTORED=0
STEPS=()
# A step function may leave a note here for its step's detail on success.
STEP_DETAIL=""
LAST_ERROR=""
RESULT_READY=0
FINAL_WRITTEN=0
FINAL_STATUS=""
SERVICES_STOPPED=0

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
# symlink planted at the target is replaced rather than followed.
write_atomic() {
  local target=$1 content=$2 tmp
  tmp=$(mktemp "$(dirname "$target")/.$(basename "$target").XXXXXX")
  printf '%s' "$content" >"$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$target"
}

write_result() {
  local status=$1 err=${2-} steps=""
  [ "$RESULT_READY" = 1 ] || return 0
  if [ "${#STEPS[@]}" -gt 0 ]; then
    steps=$(IFS=,; printf '%s' "${STEPS[*]}")
  fi
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
  )"$'\n'
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
  set +e
  "$@" >"$out" 2>&1
  rc=$?
  set -e
  sed 's/^/    /' "$out"
  if [ "$rc" -eq 0 ]; then
    add_step "$name" ok "$t0" "$STEP_DETAIL"
    if [ -n "$STEP_DETAIL" ]; then log "step $name: $STEP_DETAIL"; fi
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
    # Something this script did not expect. Don't leave the house without
    # its services: start whatever this run stopped, then say what happened.
    if [ "$SERVICES_STOPPED" = 1 ]; then
      systemctl start "$CORE_UNIT" "$WEB_UNIT" || true
    fi
    write_result failed "${LAST_ERROR:-update script exited unexpectedly (exit $rc)}" || true
  fi
}

# ─── Who owns the checkout, and where things live ────────────────────────

# The last KEY=value for KEY in domovoi/.env, quotes stripped, or empty.
dotenv_get() {
  local f=$REPO_DIR/domovoi/.env v
  [ -r "$f" ] || return 0
  v=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$f" | tail -n 1 | tr -d '\r')
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

start_services() { systemctl start "$CORE_UNIT" "$WEB_UNIT"; }

# domovoi-db is a oneshot: compose up -d postgres, then compose run flyway.
restart_db() { systemctl restart "$DB_UNIT"; }

pg_exec() { docker exec "$PG_CONTAINER" "$@"; }

psql_admin() { pg_exec psql -v ON_ERROR_STOP=1 -U "$PG_USER" -d postgres -tAc "$1"; }

migration_count() {
  local n
  n=$(pg_exec psql -U "$PG_USER" -d "$PG_DB" -tAc 'SELECT count(*) FROM flyway_schema_history' 2>/dev/null | tr -d '[:space:]') || return 1
  [[ $n =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$n"
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

# Restore dump $2 into a fresh database, then swap it in for database $1 by
# rename. `pg_restore --clean` into the live database would leave behind
# every object a failed migration CREATED (it only drops what the dump
# contains), and the re-run of that migration after a fix would then fail on
# "already exists". The replaced database is kept as <db>_failed_<ts> for
# inspection; drop it by hand once it's no longer interesting.
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

# The venv the way docs/LINUX_HOST.md builds it: CPU torch first when the
# extras pull torch in (so pip never fetches the CUDA build), then the
# editable install with the extras. resemblyzer is not re-run: pyproject
# doesn't declare it, so no pyproject change can require it.
sync_deps() {
  local target=.
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "no venv interpreter at $VENV_DIR/bin/python (set DOMOVOI_VENV)"; return 1
  fi
  if [ -n "$TORCH_INDEX_URL" ] && [[ ",$PIP_EXTRAS," == *,voice-profile,* ]]; then
    (cd "$REPO_DIR" && pip_as install torch --index-url "$TORCH_INDEX_URL") || return 1
  fi
  if [ -n "$PIP_EXTRAS" ]; then target=".[$PIP_EXTRAS]"; fi
  (cd "$REPO_DIR" && pip_as install -e "$target")
}

snapshot_venv() {
  pip_as freeze --exclude-editable >"$FREEZE_FILE.tmp" && chmod 0644 "$FREEZE_FILE.tmp" \
    && mv -f "$FREEZE_FILE.tmp" "$FREEZE_FILE"
}

# After the rolled-back sync_deps: put every package back at the exact
# version it had before the update. A plain install from the old pyproject
# keeps any newer version that still satisfies it; the snapshot doesn't.
restore_pins() {
  local pins=$UPDATE_DIR/pip-restore.txt extra=()
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
  docker build -t "$MPD_TAG" -f "$REPO_DIR/domovoi/Dockerfile.mpd" "$REPO_DIR/domovoi" || return 1
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

# Start or stop the search helper (SearXNG) to match the internet answer:
# Yes or Sometimes start it (the first start pulls the pinned image), No
# stops it if it runs, unanswered leaves it as it is. Run as a soft step.
reconcile_searxng() {
  local policy compose_file=$REPO_DIR/domovoi/docker-compose.yml
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
      docker compose -f "$compose_file" --project-directory "$REPO_DIR/domovoi" \
        up -d --no-deps "$SEARXNG_SERVICE" || return 1
      STEP_DETAIL="$SEARXNG_CONTAINER up (internet answer: $policy)" ;;
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

migrations_grew() {
  [[ $MIGRATIONS_BEFORE =~ ^[0-9]+$ ]] && [[ $MIGRATIONS_AFTER =~ ^[0-9]+$ ]] \
    && [ "$MIGRATIONS_AFTER" -gt "$MIGRATIONS_BEFORE" ]
}

# ─── The two paths ───────────────────────────────────────────────────────

plain_restart() {
  local failed=""
  MODE=restart
  write_result running
  log "nothing new since ${PREV_SHA:0:12} ($PREV_SOURCE): plain restart"
  SERVICES_STOPPED=1
  run_step stop-services stop_services || failed=stop-services
  run_step migrate restart_db || failed=${failed:-migrate}
  run_step start-services start_services || failed=${failed:-start-services}
  SERVICES_STOPPED=0
  run_step health wait_healthy || failed=${failed:-health}
  if [ -z "$failed" ]; then
    run_soft_step searxng reconcile_searxng
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
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
  if paths_changed "${DEPS_PATHS[@]}"; then DEPS_CHANGED=1; fi
  if paths_changed "${MPD_PATHS[@]}"; then MPD_CHANGED=1; fi
  if [ "$DEPS_CHANGED" = 1 ] && ! why=$(venv_writable); then
    add_step preflight refused "$t0" "$why"
    finish aborted "the dependencies changed but the venv can't be re-synced, so nothing was changed: $why"
    return
  fi
  add_step preflight ok "$t0" "deps_changed=$DEPS_CHANGED mpd_changed=$MPD_CHANGED"
  write_result running
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
  fi
  if [ -z "$failed" ] && [ "$MPD_CHANGED" = 1 ]; then
    run_step rebuild-mpd rebuild_mpd || failed=rebuild-mpd
  fi
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
    run_soft_step searxng reconcile_searxng
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
    rm -f "$BAD_FILE"
    BAD_SHA=""
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
  resolve_core_state_dir
  resolve_venv
  resolve_mpd_names

  if ! HEAD_SHA=$(git_as rev-parse --verify HEAD) || ! is_sha "$HEAD_SHA"; then
    HEAD_SHA=""
    finish failed "cannot read HEAD in $REPO_DIR"
    exit 1
  fi
  resolve_prev
  BAD_SHA=$(read_sha_file "$BAD_FILE" || true)

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
