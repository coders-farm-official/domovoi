#!/usr/bin/env bash
# Test tool (room stick kit, Windows host): put one local file into a test
# PC's root filesystem, through disk-tool.sh in the build container.
#
#   inject.sh NAME LOCAL_FILE /path/on/disk [MODE]
#
# The file goes in on the container's stdin: a file just written on the
# Windows side and read straight away through Docker Desktop's bind mount
# came through empty or stale more than once in the spike.
set -euo pipefail
name=$1 src=$2 dst=$3 mode=${4:-0644}
here=$(cd "$(dirname "$0")" && pwd)
base=${USB_STICK_KIT_DIR:?set USB_STICK_KIT_DIR}
image=${USB_STICK_KIT_IMAGE:-domovoi-usb-stick-kit:1}
MSYS_NO_PATHCONV=1 docker run --rm -i --cpuset-cpus 16-31 --name "domovoi-usb-stick-disk-$$" \
  -v "$(cygpath -w "$base/vm"):/vm" -v "$(cygpath -w "$here"):/scripts:ro" "$image" \
  bash -c 'cat >/tmp/src && bash /scripts/disk-tool.sh put "$1" /tmp/src "$2" "$3"' _ "$name" "$dst" "$mode" <"$src" \
  | tail -1
