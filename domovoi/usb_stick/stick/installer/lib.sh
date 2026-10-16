# Domovoi room stick: the checks behind the ERASE page (sourced by
# erase-confirm.sh inside Debian's installer).
#
# Plain functions over /sys and /proc, read under $DS_ROOT (empty in the
# installer; domovoi/tests/test_usb_stick_installer.py points it at a
# made-up tree), so which disk is picked and what is refused can be tested
# away from the installer. POSIX sh: the installer's shell is busybox ash.
#
# Nothing here writes to a disk.

DS_ROOT=${DS_ROOT:-}

# The smallest disk a room goes on (GB, 10^9 bytes): an installed room uses
# about 1.6 GB, plus 1 GB of swap and, on UEFI, a 1 GB EFI partition.
DS_MIN_GB=8

# What the wake-word stack's NumPy needs (x86-64-v2: SSSE3, SSE4.1, SSE4.2,
# POPCNT, CMPXCHG16B, LAHF/SAHF), as /proc/cpuinfo spells it.
DS_CPU_FLAGS="ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm"

ds_log() { :; }  # erase-confirm.sh replaces this with its own

# ds_disk_of DEV: the whole disk a block device is on (/dev/sdb1 -> sdb,
# /dev/nvme0n1p2 -> nvme0n1, /dev/sdb -> sdb). A partition shows up as a
# directory of its disk in /sys/block.
ds_disk_of() {
	_b=${1##*/}
	for _d in "$DS_ROOT"/sys/block/*; do
		if [ -d "$_d/$_b" ]; then
			echo "${_d##*/}"
			return 0
		fi
	done
	echo "$_b"
}

# ds_stick_disk: the disk the installer was started from (what /cdrom is
# mounted from), or nothing.
ds_stick_disk() {
	_src=$(awk '$2 == "/cdrom" { print $1; exit }' "$DS_ROOT/proc/mounts" 2>/dev/null || true)
	[ -n "$_src" ] && ds_disk_of "$_src"
	return 0
}

# ds_gb NAME: the disk's size in GB (10^9 bytes, rounded down).
ds_gb() {
	_sectors=$(cat "$DS_ROOT/sys/block/$1/size" 2>/dev/null || echo 0)
	case $_sectors in ''|*[!0-9]*) _sectors=0 ;; esac
	echo $((_sectors / 2 / 1000 / 1000))
}

# ds_external NAME: true when the disk hangs off a port meant for things
# that are plugged in and out: anywhere under USB, or behind a PCI port the
# kernel marks external-facing (Thunderbolt, USB4: an NVMe enclosure there
# has no "usb" in its path). Each ancestor's own "removable" attribute says
# so ("removable"; the block device's own file, 0 or 1, is read apart).
ds_external() {
	_real=$(readlink -f "$DS_ROOT/sys/block/$1" 2>/dev/null)
	case $_real in */usb[0-9]*) return 0 ;; esac
	_p=${_real%/*}
	while [ -n "$_p" ] && [ "$_p" != "${_p%/*}" ]; do
		[ "$(cat "$_p/removable" 2>/dev/null)" = removable ] && return 0
		_p=${_p%/*}
	done
	return 1
}

# ds_internal_ok STICK NAME: true when NAME is one of this computer's own
# disks the room could go on (any size): not the stick, not removable, not
# external (ds_external), not a memory card, not read-only. Says why not
# through ds_log.
ds_internal_ok() {
	_sys=$DS_ROOT/sys/block/$2
	[ -d "$_sys" ] || return 1
	if [ -n "$1" ] && [ "$2" = "$1" ]; then ds_log "skip $2: the stick"; return 1; fi
	if [ "$(cat "$_sys/removable" 2>/dev/null)" = 1 ]; then ds_log "skip $2: removable"; return 1; fi
	if ds_external "$2"; then ds_log "skip $2: on USB or an external port"; return 1; fi
	# A memory card left in the laptop's reader is solid-state and would
	# win in ds_pick; built-in eMMC (cheap laptops' only disk) says MMC.
	case $2 in
		mmcblk*)
			if [ "$(cat "$_sys/device/type" 2>/dev/null)" != MMC ]; then
				ds_log "skip $2: a memory card"; return 1
			fi ;;
	esac
	if [ "$(cat "$_sys/ro" 2>/dev/null)" = 1 ]; then ds_log "skip $2: read-only"; return 1; fi
	return 0
}

# ds_internal STICK DISK...: the names of this computer's own disks.
ds_internal() {
	_st=$1
	shift
	for _dev in "$@"; do
		ds_internal_ok "$_st" "${_dev##*/}" && echo "${_dev##*/}"
	done
	return 0
}

