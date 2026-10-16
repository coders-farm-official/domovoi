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

# ds_pick STICK DISK...: the disk the room goes on, or nothing. Never the
# stick itself, never a removable or USB disk (another stick, a backup
# drive), never a read-only one or one under $DS_MIN_GB; of the rest, a
# solid-state disk before a spinning one, then the largest. Says why each
# one was left out through ds_log.
ds_pick() {
	_stick=$1
	shift
	_best="" _best_score=-1
	for _dev in "$@"; do
		_name=${_dev##*/}
		_sys=$DS_ROOT/sys/block/$_name
		[ -d "$_sys" ] || continue
		if [ -n "$_stick" ] && [ "$_name" = "$_stick" ]; then ds_log "skip $_name: the stick"; continue; fi
		if [ "$(cat "$_sys/removable" 2>/dev/null)" = 1 ]; then ds_log "skip $_name: removable"; continue; fi
		case $(readlink -f "$_sys" 2>/dev/null) in
			*/usb[0-9]*) ds_log "skip $_name: on USB"; continue ;;
		esac
		# A memory card left in the laptop's reader is solid-state and would
		# win below; built-in eMMC (cheap laptops' only disk) says MMC.
		case $_name in
			mmcblk*)
				if [ "$(cat "$_sys/device/type" 2>/dev/null)" != MMC ]; then
					ds_log "skip $_name: a memory card"; continue
				fi ;;
		esac
		if [ "$(cat "$_sys/ro" 2>/dev/null)" = 1 ]; then ds_log "skip $_name: read-only"; continue; fi
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
