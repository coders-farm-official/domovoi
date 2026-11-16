#!/usr/bin/env bash
# linux-install-vm.sh: a real fresh install of the Domovoi server in a
# throwaway virtual machine, then an update and a rollback, the way
# .github/workflows/linux-install.yml runs it on GitHub's runners (which
# have KVM). docs/CONTRIBUTING.md, "The fresh-install test", says when it
# runs and how to read it.
#
# Usage:
#   scripts/dev/linux-install-vm.sh SYSTEM [--work DIR] [--keep-vm]
#
#   SYSTEM     ubuntu-26.04 | debian-13: the cloud image the VM boots
#              (pinned below by release and checksum)
#   --work DIR where the image, the VM's disk and the logs go (default:
#              .test-results/linux-install/SYSTEM in this checkout)
#   --keep-vm  leave the VM running at the end (ssh line printed), for a
#              look around; stop it with: sudo kill "$(cat DIR/qemu.pid)"
#
# Needs a Linux x86_64 host with KVM (/dev/kvm), about 12 GB of memory and
# 40 GB of disk to spare, and: qemu-system-x86_64, qemu-img, genisoimage
# (or cloud-localds), ssh, scp, ssh-keygen, curl, git, and sudo where
# /dev/kvm is not writable. Never on a box that runs Domovoi: it touches
# nothing outside --work, but the VM's 6369/6370 stay inside it anyway.
#
# What it does, each phase timed:
#   1. The VM: the cloud image (checksum checked), a 40 GB overlay disk, a
#      cloud-init seed with a throwaway ssh key and nothing else.
#   2. Three commits, made from this checkout's HEAD in a throwaway clone:
#      BASE (HEAD itself), U1 (a dependency change, and another
#      python-build-standalone build of the same CPython with the uv that
#      knows it, so a box on Domovoi's own CPython moves its venv: step 4b
#      of docs/LINUX_HOST.md, Updates from the dashboard) and U2 (U1 with
#      the core broken on purpose). They go into the VM as a bare
#      repository, /srv/ci/origin.git, whose main is BASE.
#   3. The install: provision.sh from BASE, smallest: --skip-models
#      --no-ai-models --no-vision-model --tier floor (Ollama, Whisper's
#      runtime, the voices and the images are still installed: only the
#      model downloads are left out), --internet never, Etc/UTC.
#      Checks: the final line says ok; domovoi-db, -core, -web, docker and
#      ollama are active; /v1/health and /api/health answer; the venv runs
#      CPython 3.14 (Debian: Domovoi's own, under /usr/local/lib/
#      domovoi-python; Ubuntu: the system's); never masked apt's timers.
#   4. provision.sh again: every step skipped, nothing restarted.
#   5. The update: the answer switched to sometimes (as Settings does),
#      main moved to U1, the checkout pulled as the service user (as the
#      dashboard's pull does), domovoi-update.service started. Checks: the
#      result is ok at U1, the dependencies were re-synced, on Debian the
#      venv moved to the new build (a python step), on Ubuntu it did not;
#      health again; apt's timers back.
#   6. The rollback: main moved to U2, pulled, the update unit started.
#      Checks: the result is rolled_back, the checkout is back at U1, and
#      it is healthy.
# On the way out, whatever happened: the provision log, both result
# files, the journal of the Domovoi units and the VM's console in
# --work/logs, and a summary (also into GITHUB_STEP_SUMMARY on Actions).
# Exit status: 0 every check passed, 1 one failed, 2 bad usage.

set -euo pipefail

usage() { sed -n '/^# Usage:/,/^# Needs/p' "${BASH_SOURCE[0]}" | sed '$d; s/^# \{0,1\}//'; }
die() { echo "linux-install-vm: $*" >&2; exit 2; }

SYSTEM=""
WORK=""
KEEP_VM=0
while [ $# -gt 0 ]; do
  case $1 in
    --work) [ $# -ge 2 ] || die "--work needs a directory"; WORK=$2; shift 2 ;;
    --keep-vm) KEEP_VM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown option: $1 (--help)" ;;
    *) [ -z "$SYSTEM" ] || die "one system at a time"; SYSTEM=$1; shift ;;
  esac
