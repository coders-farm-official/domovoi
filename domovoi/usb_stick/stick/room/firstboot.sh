#!/bin/bash
# Domovoi room stick: the first boot (domovoi/usb_stick/).
#
# Root, from domovoi-stick-firstboot.service, its output on tty1. Installs
# the satellite exactly the way the Linux one-liner does, headless:
#
#   install-pc.sh --mode headless --yes --permissions automatic --power yes
#
# from the satellite bundle the household's server put on the stick (its
# SHA-256 is in stick.json, checked here first), with the room code the
# server baked in. install-pc.sh makes the server prove the fingerprint in
# that code before it writes anything. "automatic": the server may update
# and restart the satellite on its own, as on a Pi; this computer is a
# dedicated room, not someone's laptop. Until the server can be reached
# (no network yet, the server is off), this says why on the screen and
# tries again every minute.
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
umask 022

LIB=${FB_LIB:-/usr/local/lib/domovoi-stick}
STATE=${FB_STATE:-/var/lib/domovoi-stick}
LOG=${FB_LOG:-/var/log/domovoi-stick-firstboot.log}
PY=/usr/bin/python3

field() { $PY -I -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' "$LIB/stick.json" "$1"; }
ROOM=$(field room)
PAYLOAD=$(field payload)
BUNDLE_SHA=$(field bundle_sha256)
CORE=$(field core)
MADE=$(field made)

screen() {  # screen LINE...: the whole screen, plain words
	printf '\033[2J\033[H\n\n'
	printf '   Domovoi room stick\n\n'
	local line
	for line in "$@"; do printf '   %s\n' "$line"; done
	printf '\n'
}
stamp() { date '+%Y-%m-%d %H:%M:%S'; }
echo "$(stamp) first boot: room \"$ROOM\", server $CORE" >>"$LOG"
echo "DOMOVOI-STICK: FIRSTBOOT-START" >>"$LOG"

screen "Setting up this computer as the room \"$ROOM\"." "" \
	"Checking the files on this computer..."
if ! echo "$BUNDLE_SHA  $LIB/satellite.tar.gz" | sha256sum -c --quiet - >>"$LOG" 2>&1; then
	screen "The satellite files on this computer are damaged." "" \
		"Make a new stick in the dashboard (Settings -> Satellites)" \
		"and start again."
	echo "$(stamp) bundle digest mismatch" >>"$LOG"
	exit 1
fi
rm -rf "$STATE/bundle"
mkdir -p "$STATE/bundle"
tar -xzf "$LIB/satellite.tar.gz" -C "$STATE/bundle" --no-same-owner

# The network first. The installer writes the wired port as "allow-hotplug",
# which network-online.target does not wait for (the spike's first QEMU
# boot got "Network is unreachable" 7 s after power-on), and Wi-Fi takes
# its time too. Wait for an IPv4 default route, saying so on the screen (an
# IPv6 one alone is not enough: router advertisements arrive seconds before
# DHCP does). After 90 s go on regardless, for an IPv6-only home; the
# retries below cover the rest.
waited=0
until [ -n "$(ip -4 route show default 2>/dev/null)" ] || [ "$waited" -ge 90 ]; do
	if [ $((waited % 30)) = 0 ]; then
		screen "Setting up this computer as the room \"$ROOM\"." "" \
			"Waiting for the network... ($waited s)" "" \
			"If this takes more than a minute or two: is the network" \
			"cable plugged in, and is the router on?"
	fi
	sleep 2
	waited=$((waited + 2))
done
echo "$(stamp) network up after ${waited}s: $(ip -4 route show default | head -1)" >>"$LOG"

# The clock. An old PC's flat CMOS battery starts it in 2009 or so, and
# then nothing that checks a certificate (Debian's mirror over https, PyPI,
# the household's server) works. The stick was made at $MADE, so the time
# is at least that: set it forward if it is behind, then give the time
# client (systemd-timesyncd) up to a minute to get it exactly.
now=$(date +%s)
case $MADE in ''|*[!0-9]*) MADE=0 ;; esac
if [ "$now" -lt "$MADE" ]; then
	echo "$(stamp) the clock said $(date -d "@$now" '+%Y-%m-%d'); set to the stick's date" >>"$LOG"
	date -s "@$MADE" >/dev/null 2>&1 || true
fi
synced=0
for _ in $(seq 1 30); do
	if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then synced=1; break; fi
	sleep 2
done
echo "$(stamp) clock synchronised: $synced" >>"$LOG"

attempt=0
started=$(date +%s)
while :; do
	attempt=$((attempt + 1))
	screen "Setting up this computer as the room \"$ROOM\"." "" \
		"It downloads what it needs from the internet and asks" \
		"your Domovoi server at $CORE to let it in." \
		"This takes about 5 to 20 minutes. Leave it switched on." "" \
		"(attempt $attempt, started $(date -d "@$started" '+%H:%M'))"
	echo "$(stamp) install-pc.sh, attempt $attempt" >>"$LOG"
	bash "$STATE/bundle/satellite/scripts/install-pc.sh" --mode headless --yes \
		--permissions automatic --power yes --wait 0 "$PAYLOAD" >>"$LOG" 2>&1
	rc=$?
	echo "$(stamp) install-pc.sh exit $rc" >>"$LOG"
	echo "DOMOVOI-STICK: FIRSTBOOT-RC $rc" >>"$LOG"
	why2=""
	case $rc in
		0) break ;;
		3)
			if tail -n 3 "$LOG" | grep -q "could not reach"; then
				why="Can't reach your Domovoi server at $CORE."
				why2="Is it switched on, and is this computer on the same network?"
			else
				why="The server at $CORE did not prove it is the Domovoi"
				why2="server this stick was made by, so nothing was sent to it."
			fi ;;
		2) why="The stick's room code was refused (see the log)."; why2="Make a new stick in the dashboard." ;;
		*) why="Setting up did not finish (no internet yet?)." ;;
	esac
	screen "Not ready yet: the room \"$ROOM\"." "" "$why" "$why2" "" \
		"Trying again shortly. Last lines of the log:" "" \
		"$(tail -n 4 "$LOG" | cut -c1-70 | sed -n '1p')" \
		"$(tail -n 4 "$LOG" | cut -c1-70 | sed -n '2p')" \
		"$(tail -n 4 "$LOG" | cut -c1-70 | sed -n '3p')"
	# Quick retries first (the network or the server may just be coming up),
	# then once a minute.
	if [ "$attempt" -lt 5 ]; then sleep 15; else sleep 60; fi
done

touch "$STATE/installed"
rm -rf "$STATE/bundle"
echo "$(stamp) installed in $(( $(date +%s) - started )) s" >>"$LOG"
echo "DOMOVOI-STICK: FIRSTBOOT-DONE $(( $(date +%s) - started ))s" >>"$LOG"
screen "The room \"$ROOM\" is set up. Its code comes next..."
exit 0
