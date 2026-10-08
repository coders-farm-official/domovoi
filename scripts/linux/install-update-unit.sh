#!/usr/bin/env bash
# install-update-unit.sh: set up domovoi-update.service, in one command, on a
# Linux box that already runs domovoi-db, domovoi-core and domovoi-web.
#
#   sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --dry-run
#   sudo bash /opt/domovoi/scripts/linux/install-update-unit.sh --apply
#
# It does what docs/LINUX_HOST.md, "One-time upgrade for existing installs",
# lists as manual steps, after checking that the box can take them:
#
#   1. Pre-flight, which changes nothing. systemd is running; the three
#      domovoi units are installed; git works in the checkout as the service
#      user; docker compose works for that user (domovoi-db and every update
#      run it as them); sudoers passes visudo -c as it is, and the new rule
#      passes visudo -cf on its own. Signed updates (docs/LINUX_HOST.md,
#      "Signed updates"): with the allowed-signers file in place, HEAD must
#      verify against it as root, the way every update run will, and the
#      pinned upstream must be the checkout's, or this stops rather than
#      leave a unit that refuses every run. Three things only warn: a venv
#      the service user doesn't wholly own (an update that changes
#      dependencies would abort; the chown that fixes it is printed, and
#      --fix-ownership runs it), piper-tts older than 1.3 in the venv, and
#      signed updates not set up.
#   2. The rollback baseline. When applied_sha isn't recorded yet, it becomes
#      the SHA the core is RUNNING: running_sha from GET
#      <core>/v1/admin/version, -dirty stripped, verified as a commit of the
#      checkout by the service user. Not HEAD, which a pull may already have
#      moved past the running code. If the core can't say, this stops; it
#      never guesses.
#   3. The grant, /etc/sudoers.d/domovoi-update (0440), put in place only
#      after visudo -cf passed on a copy, then visudo -c on the whole
#      configuration. Then the grant is checked as the service user with
#      the probe the core itself runs (sudo -n -l, domovoi/self_restart.py).
#   4. /etc/systemd/system/domovoi-update.service, exactly as the doc gives
#      it, and systemctl daemon-reload. The unit goes in after the grant is
#      verified, not before: the core offers the full update as soon as it
#      sees the unit file, and without the grant its Restart button would
#      stop working instead of falling back to the plain bounce.
#   5. With --fix-ownership, the chown of the venv.
#   6. With --apply: systemctl start domovoi-update.service, which waits
#      for the run, then the status from its last-result.json.
#
# A stop anywhere in 3 and 4, expected or not (a failed check, a failed
# write, Ctrl-C), takes back the grant and the unit this run put there, so
# the Restart button keeps doing what it did before. The baseline from 2
# stays: it is the SHA the core runs now, which a later run could no longer
# learn once something restarts the core.
#
# What is already in place is left alone, so a second run changes nothing
# and says so. A file it replaces is kept beside it as
# <name>.bak-<UTC timestamp>, a name sudo and systemd both ignore. Files are
# written by rename from a mktemp name in the same directory, and only into
# directories no one but root can write (the pre-flight checks the update
# state directory and /etc/default/domovoi-update for that).
#
# Settings: the options in usage() below, and the same
# /etc/default/domovoi-update that apply-update.sh reads (DOMOVOI_UPDATE_DIR,
# DOMOVOI_VENV, and DOMOVOI_USER / DOMOVOI_REPO_DIR, which must agree with
# what the core runs as and from).
#
# DOMOVOI_INSTALL_ROOT is for the tests only
# (scripts/linux/tests/test-install-update-unit.sh): a directory put in
# front of every system path this reads or writes (/etc/..., /var/lib/...,
# /run/systemd/system, /usr/bin/systemctl). It takes effect only together
# with DOMOVOI_INSTALL_TEST_HARNESS=1; either one without the other, or a
# ROOT that isn't an absolute directory other than /, makes the script
# refuse to run rather than write the real /etc for a caller who meant a
# sandbox. Never set either on a real host. The checkout and the venv are
# real paths either way, since the unit names them.

set -euo pipefail

PROG=install-update-unit
SCRIPT_PATH=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
ORIG_ARGS=("$@")

