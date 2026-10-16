#!/usr/bin/env bash
# Make this spare Linux laptop or mini PC a Domovoi room satellite.
#
# Spare-device satellites (S1, 2026-10-13). Run by the one-line command the
# dashboard shows under Settings -> Satellites -> "Add a spare device",
# which has already checked this bundle's SHA-256 before running it:
#
#   bash install-pc.sh [options] 'domovoi-room:1?core=...&fp=...&room=...'
#   bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall [--purge]
#   sudo bash /usr/local/lib/domovoi-satellite/install-pc.sh --uninstall [--purge] --account NAME
#
# Debian and Ubuntu (and Mint, Pop!_OS and the rest of their family) with
# apt, Fedora with dnf, openSUSE with zypper, Arch with pacman; x86-64 or
# 64-bit ARM, glibc 2.28 or newer. Run it as the account the satellite
# should use, NOT as root: it asks for sudo for the system steps (packages,
# linger, power settings). What it does, in order:
#
#   0. Picks the Python: a Debian-family computer's own python3 when it is
#      3.11 or newer with a pinned package list (Debian 12 and 13, Ubuntu
#      24.04 and later); otherwise (Ubuntu 22.04, Mint 21, Debian 11, Fedora,
#      Arch, openSUSE, ...) the satellite's own: the CPython that
#      satellite/locks/linux-python.txt pins by size and SHA-256, from the
#      Domovoi server's cache or else from GitHub, checked before it is
#      unpacked. Nothing is installed yet: it waits in a temporary folder.
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
#      runs in a session with no terminal (setsid). The satellite's own
#      Python, if step 0 chose it, goes beside them (root's copy, checked
#      again by root: /usr/local/lib/domovoi-satellite/python).
#   4. Installs the system packages it needs (PortAudio, mpg123, the ALSA
#      utilities; on a desktop pactl; with the computer's own Python on
#      Debian or Ubuntu also python3-venv, and on a desktop python3-tk).
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
#   --python auto|system|pinned  step 0 (default auto); system is for
#                                Debian and Ubuntu with Python 3.11+
#   --skip-packages              the packages are already there (also
#                                --skip-apt); on any other Linux, install
#                                them yourself and give this
#   --wait SECONDS               how long step 10 waits (default 900; 0: don't)
#   --uninstall [--purge]        undo all of it (--purge: the config too);
#                                run root's copy, as the uninstall line says
#   --account NAME               with --uninstall of a desktop install, run
#                                as root from an administrator's account
#                                (sudo): the account the satellite runs as.
#                                For a dedicated satellite account; in your
#                                everyday account, just run --uninstall there
#
# Exit status: 0 done; 2 a usage error or a refused payload; 3 the core
# did not prove its identity; 4 not approved before --wait ran out (the
# satellite keeps waiting in the background); 1 anything else.

set -euo pipefail

# A fixed PATH, and none of the caller's shell hooks, exported functions or
# Python settings: nothing the account put on its PATH (~/.local/bin, which
# Debian's stock ~/.profile adds) and no function it exported may stand in
# for a tool this script runs; sudo, python3 and the tools that change
# accounts are spelled in full below as well. (Security review of
# wf/sat-pc, round 3. What a compromised account's shell does before this
# line, BASH_ENV included, no script can undo: root's part of an uninstall
# is therefore run from an administrator's account, see uninstall below.)
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset BASH_ENV ENV CDPATH GLOBIGNORE
for _f in $(compgen -A function); do unset -f "$_f"; done
for _f in $(compgen -e); do case $_f in PYTHON*) unset "$_f" ;; esac; done
unset _f
PY=/usr/bin/python3

# ─── arguments ───────────────────────────────────────────────────────────

PAYLOAD=""
PERMISSIONS=""
MODE=""
POWER=ask
CHARGE_LIMIT=80
ASSUME_YES=0
NO_START=0
SKIP_APT=0
PYTHON_CHOICE=auto
WAIT_SEC=900
UNINSTALL=0
PURGE=0
ACCOUNT=""
# Test seam (the harness in satellite/tests/ sets it): where systemd lives.
SYSTEMD_RUNTIME=${DOMOVOI_TEST_SYSTEMD_RUNTIME:-/run/systemd/system}

die() { echo "install-pc: $*" >&2; exit "${2:-1}"; }
say() { printf '%s\n' "$*"; }
usage() { /usr/bin/sed -n '/^#   bash install-pc.sh/,/^#   bash .*--uninstall/p' "$0" | /usr/bin/sed 's/^#   //'; }

while [ $# -gt 0 ]; do
  case $1 in
    --permissions) PERMISSIONS=${2:-}; shift 2 ;;
    --mode) MODE=${2:-}; shift 2 ;;
    --power) POWER=${2:-}; shift 2 ;;
    --charge-limit) CHARGE_LIMIT=${2:-}; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --skip-apt|--skip-packages) SKIP_APT=1; shift ;;
    --python) PYTHON_CHOICE=${2:-}; shift 2 ;;
    --wait) WAIT_SEC=${2:-}; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --account) ACCOUNT=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    domovoi-room:*|DOMOVOI-ROOM:*) PAYLOAD=$1; shift ;;
    *) die "unknown argument: $1 (--help)" 2 ;;
  esac
done
case $PERMISSIONS in ""|automatic|ask) ;; *) die "--permissions is automatic or ask" 2 ;; esac
case $MODE in ""|desktop|headless) ;; *) die "--mode is desktop or headless" 2 ;; esac
case $POWER in ask|no|yes) ;; *) die "--power is ask, no or yes" 2 ;; esac
case $PYTHON_CHOICE in auto|system|pinned) ;; *) die "--python is auto, system or pinned" 2 ;; esac
case $CHARGE_LIMIT in ''|*[!0-9]*) die "--charge-limit is a number" 2 ;; esac
[ "$CHARGE_LIMIT" -ge 50 ] && [ "$CHARGE_LIMIT" -le 100 ] || die "--charge-limit is 50 to 100" 2
case $WAIT_SEC in ''|*[!0-9]*) die "--wait is a number of seconds" 2 ;; esac
if [ "$ASSUME_YES" = 1 ] && [ "$POWER" = ask ]; then POWER=no; fi
if [ -n "$ACCOUNT" ]; then
  [ "$UNINSTALL" = 1 ] || die "--account is for --uninstall (run from an administrator's account)" 2
  [[ $ACCOUNT =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--account is an account name" 2
fi

INTERACTIVE=0
if [ -t 0 ] && [ -t 1 ] && [ "$ASSUME_YES" = 0 ]; then INTERACTIVE=1; fi

ask_yes_no() {  # ask_yes_no "question" -> 0 for yes; the default is No
  local answer
  [ "$INTERACTIVE" = 1 ] || return 1
  read -r -p "$1 [y/N] " answer </dev/tty || return 1
  case $answer in y|Y|yes|YES|Yes) return 0 ;; *) return 1 ;; esac
}

if [ -x /usr/bin/uname ] && [ "$(/usr/bin/uname -s)" != Linux ]; then die "this installer is for Linux" 2; fi

# Every tool this script names by its full path (a test holds this list to
# the script). A system that keeps them in /bin and /sbin only is a Debian
# upgraded from 10 or older without merged /usr: Debian's own package
# converts it, and is needed before its next release upgrade anyway.
TOOLS="/usr/bin/awk /usr/bin/basename /usr/bin/cat /usr/bin/chmod /usr/bin/cp /usr/bin/cut /usr/bin/date
  /usr/bin/dirname /usr/bin/env /usr/bin/find /usr/bin/getent /usr/bin/grep /usr/bin/head /usr/bin/id
  /usr/bin/install /usr/bin/ls /usr/bin/mkdir /usr/bin/mktemp /usr/bin/mv /usr/bin/rm /usr/bin/rmdir
  /usr/bin/sed /usr/bin/setsid /usr/bin/sh /usr/bin/sha256sum /usr/bin/sleep /usr/bin/stat /usr/bin/tar
  /usr/bin/tee /usr/bin/test /usr/bin/tr /usr/bin/uname /usr/sbin/useradd /usr/sbin/userdel /usr/sbin/usermod"