done

# The cloud images, by release and checksum (a dated release directory,
# never "current", so a run tests the same starting point until this is
# moved; move both lines together).
case $SYSTEM in
  ubuntu-26.04)
    IMAGE_URL=https://cloud-images.ubuntu.com/releases/26.04/release-20261016/ubuntu-26.04-server-cloudimg-amd64.img
    IMAGE_SUM=sha256:5645d0318ad21f643ebb1ac4567514620e9893380b07b0201fda163baa5b7eb5
    OWN_PYTHON=0 ;;
  debian-13)
    IMAGE_URL=https://cloud.debian.org/images/cloud/trixie/20261103-2651/debian-13-genericcloud-amd64-20261103-2651.qcow2
    IMAGE_SUM=sha512:d25999de0770d4cde1be72c299ba3ad94eac7ea8fd4d18f8cc86c69df69337e2e8a91d64465a9bfd8f2a78c566fcbcb0842eacc91c09efdef72d719d9f13b535
    OWN_PYTHON=1 ;;
  "") usage >&2; exit 2 ;;
  *) die "SYSTEM is ubuntu-26.04 or debian-13, not: $SYSTEM" ;;
esac

# U1's CPython: the same 3.14.10, python-build-standalone's next build,
# with the uv release that carries its checksum (both read from GitHub's
# release API on 2026-11-15, as provision-manifest.conf's are).
NEXT_UV_VERSION=0.13.8
NEXT_UV_SHA256=5acfd9177cfecf0bf87578f059728215ccc884388988f55f7a65041b57387d91
NEXT_PYTHON_BUILD=20261110
NEXT_PYTHON_SHA256=c2ff2e97422f5d3896a16e0317fd212ba977bbf2688d93a6f7a03d817506bd30

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${WORK:-$SRC/.test-results/linux-install/$SYSTEM}
mkdir -p "$WORK/logs"
WORK=$(cd "$WORK" && pwd)
LOGS=$WORK/logs
SUMMARY=$LOGS/summary.md
: >"$SUMMARY"
SSH_PORT=${LINUX_INSTALL_SSH_PORT:-2222}
VM_CPUS=$(nproc)
VM_MEM_MB=$(awk '/^MemTotal:/ {m = int($2 / 1024 * 0.75); if (m > 12288) m = 12288; print m}' /proc/meminfo)
FAILED=0
T_START=$(date +%s)
QEMU_PID=""

say() { printf '[%s +%ss] %s\n' "$SYSTEM" "$(( $(date +%s) - T_START ))" "$*"; }
note() { printf '%s\n' "$*" >>"$SUMMARY"; }
pass() { say "ok   $*"; note "- ok: $*"; }
failc() { say "FAIL $*"; note "- **FAIL**: $*"; FAILED=1; }
# check WHAT CMD...: a check, recorded; the run goes on.
check() { local what=$1; shift; if "$@"; then pass "$what"; else failc "$what"; fi; }
# phase NAME: the time each phase took goes into the summary.
PHASE="" PHASE_T=0
phase() {
  local now; now=$(date +%s)
  if [ -n "$PHASE" ]; then note "- $PHASE took $(( now - PHASE_T )) s"; fi
  PHASE=$1 PHASE_T=$now
  if [ -n "$PHASE" ]; then say "== $PHASE"; note ""; note "### $PHASE"; fi
}

SSH_OPTS=(-i "$WORK/id_ed25519" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=10 -o BatchMode=yes)
vm() { ssh "${SSH_OPTS[@]}" ci@127.0.0.1 "$@"; }
vm_put() { scp -q -i "$WORK/id_ed25519" -P "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$@" ci@127.0.0.1:; }
as_root() { if [ -w /dev/kvm ]; then "$@"; else sudo "$@"; fi; }