ROOT=""
if [ -n "${DOMOVOI_INSTALL_ROOT:-}" ] || [ -n "${DOMOVOI_INSTALL_TEST_HARNESS:-}" ]; then
  if [ "${DOMOVOI_INSTALL_TEST_HARNESS:-}" != 1 ] || [[ ${DOMOVOI_INSTALL_ROOT:-} != /* ]] \
      || [ ! -d "$DOMOVOI_INSTALL_ROOT" ] || [ "$(cd "$DOMOVOI_INSTALL_ROOT" && pwd -P)" = / ]; then
    printf '%s: DOMOVOI_INSTALL_ROOT and DOMOVOI_INSTALL_TEST_HARNESS are for its test harness only, and only together; unset both and run it again\n' "$PROG" >&2
    exit 2
  fi
  ROOT=${DOMOVOI_INSTALL_ROOT%/}
else
  # Root runs what it finds on PATH; take it from no one's shell setup.
  # sudo's secure_path is this already.
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
  export PATH
fi

UNIT_NAME=domovoi-update.service
UNITS=(domovoi-db.service domovoi-core.service domovoi-web.service)
CORE_UNIT=domovoi-core.service
# The systemctl the grant names: the one the core runs (self_restart.py
# takes it from PATH, which on a merged-/usr Ubuntu finds this one first).
SYSTEMCTL_PATH=/usr/bin/systemctl
# What the Restart button runs through sudo when the unit is installed
# (self_restart._action("update")), and so exactly what the grant allows.
GRANT_ARGV=("$SYSTEMCTL_PATH" --no-block start "$UNIT_NAME")

UNIT_FILE=$ROOT/etc/systemd/system/$UNIT_NAME
SUDOERS_FILE=$ROOT/etc/sudoers.d/domovoi-update
DEFAULTS_FILE=$ROOT/etc/default/domovoi-update
DEFAULT_UPDATE_DIR=/var/lib/domovoi-update
DEFAULT_REPO=/opt/domovoi

DRY_RUN=0
APPLY=0
FIX_OWNERSHIP=0
REPO=""
SVC_USER=""
USER_FROM=""
CORE_URL=http://127.0.0.1:6370

# Filled in by the pre-flight.
UPDATE_DIR=""
APPLIED_FILE=""
VENV=""
VENV_OK=0        # VENV holds an interpreter
HEAD_SHA=""
BASELINE=""      # the rollback baseline: recorded already, or about to be
RECORD_SHA=""    # set when applied_sha has to be written
NEED_CHOWN=0
SUDOERS_RULE=""
SUDOERS_STATE=""
UNIT_STATE=""
LAST_BACKUP=""
NOT_ROOT_ONLY=""
TMPD=""
DONE=()          # what this run changed, for the summary and for a stop
# How to take back this run's grant and unit while they aren't both in
# place and loaded: "remove", or the kept copy to put back. Empty once
# there is nothing to take back.
GRANT_UNDO=""
UNIT_UNDO=""
REPORTED=0       # a stop, or the --apply result, has already said how it ended
PHASE=""         # "apply" while the update runs

usage() {
  cat <<'EOF'
Usage: sudo bash scripts/linux/install-update-unit.sh [options]

Sets up domovoi-update.service on a box that already runs domovoi-db,
domovoi-core and domovoi-web, so the dashboard's "Restart to apply changes"
runs the whole update: back up, sync dependencies, migrate, restart,
health-check, and roll back on failure. docs/LINUX_HOST.md, "One-time
upgrade for existing installs", has the detail.

  --dry-run         check everything and say what would change; change nothing
  --apply           after installing, run the update once and show its result
  --fix-ownership   hand the venv to the service user (chown -R) if it isn't
                    all theirs; without it, that is only a warning
  --repo DIR        the checkout (default: domovoi-core.service's
                    WorkingDirectory, else /opt/domovoi)
  --user NAME       the service user (default: the owner of the checkout)
  --core-url URL    where the core answers, on this box: http://127.0.0.1,
                    localhost or [::1], with a port (default:
                    http://127.0.0.1:6370)
  -h, --help        this text
EOF
}

usage_error() {
  printf '%s: %s\n\n' "$PROG" "$1" >&2
  usage >&2
  exit 2
}

say() { printf '%s\n' "$*"; }

# line MARK TEXT: one line of the report. more TEXT: its continuation.
line() { printf '  %-8s %s\n' "$1" "$2"; }
more() { printf '  %-8s %s\n' "" "$1"; }

err() { printf '%s\n' "$*" >&2; }

# Stop with a reason and what to do about it, take back what can be, and
# say what, if anything, this run changed.
stop() {
  local l
  printf '\n%s: stopped: %s\n' "$PROG" "$1" >&2
  shift
  for l in "$@"; do
    [ -z "$l" ] || printf '  %s\n' "$l" >&2
  done
  finish_stop
  exit 1
}

finish_stop() {
  local d
  REPORTED=1
  undo_changes
  if [ "${#DONE[@]}" -eq 0 ]; then
    err "Nothing was changed."
  else
    err "Changed before the stop:"
    for d in "${DONE[@]}"; do err "  $d"; done
  fi
}

# Any other way out: set -e on a command that failed, or a signal.
on_exit() {
  local rc=$?
  set +e
  if [ "$rc" != 0 ] && [ "$REPORTED" != 1 ]; then
    printf '\n%s: stopped before it finished (exit %s)\n' "$PROG" "$rc" >&2
    if [ "$PHASE" = apply ]; then
      err "  The update itself runs on in systemd: journalctl -u domovoi-update -f"
    fi
    finish_stop
  fi
  if [ -n "$TMPD" ]; then rm -rf "$TMPD"; fi
}

# Drop $1 from DONE: it was taken back.
forget() {
  local d kept=()
  for d in "${DONE[@]}"; do
    [ "$d" = "$1" ] || kept+=("$d")
  done
  DONE=("${kept[@]}")
}

# Take file $1 back to how it was before this run: remove it ($2 = remove),
# or put the kept copy $2 back with mode $3.
restore_file() {
  if [ "$2" = remove ]; then rm -f "$1"; else put_file "$1" "$2" "$3"; fi
}

# Take back the unit, then the grant, if this run put them there and they
# aren't both in place and loaded yet. Says what it did; never stops on its
# own failure, but says what is left and how to fix it.
undo_changes() {
  local how
  if [ -n "$UNIT_UNDO" ]; then
    how=$UNIT_UNDO
    UNIT_UNDO=""
    if restore_file "$UNIT_FILE" "$how" 0644; then
      forget "installed $UNIT_FILE"
      if [ "$how" = remove ]; then err "  undone: removed the new $UNIT_FILE"
      else err "  undone: put the previous $UNIT_FILE back"; fi
      if systemctl daemon-reload >/dev/null 2>&1; then
        forget "systemctl daemon-reload"
      else
        err "  systemctl daemon-reload failed after that: run sudo systemctl daemon-reload"
      fi
    elif [ "$how" = remove ]; then
      err "  NOT undone: $UNIT_FILE is still there, and the Restart button starts it. Run:"
      err "    sudo rm $UNIT_FILE && sudo systemctl daemon-reload"
    else
      err "  NOT undone: $UNIT_FILE is the new one; the previous one is $how. Run:"
      err "    sudo cp -p $how $UNIT_FILE && sudo systemctl daemon-reload"
    fi
  fi
  if [ -n "$GRANT_UNDO" ]; then
    how=$GRANT_UNDO
    GRANT_UNDO=""
    if restore_file "$SUDOERS_FILE" "$how" 0440; then
      forget "installed $SUDOERS_FILE"
      if [ "$how" = remove ]; then err "  undone: removed the new $SUDOERS_FILE"
      else err "  undone: put the previous $SUDOERS_FILE back"; fi
    elif [ "$how" = remove ]; then
      err "  NOT undone: $SUDOERS_FILE is still there. Run: sudo rm $SUDOERS_FILE"
    else
      err "  NOT undone: $SUDOERS_FILE is the new one; the previous one is $how. Run:"
      err "    sudo cp -p $how $SUDOERS_FILE"
    fi
    if ! visudo -c >/dev/null 2>&1; then
      err "  sudo's configuration fails visudo -c now. Fix it at once (sudo visudo -c says where):"
      err "  while it fails, sudo may refuse to run anything; pkexec visudo is the way back in."
    fi
  fi
}

dry() { [ "$DRY_RUN" = 1 ]; }

# Run a command as the service user. Root never runs git, docker or the
# venv's Python for the checkout: the same rule apply-update.sh keeps.
as_user() { sudo -n -u "$SVC_USER" -- "$@"; }

# A property of a systemd unit, or nothing.
unit_prop() { systemctl show -p "$2" --value "$1" 2>/dev/null || true; }

# The last KEY=value for KEY in /etc/default/domovoi-update, quotes
# stripped, or nothing: what domovoi-update.service will run apply-update.sh
# with.
defaults_get() {
  local v
  [ -r "$DEFAULTS_FILE" ] || return 0
  v=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$DEFAULTS_FILE" | tail -n 1 | tr -d '\r')
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  printf '%s' "$v"
}

is_sha() { [[ $1 =~ ^[0-9a-f]{40}([0-9a-f]{24})?$ ]]; }

# $1 with symlinks resolved, when it is a directory; else as given.
canon() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; }

# Is version $1 at least $2?
version_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" = "$2" ]; }

# The last few lines of $1 that say something, for an error.
tail_of() { printf '%s\n' "$1" | grep -v -e 'parsed OK' -e '^[[:space:]]*$' | tail -n "${2:-3}" || true; }

# Where file $1 stands against the wanted content in file $2.
file_state() {
  if [ -L "$1" ]; then echo link
  elif [ ! -e "$1" ]; then echo new
  elif [ ! -s "$1" ]; then echo empty
  elif cmp -s "$1" "$2"; then echo same
  else echo differs
  fi
}

# Put file $2 in place as $1, root-owned with mode $3, by rename from a
# mktemp dot-name beside it, never a name fixed in advance. sudo skips names
# with a dot in sudoers.d and systemd skips names without a unit suffix, so
# neither ever reads half a file. On failure $1 is as it was, and it
# returns non-zero.
put_file() {
  local target=$1 src=$2 mode=$3 tmp
  tmp=$(mktemp "$(dirname "$target")/.$(basename "$target").XXXXXX") || return 1
  if install -m "$mode" -o root -g root "$src" "$tmp" && mv -f "$tmp" "$target"; then
    return 0
  fi
  rm -f "$tmp"
  return 1
}

# Can no one but root write $1 and every directory above it? Root writes
# there by rename, and a directory anyone else can write would let them
# swap a symlink in between root's steps. A path that doesn't exist yet is
# judged by the nearest directory above it that does; symlinks on the way
# are resolved first. When the answer is no, NOT_ROOT_ONLY names the first
# path that fails.
root_only() {
  local p=$1 st
  NOT_ROOT_ONLY=$1
  while [ ! -e "$p" ] && [ "$p" != / ]; do p=$(dirname "$p"); done
  p=$(readlink -f "$p" 2>/dev/null) || return 1
  [ -n "$p" ] || return 1
  while :; do
    st=$(stat -c '%u %a' "$p" 2>/dev/null) || st=""
    if ! [[ $st =~ ^0\ ([0-7]{3,4})$ ]] || (( 8#${BASH_REMATCH[1]} & 8#022 )); then
      NOT_ROOT_ONLY=$p
      return 1
    fi
    [ "$p" != / ] || break
    p=$(dirname "$p")
  done
  NOT_ROOT_ONLY=""
  return 0
}

# Stop unless root_only $1: $2 says what root keeps there.
need_root_only() {
  root_only "$1" && return 0
  stop "$NOT_ROOT_ONLY can be written by a user other than root" \
    "$2" \
    "A directory or file another user can write would let them change what root does there." \
    "Fix: sudo chown root:root $NOT_ROOT_ONLY && sudo chmod go-w $NOT_ROOT_ONLY, then run this again."
}

# Copy $1 to $1.bak-<UTC timestamp>, mode and owner kept; LAST_BACKUP names it.
keep_copy() {
  LAST_BACKUP=$1.bak-$(date -u +%Y%m%dT%H%M%SZ)
  cp -p "$1" "$LAST_BACKUP"
}

# The unit exactly as docs/LINUX_HOST.md gives it ("Updates from the
# dashboard"), with this checkout's path in ExecStart.
# domovoi/tests/test_install_update_unit_script.py holds the two together.
unit_text() {
  cat <<EOF
[Unit]
Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-/etc/default/domovoi-update
ExecStart=/bin/bash $REPO/scripts/linux/apply-update.sh
TimeoutStartSec=30min
EOF
}

# One top-level field of last-result.json as text: quotes dropped, null empty.
result_field() {
  local v
  v=$(sed -n "s/^  \"$2\": \(.*\)$/\1/p" "$1" | head -n 1 | sed 's/,$//')
  v=${v#\"}; v=${v%\"}
  [ "$v" != null ] || v=""
  printf '%s' "$v"
}

# ─── Pre-flight: read everything, change nothing ─────────────────────────

# domovoi-update.service runs as root with what this file sets, so only
# root may be able to change it.
check_defaults_file() {
  [ -e "$DEFAULTS_FILE" ] || [ -L "$DEFAULTS_FILE" ] || return 0
  need_root_only "$DEFAULTS_FILE" \
    "domovoi-update.service runs as root with the settings in $DEFAULTS_FILE."
  line ok "$DEFAULTS_FILE can be changed by root alone"
}

check_systemd() {
  local u state missing=()
  command -v systemctl >/dev/null 2>&1 \
    || stop "systemctl not found: the update unit needs systemd"
  [ -d "$ROOT/run/systemd/system" ] \
    || stop "systemd isn't running as the init system ($ROOT/run/systemd/system is missing)"
  [ -x "$ROOT$SYSTEMCTL_PATH" ] \
    || stop "$ROOT$SYSTEMCTL_PATH not found" \
      "The grant names that path, because it is the systemctl the core runs."
  line ok "systemd is running"
  for u in "${UNITS[@]}"; do
    state=$(unit_prop "$u" LoadState)
    [ "$state" = loaded ] || missing+=("$u (${state:-unknown})")
  done
  [ "${#missing[@]}" -eq 0 ] \
    || stop "not installed: ${missing[*]}" \
      "This sets up the update unit on a box that already runs the three units in docs/LINUX_HOST.md, \"Make it an appliance\"."
  line ok "${UNITS[*]} are installed"
  if ! command -v sudo >/dev/null 2>&1 || ! command -v visudo >/dev/null 2>&1; then
    stop "sudo and visudo are needed: the Restart button starts the update through sudo"
  fi
}

check_repo() {
  local wd s
  wd=$(unit_prop "$CORE_UNIT" WorkingDirectory)
  wd=${wd#[-!+]}
  wd=${wd%/}
  if [ -z "$REPO" ]; then REPO=${wd:-$DEFAULT_REPO}; fi
  REPO=${REPO%/}
  if ! [[ $REPO =~ ^/[A-Za-z0-9._/@+-]+$ ]] || [[ $REPO/ == *//* || $REPO/ == */./* || $REPO/ == */../* ]]; then
    stop "the checkout path must be absolute and plain (letters, digits and . _ / @ + -, no . or .. parts): $REPO" \
      "It goes into the unit's ExecStart line as it is."
  fi
  [ -d "$REPO" ] \
    || stop "no checkout at $REPO" "Pass --repo with the directory domovoi-core.service runs from."
  if [ -n "$wd" ] && [ "$(canon "$wd")" != "$(canon "$REPO")" ]; then
    stop "domovoi-core.service runs from $wd, not $REPO" \
      "The update has to apply to the checkout the core runs: drop --repo, or pass --repo $wd."
  fi
  [ -f "$REPO/scripts/linux/apply-update.sh" ] \
    || stop "$REPO has no scripts/linux/apply-update.sh" \
      "That checkout predates the update unit. Pull first (Pull the latest in the dashboard), don't restart, then run this again."
  [ -f "$REPO/domovoi/docker-compose.yml" ] \
    || stop "$REPO has no domovoi/docker-compose.yml: is it a Domovoi checkout?"
  s=$(defaults_get DOMOVOI_REPO_DIR)
  if [ -n "$s" ] && [ "$(canon "$s")" != "$(canon "$REPO")" ]; then
    stop "$DEFAULTS_FILE sets DOMOVOI_REPO_DIR=$s, but the checkout is $REPO" \
      "The update would apply to the wrong tree. Fix or remove that line, then run this again."
  fi
  if [ -n "$wd" ]; then
    line ok "checkout $REPO (domovoi-core.service's WorkingDirectory)"
  else
    line ok "checkout $REPO"
  fi
}

check_user() {
  local core_user s
  if [ -z "$SVC_USER" ]; then
    SVC_USER=$(stat -c %U "$REPO" 2>/dev/null || true)
    USER_FROM="the owner of $REPO"
    [ -n "$SVC_USER" ] || stop "can't tell who owns $REPO" "Pass --user with the service user."
  fi
  [[ $SVC_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] \
    || stop "not a user name the grant can use: $SVC_USER${USER_FROM:+ ($USER_FROM)}" \
      "Pass --user with the user domovoi-core.service runs as."
  id -u "$SVC_USER" >/dev/null 2>&1 || stop "no such user: $SVC_USER"
  [ "$(id -u "$SVC_USER")" != 0 ] \
    || stop "$SVC_USER${USER_FROM:+ ($USER_FROM)} is root" \
      "The services run as their own user, User= in their units: pass --user with it."
  core_user=$(unit_prop "$CORE_UNIT" User)
  [ -n "$core_user" ] \
    || stop "domovoi-core.service has no User=, so the core runs as root" \
      "The grant is for the service user the core runs as; see the units in docs/LINUX_HOST.md."
  if [ "$core_user" != "$SVC_USER" ]; then
    stop "domovoi-core.service runs as $core_user, but the service user here is $SVC_USER${USER_FROM:+ ($USER_FROM)}" \
      "The grant has to name the user the core runs as, because the core is what calls sudo." \
      "If $core_user is the service user, pass --user $core_user."
  fi
  s=$(defaults_get DOMOVOI_USER)
  if [ -n "$s" ] && [ "$s" != "$SVC_USER" ]; then
    stop "$DEFAULTS_FILE sets DOMOVOI_USER=$s, but the core runs as $SVC_USER" \
      "Fix or remove that line, then run this again."
  fi
  line ok "service user $SVC_USER (domovoi-core.service's User=)"
  # The service user's commands run from here: python -m puts the cwd on
  # sys.path, and the admin's own cwd may be closed to them.
  cd "$REPO"
  if ! HEAD_SHA=$(as_user git -C "$REPO" rev-parse --verify HEAD 2>/dev/null) || ! is_sha "$HEAD_SHA"; then
    stop "git can't read $REPO as $SVC_USER, and every update runs git as that user" \
      "See what it says: sudo -u $SVC_USER git -C $REPO status"
  fi
  line ok "git works in $REPO as $SVC_USER (HEAD ${HEAD_SHA:0:12})"
}

check_venv() {
  local exe d foreign
  # The venv apply-update.sh will sync: DOMOVOI_VENV, else the one the core
  # unit's ExecStart runs from, else <checkout>/.venv.
  VENV=$(defaults_get DOMOVOI_VENV)
  if [ -z "$VENV" ]; then
    exe=$(unit_prop "$CORE_UNIT" ExecStart | sed -n 's/^{ path=\([^ ;]*\) ;.*/\1/p' | head -n 1)
    case $exe in
      */bin/python*)
        d=${exe%/bin/python*}
        if [ -f "$d/pyvenv.cfg" ]; then VENV=$d; fi
        ;;
    esac
  fi
  VENV=${VENV:-$REPO/.venv}
  if [[ $VENV != /?* ]] || [ ! -x "$VENV/bin/python" ]; then
    line warning "no venv interpreter at $VENV/bin/python"
    more "An update that changes dependencies would abort. If the venv is elsewhere, set"
    more "DOMOVOI_VENV in $DEFAULTS_FILE to its absolute path."
    return 0
  fi
  VENV_OK=1
  # The directory itself, symlinks resolved: find and chown -R don't follow
  # a symlink, so both have to be handed the real venv.
  VENV=$(readlink -f "$VENV")
  # Printed, so no control characters from a file name.
  foreign=$(find "$VENV" ! -user "$SVC_USER" -print -quit 2>/dev/null | tr -cd '[:print:]' || true)
  if [ -z "$foreign" ]; then
    line ok "$VENV belongs to $SVC_USER"
  elif [ "$FIX_OWNERSHIP" = 1 ] && [ -f "$VENV/pyvenv.cfg" ] && [[ $VENV == /?*/?* ]]; then
    NEED_CHOWN=1
    line fix "$VENV isn't all $SVC_USER's (first found: $foreign); --fix-ownership hands it over"
  else
    line warning "$VENV isn't all $SVC_USER's (first found: $foreign)"
    more "pip runs as $SVC_USER, so an update that changes dependencies would abort. Fix:"
    more "  sudo chown -R $SVC_USER: $VENV"
    if [ "$FIX_OWNERSHIP" = 1 ]; then
      # A chown -R as root on the wrong directory (DOMOVOI_VENV=/opt, say)
      # would hand a whole tree to the service user.
      more "--fix-ownership leaves it alone: $VENV doesn't look like a venv (no pyvenv.cfg, or at the top level)."
    else
      more "or run this again with --fix-ownership."
    fi
  fi
}

