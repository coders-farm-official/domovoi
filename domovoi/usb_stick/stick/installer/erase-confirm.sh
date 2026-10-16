#!/bin/sh
# Domovoi room stick: partman/early_command (domovoi/usb_stick/).
#
# Runs inside Debian's installer just before the partitioner, i.e. before
# anything on any disk is touched. It picks the disk the room goes on,
# refuses the computers that can't be a room (no disk, a processor older
# than x86-64-v2, an Apple T2 Mac, no internet), each on a plain page with
# nothing changed, then asks the person to type ERASE, naming that disk.
# Anything else, three times, or Go Back: "Nothing was changed", power off.
#
# Which disk (lib.sh, ds_pick): never the stick itself, never a USB or
# removable drive or a memory card; of the rest, a solid-state one before a
# spinning one, then the largest.
. /usr/share/debconf/confmodule
. /domovoi-stick/lib.sh

LOG=/var/log/domovoi-stick.log
log() { echo "domovoi-stick: $*" >>"$LOG"; }
ds_log() { log "$@"; }
# Test builds only (/domovoi-stick/debug): markers on the serial port.
mark() { [ -e /domovoi-stick/debug ] && echo "DOMOVOI-STICK: $*" >/dev/ttyS0 2>/dev/null; return 0; }

debconf-loadtemplate domovoi-stick /domovoi-stick/templates
ROOM=$(head -n 1 /domovoi-stick/room-name 2>/dev/null)
[ -n "$ROOM" ] || ROOM="new"
WIFI=off
[ "$(cat /domovoi-stick/wifi 2>/dev/null)" = on ] && WIFI=on

stop() {  # stop TEMPLATE: say it, then power off (nothing was written)
	mark "REFUSAL $1"
	db_fset "$1" seen false
	db_input critical "$1" || true
	db_go || true
	log "stopped ($1); powering off"
	mark "STOPPED $1"
	poweroff -f
	exit 1
}

STICK=$(ds_stick_disk)
log "installer medium on disk ${STICK:-?}; Wi-Fi on the stick: $WIFI"
# shellcheck disable=SC2046
BEST=$(ds_pick "$STICK" $(list-devices disk 2>/dev/null))
[ -n "$BEST" ] || stop domovoi-stick/no-disk

missing=$(ds_cpu_missing)
if [ -n "$missing" ]; then
	log "CPU lacks $missing (not x86-64-v2)"
	mark "CPU-TOO-OLD $missing"
	stop domovoi-stick/cpu-too-old
fi

if ds_t2_mac; then
	stop domovoi-stick/t2-mac
fi

if [ -e /domovoi-stick/debug ]; then
	# Whether Wi-Fi drivers can load in this installer (the stick's Wi-Fi
	# switch, no-wifi.conf): a test build's marker only.
	modprobe cfg80211 >/dev/null 2>&1
	mark "CFG80211 rc=$? wifi=$WIFI"
fi

# No internet, no room: the satellite's packages come from Debian and PyPI
# at first boot. Stop here, before anything is erased, when Debian's mirror
# can't be reached (no cable; with Wi-Fi on the stick, a Wi-Fi chip the
# installer can't drive, typically Broadcom in older Macs and laptops).
# probe URL SECONDS: fetch it within SECONDS, or fail. busybox wget's -T is
# a read timeout only: a connect to a host that never answers sat for five
# minutes in the spike's QEMU run before the ERASE page showed.
probe() {
	wget -q -T "$2" -O /dev/null "$1" 2>/dev/null &
	pid=$! n=0
	while kill -0 "$pid" 2>/dev/null; do
		if [ "$n" -ge "$2" ]; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; return 1; fi
		sleep 1
		n=$((n + 1))
	done
	wait "$pid"
}
if ! probe http://deb.debian.org/debian/dists/trixie/Release 20; then
	log "no internet (deb.debian.org unreachable)"
	if [ "$WIFI" = on ]; then stop domovoi-stick/no-network-wifi; fi
	stop domovoi-stick/no-network
