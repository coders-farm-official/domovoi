#!/usr/bin/env bash
# Make this spare Linux laptop or mini PC a Domovoi room satellite.
#
# Spare-device satellites (S1, 2026-10-13). Run by the one-line command the
# dashboard shows under Settings -> Satellites -> "Add a spare device",
# which has already checked this bundle's SHA-256 before running it:
#
#   bash install-pc.sh [options] 'domovoi-room:1?core=...&fp=...&room=...'
#   bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall [--purge]
#
# Current Debian (12, 13) and Ubuntu (24.04 and later). Run it as the
# account the satellite should use, NOT as root: it asks for sudo for the
# system steps (apt, linger, power settings). What it does, in order:
#
#   1. Reads the enrolment payload and makes the core prove it holds the
#      fingerprint in it (an address-bound signed challenge) BEFORE writing
#      anything. A core that can't is refused and nothing changes.
#   2. Asks what the server may do on its own: "automatic" (signed updates
#      and restarts, like a Pi) or "ask" (they wait for your OK in the tray).
#   3. Puts root's own copy of this script, the uninstaller, in
#      /usr/local/lib/domovoi-satellite, and starts root's record of what it
#      changes in /var/lib/domovoi-satellite (both root's alone: nothing the
#      satellite's account can write is ever run or read by root). Every
#      sudo step comes before the satellite's own code runs; on a desktop,
#      sudo then forgets your password (sudo -k), and the satellite's code
#      runs in a session with no terminal (setsid).
#   4. Installs the system packages it needs (python3-venv, libportaudio2,
#      mpg123, alsa-utils; on a desktop python3-tk and pulseaudio-utils).
#   5. A headless box: its own account, domovoi-sat, under /var/lib. A
#      desktop: linger (it starts at boot) and the audio group.
#   6. Asks, one by one, before any power change: ignore the lid switch,
#      never sleep, a battery charge limit. Each defaults to No.
#   7. Copies the satellite code to ~/.local/share/domovoi-satellite (or
#      domovoi-sat's home), makes a fresh venv, installs the Python packages
#      from the hash-pinned list for this Python (satellite/locks/: every
#      file checked against its SHA-256) and the wake-word models (each
#      checked against its pinned SHA-256).
#   8. Writes ~/.domovoi/config.toml: the generic_pc profile, the room, the
#      pinned server, your choice from step 2.
#   9. Installs and starts the service: a systemd --user unit on a desktop
#      (that is where the sound server is), a system unit on a headless
#      box; the tray in the desktop's autostart and the "Domovoi Satellite"
#      window in the app menu.
#  10. Waits for you to approve the device in the dashboard, showing the
#      approval code here (only on a terminal, never into a log).
#
# No root helpers are installed, ever: the satellite cannot run anything as
# root, re-join Wi-Fi, set the clock, or apply plugin payloads that need
# root (those plugins stay Pi-only). On a desktop the satellite runs as
# you, though, and you can use sudo: see docs/SPARE_DEVICE_SATELLITES.md
# ("What it can and cannot do") for what that means.
#
# Options:
#   --permissions automatic|ask  skip question 2
#   --mode desktop|headless      default: desktop when a graphical session is
#                                running for this account, else headless
#   --power ask|no|yes           step 6 (default ask; --yes makes it no)
#   --charge-limit N             the limit step 6 offers (default 80)
#   --yes                        no questions: --permissions must be given
#   --no-start                   install, don't start (or wait)
#   --skip-apt                   the packages are already there
#   --wait SECONDS               how long step 10 waits (default 900; 0: don't)
#   --uninstall [--purge]        undo all of it (--purge: the config too);
#                                run root's copy, as the uninstall line says
#
# Exit status: 0 done; 2 a usage error or a refused payload; 3 the core
# did not prove its identity; 4 not approved before --wait ran out (the
# satellite keeps waiting in the background); 1 anything else.

set -euo pipefail

# ─── arguments ───────────────────────────────────────────────────────────

PAYLOAD=""
PERMISSIONS=""
MODE=""
POWER=ask
CHARGE_LIMIT=80
ASSUME_YES=0
NO_START=0
SKIP_APT=0
WAIT_SEC=900
UNINSTALL=0
PURGE=0
# Test seam (the harness in satellite/tests/ sets it): where systemd lives.
SYSTEMD_RUNTIME=${DOMOVOI_TEST_SYSTEMD_RUNTIME:-/run/systemd/system}

die() { echo "install-pc: $*" >&2; exit "${2:-1}"; }
say() { printf '%s\n' "$*"; }
usage() { sed -n '/^#   bash install-pc.sh/,/^#   bash .*--uninstall/p' "$0" | sed 's/^#   //'; }