[ "$(/usr/bin/id -u)" = 0 ] && TOOLS="$TOOLS /usr/sbin/runuser"
MISSING_TOOLS=""
for _t in $TOOLS; do [ -x "$_t" ] || MISSING_TOOLS="$MISSING_TOOLS $_t"; done
unset _t
if [ -n "$MISSING_TOOLS" ]; then
  if [ ! -L /bin ] && [ -x /bin/rm ] && [ ! -x /usr/bin/rm ]; then
    die "this system keeps its programs in /bin and /sbin (a Debian upgraded without merged /usr), so nothing was installed. Convert it with: sudo apt install usrmerge   then run the command again" 2
  fi
  die "this system has none of:$MISSING_TOOLS, so nothing was installed" 2
fi

if [ "$(/usr/bin/id -u)" = 0 ] && [ "$UNINSTALL" = 0 ] && [ "${MODE:-}" != headless ]; then
  die "run this as the account the satellite should use, not as root (it asks for sudo when it needs it); for a box with no desktop, add --mode headless" 2
fi

SUDO=/usr/bin/sudo
if [ "$(/usr/bin/id -u)" = 0 ]; then SUDO=""; fi
need_sudo() {
  [ -z "$SUDO" ] && return 0
  [ -x /usr/bin/sudo ] || die "sudo is needed for the system steps; install it, or run the system steps as root"
}

HERE=$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")" && pwd)

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
# run with the system's python3 -I (or root's own copy of the satellite's
# Python, below), never the satellite's own code.
PURGER=$ROOT_LIB/purge-satellite-files.py
# The satellite's own Python, when the computer's won't do: root's copy,
# beside the uninstaller, root-owned as /usr/bin/python3 is (the satellite's
# venv is made from it; root runs it only with -I). Pinned in the bundle's
# satellite/locks/linux-python.txt; its packages (numpy, scipy, onnxruntime:
# manylinux_2_28 wheels) need glibc 2.28.
PYTHON_HOME=$ROOT_LIB/python
PINNED_PY=$PYTHON_HOME/bin/python3
# Root's own working names while it puts that Python in place (fixed paths).
PY_PART=$ROOT_LIB/.python.tar.gz
PY_NEW=$ROOT_LIB/.python.new
PY_OLD=$ROOT_LIB/.python.old
PY_LOCK_REL=satellite/locks/linux-python.txt
PBS_RELEASES=https://github.com/astral-sh/python-build-standalone/releases/download/
MIN_GLIBC_MINOR=28
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
  for s in $(/usr/bin/loginctl list-sessions --no-legend 2>/dev/null | /usr/bin/awk -v u="$(/usr/bin/id -un)" '$3==u {print $1}'); do
    case $(/usr/bin/loginctl show-session "$s" -p Type --value 2>/dev/null) in x11|wayland) return 0 ;; esac
  done
  return 1
}

layout() {
  if [ "$MODE" = headless ]; then
    RUN_AS=$SAT_ACCOUNT
    HOME_DIR=$SAT_HOME_HEADLESS
    PREFIX=$HOME_DIR/app
  elif [ -n "$ACCOUNT" ] && [ "$ACCOUNT" != "$(/usr/bin/id -un)" ]; then
    # Root, removing another account's desktop install: its home as the
    # account database has it, not anything from the environment.
    RUN_AS=$ACCOUNT
    HOME_DIR=$(/usr/bin/getent passwd "$ACCOUNT" | /usr/bin/cut -d: -f6)
    [ -n "$HOME_DIR" ] || die "there is no account $ACCOUNT" 2
    PREFIX=$HOME_DIR/.local/share/domovoi-satellite
  else
    RUN_AS=$(/usr/bin/id -un)
    HOME_DIR=$HOME
    PREFIX=$HOME_DIR/.local/share/domovoi-satellite
  fi
  CONFIG_DIR=$HOME_DIR/.domovoi
  VENV=$PREFIX/venv
}

as_sat() {  # run a command as the satellite's account ($RUN_AS)
  # In a session of its own (setsid), stdin from nowhere: whatever the
  # satellite's code starts has no terminal to borrow a sudo ticket from or
  # to type into. Its output still reaches this terminal.
  if [ "$(/usr/bin/id -u)" = 0 ]; then
    # Root: runuser, with a clean environment and the account's home.
    (cd / && /usr/sbin/runuser -u "$RUN_AS" -- /usr/bin/setsid -w /usr/bin/env -i HOME="$HOME_DIR" \
      LANG=C.UTF-8 PATH=/usr/bin:/bin "$@" </dev/null)
  elif [ "$RUN_AS" = "$(/usr/bin/id -un)" ]; then
    (cd / && /usr/bin/setsid -w "$@" </dev/null)
  else
    (cd / && $SUDO -u "$RUN_AS" -H -- /usr/bin/setsid -w "$@" </dev/null)
  fi
}

forget_sudo() {  # sudo forgets this terminal's password: nothing after can use it
  if [ -n "$SUDO" ] && [ -x /usr/bin/sudo ]; then /usr/bin/sudo -k 2>/dev/null || true; fi
}

sat_account_state() {  # prints "absent", "ok", or why the existing account is not ours
  local ent uid home shell g
  if ! ent=$(/usr/bin/getent passwd "$SAT_ACCOUNT"); then echo absent; return 0; fi
  uid=$(printf '%s' "$ent" | /usr/bin/cut -d: -f3)
  home=$(printf '%s' "$ent" | /usr/bin/cut -d: -f6)
  shell=$(printf '%s' "$ent" | /usr/bin/cut -d: -f7)
  if [ "$home" != "$SAT_HOME_HEADLESS" ]; then echo "its home is $home, not $SAT_HOME_HEADLESS"; return 0; fi
  case $shell in */nologin|*/false) ;; *) echo "it can log in ($shell)"; return 0 ;; esac
  if [ "$uid" -le 0 ] || [ "$uid" -ge 1000 ]; then echo "it is not a system account (uid $uid)"; return 0; fi
  for g in $(/usr/bin/id -nG "$SAT_ACCOUNT"); do
    case $g in "$SAT_ACCOUNT"|audio) ;; *) echo "it is in the group $g"; return 0 ;; esac
  done
  echo ok
}

core_marker_in() {  # core_marker_in DIR -> prints the first server file found there
  local f
  for f in $CORE_MARKERS; do
    if [ "$MODE" = headless ]; then
      $SUDO /usr/bin/test -e "$1/$f" && { echo "$1/$f"; return 0; }
    else
      [ -e "$1/$f" ] && { echo "$1/$f"; return 0; }
    fi
  done
  return 0
}

have_systemd() { [ -d "$SYSTEMD_RUNTIME" ]; }

user_systemctl() {  # the satellite account's own systemd (root reaches it with -M)
  if [ "$(/usr/bin/id -u)" = 0 ] && [ "$RUN_AS" != root ]; then
    /usr/bin/systemctl --user -M "$RUN_AS@" "$@"
  else
    /usr/bin/systemctl --user "$@"
  fi
}

python_is_root_s() {  # python_is_root_s PYTHON -> 0 when root may run it
  # The system's python3, or root's own copy of the satellite's Python:
  # every folder from $ROOT_LIB down to it root's, none of them writable by
  # anyone else, and no link on the way. Never the one in a temporary
  # folder, which is the installing account's.
  [ "$1" = /usr/bin/python3 ] && return 0
  [ "$1" = "$PINNED_PY" ] && [ ! -L "$ROOT_LIB" ] && [ ! -L "$PYTHON_HOME" ] && [ ! -L "$PYTHON_HOME/bin" ] || return 1
  local p
  for p in "$ROOT_LIB" "$PYTHON_HOME" "$PYTHON_HOME/bin" "$PINNED_PY"; do
    case $(/usr/bin/stat -L -c '%u %a' "$p" 2>/dev/null) in
      "0 "*[0145][0145]) ;;
      *) return 1 ;;
    esac
  done
}

