#!/bin/bash
# user-mode-install.sh: the helper's whole user-mode install on a real Mac,
# then both uninstalls, ending with an empty prefix (mac-installer Phase 2).
# Run as a standard user, on Apple Silicon, from the built package:
#
#   cd mac && swift build
#   mac/tests/user-mode-install.sh PREFIX WORK
#
# PREFIX  an empty folder for the install (keep it short: see Layout.limaHome)
# WORK    a folder for this script's own files (seed links, logs, results)
#
# Environment:
#   PROVISION    provision.sh to run in the VM (required)
#   BUNDLE       a git bundle whose refs/heads/main is the commit to install (required)
#   SEED_DIR     a folder holding already-downloaded pinned files, named as in
#                pins.conf's URLs (optional: else they are downloaded)
#   SEED_MODELS  an Ollama models folder to clone in (optional)
#   COMMIT       the commit the Mac side was built from, for side.json (optional)
#   WEB_PORT CORE_PORT OLLAMA_PORT   default 6390 6391 11500: never a real
#                Domovoi's 6369/6370/11434 (the helper refuses those)
#
# It starts a VM and an Ollama: on a shared Mac, hold its busy lock first.
# It only ever stops what the helper started (the pids it recorded), and
# its last step is the helper's own "remove everything".
set -u
PREFIX=${1:?PREFIX}; WORK=${2:?WORK}
: "${PROVISION:?set PROVISION}" "${BUNDLE:?set BUNDLE}"
WEB_PORT=${WEB_PORT:-6390}; CORE_PORT=${CORE_PORT:-6391}; OLLAMA_PORT=${OLLAMA_PORT:-11500}
MAC=$(cd "$(dirname "$0")/.." && pwd)
H=$MAC/.build/debug/DomovoiHelper
U=$MAC/.build/debug/domovoi-uninstall
[ -x "$H" ] && [ -x "$U" ] || { echo "build first: cd $MAC && swift build" >&2; exit 2; }
mkdir -p "$PREFIX" "$WORK"
[ -z "$(ls -A "$PREFIX")" ] || { echo "PREFIX $PREFIX is not empty" >&2; exit 2; }
R=$WORK/results.kv
: >"$R"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
result() { printf '%s\t%s\t%s\n' "$(ts)" "$1" "$2" | tee -a "$R"; }
fail=0
check() { if [ "$2" = "$3" ]; then result "$1" "ok ($2)"; else result "$1" "FAIL: got '$2', want '$3'"; fail=1; fi; }

common=(--user-mode --prefix "$PREFIX")
args=("${common[@]}" --web-port "$WEB_PORT" --core-port "$CORE_PORT" --ollama-port "$OLLAMA_PORT"
  --internet always --tier floor --media Music --no-vision-model
  --provision-script "$PROVISION" --repo-bundle "$BUNDLE" --ref main --track-branch main)
if [ -n "${COMMIT:-}" ]; then args+=(--commit "$COMMIT"); fi
if [ -n "${SEED_DIR:-}" ]; then args+=(--seed-cache "$SEED_DIR"); fi
if [ -n "${SEED_MODELS:-}" ]; then args+=(--seed-models "$SEED_MODELS"); fi

