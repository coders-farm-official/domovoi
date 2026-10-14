#!/usr/bin/env bash
# Make this spare Linux laptop or mini PC a Domovoi room satellite.
#
# Spare-device satellites (S1, 2026-10-13). Run by the one-line command the
# dashboard shows under Settings -> Satellites -> "Add a spare device",
# which has already checked this bundle's SHA-256 before running it:
#
#   bash install-pc.sh [options] 'domovoi-room:1?core=...&fp=...&room=...'
#   bash install-pc.sh --uninstall [--purge]
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
#   3. Installs the system packages it needs (python3-venv, libportaudio2,
#      mpg123, alsa-utils; on a desktop python3-tk and pulseaudio-utils).
#   4. Copies the satellite code to ~/.local/share/domovoi-satellite (a
#      headless box: its own account, domovoi-sat, under /var/lib), makes a
#      venv, installs the Python requirements and the wake-word models (each
#      checked against its pinned SHA-256).
#   5. Writes ~/.domovoi/config.toml: the generic_pc profile, the room, the
#      pinned server, your choice from step 2.
#   6. Installs and starts the service: a systemd --user unit with linger on
#      a desktop (that is where the sound server is), a system unit on a
#      headless box; the tray in the desktop's autostart and the
#      "Domovoi Satellite" window in the app menu.
#   7. Asks, one by one, before any power change: ignore the lid switch,
#      never sleep, a battery charge limit. Each defaults to No.
#   8. Waits for you to approve the device in the dashboard, showing the
#      approval code here.
#
# No root helpers are installed, ever: the satellite cannot run anything as
# root, re-join Wi-Fi, set the clock, or apply plugin payloads that need
# root (those plugins stay Pi-only).
#
# Options:
#   --permissions automatic|ask  skip question 2
#   --mode desktop|headless      default: desktop when a graphical session is
#                                running for this account, else headless
#   --power ask|no|yes           step 7 (default ask; --yes makes it no)
#   --charge-limit N             the limit step 7 offers (default 80)
#   --yes                        no questions: --permissions must be given
#   --no-start                   install, don't start (or wait)
#   --skip-apt                   the packages are already there
#   --wait SECONDS               how long step 8 waits (default 900; 0: don't)
#   --uninstall [--purge]        undo all of it (--purge: the config too)
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
# Test seams (the harness in satellite/tests/ sets them): where systemd
# lives and what counts as "a desktop is running".
SYSTEMD_RUNTIME=${DOMOVOI_TEST_SYSTEMD_RUNTIME:-/run/systemd/system}

die() { echo "install-pc: $*" >&2; exit "${2:-1}"; }
say() { printf '%s\n' "$*"; }
usage() { sed -n '/^#   bash install-pc.sh/,/^#   bash install-pc.sh --uninstall/p' "$0" | sed 's/^#   //'; }

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
SRC=$(cd "$HERE/../.." && pwd)
[ -f "$SRC/satellite/client.py" ] || die "run this from the satellite bundle (satellite/scripts/install-pc.sh)"

SAT_ACCOUNT=domovoi-sat
SAT_HOME_HEADLESS=/var/lib/domovoi-sat
UNIT=domovoi-satellite.service

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
  # Reading or removing the satellite account's own files on a headless box.
  SAT_SUDO=""
  if [ "$MODE" = headless ]; then SAT_SUDO=$SUDO; fi
}

as_sat() {  # run a command as the satellite's account
  if [ "$MODE" = headless ]; then $SUDO -u "$SAT_ACCOUNT" -H -- "$@"; else "$@"; fi
}

have_systemd() { [ -d "$SYSTEMD_RUNTIME" ]; }

user_systemctl() { systemctl --user "$@"; }

# ─── uninstall ───────────────────────────────────────────────────────────

state_get() {  # state_get KEY -> value from the installer's record, or ""
  local f=$CONFIG_DIR/install-pc.json
  $SAT_SUDO test -f "$f" || return 0
  $SAT_SUDO cat "$f" | python3 -c 'import json,sys
try: d = json.load(sys.stdin)
except Exception: d = {}
v = d.get(sys.argv[1], "")
print(" ".join(v) if isinstance(v, list) else ("1" if v is True else "" if v is False else v))' "$1"
}

