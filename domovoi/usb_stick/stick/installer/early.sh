#!/bin/sh
# Domovoi room stick: preseed/early_command (domovoi/usb_stick/), the first
# thing the installer runs after it has read the stick's answers.
#
# Only the stick's answers. Debian's installer takes a preseed URL from a
# DHCP server's boot-file name (network-preseed: "dhcp_preseed_url", once
# the network is up) and loads it over the stick's own answers: a rogue
# DHCP server on the network could then replace the ERASE page and erase
# the disk with no question asked (security review of wf/usb-stick, proven
# in QEMU). Two locks, before the installer loads any component from the
# stick:
#
# 1. network-preseed is recorded as installed (a version no release has),
#    so the installer never unpacks or runs it: nothing reads a URL from
#    DHCP at all.
# 2. udhcpc's script drops the boot-file name before Debian's script
#    records the lease (udhcpc-default.script), so there is none to read.
#
# erase-confirm.sh refuses if either ever fails (a preseed URL set, or
# network-preseed installed). Other ways in are the kernel command line
# (the stick's own menu) and the files the stick carries.
#
# DS_ROOT: the tests'. Test builds (debug-serial.sh present) also start the
# serial copier.
R=${DS_ROOT:-}
STICK_DIR=${DS_STICK_DIR:-/domovoi-stick}

if ! grep -q '^Package: network-preseed$' "$R/var/lib/dpkg/status" 2>/dev/null; then
	printf '\n%s\n%s\n%s\n%s\n%s\n' \
		"Package: network-preseed" \
		"Status: install ok installed" \
		"Version: 99:0+domovoi" \
		"Architecture: all" \
		"Description: switched off by the Domovoi room stick (no answers from the network)" \
		>>"$R/var/lib/dpkg/status"
fi

S=$R/etc/udhcpc/default.script
if [ -f "$S" ] && [ ! -e "$S.debian" ]; then
	mv "$S" "$S.debian"
	cp "$STICK_DIR/udhcpc-default.script" "$S"
	chmod 0755 "$S"
fi

if [ -f "$STICK_DIR/debug-serial.sh" ]; then
	sh "$STICK_DIR/debug-serial.sh"
fi
exit 0