while [ $# -gt 0 ]; do
  case $1 in
    --permissions) PERMISSIONS=${2:-}; shift 2 ;;
    --mode) MODE=${2:-}; shift 2 ;;
    --power) POWER=${2:-}; shift 2 ;;
    --charge-limit) CHARGE_LIMIT=${2:-}; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --skip-apt) SKIP_APT=1; shift ;;
    --wait) WAIT_SEC=${2:-}; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    domovoi-room:*|DOMOVOI-ROOM:*) PAYLOAD=$1; shift ;;
    *) die "unknown argument: $1 (--help)" 2 ;;
  esac
done
case $PERMISSIONS in ""|automatic|ask) ;; *) die "--permissions is automatic or ask" 2 ;; esac
case $MODE in ""|desktop|headless) ;; *) die "--mode is desktop or headless" 2 ;; esac
case $POWER in ask|no|yes) ;; *) die "--power is ask, no or yes" 2 ;; esac
case $CHARGE_LIMIT in ''|*[!0-9]*) die "--charge-limit is a number" 2 ;; esac
[ "$CHARGE_LIMIT" -ge 50 ] && [ "$CHARGE_LIMIT" -le 100 ] || die "--charge-limit is 50 to 100" 2
case $WAIT_SEC in ''|*[!0-9]*) die "--wait is a number of seconds" 2 ;; esac
if [ "$ASSUME_YES" = 1 ] && [ "$POWER" = ask ]; then POWER=no; fi

INTERACTIVE=0
if [ -t 0 ] && [ -t 1 ] && [ "$ASSUME_YES" = 0 ]; then INTERACTIVE=1; fi

ask_yes_no() {  # ask_yes_no "question" -> 0 for yes; the default is No
  local answer
  [ "$INTERACTIVE" = 1 ] || return 1
  read -r -p "$1 [y/N] " answer </dev/tty || return 1
  case $answer in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

[ "$(uname -s)" = Linux ] || die "this installer is for Linux (Debian or Ubuntu)" 2
if [ "$(id -u)" = 0 ] && [ "$UNINSTALL" = 0 ] && [ "${MODE:-}" != headless ]; then
  die "run this as the account the satellite should use, not as root (it asks for sudo when it needs it); for a box with no desktop, add --mode headless" 2
fi

SUDO=sudo
if [ "$(id -u)" = 0 ]; then SUDO=""; fi
need_sudo() {
  [ -z "$SUDO" ] && return 0
  command -v sudo >/dev/null 2>&1 || die "sudo is needed for the system steps; install it, or run the system steps as root"
}

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

SAT_ACCOUNT=domovoi-sat
SAT_HOME_HEADLESS=/var/lib/domovoi-sat
UNIT=domovoi-satellite.service

# Root's own: this script's copy that uninstalls, and the record of what the
# installer changed. Root-owned, outside anything the satellite's account
# can write; the uninstall acts on these and on the fixed paths below,
# never on a path read from a file (security review of wf/sat-pc, B3).
ROOT_LIB=/usr/local/lib/domovoi-satellite
UNINSTALLER=$ROOT_LIB/install-pc.sh
# --purge's helper (satellite/scripts/purge-satellite-files.py), root's copy:
# run with the system's python3 -I, never the satellite's own code.
PURGER=$ROOT_LIB/purge-satellite-files.py
RECORD_DIR=/var/lib/domovoi-satellite
RECORD=$RECORD_DIR/install.json
# Every system file the installer may create, and every unit it may mask.
LOGIND_DROPIN=/etc/systemd/logind.conf.d/90-domovoi-satellite.conf
CHARGE_RULE=/etc/udev/rules.d/90-domovoi-charge-limit.rules
SLEEP_TARGETS="sleep.target suspend.target hibernate.target hybrid-sleep.target"
# What a Domovoi server keeps in ~/.domovoi (satellite/pc_engine.py
# CORE_MARKERS): an account that has one is not the satellite's to use.
CORE_MARKERS="server-identity.json device-token.txt setup-code.txt"

# ─── where things go ─────────────────────────────────────────────────────

graphical_session() {
  # A desktop is running for this account: its session bus and a display.
  [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && return 0
  command -v loginctl >/dev/null 2>&1 || return 1
  local s
  for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk -v u="$(id -un)" '$3==u {print $1}'); do
    case $(loginctl show-session "$s" -p Type --value 2>/dev/null) in x11|wayland) return 0 ;; esac
  done
  return 1
}

