#!/usr/bin/env bash
# The network name on a real (Docker) network: Domovoi's own mDNS responder
# (domovoi/lan_name/) in throwaway containers on one private bridge, real
# UDP 5353 and real multicast between them.
#
# Cases:
#   default       the first server takes domovoi.local
#   second        a second one finds it taken and takes domovoi2.local
#   query-group   a third container asks in group mode and hears each address
#   query-legacy  ... and from an ephemeral port (legacy unicast), by unicast
#   typed-taken   a typed name someone holds is not changed: state "taken"
#   goodbye       a stopping server's goodbye (TTL 0) is heard
#   avahi         beside avahi-daemon in the same container: both bind 5353,
#                 and the name Avahi publishes for the box (with its LAN
#                 address) is left to Avahi: state "held"
#   other-net     a server on two networks ignores a flood of forged answers
#                 that arrive on the other one (interface + subnet check);
#                 the same flood on a server's own network does take its name
#
# Usage: scripts/dev/run-lan-name-test.sh [--rebuild] [--keep-logs]
#
# Needs Docker (Docker Desktop on Windows; run it from Git Bash). The bridge
# is a plain user-defined one: an --internal bridge drops multicast (Docker's
# isolation rules drop every packet whose destination is outside the
# subnet, 224.0.0.251 included). 224.0.0.251 is link-local: it is never
# routed or NATed off the bridge, so the test's names reach only these
# containers, and the nodes send nothing else. Every container and the
# bridge are removed at the end. The image is
# scripts/dev/lan-name-harness.Dockerfile (built once per change).
# Exit status: 0 when every case passed.

set -uo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=$(cd "$HERE/../.." && pwd)
REBUILD=0
KEEP=0
for a in "$@"; do
  case $a in
    --rebuild) REBUILD=1 ;;
    --keep-logs) KEEP=1 ;;
    -h | --help) sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "docker isn't on PATH" >&2; exit 2; }
