#!/usr/bin/env bash
# Hermetic tests for scripts/linux/install-update-unit.sh.
#
# Each case builds a throwaway git repo (commit A, which the "core" is
# running, then commit B, pulled but not applied), a fake venv, a fake root
# directory the script reads and writes through DOMOVOI_INSTALL_ROOT, and
# PATH shims for systemctl, visudo, sudo, curl, getent, git, docker, id,
# install, stat, find and chown that log every call and can be told to fail. Nothing
# touches systemd, sudoers, Docker or a real core, so this runs under Git
# Bash as well as on Linux.
#
# The shims model just enough of the real thing to test the decisions:
#   * systemctl show answers LoadState for the three domovoi units (loaded,
#     unless listed in missing-units) and the core unit's User= (core-user),
#     WorkingDirectory (the repo) and ExecStart (the venv's python);
#     domovoi-update.service is loaded once daemon-reload has read its file,
#     and NeedDaemonReload says whether the file changed since;
#     `start domovoi-update.service` writes last-result.json with the status
#     in apply-status (ok);
#   * sudo -u USER runs the command as given; sudo -n -l CMD succeeds when
#     the installed sudoers file holds exactly "USER ALL=(root) NOPASSWD: CMD"
#     (and sudo-deny isn't set);
#   * visudo -cf fails when visudo-reject is set; visudo -c fails when
#     visudo-broken is set, or when visudo-fail-with-rule is set and the new
#     rule is in place;
#   * curl serves version.json as GET /v1/admin/version, or fails (core-down,
#     or exit 22 when core-wants-token holds a token its -H @- stdin lacks);
#   * getent passwd USER answers with the home in the home file, or not at
#     all (no such user) when there is none;
#   * id -u says 0 (or what uid holds); install drops -o/-g (there is no root
#     user to hand files to here) and runs the real install;
#   * stat -c %U says what owner holds; stat -c '%u %a' says root-owned 755
#     unless the path is listed in insecure-paths; find ... ! -user prints
#     what foreign holds (a path the service user doesn't own); chown only
#     logs; cp fails on a .bak- copy when backup-fail is set;
#   * systemctl daemon-reload fails once when reload-fail is set, and
#     domovoi-update.service never loads when update-unit-unloadable is set;
#     install fails on the unit's temp file when install-fail-unit is set;
#   * the venv's python answers `pip show piper-tts` with piper-version
#     (1.3.0), or not at all when piper-missing is set.
#
# Usage: bash scripts/linux/tests/test-install-update-unit.sh
# Exit status is non-zero if any case failed.

set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SCRIPT=$(cd "$HERE/.." && pwd)/install-update-unit.sh
WORK=$(mktemp -d)
KEEP_WORK=${KEEP_WORK:-0}
cleanup() { if [ "$KEEP_WORK" = 1 ]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi; }
trap cleanup EXIT

# Hermetic git: no user or system config (hooks, signing, autocrlf).
: >"$WORK/gitconfig"
export GIT_CONFIG_GLOBAL=$WORK/gitconfig GIT_CONFIG_NOSYSTEM=1
SHIM_REAL_GIT=$(command -v git)
SHIM_REAL_INSTALL=$(command -v install)
SHIM_REAL_STAT=$(command -v stat)
SHIM_REAL_FIND=$(command -v find)
SHIM_REAL_ID=$(command -v id)
SHIM_REAL_CP=$(command -v cp)
export SHIM_REAL_GIT SHIM_REAL_INSTALL SHIM_REAL_STAT SHIM_REAL_FIND SHIM_REAL_ID SHIM_REAL_CP
export GIT_AUTHOR_NAME=harness GIT_AUTHOR_EMAIL=harness@example.invalid
export GIT_COMMITTER_NAME=harness GIT_COMMITTER_EMAIL=harness@example.invalid

GRANT_CMD="/usr/bin/systemctl --no-block start domovoi-update.service"
RULE="tester ALL=(root) NOPASSWD: $GRANT_CMD"

PASSED=0
FAILED=0
SKIPPED=0
CASE_FAILED=0

fail() { echo "    FAIL: $*"; CASE_FAILED=1; }
check() { local what=$1; shift; if ! "$@"; then fail "$what"; fi; }
# skip_case WHY: this case cannot run on this box; counted, never failed.
skip_case() { SKIPPED=$((SKIPPED + 1)); echo "skip $CASE_NAME ($1)"; }

# ─── fixtures ────────────────────────────────────────────────────────────

write_shims() {
  local bin=$1
  mkdir -p "$bin"

  cat >"$bin/systemctl" <<'SH'
#!/usr/bin/env bash
echo "systemctl $*" >>"$SHIM_STATE/calls.log"
unit_file=$SHIM_ROOT/etc/systemd/system/domovoi-update.service
loaded=$SHIM_STATE/loaded-update-unit
if [ "${1-}" = show ]; then
  # show -p PROP --value UNIT
  prop=${3-} unit=${5-}
  case "$unit $prop" in
    "domovoi-update.service LoadState")
      if [ -f "$SHIM_STATE/update-unit-unloadable" ]; then echo bad-setting
      elif [ -f "$loaded" ]; then echo loaded; else echo not-found; fi ;;
    "domovoi-update.service NeedDaemonReload")
      if [ -f "$loaded" ] && [ -f "$unit_file" ] && ! cmp -s "$unit_file" "$loaded"; then echo yes; else echo no; fi ;;
    *" LoadState")
      if grep -qxF -- "$unit" "$SHIM_STATE/missing-units" 2>/dev/null; then echo not-found; else echo loaded; fi ;;
    "domovoi-core.service User") cat "$SHIM_STATE/core-user" ;;
    "domovoi-core.service WorkingDirectory") echo "$SHIM_REPO" ;;
    "domovoi-core.service ExecStart")
      echo "{ path=$SHIM_VENV/bin/python ; argv[]=$SHIM_VENV/bin/python -m domovoi.main ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }" ;;
  esac
  exit 0
fi
case "${1-}" in
  daemon-reload)
    if [ -f "$SHIM_STATE/reload-fail" ]; then
      rm -f "$SHIM_STATE/reload-fail"
      echo "Failed to reload daemon: Connection timed out" >&2; exit 1
    fi
    if [ -f "$unit_file" ]; then cp "$unit_file" "$loaded"; else rm -f "$loaded"; fi ;;
  start)
    if [ "${2-}" = domovoi-update.service ]; then
      [ -f "$loaded" ] || { echo "Failed to start domovoi-update.service: Unit domovoi-update.service not found." >&2; exit 5; }
      status=$(cat "$SHIM_STATE/apply-status" 2>/dev/null || echo ok)
      upd=$SHIM_ROOT/var/lib/domovoi-update
      from=$(tr -d '[:space:]' <"$upd/applied_sha")
      to=$("$SHIM_REAL_GIT" -C "$SHIM_REPO" rev-parse HEAD)
      err=null
      [ "$status" = ok ] || err='"health failed (exit 1): not healthy after 120s: core down, web up"'
      printf '{\n  "status": "%s",\n  "mode": "update",\n  "from_sha": "%s",\n  "to_sha": "%s",\n  "error": %s,\n  "steps": []\n}\n' \
        "$status" "$from" "$to" "$err" >"$upd/last-result.json"
      if [ "$status" != ok ]; then
        echo "Job for domovoi-update.service failed because the control process exited with error code." >&2
        exit 1
      fi
    fi ;;
esac
exit 0
SH

  cat >"$bin/sudo" <<'SH'