fi
# The household's server: a soft check (it may be off right now; the room
# keeps trying after the install), said on the ERASE page either way.
CORE=$(head -n 1 /domovoi-stick/core-url 2>/dev/null)
SERVER_NOTE="Your Domovoi server at $CORE answered."
if [ -n "$CORE" ] && ! probe "$CORE/v1/health" 8; then
	log "server $CORE did not answer"
	SERVER_NOTE="Note: your Domovoi server at $CORE did not answer just now. Is it on? This computer will keep trying after it is set up."
fi
mark "NETWORK-OK server_note=$SERVER_NOTE"

# Is this computer already a room? (The stick left in after the install
# starts the installer again on PCs that boot USB first.) Each partition of
# the chosen disk is mounted read-only without replaying its journal
# (noload), so this writes nothing; anything that isn't ext4 just fails.
OLDROOM=""
mkdir -p /tmp/domovoi-look
for p in "/sys/block/$BEST/$BEST"*; do
	[ -d "$p" ] || continue
	part=${p##*/}
	if mount -t ext4 -o ro,noload "/dev/$part" /tmp/domovoi-look 2>/dev/null; then
		OLDROOM=$(ds_room_name_in /tmp/domovoi-look)
		umount /tmp/domovoi-look 2>/dev/null || umount -l /tmp/domovoi-look 2>/dev/null
		if [ -n "$OLDROOM" ]; then
			log "/dev/$part holds the room \"$OLDROOM\""
			mark "ALREADY-A-ROOM /dev/$part"
			break
		fi
	fi
done

DISK=$(ds_disk_desc "$BEST")
log "chosen: $DISK"
mark "ERASE-PROMPT disk=/dev/$BEST"

FIRST=domovoi-stick/erase
[ -n "$OLDROOM" ] && FIRST=domovoi-stick/erase-room
tries=0
for q in "$FIRST" domovoi-stick/erase-again domovoi-stick/erase-again; do
	db_subst "$q" ROOM "$ROOM"
	db_subst "$q" OLDROOM "$OLDROOM"
	db_subst "$q" DISK "$DISK"
	db_subst "$q" SERVER "$SERVER_NOTE"
	db_set "$q" ""
	db_fset "$q" seen false
	db_input critical "$q" || true
	if ! db_go; then
		stop domovoi-stick/stopped
	fi
	db_get "$q"
	# Capitals asked for, any case taken: typing the word is the intent.
	ans=$(echo "$RET" | tr a-z A-Z | sed 's/^ *//; s/ *$//')
	if [ "$ans" = ERASE ]; then
		log "ERASE typed for /dev/$BEST"
		mark "ERASE-CONFIRMED /dev/$BEST"
		db_set partman-auto/disk "/dev/$BEST"
		db_fset partman-auto/disk seen true
		db_set grub-installer/bootdev "/dev/$BEST"
		db_fset grub-installer/bootdev seen true
		# UEFI: the Microsoft-signed shim and Debian-signed GRUB. With
		# install-recommends off (the answers file keeps the system small)
		# apt picks grub-efi-amd64-unsigned, grub-install then puts plain
		# GRUB on the disk with no shim, and the first boot with Secure
		# Boot on ends in "Access Denied" (the spike's first Secure Boot
		# QEMU install). pkgsel runs after this, before grub-installer.
		if [ -d /sys/firmware/efi ]; then
			db_get pkgsel/include
			db_set pkgsel/include "$RET grub-efi-amd64-signed shim-signed"
			log "UEFI: added grub-efi-amd64-signed shim-signed"
		fi
		exit 0
	fi
	tries=$((tries + 1))
	log "answer was not ERASE ($tries)"
	mark "NOT-ERASE $tries"
done
stop domovoi-stick/stopped