# ─── root's record ───────────────────────────────────────────────────────

record_set() {  # record_set KEY=JSON ...: root's record of what this installer changed
  need_sudo
  python_is_root_s "$PY" || die "root runs only the system's Python or its own copy, not $PY" 1
  $SUDO /usr/bin/install -d -o root -g root -m 0755 "$RECORD_DIR"
  $SUDO $PY -I -c '
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
  [ "$(/usr/bin/stat -c %u "$RECORD")" = 0 ] && [ "$(/usr/bin/stat -c %u "$RECORD_DIR")" = 0 ] || return 0
  [ -x "$PY" ] && python_is_root_s "$PY" || return 0
  $PY -I -c '
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

# ─── the satellite's own Python ──────────────────────────────────────────
#
# For a computer whose python3 won't do (step 0). The lock line for this
# machine, from the bundle the dashboard's command checked; the file it
# names, from the Domovoi server's cache (only an address step 1 itself
# would accept) or from GitHub, held to its size and SHA-256 before anything
# unpacks it; unpacked first in a folder of the installing account that no
# other account can change, all the way up (for step 1, before anything is
# written), then by root into root's own folder from root's own copy, which
# root checks again (the first folder is the account's).
#
# Both copies get an empty python3XY.zip in their own lib/ before they first
# run: CPython looks for that file in every folder above its bin/ to find
# its standard library, so without one inside the tree, a lib/python3XY.zip
# and lib/python3.XY/ planted higher up (in /tmp, say) would be imported
# instead, sitecustomize and all, -I or not (security review of
# wf/sat-pc-python).

PIN_VERSION="" PIN_FILE="" PIN_SIZE="" PIN_SHA="" PIN_URL="" PYSTAGE=""
MACHINE=$(/usr/bin/uname -m)

read_pin() {  # the lock's line for $MACHINE -> PIN_*; 1 when there is none
  local kind m v f s h u extra rel
  local file_re='^cpython-(3\.[0-9]{1,2}\.[0-9]{1,3})\+([0-9]{8})-(x86_64|aarch64)-unknown-linux-gnu-install_only_stripped\.tar\.gz$'
  [ -f "$SRC/$PY_LOCK_REL" ] || return 1
  while read -r kind m v f s h u extra; do
    [ "$kind" = python ] && [ "$m" = "$MACHINE" ] || continue
    [[ $f =~ $file_re ]] && [ "${BASH_REMATCH[1]}" = "$v" ] && [ "${BASH_REMATCH[3]}" = "$m" ] \
      || die "the bundle's $PY_LOCK_REL names a bad file for $m" 1
    rel=${BASH_REMATCH[2]}
    [[ $s =~ ^[0-9]{1,9}$ ]] && [[ $h =~ ^[0-9a-f]{64}$ ]] && [ -z "$extra" ] \
      && [ "$u" = "$PBS_RELEASES$rel/${f//+/%2B}" ] || die "the bundle's $PY_LOCK_REL has a bad line for $m" 1
    PIN_VERSION=$v PIN_FILE=$f PIN_SIZE=$s PIN_SHA=$h PIN_URL=$u
    return 0
  done <"$SRC/$PY_LOCK_REL"
  return 1
}

# The payload, read as step 1 (satellite/enrolment.py parse) reads it, so
# that step 0 asks no address for the file that step 1 would refuse: the
# grammar, the strict %-escapes, the keys, the fingerprint's shape, and the
# core address exactly (normalize_core and is_home_network_host). Only the
# room name's finer rules (letters, UTF-8, control characters) are left to
# step 1; they name no address. A test holds this to the shared fixture
# (docs/uml/spare-device-enrol-fixture.json) and to normalize_core.

ipv4_ok() {  # ipv4_ok TEXT -> 0 for a dotted quad Python's ipaddress takes (no leading zeros)
  local o
  [[ $1 =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || return 1
  for o in "${BASH_REMATCH[@]:1}"; do [ "$o" -le 255 ] || return 1; done
}

ipv6_first() {  # ipv6_first TEXT -> prints its first 16 bits (decimal) if Python's IPv6Address takes it
  local s=$1 parts n i skip=-1 hi lo p first
  [[ $s =~ ^[0-9a-f:.]+$ ]] || return 1
  IFS=: read -r -a parts <<<"$s:"          # the extra ':' keeps a trailing empty part
  n=${#parts[@]}
  [ "$n" -ge 3 ] || return 1
  if [[ ${parts[n-1]} == *.* ]]; then
    ipv4_ok "${parts[n-1]}" || return 1
    parts[n-1]=0; parts[n]=0; n=$((n + 1))
  fi
  [ "$n" -le 9 ] || return 1
  for ((i = 1; i < n - 1; i++)); do
    if [ -z "${parts[i]}" ]; then [ "$skip" -lt 0 ] || return 1; skip=$i; fi
  done
  if [ "$skip" -ge 0 ]; then
    hi=$skip lo=$((n - skip - 1))
    if [ -z "${parts[0]}" ]; then hi=$((hi - 1)); [ "$hi" = 0 ] || return 1; fi
    if [ -z "${parts[n-1]}" ]; then lo=$((lo - 1)); [ "$lo" = 0 ] || return 1; fi
    [ $((8 - hi - lo)) -ge 1 ] || return 1
  else
    [ "$n" = 8 ] && [ -n "${parts[0]}" ] && [ -n "${parts[n-1]}" ] || return 1
  fi
  for ((i = 0; i < n; i++)); do
    p=${parts[i]}
    [ "$i" = "$skip" ] && continue
    [ -z "$p" ] && { [ "$i" = 0 ] || [ "$i" = $((n - 1)) ]; } && continue
    [[ $p =~ ^[0-9a-f]{1,4}$ ]] || return 1
  done
  first=${parts[0]:-0}
  printf '%d' "$((16#$first))"
}

home_host() {  # home_host HOST -> 0 when is_home_network_host would take it (lower-case, no brackets)
  local h=$1 a b x
  if [[ $h == *:* ]]; then
    x=$(ipv6_first "$h") || return 1
    { [ "$x" -ge $((16#fc00)) ] && [ "$x" -le $((16#fdff)) ]; } || { [ "$x" -ge $((16#fe80)) ] && [ "$x" -le $((16#febf)) ]; }
    return
  fi
  if ipv4_ok "$h"; then
    a=${h%%.*}; b=${h#*.}; b=${b%%.*}
    [ "$a" = 10 ] || { [ "$a" = 172 ] && [ "$b" -ge 16 ] && [ "$b" -le 31 ]; } || { [ "$a" = 192 ] && [ "$b" = 168 ]; }
    return
  fi
  local s
  for s in .local .home.arpa .lan .internal; do
    [[ $h == *"$s" ]] && [ "${#h}" -gt "${#s}" ] && return 0
  done
  return 1
}

core_normal() {  # core_normal VALUE -> prints the core address as normalize_core makes it, or nothing
  local LC_ALL=C v=$1 scheme rest netloc path host after shown port=""
  [ "${#v}" -ge 1 ] && [ "${#v}" -le 256 ] && [[ $v =~ ^[!-~]+$ ]] || return 0
  case $v in *'?'*|*'#'*|*'@'*) return 0 ;; esac
  [[ $v =~ ^([A-Za-z][A-Za-z0-9+.-]*)://(.*)$ ]] || return 0
  scheme=${BASH_REMATCH[1],,} rest=${BASH_REMATCH[2]}
  case $scheme in http|https) ;; *) return 0 ;; esac
  netloc=${rest%%/*} path=${rest#"$netloc"}
  [ -z "$path" ] || [ "$path" = / ] || return 0
  if [[ $netloc == \[* ]]; then
    [[ $netloc == *\]* ]] || return 0
    host=${netloc#\[}; after=${host#*\]}; host=${host%%\]*}; host=${host,,}
    case $host in *%*|*\[*) return 0 ;; esac
    ipv6_first "$host" >/dev/null || return 0
    shown="[$host]"
  else
    host=${netloc%%:*}; after=${netloc#"$host"}; host=${host,,}
    [[ $host =~ ^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$ ]] || return 0
    shown=$host
  fi
  if [ -n "$after" ]; then
    [[ $after =~ ^:[0-9]+$ ]] || return 0
    port=${after#:}
    while [ "${port#0}" != "$port" ]; do port=${port#0}; done
    [ -n "$port" ] && [ "${#port}" -le 5 ] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || return 0
    port=":$port"
  fi
  home_host "$host" || return 0
  printf '%s' "$scheme://$shown$port"
}

payload_core() {  # the payload's core address if step 1 would take the payload's address and shape, else nothing
  local LC_ALL=C t=$PAYLOAD ws=$' \t\r\n' rest query q pair key value seen=" " core="" fp="" room="" bs='\x'
  local raw_re="^([A-Za-z0-9._~!\$'()*+,;:@/?-]|%[0-9A-Fa-f]{2})*\$"
  while [ -n "$t" ] && [[ $ws == *"${t:0:1}"* ]]; do t=${t:1}; done
  while [ -n "$t" ] && [[ $ws == *"${t: -1}"* ]]; do t=${t:0:${#t}-1}; done
  [ "${#t}" -le 1024 ] && [[ $t == *:* ]] || return 0
  rest=${t%%:*}
  [ "${rest,,}" = domovoi-room ] || return 0
  rest=${t#*:}
  [[ $rest == '1?'* ]] || return 0
  query=${rest#1\?}
  case $query in *'#'*) return 0 ;; esac
  q=$query
  while :; do
    pair=${q%%&*}
    [[ $pair == *=* ]] || return 0
    key=${pair%%=*} value=${pair#*=}
    [[ $key =~ ^[a-z][a-z0-9_]{0,31}$ ]] && [[ $value =~ $raw_re ]] || return 0
    case $seen in *" $key "*) return 0 ;; esac
    seen="$seen$key "
    # A NUL can't live in a shell variable, so it is refused here (step 1
    # refuses one in the address and the fingerprint; stricter elsewhere,
    # never laxer); the "." keeps a decoded trailing newline.
    case $value in *%00*) [ "$key" = room ] || return 0 ;; esac
    value=$(printf '%b.' "${value//%/$bs}"); value=${value%.}
    case $key in core) core=$value ;; fp) fp=$value ;; room) room=$value ;; esac
    [ "$pair" != "$q" ] || break
    q=${q#*&}
  done
  [ -n "$core" ] && [ -n "$room" ] && [[ $fp =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] || return 0
  core_normal "$core"
}

fetch_to() {  # fetch_to URL FILE HOPS: at most $PIN_SIZE + 1 bytes of what URL serves
  # HOPS redirects at most, each to https only: 0 for the Domovoi server,
  # which never redirects; GitHub sends its release files to another https
  # host. curl and wget hold to the same rule (wget follows one hop at a
  # time here, so it can refuse plain http and a bad Location).
  local LC_ALL=C url=$1 hops=$3 status loc
  /usr/bin/rm -f "$2"
  if [ -x /usr/bin/curl ]; then
    /usr/bin/curl -fsSL --proto '=http,https' --proto-redir '=https' --max-redirs "$hops" --connect-timeout 15 \
      --max-time 1800 "$url" 2>/dev/null | /usr/bin/head -c "$((PIN_SIZE + 1))" >"$2" || true
  elif [ -x /usr/bin/wget ]; then
    while :; do
      /usr/bin/wget -q -S --max-redirect=0 --timeout=30 -O - "$url" 2>"$2.headers" \
        | /usr/bin/head -c "$((PIN_SIZE + 1))" >"$2" || true
      read -r status loc <<<"$(/usr/bin/awk '$1 ~ /^HTTP\// {s = $2; l = ""} tolower($1) == "location:" {l = $2}
                                            END {print s, l}' "$2.headers" | /usr/bin/tr -d '\r')"
      case $status in 3??) ;; *) break ;; esac
      /usr/bin/rm -f "$2"
      [ "$hops" -gt 0 ] && [[ $loc =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/[!-~]*)?$ ]] || break
      hops=$((hops - 1)) url=$loc
    done
    /usr/bin/rm -f "$2.headers"
  fi
}

pinned_ok() {  # pinned_ok FILE -> 0 when it is exactly the pinned file (size, then SHA-256)
  [ -f "$1" ] && [ ! -L "$1" ] && [ "$($SUDO_CHECK /usr/bin/stat -c %s "$1")" = "$PIN_SIZE" ] \
    && [ "$($SUDO_CHECK /usr/bin/sha256sum "$1" | /usr/bin/cut -d' ' -f1)" = "$PIN_SHA" ]
}
SUDO_CHECK=""

CPUINFO=/proc/cpuinfo

cpu_ok() {  # 0 when this processor runs the satellite's numpy: x86-64-v2, or any 64-bit ARM
  # x86-64-v2 is SSSE3, SSE4.2, POPCNT and CMPXCHG16B (Intel from 2008-2009,
  # AMD from 2011; a Core 2 lacks SSE4.2 and POPCNT). numpy 2.4+ needs it on
  # every x86-64 list in satellite/locks/. When the kernel says nothing about
  # the flags, nothing is refused.
  local flags f
  [ "$MACHINE" = x86_64 ] || return 0
  flags=$(/usr/bin/awk -F: '$1 ~ /^flags[ \t]*$/ {print " " $2 " "; exit}' "$CPUINFO" 2>/dev/null) || return 0
  [ -n "$flags" ] || return 0
  for f in ssse3 sse4_2 popcnt cx16; do
    case $flags in *" $f "*) ;; *) return 1 ;; esac
  done
}

glibc_ok() {  # 0 when this is glibc 2.28 or newer
  local g major minor
  g=$(/usr/bin/getconf GNU_LIBC_VERSION 2>/dev/null || true)
  case $g in "glibc "*) ;; *) return 1 ;; esac
  g=${g#glibc }; major=${g%%.*}; minor=${g#*.}; minor=${minor%%.*}
  [[ $major =~ ^[0-9]+$ ]] && [[ $minor =~ ^[0-9]+$ ]] || return 1
  [ "$major" -gt 2 ] || { [ "$major" = 2 ] && [ "$minor" -ge "$MIN_GLIBC_MINOR" ]; }
}

private_dir() {  # private_dir DIR -> 0 when only root and this account can change DIR and every folder above it
  local d me
  me=$(/usr/bin/id -u)
  d=$(cd -P -- "$1" 2>/dev/null && pwd -P) || return 1
  while :; do
    case $(/usr/bin/stat -c '%u %a' "$d" 2>/dev/null) in
      "0 "*[0145][0145]|"$me "*[0145][0145]) ;;
      *) return 1 ;;
    esac
    [ "$d" = / ] && return 0
    d=$(/usr/bin/dirname "$d")
  done
}

landmark_zip() {  # landmark_zip -> an empty zip (its end record alone) on stdout
  printf 'PK\005\006'
  /usr/bin/head -c 18 /dev/zero
}

stage_pinned_python() {  # step 0: the pinned file, checked, unpacked in a private folder -> PY
  local f core home zip
  [ -x /usr/bin/curl ] || [ -x /usr/bin/wget ] || die "curl or wget is needed to download the satellite's Python" 2
  # The account's own home (root's for root), from the account database,
  # when no other account can change it or anything above it; else a new
  # folder under /tmp (0700), where the landmark below is what keeps the
  # search inside the tree.
  home=$(/usr/bin/getent passwd "$(/usr/bin/id -un)" | /usr/bin/cut -d: -f6)
  if [ -n "$home" ] && private_dir "$home"; then
    PYSTAGE=$(/usr/bin/mktemp -d "$home/.domovoi-python.XXXXXXXX")
  else
    PYSTAGE=$(/usr/bin/mktemp -d)
  fi
  f=$PYSTAGE/$PIN_FILE
  say "This computer gets the satellite's own Python $PIN_VERSION ($((PIN_SIZE / 1000000)) MB, pinned by its SHA-256)..."
  core=$(payload_core)
  if [ -n "$core" ]; then fetch_to "$core/v1/satellite-linux/files/$PIN_FILE" "$f" 0; fi
  if pinned_ok "$f"; then
    say "  from the Domovoi server"
  else
    fetch_to "$PIN_URL" "$f" 5
    pinned_ok "$f" || die "neither the Domovoi server nor GitHub gave the pinned Python $PIN_VERSION, so nothing was installed. In the dashboard: Settings -> Satellites -> Add a spare device -> get the Linux Python (or connect this computer to the internet), then run the command again" 1
    say "  from GitHub (python-build-standalone)"
  fi
  /usr/bin/mkdir "$PYSTAGE/x"
  /usr/bin/tar -xzf "$f" -C "$PYSTAGE/x" --no-same-owner
  /usr/bin/find "$PYSTAGE/x/python/lib" -path '*/lib-dynload/_dbm.*.so' -type f -delete
  zip=$PYSTAGE/x/python/lib/$(landmark_name)
  landmark_zip >"$zip"
  PY=$PYSTAGE/x/python/bin/python3
  [ "$($PY -I -c 'import platform; print(platform.python_version())' 2>/dev/null)" = "$PIN_VERSION" ] \
    || die "the satellite's Python $PIN_VERSION does not run on this computer, so nothing was installed" 1
  python_stays_home "$PYSTAGE/x/python" || die "the satellite's Python $PIN_VERSION reads its library from outside its own folder, so nothing was installed" 1
}

landmark_name() {  # python3XY.zip for the pinned version
  local mm=${PIN_VERSION%.*}
  printf 'python%s.zip' "${mm//./}"
}

python_stays_home() {  # python_stays_home PREFIX -> 0 when $PY's prefix and library are inside PREFIX
  # (the landmark's proof: a library planted higher up would show here).
  local got prefix os_file real
  real=$(cd -P -- "$1" 2>/dev/null && pwd -P) || return 1
  got=$($PY -I -c 'import sys, os; print(sys.prefix); print(os.__file__)' 2>/dev/null) || return 1
  prefix=$(printf '%s\n' "$got" | /usr/bin/sed -n 1p) os_file=$(printf '%s\n' "$got" | /usr/bin/sed -n 2p)
  { [ "$prefix" = "$1" ] && [[ $os_file == "$1/lib/"* ]]; } || { [ "$prefix" = "$real" ] && [[ $os_file == "$real/lib/"* ]]; }
}

install_root_s_python() {  # step 3: root's own copy of the staged Python -> PY=$PINNED_PY
  # Root copies the file into its own folder and checks THAT copy: the one
  # in the temporary folder is the installing account's to change.
  $SUDO /usr/bin/rm -rf "$PY_NEW" "$PY_OLD"
  $SUDO /usr/bin/install -o root -g root -m 0600 "$PYSTAGE/$PIN_FILE" "$PY_PART"
  SUDO_CHECK=$SUDO
  if ! pinned_ok "$PY_PART"; then
    $SUDO /usr/bin/rm -f "$PY_PART"
    die "the satellite's Python changed after it was checked, so it was not installed" 1
  fi
  SUDO_CHECK=""
  $SUDO /usr/bin/install -d -o root -g root -m 0755 "$PY_NEW"
  $SUDO /usr/bin/tar -xzf "$PY_PART" -C "$PY_NEW" --no-same-owner
  # The dbm module's Berkeley DB is copyleft (Sleepycat): never installed.
  $SUDO /usr/bin/find "$PY_NEW/python/lib" -path '*/lib-dynload/_dbm.*.so' -type f -delete
  landmark_zip | $SUDO /usr/bin/tee "$PY_NEW/python/lib/$(landmark_name)" >/dev/null
  # Readable by everyone, writable by root alone, whatever root's umask
  # made of the folders tar created (the archive lists none); no setuid.
  $SUDO /usr/bin/chmod -R u=rwX,go=rX "$PY_NEW"
  if [ -e "$PYTHON_HOME" ] || [ -L "$PYTHON_HOME" ]; then $SUDO /usr/bin/mv "$PYTHON_HOME" "$PY_OLD"; fi
  $SUDO /usr/bin/mv "$PY_NEW/python" "$PYTHON_HOME"
  $SUDO /usr/bin/rm -rf "$PY_NEW" "$PY_OLD"
  $SUDO /usr/bin/rm -f "$PY_PART"
  PY=$PINNED_PY
  python_is_root_s "$PY" && [ "$($PY -I -c 'import platform; print(platform.python_version())')" = "$PIN_VERSION" ] \
    && python_stays_home "$PYTHON_HOME" || die "root's copy of the satellite's Python is not usable" 1
  say "  the satellite's Python $PIN_VERSION is in $PYTHON_HOME (root's)"
}

remove_root_s_python() {  # root's fixed paths only; never through a link
  if [ -d "$PYTHON_HOME" ] && [ ! -L "$PYTHON_HOME" ]; then $SUDO /usr/bin/rm -rf "$PYTHON_HOME"; fi
  $SUDO /usr/bin/rm -rf "$PY_NEW" "$PY_OLD"
  $SUDO /usr/bin/rm -f "$PY_PART"
}

# ─── uninstall ───────────────────────────────────────────────────────────

purge_satellite_files() {  # the satellite's own files in $CONFIG_DIR, and only those
  # Root's copy of the purger under a Python root may run (python_is_root_s)
  # with -I, run as the account (its files, its rights): nothing the
  # satellite's account can change runs here. On a computer that also runs a Domovoi server,
  # ~/.domovoi holds that server's key, voices and wake models too, and the
  # purger leaves them.
  if [ -f "$PURGER" ] && [ ! -L "$PURGER" ] && [ "$(/usr/bin/stat -c %u "$PURGER")" = 0 ] \
     && [ -x "$PY" ] && python_is_root_s "$PY"; then
    as_sat $PY -I "$PURGER" "$CONFIG_DIR" >/dev/null
  else
    local f
    for f in config.toml approved approval_code pairing_token pending-server.json \
             server-fingerprint.json synced_sha synced_manifest.json upgrade_pending.json \
             satellite_backup.tar log-spool.txt last-health.json self-restarts.json muted voice wake \
             wake_threshold manifest-serials.json install-pc.json control.sock; do
      as_sat /usr/bin/rm -f "$CONFIG_DIR/$f"
    done
    say "  kept $CONFIG_DIR/sounds and wake_models (root's purger is missing, so which files there are the satellite's is not known)"
  fi
}

uninstall() {
  local recorded_mode recorded_account state t
  # Root's copy of the satellite's own Python, if the install used one (a
  # computer may have no python3 of its own); else the system's.
  if [ -x "$PINNED_PY" ] && python_is_root_s "$PINNED_PY"; then PY=$PINNED_PY; fi
  recorded_mode=$(record_get mode)
  recorded_account=$(record_get account)
  if [ -z "$MODE" ]; then MODE=$recorded_mode; fi
  if [ -z "$MODE" ]; then
    if [ -f "/etc/systemd/system/$UNIT" ] && /usr/bin/grep -q "User=$SAT_ACCOUNT" "/etc/systemd/system/$UNIT" 2>/dev/null; then
      MODE=headless
    else
      MODE=desktop
    fi
  fi
  case $MODE in desktop|headless) ;; *) MODE=desktop ;; esac
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
  if [ "$MODE" = desktop ] && [ "$(/usr/bin/id -u)" = 0 ]; then
    # Root, from an administrator's account (--account, or root's record):
    # for a dedicated satellite account, so nobody types an administrator's
    # password into it. The account's files still go as the account.
    ACCOUNT=${ACCOUNT:-$recorded_account}
    [ -n "$ACCOUNT" ] || die "--account NAME: the account the satellite runs as" 2
    if [ -n "$recorded_account" ] && [ "$ACCOUNT" != "$recorded_account" ]; then
      die "the satellite was installed for $recorded_account, not $ACCOUNT" 2
    fi
    local ent
    ent=$(/usr/bin/getent passwd "$ACCOUNT") || die "there is no account $ACCOUNT" 2
    [ "$(printf '%s' "$ent" | /usr/bin/cut -d: -f3)" != 0 ] || die "--account is the satellite's account, not root" 2
  elif [ "$MODE" = desktop ]; then
    # The account itself (the usual way, in your everyday account).
    if [ -n "$ACCOUNT" ] && [ "$ACCOUNT" != "$(/usr/bin/id -un)" ]; then
      die "--account is for running this as root from an administrator's account: sudo $how --account $ACCOUNT" 2
    fi
    if [ -n "$recorded_account" ] && [ "$recorded_account" != "$(/usr/bin/id -un)" ]; then
      die "the satellite was installed for $recorded_account: run this as that account, or from an administrator's account: sudo $how --account $recorded_account" 2
    fi
    ACCOUNT=""
  fi
  layout
  need_sudo
  say "Removing the Domovoi satellite ($MODE install)..."
  if [ "$MODE" = desktop ]; then
    # The account's part first, with no sudo used yet and none of the
    # satellite's own code run at all: its unit stopped (root reaches the
    # account's own systemd with -M), its files removed as the account
    # (from root: runuser, a clean environment). Root's part after.
    if have_systemd; then
      user_systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    fi
    /usr/bin/pkill -u "$RUN_AS" -f 'python -m satellite.tray' >/dev/null 2>&1 || true
    as_sat /usr/bin/rm -f "$HOME_DIR/.config/systemd/user/$UNIT" \
          "$HOME_DIR/.config/systemd/user/default.target.wants/$UNIT" \
          "$HOME_DIR/.config/autostart/domovoi-satellite-tray.desktop" \
          "$HOME_DIR/.local/share/applications/domovoi-satellite.desktop"
    have_systemd && user_systemctl daemon-reload >/dev/null 2>&1 || true
    if [ "$PURGE" = 1 ]; then
      purge_satellite_files
      say "  the satellite's settings and records are gone"
    else
      say "  kept $CONFIG_DIR/config.toml (--purge removes it)"
    fi
    as_sat /usr/bin/rm -rf "$PREFIX"
    if [ "$(record_get linger_enabled_by_installer)" = 1 ]; then
      $SUDO /usr/bin/loginctl disable-linger "$RUN_AS" || true
      say "  linger turned off again (the installer had turned it on)"
    fi
  else
    if have_systemd; then $SUDO /usr/bin/systemctl disable --now "$UNIT" >/dev/null 2>&1 || true; fi
    $SUDO /usr/bin/rm -f "/etc/systemd/system/$UNIT"
    have_systemd && $SUDO /usr/bin/systemctl daemon-reload || true
  fi
  # Power settings: only what this installer changed (root's record), and
  # only at the fixed paths and units above.
  for t in $(record_get masked_targets); do
    case " $SLEEP_TARGETS " in
      *" $t "*) $SUDO /usr/bin/systemctl unmask "$t" >/dev/null 2>&1 || true ;;
    esac
  done
  if [ "$(record_get logind_dropin)" = 1 ]; then
    $SUDO /usr/bin/rm -f "$LOGIND_DROPIN"
    say "  lid switch: back to your system's setting after the next reboot"
  fi
  if [ "$(record_get charge_rule)" = 1 ]; then
    $SUDO /usr/bin/rm -f "$CHARGE_RULE"
    say "  charge limit rule removed (the current limit stays until a reboot)"
  fi
  if [ "$MODE" = headless ]; then
    # The account's files are removed as the account, never by root.
    state=$(sat_account_state)
    case $state in
      ok)
        as_sat /usr/bin/rm -rf "$PREFIX"
        if [ "$PURGE" = 1 ]; then
          # userdel removes its home without following links out of it.
          $SUDO /usr/sbin/userdel -r "$SAT_ACCOUNT" >/dev/null 2>&1 || true
          say "  removed the $SAT_ACCOUNT account and its home"
        else
          say "  kept $CONFIG_DIR/config.toml (--purge removes it, and the $SAT_ACCOUNT account)"
        fi ;;
      absent) ;;
      *) say "  left the $SAT_ACCOUNT account and its home alone: $state" ;;
    esac
  fi
  $SUDO /usr/bin/rm -f "$RECORD"
  $SUDO /usr/bin/rmdir "$RECORD_DIR" 2>/dev/null || true
  # The satellite's own Python, if it had one, and what an interrupted
  # install left of one: root's fixed paths, after its last use above.
  remove_root_s_python
  # Last: this file itself (bash keeps reading the open copy).
  $SUDO /usr/bin/rm -f "$PURGER"
  $SUDO /usr/bin/rm -f "$UNINSTALLER"
  $SUDO /usr/bin/rmdir "$ROOT_LIB" 2>/dev/null || true
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

[ -n "$PAYLOAD" ] || die "give the enrolment payload ('domovoi-room:1?...') from the dashboard: Settings -> Satellites -> Add a spare device" 2
trap '[ -z "$PYSTAGE" ] || /usr/bin/rm -rf "$PYSTAGE"' EXIT

# ─── 0. the system, and which Python ─────────────────────────────────────

OS_ID="" OS_LIKE="" OS_NAME="this Linux"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  OS_ID=$(. /etc/os-release && printf '%s' "${ID:-}")
  # shellcheck disable=SC1091
  OS_LIKE=$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")
  # shellcheck disable=SC1091
  OS_NAME=$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-this Linux}")
fi
# How its packages are installed: apt (Debian, Ubuntu, Mint, ...), dnf
# (Fedora), zypper (openSUSE), pacman (Arch). Anything else installs them
# itself and says --skip-packages.
case " $OS_ID $OS_LIKE " in
  *" debian "*|*" ubuntu "*) FAMILY=debian ;;
  *" fedora "*) FAMILY=fedora ;;
  *" suse "*|*" opensuse "*) FAMILY=suse ;;
  *" arch "*) FAMILY=arch ;;
  *) FAMILY="" ;;
