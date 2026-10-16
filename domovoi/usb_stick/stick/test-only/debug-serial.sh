#!/bin/sh
# TEST BUILDS ONLY (a stick made with debug=True): preseed/early_command.
# Copies the installer's own log to the first serial port, so the QEMU runs
# can follow the install without a screen reader, and marks the build so
# erase-confirm.sh / late.sh write their markers there too.
#
# The copier runs in the background with every inherited descriptor
# closed: the installer waits for the command's output pipes (and debconf
# for its own descriptors) to close, so a child holding one hangs the boot
# (seen in the spike: a black screen right after this command).
touch /domovoi-stick/debug
echo "DOMOVOI-STICK: INSTALLER-START" >/dev/ttyS0 2>/dev/null
(
	exec </dev/null >/dev/null 2>&1 3>&- 4>&- 5>&- 6>&- 7>&- 8>&- 9>&-
	while [ ! -f /var/log/syslog ]; do sleep 1; done
	exec tail -f /var/log/syslog >/dev/ttyS0
) &
exit 0
