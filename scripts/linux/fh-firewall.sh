#!/usr/bin/env bash
# Keep Friend Houses' Tor container off the house's own ports.
#
# Friend Houses ("serverless messaging and friend system between
# households", domovoi/friend_houses/) runs C tor in a container the core
# starts, `domovoi-tor`, on its own Docker network: `domovoi-fh`, subnet
# 172.31.254.0/24, bridge interface `domovoi-fh0`
# (domovoi/friend_houses/tor_runtime.py). Tor needs the internet, so the
# container can't go without a network; but a container on a plain Docker
# bridge can open connections to the host's own services (the dashboard on
# 6369, the core on 6370, Postgres on 6432, Ollama on 11434) and to the
# home network (spike S1; threat-model finding 22). A compromised tor
# daemon would then sit on the LAN with admin-capable ports in reach.
#
# These rules close that, and nothing else:
#
#   * INPUT: nothing from the domovoi-fh0 bridge to the host itself (that
#     traffic is the host's, so Docker's DOCKER-USER chain never sees it);
#   * DOCKER-USER: nothing from 172.31.254.0/24 to private, shared,
#     link-local, loopback or multicast addresses: the LAN, other
#     containers, the WSL or VM host behind a NAT gateway;
#   * the same for IPv6, should the network ever get IPv6.
#
# Tor relays have public addresses, so the internet still works. The rules
# live in one chain of their own, DOMOVOI-FH, which `apply` refills from
# scratch, so applying twice changes nothing.
#
#   * FORWARD: the same jump into DOMOVOI-FH, so the fence holds even
#     where nothing hooks DOCKER-USER into FORWARD (Docker's nftables
#     backend) (audit R6 FH-14).
#
# Run as root by domovoi-fh-firewall.service (scripts/linux/units/), which
# applies the rules BEFORE Docker starts (so no container is up without
# them after a reboot), restarts with Docker, and then keeps watching:
# `watch` re-applies whatever another tool flushed and refreshes the state
# file every minute, so the core can tell rules in place from a stale
# file. provision.sh and the update unit install both,
# like the file-cache trim (docs/LINUX_HOST.md, "Friend Houses' firewall").
# DOMOVOI_MANAGE_FH_FIREWALL=0 in /etc/default/domovoi-update leaves them
# to the administrator.
#
# After a successful apply it writes /run/domovoi-fh-firewall/applied
# (root's, world-readable): the core reads it to tell an admin whether the
# rules are in place before Friend Houses is turned on.
#
# Usage: fh-firewall.sh apply | remove | check | watch
#   check   exit 0 when every rule is in place, 1 when not.

set -uo pipefail

SUBNET=172.31.254.0/24
BRIDGE=domovoi-fh0
CHAIN=DOMOVOI-FH
STATE_DIR=${FH_FIREWALL_STATE_DIR:-/run/domovoi-fh-firewall}
BLOCK_V4=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4)
BLOCK_V6=(fc00::/7 fe80::/10 ::1/128 ff00::/8)
IPT=${IPTABLES:-iptables}
IP6T=${IP6TABLES:-ip6tables}
VERSION=1