esac
if [ -z "$FAMILY" ] && [ "$SKIP_APT" = 0 ]; then
  die "this is $OS_NAME: the installer installs its few packages with apt (Debian, Ubuntu, Mint), dnf (Fedora), zypper (openSUSE) or pacman (Arch), so nothing was installed. Install PortAudio, mpg123, the ALSA utilities and (with a desktop) pactl yourself, then run the command again with --skip-packages" 2
fi
case $MACHINE in
  x86_64|aarch64) ;;
  *) die "this computer is $MACHINE: the satellite runs on x86-64 and 64-bit ARM (aarch64), so nothing was installed" 2 ;;
esac
# Before anything is downloaded: the satellite's numpy (2.4 and later) runs
# only on an x86-64-v2 processor, and would otherwise fail at its first use.
cpu_ok || die "this computer's processor is too old for Domovoi (needs roughly 2009 or newer), so nothing was installed" 2

# The computer's own python3 on Debian and Ubuntu when it is new enough and
# has a pinned package list; the satellite's own otherwise.
SYSTEM_WHY=""
if [ "$FAMILY" != debian ]; then
  SYSTEM_WHY="the computer's own Python is used only on Debian and Ubuntu"
elif [ ! -x /usr/bin/python3 ]; then
  SYSTEM_WHY="it has no python3"
