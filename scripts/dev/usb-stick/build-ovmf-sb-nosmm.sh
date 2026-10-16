#!/usr/bin/env bash
# Test tool (room stick kit): Debian's own OVMF source, built with Secure
# Boot but WITHOUT the SMM requirement, so a Secure Boot run can use WHPX.
#
# Why: Debian's Secure Boot OVMF (OVMF_CODE_4M.secboot.fd, which .ms.fd
# links to) is built with SMM_REQUIRE=TRUE, and QEMU's WHPX accelerator has
# no System Management Mode ("System Management Mode not supported by this
# hypervisor", QEMU 11.1). SMM only guards the variable store against the
# running OS; the signature checks of shim, GRUB and the kernel are the same
# code (DxeImageVerificationLib) either way. The variable file layout is the
# same for the 4 MB builds, so Debian's OVMF_VARS_4M.ms.fd (Microsoft's 2011
# keys enrolled) pairs with this code file.
#
# Run in a Debian 13 container; writes /out/OVMF_CODE_4M.secboot-nosmm.fd.
set -euo pipefail
VER=2025.02-8+deb13u1
export DEBIAN_FRONTEND=noninteractive
echo "deb-src http://deb.debian.org/debian trixie main" >/etc/apt/sources.list.d/src.list
echo "deb-src http://deb.debian.org/debian-security trixie-security main" >>/etc/apt/sources.list.d/src.list
echo "deb-src http://deb.debian.org/debian trixie-updates main" >>/etc/apt/sources.list.d/src.list
apt-get update -qq
apt-get install -y -qq --no-install-recommends dpkg-dev build-essential uuid-dev nasm acpica-tools \
  python3 python3-venv python-is-python3 git ca-certificates xz-utils >/dev/null
mkdir -p /src && cd /src
apt-get source -qq "edk2=$VER" >/dev/null 2>&1 || apt-get source "edk2=$VER"
cd /src/edk2-*/
make -s -C BaseTools -j"$(nproc)" >/dev/null
set +u
# shellcheck disable=SC1091
. ./edksetup.sh >/dev/null
set -u
# Debian's OVMF_4M_SECBOOT_FLAGS without -DSMM_REQUIRE=TRUE (debian/rules).
build -q -a X64 -t GCC5 -b RELEASE -n "$(nproc)" -p OvmfPkg/OvmfPkgX64.dsc \
  -DCC_MEASUREMENT_ENABLE=TRUE -DNETWORK_HTTP_BOOT_ENABLE=TRUE -DNETWORK_IP6_ENABLE=TRUE \
  -DNETWORK_TLS_ENABLE -DTPM2_ENABLE=TRUE -DFD_SIZE_4MB \
  --pcd PcdUninstallMemAttrProtocol=TRUE -DBUILD_SHELL=FALSE -DSECURE_BOOT_ENABLE=TRUE \
  2>&1 | tail -5
cp Build/OvmfX64/RELEASE_GCC5/FV/OVMF_CODE.fd /out/OVMF_CODE_4M.secboot-nosmm.fd
sha256sum /out/OVMF_CODE_4M.secboot-nosmm.fd