# ds_pick STICK DISK...: the disk the room goes on, or nothing. Of this
# computer's own disks (ds_internal_ok) of at least $DS_MIN_GB, a
# solid-state disk before a spinning one, then the largest.
ds_pick() {
	_stick=$1
	shift
	_best="" _best_score=-1
	for _dev in "$@"; do
		_name=${_dev##*/}
		ds_internal_ok "$_stick" "$_name" || continue
		_sys=$DS_ROOT/sys/block/$_name
		_gb=$(ds_gb "$_name")
		if [ "$_gb" -lt "$DS_MIN_GB" ]; then ds_log "skip $_name: $_gb GB is too small"; continue; fi
		_ssd=0
		[ "$(cat "$_sys/queue/rotational" 2>/dev/null)" = 0 ] && _ssd=1
		_score=$((_ssd * 100000000 + _gb))
		ds_log "candidate $_name: $_gb GB ssd=$_ssd"
		if [ "$_score" -gt "$_best_score" ]; then _best=$_name _best_score=$_score; fi
	done
	echo "$_best"
}

# ds_others STICK DISK...: the names of every disk but the stick, plugged
# in or not (USB, Thunderbolt, eSATA, memory cards: all of them).
ds_others() {
	_st=$1
	shift
	for _dev in "$@"; do
		[ "${_dev##*/}" = "$_st" ] || echo "${_dev##*/}"
	done
	return 0
}

# ds_joined DISK...: of these disks, the ones that hold a piece of storage
# joined across disks (an LVM physical volume, a RAID member), on the disk
# itself or one of its partitions, as blkid reads it. Erasing one of those
# would break, or be spread into, the others: the stick refuses (erase-
# confirm.sh) when the chosen disk is one and another disk is too, any
# disk but the stick (a volume group can reach onto a USB disk). blkid
# doesn't say which group a member is in, so two unrelated ones are refused
# too (a known limit: the guide says how to wipe one first).
ds_joined() {
	for _d in "$@"; do
		for _n in "$_d" $(cd "$DS_ROOT/sys/block/$_d" 2>/dev/null && ls -d "$_d"* 2>/dev/null); do
			case $(blkid -o value -s TYPE "/dev/$_n" 2>/dev/null) in
				LVM2_member|*_raid_member)
					echo "$_d"
					break ;;
			esac
		done
	done
	return 0
}

# ds_cpu_missing: the first x86-64-v2 feature this processor lacks, or
# nothing when it has them all.
ds_cpu_missing() {
	_flags=" $(grep -m1 '^flags' "$DS_ROOT/proc/cpuinfo" 2>/dev/null | cut -d: -f2) "
	for _f in $DS_CPU_FLAGS; do
		case $_flags in
			*" $_f "*) ;;
			*) echo "$_f"; return 0 ;;
		esac
	done
	return 0
}

# ds_t2_mac: true on an Intel Mac with Apple's T2 chip (2018-2020), which
# shows up as PCI 106b:1801 (bridge) or 106b:1802 (secure enclave).
ds_t2_mac() {
	for _p in "$DS_ROOT"/sys/bus/pci/devices/*; do
		[ "$(cat "$_p/vendor" 2>/dev/null)" = 0x106b ] || continue
		case $(cat "$_p/device" 2>/dev/null) in
			0x1801|0x1802) ds_log "Apple T2 at $_p"; return 0 ;;
		esac
	done
	return 1
}

# ds_disk_desc NAME: "Samsung SSD 860, 256 GB (/dev/sda)" for the ERASE page.
ds_disk_desc() {
	_sys=$DS_ROOT/sys/block/$1
	_model=$(cat "$_sys/device/model" 2>/dev/null | sed 's/  */ /g; s/^ //; s/ $//')
	_vendor=$(cat "$_sys/device/vendor" 2>/dev/null | sed 's/  */ /g; s/^ //; s/ $//')
	case $_model in
		"$_vendor"*) _desc=$_model ;;
		*) _desc="$_vendor $_model" ;;
	esac
	_desc=$(echo "$_desc" | sed 's/^ *//; s/ *$//')
	[ -n "$_desc" ] || _desc="disk $1"
	echo "$_desc, $(ds_gb "$1") GB (/dev/$1)"
}

# ds_room_name_in DIR: the room an installed room stick made of this
# computer, from the root file system mounted at DIR, or nothing. One line
# of plain text (late.sh writes it), at most 256 bytes, control
# characters dropped.
ds_room_name_in() {
	_f=$1/usr/local/lib/domovoi-stick/room-name
	[ -f "$_f" ] || return 0
	head -n 1 "$_f" 2>/dev/null | tr -d '\000-\037\177' | cut -c1-256
}
