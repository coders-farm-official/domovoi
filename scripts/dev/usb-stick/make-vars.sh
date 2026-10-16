#!/usr/bin/env bash
# Test tool (room stick kit): OVMF variable stores for the Secure Boot runs.
# Run in the spike's build container with /out mounted (tools/ovmf).
#
#   OVMF_VARS_4M.ms.fd      Debian's own: Microsoft's 2011 KEK and db (Windows
#                           PCA 2011 + UEFI CA 2011). An old PC that never got
#                           the 2023 certificates. (copied, not made here)
#   OVMF_VARS_4M.ms2023.fd  only Microsoft's 2023 KEK and db (Windows UEFI CA
#                           2023 + UEFI CA 2023): a PC that has the new
#                           certificates and no longer trusts the 2011 CA.
#   OVMF_VARS_4M.msboth.fd  both: a PC that got the 2023 update (the state
#                           fwupd / Windows Update leave behind).
#   OVMF_VARS_4M.no3p.fd    Windows' CAs only: Secured-core, third-party CA off.
set -euo pipefail
C=/usr/lib/python3/dist-packages/virt/firmware/certs
MS=77fa9abd-0359-4d32-bd60-28f4e78f784b   # Microsoft's owner GUID
IN=/usr/share/OVMF/OVMF_VARS_4M.fd

virt-fw-vars -i "$IN" -o /out/OVMF_VARS_4M.ms2023.fd \
  --enroll-generate "Domovoi spike test PK" --no-microsoft --microsoft-kek 2023 \
  --add-db "$MS" "$C/WindowsUEFICA2023.pem" --add-db "$MS" "$C/MicrosoftUEFICA2023.pem" --sb

virt-fw-vars -i "$IN" -o /out/OVMF_VARS_4M.msboth.fd \
  --enroll-generate "Domovoi spike test PK" --no-microsoft --microsoft-kek all \
  --add-db "$MS" "$C/MicrosoftWindowsProductionPCA2011.pem" --add-db "$MS" "$C/MicrosoftCorporationUEFICA2011.pem" \
  --add-db "$MS" "$C/WindowsUEFICA2023.pem" --add-db "$MS" "$C/MicrosoftUEFICA2023.pem" --sb

# Windows' own CAs only (no "Microsoft Corporation UEFI CA", the third-party
# CA that signs shim): a Secured-core PC with the third-party CA switched off.
virt-fw-vars -i "$IN" -o /out/OVMF_VARS_4M.no3p.fd   --enroll-generate "Domovoi spike test PK" --no-microsoft --microsoft-kek all   --add-db "$MS" "$C/MicrosoftWindowsProductionPCA2011.pem" --add-db "$MS" "$C/WindowsUEFICA2023.pem" --sb

for f in /out/OVMF_VARS_4M.ms2023.fd /out/OVMF_VARS_4M.msboth.fd /out/OVMF_VARS_4M.no3p.fd; do
  echo "== $f"
  virt-fw-vars -i "$f" -v -p 2>/dev/null | grep -E "^name=(PK|KEK|db) |subject" || true
done
