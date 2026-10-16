#!/bin/bash
# Room stick kit: erase-confirm.sh and lib.sh under the installer's OWN shell
# (Debian 13.7 netinst's busybox ash), against stand-ins for debconf and the
# installer's tools, over a made-up sysfs with real symlinks. The pytest
# twin (domovoi/tests/test_usb_stick_scripts.py) runs the same scenarios
# under bash --posix; this is the check that busybox reads them the same.
#
#   bash busybox_erase_check.sh      (in the kit's container, the ISO at /iso,
#                                     the repo at /repo; needs root for chroot)
set -euo pipefail
ISO=${ISO:-/iso/debian-13.7.0-amd64-netinst.iso}
REPO=${REPO:-/repo}
C=/tmp/di
rm -rf "$C"; mkdir -p "$C"
xorriso -osirrox on -indev "$ISO" -extract /install.amd/gtk/initrd.gz /tmp/di.gz >/dev/null 2>&1
(cd "$C" && zcat /tmp/di.gz | cpio -idm --quiet 2>/dev/null) || true
[ -x "$C/bin/busybox" ] || { echo "no busybox in the installer's initrd"; exit 2; }
I=$REPO/domovoi/usb_stick/stick/installer
mkdir -p "$C/k/stick" "$C/k/bin"
cp "$I/lib.sh" "$I/templates" "$I/erase-confirm.sh" "$I/early.sh" "$I/udhcpc-default.script" "$C/k/stick/"
echo Kitchen >"$C/k/stick/room-name"; echo http://192.168.0.117:6370 >"$C/k/stick/core-url"; echo off >"$C/k/stick/wifi"

cat >"$C/k/confmodule" <<'SH'
DC=$SB/debconf
mkdir -p "$DC"
_k() { echo "$1" | tr '/' '_'; }
db_get() { RET=$(cat "$DC/$(_k "$1").v" 2>/dev/null); return 0; }
db_set() { printf '%s' "$2" >"$DC/$(_k "$1").v"; echo "set $1=$2" >>"$SB/calls"; return 0; }
db_fset() { [ "$2" = seen ] && printf '%s' "$3" >"$DC/$(_k "$1").seen"; return 0; }
db_subst() { echo "subst $1 $2 $3" >>"$SB/calls"; return 0; }
db_input() {
	if [ "$(cat "$DC/$(_k "$2").seen" 2>/dev/null)" = true ]; then echo "skipped $2" >>"$SB/calls"; return 30; fi
	echo "$2" >"$SB/shown"; echo "shown $2" >>"$SB/calls"; return 0
}
db_go() {
	[ -s "$SB/shown" ] || return 0
	q=$(cat "$SB/shown"); : >"$SB/shown"
	a=$(head -n 1 "$SB/answers" 2>/dev/null); sed -i '1d' "$SB/answers" 2>/dev/null
	case $a in "<back>") return 30 ;; "<enter>") ;; *) printf '%s' "$a" >"$DC/$(_k "$q").v" ;; esac
	printf true >"$DC/$(_k "$q").seen"; return 0
}
SH
w() { printf '#!/bin/sh\n%s\n' "$2" >"$C/k/bin/$1"; chmod +x "$C/k/bin/$1"; }
w list-devices 'echo /dev/sda; echo /dev/sdb; echo /dev/nvme1n1'
w wget '[ -e "$SB/internet" ]'
w mount 'exit 32'
w umount 'exit 0'
w poweroff 'echo poweroff >>"$SB/calls"; touch "$SB/poweroff"; [ -e "$SB/returns" ] && exit 0; kill -9 $PPID'
w sleep '[ -e "$SB/poweroff" ] || exit 0; echo sleep >>"$SB/calls"; [ "$(grep -c ^sleep "$SB/calls")" -ge 3 ] && { touch "$SB/hung"; kill -9 $PPID; }; exit 0'
w debconf-loadtemplate 'exit 0'
w modprobe 'exit 0'
w blkid 'for a; do d=$a; done; [ -f "$SB/blkid-${d##*/}" ] && cat "$SB/blkid-${d##*/}"; exit 0'

