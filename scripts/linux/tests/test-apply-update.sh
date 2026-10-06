#!/usr/bin/env bash
# Hermetic tests for scripts/linux/apply-update.sh.
#
# Each case builds a throwaway git repo (commit A, then commit B with the
# change under test), a fake venv, and PATH shims for systemctl, docker,
# pg_dump, pg_restore, psql and curl that log every call and can be told to
# fail. Nothing touches systemd, Docker or a real database, so this runs
# under Git Bash as well as on Linux.
#
# The shims model just enough of the real thing to test the decisions:
#   * each database is a file holding its flyway_schema_history row count,
#     plus a ledgers-<db> file of "plugin_<slug> <rows>" lines, one per
#     plugin migration ledger; every case starts with domovoi and its
#     domovoi_test twin;
#   * `systemctl restart domovoi-db.service` is Flyway: it raises the count
#     to the number of migration files in the checkout and never lowers it;
#     it also brings Postgres back while pg-down says it is down (every
#     `docker exec` into it fails meanwhile);
#   * the count is of applied migrations; extra-rows-<db> adds rows that
#     only `count(*)` sees (a baseline row), never the applied-migration
#     query (APPLIED_SQL);
#   * the main database also has a registry-<db> file, the plugins table as
#     "slug|t|status|last_error" lines;
#   * `systemctl start domovoi-core.service` is the core's boot: every
#     plugin in the checkout's plugins/ catches its ledger up to its
#     migration files, in each database that exists (never un-applies), and
#     every enabled registry row gets this boot's status: ok, or load_error
#     while plugin-fail-when-head names the plugin and HEAD, switched off
#     for an import failure and left on (ledger unmoved) for a failed
#     migration catch-up, the way the loader does it;
#   * pg_dump writes the count, the ledgers and the registry into the dump
#     and pg_restore reads them back;
#   * psql CREATE/DROP/ALTER ... RENAME DATABASE move those files around;
#   * curl can fail while HEAD is a given SHA (the "new code is broken" case);
#   * the venv's `python -m domovoi.egress --print-policy` prints the
#     internet answer held in internet-policy (nothing: unanswered), and
#     `docker compose` / `docker inspect` / `docker stop` model the search
#     helper (SearXNG): compose can be told to fail or to hang, inspect
#     reports searxng-running; a systemd-run shim (write_systemd_run) logs
#     the detached start.
#
# Usage: bash scripts/linux/tests/test-apply-update.sh
# Exit status is non-zero if any case failed. Set HARNESS_PYTHON to a Python
# interpreter to also check that every result file is valid JSON.

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$HERE/../apply-update.sh
WORK=$(mktemp -d)
KEEP_WORK=${KEEP_WORK:-0}
cleanup() { if [ "$KEEP_WORK" = 1 ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi; }
trap cleanup EXIT

# Hermetic git: no user or system config (hooks, signing, autocrlf).
: >"$WORK/gitconfig"
export GIT_CONFIG_GLOBAL=$WORK/gitconfig GIT_CONFIG_NOSYSTEM=1
SHIM_REAL_GIT=$(command -v git)
SHIM_REAL_DATE=$(command -v date)
export SHIM_REAL_GIT SHIM_REAL_DATE
export GIT_AUTHOR_NAME=harness GIT_AUTHOR_EMAIL=harness@example.invalid
export GIT_COMMITTER_NAME=harness GIT_COMMITTER_EMAIL=harness@example.invalid

PASSED=0
FAILED=0
CASE_FAILED=0

fail() { echo "    FAIL: $*"; CASE_FAILED=1; }
check() { local what=$1; shift; if ! "$@"; then fail "$what"; fi; }

# ─── fixtures ────────────────────────────────────────────────────────────

write_shims() {
  local bin=$1
  mkdir -p "$bin"

  cat >"$bin/systemctl" <<'SH'
#!/usr/bin/env bash
echo "systemctl $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = show ]; then
  case "$*" in *ExecStart*) cat "$SHIM_STATE/show-execstart" 2>/dev/null ;; esac
  # How the unit's last stop went: show-<unit> holds Key=Value lines.
  case "$*" in *Result*) cat "$SHIM_STATE/show-${2-}" 2>/dev/null ;; esac
  exit 0
fi
# stop-hangs lists units whose stop never finishes by itself: only a
# SIGKILL (`systemctl kill`) ends it, and not even that for a unit also in
# kill-fails.
hangs() { grep -qxF -- "$1" "$SHIM_STATE/stop-hangs" 2>/dev/null; }
still_up() {
  hangs "$1" && { [ ! -f "$SHIM_STATE/killed-$1" ] \
    || grep -qxF -- "$1" "$SHIM_STATE/kill-fails" 2>/dev/null; }
}
# Every unit runs until a stop finishes (stopped-<unit>) or a kill lands
# (killed-<unit>); a start brings it back.
if [ "${1-}" = is-active ]; then
  if still_up "${2-}"; then echo deactivating; exit 0; fi
  if [ -f "$SHIM_STATE/killed-${2-}" ]; then echo failed; exit 3; fi
  if [ -f "$SHIM_STATE/stopped-${2-}" ]; then echo inactive; exit 3; fi
  echo active; exit 0
fi
if [ "${1-}" = kill ]; then
  : >"$SHIM_STATE/killed-${!#}"
  exit 0
fi
if [ "${1-}" = stop ] && ! grep -qxF -- "$*" "$SHIM_STATE/fail-systemctl" 2>/dev/null; then
  # systemd stops the units side by side: each is down as soon as its own
  # stop is done, whatever happens to the client waiting on the others.
  for u in "${@:2}"; do
    while still_up "$u"; do sleep 0.1; done
    : >"$SHIM_STATE/stopped-$u"
  done
fi
if [ -f "$SHIM_STATE/fail-systemctl" ] && grep -qxF -- "$*" "$SHIM_STATE/fail-systemctl"; then
  echo "systemctl: forced failure for: $*" >&2; exit 1
fi
if [ "${1-}" = restart ] && [ "${2-}" = domovoi-db.service ]; then
  # Flyway: apply whatever the checkout ships, never un-apply.
  files=$(ls "$SHIM_REPO"/domovoi/db/migrations/V*.sql 2>/dev/null | wc -l | tr -d ' ')
  cur=$(cat "$SHIM_STATE/db-domovoi" 2>/dev/null || echo 0)
  if [ -f "$SHIM_STATE/flyway-fail-when-head" ] \
      && [ "$("$SHIM_REAL_GIT" -C "$SHIM_REPO" rev-parse HEAD)" = "$(cat "$SHIM_STATE/flyway-fail-when-head")" ]; then
    echo "flyway: migration failed" >&2; exit 1
  fi
  if [ "$files" -gt "$cur" ]; then echo "$files" >"$SHIM_STATE/db-domovoi"; fi
  # compose up -d postgres
  rm -f "$SHIM_STATE/pg-down"