check_docker() {
  local out
  if ! out=$(as_user docker compose -f "$REPO/domovoi/docker-compose.yml" ps --quiet 2>&1 >/dev/null); then
    stop "docker compose doesn't work for $SVC_USER: $(tail_of "$out" 1)" \
      "domovoi-db runs it as $SVC_USER, and so does every update. The usual fix is" \
      "  sudo usermod -aG docker $SVC_USER" \
      "then restart the services. Check with: sudo -u $SVC_USER docker compose -f $REPO/domovoi/docker-compose.yml ps"
  fi
  line ok "docker compose works for $SVC_USER"
}

# Warn only: the Piper voice needs piper-tts 1.3 (clients/tts.py imports
# SynthesisConfig, new in 1.3), and on an older one every Piper render falls
# through to the next engine without an error.
check_piper() {
  local v
  [ "$VENV_OK" = 1 ] || return 0
  v=$(as_user "$VENV/bin/python" -m pip --disable-pip-version-check show piper-tts 2>/dev/null \
      | sed -n 's/^Version:[[:space:]]*//p' | head -n 1 | tr -d '\r') || v=""
  if [ -z "$v" ]; then
    line warning "piper-tts isn't installed in $VENV, so the Piper voice can't render"
    more "It comes with the real-clients extra (piper-tts>=1.3). To install it now:"
    more "  sudo -u $SVC_USER $VENV/bin/python -m pip install 'piper-tts>=1.3'"
  elif ! [[ $v =~ ^[0-9][0-9A-Za-z.+!-]*$ ]]; then
    line warning "can't read the version of piper-tts in $VENV"
  elif version_ge "$v" 1.3; then
    line ok "piper-tts $v"
  else
    line warning "piper-tts $v in $VENV is older than 1.3: every Piper render falls through"
    more "to the next voice. An update re-syncs the venv when pyproject.toml changed since the"
    more "recorded SHA; to fix it now:"
    more "  sudo -u $SVC_USER $VENV/bin/python -m pip install 'piper-tts>=1.3'"
  fi
}