elif ! /usr/bin/python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  SYSTEM_WHY="its Python is $(/usr/bin/python3 -I -c 'import platform; print(platform.python_version())' 2>/dev/null || echo unknown), older than the 3.11 the satellite needs"
elif [ ! -f "$SRC/satellite/locks/pc-$(/usr/bin/python3 -I -c 'import sys; print("cp%d%d" % sys.version_info[:2])')-$MACHINE.txt" ]; then
  SYSTEM_WHY="its Python $(/usr/bin/python3 -I -c 'import platform; print(platform.python_version())') has no pinned package list yet"
fi
case $PYTHON_CHOICE in
  system) [ -z "$SYSTEM_WHY" ] || die "--python system: $SYSTEM_WHY, so nothing was installed" 2; USE_PY=system ;;
  pinned) USE_PY=pinned ;;
  *) if [ -z "$SYSTEM_WHY" ]; then USE_PY=system; else USE_PY=pinned; fi ;;
esac
if [ "$USE_PY" = pinned ]; then
  read_pin || die "there is no pinned Python for $MACHINE in this bundle, so nothing was installed" 2
  glibc_ok || die "this Linux's C library is $(/usr/bin/getconf GNU_LIBC_VERSION 2>/dev/null || echo 'not glibc'); the satellite needs glibc 2.$MIN_GLIBC_MINOR or newer (Debian 10, Ubuntu 18.10, Fedora 29 and later), so nothing was installed" 2
  [ "$PYTHON_CHOICE" = pinned ] || say "$OS_NAME: $SYSTEM_WHY."
