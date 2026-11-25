#!/usr/bin/env bash
# uninstall.sh: take Domovoi off this Linux server.
#
#   sudo bash /opt/domovoi/scripts/linux/uninstall.sh                      # asks; keep data is the default
#   sudo bash /opt/domovoi/scripts/linux/uninstall.sh --keep-data --yes
#   sudo bash /opt/domovoi/scripts/linux/uninstall.sh --remove-everything  # asks you to type it
#
# Two ways (docs/LINUX_HOST.md, "Uninstalling, and reinstalling without
# losing your rooms"):
#
#   keep data (the default)  Domovoi stops and no longer starts with the
#       computer: its services, timers, sudo grants and helpers are removed
#       and its containers stopped. Kept, where they are: the server's
#       identity (~/.domovoi/server-identity.json of the service user, the
#       key every room is set up to trust), the database (Docker volumes),
#       the settings and secrets (the checkout's domovoi/.env, ~/.domovoi),
#       the program folder and the service user. Installing again on this
#       computer (install.sh, or provision.sh) picks all of it up, so the
#       rooms reconnect by themselves.
#   remove everything  also deletes the database volumes and containers,
#       ~/.domovoi (with the server's identity: every room must then be set
#       up again), the program folder and the install records. Asked for
#       by typing "remove everything" (or --yes).
#
# Never touched, either way: your media folders, Docker, Ollama and its
# models, other apt packages, and the service user's account itself (the
# last lines say how to remove those by hand).
#
# Everything is inside functions and main runs on the last line, so a
# script deleted while it runs (remove everything deletes the program
# folder it may be in) runs nothing it has not already read.

set -euo pipefail

SVC_USER=domovoi
CHECKOUT=/opt/domovoi
MODE=""
YES=0
DRY=0
ROOT=""
TTY=/dev/tty
HARNESS=0
HAVE_TTY=0
SVC_HOME=""
CONFIRM_PHRASE="remove everything"

# Domovoi's own units (scripts/linux/units, install-update-unit.sh, the
# vm guard), the order they are stopped in.
UNITS=(domovoi-web.service domovoi-core.service domovoi-db.service domovoi-update.service
  domovoi-cache-trim.timer domovoi-cache-trim.service domovoi-fh-firewall.service domovoi-vm-guard.service)
# Files that only wire Domovoi into the system: removed either way.
INTEGRATION_FILES=(/etc/sudoers.d/domovoi-restart /etc/sudoers.d/domovoi-update
  /etc/systemd/system/docker.service.d/50-domovoi-media.conf
  /etc/systemd/journald.conf.d/50-domovoi.conf)
INTEGRATION_DIRS=(/usr/local/lib/domovoi-cache-trim /usr/local/lib/domovoi-fh-firewall /usr/local/lib/domovoi-update)
# Domovoi's records and settings outside the home: kept with the data,
# removed with everything.
RECORD_PATHS=(/var/lib/domovoi-provision /var/lib/domovoi-update /etc/domovoi /etc/default/domovoi-update
  /usr/local/lib/domovoi-python)

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'domovoi uninstall: %s\n' "$*" >&2; exit 1; }
usage_die() { printf 'domovoi uninstall: %s\n(--help lists the options)\n' "$*" >&2; exit 2; }

usage() {
  cat <<'EOF'
Take Domovoi off this computer.

  sudo bash /opt/domovoi/scripts/linux/uninstall.sh

Options:
  --keep-data          (the default) stop Domovoi and take it out of the
                       computer's startup, and keep its data: the server's
                       identity, the database, the settings and the program
                       folder. Installing again picks them up, so your rooms
                       reconnect by themselves.
  --remove-everything  also delete the database, the server's identity (every
                       room must then be set up again), the settings and the
                       program folder. Asks you to type "remove everything".
  --yes, -y            ask nothing (with --remove-everything: no typing)
  --user USER          the service user (default domovoi)
  --checkout-dir DIR   the program folder (default /opt/domovoi)
  --dry-run            say what it would do; change nothing
  -h, --help           this text
Your media folders, Docker, Ollama and its models are never removed.
EOF
}

