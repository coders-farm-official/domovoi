#!/bin/sh
# TEST BUILDS ONLY (a stick made with debug=True): the journal, the first
# boot's log and this boot's Secure Boot facts on the first serial port, so
# the QEMU kit can read what happened without a login. Never on a
# household's stick.
stty -F /dev/ttyS0 115200 2>/dev/null
(
	i=0
	while [ "$i" -lt 90 ]; do
		if [ -f /run/domovoi-stick/boot.json ]; then
			echo "DOMOVOI-STICK: BOOT-JSON $(cat /run/domovoi-stick/boot.json)"
			echo "DOMOVOI-STICK: HELD $(apt-mark showhold 2>/dev/null | tr '\n' ' ')"
			break
		fi
		sleep 2
		i=$((i + 1))
	done
) >/dev/ttyS0 2>&1 &
journalctl -b -f -o short-monotonic >/dev/ttyS0 2>&1 &
exec tail -n 0 -F /var/log/domovoi-stick-firstboot.log >/dev/ttyS0 2>&1
