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
#   * each database is a file holding its flyway_schema_history row count;
#   * `systemctl restart domovoi-db.service` is Flyway: it raises the count
#     to the number of migration files in the checkout and never lowers it;
#   * pg_dump writes the count into the dump and pg_restore reads it back;
#   * psql CREATE/DROP/ALTER ... RENAME DATABASE move those files around;
#   * curl can fail while HEAD is a given SHA (the "new code is broken" case).
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
export SHIM_REAL_GIT
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
  exit 0
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
fi
exit 0
SH

  cat >"$bin/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_STATE/calls.log"
case "${1-}" in
  exec)
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
esac
exit 0
SH

  cat >"$bin/pg_dump" <<'SH'
#!/usr/bin/env bash
echo "pg_dump $*" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/fail-pg_dump" ]; then echo "pg_dump: connection refused" >&2; exit 1; fi
db=${!#}
echo "DOMOVOI-FAKE-DUMP migrations=$(cat "$SHIM_STATE/db-$db")"
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
  echo "${body##*migrations=}" >"$SHIM_STATE/db-$db"
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
case "$sql" in
  "SELECT count(*) FROM flyway_schema_history")
    cat "$(dbfile "$db")" 2>/dev/null || { echo "psql: database $db does not exist" >&2; exit 2; } ;;
  CREATE\ DATABASE*)
    name=$(printf '%s' "$sql" | sed 's/^CREATE DATABASE "\([^"]*\)".*/\1/')
    echo 0 >"$(dbfile "$name")" ;;
  DROP\ DATABASE*)
    name=$(printf '%s' "$sql" | sed 's/^DROP DATABASE IF EXISTS "\([^"]*\)".*/\1/')
    rm -f "$(dbfile "$name")" ;;
  ALTER\ DATABASE*)
    from=$(printf '%s' "$sql" | sed 's/^ALTER DATABASE "\([^"]*\)" RENAME TO "\([^"]*\)"$/\1/')
    to=$(printf '%s' "$sql" | sed 's/^ALTER DATABASE "\([^"]*\)" RENAME TO "\([^"]*\)"$/\2/')
    [ -f "$(dbfile "$from")" ] && [ ! -f "$(dbfile "$to")" ] || { echo "psql: rename failed" >&2; exit 1; }
    mv "$(dbfile "$from")" "$(dbfile "$to")" ;;
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

  g init -q -b main
  printf '[project]\nname = "domovoi"\nversion = "0"\n' >"$REPO/pyproject.toml"
  printf 'numpy==2.4.6\n' >"$REPO/requirements.lock"
  printf 'FROM debian:bookworm-slim\n' >"$REPO/domovoi/Dockerfile.mpd"
  printf 'music_directory "/music"\n' >"$REPO/domovoi/mpd.conf"
  printf 'CREATE TABLE a (id int);\n' >"$REPO/domovoi/db/migrations/V001__a.sql"
  printf 'print("a")\n' >"$REPO/domovoi/app.py"
  SHA_A=$(commit_all A)

  export SHIM_STATE=$STATE SHIM_REPO=$REPO
}

# run_update [extra PATH dir]: run the script against the current case.
run_update() {
  local extra_bin=${1-}
  local path=$CASE/bin:$PATH venv_env=(DOMOVOI_VENV="$CASE/venv")
  if [ -n "$extra_bin" ]; then path=$extra_bin:$path; fi
  if [ "${NO_VENV_ENV:-0}" = 1 ]; then venv_env=(); fi
  RC=0
  env -u DOMOVOI_VENV PATH="$path" \
    DOMOVOI_REPO_DIR="$REPO" \
    ${venv_env[@]+"${venv_env[@]}"} \
    DOMOVOI_UPDATE_DIR="$UPD" \
    DOMOVOI_USER=tester \
    DOMOVOI_CORE_STATE_DIR="$CORE_STATE" \
    DOMOVOI_UPDATE_HEALTH_TIMEOUT=1 \
    DOMOVOI_UPDATE_HEALTH_INTERVAL=0.2 \
    DOMOVOI_UPDATE_KEEP_BACKUPS="${KEEP_BACKUPS:-5}" \
    bash "$SCRIPT" >"$CASE/output.log" 2>&1 || RC=$?
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
file_is() { [ -f "$1" ] && eq "$(tr -d '[:space:]' <"$1")" "$2"; }

valid_json() {
  [ -n "${HARNESS_PYTHON:-}" ] || return 0
  local p=$RESULT py=$HARNESS_PYTHON
  if command -v cygpath >/dev/null 2>&1; then p=$(cygpath -w "$RESULT"); py=$(cygpath -u "$py"); fi
  "$py" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); assert isinstance(d, dict) and isinstance(d["steps"], list)' "$p"
}

end_case() {
  check "result file is valid JSON" valid_json
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
  check "cheap flyway run" called "systemctl restart domovoi-db.service"
  check "starts core and web" called "systemctl start domovoi-core.service domovoi-web.service"
  check "health-checks core" called "curl http://127.0.0.1:6370/v1/health"
  check "health-checks web" called "curl http://127.0.0.1:6369/api/health"
  check "no backup on a plain restart" not_called "pg_dump"
  check "no pip on a plain restart" not_called "pip "
  check "no docker build on a plain restart" not_called "docker build"
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
  check "a healthy restart records the baseline" file_is "$UPD/applied_sha" "$SHA_A"
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
  check "one backup kept" eq "$(ls "$UPD/backups" | grep -c '^pre-.*\.dump$')" 1
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
  printf 'print("b")\n' >"$REPO/domovoi/app.py"
  commit_all "B: code" >/dev/null
  KEEP_BACKUPS=2 run_update
  check "status ok" eq "$(field status)" '"ok"'
  check "keeps the newest two" eq "$(ls "$UPD/backups" | grep -c '\.dump$')" 2
  check "the new dump survives" eq "$(ls "$UPD/backups" | grep -c "^pre-$(g rev-parse HEAD | cut -c1-12)-")" 1
  check "the newest old dump survives" eq "$(ls "$UPD/backups" | grep -c '^pre-000000000003-')" 1
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

case_noop_restart
case_noop_without_any_history
case_deps_changed_as_root
case_mpd_changed
case_mpd_conf_only
case_migration_health_failure_rolls_back
case_flyway_failure_without_growth
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
case_venv_from_the_core_unit
case_venv_ignores_a_non_venv_interpreter
case_venv_not_writable_aborts

echo "apply-update harness: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