# Where the core mirrors the household token, which GET /v1/admin/version
# wants since CORE-21: ~/.domovoi/device-token.txt of the user it runs as.
# Prints nothing when that user's home is unknown.
device_token_file() {
  local home=""
  if command -v getent >/dev/null 2>&1; then
    home=$(getent passwd "$SVC_USER" 2>/dev/null | cut -d: -f6) || home=""
  fi
  if [[ $home == /?* ]]; then printf '%s' "$home/.domovoi/device-token.txt"; fi
}

# read_device_token FILE: the token in FILE, read AS the service user and
# never as root: the file sits in a directory that user controls, and root
# following a link planted there would hand the core whatever root can
# read. Prints nothing when there is no token to read.
read_device_token() {
  local tok="" LC_ALL=C
  [ -n "${1-}" ] || return 0
  tok=$(as_user cat -- "$1" 2>/dev/null | head -c 4096 | tr -d '\r\n') || tok=""
  # Printable ASCII, as the core stores it: nothing that could end the
  # header line and start another.
  if [[ $tok =~ ^[[:print:]]+$ ]]; then printf '%s' "$tok"; fi
}

# applied_sha missing: the SHA the running core reports. Never HEAD: a pull
# moves the checkout without touching what the core runs, and an update that
# took HEAD as its baseline would see nothing to apply.
read_running_sha() {
  local url=${CORE_URL%/}/v1/admin/version body rc=0 running full token_file token
  token_file=$(device_token_file)
  token=$(read_device_token "$token_file")
  # -q first: no ~/.curlrc. Straight to the core on this box, never through
  # a proxy from the environment, no other protocol, nothing large. The
  # token rides on stdin (-H @-), never on the command line, where any
  # local user could read it.
  if [ -n "$token" ]; then
    body=$(printf 'X-Device-Token: %s\n' "$token" \
      | curl -q -fsS --noproxy '*' --proto '=http,https' --max-time 10 --max-filesize 1048576 \
        -H @- "$url" 2>/dev/null) || rc=$?
  else
    body=$(curl -q -fsS --noproxy '*' --proto '=http,https' --max-time 10 --max-filesize 1048576 \
      "$url" 2>/dev/null) || rc=$?
  fi
  if [ "$rc" = 22 ]; then
    # -f: the core answered, with an HTTP error (401 without the token).
    stop "the core at $url refused the version read (curl exit 22), and no rollback baseline is recorded yet" \
      "That read takes the household token, which this reads as $SVC_USER from ${token_file:-~$SVC_USER/.domovoi/device-token.txt}." \
      "The core writes that file at every start. Check it is there and readable by $SVC_USER, then run this again."
  fi
  if [ "$rc" != 0 ]; then
    stop "the core isn't answering at $url (curl exit $rc), and no rollback baseline is recorded yet" \
      "The baseline is the SHA the core is running, and only the running core can say which that is." \
      "The checkout's HEAD is no stand-in: a pull may have moved it past the running code." \
      "Bring the core up (sudo systemctl start domovoi-core) if nothing was pulled since it last ran, or pass --core-url; then run this again."
  fi
  # The one field; the rest of the answer is never printed.
  running=$(printf '%s' "$body" | tr -d '\r\n' \
    | sed -n 's/.*"running_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
  running=${running%-dirty}
  if ! [[ $running =~ ^[0-9a-f]{4,64}$ ]]; then
    running=$(printf '%s' "$running" | tr -cd '[:alnum:]._-' | cut -c1-40)
    stop "the core at $url doesn't say which commit it runs (running_sha: ${running:-missing})" \
      "Restart it only if nothing was pulled since it last ran, then run this again."
  fi
  if ! full=$(as_user git -C "$REPO" rev-parse --verify --quiet "$running^{commit}" 2>/dev/null) || ! is_sha "$full"; then
    stop "the core runs $running, which isn't a commit in $REPO" \
      "The rollback baseline has to be a commit the update can reset to." \
      "If the core runs another checkout, pass --repo. If the commit is missing here, fetch it (sudo -u $SVC_USER git -C $REPO fetch) and run this again."
  fi
  # git prefers a branch or tag over a short SHA of the same spelling.
  if [[ $full != "$running"* ]]; then
    stop "$running names ${full:0:12} in $REPO, a branch or tag of that name rather than the commit the core runs" \
      "Rename or delete that ref as $SVC_USER (git -C $REPO show-ref $running shows it), then run this again."
  fi
  RECORD_SHA=$full
  BASELINE=$full
  line ok "the core runs ${full:0:12}; that becomes the rollback baseline"
}

check_baseline() {
  local s
  s=$(defaults_get DOMOVOI_UPDATE_DIR)
  s=${s:-$DEFAULT_UPDATE_DIR}
  s=${s%/}
  # apply-update.sh runs from /, so a relative path would name another place.
  [[ $s == /?* ]] || stop "DOMOVOI_UPDATE_DIR in $DEFAULTS_FILE must be an absolute path: $s"
  UPDATE_DIR=$ROOT$s
  APPLIED_FILE=$UPDATE_DIR/applied_sha
  if [ -L "$UPDATE_DIR" ] || { [ -e "$UPDATE_DIR" ] && [ ! -d "$UPDATE_DIR" ]; }; then
    stop "$UPDATE_DIR isn't a plain directory (it is a symlink, or a file)" \
      "Root keeps the rollback baseline and every update's result there. Look at it, and move it aside if it isn't meant to be there."
  fi
  need_root_only "$UPDATE_DIR" \
    "Root keeps the rollback baseline and every update's result in $UPDATE_DIR, and writes them there by rename."
  if [ ! -e "$APPLIED_FILE" ] && [ ! -L "$APPLIED_FILE" ]; then
    read_running_sha
    return 0
  fi
  s=""
  if [ -f "$APPLIED_FILE" ] && [ ! -L "$APPLIED_FILE" ]; then
    s=$(head -c 200 "$APPLIED_FILE" 2>/dev/null | tr -d '[:space:]') || s=""
  fi
  if is_sha "$s" && as_user git -C "$REPO" cat-file -e "$s^{commit}" 2>/dev/null; then
    BASELINE=$s
    line ok "rollback baseline already recorded: ${s:0:12} ($APPLIED_FILE)"
    return 0
  fi
  stop "$APPLIED_FILE doesn't hold a commit of $REPO" \
    "An update would ignore it and fall back to a weaker baseline. While the core still runs the old code," \
    "remove it (sudo rm $APPLIED_FILE) and run this again to record the SHA the core runs."
}

# ─── Signed updates (pre-flight; see apply-update.sh, "Signed updates") ──

# A remote URL for comparing: no trailing slash or .git.
norm_url() {
  local u=$1
  u=${u%/}; u=${u%.git}; u=${u%/}
  printf '%s' "$u"
}

# HEAD against the allowed-signers file $1, as root with the same pins
# apply-update.sh uses (the checkout's .git/config belongs to the service
# user, so what git may run is fixed here, not there). Prints the signer
# and returns 0, or prints why not and returns 1.
verify_head_as_root() {
  local -a cfg=(-c "safe.directory=$REPO" -c "gpg.ssh.allowedSignersFile=$1"
                -c gpg.ssh.program=ssh-keygen -c gpg.program=gpg
                -c gpg.openpgp.program=gpg -c gpg.x509.program=gpgsm)
  local out signer mark
  if out=$(git "${cfg[@]}" -C "$REPO" verify-commit HEAD 2>&1); then
    signer=$(git "${cfg[@]}" -C "$REPO" log -1 --format=%GS HEAD 2>/dev/null) || signer=""
    printf '%s' "${signer:-an allowed key}"
    return 0
  fi
  mark=$(git "${cfg[@]}" -C "$REPO" log -1 --format=%G? HEAD 2>/dev/null) || mark=""
  if [ "$mark" = N ]; then
    printf 'it is not signed'
  else
    printf 'its signature is not by a key in %s (%s)' "$1" "$(tail_of "$out" 1 | tr -d '\n')"
  fi
  return 1
}

# With the allowed-signers file in place every update run verifies HEAD
# against it and refuses otherwise; with the upstream pinned every run
# checks the checkout's origin and tracking branch. Both are checked here
# the way the unit will, so a box that would refuse every run hears it
# now, and one that has not set signing up hears that once.
check_signing() {
  local f url origin want up signer
  f=$(defaults_get DOMOVOI_ALLOWED_SIGNERS)
  f=$ROOT${f:-/etc/domovoi/allowed_signers}
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then
    line warning "signed updates are not enforced: no $f"
    more "Every update runs what upstream holds as root. To bound that to commits you signed,"
    more "install the allowed-signers file: docs/LINUX_HOST.md, Signed updates."
  else
    if [ -L "$f" ]; then
      stop "$f is a symlink, and the update unit refuses it" \
        "Replace it with a plain file owned by root (install -m 0644 -o root -g root)."
    fi
    need_root_only "$f" "The update unit verifies every HEAD against the keys in $f, as root."
    if ! signer=$(verify_head_as_root "$f"); then
      stop "signed updates are enforced by $f, but HEAD ${HEAD_SHA:0:12} does not verify: $signer" \
        "The update unit would refuse every run. Pull a commit signed by a key in $f (Pull the latest in the dashboard)," \
        "or remove the file to turn enforcement off, then run this again."
    fi
    line ok "signed updates enforced: HEAD ${HEAD_SHA:0:12} is signed by $signer ($f)"
  fi
  url=$(defaults_get DOMOVOI_UPSTREAM_URL)
  if [ -n "$url" ]; then
    origin=$(as_user git -C "$REPO" remote get-url origin 2>/dev/null) || origin=""
    if [ "$(norm_url "$origin")" != "$(norm_url "$url")" ]; then
      stop "$DEFAULTS_FILE pins the upstream to $url, but the checkout's origin is ${origin:-not set}" \
        "The update unit would refuse every run. Fix the remote as $SVC_USER (git -C $REPO remote set-url origin $url)," \
        "or the pin, then run this again."
    fi
    line ok "upstream pinned: origin is $url"
  fi
  want=$(defaults_get DOMOVOI_UPSTREAM_BRANCH)
  if [ -n "$want" ]; then
    up=$(as_user git -C "$REPO" rev-parse --symbolic-full-name '@{u}' 2>/dev/null) || up=""
    if [ "$up" != "refs/remotes/origin/$want" ]; then
      stop "$DEFAULTS_FILE pins the branch to origin/$want, but HEAD tracks ${up:-no upstream branch}" \
        "The update unit would refuse every run. Fix the tracking branch as $SVC_USER (git -C $REPO branch --set-upstream-to origin/$want)," \
        "or the pin, then run this again."
    fi
    line ok "upstream pinned: HEAD tracks origin/$want"
  fi
}

check_sudoers() {
  local out
  if ! out=$(visudo -c 2>&1); then
    stop "sudoers already fails visudo -c" "$(tail_of "$out")" \
      "Fix that first (sudo visudo -c says where). This won't add a file to a configuration that is already broken."
  fi
  SUDOERS_RULE="$SVC_USER ALL=(root) NOPASSWD: ${GRANT_ARGV[*]}"
  printf '%s\n' "$SUDOERS_RULE" >"$TMPD/sudoers"
  chmod 0440 "$TMPD/sudoers"
  if ! out=$(visudo -cf "$TMPD/sudoers" 2>&1); then
    stop "visudo rejects the rule: $(tail_of "$out" 1)" "The rule: $SUDOERS_RULE"
  fi
  SUDOERS_STATE=$(file_state "$SUDOERS_FILE" "$TMPD/sudoers")
  case $SUDOERS_STATE in
    link) stop "$SUDOERS_FILE is a symlink; not writing through it" "Look at it, remove it, and run this again." ;;
    same) line ok "sudoers passes visudo -c, and $SUDOERS_FILE already holds the grant" ;;
    new) line ok "sudoers passes visudo -c, and the new rule passes visudo -cf" ;;
    *) line ok "sudoers passes visudo -c, and the new rule passes visudo -cf"
       more "$SUDOERS_FILE holds something else; it is replaced, and the old one kept" ;;
  esac
}