parse_args() {
  local opt val has
  while [ $# -gt 0 ]; do
    opt=$1 val="" has=0
    case $opt in
      --*=*) val=${opt#*=}; opt=${opt%%=*}; has=1 ;;
    esac
    case $opt in
      -h|--help) usage; exit 0 ;;
      -y|--yes) YES=1 ;;
      --dry-run) DRY=1 ;;
      --keep-data)
        [ "$MODE" != everything ] || usage_die "--keep-data and --remove-everything: give one of them"
        MODE=keep ;;
      --remove-everything)
        [ "$MODE" != keep ] || usage_die "--keep-data and --remove-everything: give one of them"
        MODE=everything ;;
      --user|--checkout-dir)
        if [ "$has" = 0 ]; then
          [ $# -ge 2 ] || usage_die "$opt needs a value"
          val=$2
          shift
        fi
        [ -n "$val" ] || usage_die "$opt needs a value"
        case $opt in
          --user)
            [[ $val =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || usage_die "--user takes a user name: $val"
            SVC_USER=$val ;;
          --checkout-dir)
            [[ $val == /?* ]] || usage_die "--checkout-dir takes an absolute path: $val"
            CHECKOUT=${val%/} ;;
        esac
        ;;
      *) usage_die "unknown option: $1" ;;
    esac
    shift
  done
}

# The test harness (domovoi/tests/test_linux_uninstall_script.py) points the
# script at a throwaway root folder and answers file, with systemctl,
# docker and runuser stubs on PATH. Only with
# DOMOVOI_UNINSTALL_TEST_HARNESS=1; never set these on a real box.
harness_setup() {
  if [ "${DOMOVOI_UNINSTALL_TEST_HARNESS:-}" = 1 ]; then
    HARNESS=1
    ROOT=${DOMOVOI_UNINSTALL_ROOT:?}
    TTY=${DOMOVOI_UNINSTALL_TTY:-none}
    unset DOMOVOI_UNINSTALL_ROOT DOMOVOI_UNINSTALL_TTY DOMOVOI_UNINSTALL_TEST_HARNESS
  elif [ -n "${DOMOVOI_UNINSTALL_ROOT:-}${DOMOVOI_UNINSTALL_TTY:-}" ]; then
    die "the DOMOVOI_UNINSTALL_* variables are for this script's tests only; unset them and run it again"
  else
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
  fi
}

open_tty() {
  [ "$YES" = 0 ] || return 0
  if [ "$TTY" != none ] && (: <"$TTY") 2>/dev/null; then
    exec 3<"$TTY"
    HAVE_TTY=1
  fi
}

