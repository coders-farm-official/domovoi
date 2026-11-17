#!/bin/bash
# installer-scripts.sh: runs the package's postinstall and the open-setup.sh
# it starts against stand-ins for stat, id, pgrep, launchctl, mktemp, sleep
# and open, in a temporary folder. No root, no launchd, nothing on this Mac
# changes. SystemPartsTests runs it with `swift test`; it runs anywhere bash
# does (also off a Mac).
#
#   mac/tests/installer-scripts.sh      prints one line per check, exits 1 on a failure
set -u
MAC=$(cd "$(dirname "$0")/.." && pwd)
POSTINSTALL=$MAC/installer/scripts/postinstall
WAITER=$MAC/installer/open-setup.sh
T=$(mktemp -d "${TMPDIR:-/tmp}/dmk-scripts.XXXXXX")
trap 'rm -rf "$T"' EXIT
STUBS=$T/stubs
mkdir -p "$STUBS"
fails=0
pass() { echo "ok   $1"; }
fail() { echo "FAIL $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# ─── stand-ins: each writes what it was asked to $T/calls ──────────────
stub() { printf '#!/bin/sh\n%s\n' "$2" >"$STUBS/$1"; chmod +x "$STUBS/$1"; }
stub stat      'echo "stat $*" >>"$T_DIR/calls"; [ -n "${CONSOLE_USER-}" ] && echo "$CONSOLE_USER"; exit 0'
stub id        'echo "id $*" >>"$T_DIR/calls"; [ -n "${USER_UID-}" ] || exit 1; echo "$USER_UID"'
# pgrep -x Installer: "running" while the counter file is above zero (or
# for ever when it says "always").
stub pgrep     'echo "pgrep $*" >>"$T_DIR/calls"; n=$(cat "$T_DIR/installer" 2>/dev/null || echo 0); [ "$n" = always ] && exit 0; [ "$n" -gt 0 ] || exit 1; echo $((n - 1)) >"$T_DIR/installer"; exit 0'
stub launchctl 'echo "launchctl $*" >>"$T_DIR/calls"
case $1 in
  print) exit "${GUI_SESSION_STATUS:-0}" ;;
  bootstrap) cp "$3" "$T_DIR/job.plist"; exit "${BOOTSTRAP_STATUS:-0}" ;;
  *) exit 0 ;;
esac'
stub mktemp    'echo "mktemp $*" >>"$T_DIR/calls"; d="$T_DIR/tmp.$$"; mkdir "$d" && echo "$d"'
stub sleep     'echo "sleep $*" >>"$T_DIR/calls"'
stub open      'echo "open $*" >>"$T_DIR/calls"'
TESTPATH=$STUBS:/usr/bin:/bin

ROOT=$T/root
APP="$ROOT/Applications/Domovoi.app"
SHARE="$ROOT/Library/Application Support/Domovoi/share"

# One postinstall run: environment overrides as arguments (NAME=VALUE).
run_postinstall() {
  rm -f "$T/calls" "$T/job.plist"; rm -rf "$T"/tmp.*
  env -i T_DIR="$T" DOMOVOI_POSTINSTALL_TEST_PATH="$TESTPATH" CONSOLE_USER=alex USER_UID=501 "$@" \
    /bin/bash "$POSTINSTALL" "$T/DomovoiSetup-mac.pkg" / "$ROOT" >"$T/out" 2>&1
  echo $? >"$T/status"
}
calls() { cat "$T/calls" 2>/dev/null; }
bootstrapped() { calls | grep -q '^launchctl bootstrap '; }

# The package's layout: the waiter where build.sh puts it, the app.
mkdir -p "$SHARE/installer" "$APP"
cp "$WAITER" "$SHARE/installer/open-setup.sh"

echo always >"$T/installer"
run_postinstall COMMAND_LINE_INSTALL=1
check "command-line install: exit 0" '[ "$(cat "$T/status")" = 0 ]'
check "command-line install: nothing started" '! bootstrapped && ! calls | grep -q launchctl'

for who in "" root loginwindow _mbsetupuser; do
  run_postinstall CONSOLE_USER="$who"
  check "console user '${who}': exit 0, nothing started" '[ "$(cat "$T/status")" = 0 ] && ! bootstrapped'
done

run_postinstall USER_UID=
check "no UID for the user: exit 0, nothing started" '[ "$(cat "$T/status")" = 0 ] && ! bootstrapped'