log() { printf 'fh-firewall: %s\n' "$*"; }
die() { printf 'fh-firewall: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# fill_chain TOOL BLOCKED...: the DOMOVOI-FH chain, made if missing and
# refilled from scratch: drop each blocked destination, return otherwise.
fill_chain() {
  local tool=$1 net
  shift
  "$tool" -w -N "$CHAIN" 2>/dev/null || true
  "$tool" -w -F "$CHAIN" || return 1
  for net in "$@"; do
    "$tool" -w -A "$CHAIN" -d "$net" -j DROP || return 1
  done
  "$tool" -w -A "$CHAIN" -j RETURN
}

# ensure TOOL CHAIN RULE...: insert RULE at the top of CHAIN unless it is
# there already.
ensure() {
  local tool=$1 chain=$2
  shift 2
  "$tool" -w -C "$chain" "$@" 2>/dev/null || "$tool" -w -I "$chain" 1 "$@"
}

drop() {
  local tool=$1 chain=$2
  shift 2
  while "$tool" -w -D "$chain" "$@" 2>/dev/null; do :; done
}

apply_family() {
  local tool=$1 blocked_name=$2
  local -n blocked=$blocked_name
  # Docker makes DOCKER-USER when it starts; make it if Docker hasn't yet,
  # so the rules hold from the first moment the network exists.
  "$tool" -w -N DOCKER-USER 2>/dev/null || true
  fill_chain "$tool" "${blocked[@]}" || return 1
  ensure "$tool" DOCKER-USER -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN" || return 1
  ensure "$tool" INPUT -i "$BRIDGE" -j DROP || return 1
  ensure "$tool" FORWARD -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN" || return 1
}

check_family() {
  local tool=$1
  "$tool" -w -C INPUT -i "$BRIDGE" -j DROP 2>/dev/null || return 1
  "$tool" -w -C FORWARD -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN" 2>/dev/null || return 1
  "$tool" -w -C DOCKER-USER -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN" 2>/dev/null || return 1
  "$tool" -w -S "$CHAIN" >/dev/null 2>&1
}

remove_family() {
  local tool=$1
  drop "$tool" INPUT -i "$BRIDGE" -j DROP
  drop "$tool" FORWARD -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN"
  drop "$tool" DOCKER-USER -s "$( [ "$tool" = "$IPT" ] && echo "$SUBNET" || echo "::/0")" -i "$BRIDGE" -j "$CHAIN"
  "$tool" -w -F "$CHAIN" 2>/dev/null || true
  "$tool" -w -X "$CHAIN" 2>/dev/null || true
}

# The state file the core reads: which network is fenced, and when the
# rules were last seen in place (the core treats an old time as missing).
write_state() {
  install -d -m 0755 "$STATE_DIR" || return 1
  printf 'version=%s\nsubnet=%s\nbridge=%s\nchecked=%s\n' "$VERSION" "$SUBNET" "$BRIDGE" "$(date +%s)" \
    >"$STATE_DIR/applied.tmp" \
    && chmod 0644 "$STATE_DIR/applied.tmp" && mv -f "$STATE_DIR/applied.tmp" "$STATE_DIR/applied"
}

case ${1:-} in
  apply)
    have "$IPT" || die "iptables isn't installed: Friend Houses' Tor container can't be fenced off"
    apply_family "$IPT" BLOCK_V4 || die "the IPv4 rules could not be applied"
    if have "$IP6T" && "$IP6T" -w -L INPUT >/dev/null 2>&1; then
      apply_family "$IP6T" BLOCK_V6 || die "the IPv6 rules could not be applied"
    fi
    write_state || die "can't write $STATE_DIR/applied"
    log "Friend Houses' Tor network ($SUBNET on $BRIDGE) can reach the internet only"
    ;;
  watch)
    # The unit's main process: every minute, check; re-apply what's gone;
    # refresh the state file's time only while every rule is in place.
    every=${FH_FIREWALL_EVERY:-60}
    rounds=${FH_FIREWALL_ROUNDS:-0}   # tests: stop after this many (0: never)
    n=0
    while :; do
      if ! "$0" check >/dev/null 2>&1; then
        log "rules missing; applying them again"
        "$0" apply >/dev/null || rm -f "$STATE_DIR/applied"
      else
        write_state || true
      fi
      n=$((n + 1))
      [ "$rounds" -gt 0 ] && [ "$n" -ge "$rounds" ] && exit 0
      sleep "$every"
    done
    ;;
  remove)
    rm -f "$STATE_DIR/applied"
    have "$IPT" && remove_family "$IPT"
    have "$IP6T" && remove_family "$IP6T" 2>/dev/null
    log "Friend Houses' firewall rules removed"
    ;;
  check)
    have "$IPT" || exit 1
    check_family "$IPT" || exit 1
    if have "$IP6T" && "$IP6T" -w -L INPUT >/dev/null 2>&1; then check_family "$IP6T" || exit 1; fi
    exit 0
    ;;
  *)
    echo "usage: fh-firewall.sh apply | remove | check | watch" >&2
    exit 2
    ;;
esac