ask() {
  printf '%s' "$1" >&2
  IFS= read -r -u 3 ANSWER || die "no answer (the terminal closed); nothing was changed"
  [ -t 3 ] || printf '\n' >&2
  ANSWER=${ANSWER#"${ANSWER%%[![:space:]]*}"}
  ANSWER=${ANSWER%"${ANSWER##*[![:space:]]}"}
}

svc_home() {
  local h=""
  if [ "$HARNESS" = 1 ]; then
    h=$(awk -F: -v u="$SVC_USER" '$1 == u {print $6}' "$ROOT/etc/passwd" 2>/dev/null) || h=""
  else
    h=$(getent passwd "$SVC_USER" 2>/dev/null | cut -d: -f6) || h=""
  fi
  printf '%s' "$h"
}

# A command as the service user (docker: that user is in the docker group).
as_svc() {
  if [ "$HARNESS" = 1 ]; then "$@"; return; fi
  runuser -u "$SVC_USER" -- env HOME="$SVC_HOME" "$@"
}

# The identity's fingerprint (public), read from the key file without
# reading anything else out of it.
fingerprint() {
  sed -n 's/^[[:space:]]*"fingerprint":[[:space:]]*"\(SHA256:[A-Za-z0-9+\/]*\)".*$/\1/p' "$1" 2>/dev/null | head -n 1
}

# Domovoi's containers and volumes (the compose project "domovoi" and the
# rooms' players, all named domovoi-...). Empty when Docker isn't there.
containers() {
  command -v docker >/dev/null 2>&1 || return 0
  as_svc docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^domovoi[-_]' || true
}
volumes() {
  command -v docker >/dev/null 2>&1 || return 0
  as_svc docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E '^domovoi[-_]' || true
}

check_system() {
  if [ "$HARNESS" = 0 ] && [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
    die "run it as root: sudo bash $0"
  fi
  SVC_HOME=$(svc_home)
  if [ -n "$SVC_HOME" ] && ! [[ $SVC_HOME =~ ^/[A-Za-z0-9._/@+-]+$ ]]; then
    die "the home of $SVC_USER ($SVC_HOME) is not a plain path; remove Domovoi by hand (docs/LINUX_HOST.md)"
  fi
  case $CHECKOUT in
    /|/opt|/usr|/usr/*|/etc|/etc/*|/home|/root|/var|/bin|/sbin|/lib|/lib64|/boot) die "--checkout-dir $CHECKOUT is not a Domovoi program folder" ;;
  esac
  if [ -z "$SVC_HOME" ] && [ ! -d "$ROOT$CHECKOUT" ]; then
    die "there is no Domovoi here: no user $SVC_USER and no $CHECKOUT (give --user or --checkout-dir if it was installed with others)"
  fi
}

identity_file() { [ -n "$SVC_HOME" ] && printf '%s' "$SVC_HOME/.domovoi/server-identity.json"; }

plan() {
  local id fp vols
  id=$(identity_file)
  fp=""
  if [ -n "$id" ] && [ -f "$ROOT$id" ]; then fp=$(fingerprint "$ROOT$id"); fi
  vols=$(volumes | tr '\n' ' ')
  say ""
  if [ "$MODE" = keep ]; then
    say "Uninstall Domovoi and KEEP its data."
    say "  Stopped and taken out of the computer's startup: ${UNITS[*]%%.*}, Domovoi's containers."
    say "  Removed: Domovoi's service files, sudo grants and helpers."
    say "  Kept, where they are now:"
    if [ -n "$fp" ]; then
      say "    - the server's identity ($fp): $id"
      say "      Installing again on this computer uses it, so your rooms reconnect by themselves."
    else
      say "    - the server's identity: there is none yet (${id:-no home for $SVC_USER})"
    fi
    say "    - the database and other data: Docker volumes ${vols:-(none found)}"
    say "    - settings and secrets: $CHECKOUT/domovoi/.env and ${SVC_HOME:-~$SVC_USER}/.domovoi"
    say "    - the program folder $CHECKOUT, Domovoi's records in /var/lib and /etc, and the user $SVC_USER"
  else
    say "Uninstall Domovoi and REMOVE EVERYTHING of its data."
    say "  Stopped and removed: ${UNITS[*]%%.*}, Domovoi's containers, service files, sudo grants and helpers."
    say "  Deleted, for good:"
    say "    - the database and other data: Docker volumes ${vols:-(none found)}"
    if [ -n "$fp" ]; then
      say "    - the server's identity ($fp): $id"
      say "      Every room set up for this server must be set up again after a new install."
      say "      To keep it instead, stop here and download a backup first (Settings > System >"
      say "      About & updates > House identity backup), or uninstall with --keep-data."
    fi
    say "    - settings and secrets: $CHECKOUT/domovoi/.env and ${SVC_HOME:-~$SVC_USER}/.domovoi"
    say "    - the program folder $CHECKOUT and Domovoi's records: ${RECORD_PATHS[*]}"
  fi
  say "  Never touched: your media folders, Docker, Ollama and its models, other packages."
  [ "$DRY" = 0 ] || say "  Dry run: nothing is changed."
}

choose_mode() {
  [ -z "$MODE" ] || return 0
  if [ "$HAVE_TTY" = 0 ]; then
    # No one to ask, and no answer given: the default is the safe one.
    MODE=keep
    return 0
  fi
  cat >&2 <<'EOF'

How should Domovoi be uninstalled?
  1) Keep my data (recommended). Domovoi stops; its database, settings and the
     server's identity stay on this computer, so installing again later brings
     everything back and your rooms reconnect by themselves.
  2) Remove everything. Also deletes the database, the settings and the
     server's identity: every room must be set up again after a new install.
EOF
  while :; do
    ask "Answer 1 or 2 [1]: "
    case ${ANSWER,,} in
      ""|1|keep) MODE=keep; return ;;
      2|remove) MODE=everything; return ;;
    esac
    printf '%s\n' "Please answer 1 or 2." >&2
  done
}

confirm() {
  [ "$DRY" = 0 ] || return 0
  [ "$YES" = 0 ] || return 0
  if [ "$HAVE_TTY" = 0 ]; then
    if [ "$MODE" = everything ]; then
      usage_die "--remove-everything with no terminal to confirm at needs --yes; nothing was changed"
    fi
    usage_die "there is no terminal to confirm at: give --yes (with --keep-data, the default); nothing was changed"
  fi
  if [ "$MODE" = keep ]; then
    ask "Uninstall and keep the data? [Y/n] "
    case ${ANSWER,,} in
      ""|y|yes) return 0 ;;
    esac
    die "stopped; nothing was changed"
  fi
  ask "Type \"$CONFIRM_PHRASE\" to delete it all, or press Enter to stop: "
  [ "${ANSWER,,}" = "$CONFIRM_PHRASE" ] || die "stopped; nothing was changed"
}

# ─── doing it ────────────────────────────────────────────────────────────

stop_units() {
  local u
  for u in "${UNITS[@]}"; do
    if [ -f "$ROOT/etc/systemd/system/$u" ] || systemctl cat "$u" >/dev/null 2>&1; then
      systemctl disable --now "$u" >/dev/null 2>&1 || systemctl stop "$u" >/dev/null 2>&1 || warn "could not stop $u"
      say "stopped $u"
    fi
  done
}

stop_containers() {
  local c
  for c in $(containers); do
    if [ "$MODE" = everything ]; then
      as_svc docker rm -f "$c" >/dev/null 2>&1 || warn "could not remove the container $c"
      say "removed the container $c"
    else
      # Stopped, not removed: a stopped container with restart
      # unless-stopped stays stopped across reboots.
      as_svc docker stop "$c" >/dev/null 2>&1 || warn "could not stop the container $c"
      say "stopped the container $c"
    fi
  done
}

remove_volumes() {
  local v
  for v in $(volumes); do
    as_svc docker volume rm "$v" >/dev/null 2>&1 || warn "could not remove the Docker volume $v"
    say "deleted the Docker volume $v"
  done
}

remove_path() {
  local p=$1
  [ -e "$ROOT$p" ] || [ -L "$ROOT$p" ] || return 0
  rm -rf --one-file-system -- "$ROOT$p"
  say "removed $p"
}

remove_integration() {
  local u p
  for u in "${UNITS[@]}"; do
    remove_path "/etc/systemd/system/$u"
    remove_path "/etc/systemd/system/$u.d"
  done
  for p in "${INTEGRATION_FILES[@]}" "${INTEGRATION_DIRS[@]}"; do remove_path "$p"; done
  systemctl daemon-reload >/dev/null 2>&1 || true
}

remove_data() {
  local p
  remove_volumes
  if [ -n "$SVC_HOME" ]; then remove_path "$SVC_HOME/.domovoi"; fi
  # The program folder, only if it is one (a checkout with provision.sh).
  if [ -f "$ROOT$CHECKOUT/scripts/linux/provision.sh" ]; then
    remove_path "$CHECKOUT"
  elif [ -e "$ROOT$CHECKOUT" ]; then
    warn "$CHECKOUT doesn't look like Domovoi's program folder (no scripts/linux/provision.sh); left as it is"
  fi
  for p in "${RECORD_PATHS[@]}"; do remove_path "$p"; done
}

closing() {
  local id fp
  say ""
  if [ "$MODE" = keep ]; then
    id=$(identity_file)
    fp=""
    if [ -n "$id" ] && [ -f "$ROOT$id" ]; then fp=$(fingerprint "$ROOT$id"); fi
    say "Domovoi is uninstalled and its data was kept."
    if [ -n "$fp" ]; then
      say "  The server's identity $fp is still in $id."
    fi
    say "  The database is in Docker's volumes; the settings in $CHECKOUT/domovoi/.env and ${SVC_HOME:-~$SVC_USER}/.domovoi."
    say "To install again with all of it: run the same install command as before"
    say "(docs/LINUX_HOST.md, Install). To delete the kept data later:"
    say "  sudo bash $CHECKOUT/scripts/linux/uninstall.sh --remove-everything"
  else
    say "Domovoi is uninstalled and its data deleted."
  fi
  say "Left on this computer on purpose: your media folders, Docker, Ollama and its models"
  say "(sudo systemctl disable --now ollama; sudo rm -rf /usr/local/bin/ollama /usr/share/ollama),"
  say "and the user $SVC_USER (sudo userdel -r $SVC_USER)."
}

main() {
  harness_setup
  parse_args "$@"
  open_tty
  check_system
  choose_mode
  plan
  confirm
  if [ "$DRY" = 1 ]; then
    say ""
    say "Dry run finished: nothing was changed."
    return 0
  fi
  say ""
  stop_units
  stop_containers
  remove_integration
  if [ "$MODE" = everything ]; then remove_data; fi
  closing
}

main "$@"; exit $?
