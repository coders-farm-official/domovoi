#!/usr/bin/env bash
# db-diagnostics.sh: why the database did not come up, for a log.
#
#   db-diagnostics.sh            the report: the Postgres container's state,
#                                its last health check and its last 100
#                                log lines, then the one-line reason
#   db-diagnostics.sh --summary  the reason only, one line, e.g.
#                                "Postgres is unhealthy (last check:
#                                127.0.0.1:5432 - no response)"
#   db-diagnostics.sh --unit     the report, as domovoi-db.service's
#                                ExecStopPost=: nothing when the unit's
#                                start succeeded ($SERVICE_RESULT=success)
#
# Who runs it: domovoi-db.service when its start fails (into its journal),
# provision.sh's start step and apply-update.sh's database restart when
# domovoi-db failed (into their own output: the Windows installer's
# provision.log, the update's journal and result). Read-only: docker
# inspect and docker logs, nothing else; it always exits 0.
#
# No secrets: Postgres does not log its password, but a statement it
# logs could hold one, and so could a connection string or a value that
# ended up in an error. Blanked, in any letter case and across line
# breaks: PASSWORD followed by a quoted string ('...' with '' inside,
# E'...', $$...$$ or $tag$...$tag$, an unclosed '... to the end of its
# line), password=... (quoted or bare), a password in a URL, and the
# database's own password wherever it appears as it is: POSTGRES_PASSWORD
# from domovoi/.env and from the container's environment. (Not the
# historical default "domovoi", which is no secret and is also the
# database's and the user's name.)
#
# DOMOVOI_PG_CONTAINER names the container (default domovoi-postgres);
# DOMOVOI_DB_DIAG_LINES the log lines (default 100); DOMOVOI_ENV_FILE the
# .env (default: domovoi/.env of the checkout this script is in, else
# ./.env, the unit's working directory).
set -uo pipefail

CONTAINER=${DOMOVOI_PG_CONTAINER:-domovoi-postgres}
LINES=${DOMOVOI_DB_DIAG_LINES:-100}
case $LINES in '' | *[!0-9]*) LINES=100 ;; esac
MODE=${1:-report}

if [ "$MODE" = --unit ]; then
  [ "${SERVICE_RESULT:-}" = success ] && exit 0
  echo "domovoi-db.service failed (${SERVICE_RESULT:-unknown result}); what Postgres says:"
fi

# The literal passwords to blank, one per line (scrub reads them from the
# environment, never from its command line).
env_file=${DOMOVOI_ENV_FILE:-}
if [ -z "$env_file" ]; then
  env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)/domovoi/.env"
  [ -f "$env_file" ] || env_file=./.env
fi
pw_of() { sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}POSTGRES_PASSWORD[[:space:]]*=[[:space:]]*//p' | tail -n 1 | tr -d '\r' \
  | sed -e "s/^'\(.*\)'[[:space:]]*\$/\1/" -e 's/^"\(.*\)"[[:space:]]*$/\1/'; }
DIAG_SECRETS=$( { pw_of <"$env_file"; } 2>/dev/null
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>/dev/null | pw_of)
export DIAG_SECRETS

scrub() {
  if command -v perl >/dev/null 2>&1; then
    # The whole text at once (-0777), so a password on the line after its
    # PASSWORD is caught too.
    LC_ALL=C perl -0777 -pe '
      BEGIN { @s = grep { length($_) && $_ ne "domovoi" } split /\n/, ($ENV{DIAG_SECRETS} // ""); }
      for my $x (@s) { s/\Q$x\E/[hidden]/g; }
      my $q = qr/(?:[Ee]?\x27(?:[^\x27\\]|\x27\x27|\\.)*\x27|\$(\w*)\$.*?\$\g{-1}\$|[Ee]?\x27[^\n]*)/s;
      s/(?<![A-Za-z])(password)\s*=\s*(?:$q|[^\s\x27",;)]+)/$1 [hidden]/gis;
      s/(?<![A-Za-z])(password)\s+$q/$1 [hidden]/gis;
      s{(://[^:/@\s]+:)[^@/\s]+@}{$1\[hidden]@}g;
    '
  else
    LC_ALL=C sed -E \
      -e "s/([Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd])([[:space:]]+[Ee]?'.*|[[:space:]]*=[[:space:]]*('[^']*'|[^[:space:]',;]+))/\1 [hidden]/g" \
      -e 's#(://[^:/@[:space:]]+:)[^@/[:space:]]+@#\1[hidden]@#g'
  fi
}

# One line from docker inspect: status|health|exit code|restarts, or empty
# when there is no such container (or no Docker to ask).
state=$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}|{{.RestartCount}}' "$CONTAINER" 2>/dev/null | head -n 1)
# The last health check's output (pg_isready's own words).
last_check=""
if [ -n "$state" ]; then
  last_check=$(docker inspect --format '{{if .State.Health}}{{range .State.Health.Log}}{{.Output}}{{"\n"}}{{end}}{{end}}' "$CONTAINER" 2>/dev/null \
    | sed '/^[[:space:]]*$/d' | tail -n 1 | scrub | cut -c1-200)
fi
logs=""
if [ -n "$state" ]; then logs=$(docker logs --tail "$LINES" "$CONTAINER" 2>&1 | scrub); fi

reason() {
  local status health code restarts r fatal
  if [ -z "$state" ]; then
    if docker info >/dev/null 2>&1; then echo "there is no Postgres container ($CONTAINER)"
    else echo "Docker does not answer, so there is no Postgres"; fi
    return
  fi
  IFS='|' read -r status health code restarts <<<"$state"
  case $status in
    running)
      case $health in
        healthy) r="Postgres is running and healthy" ;;
        starting) r="Postgres is still starting" ;;
        unhealthy) r="Postgres is unhealthy" ;;
        *) r="Postgres is running" ;;
      esac ;;
    restarting) r="Postgres keeps restarting" ;;
    exited | dead) r="Postgres has stopped (exit code $code)" ;;
    *) r="Postgres is $status" ;;
  esac
  if [ "${restarts:-0}" -gt 0 ] 2>/dev/null; then r="$r, restarted $restarts time$([ "$restarts" = 1 ] || echo s)"; fi
  # Its own last word of trouble, if it said one.
  fatal=$(printf '%s\n' "$logs" | grep -E '(FATAL|PANIC|ERROR):' | tail -n 1 | sed -E 's/^.*((FATAL|PANIC|ERROR):)/\1/' | cut -c1-200)
  if [ -n "$fatal" ]; then r="$r; its log: $fatal"
  elif [ "$health" != healthy ] && [ -n "$last_check" ]; then r="$r (last check: $last_check)"; fi
  echo "$r"
}

if [ "$MODE" = --summary ]; then
  reason
  exit 0
fi

if [ -z "$state" ]; then
  reason
  exit 0
fi
IFS='|' read -r st_status st_health st_code st_restarts <<<"$state"
echo "Postgres container $CONTAINER: ${st_status}, health ${st_health:-none}, exit code $st_code, restarts $st_restarts"
if [ -n "$last_check" ]; then echo "its last health check: $last_check"; fi
echo "the last $LINES lines of docker logs $CONTAINER:"
printf '%s\n' "$logs" | sed 's/^/    /'
echo "in short: $(reason)"
exit 0