collect() {
  set +e
  if [ -n "$QEMU_PID" ]; then
    vm 'sudo journalctl --no-pager -u domovoi-db -u domovoi-core -u domovoi-web -u domovoi-update -u ollama -u docker' >"$LOGS/journal.log" 2>&1
    vm 'sudo cat /var/lib/domovoi-provision/result.json' >"$LOGS/provision-result.json" 2>/dev/null
    vm 'sudo cat /var/lib/domovoi-update/last-result.json' >"$LOGS/update-last-result.json" 2>/dev/null
    vm 'sudo systemctl --failed --no-pager; df -h /; free -m' >"$LOGS/vm-state.log" 2>&1
    if [ "$KEEP_VM" = 1 ]; then
      say "the VM keeps running: ssh ${SSH_OPTS[*]} ci@127.0.0.1"
    else
      vm 'sudo poweroff' >/dev/null 2>&1
      sleep 5
      as_root kill "$QEMU_PID" 2>/dev/null
    fi
  fi
  if [ -f "$WORK/serial.log" ]; then as_root cat "$WORK/serial.log" >"$LOGS/console.log" 2>/dev/null; fi
  phase ""
  note ""
  note "**$SYSTEM: $([ "$FAILED" = 0 ] && echo passed || echo FAILED) in $(( $(date +%s) - T_START )) s**"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then { echo "## Fresh install: $SYSTEM"; cat "$SUMMARY"; } >>"$GITHUB_STEP_SUMMARY"; fi
}
trap collect EXIT

# ─── 1. The VM ───────────────────────────────────────────────────────────

phase "the VM"
for t in qemu-system-x86_64 qemu-img ssh scp ssh-keygen curl git; do
  command -v "$t" >/dev/null 2>&1 || die "needs $t"
