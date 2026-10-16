#!/usr/bin/env bash
# Test tool (room stick kit, Windows host): put a test hook on a test PC's
# disk: /usr/local/lib/domovoi-stick-test/{hook.sh, extra files...} and the
# domovoi-stick-testhook.service unit, enabled. Files go in as a tar stream
# on the container's stdin (Docker Desktop's bind mount served stale files).
#
#   inject-hook.sh NAME HOOK.sh [EXTRA_FILE...]
set -euo pipefail
name=$1 hook=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
base=${USB_STICK_KIT_DIR:?set USB_STICK_KIT_DIR}
image=${USB_STICK_KIT_IMAGE:-domovoi-usb-stick-kit:1}
stage=$(mktemp -d)
cp "$hook" "$stage/hook.sh"
cp "$here/hooks/domovoi-stick-testhook.service" "$stage/"
for f in "$@"; do cp "$f" "$stage/"; done
(cd "$stage" && tar -cf - .) | MSYS_NO_PATHCONV=1 docker run --rm -i --cpuset-cpus 16-31 \
  --name "domovoi-usb-stick-disk-$$" -v "$(cygpath -w "$base/vm"):/vm" "$image" bash -c '
set -euo pipefail
mkdir -p /tmp/in && tar -xf - -C /tmp/in
Q=/vm/$1.qcow2 R=/tmp/d.raw
qemu-img convert -O raw "$Q" "$R"
o=$(sfdisk -J "$R" | python3 -I -c "
import json, sys
t = json.load(sys.stdin)[\"partitiontable\"]
print([p for p in t[\"partitions\"] if p[\"type\"].upper() in (\"0FC63DAF-8483-4772-8E79-3D69D8477DE4\", \"83\")][0][\"start\"] * 512)")
D=/usr/local/lib/domovoi-stick-test
{
  echo "mkdir $D"
  for f in /tmp/in/*; do
    b=$(basename "$f")
    [ "$b" = domovoi-stick-testhook.service ] && continue
    echo "rm $D/$b"; echo "write $f $D/$b"; echo "sif $D/$b mode 0100755"
  done
  echo "rm /etc/systemd/system/domovoi-stick-testhook.service"
  echo "write /tmp/in/domovoi-stick-testhook.service /etc/systemd/system/domovoi-stick-testhook.service"
  echo "sif /etc/systemd/system/domovoi-stick-testhook.service mode 0100644"
  echo "symlink /etc/systemd/system/multi-user.target.wants/domovoi-stick-testhook.service /etc/systemd/system/domovoi-stick-testhook.service"
} | debugfs -w -f - "$R?offset=$o" >/dev/null 2>&1 || true
debugfs -R "ls -l $D" "$R?offset=$o" 2>/dev/null | awk "NF>8 {print \$6, \$NF}"
qemu-img convert -O qcow2 "$R" "$Q.new" && mv "$Q.new" "$Q"
' _ "$name"
rm -rf "$stage"