echo 0 >"$T/installer"
run_postinstall
check "Installer not running (installed some other way): nothing started" '[ "$(cat "$T/status")" = 0 ] && ! bootstrapped'

echo always >"$T/installer"
run_postinstall GUI_SESSION_STATUS=113
check "no GUI session for the console user: nothing started" '[ "$(cat "$T/status")" = 0 ] && ! bootstrapped'

mv "$SHARE/installer/open-setup.sh" "$T/aside.sh"
run_postinstall
check "waiter missing: exit 0, nothing started" '[ "$(cat "$T/status")" = 0 ] && ! bootstrapped'
mv "$T/aside.sh" "$SHARE/installer/open-setup.sh"

run_postinstall
check "happy path: exit 0" '[ "$(cat "$T/status")" = 0 ]'
check "happy path: the job goes into the console user's GUI domain, never root's" 'calls | grep -Eq "^launchctl bootstrap gui/501 .+/com\.domovoi\.mac\.open-setup\.plist$"'
check "happy path: an earlier waiting job is unloaded first" '[ "$(calls | grep -n "^launchctl bootout gui/501/com.domovoi.mac.open-setup$" | cut -d: -f1)" -lt "$(calls | grep -n "^launchctl bootstrap" | cut -d: -f1)" ]'
check "happy path: the job file is deleted after bootstrap" '[ -z "$(ls -d "$T"/tmp.* 2>/dev/null)" ]'
check "happy path: the job runs open-setup.sh with its label, the app and 1800 s" \
  'grep -q "<string>$SHARE/installer/open-setup.sh</string>" "$T/job.plist" && grep -q "<string>com.domovoi.mac.open-setup</string>" "$T/job.plist" && grep -q "<string>$APP</string>" "$T/job.plist" && grep -q "<string>1800</string>" "$T/job.plist"'
check "happy path: one-shot (KeepAlive false, RunAtLoad), no UserName (it is the user's own domain)" \
  'grep -q "<key>KeepAlive</key><false/>" "$T/job.plist" && grep -q "<key>RunAtLoad</key><true/>" "$T/job.plist" && ! grep -q UserName "$T/job.plist"'
check "happy path: postinstall itself never opens the app" '! calls | grep -q "^open "'
if command -v plutil >/dev/null 2>&1; then
  check "happy path: the job file is a valid property list" 'plutil -lint "$T/job.plist" >/dev/null'
fi

run_postinstall BOOTSTRAP_STATUS=5
check "bootstrap fails: the install still succeeds" '[ "$(cat "$T/status")" = 0 ] && [ -z "$(ls -d "$T"/tmp.* 2>/dev/null)" ]'

# ─── the waiter ────────────────────────────────────────────────────────
run_waiter() {
  rm -f "$T/calls"
  env -i T_DIR="$T" DOMOVOI_OPEN_SETUP_TEST_PATH="$TESTPATH" USER_UID=501 \
    /bin/sh "$WAITER" com.domovoi.mac.open-setup "$1" "$2" >"$T/out" 2>&1
  echo $? >"$T/status"
}

echo 3 >"$T/installer"
run_waiter "$APP" 1800
check "waiter: waits while Installer runs" '[ "$(calls | grep -c "^sleep 2$")" = 3 ]'
check "waiter: then opens the app" 'calls | grep -qx "open $APP"'
check "waiter: and unloads its own job, last" '[ "$(calls | tail -n 1)" = "launchctl bootout gui/501/com.domovoi.mac.open-setup" ]'
check "waiter: exit 0" '[ "$(cat "$T/status")" = 0 ]'

echo always >"$T/installer"
run_waiter "$APP" 6
check "waiter: gives up after its limit and opens nothing" '[ "$(calls | grep -c "^sleep 2$")" = 3 ] && ! calls | grep -q "^open "'
check "waiter: unloads its job after giving up too" '[ "$(calls | tail -n 1)" = "launchctl bootout gui/501/com.domovoi.mac.open-setup" ]'

echo 0 >"$T/installer"
run_waiter "$T/nowhere/Domovoi.app" 1800
check "waiter: no app (uninstalled meanwhile): opens nothing, still unloads" '! calls | grep -q "^open " && calls | grep -q "^launchctl bootout"'

run_waiter "$APP" "not-a-number"
check "waiter: a bad limit falls back to 1800 s, never loops for ever" 'calls | grep -qx "open $APP"'

echo
if [ "$fails" -eq 0 ]; then echo "installer-scripts: all passed"; else echo "installer-scripts: $fails failed"; fi
[ "$fails" -eq 0 ]