check_unit() {
  unit_text >"$TMPD/unit"
  UNIT_STATE=$(file_state "$UNIT_FILE" "$TMPD/unit")
  case $UNIT_STATE in
    link|empty)
      stop "$UNIT_FILE is masked (a symlink or an empty file)" \
        "Someone switched the update unit off on purpose. If it should be on: sudo systemctl unmask $UNIT_NAME, then run this again." ;;
    same) line ok "$UNIT_FILE is already the doc's unit" ;;
    differs) line ok "$UNIT_FILE differs from the doc's; it is replaced, and the old one kept" ;;
  esac
}

# ─── The changes ─────────────────────────────────────────────────────────

record_baseline() {
  [ -n "$RECORD_SHA" ] || return 0
  if dry; then line would "record $RECORD_SHA in $APPLIED_FILE"; return 0; fi
  if [ ! -d "$UPDATE_DIR" ]; then install -d -m 0755 -o root -g root "$UPDATE_DIR"; fi
  printf '%s\n' "$RECORD_SHA" >"$TMPD/applied_sha"
  put_file "$APPLIED_FILE" "$TMPD/applied_sha" 0644 || stop "couldn't write $APPLIED_FILE"
  DONE+=("recorded $RECORD_SHA in $APPLIED_FILE")
  line done "recorded $RECORD_SHA in $APPLIED_FILE"
}