layout() {
  if [ "$MODE" = headless ]; then
    RUN_AS=$SAT_ACCOUNT
    HOME_DIR=$SAT_HOME_HEADLESS
    PREFIX=$HOME_DIR/app
  else
    RUN_AS=$(id -un)
    HOME_DIR=$HOME
    PREFIX=$HOME_DIR/.local/share/domovoi-satellite
  fi
  CONFIG_DIR=$HOME_DIR/.domovoi
  VENV=$PREFIX/venv
}

as_sat() {  # run a command as the satellite's account (on a desktop: you)
  # In a session of its own (setsid), stdin from nowhere: whatever the
  # satellite's code starts has no terminal to borrow a sudo ticket from or
  # to type into. Its output still reaches this terminal.
  if [ "$MODE" != headless ]; then
    (cd / && setsid -w "$@" </dev/null)
  elif [ -z "$SUDO" ]; then
    # Root: runuser, with a clean environment and the account's home.
    (cd / && runuser -u "$SAT_ACCOUNT" -- setsid -w env -i HOME="$HOME_DIR" LANG=C.UTF-8 \
      PATH=/usr/local/bin:/usr/bin:/bin "$@" </dev/null)
  else
    (cd / && $SUDO -u "$SAT_ACCOUNT" -H -- setsid -w "$@" </dev/null)
  fi
}

forget_sudo() {  # sudo forgets this terminal's password: nothing after can use it
  if [ -n "$SUDO" ] && command -v sudo >/dev/null 2>&1; then sudo -k 2>/dev/null || true; fi
}

sat_account_state() {  # prints "absent", "ok", or why the existing account is not ours
  local ent uid home shell g
  if ! ent=$(getent passwd "$SAT_ACCOUNT"); then echo absent; return 0; fi
  uid=$(printf '%s' "$ent" | cut -d: -f3)
  home=$(printf '%s' "$ent" | cut -d: -f6)
  shell=$(printf '%s' "$ent" | cut -d: -f7)
  if [ "$home" != "$SAT_HOME_HEADLESS" ]; then echo "its home is $home, not $SAT_HOME_HEADLESS"; return 0; fi
  case $shell in */nologin|*/false) ;; *) echo "it can log in ($shell)"; return 0 ;; esac
  if [ "$uid" -le 0 ] || [ "$uid" -ge 1000 ]; then echo "it is not a system account (uid $uid)"; return 0; fi
  for g in $(id -nG "$SAT_ACCOUNT"); do
    case $g in "$SAT_ACCOUNT"|audio) ;; *) echo "it is in the group $g"; return 0 ;; esac
  done
  echo ok
}

core_marker_in() {  # core_marker_in DIR -> prints the first server file found there
  local f
  for f in $CORE_MARKERS; do
    if [ "$MODE" = headless ]; then
      $SUDO test -e "$1/$f" && { echo "$1/$f"; return 0; }
    else
      test -e "$1/$f" && { echo "$1/$f"; return 0; }
    fi
  done
  return 0
}

have_systemd() { [ -d "$SYSTEMD_RUNTIME" ]; }

user_systemctl() { systemctl --user "$@"; }

# ─── root's record ───────────────────────────────────────────────────────

record_set() {  # record_set KEY=JSON ...: root's record of what this installer changed
  need_sudo
  $SUDO install -d -o root -g root -m 0755 "$RECORD_DIR"
  $SUDO python3 -I -c '
import json, os, sys
path = sys.argv[1]
doc = {}
if os.path.isfile(path) and not os.path.islink(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
if not isinstance(doc, dict):
    doc = {}
for item in sys.argv[2:]:
    key, _, value = item.partition("=")
    doc[key] = json.loads(value)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, sort_keys=True)
    f.write("\n")
os.chmod(tmp, 0o644)
os.replace(tmp, path)
' "$RECORD" "$@"
}

record_get() {  # record_get KEY -> its value ("" when absent); only a root-owned record counts
  [ -f "$RECORD" ] && [ ! -L "$RECORD" ] || return 0
  [ "$(stat -c %u "$RECORD")" = 0 ] && [ "$(stat -c %u "$RECORD_DIR")" = 0 ] || return 0
  python3 -I -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
except (OSError, ValueError):
    d = {}
v = d.get(sys.argv[2], "") if isinstance(d, dict) else ""
if isinstance(v, list):
    print(" ".join(str(x) for x in v))
elif v is True:
    print(1)
elif v is False or v is None:
    print("")
else:
    print(v)
' "$RECORD" "$1"
}

# ─── uninstall ───────────────────────────────────────────────────────────

