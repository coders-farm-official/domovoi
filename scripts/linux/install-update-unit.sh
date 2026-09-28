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
#      passes visudo -cf on its own. Two things only warn: a venv the
#      service user doesn't wholly own (an update that changes dependencies
#      would abort; the chown that fixes it is printed, and --fix-ownership
#      runs it), and piper-tts older than 1.3 in the venv.
#   2. The rollback baseline. When applied_sha isn't recorded yet, it becomes
#      the SHA the core is RUNNING: running_sha from GET
#      <core>/v1/admin/version, -dirty stripped, verified as a commit of the
#      checkout by the service user. Not HEAD, which a pull may already have
#      moved past the running code. If the core can't say, this stops; it
#      never guesses.
#   3. The grant, /etc/sudoers.d/domovoi-update (0440), put in place only
#      after visudo -cf passed on a copy, then visudo -c on the whole
#      configuration; if that fails, the previous state is put back. Then
#      the grant is checked as the service user with the probe the core
#      itself runs (sudo -n -l, domovoi/self_restart.py).
#   4. /etc/systemd/system/domovoi-update.service, exactly as the doc gives
#      it, and systemctl daemon-reload. The unit goes in after the grant is
#      verified, not before: the core offers the full update as soon as it
#      sees the unit file, and without the grant its Restart button would
#      stop working instead of falling back to the plain bounce.
#   5. With --apply: systemctl start domovoi-update.service, which waits
#      for the run, then the status from its last-result.json.
#
# What is already in place is left alone, so a second run changes nothing
# and says so. A file it replaces is kept beside it as
# <name>.bak-<UTC timestamp>, a name sudo and systemd both ignore.
#
# Settings: the options in usage() below, and the same
# /etc/default/domovoi-update that apply-update.sh reads (DOMOVOI_UPDATE_DIR,
# DOMOVOI_VENV, and DOMOVOI_USER / DOMOVOI_REPO_DIR, which must agree with
# what the core runs as and from).
#
# DOMOVOI_INSTALL_ROOT is for the tests only
# (scripts/linux/tests/test-install-update-unit.sh): a directory put in
# front of every system path this reads or writes (/etc/..., /var/lib/...,
# /run/systemd/system, /usr/bin/systemctl). Never set it on a real host. The
# checkout and the venv are real paths either way, since the unit names
# them.

set -euo pipefail

PROG=install-update-unit
SCRIPT_PATH=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
ORIG_ARGS=("$@")

