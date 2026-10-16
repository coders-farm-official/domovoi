#!/bin/sh
# Domovoi room stick: partman/early_command (domovoi/usb_stick/).
#
# Runs inside Debian's installer just before the partitioner, i.e. before
# anything on any disk is touched. It picks the disk the room goes on,
# refuses the computers that can't be a room (no disk, a processor older
# than x86-64-v2, an Apple T2 Mac, no internet, disks joined by LVM or
# RAID), each on a plain page with nothing changed, then asks the person to
# type ERASE, naming that disk. Anything else, three times, or Go Back:
# "Nothing was changed", power off.
#
# Fails closed. A refusal never returns to the partitioner (stop() powers
# off, and waits for ever if the firmware ignores that), and the answers
# that let the partitioner write without asking (partman's confirmations)
# are given here, after ERASE, never in the answers file: if this script
# ends any other way (an error, a mistake in it), Debian's partitioner asks
# the person itself.
#
# Which disk (lib.sh, ds_pick): never the stick itself, never a removable,
# USB, Thunderbolt or memory-card disk; of the rest, a solid-state one
# before a spinning one, then the largest.
#
# DS_* variables are the tests' (domovoi/tests/test_usb_stick_installer.py,
# with stand-ins for debconf and the installer's tools); in the installer
# they are unset and every path is the installer's own.
DS_STICK_DIR=${DS_STICK_DIR:-/domovoi-stick}
. "${DS_CONFMODULE:-/usr/share/debconf/confmodule}"
. "$DS_STICK_DIR/lib.sh"

LOG=${DS_LOG:-/var/log/domovoi-stick.log}
LOOK=${DS_LOOK:-/tmp/domovoi-look}
log() { echo "domovoi-stick: $*" >>"$LOG"; }
ds_log() { log "$@"; }
# Test builds only (/domovoi-stick/debug): markers on the serial port.
mark() { [ -e "$DS_STICK_DIR/debug" ] && { echo "DOMOVOI-STICK: $*" >/dev/ttyS0; } 2>/dev/null || true; return 0; }

debconf-loadtemplate domovoi-stick "$DS_STICK_DIR/templates"
ROOM=$(head -n 1 "$DS_STICK_DIR/room-name" 2>/dev/null)
[ -n "$ROOM" ] || ROOM="new"
WIFI=off
[ "$(cat "$DS_STICK_DIR/wifi" 2>/dev/null)" = on ] && WIFI=on

stop() {  # stop TEMPLATE [KEY VALUE]: say it, then power off. Never returns.
	mark "REFUSAL $1"
	[ -n "${2:-}" ] && db_subst "$1" "$2" "$3"
	db_fset "$1" seen false
	db_input critical "$1" || true
	db_go || true
	log "stopped ($1); powering off"
	mark "STOPPED $1"
	poweroff -f
	# Never back to the partitioner, whatever happened to the power-off
	# (firmware that ignores it): nothing after this may run.
	log "the power-off returned; waiting here"
	mark "POWEROFF-RETURNED"
	while :; do sleep 3600; done
}

# Answers only the stick gives. Debian's network preseeding (a DHCP
# server's boot-file name as a preseed URL) is switched off by early.sh; if
# it ran anyway, or anything named a preseed URL, someone else's answers
# are in play: stop.
url=""
db_get preseed/url && url=$RET
if [ -n "$url" ] || [ -e "$DS_ROOT/var/lib/dpkg/info/network-preseed.postinst" ]; then
	log "answers from the network: preseed/url '$url'"
	mark "TAMPERED"
	stop domovoi-stick/tampered
fi

STICK=$(ds_stick_disk)
log "installer medium on disk ${STICK:-?}; Wi-Fi on the stick: $WIFI"
DISKS=$(list-devices disk 2>/dev/null)
# shellcheck disable=SC2086
BEST=$(ds_pick "$STICK" $DISKS)
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

# Disks joined by LVM or RAID: the partitioner's clean-up of the chosen
# disk would reach the others. Refused, naming every one, when the chosen
# disk is one of them and another of this computer's disks is too.
# shellcheck disable=SC2086
JOINED=$(ds_joined $(ds_internal "$STICK" $DISKS))
case " $(echo $JOINED) " in
	*" $BEST "*)
		if [ "$(echo "$JOINED" | wc -w)" -gt 1 ]; then
			names=""
			for d in $JOINED; do names="${names:+$names; }$(ds_disk_desc "$d")"; done
			log "joined disks: $names"
			mark "JOINED $JOINED"
			stop domovoi-stick/joined DISKS "$names"
		fi ;;
esac

if [ -e "$DS_STICK_DIR/debug" ]; then
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
CORE=$(head -n 1 "$DS_STICK_DIR/core-url" 2>/dev/null)
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
mkdir -p "$LOOK"
for p in "$DS_ROOT/sys/block/$BEST/$BEST"*; do
	[ -d "$p" ] || continue
	part=${p##*/}
	if mount -t ext4 -o ro,noload "/dev/$part" "$LOOK" 2>/dev/null; then
		OLDROOM=$(ds_room_name_in "$LOOK")
		umount "$LOOK" 2>/dev/null || umount -l "$LOOK" 2>/dev/null
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
	# Nothing typed before counts: an empty field, asked afresh.
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
		# The partitioner's own confirmations, answered now and only now.
		for kv in "partman-auto/purge_lvm_from_device true" "partman-lvm/device_remove_lvm true" \
		          "partman-md/device_remove_md true" "partman-lvm/confirm true" \
		          "partman-lvm/confirm_nooverwrite true" "partman-partitioning/confirm_write_new_label true" \
		          "partman/choose_partition finish" "partman/confirm true" "partman/confirm_nooverwrite true"; do
			db_set "${kv%% *}" "${kv#* }"
			db_fset "${kv%% *}" seen true
		done
		# UEFI: the Microsoft-signed shim and Debian-signed GRUB. With
		# install-recommends off (the answers file keeps the system small)
		# apt picks grub-efi-amd64-unsigned, grub-install then puts plain
		# GRUB on the disk with no shim, and the first boot with Secure
		# Boot on ends in "Access Denied" (the spike's first Secure Boot
		# QEMU install). pkgsel runs after this, before grub-installer.
		if [ -d "$DS_ROOT/sys/firmware/efi" ]; then
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
