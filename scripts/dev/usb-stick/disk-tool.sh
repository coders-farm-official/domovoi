#!/usr/bin/env bash
# Test tool (room stick kit): read and change a test PC's disk between QEMU
# runs, without logging in (the room has no login). Runs in the spike's
# build container with the VM folder at /vm:
#
#   disk-tool.sh info   NAME                      partitions, space used
#   disk-tool.sh get    NAME /path/in/root ...    copy files out to /vm/NAME.out/
#   disk-tool.sh put    NAME LOCAL /path [MODE]   write a file into the root fs (root-owned)
#   disk-tool.sh link   NAME TARGET /path         make a symlink in the root fs
#   disk-tool.sh esp-put NAME LOCAL /EFI/x/y.efi  replace a file on the EFI partition
#   disk-tool.sh esp-ls NAME                      list the EFI partition
#
# The qcow2 is converted to a sparse raw file, changed with debugfs/mtools
# at the partition's offset, and (for changes) converted back.
set -euo pipefail
op=$1 name=$2; shift 2
Q=/vm/$name.qcow2
R=/tmp/$name.raw
qemu-img convert -O raw "$Q" "$R"
part_off() {  # part_off TYPE-REGEX -> byte offset of the first matching partition
  sfdisk -J "$R" | python3 -I -c '
import json, re, sys
t = json.load(sys.stdin)["partitiontable"]
ss = t.get("sectorsize", 512)
for p in t["partitions"]:
    if re.search(sys.argv[1], p.get("type", ""), re.I):
        print(p["start"] * ss); break' "$1"
}
root_off() {  # the ext4 root: Linux filesystem GUID (gpt) or type 83 (dos)
  part_off '^(0FC63DAF-8483-4772-8E79-3D69D8477DE4|83)$'
}
esp_off() { part_off '^(C12A7328-F81F-11D2-BA4B-00A0C93EC93B|ef)$'; }
changed=0
case $op in
  info)
    sfdisk -l "$R"
    o=$(root_off); echo "root at $o"
    dumpe2fs -h "$R?offset=$o" 2>/dev/null | grep -E "Block count|Free blocks|Block size"
    ;;
  get)
    o=$(root_off); mkdir -p "/vm/$name.out"
    for p in "$@"; do
      debugfs -R "dump -p $p /vm/$name.out/$(basename "$p")" "$R?offset=$o" 2>&1 | grep -v "^debugfs" || true
    done
    ls -la "/vm/$name.out"
    ;;
  put)
    o=$(root_off); src=$1 dst=$2 mode=${3:-0644}
    debugfs -w -R "rm $dst" "$R?offset=$o" >/dev/null 2>&1 || true
    debugfs -w -f - "$R?offset=$o" <<EOF
write $src $dst
sif $dst mode 0100${mode#0}
sif $dst uid 0
sif $dst gid 0
EOF
    # Docker Desktop's file sharing once handed debugfs a stale copy of a
    # just-written source: check what landed.
    got=$(debugfs -R "stat $dst" "$R?offset=$o" 2>/dev/null | sed -n 's/.*Size: \([0-9]*\).*/\1/p' | head -1)
    [ "$got" = "$(stat -c %s "$src")" ] || { echo "disk-tool: wrote $got bytes, source has $(stat -c %s "$src")" >&2; exit 1; }
    changed=1
    ;;
  link)
    o=$(root_off)
    debugfs -w -R "symlink $2 $1" "$R?offset=$o"
    changed=1
    ;;
  esp-put)
    o=$(esp_off)
    mcopy -o -i "$R@@$o" "$1" "::$2"
    mdir -i "$R@@$o" "::$(dirname "$2")"
    changed=1
    ;;
  esp-ls)
    o=$(esp_off)
    mdir -/ -i "$R@@$o" ::/
    ;;
  *) echo "unknown op $op" >&2; exit 2 ;;
esac
if [ "$changed" = 1 ]; then
  qemu-img convert -O qcow2 "$R" "$Q.new" && mv "$Q.new" "$Q"
fi
rm -f "$R"
