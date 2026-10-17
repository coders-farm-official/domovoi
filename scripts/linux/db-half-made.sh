#!/usr/bin/env bash
# db-half-made.sh: find, and with --repair remove, a database volume whose
# first start was cut off before it made any database.
#
#   db-half-made.sh            say what the volume holds; exit 0 when it is
#                              half made, 1 when it is not (or there is no
#                              volume), 2 when it can't tell
#   db-half-made.sh --repair   the same, and a half-made volume is removed
#                              with its container, so the next
#                              `docker compose up -d postgres` starts the
#                              first start again; exit 0 unless that removal
#                              failed (1) or it could not tell (2)
#
# Why (2026-10-17): Postgres's image initialises a new volume once: initdb,
# then pg_hba.conf's network line, then a temporary server that creates the
# database (POSTGRES_DB, here domovoi) and runs the init scripts. On a slow
# disk initdb alone takes minutes. Killed in that window (a reboot, the
# distro stopped, Setup closed), the volume keeps initdb's files, so every
# later start skips the initialisation ("Database directory appears to
# contain a database; Skipping initialization") and Postgres runs without
# the domovoi database and without the line that lets Flyway and the core
# log in over the network: every start of domovoi-db fails again, Setup's
# Retry included.
#
# What counts as half made, read from the volume's files (so it works
# whether or not Postgres can start): PG_VERSION is there, there is no
# tablespace, and base/ holds no database but initdb's own three
# (template1, template0, postgres; every database created later gets an
# OID of 16384 or more). The domovoi
# database is the first one the image creates, so such a volume never held
# any of the household's data, and removing it loses nothing.
#
# Left alone: no volume (a first start makes it), a volume without
# PG_VERSION (the image initialises it), any volume with a database in it,
# and a Postgres whose first start is running now (its log has no
# "Skipping initialization" yet): that one is slow, not broken.
#
# provision.sh's start step runs it as root, before starting domovoi-db, when
# domovoi-db is not running. DOMOVOI_PG_CONTAINER / DOMOVOI_PG_VOLUME name
# the container and volume (default domovoi-postgres / domovoi-pgdata);
# DOMOVOI_PG_IMAGE the image that reads the volume when there is no
# container to take it from.
set -uo pipefail

CONTAINER=${DOMOVOI_PG_CONTAINER:-domovoi-postgres}
VOLUME=${DOMOVOI_PG_VOLUME:-domovoi-pgdata}
IMAGE=${DOMOVOI_PG_IMAGE:-}
REPAIR=0
case ${1:-} in
  --repair) REPAIR=1 ;;
  '') ;;
  *) echo "usage: db-half-made.sh [--repair]" >&2; exit 2 ;;
esac
say() { printf 'db-half-made: %s\n' "$*"; }
not_half_made() { say "$1"; exit 1; }
cant_tell() { say "can't tell: $1"; exit 2; }
if [ "$REPAIR" = 1 ]; then not_half_made() { say "$1"; exit 0; }; fi

docker volume inspect "$VOLUME" >/dev/null 2>&1 || not_half_made "no volume $VOLUME: a first start makes it"

state=$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null) || state=""
if [ -n "$state" ]; then
  IMAGE=$(docker inspect --format '{{.Image}}' "$CONTAINER" 2>/dev/null || true)
  IMAGE=${IMAGE:-${DOMOVOI_PG_IMAGE:-}}
  if [ "$state" = running ] || [ "$state" = restarting ]; then
    # A first start still running: its log has the initialisation's lines
    # and no "Skipping" yet. Slow, not broken.
    if ! docker logs "$CONTAINER" 2>&1 | grep -q 'Skipping initialization'; then
      not_half_made "$CONTAINER is $state and has not restarted since its first start: left alone"
    fi
  fi
fi
[ -n "$IMAGE" ] || cant_tell "no container $CONTAINER to read the volume with, and no DOMOVOI_PG_IMAGE"

# The volume's files, read by a throwaway container of the same image (no
# network, the volume read-only): "none" without PG_VERSION, else base/.
listing=$(docker run --rm --network none --entrypoint sh -v "$VOLUME:/pgdata:ro" "$IMAGE" \
  -c 'if [ ! -s /pgdata/PG_VERSION ]; then echo none; else ls -1 /pgdata/base; echo "tablespaces $(ls -A /pgdata/pg_tblspc 2>/dev/null | wc -l)"; fi' 2>&1) \
  || cant_tell "reading $VOLUME failed: $(printf '%s' "$listing" | tail -n 1)"
if [ "$listing" = none ]; then not_half_made "$VOLUME has no PG_VERSION: the image initialises it"; fi
dbs=0
oids=""
while IFS= read -r d; do
  case $d in '' | *[!0-9]*) continue ;; esac
  oids="$oids $d"
  if [ "$d" -ge 16384 ]; then dbs=$((dbs + 1)); fi
done <<<"$listing"
# A database in a tablespace of its own lives outside base/: any
# tablespace at all, and the volume is not ours to judge.
tablespaces=$(printf '%s\n' "$listing" | sed -n 's/^tablespaces //p')
case $tablespaces in
  0) ;;
  '' | *[!0-9]*) cant_tell "$VOLUME's tablespaces could not be counted" ;;
  *) not_half_made "$VOLUME has $tablespaces tablespace(s): left alone" ;;
esac
[ -n "$oids" ] || cant_tell "$VOLUME's base/ lists no database at all: $(printf '%s' "$listing" | tr '\n' ' ' | cut -c1-200)"
if [ "$dbs" -gt 0 ]; then not_half_made "$VOLUME holds $dbs database(s) besides initdb's own: left alone"; fi

say "$VOLUME is half made: initdb's files (base/:$oids) and no database, so its first start was cut off before it made one; it never held any data"
[ "$REPAIR" = 1 ] || exit 0
if [ -n "$state" ]; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || { say "docker rm -f $CONTAINER failed"; exit 1; }
fi
docker volume rm "$VOLUME" >/dev/null 2>&1 || { say "docker volume rm $VOLUME failed"; exit 1; }
say "removed ${state:+$CONTAINER and }$VOLUME: the next start initialises it again"
exit 0