DOCKERFILE=$HERE/lan-name-harness.Dockerfile
TAG=$(tr -d '\r' <"$DOCKERFILE" | sha256sum | cut -c1-12)
IMAGE=domovoi-lan-name-harness:$TAG
if [ "$REBUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "building $IMAGE ..." >&2
  tr -d '\r' <"$DOCKERFILE" | docker build -q -t "$IMAGE" - >/dev/null || { echo "image build failed" >&2; exit 2; }
fi

WORK=$(mktemp -d)
NET=domovoi-lanname-test-$$
NET_SUBNET=172.29.77.0/24
ISO_IP=172.29.77.10
NET2=domovoi-lanname-test2-$$
NET2_SUBNET=172.30.99.0/24
NET2_IP=172.30.99.10
CONTAINERS=()
cleanup() {
  local c
  for c in ${CONTAINERS[@]+"${CONTAINERS[@]}"}; do docker rm -f "$c" >/dev/null 2>&1; done
  docker network rm "$NET" "$NET2" >/dev/null 2>&1
  if [ "$KEEP" = 1 ]; then echo "logs kept: $WORK" >&2; else rm -rf "$WORK"; fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Only what the node needs: the responder package and the node script.
(cd "$SRC" && tar -cf "$WORK/tree.tar" domovoi/__init__.py domovoi/lan_name scripts/linux/tests/lan_name_node.py) \
  || { echo "copying the tree failed" >&2; exit 2; }
docker network create --subnet "$NET_SUBNET" "$NET" >/dev/null || { echo "can't create the bridge" >&2; exit 2; }

# node NAME [docker run args --] node-args...: one container in the background,
# its output in $WORK/NAME.log.
node() {
  local name=$1 cname=domovoi-lanname-$1-$$
  shift
  local extra=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do extra+=("$1"); shift; done
  [ "${1:-}" = -- ] && shift
  CONTAINERS+=("$cname")
  docker run --rm -i --name "$cname" --network "${NODE_NET:-$NET}" ${extra[@]+"${extra[@]}"} "$IMAGE" \
    bash -c 'mkdir -p /root/src && tar -xf - -C /root/src && exec python3 /root/src/scripts/linux/tests/lan_name_node.py "$@"' \
    node "$@" <"$WORK/tree.tar" >"$WORK/$name.log" 2>&1 &
  PIDS[$name]=$!
}
declare -A PIDS

# done_with NAME: wait for that node to end.
done_with() { wait "${PIDS[$1]}" 2>/dev/null; }

# wait_for LOG PATTERN SECONDS
wait_for() {
  local i
  for i in $(seq 1 $(($3 * 10))); do
    grep -q -- "$2" "$WORK/$1.log" 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

PASS=0
FAIL=0
ok() { echo "ok   $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $1: $2"; FAIL=$((FAIL + 1)); [ -f "$WORK/$3.log" ] && sed 's/^/     | /' "$WORK/$3.log"; }
ip_of() { sed -n 's/.*"ip": "\([0-9.]*\)".*/\1/p' "$WORK/$1.log" | head -n 1; }

# --- default: the first server takes domovoi.local
node a --hostname srv-a -- respond --name domovoi --seconds 40
if wait_for a '"state": "announced", "taken": null' 15 && grep -q '"claimed": "domovoi",' "$WORK/a.log"; then
  ok default
else
  bad default "the first server did not announce domovoi" a
fi
A_IP=$(ip_of a)

# --- second: another server finds it taken and takes domovoi2.local
node b --hostname srv-b -- respond --name domovoi --seconds 18
if wait_for b '"claimed": "domovoi2", "state": "announced", "taken": "domovoi"' 15; then
  ok second
else
  bad second "the second server did not move to domovoi2" b
fi
B_IP=$(ip_of b)

# --- queries from a third container
node qg -- query --name domovoi.local --wait 2
node qg2 -- query --name domovoi2.local --wait 2
node ql -- query --name domovoi.local --legacy --wait 2
done_with qg; done_with qg2; done_with ql
if grep -q "\"a\": \"$A_IP\"" "$WORK/qg.log" && ! grep -q "\"a\": \"$B_IP\"" "$WORK/qg.log" \
   && grep -q "\"a\": \"$B_IP\"" "$WORK/qg2.log"; then
  ok query-group
else
  bad query-group "group-mode answers: domovoi.local should be $A_IP, domovoi2.local $B_IP" qg
  sed 's/^/     | /' "$WORK/qg2.log"
fi
if grep -q "\"a\": \"$A_IP\", \"ttl\": 10, \"from\": \"$A_IP\", \"unicast\": true" "$WORK/ql.log"; then
  ok query-legacy
else
  bad query-legacy "no unicast answer with a 10 s TTL from $A_IP" ql
fi

# --- typed-taken: a name the household typed that another server holds
node t --hostname srv-t -- respond --name domovoi2 --seconds 6
done_with t
if grep -q '"claimed": null, "state": "taken", "taken": "domovoi2"' "$WORK/t.log"; then
  ok typed-taken
else
  bad typed-taken "a typed name held by another server should say taken and claim nothing" t
fi

# --- goodbye: b stops (its --seconds ends) and says goodbye for domovoi2
node g -- goodbye --name domovoi2.local --wait 25
done_with b
done_with g
if grep -q "\"event\": \"goodbye\", \"name\": \"domovoi2.local\", \"a\": \"$B_IP\"" "$WORK/g.log"; then
  ok goodbye
else
  bad goodbye "no TTL-0 record for domovoi2.local from $B_IP" g
fi

# --- avahi: avahi-daemon publishes avahibox.local for this box; our
# responder binds 5353 beside it and holds that name as its own.
cat >"$WORK/avahi.sh" <<'AV'
set -e
mkdir -p /root/src && tar -xf - -C /root/src
sed -i 's/^#\{0,1\}enable-dbus=.*/enable-dbus=no/' /etc/avahi/avahi-daemon.conf
avahi-daemon --no-drop-root --no-chroot --no-rlimits -D
sleep 3
pgrep -x avahi-daemon >/dev/null && echo '{"event": "avahi-running"}'
python3 /root/src/scripts/linux/tests/lan_name_node.py respond --name avahibox --seconds 6
pgrep -x avahi-daemon >/dev/null && echo '{"event": "avahi-still-running"}'
AV
CONTAINERS+=("domovoi-lanname-av-$$")
docker run --rm -i --name "domovoi-lanname-av-$$" --network "$NET" --hostname avahibox "$IMAGE" \
  bash -c "$(cat "$WORK/avahi.sh")" <"$WORK/tree.tar" >"$WORK/av.log" 2>&1
if grep -q '"event": "avahi-running"' "$WORK/av.log" \
   && grep -q '"phase": "held", "requested": "avahibox", "claimed": "avahibox", "state": "held"' "$WORK/av.log" \
   && grep -q '"event": "avahi-still-running"' "$WORK/av.log"; then
  ok avahi
else
  bad avahi "beside avahi-daemon the responder should bind and hold avahibox.local as ours" av
fi

# --- other-net: a server on two networks; a device on the second floods
# forged answers for the server's name at its second address. They arrive
# on the other interface, from outside the LAN's subnet: ignored. The
# control: the same flood on a server's own network takes its name.
if docker network create --subnet "$NET2_SUBNET" "$NET2" >/dev/null; then
  ISO=domovoi-lanname-iso-$$
  CONTAINERS+=("$ISO")
  docker create -i --name "$ISO" --network "$NET" --ip "$ISO_IP" --hostname srv-iso "$IMAGE" \
    bash -c 'mkdir -p /root/src && tar -xf - -C /root/src && exec python3 /root/src/scripts/linux/tests/lan_name_node.py "$@"' \
    node respond --name isolated --ip "$ISO_IP" --seconds 8 >/dev/null
  docker network connect --ip "$NET2_IP" "$NET2" "$ISO"
  NODE_NET=$NET2 node f1 -- forge --name isolated.local --target "$NET2_IP" --seconds 10
  NODE_NET=$NET2 node f2 -- forge --name isolated2.local --target group --seconds 10
  sleep 1
  docker start -ai "$ISO" <"$WORK/tree.tar" >"$WORK/iso.log" 2>&1 &
  PIDS[iso]=$!
  NODE_NET=$NET2 node ctl --hostname srv-ctl -- respond --name isolated2 --seconds 8
  done_with iso; done_with ctl; done_with f1; done_with f2
  if grep -q "\"ip\": \"$ISO_IP\", \"phase\": \"announced\", \"requested\": \"isolated\", \"claimed\": \"isolated\", \"state\": \"announced\"" "$WORK/iso.log" \
     && grep -q '"claimed": null, "state": "taken", "taken": "isolated2"' "$WORK/ctl.log" \
     && grep -q '"event": "forged"' "$WORK/f1.log"; then
    ok other-net
  else
    bad other-net "forged answers on the other network must be ignored, and must win on the control's own network" iso
    sed 's/^/     | /' "$WORK/ctl.log" "$WORK/f1.log"
  fi
else
  bad other-net "can't create the second bridge" a
fi

done_with a
echo "lan-name: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