fi
# The Python packages come from a hash-pinned list made for this Python and
# machine (satellite/scripts/lock-pc-requirements.sh); none, no install.
if [ "$USE_PY" = pinned ]; then
  PYTAG=cp$(printf '%s' "$PIN_VERSION" | /usr/bin/cut -d. -f1-2 | /usr/bin/tr -d .)
else
  PYTAG=$($PY -I -c 'import sys; print("cp%d%d" % sys.version_info[:2])')
fi
LOCK_REL=satellite/locks/pc-$PYTAG-$MACHINE.txt
if [ ! -f "$SRC/$LOCK_REL" ]; then
  die "there is no pinned package list for Python $PYTAG on $MACHINE yet, so nothing was installed (lists: $(cd "$SRC/satellite/locks" && ls pc-*.txt 2>/dev/null | sed 's/^pc-//; s/\.txt$//' | tr '\n' ' '))" 2
fi
[ "$USE_PY" = system ] || stage_pinned_python

# ─── 1. the payload, and the core proves who it is ───────────────────────

say "Checking the Domovoi server named in the code..."
ERRFILE=$(mktemp)
set +e
# -I (as every Python this script runs): no environment, no user site, nothing
# from the current directory; the bundle is put on the path explicitly.
ENROL_JSON=$($PY -I -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("satellite.enrolment", run_name="__main__", alter_sys=True)' "$SRC" verify "$PAYLOAD" 2>"$ERRFILE")
rc=$?
set -e
msg=$(/usr/bin/cat "$ERRFILE" 2>/dev/null || true)
/usr/bin/rm -f "$ERRFILE"
if [ "$rc" = 3 ]; then
  die "the server at that address did not prove it is the one in the code, so nothing was installed: $msg" 3
elif [ "$rc" != 0 ]; then
  die "that is not a usable enrolment code: $msg" 2
fi
enrol() { printf '%s' "$ENROL_JSON" | $PY -I -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
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
$SUDO /usr/bin/install -d -o root -g root -m 0755 "$ROOT_LIB"
if ! [ "$HERE/install-pc.sh" -ef "$UNINSTALLER" ]; then
  $SUDO /usr/bin/install -o root -g root -m 0755 "$HERE/install-pc.sh" "$UNINSTALLER"
fi
$SUDO /usr/bin/install -o root -g root -m 0644 "$HERE/purge-satellite-files.py" "$PURGER"
if [ "$USE_PY" = pinned ]; then
  install_root_s_python
else
  # The computer's own Python: an earlier install's copy of ours goes.
  remove_root_s_python
fi
record_set "mode=\"$MODE\"" "account=\"$RUN_AS\"" "installed_at=$(/usr/bin/date +%s)" \
  "python=\"$USE_PY\"" "python_version=\"$($PY -I -c 'import platform; print(platform.python_version())')\""

# ─── 4. system packages ──────────────────────────────────────────────────

missing_pkgs() {
  local p out=""
  for p in "$@"; do
    case $FAMILY in
      debian) /usr/bin/dpkg-query -W -f '${Status}' "$p" 2>/dev/null | /usr/bin/grep -q "install ok installed" ;;
      fedora|suse) /usr/bin/rpm -q --quiet "$p" 2>/dev/null ;;
      arch) /usr/bin/pacman -Q "$p" >/dev/null 2>&1 ;;
    esac || out="$out $p"
  done
  printf '%s' "$out"
}
# PortAudio (the microphone and speakers), mpg123 (music), the ALSA
# utilities (aplay, amixer), the CA certificates (pip's downloads), and on
# a desktop pactl (the sound server's volume). With the computer's own
# Python (Debian and Ubuntu only) also its venv, and on a desktop Tk for
# the tray's window; the satellite's own Python has both.
case $FAMILY in
  debian) PKGS="libportaudio2 mpg123 alsa-utils ca-certificates"; DESKTOP_PKGS="pulseaudio-utils"
          if [ "$USE_PY" = system ]; then PKGS="python3-venv python3-pip $PKGS"; DESKTOP_PKGS="python3-tk $DESKTOP_PKGS"; fi ;;
  fedora) PKGS="portaudio mpg123 alsa-utils ca-certificates"; DESKTOP_PKGS="pulseaudio-utils" ;;
  suse) PKGS="libportaudio2 mpg123 alsa-utils ca-certificates"; DESKTOP_PKGS="pulseaudio-utils" ;;
  arch) PKGS="portaudio mpg123 alsa-utils ca-certificates"; DESKTOP_PKGS="libpulse" ;;
  *) PKGS="" DESKTOP_PKGS="" ;;