purge_satellite_files() {  # the satellite's own files in $CONFIG_DIR, and only those
  # Root's copy of the purger with the system's python3 -I: nothing the
  # satellite's account can change runs here (its code could otherwise use
  # a sudo ticket this terminal holds). On a computer that also runs a
  # Domovoi server, ~/.domovoi holds that server's key, voices and wake
  # models too, and the purger leaves them.
  if [ -f "$PURGER" ] && [ ! -L "$PURGER" ] && [ "$(stat -c %u "$PURGER")" = 0 ]; then
    python3 -I "$PURGER" "$CONFIG_DIR" >/dev/null
  else
    local f
    for f in config.toml approved approval_code pairing_token pending-server.json \
             server-fingerprint.json synced_sha synced_manifest.json upgrade_pending.json \
             satellite_backup.tar log-spool.txt last-health.json self-restarts.json muted voice wake \
             wake_threshold manifest-serials.json install-pc.json control.sock; do
      rm -f "$CONFIG_DIR/$f"
    done
    say "  kept $CONFIG_DIR/sounds and wake_models (root's purger is missing, so which files there are the satellite's is not known)"
  fi
}

uninstall() {
  local recorded_mode recorded_account state t
  recorded_mode=$(record_get mode)
  recorded_account=$(record_get account)
  if [ -z "$MODE" ]; then MODE=$recorded_mode; fi
  if [ -z "$MODE" ]; then
    if [ -f "/etc/systemd/system/$UNIT" ] && grep -q "User=$SAT_ACCOUNT" "/etc/systemd/system/$UNIT" 2>/dev/null; then
      MODE=headless
    else
      MODE=desktop
    fi
  fi
  case $MODE in desktop|headless) ;; *) MODE=desktop ;; esac
  layout
  local how="bash $UNINSTALLER --uninstall"
  [ "$MODE" = headless ] && how="sudo $how"
  # Root runs only root's copy of this script: the one in the satellite's
  # code tree belongs to the satellite's account (and the core can rewrite it).
  if [ -f "$UNINSTALLER" ] && ! [ "$HERE/install-pc.sh" -ef "$UNINSTALLER" ]; then
    die "uninstall with root's own copy of this script: $how" 2
  fi
  case $HERE/ in
    "$SAT_HOME_HEADLESS"/*) die "this copy belongs to the satellite's account; do not run it as root (see docs/SPARE_DEVICE_SATELLITES.md, Removing it)" 2 ;;
  esac
  if [ "$MODE" = desktop ]; then
    if [ "$(id -u)" = 0 ]; then
      die "a desktop install is removed by its own account${recorded_account:+ ($recorded_account)}, not root: $how" 2
    fi
    if [ -n "$recorded_account" ] && [ "$recorded_account" != "$(id -un)" ]; then
      die "the satellite was installed for $recorded_account: run this as that account" 2
    fi
  fi
  need_sudo
  say "Removing the Domovoi satellite ($MODE install)..."
  if [ "$MODE" = desktop ]; then
    # Your part first, with no sudo used yet, and none of the satellite's
    # own code run at all; root's part after.
    if have_systemd; then
      user_systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "$HOME_DIR/.config/systemd/user/$UNIT"
    have_systemd && user_systemctl daemon-reload >/dev/null 2>&1 || true
    pkill -u "$(id -un)" -f 'python -m satellite.tray' >/dev/null 2>&1 || true
    rm -f "$HOME_DIR/.config/autostart/domovoi-satellite-tray.desktop" \
          "$HOME_DIR/.local/share/applications/domovoi-satellite.desktop"
    if [ "$PURGE" = 1 ]; then
      purge_satellite_files
      say "  the satellite's settings and records are gone"
    else
      say "  kept $CONFIG_DIR/config.toml (--purge removes it)"
    fi
    rm -rf "$PREFIX"
    if [ "$(record_get linger_enabled_by_installer)" = 1 ]; then
      $SUDO loginctl disable-linger "$RUN_AS" || true
      say "  linger turned off again (the installer had turned it on)"
    fi
  else
    if have_systemd; then $SUDO systemctl disable --now "$UNIT" >/dev/null 2>&1 || true; fi
    $SUDO rm -f "/etc/systemd/system/$UNIT"
    have_systemd && $SUDO systemctl daemon-reload || true
  fi
  # Power settings: only what this installer changed (root's record), and
  # only at the fixed paths and units above.
  for t in $(record_get masked_targets); do
    case " $SLEEP_TARGETS " in
      *" $t "*) $SUDO systemctl unmask "$t" >/dev/null 2>&1 || true ;;
    esac
  done
  if [ "$(record_get logind_dropin)" = 1 ]; then
    $SUDO rm -f "$LOGIND_DROPIN"
    say "  lid switch: back to your system's setting after the next reboot"
  fi
  if [ "$(record_get charge_rule)" = 1 ]; then
    $SUDO rm -f "$CHARGE_RULE"
    say "  charge limit rule removed (the current limit stays until a reboot)"
  fi
  if [ "$MODE" = headless ]; then
    # The account's files are removed as the account, never by root.
    state=$(sat_account_state)
    case $state in
      ok)
        as_sat rm -rf "$PREFIX"
        if [ "$PURGE" = 1 ]; then
          # userdel removes its home without following links out of it.
          $SUDO userdel -r "$SAT_ACCOUNT" >/dev/null 2>&1 || true
          say "  removed the $SAT_ACCOUNT account and its home"
        else
          say "  kept $CONFIG_DIR/config.toml (--purge removes it, and the $SAT_ACCOUNT account)"
        fi ;;
      absent) ;;
      *) say "  left the $SAT_ACCOUNT account and its home alone: $state" ;;
    esac
  fi
  $SUDO rm -f "$RECORD"
  $SUDO rmdir "$RECORD_DIR" 2>/dev/null || true
  # Last: this file itself (bash keeps reading the open copy).
  $SUDO rm -f "$PURGER"
  $SUDO rm -f "$UNINSTALLER"
  $SUDO rmdir "$ROOT_LIB" 2>/dev/null || true
  forget_sudo
  say "Done. In the dashboard, retire the room too once it shows offline"
  say "(Settings -> Satellites -> the room -> Retire room): that frees its name and removes its music player."
}

if [ "$UNINSTALL" = 1 ]; then
  uninstall
  exit 0
fi

SRC=$(cd "$HERE/../.." && pwd)
[ -f "$SRC/satellite/client.py" ] || die "run this from the satellite bundle (satellite/scripts/install-pc.sh)"
case $SRC/ in
  "$SAT_HOME_HEADLESS"/*) die "run the installer from the dashboard's command, not from the satellite's own copy of it" 2 ;;
esac

# ─── 1. the payload, and the core proves who it is ───────────────────────

[ -n "$PAYLOAD" ] || die "give the enrolment payload ('domovoi-room:1?...') from the dashboard: Settings -> Satellites -> Add a spare device" 2
command -v python3 >/dev/null 2>&1 || die "python3 is needed (sudo apt install python3)"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "Python 3.11 or newer is needed (Debian 12, Ubuntu 24.04 and later have it)"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in
    *" debian "*|*" ubuntu "*) ;;
    *) say "note: this is ${PRETTY_NAME:-an unknown Linux}; the installer is tested on Debian and Ubuntu" ;;
  esac
fi
# The Python packages come from a hash-pinned list made for this Python and
# machine (satellite/scripts/lock-pc-requirements.sh); none, no install.
PYTAG=$(python3 -I -c 'import sys; print("cp%d%d" % sys.version_info[:2])')
LOCK_REL=satellite/locks/pc-$PYTAG-$(uname -m).txt
if [ ! -f "$SRC/$LOCK_REL" ]; then
  die "there is no pinned package list for Python $PYTAG on $(uname -m) yet, so nothing was installed (lists: $(cd "$SRC/satellite/locks" && ls pc-*.txt 2>/dev/null | sed 's/^pc-//; s/\.txt$//' | tr '\n' ' '))" 2
fi

say "Checking the Domovoi server named in the code..."
ERRFILE=$(mktemp)
set +e
# -P: nothing from the current directory is importable; the bundle is
# named explicitly.
ENROL_JSON=$(env PYTHONPATH="$SRC" python3 -P -m satellite.enrolment verify "$PAYLOAD" 2>"$ERRFILE")
rc=$?
set -e
msg=$(cat "$ERRFILE" 2>/dev/null || true)
rm -f "$ERRFILE"
if [ "$rc" = 3 ]; then
  die "the server at that address did not prove it is the one in the code, so nothing was installed: $msg" 3
elif [ "$rc" != 0 ]; then
  die "that is not a usable enrolment code: $msg" 2
fi
enrol() { printf '%s' "$ENROL_JSON" | python3 -I -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
CORE=$(enrol core); FP=$(enrol fp); ROOM=$(enrol room); ROOM_ID=$(enrol room_id)
say "  $CORE proved it is $FP"
say "  this device will be the room \"$ROOM\" ($ROOM_ID)"

# ─── 2. what the server may do on its own ────────────────────────────────

if [ -z "$PERMISSIONS" ]; then
  if [ "$INTERACTIVE" = 1 ]; then
    say ""
    say "May the Domovoi server update and restart this satellite on its own?"
    say "  1) automatic - it installs signed satellite updates, applies settings from the"
    say "                 dashboard and restarts the satellite when it needs to, like a Pi"
    say "  2) ask me    - each of those waits for your OK in the tray (or the command line)"
    say "Either way nothing runs as root. You can change this later in ~/.domovoi/config.toml ([pc] permissions)."
    read -r -p "Choose 1 or 2 [2]: " choice </dev/tty || choice=2
    case $choice in 1|automatic) PERMISSIONS=automatic ;; *) PERMISSIONS=ask ;; esac
  elif [ "$ASSUME_YES" = 1 ]; then
    die "--yes needs --permissions automatic or --permissions ask" 2
  else
    PERMISSIONS=ask
  fi
fi

if [ -z "$MODE" ]; then
  if graphical_session; then MODE=desktop; else MODE=headless; fi
fi
layout

# A satellite never shares an account with a Domovoi server: both keep
# ~/.domovoi, and the server's private key is there (review of wf/sat-pc, S2).
SERVER_FILE=$(core_marker_in "$CONFIG_DIR")
if [ -n "$SERVER_FILE" ]; then
  die "$SERVER_FILE is here: this account runs a Domovoi server, so nothing was installed. Install the satellite for another account, or with --mode headless (its own account)" 2
fi
if [ "$MODE" = headless ]; then
  SAT_STATE=$(sat_account_state)
  case $SAT_STATE in
    ok|absent) ;;
    *) die "an account named $SAT_ACCOUNT already exists and is not one this installer makes ($SAT_STATE), so nothing was installed; remove or rename it and run the command again" 2 ;;
  esac
fi
say "Installing for $RUN_AS ($MODE): code in $PREFIX, settings in $CONFIG_DIR, updates: $PERMISSIONS"

# ─── 3. root's copy of the uninstaller, root's record ────────────────────
#
# Every step that needs sudo comes first (3 to 6). On a desktop the
# installer then makes sudo forget your password (sudo -k) before anything
# the satellite's account can change runs (its code, its venv), and runs
# that in a session of its own with no terminal: nothing it starts can
# borrow a sudo ticket from this terminal or type into it (security review
# of wf/sat-pc, round 2). A headless box runs that code as domovoi-sat,
# which has no sudo, the same way.

need_sudo
$SUDO install -d -o root -g root -m 0755 "$ROOT_LIB"
if ! [ "$HERE/install-pc.sh" -ef "$UNINSTALLER" ]; then
  $SUDO install -o root -g root -m 0755 "$HERE/install-pc.sh" "$UNINSTALLER"
fi
$SUDO install -o root -g root -m 0644 "$HERE/purge-satellite-files.py" "$PURGER"
record_set "mode=\"$MODE\"" "account=\"$RUN_AS\"" "installed_at=$(date +%s)"

# ─── 4. system packages ──────────────────────────────────────────────────

missing_pkgs() {
  local p out=""
  for p in "$@"; do
    dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || out="$out $p"
  done
  printf '%s' "$out"
}
PKGS="python3-venv python3-pip libportaudio2 mpg123 alsa-utils ca-certificates"
[ "$MODE" = desktop ] && PKGS="$PKGS python3-tk pulseaudio-utils"
if [ "$SKIP_APT" = 0 ]; then
  MISSING=$(missing_pkgs $PKGS)
  if [ -n "$MISSING" ]; then
    say "Installing system packages:$MISSING"
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # shellcheck disable=SC2086
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $MISSING
  fi
fi

# ─── 5. the account, linger, the audio group ─────────────────────────────

if [ "$MODE" = headless ]; then
  if [ "$SAT_STATE" = absent ]; then
    $SUDO useradd --system --create-home --home-dir "$SAT_HOME_HEADLESS" --shell /usr/sbin/nologin "$SAT_ACCOUNT"
    say "  created the $SAT_ACCOUNT account (no login, no sudo)"
  fi
  # Root touches the home directory itself (its parent is root's), never
  # anything inside it: that is the account's, and is done as the account.
  if [ ! -d "$HOME_DIR" ] || [ -L "$HOME_DIR" ] || [ "$($SUDO stat -c %U "$HOME_DIR")" != "$SAT_ACCOUNT" ]; then
    die "$HOME_DIR is not $SAT_ACCOUNT's own directory; remove the account (sudo userdel -r $SAT_ACCOUNT) and run the command again" 2
  fi
  $SUDO chmod 0750 "$HOME_DIR"
  $SUDO usermod -aG audio "$SAT_ACCOUNT"
elif have_systemd; then
  if [ "$(loginctl show-user "$RUN_AS" -p Linger --value 2>/dev/null || echo no)" != yes ]; then
    $SUDO loginctl enable-linger "$RUN_AS"
    record_set "linger_enabled_by_installer=true"
    say "  linger on: the satellite starts at boot, before anyone signs in"
  fi
  # The sound devices belong to whoever sits at the screen; in the audio
  # group the satellite can still open them when nobody is signed in.
  if ! id -nG "$RUN_AS" | tr ' ' '\n' | grep -qx audio; then
    $SUDO usermod -aG audio "$RUN_AS"
    record_set "audio_group_added=true"
    say "  $RUN_AS joined the audio group (sound while nobody is signed in; takes effect at the next sign-in)"
  fi
fi

# ─── 6. power: asked, one at a time, default No ──────────────────────────

power_change() {  # power_change "question" -> 0 to make the change
  case $POWER in
    yes) return 0 ;;
    no) return 1 ;;
    *) ask_yes_no "$1" ;;
  esac
}
if have_systemd; then
  if [ -d /proc/acpi/button/lid ] || ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then
    if power_change "Keep running with the lid closed (the lid switch does nothing)?"; then
      $SUDO mkdir -p "$(dirname "$LOGIND_DROPIN")"
      printf '%s\n' "# Domovoi satellite: closing the lid does not suspend (install-pc.sh)." \
        "[Login]" "HandleLidSwitch=ignore" "HandleLidSwitchExternalPower=ignore" "HandleLidSwitchDocked=ignore" \
        | $SUDO tee "$LOGIND_DROPIN" >/dev/null
      record_set "logind_dropin=true"
      say "  lid switch ignored from the next reboot (the desktop's own lid setting may need the same)"
    fi
  fi
  if power_change "Never sleep or hibernate (the screen can still turn off)?"; then
    MASKED=""
    for t in $SLEEP_TARGETS; do
      if [ "$(systemctl is-enabled "$t" 2>/dev/null || true)" != masked ]; then
        $SUDO systemctl mask "$t" >/dev/null 2>&1 && MASKED="$MASKED \"$t\","
      fi
    done
    record_set "masked_targets=[${MASKED%,}]"
    say "  sleep and hibernate are off"
  fi
  RULES=""
  for BAT in /sys/class/power_supply/BAT*; do
    [ -e "$BAT/charge_control_end_threshold" ] || continue
    if power_change "Stop charging $(basename "$BAT") at $CHARGE_LIMIT% (kinder to a battery that is always plugged in)?"; then
      RULES="${RULES}SUBSYSTEM==\"power_supply\", KERNEL==\"$(basename "$BAT")\", ATTR{charge_control_end_threshold}=\"$CHARGE_LIMIT\"
"
      echo "$CHARGE_LIMIT" | $SUDO tee "$BAT/charge_control_end_threshold" >/dev/null \
        || say "  this laptop refused the limit now; the rule tries again at every boot"
      say "  $(basename "$BAT") stops charging at $CHARGE_LIMIT%"
    fi
  done
  if [ -n "$RULES" ]; then
    printf '%s' "$RULES" | $SUDO tee "$CHARGE_RULE" >/dev/null
    record_set "charge_rule=true"
  fi
fi

if [ "$MODE" = desktop ]; then
  # Done with root. Your account runs the satellite's code from here on:
  # sudo forgets your password first, so nothing that code starts can use it.
  forget_sudo
fi

# ─── 7. the code, a fresh venv, the models ───────────────────────────────

if [ "$MODE" = headless ]; then
  STAGE=$(mktemp -d)
  cp -a "$SRC/satellite" "$STAGE/satellite"
  chmod -R a+rX "$STAGE"
  as_sat mkdir -p "$PREFIX"
  as_sat rm -rf "$PREFIX/satellite"
  as_sat cp -a "$STAGE/satellite" "$PREFIX/satellite"
  rm -rf "$STAGE"
else
  mkdir -p "$PREFIX"
  rm -rf "$PREFIX/satellite"
  cp -a "$SRC/satellite" "$PREFIX/satellite"
fi
as_sat find "$PREFIX/satellite" -name __pycache__ -type d -prune -exec rm -rf {} +

say "Setting up Python (this downloads the satellite's libraries, a few minutes)..."
# A fresh venv every time: an old one is the account's, and nothing in it
# (a planted .pth runs at Python's start) is ever run.
as_sat rm -rf "$VENV"
as_sat python3 -I -m venv "$VENV"
# Every package, its version and the SHA-256 of every file pip may take are
# in the list (it reached this computer inside the checked bundle); pip
# refuses anything else, and builds nothing from source.
as_sat "$VENV/bin/pip" install --no-input -q --disable-pip-version-check \
  --require-hashes --only-binary=:all: --no-deps -r "$PREFIX/$LOCK_REL"
OWW_DIR=$(as_sat "$VENV/bin/python" -c 'import os, openwakeword; print(os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models"))')
say "Fetching the wake-word models (checked against their pinned SHA-256)..."
as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install models --dest '$OWW_DIR' --payload \"\$1\"" _ "$PAYLOAD" >/dev/null \
  || die "the wake-word models could not be fetched (the error is above); run the command again when the internet is back"

# ─── 8. the config ───────────────────────────────────────────────────────

CONFIG_REPORT=$(as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install config --config '$CONFIG_DIR/config.toml' --permissions '$PERMISSIONS' --payload \"\$1\"" _ "$PAYLOAD")
BACKUP=$(printf '%s' "$CONFIG_REPORT" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("backup") or "")')
[ -z "$BACKUP" ] || say "  your previous config is kept as $BACKUP"
say "  wrote $CONFIG_DIR/config.toml (pinned to $FP)"

# ─── 9. the service, the tray ────────────────────────────────────────────

render() {  # render TEMPLATE -> stdout
  sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@USER@|$RUN_AS|g" -e "s|@HOME@|$HOME_DIR|g" "$1"
}

STARTED=0
if ! have_systemd; then
  say "systemd is not running here, so no service was installed. Run the satellite by hand:"
  say "  cd $PREFIX && $VENV/bin/python -m satellite.client"
elif [ "$MODE" = desktop ]; then
  mkdir -p "$HOME_DIR/.config/systemd/user"
  render "$SRC/satellite/scripts/domovoi-satellite-user.service" >"$HOME_DIR/.config/systemd/user/$UNIT"
  export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
  # A user manager that linger has only just started needs a moment.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -S "$XDG_RUNTIME_DIR/bus" ] || [ -S "$XDG_RUNTIME_DIR/systemd/private" ] && break
    sleep 1
  done
  user_systemctl daemon-reload
  if [ "$NO_START" = 1 ]; then
    user_systemctl enable "$UNIT" >/dev/null
  else
    user_systemctl enable "$UNIT" >/dev/null
    user_systemctl restart "$UNIT"
    STARTED=1
  fi
  mkdir -p "$HOME_DIR/.config/autostart" "$HOME_DIR/.local/share/applications"
  cat >"$HOME_DIR/.config/autostart/domovoi-satellite-tray.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Domovoi Satellite tray
Comment=The room satellite's microphone, mute and approval code
Exec=$VENV/bin/python -m satellite.tray
Path=$PREFIX
Icon=audio-input-microphone
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
  cat >"$HOME_DIR/.local/share/applications/domovoi-satellite.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Domovoi Satellite
Comment=Is this room's microphone on? Mute it, see the approval code
Exec=$VENV/bin/python -m satellite.tray --window
Path=$PREFIX
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Utility;
EOF
  if [ "$NO_START" = 0 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    (cd "$PREFIX" && setsid "$VENV/bin/python" -m satellite.tray >/dev/null 2>&1 </dev/null &) || true
  fi
else
  render "$SRC/satellite/scripts/domovoi-satellite-system.service" | $SUDO tee "/etc/systemd/system/$UNIT" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$UNIT" >/dev/null
  if [ "$NO_START" = 0 ]; then $SUDO systemctl restart "$UNIT"; STARTED=1; fi
fi
forget_sudo

# ─── 10. approval ────────────────────────────────────────────────────────

say ""
say "Installed. The satellite is the room \"$ROOM\" ($ROOM_ID)."
if [ "$MODE" = desktop ]; then
  say "  status:    cd $PREFIX && $VENV/bin/python -m satellite.client --status"
  say "  log:       journalctl --user -u domovoi-satellite -e"
  say "  uninstall: bash $UNINSTALLER --uninstall"
else
  say "  status:    sudo -u $SAT_ACCOUNT sh -c 'cd $PREFIX && $VENV/bin/python -m satellite.client --status'"
  say "  log:       journalctl -u domovoi-satellite -e"
  say "  uninstall: sudo bash $UNINSTALLER --uninstall"
fi
if [ "$STARTED" = 1 ] && [ "$WAIT_SEC" -gt 0 ]; then
  say ""
  # The code is shown only when this script's own output is a terminal
  # (not "| tee install.log").
  SHOW_CODE=""
  if [ -t 1 ]; then
    SHOW_CODE=--show-code
    say "Now approve it: in the dashboard, Settings -> Satellites, type the code shown below."
  fi
  if as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install wait --socket '$CONFIG_DIR/control.sock' --timeout '$WAIT_SEC' $SHOW_CODE"; then
    exit 0
  fi
  say "Not approved yet. It keeps waiting in the background; the code is also in the tray,"
  say "or: cd $PREFIX && $VENV/bin/python -m satellite.client --approval-code"
  exit 4
fi
exit 0