fi
if [ "${1-}" = start ] && [[ " $* " == *" domovoi-core.service "* ]]; then
  # The core's boot, the loader's way: each plugin's migration catch-up
  # (main database, then _test), then its load, whose status lands in the
  # registry. plugin-fail-when-head lines: "<slug> <sha> import|catchup".
  head=$("$SHIM_REAL_GIT" -C "$SHIM_REPO" rev-parse HEAD)
  reg=$SHIM_STATE/registry-domovoi
  fails() { grep -qxF -- "$1 $head $2" "$SHIM_STATE/plugin-fail-when-head" 2>/dev/null; }
  for mig in "$SHIM_REPO"/plugins/*/migrations; do
    [ -d "$mig" ] || continue
    slug=$(basename "$(dirname "$mig")")
    ledger=plugin_$slug
    # Boot skips a switched-off plugin; a failed catch-up applies nothing.
    if grep -q "^$slug|f|" "$reg" 2>/dev/null || fails "$slug" catchup; then continue; fi
    files=$(ls "$mig"/V*.sql 2>/dev/null | wc -l | tr -d ' ')
    for db in domovoi domovoi_test; do
      [ -f "$SHIM_STATE/db-$db" ] || continue
      f=$SHIM_STATE/ledgers-$db
      cur=$(awk -v l="$ledger" '$1 == l { print $2 }' "$f" 2>/dev/null)
      if [ "$files" -gt "${cur:-0}" ]; then
        { grep -v "^$ledger " "$f" 2>/dev/null; echo "$ledger $files"; } >"$f.new"
        mv "$f.new" "$f"
      fi
    done
  done
  if [ -f "$reg" ]; then
    while IFS='|' read -r slug enabled status err; do
      [ -n "$slug" ] || continue
      if [ "$enabled" = f ]; then echo "$slug|f|$status|$err"
      elif fails "$slug" import; then echo "$slug|f|load_error|import failed: No module named 'boom'"
      elif fails "$slug" catchup; then echo "$slug|t|load_error|$slug: V002__played.sql failed | at line 1"
      else echo "$slug|t|ok|"
      fi
    done <"$reg" >"$reg.new"
    mv "$reg.new" "$reg"
  fi
fi
if [ "${1-}" = start ]; then
  for u in "${@:2}"; do rm -f "$SHIM_STATE/stopped-$u" "$SHIM_STATE/killed-$u"; done
fi
exit 0
SH

  cat >"$bin/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_STATE/calls.log"
case "${1-}" in
  exec)
    if [ -f "$SHIM_STATE/pg-down" ]; then
      echo "Error response from daemon: container domovoi-postgres is not running" >&2; exit 1
    fi
    shift
    while [ "${1-}" = -i ]; do shift; done
    shift   # the container
    exec "$@"
    ;;
  build)
    if [ -f "$SHIM_STATE/fail-docker-build" ]; then echo "build failed" >&2; exit 1; fi
    exit 0
    ;;
  ps)
    cat "$SHIM_STATE/containers" 2>/dev/null
    exit 0
    ;;
  rm)
    name=${!#}
    grep -vxF -- "$name" "$SHIM_STATE/containers" >"$SHIM_STATE/containers.new" || true
    mv "$SHIM_STATE/containers.new" "$SHIM_STATE/containers"
    exit 0
    ;;
  compose)
    if [ -f "$SHIM_STATE/fail-docker-compose" ]; then
      echo "Error response from daemon: pull access denied" >&2; exit 1
    fi
    # A first start that never finishes (an image pull on a slow line).
    if [ -f "$SHIM_STATE/hang-docker-compose" ]; then exec sleep 30; fi
    case " $* " in *" up "*) echo true >"$SHIM_STATE/searxng-running" ;; esac
    exit 0
    ;;
  inspect)
    if [ -f "$SHIM_STATE/searxng-running" ]; then cat "$SHIM_STATE/searxng-running"; exit 0; fi
    echo "Error: No such object: ${!#}" >&2; exit 1
    ;;
  stop)
    echo false >"$SHIM_STATE/searxng-running"
    exit 0
    ;;
esac
exit 0
SH

  cat >"$bin/pg_dump" <<'SH'
#!/usr/bin/env bash
echo "pg_dump $*" >>"$SHIM_STATE/calls.log"
db=${!#}
if [ -f "$SHIM_STATE/fail-pg_dump" ] || [ -f "$SHIM_STATE/fail-pg_dump-$db" ]; then
  echo "pg_dump: connection refused" >&2; exit 1
fi
echo "DOMOVOI-FAKE-DUMP migrations=$(cat "$SHIM_STATE/db-$db")"
sed 's/^/ledger /' "$SHIM_STATE/ledgers-$db" 2>/dev/null
sed 's/^/registry /' "$SHIM_STATE/registry-$db" 2>/dev/null
exit 0
SH

  cat >"$bin/pg_restore" <<'SH'
#!/usr/bin/env bash
echo "pg_restore $*" >>"$SHIM_STATE/calls.log"
body=$(cat)
case "$body" in DOMOVOI-FAKE-DUMP*) ;; *) echo "pg_restore: not a dump" >&2; exit 1 ;; esac
db=""
while [ $# -gt 0 ]; do
  case "$1" in -d) db=$2; shift 2 ;; *) shift ;; esac
done
if [ -n "$db" ]; then
  [ -f "$SHIM_STATE/db-$db" ] || { echo "pg_restore: no database $db" >&2; exit 1; }
  head=${body%%$'\n'*}
  echo "${head##*migrations=}" >"$SHIM_STATE/db-$db"
  printf '%s\n' "$body" | sed -n 's/^ledger //p' >"$SHIM_STATE/ledgers-$db"
  printf '%s\n' "$body" | sed -n 's/^registry //p' >"$SHIM_STATE/registry-$db"
fi
exit 0
SH

  cat >"$bin/psql" <<'SH'
#!/usr/bin/env bash
echo "psql $*" >>"$SHIM_STATE/calls.log"
db="" sql=""
while [ $# -gt 0 ]; do
  case "$1" in
    -d) db=$2; shift 2 ;;
    -tAc|-c) sql=$2; shift 2 ;;
    *) shift ;;
  esac
done
dbfile() { printf '%s/db-%s' "$SHIM_STATE" "$1"; }
ledgers() { printf '%s/ledgers-%s' "$SHIM_STATE" "$1"; }
registry() { printf '%s/registry-%s' "$SHIM_STATE" "$1"; }
exists() { [ -f "$(dbfile "$1")" ] || { echo "psql: database $1 does not exist" >&2; exit 2; }; }
case "$sql" in
  "SELECT count(*) FROM flyway_schema_history")
    exists "$db"
    echo $(( $(cat "$(dbfile "$db")") + $(cat "$SHIM_STATE/extra-rows-$db" 2>/dev/null || echo 0) )) ;;
  "SELECT count(*) FROM flyway_schema_history WHERE success AND type = 'SQL' AND version IS NOT NULL")
    exists "$db"; cat "$(dbfile "$db")" ;;
  *query_to_xml*schema_history*)
    exists "$db"; cat "$(ledgers "$db")" 2>/dev/null ;;
  *" FROM plugins ORDER BY slug")
    exists "$db"; cat "$(registry "$db")" 2>/dev/null ;;
  "UPDATE plugins SET enabled = true"*" AND slug IN ("*")")
    exists "$db"
    list=${sql##*slug IN (}; list=${list%)}; list=${list//\'/}; list=${list//,/ }
    f=$(registry "$db") n=0
    while IFS='|' read -r slug enabled status err; do
      if [ "$enabled" = f ] && [ "$status" = load_error ] && [[ " $list " == *" $slug "* ]]; then
        enabled=t; n=$((n + 1))
      fi
      echo "$slug|$enabled|$status|$err"
    done <"$f" >"$f.new"
    mv "$f.new" "$f"
    echo "UPDATE $n" ;;
  "SELECT 1 FROM pg_database WHERE datname = '"*"'")
    name=${sql#*datname = \'}; name=${name%\'}
    if [ -f "$(dbfile "$name")" ]; then echo 1; fi ;;
  CREATE\ DATABASE*)
    name=$(printf '%s' "$sql" | sed 's/^CREATE DATABASE "\([^"]*\)".*/\1/')
    echo 0 >"$(dbfile "$name")"; : >"$(ledgers "$name")"; : >"$(registry "$name")" ;;
  DROP\ DATABASE*)
    name=$(printf '%s' "$sql" | sed 's/^DROP DATABASE IF EXISTS "\([^"]*\)".*/\1/')
    rm -f "$(dbfile "$name")" "$(ledgers "$name")" "$(registry "$name")" ;;
  ALTER\ DATABASE*)
    from=$(printf '%s' "$sql" | sed 's/^ALTER DATABASE "\([^"]*\)" RENAME TO "\([^"]*\)"$/\1/')
    to=$(printf '%s' "$sql" | sed 's/^ALTER DATABASE "\([^"]*\)" RENAME TO "\([^"]*\)"$/\2/')
    [ -f "$(dbfile "$from")" ] && [ ! -f "$(dbfile "$to")" ] || { echo "psql: rename failed" >&2; exit 1; }
    mv "$(dbfile "$from")" "$(dbfile "$to")"
    if [ -f "$(ledgers "$from")" ]; then mv "$(ledgers "$from")" "$(ledgers "$to")"; fi
    if [ -f "$(registry "$from")" ]; then mv "$(registry "$from")" "$(registry "$to")"; fi ;;
  SELECT\ pg_terminate_backend*) ;;
  *) echo "psql: unexpected SQL: $sql" >&2; exit 1 ;;
esac
SH

  cat >"$bin/curl" <<'SH'