# The made-up computer: an internal HDD, the stick on USB, a Thunderbolt NVMe.
R=$C/k/root
mkdir -p "$R/sys/block" "$R/proc"
disk() {  # NAME DEVPATH GB ROT REM
	d=$R/sys/devices/$2/block/$1
	mkdir -p "$d/queue" "$d/device" "$d/${1}1"
	echo $(($3 * 2000000)) >"$d/size"; echo "$4" >"$d/queue/rotational"; echo "$5" >"$d/removable"; echo 0 >"$d/ro"
	echo "Model $1" >"$d/device/model"; echo ATA >"$d/device/vendor"
	ln -s "../devices/$2/block/$1" "$R/sys/block/$1"
}
disk sda pci0000:00/0000:00:17.0/ata1/host0/target0:0:0/0:0:0:0 1000 1 0
disk sdb pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host6/target6:0:0/6:0:0:0 32 1 1
disk nvme1n1 pci0000:00/0000:00:07.0/0000:20:00.0/0000:22:00.0/nvme/nvme1 2000 0 0
echo removable >"$R/sys/devices/pci0000:00/0000:00:07.0/0000:20:00.0/removable"
echo "/dev/sdb1 /cdrom iso9660 ro 0 0" >"$R/proc/mounts"
printf 'processor\t: 0\nflags\t\t: fpu sse sse2 pni ssse3 cx16 sse4_1 sse4_2 popcnt lahf_lm\n' >"$R/proc/cpuinfo"

pass=0 fail=0
run() {  # run NAME "ANSWERS" EXPECT [setup]
	SB=$C/k/sb-$1
	rm -rf "$SB"; mkdir -p "$SB"
	printf '%s\n' $2 | sed 's/_/ /g' >"$SB/answers"
	touch "$SB/internet"
	[ -z "${4:-}" ] || eval "$4"
	(chroot "$C" /usr/bin/env -i PATH=/k/bin:/bin:/sbin:/usr/bin:/usr/sbin SB="/k/sb-$1" DS_ROOT=/k/root \
		DS_STICK_DIR=/k/stick DS_CONFMODULE=/k/confmodule DS_LOG="/k/sb-$1/log" DS_LOOK="/k/sb-$1/look" \
		/bin/sh /k/stick/erase-confirm.sh >/dev/null 2>&1 || true) 2>/dev/null
	got=$(cat "$SB/calls" 2>/dev/null | tr '\n' '|')
	if echo "$got" | grep -q -- "$3"; then pass=$((pass + 1)); echo "ok   $1"; else fail=$((fail + 1)); echo "FAIL $1: $got"; fi
	case $1 in erase|lower|lvm-alone) ;; *)
		if echo "$got" | grep -q "set partman"; then fail=$((fail + 1)); echo "FAIL $1: partman answered"; fi ;;
	esac
}
run erase "ERASE" "set partman-auto/disk=/dev/sda|.*set partman/confirm=true"
run lower "erase" "set partman-auto/disk=/dev/sda"
run wrong "nope ERASED x" "shown domovoi-stick/stopped|poweroff"
run back "<back>" "shown domovoi-stick/stopped|poweroff"
run empty "<enter> <enter> <enter>" "shown domovoi-stick/stopped|poweroff"
run noinet "ERASE" "shown domovoi-stick/no-network|poweroff" 'rm -f "$SB/internet"'
run returns "x x x" "poweroff|sleep|sleep|sleep" 'touch "$SB/returns"'
run tampered "ERASE" "shown domovoi-stick/tampered" 'mkdir -p "$SB/debconf"; printf http://x/e.cfg >"$SB/debconf/preseed_url.v"'
# A volume group from the chosen disk onto the Thunderbolt one; a RAID with it.
run joined-ext "ERASE" "subst domovoi-stick/joined DISKS .*(/dev/sda).*(/dev/nvme1n1)" \
	'echo LVM2_member >"$SB/blkid-sda1"; echo LVM2_member >"$SB/blkid-nvme1n1"'
run joined-raid "ERASE" "subst domovoi-stick/joined DISKS .*(/dev/sda).*(/dev/nvme1n1)" \
	'echo linux_raid_member >"$SB/blkid-sda"; echo linux_raid_member >"$SB/blkid-nvme1n11"'
run lvm-alone "ERASE" "set partman-auto/disk=/dev/sda" 'echo LVM2_member >"$SB/blkid-sda1"'
echo "busybox erase-confirm check: $pass passed, $fail failed ($("$C/bin/busybox" 2>&1 | head -1))"
[ "$fail" = 0 ]