# 1. plan, then install
"$H" plan "${args[@]}" >"$WORK/plan.txt" 2>&1; check plan_exit "$?" 0
t0=$(date +%s)
"$H" install "${args[@]}" >"$WORK/install.out" 2>"$WORK/install.err"; rc=$?
result install_seconds "$(( $(date +%s) - t0 ))"
check install_exit "$rc" 0
result install_steps "$(grep -o '"state":"done","step":"[a-z-]*"' "$WORK/install.out" | sed 's/.*"step":"//; s/"//' | tr '\n' ' ')"
[ "$rc" = 0 ] || result install_error "$(tail -n 3 "$WORK/install.err" | tr '\n' ' ' | cut -c1-600)"
L() { env LIMA_HOME="$PREFIX/lima" HOME="$PREFIX/Library/Application Support/Domovoi/home" "$PREFIX/Library/Application Support/Domovoi/bin/lima/bin/limactl" "$@"; }
if [ "$rc" != 0 ] && [ "$(L list --format '{{.Status}}' domovoi 2>/dev/null)" = Running ]; then
  # What the guest's network looks like, both ways out: vzNAT (lima0, the
  # default route) and Lima's user-mode network (eth0).
  G() { L shell --workdir / domovoi "$@" 2>&1 | tr '\n' ' ' | cut -c1-400; }
  result net_routes "$(G ip -4 route show)"
  result net_addrs "$(G ip -4 -o addr show)"
  result net_dns "$(G getent ahostsv4 archive.ubuntu.com | head -n 1)"
  result net_via_default "$(G curl -sS -m 8 -o /dev/null -w 'http=%{http_code}' http://archive.ubuntu.com/ubuntu/)"
  result net_via_lima0 "$(G curl -sS -m 8 --interface lima0 -o /dev/null -w 'http=%{http_code}' http://archive.ubuntu.com/ubuntu/)"
  result net_via_eth0 "$(G curl -sS -m 8 --interface eth0 -o /dev/null -w 'http=%{http_code}' http://archive.ubuntu.com/ubuntu/)"
  result net_gw_lima0 "$(G sh -c 'gw=$(ip -4 route show default dev lima0 | awk "{print \$3; exit}"); ping -c 2 -W 2 $gw | tail -n 2')"
  result net_from_mac "$(curl -sS -m 8 -o /dev/null -w 'http=%{http_code}' http://archive.ubuntu.com/ubuntu/ 2>&1)"
  result mac_bridge "$(ifconfig | awk '/^bridge[0-9]+:/{b=$1} b && /inet /{print b, $2} /^[a-z]/ && !/^bridge/{b=""}' | tr '\n' ' ')"
  if [ "${NET_FALLBACK:-0}" = 1 ]; then
    # Diagnostic only: send the guest's traffic out through Lima's
    # user-mode network, then run the install again (the repair path).
    G sudo ip route add default via 192.168.5.2 dev eth0 metric 50 >/dev/null
    result net_fallback "$(G ip -4 route show default)"
    t0=$(date +%s)
    "$H" install "${args[@]}" >"$WORK/install2.out" 2>"$WORK/install2.err"; rc=$?
    result install2_seconds "$(( $(date +%s) - t0 ))"
    check install2_exit "$rc" 0
    for f in "$PREFIX"/Library/LaunchDaemons/*.pid; do [ -f "$f" ] && echo "$(basename "$f" .pid) $(cat "$f")"; done >>"$WORK/pids.txt"
    [ "$rc" = 0 ] || result install2_error "$(tail -n 3 "$WORK/install2.err" | tr '\n' ' ' | cut -c1-600)"
  fi
fi
for f in "$PREFIX"/Library/LaunchDaemons/*.pid; do [ -f "$f" ] && echo "$(basename "$f" .pid) $(cat "$f")"; done >"$WORK/pids.txt"
result pids "$(tr '\n' ' ' <"$WORK/pids.txt")"

if [ "$rc" = 0 ]; then
  # 2. what is running, from the Mac
  "$H" status "${common[@]}" --wait-healthy 120 >"$WORK/status.json"; check status_healthy "$?" 0
  result status "$(cat "$WORK/status.json")"
  check web_health "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/health")" 200
  check core_health "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$CORE_PORT/v1/health")" 200
  check dashboard "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/")" 200
  check ollama_version "$(curl -s -m 5 "http://127.0.0.1:$OLLAMA_PORT/api/version")" '{"version":"0.34.4"}'
  # Every listener of the helper's jobs and their children (lsof -a: AND).
  pids=$(awk '{print $2}' "$WORK/pids.txt" | tr '\n' ',' | sed 's/,$//')
  pg=$(for p in $(awk '{print $2}' "$WORK/pids.txt"); do pgrep -g "$p"; done | sort -u | tr '\n' ',' | sed 's/,$//')
  result mac_listeners "$(lsof -nP -a -p "${pg:-$pids}" -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $1, $9}' | sort -u | tr '\n' ';')"
  L() { env LIMA_HOME="$PREFIX/lima" HOME="$PREFIX/Library/Application Support/Domovoi/home" "$PREFIX/Library/Application Support/Domovoi/bin/lima/bin/limactl" "$@"; }
  result guest_lan_json "$(L shell --workdir / domovoi sudo stat -c '%U:%G %a' /var/lib/domovoi-mac /var/lib/domovoi-mac/lan.json /var/lib/domovoi-mac/side.json | tr '\n' ' ') $(L shell --workdir / domovoi sudo cat /var/lib/domovoi-mac/lan.json)"
  result guest_side_read "$(L shell --workdir / domovoi sudo -u domovoi sh -c 'cd /opt/domovoi && set -a && . domovoi/.env && set +a && .venv/bin/python -c "from domovoi import windows_side as w; print(w.read_host_side())"' 2>&1 | tail -n 1 | cut -c1-300)"
  result guest_units "$(L shell --workdir / domovoi systemctl is-active domovoi-db domovoi-core domovoi-web domovoi-vm-guard | tr '\n' ' ')"
  result provision_final "$(grep '"final":true' "$PREFIX/Library/Logs/Domovoi/provision.log" | tail -n 1 | cut -c1-400)"

  # 3. a second install is a repair
  t0=$(date +%s)
  "$H" install "${args[@]}" >"$WORK/repair.out" 2>"$WORK/repair.err"; check repair_exit "$?" 0
  result repair_seconds "$(( $(date +%s) - t0 ))"
  for f in "$PREFIX"/Library/LaunchDaemons/*.pid; do [ -f "$f" ] && echo "$(basename "$f" .pid) $(cat "$f")"; done >>"$WORK/pids.txt"
  "$H" status "${common[@]}" --wait-healthy 180 >"$WORK/status2.json"; check repair_healthy "$?" 0
fi

# The helper's logs leave with the prefix: keep a copy first.
cp -R "$PREFIX/Library/Logs/Domovoi" "$WORK/logs" 2>/dev/null
cp "$PREFIX/lima/domovoi/ha.stdout.log" "$PREFIX/lima/domovoi/ha.stderr.log" "$WORK/logs/" 2>/dev/null

# 4. keep data: the jobs stop, the VM is stopped and kept, the models stay
"$U" "${common[@]}" >"$WORK/uninstall-keep.out" 2>&1; check uninstall_keep_exit "$?" 0
result after_keep "$(cd "$PREFIX" && find . -maxdepth 4 -mindepth 1 | sort | tr '\n' ' ' | cut -c1-900)"
check keep_vm_kept "$(test -d "$PREFIX/lima/domovoi" && echo yes)" yes
check keep_record_kept "$(test -f "$PREFIX/Library/Application Support/Domovoi/state/install.json" && echo yes)" yes
check keep_limactl_gone "$(test -e "$PREFIX/Library/Application Support/Domovoi/bin" && echo there || echo gone)" gone
alive=""; while read -r label pid; do kill -0 "$pid" 2>/dev/null && alive="$alive $label:$pid"; done <"$WORK/pids.txt"
check keep_jobs_stopped "${alive:-none}" none

# 5. remove everything: the prefix ends empty
"$U" "${common[@]}" --remove-everything --confirm DELETE >"$WORK/uninstall-all.out" 2>&1; check uninstall_all_exit "$?" 0
check prefix_empty "$(find "$PREFIX" -mindepth 1 | wc -l | tr -d ' ')" 0
alive=""; while read -r label pid; do kill -0 "$pid" 2>/dev/null && alive="$alive $label:$pid"; done <"$WORK/pids.txt"
check no_job_left "${alive:-none}" none
check no_listener_left "$(lsof -nP -iTCP:"$WEB_PORT" -iTCP:"$CORE_PORT" -iTCP:"$OLLAMA_PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR>1' | wc -l | tr -d ' ')" 0
check no_dot_lima "$(test -e "$HOME/.lima" && echo there || echo none)" none
result done "fail=$fail"
exit $fail
