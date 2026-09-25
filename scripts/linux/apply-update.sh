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
#      the backups dir (newest DOMOVOI_UPDATE_KEEP_BACKUPS kept). A failed
#      backup aborts the update before anything is stopped.
#   3. Stop domovoi-web and domovoi-core.
#   4. Re-sync the venv the LINUX_HOST.md way if pyproject.toml or a
#      requirements lock changed.
#   5. Rebuild the MPD image the way mpd_provisioner.py does if
#      Dockerfile.mpd or mpd.conf changed, and remove the room containers so
#      the core recreates them (same data volumes) from the new image.
#   6. systemctl restart domovoi-db: compose up plus Flyway.
#   7. Start core and web; both must answer their health endpoint within
#      DOMOVOI_UPDATE_HEALTH_TIMEOUT seconds.
#   On any failure in 3-7: stop both again, `git reset --keep` back to the
#   previous SHA, undo the dependency and MPD changes, restore the dump if
#   flyway_schema_history grew, restart, and record the new SHA as bad_sha so
#   the version panel stops offering it.
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
CORE_HEALTH_URL=${DOMOVOI_CORE_HEALTH_URL:-http://127.0.0.1:6370/v1/health}
WEB_HEALTH_URL=${DOMOVOI_WEB_HEALTH_URL:-http://127.0.0.1:6369/api/health}
PG_CONTAINER=${DOMOVOI_PG_CONTAINER:-domovoi-postgres}
PG_USER=${DOMOVOI_PG_USER:-domovoi}
PG_DB=${DOMOVOI_PG_DB:-domovoi}

CORE_UNIT=domovoi-core.service
WEB_UNIT=domovoi-web.service
DB_UNIT=domovoi-db.service

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
BACKUP_FILE=""
DB_RESTORED=0
STEPS=()
LAST_ERROR=""
RESULT_READY=0
FINAL_WRITTEN=0
FINAL_STATUS=""
SERVICES_STOPPED=0

log() { printf 'apply-update: %s\n' "$*"; }

now_ms() {
  local t
  t=$(date +%s%3N)
  # A date(1) without %N prints it literally; fall back to whole seconds.
  if [[ $t =~ ^[0-9]+$ ]]; then printf '%s' "$t"; else printf '%s000' "$(date +%s)"; fi
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

fmt_sec() { printf '%d.%03d' $(($1 / 1000)) $(($1 % 1000)); }

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
    printf '  "backup": %s,\n' "$(json_str_or_null "$BACKUP_FILE")"
    printf '  "db_restored": %s,\n' "$(json_bool "$DB_RESTORED")"
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
  set +e
  "$@" >"$out" 2>&1
  rc=$?
  set -e
  sed 's/^/    /' "$out"
  if [ "$rc" -eq 0 ]; then
    add_step "$name" ok "$t0" ""
  else
    add_step "$name" failed "$t0" "$(tail -n 15 "$out" | cut -c1-300)"
    LAST_ERROR="$name failed (exit $rc): $(tail -n 3 "$out" | tr '\n' ' ' | cut -c1-400 | sed 's/[[:space:]]*$//')"
    log "step $name failed (exit $rc)"
  fi
  rm -f "$out"
  return "$rc"
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

stop_services() { systemctl stop "$WEB_UNIT" "$CORE_UNIT"; }

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

backup_db() {
  local f partial
  mkdir -p "$BACKUP_DIR" || return 1
  # Dumps hold every secret in the database. Each one is 0600 regardless
  # (umask below); the dir is closed too where the filesystem allows it.
  chmod 0700 "$BACKUP_DIR" || echo "warning: could not chmod 0700 $BACKUP_DIR"
  f=$BACKUP_DIR/pre-${HEAD_SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ).dump
  partial=$f.partial
  echo "dumping $PG_DB from container $PG_CONTAINER to $f"
  if ! (umask 077; pg_exec pg_dump -Fc -U "$PG_USER" "$PG_DB" >"$partial"); then
    rm -f "$partial"; return 1
  fi
  if [ ! -s "$partial" ]; then
    echo "pg_dump produced an empty file"; rm -f "$partial"; return 1
  fi
  # A dump that pg_restore can't read is no backup at all.
  if ! docker exec -i "$PG_CONTAINER" pg_restore --list <"$partial" >/dev/null; then
    echo "pg_restore cannot read the dump"; rm -f "$partial"; return 1
  fi
  mv -f "$partial" "$f"
  BACKUP_FILE=$f
  prune_backups
}

prune_backups() {
  local old
  [[ $KEEP_BACKUPS =~ ^[0-9]+$ ]] && [ "$KEEP_BACKUPS" -ge 1 ] || return 0
  # Newest first; everything past the first KEEP_BACKUPS goes. Names carry
  # no whitespace (pre-<sha>-<timestamp>.dump).
  old=$(cd "$BACKUP_DIR" && ls -1t -- pre-*.dump 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))") || return 0
  local name
  for name in $old; do
    echo "pruning old backup $name"
    rm -f -- "${BACKUP_DIR:?}/$name"
  done
}

# Restore the pre-update dump into a fresh database, then swap it in by
# rename. `pg_restore --clean` into the live database would leave behind
# every object a failed migration CREATED (it only drops what the dump
# contains), and the re-run of that migration after a fix would then fail on
# "already exists". The replaced database is kept as <db>_failed_<ts> for
# inspection; drop it by hand once it's no longer interesting.
restore_db() {
  local ts tmpdb olddb
  [ -n "$BACKUP_FILE" ] && [ -s "$BACKUP_FILE" ] || { echo "no backup to restore"; return 1; }
  ts=$(date -u +%Y%m%d%H%M%S)
  tmpdb=${PG_DB}_restore_$ts
  olddb=${PG_DB}_failed_$ts
  psql_admin "CREATE DATABASE \"$tmpdb\" OWNER \"$PG_USER\"" || return 1
  if ! docker exec -i "$PG_CONTAINER" pg_restore -U "$PG_USER" -d "$tmpdb" \
      --single-transaction --exit-on-error <"$BACKUP_FILE"; then
    psql_admin "DROP DATABASE IF EXISTS \"$tmpdb\"" || true
    return 1
  fi
  psql_admin "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$PG_DB' AND pid <> pg_backend_pid()" >/dev/null || true
  if ! psql_admin "ALTER DATABASE \"$PG_DB\" RENAME TO \"$olddb\""; then
    psql_admin "DROP DATABASE IF EXISTS \"$tmpdb\"" || true
    return 1
  fi
  if ! psql_admin "ALTER DATABASE \"$tmpdb\" RENAME TO \"$PG_DB\""; then
    psql_admin "ALTER DATABASE \"$olddb\" RENAME TO \"$PG_DB\"" || true
    return 1
  fi
  echo "restored $BACKUP_FILE; the replaced database is kept as $olddb"
  DB_RESTORED=1
}

pip_as() {
  as_user "$VENV_DIR/bin/python" -m pip --disable-pip-version-check --no-input "$@"
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
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
    finish ok
  else
    finish failed "$LAST_ERROR"
  fi
}

full_update() {
  local dirty failed="" t0
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
  add_step preflight ok "$t0" "deps_changed=$DEPS_CHANGED mpd_changed=$MPD_CHANGED"
  write_result running

  if ! run_step backup backup_db; then
    if [ "$REQUIRE_BACKUP" = 1 ]; then
      finish aborted "the pre-update backup failed, so nothing was changed: $LAST_ERROR"
      return
    fi
    log "continuing without a backup (DOMOVOI_UPDATE_REQUIRE_BACKUP=$REQUIRE_BACKUP)"
  fi
  MIGRATIONS_BEFORE=$(migration_count || true)
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
  MIGRATIONS_AFTER=$(migration_count || true)

  if [ -z "$failed" ]; then
    write_atomic "$APPLIED_FILE" "$HEAD_SHA"$'\n'
    rm -f "$BAD_FILE"
    BAD_SHA=""
    finish ok
    return
  fi
  rollback "$failed" "$LAST_ERROR"
}

rollback() {
  local failed_step=$1 cause=$2 rb=""
  log "update failed at $failed_step; rolling back to ${PREV_SHA:0:12}"
  SERVICES_STOPPED=1
  run_step rollback-stop stop_services || true
  if run_step rollback-checkout git_as reset --keep "$PREV_SHA"; then
    if [ "$DEPS_CHANGED" = 1 ]; then
      run_step rollback-deps sync_deps || rb=${rb:-rollback-deps}
      # Best effort: a pin that can't be restored leaves a newer but
      # compatible version, which is what sync_deps alone would give.
      run_step rollback-pins restore_pins || true
    fi
    if [ "$MPD_CHANGED" = 1 ]; then
      run_step rollback-mpd rebuild_mpd || rb=${rb:-rollback-mpd}
    fi
    MIGRATIONS_AFTER=$(migration_count || true)
    if migrations_grew; then
      run_step restore-db restore_db || rb=${rb:-restore-db}
    fi
  else
    # The tree could not go back, so neither may the database: old data
    # under new code is worse than new data under new code.
    rb=rollback-checkout
  fi
  run_step rollback-migrate restart_db || rb=${rb:-rollback-migrate}
  run_step rollback-start start_services || rb=${rb:-rollback-start}
  SERVICES_STOPPED=0
  run_step rollback-health wait_healthy || rb=${rb:-rollback-health}

  BAD_SHA=$HEAD_SHA
  write_atomic "$BAD_FILE" "$HEAD_SHA"$'\n'
  if [ -z "$rb" ]; then
    # Healthy again on the previous SHA: that is the known-good baseline now.
    write_atomic "$APPLIED_FILE" "$PREV_SHA"$'\n'
    finish rolled_back "update to ${HEAD_SHA:0:12} failed at $failed_step and was rolled back to ${PREV_SHA:0:12}: $cause"
  else
    finish rollback_failed "update to ${HEAD_SHA:0:12} failed at $failed_step ($cause); the rollback then failed at $rb: $LAST_ERROR"
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
