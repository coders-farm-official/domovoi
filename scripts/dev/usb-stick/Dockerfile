# The room stick kit's build box (domovoi/usb_stick/, 2026-10-16), tagged
# domovoi-usb-stick-kit:1 (USB_STICK_KIT_IMAGE) by
#   docker build -t domovoi-usb-stick-kit:1 scripts/dev/usb-stick
#
# xorriso and python3 run domovoi/usb_stick/repack.py for a Domovoi core
# that has no xorriso of its own (repack_in_docker.py). Everything else is
# for the kit's checks only: sbsigntool/osslsigncode read the shim's signatures,
# python3-virt-firmware reads and writes OVMF's Secure Boot keys,
# qemu-utils + e2fsprogs + fdisk read a test VM's disk after the run, gpgv +
# debian-keyring check Debian's SHA256SUMS signature when the pin moves.
#
# Pinned base (trixie-slim, 2026-08-26) so two builds use the same tools.
FROM debian@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      xorriso cpio gzip ca-certificates curl \
      sbsigntool osslsigncode python3-virt-firmware ovmf ovmf-ia32 \
      qemu-utils e2fsprogs fdisk gpgv debian-keyring mtools \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /work