#!/usr/bin/env bash
url=${!#}
echo "curl $url" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/curl-fail-always" ]; then exit 7; fi
if [ -f "$SHIM_STATE/curl-fail-when-head" ]; then
  head=$("$SHIM_REAL_GIT" -C "$SHIM_REPO" rev-parse HEAD)
  if [ "$head" = "$(cat "$SHIM_STATE/curl-fail-when-head")" ]; then exit 22; fi
fi
exit 0
SH

  cat >"$bin/git" <<'SH'
#!/usr/bin/env bash
echo "git $*" >>"$SHIM_STATE/calls.log"
exec "$SHIM_REAL_GIT" "$@"
SH

  chmod +x "$bin"/*
}

write_root_shims() {  # pretend to be root: id says 0, runuser logs and runs
  local bin=$1
  mkdir -p "$bin"
  cat >"$bin/id" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -u ]; then echo 0; exit 0; fi
exec /usr/bin/id "$@"
SH
  cat >"$bin/runuser" <<'SH'
#!/usr/bin/env bash
# runuser -u USER -- CMD...
echo "runuser $1 $2 -- ${*:4}" >>"$SHIM_STATE/calls.log"
shift 3
exec "$@"
SH
  chmod +x "$bin"/*
}

write_venv() {
  local venv=$1
  mkdir -p "$venv/bin"
  cat >"$venv/bin/python" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -m ] && [ "${2-}" = pip ]; then
  shift 2
  echo "pip $*" >>"$SHIM_STATE/calls.log"
  if [ -f "$SHIM_STATE/fail-pip" ]; then echo "pip: resolution failed" >&2; exit 1; fi
  if [[ " $* " == *" install "* ]] && [ -f "$SHIM_STATE/pip-fail-when-head" ] \
      && [ "$("$SHIM_REAL_GIT" -C "$SHIM_REPO" rev-parse HEAD)" = "$(cat "$SHIM_STATE/pip-fail-when-head")" ]; then
    cat "$SHIM_STATE/pip-fail-output" >&2; exit 1
  fi
  for a in "$@"; do
    if [ "$a" = freeze ]; then
      printf 'requests==2.31.0\ntorch==2.14.0+cpu\nlocalthing @ file:///tmp/localthing.whl\n'
    fi
    if [ "$a" = -r ]; then echo "pip-r-file: $(tr '\n' ' ' <"${!#}")" >>"$SHIM_STATE/calls.log"; fi
  done
  exit 0
fi
echo "python $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = -m ] && [ "${2-}" = domovoi.egress ]; then
  # A checkout from before the internet setting: no such module.
  if [ -f "$SHIM_STATE/egress-missing" ]; then echo "No module named domovoi.egress" >&2; exit 1; fi
  cat "$SHIM_STATE/internet-policy" 2>/dev/null
  echo
  exit 0
fi
if [ "${1-}" = -c ]; then
  d=$(cd "$(dirname "$0")/.." && pwd)/lib/site-packages; mkdir -p "$d"; printf '%s\n' "$d"
fi
SH
  chmod +x "$venv/bin/python"
}

g() { git -C "$REPO" "$@"; }

commit_all() { g add -A >/dev/null && g commit -q -m "$1" && g rev-parse HEAD; }

# new_case NAME: a fresh repo at commit A, shims, venv and state. Sets
# CASE, REPO, STATE, UPD, CORE_STATE, SHA_A and exports the shim env.
new_case() {
  CASE_NAME=$1
  CASE_FAILED=0
  CASE=$WORK/$1
  REPO=$CASE/repo
  STATE=$CASE/state
  UPD=$CASE/update
  CORE_STATE=$CASE/core-state
  mkdir -p "$REPO/domovoi/db/migrations" "$STATE" "$CORE_STATE"
  write_shims "$CASE/bin"
  write_venv "$CASE/venv"
  : >"$STATE/calls.log"
  printf 'domovoi-postgres\ndomovoi-mpd-kitchen\ndomovoi-mpd-office\n' >"$STATE/containers"
  echo 1 >"$STATE/db-domovoi"
  echo "plugin_radio 1" >"$STATE/ledgers-domovoi"
  echo "radio|t|ok|" >"$STATE/registry-domovoi"
  # The test twin compose creates at initdb: no Flyway run there, but the
  # plugin runtime migrates it too.
  echo 0 >"$STATE/db-domovoi_test"
  echo "plugin_radio 1" >"$STATE/ledgers-domovoi_test"

  g init -q -b main
  printf '[project]\nname = "domovoi"\nversion = "0"\n' >"$REPO/pyproject.toml"
  printf 'numpy==2.4.6\n' >"$REPO/requirements.lock"
  printf 'FROM debian:bookworm-slim\n' >"$REPO/domovoi/Dockerfile.mpd"
  printf 'music_directory "/music"\n' >"$REPO/domovoi/mpd.conf"
  printf 'CREATE TABLE a (id int);\n' >"$REPO/domovoi/db/migrations/V001__a.sql"
  printf 'print("a")\n' >"$REPO/domovoi/app.py"
  # A bundled plugin with one migration, already applied (ledgers above).
  mkdir -p "$REPO/plugins/radio/migrations"
  printf 'CREATE TABLE stations (id bigserial PRIMARY KEY);\n' >"$REPO/plugins/radio/migrations/V001__stations.sql"
  SHA_A=$(commit_all A)
  RESULT=""

  export SHIM_STATE=$STATE SHIM_REPO=$REPO
}

# run_update [extra PATH dir]: run the script against the current case.
# NO_BASH_CLOCK=1 runs it without $EPOCHREALTIME, the way a bash older than
# 5 would, so its clock is date(1) and a shim can play that.
run_update() {
  local extra_bin=${1-}
  local path=$CASE/bin:$PATH venv_env=(DOMOVOI_VENV="$CASE/venv") manage_env=()
  local run=(bash "$SCRIPT")
  if [ -n "$extra_bin" ]; then path=$extra_bin:$path; fi
  if [ "${NO_VENV_ENV:-0}" = 1 ]; then venv_env=(); fi
  if [ -n "${MANAGE_SEARXNG:-}" ]; then manage_env=(DOMOVOI_MANAGE_SEARXNG="$MANAGE_SEARXNG"); fi
  # Unset, EPOCHREALTIME is an ordinary (empty) variable in that shell.
  if [ "${NO_BASH_CLOCK:-0}" = 1 ]; then run=(bash -c 'unset EPOCHREALTIME; . "$0"' "$SCRIPT"); fi
  RC=0
  env -u DOMOVOI_VENV -u DOMOVOI_MANAGE_SEARXNG PATH="$path" \
    DOMOVOI_REPO_DIR="$REPO" \
    ${venv_env[@]+"${venv_env[@]}"} \
    ${manage_env[@]+"${manage_env[@]}"} \
    DOMOVOI_UPDATE_DIR="$UPD" \
    DOMOVOI_USER=tester \
    DOMOVOI_CORE_STATE_DIR="$CORE_STATE" \
    DOMOVOI_UPDATE_HEALTH_TIMEOUT=1 \
    DOMOVOI_UPDATE_HEALTH_INTERVAL=0.2 \
    DOMOVOI_UPDATE_STOP_TIMEOUT="${STOP_TIMEOUT:-5}" \
    DOMOVOI_UPDATE_STOP_KILL_WAIT=1 \
    DOMOVOI_UPDATE_KEEP_BACKUPS="${KEEP_BACKUPS:-5}" \
    DOMOVOI_UPDATE_SEARXNG_DETACH="${SEARXNG_DETACH:-0}" \
    DOMOVOI_UPDATE_SEARXNG_TIMEOUT="${SEARXNG_TIMEOUT:-300}" \
    "${run[@]}" >"$CASE/output.log" 2>&1 || RC=$?
  RESULT=$UPD/last-result.json
}

# A top-level field of the result, as raw JSON ("ok", true, null, 3 ...).
field() { sed -n "s/^  \"$1\": \(.*\)$/\1/p" "$RESULT" | sed 's/,$//'; }

called() { grep -qF -- "$1" "$STATE/calls.log"; }
not_called() { ! grep -qF -- "$1" "$STATE/calls.log"; }
line_of() { grep -nF -- "$1" "$STATE/calls.log" | head -n 1 | cut -d: -f1; }
before() {  # before A B: the first call matching A precedes the first matching B
  local a b
  a=$(line_of "$1"); b=$(line_of "$2")
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
}
again_after() {  # again_after A B: some call matching B comes after the first matching A
  local a b
  a=$(line_of "$1"); b=$(grep -nF -- "$2" "$STATE/calls.log" | tail -n 1 | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ "$b" -gt "$a" ]
}
eq() { [ "$1" = "$2" ] || { echo "      expected [$2], got [$1]"; return 1; }; }
step_is() { grep -qF "{\"name\": \"$1\", \"status\": \"$2\"" "$RESULT"; }  # step_is NAME STATUS
step_is_timed() { grep -qF "{\"name\": \"$1\", \"status\": \"ok\", \"duration_sec\": $2," "$RESULT"; }  # step_is_timed NAME SECONDS
file_is() { [ -f "$1" ] && eq "$(tr -d '[:space:]' <"$1")" "$2"; }
# step_took_under NAME SECONDS: the step's recorded duration is below SECONDS.
step_took_under() {
  local d
  d=$(grep -o "{\"name\": \"$1\", \"status\": \"[a-z_]*\", \"duration_sec\": [0-9.]*" "$RESULT" \
    | head -n 1 | sed 's/.*"duration_sec": //')
  [ -n "$d" ] && [ "${d%.*}" -lt "$2" ] || { echo "      $1 took [$d], expected under ${2}s"; return 1; }
}
# The result's steps by name, in order, on one line.
step_names() { grep -o '{"name": "[a-z-]*"' "$RESULT" | sed 's/^{"name": "//; s/"$//' | tr '\n' ' ' | sed 's/ $//'; }

valid_json() {
  [ -n "${HARNESS_PYTHON:-}" ] || return 0
  local p=$RESULT py=$HARNESS_PYTHON
  if command -v cygpath >/dev/null 2>&1; then p=$(cygpath -w "$RESULT"); py=$(cygpath -u "$py"); fi
  "$py" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); assert isinstance(d, dict) and isinstance(d["steps"], list)' "$p"
}

# durations_within SECONDS [AT_LEAST]: the result has AT_LEAST (1)
# durations, and every one is a plain N.NNN no longer than SECONDS.
durations_within() {
  local d n=0 bad=0
  while IFS= read -r d; do
    n=$((n + 1))
    if ! [[ $d =~ ^[0-9]{1,6}\.[0-9]{3}$ ]] || [ "${d%.*}" -gt "$1" ]; then
      echo "      duration $d is not a plain N.NNN of at most ${1}s"; bad=1
    fi
  done < <(grep -o '"duration_sec": [^,}]*' "$RESULT" | sed 's/^"duration_sec": //')
  [ "$n" -ge "${2:-1}" ] || { echo "      $n durations, expected at least ${2:-1}"; bad=1; }
  [ "$bad" = 0 ]
}

end_case() {
  # RESULT is empty in a case that never ran the script.
  if [ -n "$RESULT" ]; then
    check "result file is valid JSON" valid_json
    # Whatever date(1) the host has (uutils on Ubuntu 26.04), no case here
    # runs for anything like an hour.
    check "every duration is plausible" durations_within 3600
  fi
  if [ "$CASE_FAILED" = 0 ]; then
    PASSED=$((PASSED + 1)); echo "ok   $CASE_NAME"
  else
    FAILED=$((FAILED + 1)); echo "FAIL $CASE_NAME"
    echo "    --- calls"; sed 's/^/    /' "$STATE/calls.log"
    echo "    --- output"; sed 's/^/    /' "$CASE/output.log"
    [ -f "$RESULT" ] && { echo "    --- result"; sed 's/^/    /' "$RESULT"; }
  fi
}

# ─── cases ───────────────────────────────────────────────────────────────

case_noop_restart() {
  new_case noop_restart
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mode restart" eq "$(field mode)" '"restart"'
  check "prev from applied_sha" eq "$(field prev_source)" '"applied"'
  check "stops web then core" called "systemctl stop domovoi-web.service domovoi-core.service"
  check "starts core and web" called "systemctl start domovoi-core.service domovoi-web.service"
  check "health-checks core" called "curl http://127.0.0.1:6370/v1/health"
  check "health-checks web" called "curl http://127.0.0.1:6369/api/health"
  check "no backup on a plain restart" not_called "pg_dump"
  check "no pip on a plain restart" not_called "pip "
  check "no docker build on a plain restart" not_called "docker build"
  # The dashboard's "Restart Domovoi" with nothing waiting: Postgres answers
  # with the checkout's one migration applied, so no Flyway run either.
  check "asks Flyway's history what is applied" called "WHERE success AND type = 'SQL' AND version IS NOT NULL"
  check "no migrations on a plain restart" not_called "systemctl restart domovoi-db.service"
  check "nothing but the restart's own steps" eq "$(step_names)" "stop-services start-services health searxng"
  check "migration count recorded" eq "$(field migrations_before)" 1
  check "and left as it was" eq "$(field migrations_after)" 1
  check "applied_sha unchanged" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_noop_without_any_history() {
  new_case noop_without_any_history
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mode restart" eq "$(field mode)" '"restart"'
  check "prev falls back to HEAD" eq "$(field prev_source)" '"head"'
  check "no migrations: the database has them all" not_called "systemctl restart domovoi-db.service"
  check "a healthy restart records the baseline" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

# The checkout ships a migration the database never got (domovoi-db last
# ran before it was there): the plain restart still runs Flyway, as every
# plain restart did before it learned to leave the database alone.
case_noop_restart_migrates_a_database_behind_the_checkout() {
  new_case noop_restart_migrates_a_database_behind_the_checkout
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo 0 >"$STATE/db-domovoi"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "still a plain restart" eq "$(field mode)" '"restart"'
  check "runs Flyway through domovoi-db" called "systemctl restart domovoi-db.service"
  check "with core and web stopped" before "systemctl stop" "systemctl restart domovoi-db.service"
  check "before they start" before "systemctl restart domovoi-db.service" "systemctl start domovoi-core.service"
  check "the step says why" grep -qF \
    '"name": "migrate", "status": "ok", "duration_sec": ' "$RESULT"
  check "in words" grep -qF '"detail": "0 of the 1 migrations in the checkout applied"' "$RESULT"
  check "migrations before" eq "$(field migrations_before)" 0
  check "and after" eq "$(field migrations_after)" 1
  check "still no backup" not_called "pg_dump"
  check "still no pip" not_called "pip "
  end_case
}

# Postgres isn't answering (its container stopped): restarting domovoi-db
# brings it back, as the plain restart always did.
case_noop_restart_brings_postgres_back() {
  new_case noop_restart_brings_postgres_back
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  : >"$STATE/pg-down"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "restarts domovoi-db" called "systemctl restart domovoi-db.service"
  check "the step says why" grep -qF '"detail": "Postgres did not answer with its Flyway history"' "$RESULT"
  check "Postgres is back" test ! -f "$STATE/pg-down"
  check "count unknown before" eq "$(field migrations_before)" null
  check "known after" eq "$(field migrations_after)" 1
  end_case
}

# A baseline row in Flyway's history is a row, not a migration: count(*)
# matches the checkout's one file, but nothing was applied.
case_noop_restart_a_baseline_row_is_not_a_migration() {
  new_case noop_restart_a_baseline_row_is_not_a_migration
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo 0 >"$STATE/db-domovoi"
  echo 1 >"$STATE/extra-rows-domovoi"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "runs Flyway" called "systemctl restart domovoi-db.service"
  check "the step says why" grep -qF '"detail": "0 of the 1 migrations in the checkout applied"' "$RESULT"
  end_case
}

case_deps_changed_as_root() {
  new_case deps_changed_as_root
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\ndependencies = ["httpx"]\n' >"$REPO/pyproject.toml"
  local sha_b; sha_b=$(commit_all "B: deps")
  write_root_shims "$CASE/rootbin"
  run_update "$CASE/rootbin"
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mode update" eq "$(field mode)" '"update"'
  check "deps_changed" eq "$(field deps_changed)" true
  check "mpd not changed" eq "$(field mpd_changed)" false
  check "from A" eq "$(field from_sha)" "\"$SHA_A\""
  check "to B" eq "$(field to_sha)" "\"$sha_b\""
  check "backs up through the postgres container" called "docker exec domovoi-postgres pg_dump -Fc -U domovoi domovoi"
  check "verifies the dump" called "pg_restore --list"
  check "backup before stopping anything" before "pg_dump" "systemctl stop"
  check "stops before syncing" before "systemctl stop" "install torch"
  check "snapshots the venv" called "pip --disable-pip-version-check --no-input freeze --exclude-editable"
  check "CPU torch first" called "pip --disable-pip-version-check --no-input install torch --index-url https://download.pytorch.org/whl/cpu"
  check "then the extras" called "pip --disable-pip-version-check --no-input install -e .[dev,real-clients,voice-profile]"
  check "torch before extras" before "install torch" "install -e"
  check "syncs before migrating" before "install -e" "systemctl restart domovoi-db.service"
  check "migrates before starting" before "systemctl restart domovoi-db.service" "systemctl start domovoi-core.service"
  check "git runs as the service user" called "runuser -u tester -- git -C $REPO"
  check "every git call goes through runuser" eq \
    "$(grep -c '^runuser -u tester -- git ' "$STATE/calls.log")" "$(grep -c '^git ' "$STATE/calls.log")"
  check "pip runs as the service user" called "runuser -u tester -- $CASE/venv/bin/python -m pip"
  check "systemctl does not go through runuser" not_called "runuser -u tester -- systemctl"
  check "no MPD rebuild" not_called "docker build"
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  check "one backup kept" eq "$(ls "$UPD/backups" | grep -c '^pre-.*Z\.dump$')" 1
  check "backs up the test twin too" called "docker exec domovoi-postgres pg_dump -Fc -U domovoi domovoi_test"
  check "one test backup kept" eq "$(ls "$UPD/backups" | grep -c '^pre-.*Z\.test\.dump$')" 1
  check "both dumps verified" eq "$(grep -c '^pg_restore --list' "$STATE/calls.log")" 2
  check "test backup in the result" eq "$(field test_backup | grep -c '\.test\.dump"$')" 1
  check "no test restore on success" eq "$(field test_db_restored)" false
  check "no bad_sha" eq "$(field bad_sha)" null
  end_case
}

case_mpd_changed() {
  new_case mpd_changed
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'FROM debian:trixie-slim\n' >"$REPO/domovoi/Dockerfile.mpd"
  commit_all "B: mpd" >/dev/null
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mpd_changed" eq "$(field mpd_changed)" true
  check "deps not changed" eq "$(field deps_changed)" false
  check "builds like mpd_provisioner" called "docker build -t domovoi-mpd:latest -f $REPO/domovoi/Dockerfile.mpd $REPO/domovoi"
  check "removes room kitchen" called "docker rm -f domovoi-mpd-kitchen"
  check "removes room office" called "docker rm -f domovoi-mpd-office"
  check "leaves postgres alone" not_called "docker rm -f domovoi-postgres"
  check "rebuild while core is stopped" before "systemctl stop" "docker build"
  check "rebuild before core starts" before "docker build" "systemctl start domovoi-core.service"
  check "no pip" not_called "pip "
  end_case
}

case_mpd_conf_only() {
  new_case mpd_conf_only
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'music_directory "/music"\naudio_output { type "httpd" }\n' >"$REPO/domovoi/mpd.conf"
  commit_all "B: mpd.conf" >/dev/null
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "mpd.conf alone counts as an MPD change" eq "$(field mpd_changed)" true
  check "containers recreated for the new conf" called "docker rm -f domovoi-mpd-kitchen"
  end_case
}

case_migration_health_failure_rolls_back() {
  new_case migration_health_failure_rolls_back
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'CREATE TABLE b (id int);\n' >"$REPO/domovoi/db/migrations/V002__b.sql"
  printf 'raise SystemExit("broken")\n' >"$REPO/domovoi/app.py"
  printf '[project]\nname = "domovoi"\nversion = "2"\n' >"$REPO/pyproject.toml"
  local sha_b; sha_b=$(commit_all "B: migration + broken code")
  echo "$sha_b" >"$STATE/curl-fail-when-head"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "bad_sha in the result" eq "$(field bad_sha)" "\"$sha_b\""
  check "bad_sha file" file_is "$UPD/bad_sha" "$sha_b"
  check "applied_sha stays A" file_is "$UPD/applied_sha" "$SHA_A"
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "reset --keep, never --hard" called "git -C $REPO reset --keep $SHA_A"
  check "no hard reset" not_called "reset --hard"
  check "migration count before" eq "$(field migrations_before)" 1
  check "migration count grew" eq "$(field migrations_after)" 2
  check "db restored" eq "$(field db_restored)" true
  check "restores into a fresh database" called 'CREATE DATABASE "domovoi_restore_'
  check "pg_restore into it" called "pg_restore -U domovoi -d domovoi_restore_"
  check "swaps it in by rename" called 'RENAME TO "domovoi"'
  check "live database back to one migration" file_is "$STATE/db-domovoi" 1
  check "failed database kept aside" eq "$(ls "$STATE" | grep -c '^db-domovoi_failed_')" 1
  check "restore happens with core stopped" before "reset --keep" "pg_restore -U domovoi -d"
  check "re-syncs deps after the reset" again_after "reset --keep" "pip --disable-pip-version-check --no-input install -e"
  check "restores exact pins" called "pip-r-file: requests==2.31.0 torch==2.14.0+cpu"
  check "pins skip direct references" not_called "localthing @"
  check "pins can reach the CPU torch index" called "--extra-index-url https://download.pytorch.org/whl/cpu -r"
  check "health re-checked after the rollback" test "$(grep -c 'curl http://127.0.0.1:6370/v1/health' "$STATE/calls.log")" -gt 1
  check "error names the failing step" eq "$(field error | grep -c 'failed at health')" 1
  end_case
}

case_flyway_failure_without_growth() {
  new_case flyway_failure_without_growth
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'CREATE TABLE broken (;\n' >"$REPO/domovoi/db/migrations/V002__broken.sql"
  local sha_b; sha_b=$(commit_all "B: bad migration")
  # Flyway fails on B's migration set; the rollback's run on A's succeeds.
  echo "$sha_b" >"$STATE/flyway-fail-when-head"
  run_update
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "failed at migrate" eq "$(field error | grep -c 'failed at migrate')" 1
  check "no restore when nothing was applied" eq "$(field db_restored)" false
  check "no pg_restore into a database" not_called "pg_restore -U domovoi -d"
  check "core never started on the bad SHA" eq "$(grep -c 'systemctl start domovoi-core.service' "$STATE/calls.log")" 1
  check "bad_sha recorded" file_is "$UPD/bad_sha" "$sha_b"
  end_case
}

case_plugin_migration_health_failure_restores() {
  new_case plugin_migration_health_failure_restores
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  # No core migration: only the bundled plugin's ledger moves, and only
  # when the new core boots (its migration catch-up), after Flyway ran.
  printf 'ALTER TABLE stations ADD COLUMN played_at timestamptz;\n' >"$REPO/plugins/radio/migrations/V002__played.sql"
  printf 'raise SystemExit("broken")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: plugin migration + broken code")
  echo "$sha_b" >"$STATE/curl-fail-when-head"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "flyway count before" eq "$(field migrations_before)" 1
  check "flyway count did not move" eq "$(field migrations_after)" 1
  check "plugin ledgers before" eq "$(field plugin_migrations_before)" 1
  check "plugin ledgers grew" eq "$(field plugin_migrations_after)" 2
  check "db restored for the plugin migration alone" eq "$(field db_restored)" true
  check "restores into a fresh database" called 'CREATE DATABASE "domovoi_restore_'
  check "swaps it in by rename" called 'RENAME TO "domovoi"'
  check "plugin ledger back to one row" file_is "$STATE/ledgers-domovoi" "plugin_radio1"
  check "flyway untouched by the restore" file_is "$STATE/db-domovoi" 1
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "restore happens with core stopped" before "reset --keep" "pg_restore -U domovoi -d"
  # The same migration reached the test twin; it goes back too.
  check "test twin restored" eq "$(field test_db_restored)" true
  check "test twin restored into a fresh database" called 'CREATE DATABASE "domovoi_test_restore_'
  check "test twin swapped in by rename" called 'RENAME TO "domovoi_test"'
  check "test twin ledger back to one row" file_is "$STATE/ledgers-domovoi_test" "plugin_radio1"
  check "replaced test twin kept aside" eq "$(ls "$STATE" | grep -c '^db-domovoi_test_failed_')" 1
  check "test twin restored from its own dump" called "pg_restore -U domovoi -d domovoi_test_restore_"
  end_case
}

case_test_twin_restored_on_its_own() {
  new_case test_twin_restored_on_its_own
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  # The main database already had V002 (an earlier catch-up reached it but
  # not the twin), so only the twin's ledger moves on B's boot.
  echo "plugin_radio 2" >"$STATE/ledgers-domovoi"
  printf 'ALTER TABLE stations ADD COLUMN played_at timestamptz;\n' >"$REPO/plugins/radio/migrations/V002__played.sql"
  local sha_b; sha_b=$(commit_all "B: plugin migration + broken code")
  echo "$sha_b" >"$STATE/curl-fail-when-head"
  run_update
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "main database left alone" eq "$(field db_restored)" false
  check "no restore into the main database" not_called 'CREATE DATABASE "domovoi_restore_'
  check "test twin restored" eq "$(field test_db_restored)" true
  check "test twin ledger back to one row" file_is "$STATE/ledgers-domovoi_test" "plugin_radio1"
  check "main ledger untouched" file_is "$STATE/ledgers-domovoi" "plugin_radio2"
  end_case
}

case_no_test_twin() {
  new_case no_test_twin
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  rm -f "$STATE/db-domovoi_test" "$STATE/ledgers-domovoi_test"
  printf 'ALTER TABLE stations ADD COLUMN played_at timestamptz;\n' >"$REPO/plugins/radio/migrations/V002__played.sql"
  local sha_b; sha_b=$(commit_all "B: plugin migration + broken code")
  echo "$sha_b" >"$STATE/curl-fail-when-head"
  run_update
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "asked whether the twin exists" called "SELECT 1 FROM pg_database WHERE datname = 'domovoi_test'"
  check "no dump of a missing twin" not_called "pg_dump -Fc -U domovoi domovoi_test"
  check "no test backup" eq "$(field test_backup)" null
  check "main database still backed up" eq "$(ls "$UPD/backups" | grep -c 'Z\.dump$')" 1
  check "main database still restored" eq "$(field db_restored)" true
  check "no test restore" eq "$(field test_db_restored)" false
  check "no twin created by the restore" test ! -f "$STATE/db-domovoi_test"
  end_case
}

case_test_twin_backup_failure_aborts() {
  new_case test_twin_backup_failure_aborts
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  : >"$STATE/fail-pg_dump-domovoi_test"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status aborted" eq "$(field status)" '"aborted"'
  check "error says the backup failed" eq "$(field error | grep -c 'pre-update backup failed')" 1
  check "nothing stopped" not_called "systemctl stop"
  check "no partial test dump left" eq "$(ls "$UPD/backups" | grep -c 'partial')" 0
  check "HEAD untouched" eq "$(g rev-parse HEAD)" "$sha_b"
  end_case
}

case_plugin_migration_kept_when_healthy() {
  new_case plugin_migration_kept_when_healthy
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'ALTER TABLE stations ADD COLUMN played_at timestamptz;\n' >"$REPO/plugins/radio/migrations/V002__played.sql"
  local sha_b; sha_b=$(commit_all "B: plugin migration")
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "plugin ledgers reported" eq "$(field plugin_migrations_after)" 2
  check "no restore on success" eq "$(field db_restored)" false
  check "no pg_restore into a database" not_called "pg_restore -U domovoi -d"
  check "the new plugin migration stays" file_is "$STATE/ledgers-domovoi" "plugin_radio2"
  check "and stays in the test twin" file_is "$STATE/ledgers-domovoi_test" "plugin_radio2"
  check "no test restore on success" eq "$(field test_db_restored)" false
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  end_case
}

case_plugin_switched_off_by_load_error_rolls_back() {
  new_case plugin_switched_off_by_load_error_rolls_back
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  mkdir -p "$REPO/plugins/radio/radio"
  printf 'import boom\n' >"$REPO/plugins/radio/radio/__init__.py"
  local sha_b; sha_b=$(commit_all "B: radio imports a missing module")
  # Core and web come up fine on B; only the plugin fails, and the loader
  # switches it off.
  echo "radio $sha_b import" >"$STATE/plugin-fail-when-head"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "health passed on B" step_is health ok
  check "then the plugin check failed" step_is plugins failed
  check "error names the step" eq "$(field error | grep -c 'failed at plugins')" 1
  check "error names the plugin and its error" \
    eq "$(field error | grep -c "radio: import failed: No module named 'boom'")" 1
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "switched back on" step_is reenable-plugins ok
  check "only the rows a load error switched off" \
    called "slug IN ('radio')"
  check "switched on with the core stopped" before "reset --keep" "UPDATE plugins SET enabled = true"
  check "before the previous SHA boots" again_after "UPDATE plugins SET enabled = true" "systemctl start domovoi-core.service"
  check "loads again on A" file_is "$STATE/registry-domovoi" "radio|t|ok|"
  check "plugins re-checked after the rollback" step_is rollback-plugins ok
  check "nothing grew, nothing restored" eq "$(field db_restored)" false
  check "bad_sha recorded" file_is "$UPD/bad_sha" "$sha_b"
  check "applied_sha stays A" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_enabled_plugin_at_load_error_rolls_back() {
  new_case enabled_plugin_at_load_error_rolls_back
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'ALTER TABLE stations ADD COLUMN played_at timestamptz;\n' >"$REPO/plugins/radio/migrations/V002__played.sql"
  local sha_b; sha_b=$(commit_all "B: plugin migration that fails")
  # The catch-up fails: the plugin stays switched on, at load_error.
  echo "radio $sha_b catchup" >"$STATE/plugin-fail-when-head"
  run_update
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "failed at plugins" eq "$(field error | grep -c 'failed at plugins')" 1
  check "the error keeps a | in last_error" eq "$(field error | grep -c 'V002__played.sql failed | at line 1')" 1
  check "still on, so nothing to switch on" not_called "UPDATE plugins"
  check "nothing applied, nothing restored" eq "$(field db_restored)" false
  check "test twin not restored either" eq "$(field test_db_restored)" false
  check "loads again on A" file_is "$STATE/registry-domovoi" "radio|t|ok|"
  check "plugins re-checked after the rollback" step_is rollback-plugins ok
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  end_case
}

case_plugin_broken_before_does_not_block() {
  new_case plugin_broken_before_does_not_block
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  # radio was already failing on A and still fails on B; sleep was switched
  # off by hand. Neither is this update's doing.
  printf 'radio|t|load_error|V002 failed before\nsleep|f|ok|\n' >"$STATE/registry-domovoi"
  printf 'radio %s catchup\nradio %s catchup\n' "$SHA_A" "$sha_b" >"$STATE/plugin-fail-when-head"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "plugin check passed" step_is plugins ok
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  check "the disabled plugin stays off" eq "$(grep -c '^sleep|f|' "$STATE/registry-domovoi")" 1
  check "nothing switched on" not_called "UPDATE plugins"
  end_case
}

case_rollback_cannot_bring_a_plugin_back() {
  new_case rollback_cannot_bring_a_plugin_back
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  # Loaded before the update, but fails on B and then on A as well.
  printf 'radio %s import\nradio %s import\n' "$sha_b" "$SHA_A" >"$STATE/plugin-fail-when-head"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status rollback_failed" eq "$(field status)" '"rollback_failed"'
  check "names the rollback's plugin check" \
    eq "$(field error | grep -c 'the rollback then failed at rollback-plugins: rollback-plugins failed')" 1
  check "it did switch the plugin back on first" step_is reenable-plugins ok
  check "rollback health itself passed" step_is rollback-health ok
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "applied_sha not moved" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_dirty_tree_refused() {
  new_case dirty_tree_refused
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  printf 'print("local tweak")\n' >"$REPO/domovoi/app.py"
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status refused" eq "$(field status)" '"refused"'
  check "error explains why" eq "$(field error | grep -c 'uncommitted changes')" 1
  check "names the file" eq "$(field error | grep -c 'domovoi/app.py')" 1
  check "nothing stopped" not_called "systemctl stop"
  check "nothing started" not_called "systemctl start"
  check "no backup taken" not_called "pg_dump"
  check "HEAD untouched" eq "$(g rev-parse HEAD)" "$sha_b"
  check "local change preserved" eq "$(cat "$REPO/domovoi/app.py")" 'print("local tweak")'
  check "applied_sha untouched" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_untracked_files_do_not_block() {
  new_case untracked_files_do_not_block
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  printf 'scratch\n' >"$REPO/notes.txt"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "untracked file kept" eq "$(cat "$REPO/notes.txt")" scratch
  end_case
}

case_backup_failure_aborts() {
  new_case backup_failure_aborts
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  : >"$STATE/fail-pg_dump"
  run_update
  check "status aborted" eq "$(field status)" '"aborted"'
  check "nothing stopped" not_called "systemctl stop"
  check "no partial dump left" eq "$(ls "$UPD/backups" 2>/dev/null | wc -l | tr -d ' ')" 0
  end_case
}

case_prev_from_core_pull_record() {
  new_case prev_from_core_pull_record
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  echo "$SHA_A" >"$CORE_STATE/prev_sha"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "mode update" eq "$(field mode)" '"update"'
  check "prev from the core's pull record" eq "$(field prev_source)" '"pull"'
  check "from A" eq "$(field from_sha)" "\"$SHA_A\""
  check "applied_sha recorded" file_is "$UPD/applied_sha" "$sha_b"
  end_case
}

case_prev_from_orig_head() {
  new_case prev_from_orig_head
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  g update-ref ORIG_HEAD "$SHA_A"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "prev from ORIG_HEAD" eq "$(field prev_source)" '"orig_head"'
  check "from A" eq "$(field from_sha)" "\"$SHA_A\""
  end_case
}

case_garbage_prev_record_ignored() {
  new_case garbage_prev_record_ignored
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  printf 'not-a-sha; rm -rf /\n' >"$CORE_STATE/prev_sha"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "garbage record ignored" eq "$(field prev_source)" '"head"'
  check "garbage never echoed" not_called "rm -rf"
  end_case
}

case_backups_pruned() {
  new_case backups_pruned
  mkdir -p "$UPD/backups" && echo "$SHA_A" >"$UPD/applied_sha"
  local i
  for i in 1 2 3; do
    echo old >"$UPD/backups/pre-00000000000$i-2026010${i}T000000Z.dump"
    touch -d "2026-01-0$i" "$UPD/backups/pre-00000000000$i-2026010${i}T000000Z.dump"
  done
  # Test-twin dumps are their own series: these three don't push any main
  # dump out, and only one of them survives next to the new one.
  for i in 4 5 6; do
    echo old >"$UPD/backups/pre-00000000000$i-2026010${i}T000000Z.test.dump"
    touch -d "2026-01-0$i" "$UPD/backups/pre-00000000000$i-2026010${i}T000000Z.test.dump"
  done
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  KEEP_BACKUPS=2 run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "keeps the newest two" eq "$(ls "$UPD/backups" | grep -c 'Z\.dump$')" 2
  check "the new dump survives" eq "$(ls "$UPD/backups" | grep -c "^pre-$(g rev-parse HEAD | cut -c1-12)-.*Z\.dump$")" 1
  check "the newest old dump survives" eq "$(ls "$UPD/backups" | grep -c '^pre-000000000003-')" 1
  check "keeps the newest two test dumps" eq "$(ls "$UPD/backups" | grep -c 'Z\.test\.dump$')" 2
  check "the new test dump survives" eq "$(ls "$UPD/backups" | grep -c "^pre-$(g rev-parse HEAD | cut -c1-12)-.*Z\.test\.dump$")" 1
  check "the newest old test dump survives" eq "$(ls "$UPD/backups" | grep -c '^pre-000000000006-')" 1
  end_case
}

case_noop_health_failure_reports() {
  new_case noop_health_failure_reports
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  : >"$STATE/curl-fail-always"
  run_update
  check "status failed" eq "$(field status)" '"failed"'
  check "no rollback on a plain restart" not_called "reset --keep"
  check "no bad_sha" eq "$(field bad_sha)" null
  check "an unhealthy run never touches the search helper" not_called "domovoi.egress"
  end_case
}

case_rollback_that_cannot_get_healthy() {
  new_case rollback_that_cannot_get_healthy
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  : >"$STATE/curl-fail-always"          # neither B nor A comes back up
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status rollback_failed" eq "$(field status)" '"rollback_failed"'
  check "names the rollback step and its own error" \
    eq "$(field error | grep -c 'the rollback then failed at rollback-health: rollback-health failed')" 1
  check "checkout still went back to A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "bad_sha recorded" file_is "$UPD/bad_sha" "$sha_b"
  check "applied_sha not moved" file_is "$UPD/applied_sha" "$SHA_A"
  check "services started again anyway" again_after "reset --keep" "systemctl start domovoi-core.service"
  end_case
}

case_sync_failure_output_stays_valid_utf8() {
  new_case sync_failure_output_stays_valid_utf8
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\ndependencies = ["nope"]\n' >"$REPO/pyproject.toml"
  local sha_b; sha_b=$(commit_all "B: unresolvable deps")
  echo "$sha_b" >"$STATE/pip-fail-when-head"
  # pip's own error art, long enough that a byte-counting cut(1) lands
  # inside a three-byte character, plus a quote and a backslash for the
  # JSON escaping. valid_json then reads the result as strict UTF-8.
  { printf 'error: resolution-impossible "quoted" C:\\path\n'
    printf 'x'; for _ in $(seq 150); do printf '\342\224\200'; done; printf '\n'
    printf '\303\227 No matching distribution found for nope \342\225\260\342\224\200> see above\n'
  } >"$STATE/pip-fail-output"
  run_update
  check "status rolled_back" eq "$(field status)" '"rolled_back"'
  check "failed at sync-deps" eq "$(field error | grep -c 'failed at sync-deps')" 1
  check "checkout back at A" eq "$(g rev-parse HEAD)" "$SHA_A"
  check "rollback re-synced A's deps" again_after "reset --keep" "install -e"
  end_case
}

case_clock_stepped_back_keeps_the_result_valid() {
  new_case clock_stepped_back_keeps_the_result_valid
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  # A wall clock that NTP steps back 90 s after the run's second reading
  # (START_MS, then the preflight's t0): the preflight and the whole run
  # both end "before" they started. The script reads bash's clock, which
  # no shim can move, so this run goes without it and reads date(1): 13
  # digits of milliseconds, taken from the shim's own bash clock (the real
  # date may be uutils, whose +%s%3N is no use).
  mkdir -p "$CASE/clockbin"
  cat >"$CASE/clockbin/date" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = +%s%3N ]; then
  n=$(cat "$SHIM_STATE/date-readings" 2>/dev/null || echo 0)
  echo $((n + 1)) >"$SHIM_STATE/date-readings"
  t=$EPOCHREALTIME
  t=$(( ${t%[.,]*}${t#*[.,]} / 1000 ))
  if [ "$n" -ge 2 ]; then t=$((t - 90000)); fi
  echo "$t"
  exit 0
fi
exec "$SHIM_REAL_DATE" "$@"
SH
  chmod +x "$CASE/clockbin/date"
  NO_BASH_CLOCK=1 run_update "$CASE/clockbin"
  check "the clock did step back" eq "$(( $(cat "$STATE/date-readings" 2>/dev/null || echo 0) > 2 ))" 1
  check "status ok" eq "$(field status)" '"ok"'
  check "no negative duration" eq "$(grep -c '"duration_sec": -' "$RESULT")" 0
  check "no half-negative duration" eq "$(grep -c '[0-9]\.-' "$RESULT")" 0
  check "the run's duration reads 0" eq "$(field duration_sec)" 0.000
  check "so does the preflight's" step_is_timed preflight 0.000
  end_case
}

# A date(1) like uutils 0.8.0's, Ubuntu 26.04's coreutils: +%s%3N ignores
# the 3 and prints the nanoseconds unpadded ("1790745144" then "1476483"),
# 11 to 19 digits in all. Every call is counted.
write_uutils_date() {
  mkdir -p "$1"
  cat >"$1/date" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = +%s%3N ]; then
  echo x >>"$SHIM_STATE/uutils-3n-calls"
  t=$EPOCHREALTIME
  echo "${t%[.,]*}$(( 10#${t#*[.,]} * 1000 + RANDOM % 1000 ))"
  exit 0
fi
exec "$SHIM_REAL_DATE" "$@"
SH
  chmod +x "$1/date"
}

case_uutils_date_leaves_the_durations_alone() {
  new_case uutils_date_leaves_the_durations_alone
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  write_uutils_date "$CASE/uutilsbin"
  local t0=$SECONDS
  run_update "$CASE/uutilsbin"
  local wall=$((SECONDS - t0 + 1))
  check "status ok" eq "$(field status)" '"ok"'
  check "the run is timed by bash's clock, not date +%s%3N" eq "$(wc -l 2>/dev/null <"$STATE/uutils-3n-calls" || echo 0)" 0
  check "every duration is real" durations_within "$wall" 4
  end_case
}

case_uutils_date_without_bash_clock_falls_back_to_seconds() {
  new_case uutils_date_without_bash_clock_falls_back_to_seconds
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  write_uutils_date "$CASE/uutilsbin"
  local t0=$SECONDS
  NO_BASH_CLOCK=1 run_update "$CASE/uutilsbin"
  local wall=$((SECONDS - t0 + 1))
  check "status ok" eq "$(field status)" '"ok"'
  check "date +%s%3N was asked" eq "$(( $(wc -l 2>/dev/null <"$STATE/uutils-3n-calls" || echo 0) > 2 ))" 1
  check "every duration is real" durations_within "$wall" 4
  check "and in whole seconds" eq "$(grep -o '"duration_sec": [0-9]*\.[0-9]*' "$RESULT" | grep -vc '\.000$')" 0
  end_case
}

# now_ms and fmt_sec on their own, out of the script.
case_timing_helpers() {
  new_case timing_helpers
  mkdir -p "$CASE/fakebin"
  cat >"$CASE/fakebin/date" <<'SH'
#!/usr/bin/env bash
case "${1-}" in
  +%s%3N) printf '%s\n' "$FAKE_MS" ;;
  +%s) echo 1790745144 ;;
  *) exit 1 ;;
esac
SH
  chmod +x "$CASE/fakebin/date"
  local out
  out=$(
    eval "$(sed -n '/^now_ms() {/,/^}/p;/^fmt_sec() {/,/^}/p' "$SCRIPT")"
    PATH=$CASE/fakebin:$PATH
    FAKE_MS=1
    export FAKE_MS
    [[ $(now_ms) =~ ^[0-9]{13}$ ]] && echo "bash clock: 13 digits"
    # Unset, it is an ordinary variable, and each value below is kept.
    unset EPOCHREALTIME
    for EPOCHREALTIME in 1790745131.686341 1790745131,686341 1790745131.000999 1790745131.5; do
      echo "EPOCHREALTIME=$EPOCHREALTIME: $(now_ms)"
    done
    unset EPOCHREALTIME
    for FAKE_MS in 1790745131686 17907451441476483 1790745131688765458 179074513168 1790745131%3N ''; do
      echo "date +%s%3N=$FAKE_MS: $(now_ms)"
    done
    for ms in 0 7 1234 101766 -90412 -1 abc ''; do echo "fmt_sec $ms: $(fmt_sec "$ms")"; done
  )
  check "now_ms and fmt_sec" eq "$out" "bash clock: 13 digits
EPOCHREALTIME=1790745131.686341: 1790745131686
EPOCHREALTIME=1790745131,686341: 1790745131686
EPOCHREALTIME=1790745131.000999: 1790745131000
EPOCHREALTIME=1790745131.5: 1790745144000
date +%s%3N=1790745131686: 1790745131686
date +%s%3N=17907451441476483: 1790745144000
date +%s%3N=1790745131688765458: 1790745144000
date +%s%3N=179074513168: 1790745144000
date +%s%3N=1790745131%3N: 1790745144000
date +%s%3N=: 1790745144000
fmt_sec 0: 0.000
fmt_sec 7: 0.007
fmt_sec 1234: 1.234
fmt_sec 101766: 101.766
fmt_sec -90412: 0.000
fmt_sec -1: 0.000
fmt_sec abc: 0.000
fmt_sec : 0.000"
  end_case
}

case_venv_from_the_core_unit() {
  new_case venv_from_the_core_unit
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  write_venv "$CASE/unit-venv"
  : >"$CASE/unit-venv/pyvenv.cfg"
  printf '{ path=%s/unit-venv/bin/python ; argv[]=%s/unit-venv/bin/python -m domovoi.main ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }\n' \
    "$CASE" "$CASE" >"$STATE/show-execstart"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  NO_VENV_ENV=1 run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "asked the core unit" called "systemctl show -p ExecStart --value domovoi-core.service"
  check "pip ran (from the unit's venv)" eq "$(grep -c '^pip .*install -e' "$STATE/calls.log")" 1
  end_case
}

case_venv_ignores_a_non_venv_interpreter() {
  new_case venv_ignores_a_non_venv_interpreter
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m domovoi.main ; ignore_errors=no }\n' >"$STATE/show-execstart"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  NO_VENV_ENV=1 run_update
  check "aborted before anything changed" eq "$(field status)" '"aborted"'
  check "nothing stopped" not_called "systemctl stop"
  check "no backup taken" not_called "pg_dump"
  check "pip never ran" not_called "pip "
  check "no bad_sha" eq "$(field bad_sha)" null
  check "names the missing venv" eq "$(field error | grep -c "no venv interpreter at $REPO/.venv/bin/python")" 1
  end_case
}

case_venv_not_writable_aborts() {
  new_case venv_not_writable_aborts
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  local sha_b; sha_b=$(commit_all "B: deps")
  write_root_shims "$CASE/rootbin"
  # The venv's site-packages belongs to someone else: `test -w` as the
  # service user says no for it (and only it).
  cat >"$CASE/rootbin/test" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -w ] && [ "${2-}" = "$(cat "$SHIM_STATE/not-writable")" ]; then exit 1; fi
exec /usr/bin/test "$@"
SH
  chmod +x "$CASE/rootbin/test"
  echo "$CASE/venv/lib/site-packages" >"$STATE/not-writable"
  run_update "$CASE/rootbin"
  check "exit non-zero" test "$RC" -ne 0
  check "status aborted" eq "$(field status)" '"aborted"'
  check "error names the dir and the user" \
    eq "$(field error | grep -c "$CASE/venv/lib/site-packages is not writable by tester")" 1
  check "asked as the service user" called "runuser -u tester -- test -w $CASE/venv/lib/site-packages"
  check "nothing stopped" not_called "systemctl stop"
  check "no backup taken" not_called "pg_dump"
  check "HEAD untouched" eq "$(g rev-parse HEAD)" "$sha_b"
  check "no bad_sha" eq "$(field bad_sha)" null
  check "applied_sha untouched" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

# ─── the stop step (2026-09-30: a core that swallowed SIGTERM cost every
# update 90 s, recorded only as a slow stop-services step) ────────────────

case_stop_detail_says_how_each_unit_stopped() {
  new_case stop_detail_says_how_each_unit_stopped
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'Result=success\nActiveExitTimestampMonotonic=1000000\nInactiveEnterTimestampMonotonic=1412000\n' \
    >"$STATE/show-domovoi-web.service"
  printf 'Result=timeout\nActiveExitTimestampMonotonic=5000000\nInactiveEnterTimestampMonotonic=95457000\n' \
    >"$STATE/show-domovoi-core.service"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "stop step ok" step_is stop-services ok
  check "each unit's stop time, and systemd's kill, in the detail" grep -qF \
    '"detail": "domovoi-web.service 0.412 s; domovoi-core.service 90.457 s (systemd SIGKILLed it after TimeoutStopSec)"' \
    "$RESULT"
  check "nothing killed by the script" not_called "systemctl kill"
  end_case
}

case_a_unit_already_down_is_said_to_be() {
  new_case a_unit_already_down_is_said_to_be
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  : >"$STATE/stopped-domovoi-web.service"
  printf 'Result=success\nActiveExitTimestampMonotonic=1000000\nInactiveEnterTimestampMonotonic=1250000\n' \
    >"$STATE/show-domovoi-core.service"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "detail" grep -qF \
    '"detail": "domovoi-web.service was not running; domovoi-core.service 0.250 s"' "$RESULT"
  end_case
}

case_a_hung_stop_is_killed_and_the_restart_goes_on() {
  new_case a_hung_stop_is_killed_and_the_restart_goes_on
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo domovoi-core.service >"$STATE/stop-hangs"
  STOP_TIMEOUT=1 run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "stop step ok" step_is stop-services ok
  check "SIGKILLs the core" called "systemctl kill --signal=SIGKILL domovoi-core.service"
  check "leaves the web alone" not_called "systemctl kill --signal=SIGKILL domovoi-web.service"
  check "says so in the detail" grep -qF \
    'domovoi-core.service (still stopping after 1s: SIGKILLed by this script)' "$RESULT"
  check "then starts" again_after "systemctl kill" "systemctl start domovoi-core.service domovoi-web.service"
  check "the stop step is bounded" step_took_under stop-services 5
  end_case
}

case_a_hung_stop_during_an_update_still_updates() {
  new_case a_hung_stop_during_an_update_still_updates
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'CREATE TABLE b (id int);\n' >"$REPO/domovoi/db/migrations/V002__b.sql"
  local sha_b; sha_b=$(commit_all "B: migration")
  echo domovoi-core.service >"$STATE/stop-hangs"
  STOP_TIMEOUT=1 run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mode update" eq "$(field mode)" '"update"'
  check "backed up first" before "pg_dump" "systemctl kill --signal=SIGKILL domovoi-core.service"
  check "migrated after the kill" again_after "systemctl kill" "systemctl restart domovoi-db.service"
  check "no rollback" not_called "reset --keep"
  check "applied_sha moved" file_is "$UPD/applied_sha" "$sha_b"
  end_case
}

case_a_unit_that_survives_sigkill_fails_the_stop() {
  new_case a_unit_that_survives_sigkill_fails_the_stop
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo domovoi-core.service >"$STATE/stop-hangs"
  echo domovoi-core.service >"$STATE/kill-fails"
  STOP_TIMEOUT=1 run_update
  check "exit 1" eq "$RC" 1
  check "status failed" eq "$(field status)" '"failed"'
  check "stop step failed" step_is stop-services failed
  check "error names the unit" grep -qF 'still running after SIGKILL: domovoi-core.service' "$RESULT"
  check "still tries to bring the house back" called "systemctl start domovoi-core.service domovoi-web.service"
  check "bounded even so" step_took_under stop-services 6
  end_case
}

# ─── the search helper (SearXNG) follows the internet answer ─────────────

case_searxng_started_after_a_healthy_restart() {
  new_case searxng_started_after_a_healthy_restart
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo always >"$STATE/internet-policy"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "reads the answer the core's way" called "python -m domovoi.egress --print-policy"
  check "starts the search helper" called "docker compose -f $REPO/domovoi/docker-compose.yml --project-directory $REPO/domovoi up -d --no-deps searxng"
  check "only after health" before "curl http://127.0.0.1:6369/api/health" "docker compose"
  check "a searxng step, ok" step_is searxng ok
  check "applied_sha unchanged" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_searxng_failure_never_fails_an_update() {
  new_case searxng_failure_never_fails_an_update
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  echo sometimes >"$STATE/internet-policy"
  : >"$STATE/fail-docker-compose"
  run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "mode update" eq "$(field mode)" '"update"'
  check "no error" eq "$(field error)" null
  check "tried to start it" called "up -d --no-deps searxng"
  check "only after health" before "curl http://127.0.0.1:6369/api/health" "docker compose"
  check "recorded as a warning" step_is searxng warn
  check "never as a failure" eq "$(grep -c '"name": "searxng", "status": "failed"' "$RESULT")" 0
  check "the reason is kept" eq "$(grep -c 'pull access denied' "$RESULT")" 1
  check "no rollback" not_called "reset --keep"
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  end_case
}

case_searxng_stopped_for_never() {
  new_case searxng_stopped_for_never
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo never >"$STATE/internet-policy"
  echo true >"$STATE/searxng-running"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "asks whether it runs" called "docker inspect -f {{.State.Running}} domovoi-searxng"
  check "stops it" called "docker stop domovoi-searxng"
  check "never starts it" not_called "docker compose"
  check "a searxng step, ok" step_is searxng ok
  end_case
}

case_searxng_not_running_for_never_is_left_alone() {
  new_case searxng_not_running_for_never_is_left_alone
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo never >"$STATE/internet-policy"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "no stop for a container that isn't running" not_called "docker stop"
  check "a searxng step, ok" step_is searxng ok
  end_case
}

case_searxng_left_alone_when_unanswered() {
  new_case searxng_left_alone_when_unanswered
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo true >"$STATE/searxng-running"
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "no compose" not_called "docker compose"
  check "no stop" not_called "docker stop"
  check "a searxng step, ok" step_is searxng ok
  check "says why" eq "$(grep -c "the internet question isn't answered" "$CASE/output.log")" 1
  end_case
}

case_searxng_opt_out_and_an_older_checkout() {
  new_case searxng_opt_out_and_an_older_checkout
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo always >"$STATE/internet-policy"
  MANAGE_SEARXNG=0 run_update
  check "opted out: status ok" eq "$(field status)" '"ok"'
  check "opted out: no compose" not_called "docker compose"
  check "opted out: the answer isn't even read" not_called "domovoi.egress"
  : >"$STATE/egress-missing"
  run_update
  check "older checkout: status ok" eq "$(field status)" '"ok"'
  check "older checkout: no compose" not_called "docker compose"
  check "older checkout: a searxng step, ok" step_is searxng ok
  end_case
}

# 2026-10-03 review: the first start pulls ~375 MB. Unbounded, it held the
# update open for the whole pull, and the unit's TimeoutStartSec could kill
# the run into a "failed" result after core and web were already healthy.
case_searxng_hung_start_is_bounded_and_the_update_stays_ok() {
  new_case searxng_hung_start_is_bounded_and_the_update_stays_ok
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  local sha_b; sha_b=$(commit_all "B: code")
  echo always >"$STATE/internet-policy"
  : >"$STATE/hang-docker-compose"
  SEARXNG_TIMEOUT=2 run_update
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "no error" eq "$(field error)" null
  check "tried to start it" called "up -d --no-deps searxng"
  check "a warning, not a failure" step_is searxng warn
  check "the warning says why" eq "$(grep -c 'did not start within 2s' "$RESULT")" 1
  check "bounded: the step took under 20 s" step_took_under searxng 20
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  check "no rollback" not_called "reset --keep"
  end_case
}

write_systemd_run() {
  local bin=$1
  mkdir -p "$bin"
  cat >"$bin/systemd-run" <<'SH'
#!/usr/bin/env bash
echo "systemd-run $*" >>"$SHIM_STATE/calls.log"
exit 0
SH
  chmod +x "$bin/systemd-run"
}

case_searxng_start_detached_under_systemd() {
  new_case searxng_start_detached_under_systemd
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo sometimes >"$STATE/internet-policy"
  write_systemd_run "$CASE/sdbin"
  SEARXNG_DETACH=1 run_update "$CASE/sdbin"
  check "exit 0" eq "$RC" 0
  check "status ok" eq "$(field status)" '"ok"'
  check "handed to a transient unit" called "systemd-run --no-block --collect --quiet --unit=domovoi-searxng-start docker compose -f $REPO/domovoi/docker-compose.yml --project-directory $REPO/domovoi up -d --no-deps searxng"
  check "the update did not run compose itself" eq "$(grep -c '^docker compose' "$STATE/calls.log")" 0
  check "a searxng step, ok" step_is searxng ok
  check "says it starts in the background" eq "$(grep -c 'in the background' "$RESULT")" 1
  end_case
}

# 2026-10-03 review: under INTERNET_ACCESS=never the update unit must not
# go online behind the answer's back (pip, docker build).
case_never_refuses_a_dependency_update() {
  new_case never_refuses_a_dependency_update
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo never >"$STATE/internet-policy"
  printf '[project]\nname = "domovoi"\nversion = "1"\ndependencies = ["httpx"]\n' >"$REPO/pyproject.toml"
  local sha_b; sha_b=$(commit_all "B: deps")
  run_update
  check "exit non-zero" test "$RC" -ne 0
  check "status aborted" eq "$(field status)" '"aborted"'
  check "says the internet is off" eq "$(field error | grep -c 'internet access is turned off for this box')" 1
  check "names the dependencies" eq "$(field error | grep -c 'Python dependencies')" 1
  check "nothing stopped" not_called "systemctl stop"
  check "no pip" not_called "pip "
  check "no backup taken" not_called "pg_dump"
  check "HEAD untouched" eq "$(g rev-parse HEAD)" "$sha_b"
  check "applied_sha untouched" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_never_refuses_a_music_image_change() {
  new_case never_refuses_a_music_image_change
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo never >"$STATE/internet-policy"
  printf 'FROM debian:trixie-slim\n' >"$REPO/domovoi/Dockerfile.mpd"
  commit_all "B: mpd image" >/dev/null
  run_update
  check "status aborted" eq "$(field status)" '"aborted"'
  check "names the image" eq "$(field error | grep -c 'music player image')" 1
  check "no docker build" not_called "docker build"
  check "nothing stopped" not_called "systemctl stop"
  end_case
}

case_never_mpd_conf_only_keeps_the_image() {
  new_case never_mpd_conf_only_keeps_the_image
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  echo never >"$STATE/internet-policy"
  printf 'music_directory "/music"\naudio_output { type "httpd" }\n' >"$REPO/domovoi/mpd.conf"
  local sha_b; sha_b=$(commit_all "B: mpd.conf")
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "no docker build" not_called "docker build"
  check "rooms recreated for the new conf" called "docker rm -f domovoi-mpd-kitchen"
  check "says why" eq "$(grep -c 'keeping the domovoi-mpd:latest image' "$CASE/output.log")" 1
  check "applied_sha moves to B" file_is "$UPD/applied_sha" "$sha_b"
  end_case
}

case_unanswered_dependency_update_reads_the_answer_once() {
  new_case unanswered_dependency_update_reads_the_answer_once
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  printf '[project]\nname = "domovoi"\nversion = "1"\n' >"$REPO/pyproject.toml"
  commit_all "B: deps" >/dev/null
  run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "syncs as before" called "install -e"
  check "the answer was read before stopping anything" before "domovoi.egress --print-policy" "systemctl stop"
  end_case
}

case_noop_restart
case_noop_without_any_history
case_noop_restart_migrates_a_database_behind_the_checkout
case_noop_restart_brings_postgres_back
case_noop_restart_a_baseline_row_is_not_a_migration
case_deps_changed_as_root
case_mpd_changed
case_mpd_conf_only
case_migration_health_failure_rolls_back
case_flyway_failure_without_growth
case_plugin_migration_health_failure_restores
case_plugin_migration_kept_when_healthy
case_test_twin_restored_on_its_own
case_no_test_twin
case_test_twin_backup_failure_aborts
case_plugin_switched_off_by_load_error_rolls_back
case_enabled_plugin_at_load_error_rolls_back
case_plugin_broken_before_does_not_block
case_rollback_cannot_bring_a_plugin_back
case_dirty_tree_refused
case_untracked_files_do_not_block
case_backup_failure_aborts
case_prev_from_core_pull_record
case_prev_from_orig_head
case_garbage_prev_record_ignored
case_backups_pruned
case_noop_health_failure_reports
case_rollback_that_cannot_get_healthy
case_sync_failure_output_stays_valid_utf8
case_clock_stepped_back_keeps_the_result_valid
case_uutils_date_leaves_the_durations_alone
case_uutils_date_without_bash_clock_falls_back_to_seconds
case_timing_helpers
case_venv_from_the_core_unit
case_venv_ignores_a_non_venv_interpreter
case_venv_not_writable_aborts
case_stop_detail_says_how_each_unit_stopped
case_a_unit_already_down_is_said_to_be
case_a_hung_stop_is_killed_and_the_restart_goes_on
case_a_hung_stop_during_an_update_still_updates
case_a_unit_that_survives_sigkill_fails_the_stop
case_searxng_started_after_a_healthy_restart
case_searxng_failure_never_fails_an_update
case_searxng_stopped_for_never
case_searxng_not_running_for_never_is_left_alone
case_searxng_left_alone_when_unanswered
case_searxng_opt_out_and_an_older_checkout
case_searxng_hung_start_is_bounded_and_the_update_stays_ok
case_searxng_start_detached_under_systemd
case_never_refuses_a_dependency_update
case_never_refuses_a_music_image_change
case_never_mpd_conf_only_keeps_the_image
case_unanswered_dependency_update_reads_the_answer_once

echo "apply-update harness: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