esac
[ "$MODE" = desktop ] && PKGS="$PKGS $DESKTOP_PKGS"
if [ "$SKIP_APT" = 0 ]; then
  MISSING=$(missing_pkgs $PKGS)
  if [ -n "$MISSING" ]; then
    say "Installing system packages:$MISSING"
    # shellcheck disable=SC2086
    case $FAMILY in
      debian)
        $SUDO /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get update -qq
        $SUDO /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $MISSING ;;
      fedora) $SUDO /usr/bin/dnf install -y -q $MISSING ;;
      suse) $SUDO /usr/bin/zypper --non-interactive --quiet install --no-recommends $MISSING ;;
      arch) $SUDO /usr/bin/pacman -S --needed --noconfirm $MISSING \
              || die "pacman could not install$MISSING: bring the system up to date (sudo pacman -Syu), then run the command again" ;;
    esac
  fi
fi

# ─── 5. the account, linger, the audio group ─────────────────────────────

if [ "$MODE" = headless ]; then
  if [ "$SAT_STATE" = absent ]; then
    $SUDO /usr/sbin/useradd --system --create-home --home-dir "$SAT_HOME_HEADLESS" --shell /usr/sbin/nologin "$SAT_ACCOUNT"
    say "  created the $SAT_ACCOUNT account (no login, no sudo)"
  fi
  # Root touches the home directory itself (its parent is root's), never
  # anything inside it: that is the account's, and is done as the account.
  if [ ! -d "$HOME_DIR" ] || [ -L "$HOME_DIR" ] || [ "$($SUDO /usr/bin/stat -c %U "$HOME_DIR")" != "$SAT_ACCOUNT" ]; then
    die "$HOME_DIR is not $SAT_ACCOUNT's own directory; remove the account (sudo userdel -r $SAT_ACCOUNT) and run the command again" 2
  fi
  $SUDO /usr/bin/chmod 0750 "$HOME_DIR"
  $SUDO /usr/sbin/usermod -aG audio "$SAT_ACCOUNT"
