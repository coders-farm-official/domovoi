#!/bin/bash
# network-check.sh: what the installed Domovoi did about the home network
# (mac-installer Phase 4), read only. Run on the Mac, as an administrator:
#
#   sudo mac/tests/network-check.sh [OUT]
#
# OUT  a file for the report (default: stdout). Nothing is changed: pf, the
#      VM and the core are only asked. Every line is `key: value` or a block
#      under a `== name` heading, so two runs can be diffed (before / after
#      uninstall, Wi-Fi / Ethernet).
set -u
OUT=${1:-/dev/stdout}
[ "$(id -u)" = 0 ] || { echo "run it with sudo (pf and the VM's account need root)" >&2; exit 2; }
AS="/Library/Application Support/Domovoi"
H="$AS/bin/DomovoiHelper"
LIMA_HOME="$AS/lima"
L() { sudo -n -u _domovoi /usr/bin/env LIMA_HOME="$LIMA_HOME" HOME="$AS/home" PATH=/usr/bin:/bin:/usr/sbin:/sbin \
        "$AS/bin/lima/bin/limactl" shell --workdir / domovoi "$@" 2>&1; }
{
  echo "== when"; date -u +%Y-%m-%dT%H:%M:%SZ
  echo "== the Mac's network"
  route -n get default 2>/dev/null | awk '/interface:|gateway:/'
  for i in $(ifconfig -l); do
    a=$(ipconfig getifaddr "$i" 2>/dev/null) || continue
    echo "$i: $a mask $(ipconfig getoption "$i" subnet_mask 2>/dev/null) $(networksetup -listallhardwareports | grep -B1 "Device: $i\$" | head -n 1 | sed 's/Hardware Port: //')"
  done
  echo "== pf"
  pfctl -s info 2>/dev/null | head -n 1
  echo "references:"; pfctl -s References 2>/dev/null
  echo "anchors under com.apple:"; pfctl -s Anchors -a com.apple 2>/dev/null
  echo "com.apple/domovoi nat:"; pfctl -a com.apple/domovoi -s nat 2>/dev/null
  echo "com.apple/domovoi rules:"; pfctl -a com.apple/domovoi -s rules 2>/dev/null
  echo "com.apple/domovoi fence table (everything that isn't this Mac):"; pfctl -a com.apple/domovoi -t domovoi_elsewhere -T show 2>/dev/null
  echo "main ruleset translation (anchor points):"; pfctl -s nat 2>/dev/null
  echo "all anchors:"; pfctl -s Anchors -v 2>/dev/null
  # The VM's way out (vzNAT = vmnet shared mode): macOS's own NAT, expected
  # in an internet-sharing anchor, not in Domovoi's.
  for a in $(pfctl -s Anchors -v 2>/dev/null | grep -i sharing); do echo "nat in $a:"; pfctl -a "$a" -s nat 2>/dev/null; done
  echo "states to the VM:";pfctl -s states 2>/dev/null | grep -E '192\.168\.6[4-9]\.' | head -n 10
  echo "ip forwarding: $(sysctl -n net.inet.ip.forwarding)"
  echo "app firewall: $(/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>&1)"
  echo "pf.conf anchor points: $(grep -cE '^(rdr-)?anchor "com\.apple/\*"' /etc/pf.conf)"
  echo "== helper"
  launchctl print system/com.domovoi.mac.helper 2>/dev/null | awk -F' = ' '/^\t(state|pid|runs|last exit code) /'
  [ -x "$H" ] && "$H" network --json 2>&1
  echo "== domovoi.local"
  echo "this Mac's Bonjour name: $(scutil --get LocalHostName 2>/dev/null)"
  dscacheutil -q host -a name domovoi.local 2>/dev/null | head -n 4
  echo "== listeners (only 127.0.0.1 expected for Domovoi's own)"
  lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR==1 || /:(6369|6370|7676|8096|9467|11434|80[5-9][0-9]) /'
  if [ -x "$AS/bin/lima/bin/limactl" ]; then
    echo "== the VM"
    L ip -4 -o addr show
    L ip rule show
    echo "table 166:"; L ip route show table 166
    echo "default routes:"; L ip -4 route show default
    echo "fence:"; L sudo nft list table inet domovoi_vm_guard
    echo "lan.json:"; L cat /var/lib/domovoi-mac/lan.json
    echo "the core reads:"; L sudo -u domovoi sh -c 'cd /opt/domovoi && set -a && . domovoi/.env && set +a && .venv/bin/python -c "from domovoi import lan_address; print(lan_address.host_lan_ipv4())"'
    echo "client addresses the dashboard and core saw (last 15):"
    L sudo journalctl -u domovoi-web -u domovoi-core -n 400 --no-pager -o cat | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+ - "[A-Z]+ [^"]*"' | tail -n 15
    echo "media changes the core was told about (last 10):"
    L sudo journalctl -u domovoi-core -n 2000 --no-pager -o cat | grep 'host media' | tail -n 10
  fi
} >"$OUT"
