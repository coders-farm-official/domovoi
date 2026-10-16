#!/bin/sh
# TEST ONLY (room stick kit): what an installed room knows about Secure
# Boot and Microsoft's 2023 certificates, and whether fwupd can add the 2023
# UEFI CA to the firmware's db (the update Microsoft signs with its 2011 KEK).
# Run by domovoi-stick-testhook.service; markers go to ttyS0.
PATH=/usr/sbin:/usr/bin:/sbin:/bin
echo "DOMOVOI-STICK: HOOK-START sb-check"
until ip -4 route show default | grep -q .; do sleep 2; done
mokutil --sb-state 2>&1 | sed 's/^/sb-state: /'
certs() {  # certs VAR: the Microsoft certificate names in that UEFI variable
	f=$(ls /sys/firmware/efi/efivars/"$1"-* 2>/dev/null | head -1)
	[ -n "$f" ] || { echo "(no $1)"; return; }
	grep -a -o -E "(Microsoft|Windows)[A-Za-z0-9 ]*(CA|PCA)[A-Za-z0-9 ]*20(11|23)" "$f" | sort -u | tr '\n' ';'
	echo
}
echo "DOMOVOI-STICK: DB-BEFORE $(certs db)"
echo "DOMOVOI-STICK: KEK $(certs KEK)"
if certs db | grep -q "UEFI CA 2023"; then
	echo "DOMOVOI-STICK: TRUSTS-2023 yes"
else
	echo "DOMOVOI-STICK: TRUSTS-2023 no"
fi
[ "${1:-}" = fwupd ] || [ -e /usr/local/lib/domovoi-stick-test/do-fwupd ] || { echo "DOMOVOI-STICK: HOOK-DONE"; exit 0; }

echo "DOMOVOI-STICK: FWUPD-INSTALL"
DEBIAN_FRONTEND=noninteractive apt-get update -qq
# fwupdmgr talks to the fwupd daemon over the system bus: a minimal system
# has no dbus until something asks for it (first try: "Failed to connect
# to daemon").
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends fwupd dbus ca-certificates >/dev/null 2>&1
systemctl start dbus.service fwupd.service 2>&1 | tail -2
fwupdmgr --version | head -3
fwupdmgr refresh --force 2>&1 | tail -3
fwupdmgr get-devices --no-unreported-check 2>&1 | grep -i -E -A6 "UEFI (db|dbx|KEK)" | head -40
echo "DOMOVOI-STICK: FWUPD-UPDATES"
fwupdmgr get-updates --no-unreported-check 2>&1 | head -40
echo "DOMOVOI-STICK: FWUPD-UPDATE"
fwupdmgr update -y --no-reboot-check --no-unreported-check 2>&1 | tail -20
echo "DOMOVOI-STICK: DB-AFTER $(certs db)"
echo "DOMOVOI-STICK: HOOK-DONE"