elif have_systemd; then
  if [ "$(/usr/bin/loginctl show-user "$RUN_AS" -p Linger --value 2>/dev/null || echo no)" != yes ]; then
    $SUDO /usr/bin/loginctl enable-linger "$RUN_AS"
    record_set "linger_enabled_by_installer=true"
    say "  linger on: the satellite starts at boot, before anyone signs in"
  fi
  # The sound devices belong to whoever sits at the screen; in the audio
  # group the satellite can still open them when nobody is signed in.
  if ! /usr/bin/id -nG "$RUN_AS" | /usr/bin/tr ' ' '\n' | /usr/bin/grep -qx audio; then
    $SUDO /usr/sbin/usermod -aG audio "$RUN_AS"
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
  if [ -d /proc/acpi/button/lid ] || /usr/bin/ls /sys/class/power_supply/BAT* >/dev/null 2>&1; then
    if power_change "Keep running with the lid closed (the lid switch does nothing)?"; then
      $SUDO /usr/bin/mkdir -p "$(/usr/bin/dirname "$LOGIND_DROPIN")"
      printf '%s\n' "# Domovoi satellite: closing the lid does not suspend (install-pc.sh)." \
        "[Login]" "HandleLidSwitch=ignore" "HandleLidSwitchExternalPower=ignore" "HandleLidSwitchDocked=ignore" \
        | $SUDO /usr/bin/tee "$LOGIND_DROPIN" >/dev/null
      record_set "logind_dropin=true"
      say "  lid switch ignored from the next reboot (the desktop's own lid setting may need the same)"
    fi
  fi
  if power_change "Never sleep or hibernate (the screen can still turn off)?"; then
    MASKED=""
    for t in $SLEEP_TARGETS; do
      if [ "$(/usr/bin/systemctl is-enabled "$t" 2>/dev/null || true)" != masked ]; then
        $SUDO /usr/bin/systemctl mask "$t" >/dev/null 2>&1 && MASKED="$MASKED \"$t\","
      fi
    done
    record_set "masked_targets=[${MASKED%,}]"
    say "  sleep and hibernate are off"
  fi
  RULES=""
  for BAT in /sys/class/power_supply/BAT*; do
    [ -e "$BAT/charge_control_end_threshold" ] || continue
    if power_change "Stop charging $(/usr/bin/basename "$BAT") at $CHARGE_LIMIT% (kinder to a battery that is always plugged in)?"; then
      RULES="${RULES}SUBSYSTEM==\"power_supply\", KERNEL==\"$(/usr/bin/basename "$BAT")\", ATTR{charge_control_end_threshold}=\"$CHARGE_LIMIT\"
"
      echo "$CHARGE_LIMIT" | $SUDO /usr/bin/tee "$BAT/charge_control_end_threshold" >/dev/null \
        || say "  this laptop refused the limit now; the rule tries again at every boot"
      say "  $(basename "$BAT") stops charging at $CHARGE_LIMIT%"
    fi
  done
  if [ -n "$RULES" ]; then
    printf '%s' "$RULES" | $SUDO /usr/bin/tee "$CHARGE_RULE" >/dev/null
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
  STAGE=$(/usr/bin/mktemp -d)
  /usr/bin/cp -a "$SRC/satellite" "$STAGE/satellite"
  /usr/bin/chmod -R a+rX "$STAGE"
  as_sat /usr/bin/mkdir -p "$PREFIX"
  as_sat /usr/bin/rm -rf "$PREFIX/satellite"
  as_sat /usr/bin/cp -a "$STAGE/satellite" "$PREFIX/satellite"
  /usr/bin/rm -rf "$STAGE"
else
  /usr/bin/mkdir -p "$PREFIX"
  /usr/bin/rm -rf "$PREFIX/satellite"
  /usr/bin/cp -a "$SRC/satellite" "$PREFIX/satellite"
fi
as_sat /usr/bin/find "$PREFIX/satellite" -name __pycache__ -type d -prune -exec /usr/bin/rm -rf {} +

say "Setting up Python (this downloads the satellite's libraries, a few minutes)..."
# A fresh venv every time: an old one is the account's, and nothing in it
# (a planted .pth runs at Python's start) is ever run.
as_sat /usr/bin/rm -rf "$VENV"
as_sat $PY -I -m venv "$VENV"
# Every package, its version and the SHA-256 of every file pip may take are
# in the list (it reached this computer inside the checked bundle); pip
# refuses anything else, and builds nothing from source.
as_sat "$VENV/bin/pip" install --no-input -q --disable-pip-version-check \
  --require-hashes --only-binary=:all: --no-deps -r "$PREFIX/$LOCK_REL"
OWW_DIR=$(as_sat "$VENV/bin/python" -c 'import os, openwakeword; print(os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models"))')
say "Fetching the wake-word models (checked against their pinned SHA-256)..."
as_sat /usr/bin/sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install models --dest '$OWW_DIR' --payload \"\$1\"" _ "$PAYLOAD" >/dev/null \
  || die "the wake-word models could not be fetched (the error is above); run the command again when the internet is back"

# ─── 8. the config ───────────────────────────────────────────────────────

CONFIG_REPORT=$(as_sat /usr/bin/sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install config --config '$CONFIG_DIR/config.toml' --permissions '$PERMISSIONS' --payload \"\$1\"" _ "$PAYLOAD")
BACKUP=$(printf '%s' "$CONFIG_REPORT" | $PY -I -c 'import json,sys; print(json.load(sys.stdin).get("backup") or "")')
[ -z "$BACKUP" ] || say "  your previous config is kept as $BACKUP"
say "  wrote $CONFIG_DIR/config.toml (pinned to $FP)"

# ─── 9. the service, the tray ────────────────────────────────────────────

render() {  # render TEMPLATE -> stdout
  /usr/bin/sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@USER@|$RUN_AS|g" -e "s|@HOME@|$HOME_DIR|g" "$1"
}

STARTED=0
if ! have_systemd; then
  say "systemd is not running here, so no service was installed. Run the satellite by hand:"
  say "  cd $PREFIX && $VENV/bin/python -m satellite.client"
elif [ "$MODE" = desktop ]; then
  /usr/bin/mkdir -p "$HOME_DIR/.config/systemd/user"
  render "$SRC/satellite/scripts/domovoi-satellite-user.service" >"$HOME_DIR/.config/systemd/user/$UNIT"
  export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(/usr/bin/id -u)}
  # A user manager that linger has only just started needs a moment.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [ -S "$XDG_RUNTIME_DIR/bus" ] || [ -S "$XDG_RUNTIME_DIR/systemd/private" ] && break
    /usr/bin/sleep 1
  done
  user_systemctl daemon-reload
  if [ "$NO_START" = 1 ]; then
    user_systemctl enable "$UNIT" >/dev/null
  else
    user_systemctl enable "$UNIT" >/dev/null
    user_systemctl restart "$UNIT"
    STARTED=1
  fi
  /usr/bin/mkdir -p "$HOME_DIR/.config/autostart" "$HOME_DIR/.local/share/applications"
  /usr/bin/cat >"$HOME_DIR/.config/autostart/domovoi-satellite-tray.desktop" <<EOF
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
  /usr/bin/cat >"$HOME_DIR/.local/share/applications/domovoi-satellite.desktop" <<EOF
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
    (cd "$PREFIX" && /usr/bin/setsid "$VENV/bin/python" -m satellite.tray >/dev/null 2>&1 </dev/null &) || true
  fi
else
  render "$SRC/satellite/scripts/domovoi-satellite-system.service" | $SUDO /usr/bin/tee "/etc/systemd/system/$UNIT" >/dev/null
  $SUDO /usr/bin/systemctl daemon-reload
  $SUDO /usr/bin/systemctl enable "$UNIT" >/dev/null
  if [ "$NO_START" = 0 ]; then $SUDO /usr/bin/systemctl restart "$UNIT"; STARTED=1; fi
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
  if as_sat /usr/bin/sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install wait --socket '$CONFIG_DIR/control.sock' --timeout '$WAIT_SEC' $SHOW_CODE"; then
    exit 0
  fi
  say "Not approved yet. It keeps waiting in the background; the code is also in the tray,"
  say "or: cd $PREFIX && $VENV/bin/python -m satellite.client --approval-code"
  exit 4
fi
exit 0