uninstall() {
  if [ -z "$MODE" ]; then
    if [ -f "/etc/systemd/system/$UNIT" ] && grep -q "User=$SAT_ACCOUNT" "/etc/systemd/system/$UNIT" 2>/dev/null; then
      MODE=headless
    else
      MODE=desktop
    fi
  fi
  layout
  need_sudo
  say "Removing the Domovoi satellite ($MODE install)..."
  if [ "$MODE" = desktop ]; then
    if have_systemd; then
      user_systemctl disable --now "$UNIT" >/dev/null 2>&1 || true
    fi
    rm -f "$HOME_DIR/.config/systemd/user/$UNIT"
    have_systemd && user_systemctl daemon-reload >/dev/null 2>&1 || true
    pkill -u "$(id -un)" -f 'python -m satellite.tray' >/dev/null 2>&1 || true
    rm -f "$HOME_DIR/.config/autostart/domovoi-satellite-tray.desktop" \
          "$HOME_DIR/.local/share/applications/domovoi-satellite.desktop"
    if [ "$(state_get linger_enabled_by_installer)" = 1 ]; then
      $SUDO loginctl disable-linger "$RUN_AS" || true
      say "  linger turned off again (the installer had turned it on)"
    fi
  else
    if have_systemd; then $SUDO systemctl disable --now "$UNIT" >/dev/null 2>&1 || true; fi
    $SUDO rm -f "/etc/systemd/system/$UNIT"
    have_systemd && $SUDO systemctl daemon-reload || true
  fi
  # Power settings: only what this installer changed, from its record.
  local t
  for t in $(state_get masked_targets); do
    $SUDO systemctl unmask "$t" >/dev/null 2>&1 || true
  done
  if [ -n "$(state_get logind_dropin)" ]; then
    $SUDO rm -f "$(state_get logind_dropin)"
    say "  lid switch: back to your system's setting after the next reboot"
  fi
  if [ -n "$(state_get charge_rule)" ]; then
    $SUDO rm -f "$(state_get charge_rule)"
    say "  charge limit rule removed (the current limit stays until a reboot)"
  fi
  if [ "$MODE" = headless ]; then
    $SUDO rm -rf "$PREFIX"
  else
    rm -rf "$PREFIX"
  fi
  if [ "$PURGE" = 1 ]; then
    # Only what a satellite keeps there: on a box that also runs a Domovoi
    # server, ~/.domovoi holds that server's identity too.
    local f
    for f in config.toml config.toml.bak approved approval_code pairing_token pending-server.json \
             server-fingerprint.json synced_sha synced_manifest.json upgrade_pending.json \
             satellite_backup.tar log-spool.txt last-health.json self-restarts.json muted voice wake \
             wake_threshold manifest-serials.json install-pc.json control.sock; do
      $SAT_SUDO rm -f "$CONFIG_DIR/$f"
    done
    $SAT_SUDO rm -rf "$CONFIG_DIR/sounds" "$CONFIG_DIR/wake_models"
    if [ "$MODE" = headless ] && id "$SAT_ACCOUNT" >/dev/null 2>&1; then
      $SUDO userdel -r "$SAT_ACCOUNT" >/dev/null 2>&1 || true
      say "  removed the $SAT_ACCOUNT account and its home"
    fi
    say "  the satellite's settings and records are gone"
  else
    say "  kept $CONFIG_DIR/config.toml (--purge removes it)"
  fi
  say "Done. In the dashboard, remove the room too (Settings -> Satellites -> the room -> Remove):"
  say "that frees its name and stops its music player."
}

if [ "$UNINSTALL" = 1 ]; then
  uninstall
  exit 0