#!/usr/bin/env bash
echo "sudo $*" >>"$SHIM_STATE/calls.log"
user="" list=0
while [ $# -gt 0 ]; do
  case $1 in
    -n) shift ;;
    -u) user=$2; shift 2 ;;
    -l) list=1; shift ;;
    --) shift; break ;;
    *) break ;;
  esac
done
if [ "$list" = 1 ]; then
  # sudo -l CMD: may the calling user run exactly CMD?
  [ ! -f "$SHIM_STATE/sudo-deny" ] || exit 1
  grep -qxF -- "${SHIM_SUDO_AS:-root} ALL=(root) NOPASSWD: $*" "$SHIM_ROOT/etc/sudoers.d/domovoi-update" 2>/dev/null
  exit $?
fi
if [ -n "$user" ]; then export SHIM_SUDO_AS=$user; fi
exec "$@"
SH

  cat >"$bin/visudo" <<'SH'
#!/usr/bin/env bash
echo "visudo $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = -cf ]; then
  if [ -f "$SHIM_STATE/visudo-reject" ]; then echo "$2:1:38: syntax error"; echo ">>> $2: syntax error near line 1 <<<"; exit 1; fi
  echo "$2: parsed OK"; exit 0
fi
if [ "${1-}" = -c ] && [ $# -eq 1 ]; then
  if [ -f "$SHIM_STATE/visudo-broken" ]; then echo ">>> /etc/sudoers.d/other: syntax error near line 3 <<<"; exit 1; fi
  if [ -f "$SHIM_STATE/visudo-fail-with-rule" ] \
      && grep -qF -- '--no-block start domovoi-update.service' "$SHIM_ROOT/etc/sudoers.d/domovoi-update" 2>/dev/null; then
    echo ">>> /etc/sudoers.d/domovoi-update: duplicate Defaults near line 1 <<<"; exit 1
  fi
  echo "/etc/sudoers: parsed OK"; exit 0
fi
echo "visudo: unexpected: $*" >&2; exit 2
SH

  cat >"$bin/curl" <<'SH'
#!/usr/bin/env bash
url=${!#}
echo "curl $url" >>"$SHIM_STATE/calls.log"
echo "curl-args $*" >>"$SHIM_STATE/calls.log"
rm -f "$SHIM_STATE/curl-stdin"
if [[ " $* " == *" @- "* ]]; then cat >"$SHIM_STATE/curl-stdin"; fi
if [ -f "$SHIM_STATE/core-down" ]; then echo "curl: (7) Failed to connect" >&2; exit 7; fi
if [ -f "$SHIM_STATE/core-wants-token" ] \
    && ! grep -qxF -- "X-Device-Token: $(cat "$SHIM_STATE/core-wants-token")" "$SHIM_STATE/curl-stdin" 2>/dev/null; then
  echo "curl: (22) The requested URL returned error: 401" >&2; exit 22
fi
cat "$SHIM_STATE/version.json"
SH

  cat >"$bin/getent" <<'SH'
#!/usr/bin/env bash
echo "getent $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = passwd ] && [ -f "$SHIM_STATE/home" ]; then
  printf '%s:x:1001:1001::%s:/bin/bash\n' "${2-}" "$(cat "$SHIM_STATE/home")"; exit 0
fi
exit 2
SH

  cat >"$bin/docker" <<'SH'
#!/usr/bin/env bash
echo "docker $*" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/docker-denied" ]; then
  echo "permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock" >&2
  exit 1
fi
echo 0123456789ab
SH

  cat >"$bin/git" <<'SH'
#!/usr/bin/env bash
echo "git $*" >>"$SHIM_STATE/calls.log"
exec "$SHIM_REAL_GIT" "$@"
SH

  cat >"$bin/id" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -u ]; then
  if [ $# -eq 1 ]; then cat "$SHIM_STATE/uid" 2>/dev/null || echo 0; exit 0; fi
  if [ "$2" = root ]; then echo 0; exit 0; fi
  if grep -qxF -- "$2" "$SHIM_STATE/no-users" 2>/dev/null; then echo "id: '$2': no such user" >&2; exit 1; fi
  echo 1001; exit 0
fi
exec "$SHIM_REAL_ID" "$@"
SH

  cat >"$bin/install" <<'SH'
#!/usr/bin/env bash
echo "install $*" >>"$SHIM_STATE/calls.log"
if [ -f "$SHIM_STATE/install-fail-unit" ] && [[ ${!#} == */.domovoi-update.service.* ]]; then
  echo "install: cannot create regular file '${!#}': No space left on device" >&2; exit 1
fi
args=()
while [ $# -gt 0 ]; do
  case $1 in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
done
exec "$SHIM_REAL_INSTALL" "${args[@]}"
SH

  cat >"$bin/stat" <<'SH'
#!/usr/bin/env bash
if [ "${1-}" = -c ] && [ "${2-}" = %U ]; then cat "$SHIM_STATE/owner"; exit 0; fi
if [ "${1-}" = -c ] && [ "${2-}" = '%u %a' ]; then
  echo "stat -c %u %a ${3-}" >>"$SHIM_STATE/calls.log"
  if grep -qxF -- "${3-}" "$SHIM_STATE/insecure-paths" 2>/dev/null; then echo "1001 775"; else echo "0 755"; fi
  exit 0
fi
exec "$SHIM_REAL_STAT" "$@"
SH

  cat >"$bin/cp" <<'SH'
#!/usr/bin/env bash
if [ -f "$SHIM_STATE/backup-fail" ] && [[ " $* " == *.bak-* ]]; then
  echo "cp: cannot create regular file: No space left on device" >&2; exit 1
fi
exec "$SHIM_REAL_CP" "$@"
SH

  cat >"$bin/find" <<'SH'
#!/usr/bin/env bash
case " $* " in
  *" -user "*) echo "find $*" >>"$SHIM_STATE/calls.log"; cat "$SHIM_STATE/foreign" 2>/dev/null; exit 0 ;;
esac
exec "$SHIM_REAL_FIND" "$@"
SH

  cat >"$bin/chown" <<'SH'
#!/usr/bin/env bash
echo "chown $*" >>"$SHIM_STATE/calls.log"
SH

  chmod +x "$bin"/*
}

write_venv() {
  local venv=$1
  mkdir -p "$venv/bin"
  : >"$venv/pyvenv.cfg"
  cat >"$venv/bin/python" <<'SH'
#!/usr/bin/env bash
echo "python $*" >>"$SHIM_STATE/calls.log"
if [ "${1-}" = -m ] && [ "${2-}" = pip ] && [[ " $* " == *" show piper-tts "* ]]; then
  if [ -f "$SHIM_STATE/piper-missing" ]; then echo "WARNING: Package(s) not found: piper-tts" >&2; exit 1; fi
  printf 'Name: piper-tts\nVersion: %s\nSummary: fast local TTS\n' "$(cat "$SHIM_STATE/piper-version" 2>/dev/null || echo 1.3.0)"
fi
exit 0
SH
  chmod +x "$venv/bin/python"
}

g() { git -C "$REPO" "$@"; }

commit_all() { g add -A >/dev/null && g commit -q -m "$1" && g rev-parse HEAD; }

# new_case NAME: a repo at commit B with the core running commit A, the
# shims, a venv, and an empty fake root. Sets CASE, REPO, STATE, ROOT, VENV,
# UPD, UNIT, SUDOERS, SHA_A, SHA_B and exports the shim env.
new_case() {
  CASE_NAME=$1
  CASE_FAILED=0
  CASE=$WORK/$1
  REPO=$CASE/repo
  STATE=$CASE/state
  ROOT=$CASE/root
  VENV=$CASE/venv
  UPD=$ROOT/var/lib/domovoi-update
  UNIT=$ROOT/etc/systemd/system/domovoi-update.service
  SUDOERS=$ROOT/etc/sudoers.d/domovoi-update
  mkdir -p "$REPO/scripts/linux" "$REPO/domovoi" "$STATE" "$CASE/tmp" \
    "$ROOT/run/systemd/system" "$ROOT/etc/systemd/system" "$ROOT/etc/sudoers.d" "$ROOT/usr/bin"
  printf '#!/bin/sh\nexit 0\n' >"$ROOT/usr/bin/systemctl"
  chmod +x "$ROOT/usr/bin/systemctl"
  write_shims "$CASE/bin"
  write_venv "$VENV"
  : >"$STATE/calls.log"
  echo tester >"$STATE/owner"
  echo tester >"$STATE/core-user"

  g init -q -b main
  printf '#!/usr/bin/env bash\necho apply\n' >"$REPO/scripts/linux/apply-update.sh"
  printf 'services: {}\n' >"$REPO/domovoi/docker-compose.yml"
  printf 'print("a")\n' >"$REPO/app.py"
  SHA_A=$(commit_all A)
  printf 'print("b")\n' >"$REPO/app.py"
  SHA_B=$(commit_all "B: pulled, not applied")
  # What the core serves: short SHAs, the running one dirty. The note field
  # stands for everything else in the answer, none of which may be printed.
  printf '{"sha":"%s-dirty","running_sha":"%s-dirty","checkout_sha":"%s","restart_required":true,"note":"SENTINEL-BODY-NOT-PRINTED"}\n' \
    "${SHA_A:0:7}" "${SHA_A:0:7}" "${SHA_B:0:7}" >"$STATE/version.json"

  export SHIM_STATE=$STATE SHIM_REPO=$REPO SHIM_VENV=$VENV SHIM_ROOT=$ROOT
}

# run_install [ARGS...]: run the script against the current case.
run_install() {
  RC=0
  env PATH="$CASE/bin:$PATH" DOMOVOI_INSTALL_ROOT="$ROOT" DOMOVOI_INSTALL_TEST_HARNESS=1 \
    TMPDIR="$CASE/tmp" bash "$SCRIPT" "$@" >"$CASE/output.log" 2>&1 || RC=$?
}

# run_env VAR=VALUE... -- ARGS...: run the script with only the test
# variables given (neither DOMOVOI_INSTALL_ROOT nor
# DOMOVOI_INSTALL_TEST_HARNESS otherwise), for the cases about them.
run_env() {
  local envs=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
  shift
  RC=0
  env -u DOMOVOI_INSTALL_ROOT -u DOMOVOI_INSTALL_TEST_HARNESS PATH="$CASE/bin:$PATH" \
    TMPDIR="$CASE/tmp" "${envs[@]}" bash "$SCRIPT" "$@" >"$CASE/output.log" 2>&1 || RC=$?
}

called() { grep -qF -- "$1" "$STATE/calls.log"; }
not_called() { ! grep -qF -- "$1" "$STATE/calls.log"; }
starts() { grep -q -- "^$1" "$STATE/calls.log"; }
none_start() { ! grep -q -- "^$1" "$STATE/calls.log"; }
count_x() { grep -cxF -- "$1" "$STATE/calls.log"; }
line_of() { grep -nF -- "$1" "$STATE/calls.log" | head -n 1 | cut -d: -f1; }
before() {  # before A B: the first call matching A precedes the first matching B
  local a b
  a=$(line_of "$1"); b=$(line_of "$2")
  [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
}
said() { grep -qF -- "$1" "$CASE/output.log"; }
not_said() { ! grep -qF -- "$1" "$CASE/output.log"; }
eq() { [ "$1" = "$2" ] || { echo "      expected [$2], got [$1]"; return 1; }; }
file_is() { [ -f "$1" ] && eq "$(tr -d '[:space:]' <"$1")" "$2"; }
absent() { [ ! -e "$1" ]; }

# Every path and every file's checksum under the fake root.
tree_sig() {
  (cd "$ROOT" && "$SHIM_REAL_FIND" . | LC_ALL=C sort && "$SHIM_REAL_FIND" . -type f -exec cksum {} + | LC_ALL=C sort)
}

expected_unit() {
  printf '%s\n' \
    '[Unit]' \
    'Description=Domovoi update (back up, sync, migrate, restart, roll back on failure)' \
    'After=docker.service network-online.target' \
    'Wants=network-online.target' \
    '' \
    '[Service]' \
    'Type=oneshot' \
    'EnvironmentFile=-/etc/default/domovoi-update' \
    "ExecStart=/bin/bash $REPO/scripts/linux/apply-update.sh" \
    'TimeoutStartSec=30min'
}

nothing_installed() {
  absent "$UNIT" && absent "$SUDOERS" && absent "$UPD/applied_sha" \
    && none_start "install " && not_called "daemon-reload" && not_called "chown"
}

end_case() {
  if [ "$CASE_FAILED" = 0 ]; then
    PASSED=$((PASSED + 1)); echo "ok   $CASE_NAME"
  else
    FAILED=$((FAILED + 1)); echo "FAIL $CASE_NAME"
    echo "    --- calls"; sed 's/^/    /' "$STATE/calls.log"
    echo "    --- output"; sed 's/^/    /' "$CASE/output.log"
  fi
}

# ─── cases ───────────────────────────────────────────────────────────────

case_fresh_install() {
  new_case fresh_install
  run_install
  check "exit 0" eq "$RC" 0
  check "asks the running core" called "curl http://127.0.0.1:6370/v1/admin/version"
  check "verifies the running SHA, -dirty stripped, as the service user" \
    called "sudo -n -u tester -- git -C $REPO rev-parse --verify --quiet ${SHA_A:0:7}^{commit}"
  check "records the running SHA in full, not HEAD" file_is "$UPD/applied_sha" "$SHA_A"
  check "creates the state dir 0755 root:root" called "install -d -m 0755 -o root -g root $UPD"
  check "the unit is the doc's" eq "$(cat "$UNIT")" "$(expected_unit)"
  check "the grant is the doc's one rule" eq "$(cat "$SUDOERS")" "$RULE"
  check "grant installed 0440 root:root" called "install -m 0440 -o root -g root"
  check "unit installed 0644 root:root, from a mktemp name" \
    starts "install -m 0644 -o root -g root .*/etc/systemd/system/\.domovoi-update\.service\.[A-Za-z0-9]\{6\}$"
  check "baseline installed 0644 root:root, from a mktemp name" \
    starts "install -m 0644 -o root -g root .*/\.applied_sha\.[A-Za-z0-9]\{6\}$"
  check "grant installed from a mktemp name" \
    starts "install -m 0440 -o root -g root .*/etc/sudoers\.d/\.domovoi-update\.[A-Za-z0-9]\{6\}$"
  check "no dot-files left behind" eq "$(ls -A "$ROOT/etc/sudoers.d" "$ROOT/etc/systemd/system" "$UPD" | grep -c '^\.')" 0
  check "sudoers checked as it is, first" before "visudo -c" "visudo -cf"
  check "the rule checked alone before it goes in" before "visudo -cf" "install -m 0440"
  check "the whole config checked after it went in" eq "$(count_x "visudo -c")" 2
  check "the grant verified as the service user, with the core's probe" \
    called "sudo -n -u tester -- sudo -n -l $GRANT_CMD"
  check "the grant goes in before the unit" before "install -m 0440" "/.domovoi-update.service."
  check "the grant verified before the unit goes in" before "sudo -n -l" "/.domovoi-update.service."
  check "daemon-reload after the unit" before "/.domovoi-update.service." "systemctl daemon-reload"
  check "the state dir's path checked for root-only" called "stat -c %u %a $ROOT"
  check "systemd loaded it" test -f "$STATE/loaded-update-unit"
  check "docker compose checked as the service user" \
    called "sudo -n -u tester -- docker compose -f $REPO/domovoi/docker-compose.yml ps --quiet"
  check "piper checked as the service user" called "sudo -n -u tester -- $VENV/bin/python -m pip"
  check "venv ownership checked" called "find $VENV ! -user tester -print -quit"
  check "no update started without --apply" not_called "systemctl start"
  check "no ownership change without --fix-ownership" not_called "chown"
  check "says the checkout is ahead of the running code" said "is ahead of the running code (${SHA_A:0:12})"
  check "says how to apply it" said "sudo systemctl start domovoi-update.service"
  check "never prints the core's answer" not_said SENTINEL
  # The one warning a clean box gets: signing is not set up (see the
  # signed-updates cases below).
  check "one warning, the signing one" eq "$(grep -c warning "$CASE/output.log")" 1
  check "which says so" said "signed updates are not enforced: no $ROOT/etc/domovoi/allowed_signers"
  check "and points at the doc" said "docs/LINUX_HOST.md, Signed updates"
  end_case
}

case_idempotent_rerun() {
  new_case idempotent_rerun
  run_install
  check "first run exit 0" eq "$RC" 0
  local sig; sig=$(tree_sig)
  : >"$STATE/calls.log"
  run_install
  check "second run exit 0" eq "$RC" 0
  check "says nothing changed" said "Nothing to change"
  check "every file as it was" eq "$(tree_sig)" "$sig"
  check "nothing installed" none_start "install "
  check "no daemon-reload" not_called "daemon-reload"
  check "the core isn't asked again" not_called "curl"
  check "the baseline is kept" file_is "$UPD/applied_sha" "$SHA_A"
  check "the grant is still verified" called "sudo -n -l $GRANT_CMD"
  check "no backups" eq "$(ls "$ROOT/etc/systemd/system" "$ROOT/etc/sudoers.d" | grep -c 'bak')" 0
  end_case
}

case_existing_files_replaced_and_kept() {
  new_case existing_files_replaced_and_kept
  printf '[Service]\nExecStart=/bin/true\n' >"$UNIT"
  cp "$UNIT" "$STATE/loaded-update-unit"
  printf 'tester ALL=(root) NOPASSWD: /bin/true\n' >"$SUDOERS"
  run_install
  check "exit 0" eq "$RC" 0
  check "unit replaced by the doc's" eq "$(cat "$UNIT")" "$(expected_unit)"
  check "old unit kept beside it" eq "$(cat "$UNIT".bak-*)" "$(printf '[Service]\nExecStart=/bin/true')"
  check "grant replaced" eq "$(cat "$SUDOERS")" "$RULE"
  check "old grant kept beside it" eq "$(cat "$SUDOERS".bak-*)" "tester ALL=(root) NOPASSWD: /bin/true"
  check "the kept grant's name has a dot, so sudo skips it" eq "$(ls "$ROOT/etc/sudoers.d" | grep -c '^domovoi-update\.bak-')" 1
  check "systemd re-read the unit" called "systemctl daemon-reload"
  check "and has the new one" eq "$(cat "$STATE/loaded-update-unit")" "$(expected_unit)"
  end_case
}

case_unit_in_place_but_not_loaded() {
  new_case unit_in_place_but_not_loaded
  mkdir -p "$UPD" && echo "$SHA_A" >"$UPD/applied_sha"
  expected_unit >"$UNIT"
  echo "$RULE" >"$SUDOERS"
  run_install
  check "exit 0" eq "$RC" 0
  check "nothing installed" none_start "install "
  check "but systemd is told to read it" called "systemctl daemon-reload"
  check "which is a change" said "Done:"
  end_case
}

case_candidate_rule_rejected() {
  new_case candidate_rule_rejected
  : >"$STATE/visudo-reject"
  run_install
  check "exit 1" eq "$RC" 1
  check "says visudo rejected the rule" said "visudo rejects the rule"
  check "nothing installed, not even the baseline" nothing_installed
  check "says nothing changed" said "Nothing was changed."
  end_case
}

case_sudoers_already_broken() {
  new_case sudoers_already_broken
  : >"$STATE/visudo-broken"
  run_install
  check "exit 1" eq "$RC" 1
  check "says sudoers is already broken" said "sudoers already fails visudo -c"
  check "names where" said "/etc/sudoers.d/other: syntax error"
  check "nothing installed" nothing_installed
  end_case
}

case_full_check_fails_puts_old_grant_back() {
  new_case full_check_fails_puts_old_grant_back
  printf 'tester ALL=(root) NOPASSWD: /bin/true\n' >"$SUDOERS"
  : >"$STATE/visudo-fail-with-rule"
  run_install
  check "exit 1" eq "$RC" 1
  check "the previous grant is back" eq "$(cat "$SUDOERS")" "tester ALL=(root) NOPASSWD: /bin/true"
  check "says so" said "undone: put the previous $SUDOERS back"
  check "checked before, after, and after the undo" eq "$(count_x "visudo -c")" 3
  check "the unit is not installed" absent "$UNIT"
  check "no daemon-reload" not_called "daemon-reload"
  check "says what it had changed before" said "Changed before the stop:"
  check "the baseline stays recorded" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_full_check_fails_removes_new_grant() {
  new_case full_check_fails_removes_new_grant
  : >"$STATE/visudo-fail-with-rule"
  run_install
  check "exit 1" eq "$RC" 1
  check "the new grant is gone" absent "$SUDOERS"
  check "no dot-file left" eq "$(ls -A "$ROOT/etc/sudoers.d" | wc -l | tr -d ' ')" 0
  check "says so" said "undone: removed the new $SUDOERS"
  check "names visudo's complaint" said "duplicate Defaults near line 1"
  check "the unit is not installed" absent "$UNIT"
  end_case
}

case_grant_not_effective() {
  new_case grant_not_effective
  : >"$STATE/sudo-deny"
  run_install
  check "exit 1" eq "$RC" 1
  check "says sudo refuses" said "sudo doesn't let tester run: $GRANT_CMD"
  check "the unit is not installed, so the button keeps its old job" absent "$UNIT"
  check "says so" said "keeps doing what it did before"
  check "the useless grant is taken back" absent "$SUDOERS"
  check "and says so" said "undone: removed the new $SUDOERS"
  check "sudoers checked again after that" eq "$(count_x "visudo -c")" 3
  end_case
}

case_core_unreachable_stops() {
  new_case core_unreachable_stops
  : >"$STATE/core-down"
  run_install
  check "exit 1" eq "$RC" 1
  check "says the core isn't answering" said "the core isn't answering at http://127.0.0.1:6370/v1/admin/version"
  check "says why HEAD won't do" said "a pull may have moved it past the running code"
  check "nothing installed, and HEAD not recorded" nothing_installed
  check "says nothing changed" said "Nothing was changed."
  end_case
}

case_core_url_option() {
  new_case core_url_option
  run_install --core-url http://127.0.0.1:6399/
  check "exit 0" eq "$RC" 0
  check "asks the given core" called "curl http://127.0.0.1:6399/v1/admin/version"
  end_case
}

case_core_does_not_know() {
  new_case core_does_not_know
  printf '{"sha":"unknown","running_sha":"unknown","checkout_sha":"%s"}\n' "${SHA_B:0:7}" >"$STATE/version.json"
  run_install
  check "exit 1" eq "$RC" 1
  check "says the core can't say" said "doesn't say which commit it runs (running_sha: unknown)"
  check "nothing installed" nothing_installed
  end_case
}

case_sha_not_in_repo_stops() {
  new_case sha_not_in_repo_stops
  printf '{"running_sha":"eeeeeeeeeeee","checkout_sha":"%s"}\n' "${SHA_B:0:7}" >"$STATE/version.json"
  run_install
  check "exit 1" eq "$RC" 1
  check "says it isn't a commit here" said "the core runs eeeeeeeeeeee, which isn't a commit in $REPO"
  check "says how to fetch it" said "sudo -u tester git -C $REPO fetch"
  check "nothing installed" nothing_installed
  end_case
}

case_existing_baseline_kept() {
  new_case existing_baseline_kept
  mkdir -p "$UPD" && echo "$SHA_B" >"$UPD/applied_sha"
  : >"$STATE/core-down"   # not needed when the baseline is recorded
  run_install
  check "exit 0" eq "$RC" 0
  check "the core isn't asked" not_called "curl"
  check "the baseline is untouched" file_is "$UPD/applied_sha" "$SHA_B"
  check "says it was already recorded" said "rollback baseline already recorded: ${SHA_B:0:12}"
  check "nothing waiting" said "Nothing is waiting"
  end_case
}

case_garbage_baseline_stops() {
  new_case garbage_baseline_stops
  mkdir -p "$UPD" && printf 'not-a-sha; rm -rf /\n' >"$UPD/applied_sha"
  run_install
  check "exit 1" eq "$RC" 1
  check "says it holds no commit" said "doesn't hold a commit of $REPO"
  check "never echoes the file" not_said "rm -rf"
  check "file untouched" eq "$(cat "$UPD/applied_sha")" 'not-a-sha; rm -rf /'
  check "no unit, no grant" eq "$(absent "$UNIT" && absent "$SUDOERS" && echo none)" none
  end_case
}

case_dry_run_changes_nothing() {
  new_case dry_run_changes_nothing
  echo "$VENV/lib/site-packages/numpy/__init__.py" >"$STATE/foreign"
  local sig; sig=$(tree_sig)
  run_install --dry-run --apply --fix-ownership
  check "exit 0" eq "$RC" 0
  check "every file as it was" eq "$(tree_sig)" "$sig"
  check "nothing installed" nothing_installed
  check "no update started" not_called "systemctl start"
  check "the rule is still checked" called "visudo -cf"
  check "says it would record the running SHA" said "record $SHA_A in $UPD/applied_sha"
  check "says it would install the grant" said "would    install $SUDOERS"
  check "says it would install the unit" said "would    install $UNIT"
  check "says it would chown" said "would    chown -R tester: $VENV"
  check "says it would reload" said "would    systemctl daemon-reload"
  check "says it would start the update" said "it would then run: systemctl start domovoi-update.service"
  check "says nothing was changed" said "Dry run: nothing was changed."
  end_case
}

case_not_root_refuses() {
  new_case not_root_refuses
  echo 1000 >"$STATE/uid"
  run_install --apply
  check "exit 1" eq "$RC" 1
  check "says to use sudo, with the same options" said "sudo bash $SCRIPT --apply"
  check "calls nothing" eq "$(wc -c <"$STATE/calls.log" | tr -d ' ')" 0
  check "nothing installed" nothing_installed
  end_case
}

case_help_needs_no_root() {
  new_case help_needs_no_root
  echo 1000 >"$STATE/uid"
  run_install --help
  check "exit 0" eq "$RC" 0
  check "prints usage" said "Usage: sudo bash scripts/linux/install-update-unit.sh"
  run_install --bogus
  check "unknown option exits 2" eq "$RC" 2
  end_case
}

case_venv_owned_by_root_warns() {
  new_case venv_owned_by_root_warns
  echo "$VENV/lib/site-packages/numpy/__init__.py" >"$STATE/foreign"
  run_install
  check "exit 0: a warning, not a stop" eq "$RC" 0
  check "warns" said "warning  $VENV isn't all tester's (first found: $VENV/lib/site-packages/numpy/__init__.py)"
  check "gives the exact fix" said "sudo chown -R tester: $VENV"
  check "and the option" said "--fix-ownership"
  check "changes no ownership by itself" not_called "chown"
  check "still installs the unit" eq "$(cat "$UNIT")" "$(expected_unit)"
  end_case
}

case_fix_ownership() {
  new_case fix_ownership
  echo "$VENV/lib/site-packages/numpy/__init__.py" >"$STATE/foreign"
  run_install --fix-ownership
  check "exit 0" eq "$RC" 0
  check "hands the venv over" called "chown -R tester: $VENV"
  check "last, once the unit is in: a chown can't be taken back" before "systemctl daemon-reload" "chown -R"
  # The venv's warning is gone. Not every warning: a box without the
  # allowed-signers file still gets the signing one, and should.
  check "no ownership warning left" not_said "warning  $VENV"
  check "the fix line instead" said "fix      $VENV isn't all tester's"
  end_case
}

case_piper_too_old_warns() {
  new_case piper_too_old_warns
  echo 1.2.0 >"$STATE/piper-version"
  run_install
  check "exit 0" eq "$RC" 0
  check "warns" said "piper-tts 1.2.0 in $VENV is older than 1.3"
  check "gives the fix" said "sudo -u tester $VENV/bin/python -m pip install 'piper-tts>=1.3'"
  end_case
}

case_piper_missing_warns() {
  new_case piper_missing_warns
  : >"$STATE/piper-missing"
  run_install
  check "exit 0" eq "$RC" 0
  check "warns" said "piper-tts isn't installed in $VENV"
  end_case
}

case_piper_newer_is_fine() {
  new_case piper_newer_is_fine
  echo 1.10.2 >"$STATE/piper-version"
  run_install
  check "exit 0" eq "$RC" 0
  check "1.10 counts as newer than 1.3" said "ok       piper-tts 1.10.2"
  end_case
}

case_docker_denied_stops() {
  new_case docker_denied_stops
  : >"$STATE/docker-denied"
  run_install
  check "exit 1" eq "$RC" 1
  check "says compose fails for the user" said "docker compose doesn't work for tester: permission denied"
  check "gives the usual fix" said "sudo usermod -aG docker tester"
  check "nothing installed" nothing_installed
  end_case
}

case_missing_unit_stops() {
  new_case missing_unit_stops
  echo domovoi-web.service >"$STATE/missing-units"
  run_install
  check "exit 1" eq "$RC" 1
  check "names it" said "not installed: domovoi-web.service (not-found)"
  check "nothing installed" nothing_installed
  end_case
}

case_no_systemd_stops() {
  new_case no_systemd_stops
  rm -rf "$ROOT/run/systemd"
  run_install
  check "exit 1" eq "$RC" 1
  check "says systemd isn't running" said "systemd isn't running as the init system"
  check "nothing installed" nothing_installed
  end_case
}

case_masked_unit_stops() {
  new_case masked_unit_stops
  : >"$UNIT"   # an empty unit file is how systemd reads a mask, too
  run_install
  check "exit 1" eq "$RC" 1
  check "says it is masked" said "is masked"
  check "says how to unmask" said "sudo systemctl unmask domovoi-update.service"
  check "the mask is untouched" test ! -s "$UNIT"
  check "nothing else installed" eq "$(absent "$SUDOERS" && absent "$UPD/applied_sha" && echo none)" none
  end_case
}

case_user_mismatch_stops() {
  new_case user_mismatch_stops
  echo someone >"$STATE/core-user"
  run_install
  check "exit 1" eq "$RC" 1
  check "says who the core runs as" said "domovoi-core.service runs as someone, but the service user here is tester (the owner of $REPO)"
  check "suggests --user" said "pass --user someone"
  check "nothing installed" nothing_installed
  end_case
}

case_root_owned_checkout_needs_user() {
  new_case root_owned_checkout_needs_user
  echo root >"$STATE/owner"
  run_install
  check "exit 1" eq "$RC" 1
  check "says the default user is root" said "root (the owner of $REPO) is root"
  run_install --user tester
  check "--user tester goes ahead" eq "$RC" 0
  check "grant for tester" eq "$(cat "$SUDOERS")" "$RULE"
  end_case
}

case_other_repo_stops() {
  new_case other_repo_stops
  mkdir -p "$CASE/elsewhere"
  run_install --repo "$CASE/elsewhere"
  check "exit 1" eq "$RC" 1
  check "says where the core runs from" said "domovoi-core.service runs from $REPO, not $CASE/elsewhere"
  check "nothing installed" nothing_installed
  end_case
}

case_defaults_file_user_must_agree() {
  new_case defaults_file_user_must_agree
  mkdir -p "$ROOT/etc/default"
  printf '# local settings\nDOMOVOI_USER="someone"\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 1" eq "$RC" 1
  check "names the setting" said "sets DOMOVOI_USER=someone, but the core runs as tester"
  check "nothing installed" nothing_installed
  end_case
}

case_defaults_file_update_dir() {
  new_case defaults_file_update_dir
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPDATE_DIR=/srv/domovoi-update\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 0" eq "$RC" 0
  check "records the baseline where the update will look" file_is "$ROOT/srv/domovoi-update/applied_sha" "$SHA_A"
  check "not in the default dir" absent "$UPD/applied_sha"
  end_case
}

case_apply_runs_the_update() {
  new_case apply_runs_the_update
  run_install --apply
  check "exit 0" eq "$RC" 0
  check "starts the unit" called "systemctl start domovoi-update.service"
  check "after systemd loaded it" before "systemctl daemon-reload" "systemctl start domovoi-update.service"
  check "reports the result" said "result   ok (mode update, ${SHA_A:0:12} -> ${SHA_B:0:12})"
  end_case
}

case_apply_reports_a_rollback() {
  new_case apply_reports_a_rollback
  echo rolled_back >"$STATE/apply-status"
  run_install --apply
  check "exit 1" eq "$RC" 1
  check "reports the status" said "result   rolled_back"
  check "and the error" said "health failed (exit 1)"
  check "points at the journal" said "journalctl -u domovoi-update -n 200"
  check "the unit stays installed" test -f "$UNIT"
  end_case
}

case_install_root_needs_the_harness_flag() {
  new_case install_root_needs_the_harness_flag
  run_env DOMOVOI_INSTALL_ROOT="$ROOT" -- --dry-run
  check "the root alone: refused" eq "$RC" 2
  check "says why" said "for its test harness only, and only together"
  run_env DOMOVOI_INSTALL_TEST_HARNESS=1 DOMOVOI_INSTALL_ROOT=relative/root -- --dry-run
  check "a relative root: refused" eq "$RC" 2
  run_env DOMOVOI_INSTALL_TEST_HARNESS=yes DOMOVOI_INSTALL_ROOT="$ROOT" -- --dry-run
  check "any flag value but 1: refused" eq "$RC" 2
  check "calls nothing" eq "$(wc -c <"$STATE/calls.log" | tr -d ' ')" 0
  check "nothing installed" nothing_installed
  end_case
}

case_state_dir_writable_by_others_stops() {
  new_case state_dir_writable_by_others_stops
  mkdir -p "$UPD"
  echo "$UPD" >"$STATE/insecure-paths"
  run_install
  check "exit 1" eq "$RC" 1
  check "names it" said "$UPD can be written by a user other than root"
  check "gives the fix" said "sudo chown root:root $UPD && sudo chmod go-w $UPD"
  check "the core isn't asked" not_called "curl"
  check "nothing installed" nothing_installed
  end_case
}

case_state_dir_parent_writable_by_others_stops() {
  new_case state_dir_parent_writable_by_others_stops
  mkdir -p "$ROOT/var/lib"
  echo "$ROOT/var/lib" >"$STATE/insecure-paths"
  run_install
  check "exit 1: judged by the nearest directory that exists" eq "$RC" 1
  check "names that directory" said "$ROOT/var/lib can be written by a user other than root"
  check "nothing installed" nothing_installed
  end_case
}

case_defaults_file_writable_by_others_stops() {
  new_case defaults_file_writable_by_others_stops
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPDATE_KEEP_BACKUPS=5\n' >"$ROOT/etc/default/domovoi-update"
  echo "$ROOT/etc/default/domovoi-update" >"$STATE/insecure-paths"
  run_install
  check "exit 1" eq "$RC" 1
  check "names it" said "$ROOT/etc/default/domovoi-update can be written by a user other than root"
  check "nothing installed" nothing_installed
  end_case
}

case_relative_state_dir_stops() {
  new_case relative_state_dir_stops
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPDATE_DIR=var/lib/domovoi-update\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 1" eq "$RC" 1
  check "says it must be absolute" said "DOMOVOI_UPDATE_DIR in $ROOT/etc/default/domovoi-update must be an absolute path"
  check "nothing installed" nothing_installed
  end_case
}

case_core_url_must_be_this_box() {
  new_case core_url_must_be_this_box
  run_install --core-url http://192.168.1.20:6370
  check "a LAN address: refused" eq "$RC" 2
  check "says it must be this box" said "--core-url must be the core on this box"
  run_install --core-url http://admin:hunter2@127.0.0.1:6370
  check "credentials in the URL: refused" eq "$RC" 2
  check "and never printed" not_said hunter2
  run_install --core-url=file:///etc/shadow
  check "another scheme: refused" eq "$RC" 2
  check "the core is never asked" not_called "curl"
  check "nothing installed" nothing_installed
  end_case
}

case_curl_is_hardened() {
  new_case curl_is_hardened
  run_install
  check "exit 0" eq "$RC" 0
  check "no .curlrc, no proxy, http(s) only" \
    called "curl-args -q -fsS --noproxy * --proto =http,https --max-time 10 --max-filesize 1048576 http://127.0.0.1:6370/v1/admin/version"
  end_case
}

# CORE-21: the version read is on the device tier, so the installer brings
# the household token the core mirrors into the service user's home.
case_version_read_brings_the_household_token() {
  new_case version_read_brings_the_household_token
  local home=$CASE/home/tester tok="acorn maple-River 7"
  mkdir -p "$home/.domovoi"
  printf '%s\r\n' "$tok" >"$home/.domovoi/device-token.txt"   # a CRLF file, as Windows writes it
  echo "$home" >"$STATE/home"
  printf '%s' "$tok" >"$STATE/core-wants-token"
  run_install
  check "exit 0" eq "$RC" 0
  check "reads it as the service user, not as root" \
    called "sudo -n -u tester -- cat -- $home/.domovoi/device-token.txt"
  check "sends it as the header, on stdin" grep -qxF -- "X-Device-Token: $tok" "$STATE/curl-stdin"
  check "the same hardened curl" \
    called "curl-args -q -fsS --noproxy * --proto =http,https --max-time 10 --max-filesize 1048576 -H @- http://127.0.0.1:6370/v1/admin/version"
  check "never on a command line" not_called "$tok"
  check "never printed" not_said "$tok"
  check "records the running SHA" file_is "$UPD/applied_sha" "$SHA_A"
  end_case
}

case_version_read_refused_without_the_token_stops() {
  new_case version_read_refused_without_the_token_stops
  printf 'acorn-maple-river' >"$STATE/core-wants-token"   # and no home, so no token file
  run_install
  check "exit 1" eq "$RC" 1
  check "says the core refused the read" said "refused the version read (curl exit 22)"
  check "says where the token comes from" said "~tester/.domovoi/device-token.txt"
  check "nothing installed, and HEAD not recorded" nothing_installed
  end_case
}

case_ref_named_like_the_sha_stops() {
  new_case ref_named_like_the_sha_stops
  g branch -q "${SHA_A:0:7}" "$SHA_B"   # a branch spelled like the running SHA
  run_install
  check "exit 1" eq "$RC" 1
  check "says the name resolves elsewhere" said "${SHA_A:0:7} names ${SHA_B:0:12} in $REPO, a branch or tag of that name"
  check "the wrong commit isn't recorded" nothing_installed
  end_case
}

case_unit_write_fails_takes_the_grant_back() {
  new_case unit_write_fails_takes_the_grant_back
  : >"$STATE/install-fail-unit"
  run_install
  check "exit 1" eq "$RC" 1
  check "says the unit couldn't be written" said "couldn't write $UNIT (it is as it was)"
  check "no unit" absent "$UNIT"
  check "the grant is taken back" absent "$SUDOERS"
  check "and says so" said "undone: removed the new $SUDOERS"
  check "no temp files left" eq "$(ls -A "$ROOT/etc/sudoers.d" "$ROOT/etc/systemd/system" | grep -c '^\.')" 0
  check "the baseline stays" file_is "$UPD/applied_sha" "$SHA_A"
  check "and is the one change reported" said "Changed before the stop:"
  end_case
}

case_daemon_reload_fails_takes_everything_back() {
  new_case daemon_reload_fails_takes_everything_back
  : >"$STATE/reload-fail"
  run_install
  check "exit 1" eq "$RC" 1
  check "says the reload failed" said "systemctl daemon-reload failed: Failed to reload daemon"
  check "the unit is taken back" absent "$UNIT"
  check "so is the grant" absent "$SUDOERS"
  check "and systemd reloaded without the unit" eq "$(count_x "systemctl daemon-reload")" 2
  check "says both" said "undone: removed the new $UNIT"
  end_case
}

case_unit_not_loaded_puts_the_old_one_back() {
  new_case unit_not_loaded_puts_the_old_one_back
  printf '[Service]\nExecStart=/bin/true\n' >"$UNIT"
  cp "$UNIT" "$STATE/loaded-update-unit"
  : >"$STATE/update-unit-unloadable"
  run_install
  check "exit 1" eq "$RC" 1
  check "says systemd doesn't load it" said "systemd doesn't load domovoi-update.service (LoadState: bad-setting)"
  check "the previous unit is back" eq "$(cat "$UNIT")" "$(printf '[Service]\nExecStart=/bin/true')"
  check "says so" said "undone: put the previous $UNIT back"
  check "the new grant is gone" absent "$SUDOERS"
  end_case
}

case_unexpected_failure_takes_back() {
  new_case unexpected_failure_takes_back
  printf '[Service]\nExecStart=/bin/true\n' >"$UNIT"
  cp "$UNIT" "$STATE/loaded-update-unit"
  : >"$STATE/backup-fail"   # keeping a copy of the old unit fails, under set -e
  run_install
  check "exits non-zero" test "$RC" -ne 0
  check "says it stopped" said "stopped before it finished"
  check "the grant it had installed is taken back" absent "$SUDOERS"
  check "and says so" said "undone: removed the new $SUDOERS"
  check "the old unit is untouched" eq "$(cat "$UNIT")" "$(printf '[Service]\nExecStart=/bin/true')"
  end_case
}

case_fix_ownership_only_for_a_venv() {
  new_case fix_ownership_only_for_a_venv
  mkdir -p "$ROOT/etc/default" "$CASE/opt/bin"
  cp "$VENV/bin/python" "$CASE/opt/bin/python"   # an interpreter, but no pyvenv.cfg
  printf 'DOMOVOI_VENV=%s\n' "$CASE/opt" >"$ROOT/etc/default/domovoi-update"
  echo "$CASE/opt/something" >"$STATE/foreign"
  run_install --fix-ownership
  check "exit 0: a warning" eq "$RC" 0
  check "won't chown -R a directory that isn't a venv" not_called "chown"
  check "says why" said "doesn't look like a venv"
  check "still gives the manual fix" said "sudo chown -R tester: $CASE/opt"
  end_case
}

# ─── signed updates: the pre-flight check (2026-10 audit, A8-01) ──────────

# Does this box's ssh-keygen sign and verify (OpenSSH 8.0+)? Only the case
# with a real signature needs it; without it that case is skipped.
have_ssh_signing() { local out; out=$(ssh-keygen -Y 2>&1); [[ $out == *"requires an argument"* ]]; }

SIGNERS_FILE=""   # where the installer looks under the fake root

# An ed25519 key in $1, listed in the allowed-signers file under the fake
# root where the installer looks.
write_signing() {
  SIGNERS_FILE=$ROOT/etc/domovoi/allowed_signers
  mkdir -p "$1" "$ROOT/etc/domovoi"
  ssh-keygen -q -t ed25519 -N '' -C owner -f "$1/owner" >/dev/null
  printf 'owner@example.invalid namespaces="git" %s\n' "$(cut -d' ' -f1,2 "$1/owner.pub")" >"$SIGNERS_FILE"
}

# A signers file that lists some key, for the cases where HEAD is unsigned
# and the key is never consulted.
write_some_signers() {
  SIGNERS_FILE=$ROOT/etc/domovoi/allowed_signers
  mkdir -p "$ROOT/etc/domovoi"
  printf 'owner namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n' >"$SIGNERS_FILE"
}

# Re-make commit B SSH-signed by private key $1 (the case starts with it
# unsigned), and let the core's answer name the new B.
resign_head() {
  g -c gpg.format=ssh -c user.signingkey="$1" commit -q --amend -S --no-edit
  SHA_B=$(g rev-parse HEAD)
  printf '{"sha":"%s-dirty","running_sha":"%s-dirty","checkout_sha":"%s","restart_required":true,"note":"SENTINEL-BODY-NOT-PRINTED"}\n' \
    "${SHA_A:0:7}" "${SHA_A:0:7}" "${SHA_B:0:7}" >"$STATE/version.json"
}

case_signing_enforced_and_head_verifies() {
  new_case signing_enforced_and_head_verifies
  if ! have_ssh_signing; then skip_case "no ssh-keygen -Y on this box"; return; fi
  write_signing "$CASE/keys"
  resign_head "$CASE/keys/owner"
  run_install
  check "exit 0" eq "$RC" 0
  check "says who signed HEAD" said "signed updates enforced: HEAD ${SHA_B:0:12} is signed by owner@example.invalid ($SIGNERS_FILE)"
  check "no signing warning" not_said "signed updates are not enforced"
  check "verified as root, against that file, with the programs pinned" \
    called "git -c safe.directory=$REPO -c gpg.ssh.allowedSignersFile=$SIGNERS_FILE -c gpg.ssh.program=ssh-keygen -c gpg.program=gpg"
  check "verify-commit on HEAD" called "verify-commit HEAD"
  check "not as the service user" not_called "sudo -n -u tester -- git -c safe.directory"
  check "the file judged root-only first" before "stat -c %u %a $SIGNERS_FILE" "verify-commit HEAD"
  check "installed as usual" eq "$(cat "$UNIT")" "$(expected_unit)"
  end_case
}

case_signing_enforced_but_head_unsigned_stops() {
  new_case signing_enforced_but_head_unsigned_stops
  write_some_signers
  run_install
  check "exit 1" eq "$RC" 1
  check "says why" said "signed updates are enforced by $SIGNERS_FILE, but HEAD ${SHA_B:0:12} does not verify: it is not signed"
  check "and what it would mean" said "The update unit would refuse every run"
  check "and the way out" said "or remove the file to turn enforcement off"
  check "the core was asked first (the baseline is still worth having)" called "curl http://127.0.0.1:6370/v1/admin/version"
  check "nothing installed" nothing_installed
  end_case
}

case_signers_file_writable_by_others_stops() {
  new_case signers_file_writable_by_others_stops
  write_some_signers
  echo "$SIGNERS_FILE" >"$STATE/insecure-paths"
  run_install
  check "exit 1" eq "$RC" 1
  check "names it" said "$SIGNERS_FILE can be written by a user other than root"
  check "says what root keeps there" said "verifies every HEAD against the keys in $SIGNERS_FILE"
  check "never verified against it" not_called "verify-commit"
  check "nothing installed" nothing_installed
  end_case
}

case_signers_file_symlink_stops() {
  new_case signers_file_symlink_stops
  mkdir -p "$ROOT/etc/domovoi"
  printf 'owner namespaces="git" ssh-ed25519 AAAA\n' >"$CASE/elsewhere"
  # MSYS's ln -s copies unless symlinks are enabled: only a real link counts.
  ln -s "$CASE/elsewhere" "$ROOT/etc/domovoi/allowed_signers" 2>/dev/null
  if [ ! -L "$ROOT/etc/domovoi/allowed_signers" ]; then skip_case "cannot make a symlink here"; return; fi
  run_install
  check "exit 1" eq "$RC" 1
  check "says so" said "$ROOT/etc/domovoi/allowed_signers is a symlink, and the update unit refuses it"
  check "nothing installed" nothing_installed
  end_case
}

case_signers_path_comes_from_the_defaults_file() {
  new_case signers_path_comes_from_the_defaults_file
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_ALLOWED_SIGNERS=/etc/domovoi/keys/allowed\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 0" eq "$RC" 0
  check "looked where the unit will" said "signed updates are not enforced: no $ROOT/etc/domovoi/keys/allowed"
  end_case
}

case_upstream_pin_mismatch_stops() {
  new_case upstream_pin_mismatch_stops
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPSTREAM_URL=https://github.com/coders-farm-official/domovoi\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 1" eq "$RC" 1
  check "says why" said "pins the upstream to https://github.com/coders-farm-official/domovoi, but the checkout's origin is not set"
  check "asked the service user's git" called "sudo -n -u tester -- git -C $REPO remote get-url origin"
  check "gives the fix" said "git -C $REPO remote set-url origin https://github.com/coders-farm-official/domovoi"
  check "nothing installed" nothing_installed
  end_case
}

case_upstream_branch_pin_mismatch_stops() {
  new_case upstream_branch_pin_mismatch_stops
  mkdir -p "$ROOT/etc/default"
  printf 'DOMOVOI_UPSTREAM_BRANCH=main\n' >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 1: no tracking branch at all" eq "$RC" 1
  check "says why" said "pins the branch to origin/main, but HEAD tracks no upstream branch"
  check "nothing installed" nothing_installed
  end_case
}

case_upstream_pin_that_matches_is_reported() {
  new_case upstream_pin_that_matches_is_reported
  local bare=$CASE/upstream.git url
  git init -q --bare -b main "$bare"
  g remote add origin "$bare"
  g push -q origin main 2>/dev/null
  g branch -q --set-upstream-to=origin/main main
  url=$(g remote get-url origin)
  mkdir -p "$ROOT/etc/default"
  # A trailing slash on the pin is the same remote.
  printf 'DOMOVOI_UPSTREAM_URL=%s/\nDOMOVOI_UPSTREAM_BRANCH=main\n' "$url" >"$ROOT/etc/default/domovoi-update"
  run_install
  check "exit 0" eq "$RC" 0
  check "reports the URL pin" said "upstream pinned: origin is $url/"
  check "reports the branch pin" said "upstream pinned: HEAD tracks origin/main"
  check "installed as usual" eq "$(cat "$UNIT")" "$(expected_unit)"
  end_case
}

CASES=(
  case_fresh_install
  case_idempotent_rerun
  case_existing_files_replaced_and_kept
  case_unit_in_place_but_not_loaded
  case_candidate_rule_rejected
  case_sudoers_already_broken
  case_full_check_fails_puts_old_grant_back
  case_full_check_fails_removes_new_grant
  case_grant_not_effective
  case_core_unreachable_stops
  case_core_url_option
  case_core_does_not_know
  case_sha_not_in_repo_stops
  case_existing_baseline_kept
  case_garbage_baseline_stops
  case_dry_run_changes_nothing
  case_not_root_refuses
  case_help_needs_no_root
  case_venv_owned_by_root_warns
  case_fix_ownership
  case_piper_too_old_warns
  case_piper_missing_warns
  case_piper_newer_is_fine
  case_docker_denied_stops
  case_missing_unit_stops
  case_no_systemd_stops
  case_masked_unit_stops
  case_user_mismatch_stops
  case_root_owned_checkout_needs_user
  case_other_repo_stops
  case_defaults_file_user_must_agree
  case_defaults_file_update_dir
  case_apply_runs_the_update
  case_apply_reports_a_rollback
  case_install_root_needs_the_harness_flag
  case_state_dir_writable_by_others_stops
  case_state_dir_parent_writable_by_others_stops
  case_defaults_file_writable_by_others_stops
  case_relative_state_dir_stops
  case_core_url_must_be_this_box
  case_curl_is_hardened
  case_version_read_brings_the_household_token
  case_version_read_refused_without_the_token_stops
  case_ref_named_like_the_sha_stops
  case_unit_write_fails_takes_the_grant_back
  case_daemon_reload_fails_takes_everything_back
  case_unit_not_loaded_puts_the_old_one_back
  case_unexpected_failure_takes_back
  case_fix_ownership_only_for_a_venv
  case_signing_enforced_and_head_verifies
  case_signing_enforced_but_head_unsigned_stops
  case_signers_file_writable_by_others_stops
  case_signers_file_symlink_stops
  case_signers_path_comes_from_the_defaults_file
  case_upstream_pin_mismatch_stops
  case_upstream_branch_pin_mismatch_stops
  case_upstream_pin_that_matches_is_reported
)

# ONLY=<regex> runs the cases whose names match it (a quick look while
# working on one); the suite runs them all.
for c in "${CASES[@]}"; do
  if [ -n "${ONLY:-}" ] && ! [[ $c =~ ${ONLY} ]]; then continue; fi
  "$c"
done

echo "install-update-unit harness: $PASSED passed, $FAILED failed, $SKIPPED skipped"
[ "$FAILED" -eq 0 ]
