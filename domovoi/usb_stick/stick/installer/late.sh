#!/bin/sh
# Domovoi room stick: preseed/late_command (domovoi/usb_stick/).
#
# Debian is installed on /target. Copy our files from the stick into it and
# set up the first boot: the unit that installs the satellite (the same
# install-pc.sh --mode headless the Linux one-liner runs), the console page
# that shows the room and its approval code, the per-boot Secure Boot check,
# security updates with a night-time restart, and on UEFI a frozen boot
# loader. Nothing here needs the network; the satellite itself is installed
# at first boot, by systemd.
set -e
LOG=/var/log/domovoi-stick.log
log() { echo "domovoi-stick late: $*" >>"$LOG"; }
mark() { [ -e /domovoi-stick/debug ] && echo "DOMOVOI-STICK: $*" >/dev/ttyS0 2>/dev/null; return 0; }
mark "LATE-START"

SRC=/cdrom/domovoi
T=/target
LIB=$T/usr/local/lib/domovoi-stick

mkdir -p "$LIB/sounds" "$T/var/lib/domovoi-stick"
cp "$SRC/stick.json" "$SRC/satellite.tar.gz" "$LIB/"
for f in firstboot.sh console.py boot-check.sh; do
	cp "$SRC/room/$f" "$LIB/$f"
done
# The room's name as plain text: the stick's ERASE page reads it to say
# "this computer is already the room ..." if the stick is started again.
cp /domovoi-stick/room-name "$LIB/room-name"
for w in "$SRC"/sounds/*.wav; do
	[ -f "$w" ] && cp "$w" "$LIB/sounds/"
done
chmod 0755 "$LIB" "$LIB/sounds" "$LIB/firstboot.sh" "$LIB/boot-check.sh"
chmod 0644 "$LIB/stick.json" "$LIB/satellite.tar.gz" "$LIB/console.py" "$LIB/room-name"
chmod 0644 "$LIB"/sounds/* 2>/dev/null || true
chmod 0755 "$T/var/lib/domovoi-stick"
# The speaker line for the approval code: the satellite runs
# /usr/local/sbin/domovoi-status say-code <digits> when it waits for
# approval, as on a Pi card. Root's file, run by the satellite's own
# account with that account's rights: not a root helper (no sudo, no
# setuid), it only plays the stick's clips with aplay.
install -o root -g root -m 0755 "$SRC/room/domovoi-status" "$T/usr/local/sbin/domovoi-status"
log "copied $(ls "$LIB" | tr '\n' ' ')"

for u in "$SRC"/room/units/*.service; do
	cp "$u" "$T/etc/systemd/system/"
	chmod 0644 "$T/etc/systemd/system/$(basename "$u")"
	in-target systemctl enable "$(basename "$u")" >>"$LOG" 2>&1
done
# tty1 is the room's screen: no login prompt over it (root is locked
# anyway; tty2-6 keep theirs for whoever needs to look later).
in-target systemctl mask getty@tty1.service >>"$LOG" 2>&1

# Security updates by themselves (pkgsel's update policy turned
# unattended-upgrades on), and a restart at night when one needs it (a new
# kernel). The boot loader's packages are never among them: see below.
install -o root -g root -m 0644 "$SRC/room/52domovoi-stick" "$T/etc/apt/apt.conf.d/52domovoi-stick"

# Secure Boot: freeze the boot loader the stick installed (owner decision
# 2026-10-15). Microsoft now signs new shims with its 2023 certificate
# only, and a PC whose firmware never got that certificate would stop
# starting the day an update brought one, with nobody at a keyboard. So on
# UEFI the shim and GRUB packages are held at what this install put on the
# disk (apt-mark hold, and the unattended-upgrades blacklist above), and
# fwupd is not installed: nothing changes the firmware's certificates from
# here. Kernels keep updating (Debian signs them; this shim checks them).
if [ -d /sys/firmware/efi ]; then
	held=""
	for p in shim-signed shim-signed-common shim-unsigned shim-helpers-amd64-signed \
	         grub-efi-amd64-signed grub-efi-amd64-bin grub-efi-amd64 grub-common grub2-common; do
		if in-target dpkg-query -W -f '${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
			held="$held $p"
		fi
	done
	if [ -n "$held" ]; then
		# shellcheck disable=SC2086
		in-target apt-mark hold $held >>"$LOG" 2>&1
		echo "$held" | tr ' ' '\n' | sed '/^$/d' >"$T/var/lib/domovoi-stick/held-packages"
		chmod 0644 "$T/var/lib/domovoi-stick/held-packages"
		log "boot loader held:$held"
		mark "BOOTLOADER-HELD$held"
	fi
fi

# Large console text for the code (Terminus 16x32: ~60 columns on 1024x768).
if [ -f "$T/etc/default/console-setup" ]; then
	sed -i -e 's/^FONTFACE=.*/FONTFACE="TerminusBold"/' -e 's/^FONTSIZE=.*/FONTSIZE="16x32"/' \
		"$T/etc/default/console-setup"
fi

# Boot straight in, quietly.
if [ -f "$T/etc/default/grub" ]; then
	sed -i -e 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=1/' \
		-e 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3"/' \
		"$T/etc/default/grub"
	in-target update-grub >>"$LOG" 2>&1 || log "update-grub failed"
fi

# Belt and braces: root stays locked even if the answers file's hash was
# not taken as a locked password.
in-target passwd -l root >>"$LOG" 2>&1 || true

# Test builds only: the journal on the serial port for the QEMU runs.
if [ -e /domovoi-stick/debug ] && [ -f "$SRC/test-only/domovoi-stick-debug.service" ]; then
	cp "$SRC/test-only/domovoi-stick-debug.service" "$T/etc/systemd/system/"
	in-target systemctl enable domovoi-stick-debug.service >>"$LOG" 2>&1 || true
fi

cp "$LOG" "$T/var/log/domovoi-stick-install.log" 2>/dev/null || true
log "done"
mark "LATE-DONE"