install_grant() {
  local out how=remove
  case $SUDOERS_STATE in new|empty|differs) ;; *) return 0 ;; esac
  if dry; then line would "install $SUDOERS_FILE (0440): $SUDOERS_RULE"; return 0; fi
  if [ "$SUDOERS_STATE" != new ]; then
    keep_copy "$SUDOERS_FILE"
    how=$LAST_BACKUP
    line kept "the previous $SUDOERS_FILE as $how"
  fi
  # From here until the unit is loaded, a stop puts this back (undo_changes).
  GRANT_UNDO=$how
  if ! put_file "$SUDOERS_FILE" "$TMPD/sudoers" 0440; then
    GRANT_UNDO=""
    stop "couldn't write $SUDOERS_FILE (it is as it was)"
  fi
  DONE+=("installed $SUDOERS_FILE")
  if ! out=$(visudo -c 2>&1); then
    stop "visudo -c failed with the new $SUDOERS_FILE in place" "$(tail_of "$out")" \
      "The update unit was not installed."
  fi
  line done "installed $SUDOERS_FILE: $SUDOERS_RULE"
}

# The probe domovoi/self_restart.py runs to decide whether to offer the
# button, run as the service user. Read-only: it asks, it doesn't start.
verify_grant() {
  if dry && [ "$SUDOERS_STATE" != same ]; then
    line would "check the grant as $SVC_USER: sudo -n -l ${GRANT_ARGV[*]}"
    return 0
  fi
  if ! as_user sudo -n -l "${GRANT_ARGV[@]}" >/dev/null 2>&1; then
    stop "sudo doesn't let $SVC_USER run: ${GRANT_ARGV[*]}" \
      "The rule passes visudo, so either something later in sudoers overrides it, or sudo doesn't read $(dirname "$SUDOERS_FILE") (/etc/sudoers needs its @includedir line)." \
      "The update unit was not installed, so the Restart button keeps doing what it did before."
  fi
  line ok "$SVC_USER may run: ${GRANT_ARGV[*]}"
}