fi

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
enrol() { printf '%s' "$ENROL_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
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
say "Installing for $RUN_AS ($MODE): code in $PREFIX, settings in $CONFIG_DIR, updates: $PERMISSIONS"

# ─── 3. system packages ──────────────────────────────────────────────────

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
    need_sudo
    say "Installing system packages:$MISSING"
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    # shellcheck disable=SC2086
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $MISSING
  fi
fi

# ─── 4. the account, the code, the venv, the models ──────────────────────

if [ "$MODE" = headless ]; then
  need_sudo
  if ! id "$SAT_ACCOUNT" >/dev/null 2>&1; then
    $SUDO useradd --system --create-home --home-dir "$SAT_HOME_HEADLESS" --shell /usr/sbin/nologin "$SAT_ACCOUNT"
    say "  created the $SAT_ACCOUNT account (no login, no sudo)"
  fi
  $SUDO usermod -aG audio "$SAT_ACCOUNT"
  $SUDO install -d -o "$SAT_ACCOUNT" -g "$SAT_ACCOUNT" -m 0750 "$HOME_DIR" "$PREFIX"
  STAGE=$(mktemp -d)
  cp -a "$SRC/satellite" "$STAGE/satellite"
  chmod -R a+rX "$STAGE"
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
[ -x "$VENV/bin/python" ] || as_sat python3 -m venv "$VENV"
REQ=$(as_sat mktemp)
# spidev drives a Pi HAT's LEDs: never wanted here, and no wheel on a laptop.
as_sat sh -c "grep -viE '^[[:space:]]*spidev([^A-Za-z0-9_-]|\$)' '$PREFIX/satellite/requirements.txt' > '$REQ'"
as_sat "$VENV/bin/pip" install --no-input -q --disable-pip-version-check -r "$REQ"
as_sat rm -f "$REQ"
if [ "$MODE" = desktop ]; then
  as_sat "$VENV/bin/pip" install --no-input -q --disable-pip-version-check -r "$PREFIX/satellite/requirements-pc.txt"
fi
as_sat "$VENV/bin/pip" install --no-input -q --disable-pip-version-check --no-deps "openwakeword==0.6.0"
OWW_DIR=$(as_sat "$VENV/bin/python" -c 'import os, openwakeword; print(os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models"))')
say "Fetching the wake-word models (checked against their pinned SHA-256)..."
as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install models --dest '$OWW_DIR' --payload \"\$1\"" _ "$PAYLOAD" >/dev/null \
  || die "the wake-word models could not be fetched (the error is above); run the command again when the internet is back"

# ─── 5. the config ───────────────────────────────────────────────────────

CONFIG_REPORT=$(as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install config --config '$CONFIG_DIR/config.toml' --permissions '$PERMISSIONS' --payload \"\$1\"" _ "$PAYLOAD")
BACKUP=$(printf '%s' "$CONFIG_REPORT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("backup") or "")')
[ -z "$BACKUP" ] || say "  your previous config is kept as $BACKUP"
say "  wrote $CONFIG_DIR/config.toml (pinned to $FP)"

note_state() {  # note_state KEY=JSON...
  as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install state --config-dir '$CONFIG_DIR' --set \"\$@\"" _ "$@" >/dev/null
}
note_state "mode=\"$MODE\"" "prefix=\"$PREFIX\"" "room_id=\"$ROOM_ID\"" "installed_at=$(date +%s)"

# ─── 6. the service, the tray ────────────────────────────────────────────

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
  need_sudo
  if [ "$(loginctl show-user "$RUN_AS" -p Linger --value 2>/dev/null || echo no)" != yes ]; then
    $SUDO loginctl enable-linger "$RUN_AS"
    note_state "linger_enabled_by_installer=true"
    say "  linger on: the satellite starts at boot, before anyone signs in"
  fi
  # The sound devices belong to whoever sits at the screen; in the audio
  # group the satellite can still open them when nobody is signed in.
  if ! id -nG "$RUN_AS" | tr ' ' '\n' | grep -qx audio; then
    $SUDO usermod -aG audio "$RUN_AS"
    note_state "audio_group_added=true"
    say "  $RUN_AS joined the audio group (sound while nobody is signed in; takes effect at the next sign-in)"
  fi
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

# ─── 7. power: asked, one at a time, default No ──────────────────────────

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
      need_sudo
      DROPIN=/etc/systemd/logind.conf.d/90-domovoi-satellite.conf
      $SUDO mkdir -p /etc/systemd/logind.conf.d
      printf '%s\n' "# Domovoi satellite: closing the lid does not suspend (install-pc.sh)." \
        "[Login]" "HandleLidSwitch=ignore" "HandleLidSwitchExternalPower=ignore" "HandleLidSwitchDocked=ignore" \
        | $SUDO tee "$DROPIN" >/dev/null
      note_state "logind_dropin=\"$DROPIN\""
      say "  lid switch ignored from the next reboot (the desktop's own lid setting may need the same)"
    fi
  fi
  if power_change "Never sleep or hibernate (the screen can still turn off)?"; then
    need_sudo
    MASKED=""
    for t in sleep.target suspend.target hibernate.target hybrid-sleep.target; do
      if [ "$(systemctl is-enabled "$t" 2>/dev/null || true)" != masked ]; then
        $SUDO systemctl mask "$t" >/dev/null 2>&1 && MASKED="$MASKED \"$t\","
      fi
    done
    note_state "masked_targets=[${MASKED%,}]"
    say "  sleep and hibernate are off"
  fi
  RULE=/etc/udev/rules.d/90-domovoi-charge-limit.rules
  RULES=""
  for BAT in /sys/class/power_supply/BAT*; do
    [ -e "$BAT/charge_control_end_threshold" ] || continue
    if power_change "Stop charging $(basename "$BAT") at $CHARGE_LIMIT% (kinder to a battery that is always plugged in)?"; then
      need_sudo
      RULES="${RULES}SUBSYSTEM==\"power_supply\", KERNEL==\"$(basename "$BAT")\", ATTR{charge_control_end_threshold}=\"$CHARGE_LIMIT\"
"
      echo "$CHARGE_LIMIT" | $SUDO tee "$BAT/charge_control_end_threshold" >/dev/null \
        || say "  this laptop refused the limit now; the rule tries again at every boot"
      say "  $(basename "$BAT") stops charging at $CHARGE_LIMIT%"
    fi
  done
  if [ -n "$RULES" ]; then
    printf '%s' "$RULES" | $SUDO tee "$RULE" >/dev/null
    note_state "charge_rule=\"$RULE\""
  fi
fi

# ─── 8. approval ─────────────────────────────────────────────────────────

say ""
say "Installed. The satellite is the room \"$ROOM\" ($ROOM_ID)."
if [ "$MODE" = desktop ]; then
  say "  status:    cd $PREFIX && $VENV/bin/python -m satellite.client --status"
  say "  log:       journalctl --user -u domovoi-satellite -e"
else
  say "  status:    sudo -u $SAT_ACCOUNT sh -c 'cd $PREFIX && $VENV/bin/python -m satellite.client --status'"
  say "  log:       journalctl -u domovoi-satellite -e"
fi
if [ "$MODE" = desktop ]; then
  say "  uninstall: bash $PREFIX/satellite/scripts/install-pc.sh --uninstall"
else
  say "  uninstall: sudo bash $PREFIX/satellite/scripts/install-pc.sh --uninstall --mode headless"
fi
if [ "$STARTED" = 1 ] && [ "$WAIT_SEC" -gt 0 ]; then
  say ""
  say "Now approve it: in the dashboard, Settings -> Satellites, type the code shown below."
  if as_sat sh -c "cd '$PREFIX' && '$VENV/bin/python' -m satellite.pc_install wait --socket '$CONFIG_DIR/control.sock' --timeout '$WAIT_SEC'"; then
    exit 0
  fi
  say "Not approved yet. It keeps waiting in the background; the code is also in the tray,"
  say "or: cd $PREFIX && $VENV/bin/python -m satellite.client --approval-code"
  exit 4
fi
exit 0