ROOT=${DOMOVOI_INSTALL_ROOT:-}
ROOT=${ROOT%/}

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
HEAD_SHA=""
BASELINE=""      # the rollback baseline: recorded already, or about to be
RECORD_SHA=""    # set when applied_sha has to be written
NEED_CHOWN=0
SUDOERS_RULE=""
SUDOERS_STATE=""
UNIT_STATE=""
LAST_BACKUP=""
TMPD=""
DONE=()          # what this run changed, for the summary and for a stop

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
  --core-url URL    where the core answers (default: http://127.0.0.1:6370)
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

# Stop with a reason and what to do about it, and say what, if anything,
# this run had already changed.
stop() {
  local l d
  printf '\n%s: stopped: %s\n' "$PROG" "$1" >&2
  shift
  for l in "$@"; do
    [ -z "$l" ] || printf '  %s\n' "$l" >&2
  done
  if [ "${#DONE[@]}" -eq 0 ]; then
    printf 'Nothing was changed.\n' >&2
  else
    printf 'Changed before the stop:\n' >&2
    for d in "${DONE[@]}"; do printf '  %s\n' "$d" >&2; done
  fi
  exit 1
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
# dot-name beside it. sudo skips names with a dot in sudoers.d and systemd
# skips names without a unit suffix, so neither ever reads half a file.
put_file() {
  local target=$1 src=$2 mode=$3 tmp
  tmp=$(dirname "$target")/.$(basename "$target").new
  install -m "$mode" -o root -g root "$src" "$tmp"
  mv -f "$tmp" "$target"
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
  [[ $REPO =~ ^/[A-Za-z0-9._/@+-]+$ ]] \
    || stop "the checkout path must be absolute and plain (letters, digits and . _ / @ + -): $REPO" \
      "It goes into the unit's ExecStart line as it is."
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
  if [ ! -x "$VENV/bin/python" ]; then
    line warning "no venv interpreter at $VENV/bin/python"
    more "An update that changes dependencies would abort. If the venv is elsewhere, set"
    more "DOMOVOI_VENV in $DEFAULTS_FILE."
    return 0
  fi
  foreign=$(find "$VENV" ! -user "$SVC_USER" -print -quit 2>/dev/null || true)
  if [ -z "$foreign" ]; then
    line ok "$VENV belongs to $SVC_USER"
  elif [ "$FIX_OWNERSHIP" = 1 ]; then
    NEED_CHOWN=1
    line fix "$VENV isn't all $SVC_USER's (first found: $foreign); --fix-ownership hands it over"
  else
    line warning "$VENV isn't all $SVC_USER's (first found: $foreign)"
    more "pip runs as $SVC_USER, so an update that changes dependencies would abort. Fix:"
    more "  sudo chown -R $SVC_USER: $VENV"
    more "or run this again with --fix-ownership."
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
  [ -x "$VENV/bin/python" ] || return 0
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

# applied_sha missing: the SHA the running core reports. Never HEAD: a pull
# moves the checkout without touching what the core runs, and an update that
# took HEAD as its baseline would see nothing to apply.
read_running_sha() {
  local url=${CORE_URL%/}/v1/admin/version body rc=0 running full
  body=$(curl -fsS --max-time 10 "$url" 2>/dev/null) || rc=$?
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
  RECORD_SHA=$full
  BASELINE=$full
  line ok "the core runs ${full:0:12}; that becomes the rollback baseline"
}

check_baseline() {
  local s
  s=$(defaults_get DOMOVOI_UPDATE_DIR)
  UPDATE_DIR=$ROOT${s:-$DEFAULT_UPDATE_DIR}
  APPLIED_FILE=$UPDATE_DIR/applied_sha
  if [ ! -e "$APPLIED_FILE" ]; then
    read_running_sha
    return 0
  fi
  s=$(head -c 200 "$APPLIED_FILE" 2>/dev/null | tr -d '[:space:]') || s=""
  if is_sha "$s" && as_user git -C "$REPO" cat-file -e "$s^{commit}" 2>/dev/null; then
    BASELINE=$s
    line ok "rollback baseline already recorded: ${s:0:12} ($APPLIED_FILE)"
    return 0
  fi
  stop "$APPLIED_FILE doesn't hold a commit of $REPO" \
    "An update would ignore it and fall back to a weaker baseline. While the core still runs the old code," \
    "remove it (sudo rm $APPLIED_FILE) and run this again to record the SHA the core runs."
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
  put_file "$APPLIED_FILE" "$TMPD/applied_sha" 0644
  DONE+=("recorded $RECORD_SHA in $APPLIED_FILE")
  line done "recorded $RECORD_SHA in $APPLIED_FILE"
}

fix_ownership() {
  [ "$NEED_CHOWN" = 1 ] || return 0
  if dry; then line would "chown -R $SVC_USER: $VENV"; return 0; fi
  chown -R "$SVC_USER:" "$VENV"
  DONE+=("chown -R $SVC_USER: $VENV")
  line done "chown -R $SVC_USER: $VENV"
}

install_grant() {
  local out backup="" undo
  case $SUDOERS_STATE in new|empty|differs) ;; *) return 0 ;; esac
  if dry; then line would "install $SUDOERS_FILE (0440): $SUDOERS_RULE"; return 0; fi
  if [ "$SUDOERS_STATE" != new ]; then
    keep_copy "$SUDOERS_FILE"
    backup=$LAST_BACKUP
    line kept "the previous $SUDOERS_FILE as $backup"
  fi
  put_file "$SUDOERS_FILE" "$TMPD/sudoers" 0440
  if ! out=$(visudo -c 2>&1); then
    # Put back what was there, so sudo keeps working for everyone.
    if [ -n "$backup" ]; then
      put_file "$SUDOERS_FILE" "$backup" 0440
      undo="put the previous file back"
    else
      rm -f "$SUDOERS_FILE"
      undo="removed it again"
    fi
    if ! visudo -c >/dev/null 2>&1; then undo="$undo, but visudo -c still fails: run sudo visudo -c now"; fi
    stop "visudo -c failed with the new $SUDOERS_FILE in place, so this $undo" "$(tail_of "$out")" \
      "The update unit was not installed."
  fi
  DONE+=("installed $SUDOERS_FILE")
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
      "The rule is in $SUDOERS_FILE, so something else in sudoers overrides it. See: sudo -u $SVC_USER sudo -n -l ${GRANT_ARGV[*]}" \
      "The update unit was not installed, so the Restart button keeps doing what it did before."
  fi
  line ok "$SVC_USER may run: ${GRANT_ARGV[*]}"
}

install_unit() {
  local reload=0 state
  case $UNIT_STATE in
    new|differs)
      reload=1
      if dry; then
        line would "install $UNIT_FILE (ExecStart=/bin/bash $REPO/scripts/linux/apply-update.sh)"
      else
        if [ "$UNIT_STATE" = differs ]; then
          keep_copy "$UNIT_FILE"
          line kept "the previous $UNIT_FILE as $LAST_BACKUP"
        fi
        put_file "$UNIT_FILE" "$TMPD/unit" 0644
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
    systemctl daemon-reload
    DONE+=("systemctl daemon-reload")
    line done "systemctl daemon-reload"
  fi
  dry && return 0
  state=$(unit_prop "$UNIT_NAME" LoadState)
  [ "$state" = loaded ] \
    || stop "systemd doesn't load $UNIT_NAME (LoadState: ${state:-unknown})" "See: systemctl status $UNIT_NAME"
  return 0
}

# --apply: run the update once, the way the button will, and report it.
apply_now() {
  local rc=0 result=$UPDATE_DIR/last-result.json status mode from to err
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
      more "Tracked files in $REPO have local changes. Commit or stash them as $SVC_USER,"
      more "then run: sudo systemctl start $UNIT_NAME"
      ;;
    aborted)
      more "Nothing was changed: $err"
      ;;
    *)
      [ -z "$err" ] || more "$err"
      ;;
  esac
  more "Detail: journalctl -u domovoi-update -n 200"
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

  if [ "$(id -u)" != 0 ]; then
    local args=""
    if [ "${#ORIG_ARGS[@]}" -gt 0 ]; then args=$(printf ' %q' "${ORIG_ARGS[@]}"); fi
    printf '%s: this needs root; run it with sudo:\n  sudo bash %s%s\n' "$PROG" "$SCRIPT_PATH" "$args" >&2
    exit 1
  fi

  umask 022
  TMPD=$(mktemp -d)
  trap 'rm -rf "$TMPD"' EXIT

  if dry; then say "Pre-flight (dry run: nothing is changed)"; else say "Pre-flight (nothing is changed yet)"; fi
  check_systemd
  check_repo
  check_user
  check_venv
  check_docker
  check_piper
  check_baseline
  check_sudoers
  check_unit

  say ""
  if dry; then say "Would change"; else say "Changes"; fi
  fix_ownership
  record_baseline
  install_grant
  verify_grant
  install_unit

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