install_unit() {
  local reload=0 state out how=remove
  case $UNIT_STATE in
    new|differs)
      reload=1
      if dry; then
        line would "install $UNIT_FILE (ExecStart=/bin/bash $REPO/scripts/linux/apply-update.sh)"
      else
        if [ "$UNIT_STATE" = differs ]; then
          keep_copy "$UNIT_FILE"
          how=$LAST_BACKUP
          line kept "the previous $UNIT_FILE as $how"
        fi
        UNIT_UNDO=$how
        if ! put_file "$UNIT_FILE" "$TMPD/unit" 0644; then
          UNIT_UNDO=""
          stop "couldn't write $UNIT_FILE (it is as it was)"
        fi
        DONE+=("installed $UNIT_FILE")
        line done "installed $UNIT_FILE"
      fi
      ;;
    same)
      # In place, but maybe never loaded, or changed since systemd read it.
      if [ "$(unit_prop "$UNIT_NAME" LoadState)" != loaded ]; then reload=1; fi
      if [ "$(unit_prop "$UNIT_NAME" NeedDaemonReload)" = yes ]; then reload=1; fi
      ;;
  esac
  if [ "$reload" = 1 ]; then
    if dry; then line would "systemctl daemon-reload"; return 0; fi
    if ! out=$(systemctl daemon-reload 2>&1); then
      stop "systemctl daemon-reload failed: $(tail_of "$out" 1)"
    fi
    DONE+=("systemctl daemon-reload")
    line done "systemctl daemon-reload"
  fi
  dry && return 0
  state=$(unit_prop "$UNIT_NAME" LoadState)
  if [ "$state" != loaded ]; then
    stop "systemd doesn't load $UNIT_NAME (LoadState: ${state:-unknown})" \
      "journalctl -b | grep $UNIT_NAME may say why."
  fi
  # The grant and the unit are both in and loaded: nothing is taken back
  # from here on.
  GRANT_UNDO=""
  UNIT_UNDO=""
  return 0
}

