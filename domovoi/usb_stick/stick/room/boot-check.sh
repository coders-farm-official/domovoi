#!/bin/sh
# Domovoi room stick: what this computer's firmware says about Secure Boot,
# every boot (domovoi-stick-boot-check.service, root, before the satellite).
#
# Reads only: /sys/firmware/efi (the SecureBoot variable, and the
# certificates in the db variable) and the stick's list of held boot-loader
# packages. Writes one file, /run/domovoi-stick/boot.json (tmpfs, so it is
# this boot's), which the satellite reads and reports in its health; the
# dashboard then warns when Secure Boot is on and the firmware lacks
# Microsoft's 2023 certificate (docs/SPARE_DEVICE_SATELLITES.md, "Old
# computer"). Changes nothing on the firmware. Under $BC_ROOT for the tests.
set -u
PATH=/usr/sbin:/usr/bin:/sbin:/bin
R=${BC_ROOT:-}
OUT=${BC_OUT:-/run/domovoi-stick/boot.json}
EFI=$R/sys/firmware/efi
GLOBAL=8be4df61-93ca-11d2-aa0d-00e098032b8c
SECDB=d719b2cb-3d3a-4596-a3bc-dad00e67656f

firmware=bios secure_boot=null ca_2023=null ca_2011=null
if [ -d "$EFI" ]; then
	firmware=uefi
	sb=$EFI/efivars/SecureBoot-$GLOBAL
	if [ -r "$sb" ]; then
		# 4 bytes of attributes, then the value: 1 = on.
		case $(od -An -t u1 -j4 -N1 "$sb" 2>/dev/null | tr -d ' \n') in
			1) secure_boot=true ;;
			0) secure_boot=false ;;
		esac
	fi
	db=$EFI/efivars/db-$SECDB
	if [ -r "$db" ]; then
		# The certificates' names are plain text inside their DER.
		if grep -a -q "Microsoft UEFI CA 2023" "$db"; then ca_2023=true; else ca_2023=false; fi
		if grep -a -q "Microsoft Corporation UEFI CA 2011" "$db"; then ca_2011=true; else ca_2011=false; fi
	fi
fi
held=false
[ -s "$R/var/lib/domovoi-stick/held-packages" ] && held=true

mkdir -p "$(dirname "$OUT")"
tmp=$OUT.tmp
printf '{"firmware": "%s", "secure_boot": %s, "ca_2023": %s, "ca_2011": %s, "loader_held": %s, "stick": true}\n' \
	"$firmware" "$secure_boot" "$ca_2023" "$ca_2011" "$held" >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$OUT"