done
[ -e /dev/kvm ] || die "needs KVM (/dev/kvm)"
IMG=$WORK/$(basename "$IMAGE_URL")
algo=${IMAGE_SUM%%:*} want=${IMAGE_SUM#*:}
if [ ! -f "$IMG" ] || [ "$("${algo}sum" "$IMG" | cut -d' ' -f1)" != "$want" ]; then
  say "downloading $IMAGE_URL"
  curl -fsSL --retry 3 -o "$IMG.part" "$IMAGE_URL"
  [ "$("${algo}sum" "$IMG.part" | cut -d' ' -f1)" = "$want" ] || { rm -f "$IMG.part"; die "$IMAGE_URL does not match its $algo pin"; }
  mv -f "$IMG.part" "$IMG"
fi
pass "cloud image $(basename "$IMG"), $algo checked"
rm -f "$WORK/disk.qcow2" "$WORK/id_ed25519" "$WORK/id_ed25519.pub" "$WORK/seed.iso"
qemu-img create -q -f qcow2 -F qcow2 -b "$IMG" "$WORK/disk.qcow2" 40G
ssh-keygen -q -t ed25519 -N '' -C linux-install-vm -f "$WORK/id_ed25519"
mkdir -p "$WORK/seed"
cat >"$WORK/seed/user-data" <<EOF
#cloud-config
users:
  - name: ci
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$WORK/id_ed25519.pub")
ssh_pwauth: false
package_update: false
package_upgrade: false
EOF
printf 'instance-id: linux-install-%s\nlocal-hostname: domovoi-ci\n' "$SYSTEM" >"$WORK/seed/meta-data"
if command -v cloud-localds >/dev/null 2>&1; then
  cloud-localds "$WORK/seed.iso" "$WORK/seed/user-data" "$WORK/seed/meta-data"
else
  genisoimage -quiet -output "$WORK/seed.iso" -volid cidata -joliet -rock "$WORK/seed/user-data" "$WORK/seed/meta-data"
fi
rm -f "$WORK/qemu.pid" "$WORK/serial.log"
as_root qemu-system-x86_64 -enable-kvm -cpu host -smp "$VM_CPUS" -m "$VM_MEM_MB" \
  -drive "file=$WORK/disk.qcow2,if=virtio,format=qcow2" \
  -drive "file=$WORK/seed.iso,if=virtio,format=raw,readonly=on" \
  -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22" -device virtio-net-pci,netdev=n0 \
  -display none -serial "file:$WORK/serial.log" -daemonize -pidfile "$WORK/qemu.pid"
QEMU_PID=$(as_root cat "$WORK/qemu.pid")
say "VM started (pid $QEMU_PID, $VM_CPUS CPUs, $VM_MEM_MB MB)"
for _ in $(seq 1 60); do
  if vm true 2>/dev/null; then break; fi
  sleep 5
done
vm true || { failc "the VM answers ssh within 5 minutes"; exit 1; }
vm 'sudo cloud-init status --wait >/dev/null 2>&1 || true; . /etc/os-release; echo "$PRETTY_NAME, $(uname -r), $(nproc) CPUs, $(free -m | awk "/^Mem:/ {print \$2}") MB, $(df -h / | awk "NR==2 {print \$2}") disk"' | tee "$LOGS/vm.txt"
pass "VM up: $(cat "$LOGS/vm.txt")"

# ─── 2. The commits ──────────────────────────────────────────────────────

phase "the commits"
R=$WORK/repo
rm -rf "$R" "$WORK/origin.git"
git clone -q --no-local "$SRC" "$R"
BASE=$(git -C "$SRC" rev-parse HEAD)
gc() { git -C "$R" -c user.name=ci -c user.email=ci@example.invalid -c commit.gpgsign=false "$@"; }
gc checkout -q -B ci-base "$BASE"
gc checkout -q -b ci-u1
printf '# linux-install-vm: an update that changes the dependency inputs\n' >>"$R/requirements-linux-py314.in"
sed -i -e "s/^uv_version=.*/uv_version=$NEXT_UV_VERSION/" -e "s/^uv_sha256=.*/uv_sha256=$NEXT_UV_SHA256/" \
  -e "s/^python_build=.*/python_build=$NEXT_PYTHON_BUILD/" -e "s/^python_sha256=.*/python_sha256=$NEXT_PYTHON_SHA256/" \
  "$R/scripts/linux/provision-manifest.conf"
gc commit -q -am "linux-install-vm: U1, the dependencies and the CPython build"
U1=$(gc rev-parse HEAD)
gc checkout -q -b ci-u2
sed -i '1i raise SystemExit("linux-install-vm: U2 is broken on purpose")' "$R/domovoi/main.py"
gc commit -q -am "linux-install-vm: U2, broken on purpose"
U2=$(gc rev-parse HEAD)
git clone -q --bare --no-local "$R" "$WORK/origin.git"
git -C "$WORK/origin.git" update-ref refs/heads/main "$BASE"
git -C "$WORK/origin.git" symbolic-ref HEAD refs/heads/main
tar -czf "$WORK/origin.tgz" -C "$WORK" origin.git
git -C "$R" show "$BASE:scripts/linux/provision.sh" >"$WORK/provision.sh"
vm_put "$WORK/origin.tgz" "$WORK/provision.sh"
vm 'sudo mkdir -p /srv/ci && sudo tar -xzf origin.tgz -C /srv/ci && sudo chown -R root:root /srv/ci && sudo chmod -R a+rX /srv/ci'
# The origin is root's and the service user clones and pulls from it: git
# asks for that to be said (a real box clones over https).
vm 'printf "[safe]\n\tdirectory = /srv/ci/origin.git\n" | sudo tee -a /etc/gitconfig >/dev/null'
pass "BASE ${BASE:0:12}, U1 ${U1:0:12}, U2 ${U2:0:12} in /srv/ci/origin.git (main = BASE)"

# ─── 3. The install ──────────────────────────────────────────────────────

phase "the install"
PROVISION_ARGS="--repo-url /srv/ci/origin.git --ref main --track-branch main --internet never --timezone Etc/UTC --tier floor --no-ai-models --skip-models --no-vision-model --health-timeout 900"
set +e
vm "sudo bash provision.sh $PROVISION_ARGS </dev/null" 2>&1 | tee "$LOGS/provision.log"
rc=${PIPESTATUS[0]}
set -e
final=$(grep '"final":true' "$LOGS/provision.log" | tail -n 1 || true)
check "provision.sh finished: ok (exit $rc)" grep -q '"result":"ok"' <<<"$final"
health() { vm 'curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:6370/v1/health && curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:6369/api/health'; }
active() { vm "systemctl is-active --quiet domovoi-db domovoi-core domovoi-web docker ollama"; }
check "domovoi-db, -core, -web, docker and ollama are active" active
check "/v1/health and /api/health answer" health
py_report=$(vm 'sudo -u domovoi /opt/domovoi/.venv/bin/python -c "import sys; print(\"%d.%d.%d\" % sys.version_info[:3])"; sed -n "s/^home *= *//p" /opt/domovoi/.venv/pyvenv.cfg' || true)
say "venv: $(tr '\n' ' ' <<<"$py_report")"
check "the venv runs CPython 3.14" grep -q '^3\.14\.' <<<"$py_report"
if [ "$OWN_PYTHON" = 1 ]; then
  check "on Domovoi's own CPython (the manifest's build)" grep -q '^/usr/local/lib/domovoi-python/20261026/cpython-3.14.10-linux-x86_64-gnu/bin$' <<<"$py_report"
else
  check "on the system's python3" grep -qx '/usr/bin' <<<"$py_report"
  check "no CPython of Domovoi's" vm '! test -e /usr/local/lib/domovoi-python'
fi
check "never: apt's daily timer masked" vm '[ "$(systemctl is-enabled apt-daily-upgrade.timer 2>/dev/null)" = masked ]'
check "the update unit is installed" vm 'test -f /etc/systemd/system/domovoi-update.service'

# ─── 4. Again ────────────────────────────────────────────────────────────

phase "provision.sh again"
vm "sudo bash provision.sh $PROVISION_ARGS </dev/null" >"$LOGS/provision-again.log" 2>&1 || true
check "the second run ends ok" grep -q '"final":true.*"result":"ok"' "$LOGS/provision-again.log"
check "and skips the venv" grep -q '\[venv\] already done, inputs unchanged' "$LOGS/provision-again.log"
check "and restarts nothing" bash -c '! grep -q "restarting domovoi" "$1"' _ "$LOGS/provision-again.log"

# ─── 5. The update ───────────────────────────────────────────────────────

# update_to SHA: what the dashboard does: the commit published on main, the
# checkout pulled as the service user, the update unit started.
update_to() {
  vm "sudo git -C /srv/ci/origin.git update-ref refs/heads/main $1 \
    && sudo -u domovoi git -C /opt/domovoi pull -q --ff-only \
    && sudo timeout 1500 systemctl start domovoi-update.service" >"$LOGS/update-${1:0:12}.log" 2>&1 || true
  vm 'sudo cat /var/lib/domovoi-update/last-result.json' >"$LOGS/result-${1:0:12}.json" 2>/dev/null || true
}
result_has() { grep -q -- "$2" "$LOGS/result-${1:0:12}.json"; }

phase "the update (U1)"
vm "sudo -u domovoi sed -i 's/^INTERNET_ACCESS=.*/INTERNET_ACCESS=sometimes/' /opt/domovoi/domovoi/.env"
update_to "$U1"
check "status ok" result_has "$U1" '"status": "ok"'
check "at U1" result_has "$U1" "\"to_sha\": \"$U1\""
check "the dependencies re-synced" result_has "$U1" '"deps_changed": true'
check "healthy" health
if [ "$OWN_PYTHON" = 1 ]; then
  check "a python step moved the venv" result_has "$U1" '"name": "python", "status": "ok"'
  check "onto U1's build" vm "grep -q '^home = /usr/local/lib/domovoi-python/$NEXT_PYTHON_BUILD/' /opt/domovoi/.venv/pyvenv.cfg"
  check "the old build kept" vm 'test -x /usr/local/lib/domovoi-python/20261026/cpython-3.14.10-linux-x86_64-gnu/bin/python3.14'
else
  check "no python step (the system's python3)" bash -c '! grep -q "\"name\": \"python\"" "$1"' _ "$LOGS/result-${U1:0:12}.json"
fi
check "sometimes: apt's daily timer back" vm '[ "$(systemctl is-enabled apt-daily-upgrade.timer 2>/dev/null)" != masked ]'

# ─── 6. The rollback ─────────────────────────────────────────────────────

phase "the rollback (U2)"
update_to "$U2"
check "status rolled_back" result_has "$U2" '"status": "rolled_back"'
check "the checkout back at U1" vm "[ \"\$(sudo -u domovoi git -C /opt/domovoi rev-parse HEAD)\" = $U1 ]"
check "healthy again" health

exit "$FAILED"