# Last among the changes, because a chown can't be taken back.
fix_ownership() {
  [ "$NEED_CHOWN" = 1 ] || return 0
  if dry; then line would "chown -R $SVC_USER: $VENV"; return 0; fi
  if ! chown -R "$SVC_USER:" "$VENV"; then
    stop "chown -R $SVC_USER: $VENV failed" \
      "The update unit and its grant are in place. Fix the venv's ownership by hand before an update needs it."
  fi
  DONE+=("chown -R $SVC_USER: $VENV")
  line done "chown -R $SVC_USER: $VENV"
}

# --apply: run the update once, the way the button will, and report it.
apply_now() {
  local rc=0 result=$UPDATE_DIR/last-result.json status mode from to err
  PHASE=apply
  if [ -f "$result" ]; then cp "$result" "$TMPD/before.json"; fi
  say ""
  say "Applying: systemctl start $UNIT_NAME"
  say "  It waits until the update has run; journalctl -u domovoi-update -f follows it."
  systemctl start "$UNIT_NAME" || rc=$?
  if [ ! -f "$result" ] || { [ -f "$TMPD/before.json" ] && cmp -s "$result" "$TMPD/before.json"; }; then
    stop "$UNIT_NAME wrote no result (systemctl exit $rc)" "See: journalctl -u domovoi-update -n 200"
  fi
  status=$(result_field "$result" status)
  mode=$(result_field "$result" mode)
  from=$(result_field "$result" from_sha)
  to=$(result_field "$result" to_sha)
  err=$(result_field "$result" error)
  line result "${status:-unknown} (mode ${mode:-unknown}, ${from:0:12} -> ${to:0:12})"
  case $status in
    ok)
      say ""
      say "From now on, Pull the latest followed by Restart to apply changes runs the whole update."
      return 0
      ;;
    rolled_back)
      more "The box is back on ${from:0:12}: $err"
      more "If that SHA predates the update unit, its Restart button is still the plain bounce:"
      more "once the cause is fixed, pull again and run: sudo systemctl start $UNIT_NAME"
      ;;
    refused)
      case $err in
        *"uncommitted changes"*)
          more "Tracked files in $REPO have local changes. Commit or stash them as $SVC_USER,"
          more "then run: sudo systemctl start $UNIT_NAME" ;;
        *)
          # A signature or upstream refusal: the error says what to fix.
          more "Nothing was changed: $err" ;;
      esac
      ;;
    aborted)
      more "Nothing was changed: $err"
      ;;
    *)
      [ -z "$err" ] || more "$err"
      ;;
  esac
  more "Detail: journalctl -u domovoi-update -n 200"
  REPORTED=1
  exit 1
}

next_steps() {
  say ""
  say "Next"
  if [ -n "$BASELINE" ] && [ "$BASELINE" != "$HEAD_SHA" ]; then
    say "  The checkout (${HEAD_SHA:0:12}) is ahead of the running code (${BASELINE:0:12})."
    say "  Restart to apply changes in the dashboard applies it, or from here:"
  else
    say "  Nothing is waiting: the checkout is what the core runs. From now on, Pull the"
    say "  latest followed by Restart to apply changes runs the whole update. From here:"
  fi
  say "    sudo systemctl start $UNIT_NAME"
  say "    cat $UPDATE_DIR/last-result.json"
}

main() {
  while [ $# -gt 0 ]; do
    case $1 in
      --dry-run) DRY_RUN=1; shift ;;
      --apply) APPLY=1; shift ;;
      --fix-ownership) FIX_OWNERSHIP=1; shift ;;
      --repo|--user|--core-url)
        [ $# -ge 2 ] && [ -n "$2" ] || usage_error "$1 needs a value"
        case $1 in
          --repo) REPO=$2 ;;
          --user) SVC_USER=$2; USER_FROM="--user" ;;
          --core-url) CORE_URL=$2 ;;
        esac
        shift 2
        ;;
      --repo=*) REPO=${1#*=}; shift ;;
      --user=*) SVC_USER=${1#*=}; USER_FROM="--user"; shift ;;
      --core-url=*) CORE_URL=${1#*=}; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage_error "unknown option: $1" ;;
    esac
  done
  # The running SHA has to come from the core on this box, the one that
  # runs this checkout. Not echoed: a URL can carry a password.
  [[ $CORE_URL =~ ^https?://(127\.0\.0\.1|localhost|\[::1\])(:[0-9]{1,5})?/?$ ]] \
    || usage_error "--core-url must be the core on this box: http://127.0.0.1:PORT, http://localhost:PORT or http://[::1]:PORT"

  if [ "$(id -u)" != 0 ]; then
    local args=""
    if [ "${#ORIG_ARGS[@]}" -gt 0 ]; then args=$(printf ' %q' "${ORIG_ARGS[@]}"); fi
    printf '%s: this needs root; run it with sudo:\n  sudo bash %s%s\n' "$PROG" "$SCRIPT_PATH" "$args" >&2
    exit 1
  fi

  umask 022
  trap on_exit EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  TMPD=$(mktemp -d)

  if [ -n "$ROOT" ]; then say "(test harness: system paths under $ROOT)"; fi
  if dry; then say "Pre-flight (dry run: nothing is changed)"; else say "Pre-flight (nothing is changed yet)"; fi
  check_defaults_file
  check_systemd
  check_repo
  check_user
  check_venv
  check_docker
  check_piper
  check_baseline
  check_signing
  check_sudoers
  check_unit

  say ""
  if dry; then say "Would change"; else say "Changes"; fi
  record_baseline
  install_grant
  verify_grant
  install_unit
  fix_ownership

  say ""
  if dry; then
    say "Dry run: nothing was changed. Run it again without --dry-run to make the changes above."
    if [ "$APPLY" = 1 ]; then say "With --apply it would then run: systemctl start $UNIT_NAME"; fi
    exit 0
  fi
  if [ "${#DONE[@]}" -eq 0 ]; then
    say "Nothing to change: the update unit, its grant and the rollback baseline were already in place."
  else
    say "Done: the Restart button now runs the whole update."
  fi
  if [ "$APPLY" = 1 ]; then
    apply_now
  else
    next_steps
  fi
}

# One line, and main is one function: bash has read all of this before
# --apply's update can move the checkout (and this file) underneath it.
main "$@"; exit $?
