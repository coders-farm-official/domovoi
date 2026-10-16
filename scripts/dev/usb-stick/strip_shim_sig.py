"""Test tool (room stick kit): keep only some Authenticode signatures of a PE.

Debian's shim since 13.6 carries two signatures, one chaining to Microsoft's
UEFI CA 2011 and one to the UEFI CA 2023. The next shim Microsoft signs will
carry only the 2023 one (the 2011 CA expired 2026-06-27). Removing the 2011
signature from today's shim gives exactly that file (the Authenticode digest
does not cover the certificate table), so a QEMU run can show what an old PC
that only trusts the 2011 CA does with it.

  python3 strip_shim_sig.py IN OUT --keep "UEFI CA 2023"
"""

from __future__ import annotations

import argparse
import struct
import sys


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--keep", required=True, help="keep the signatures whose bytes contain this text")
    a = p.parse_args(argv)
    data = bytearray(open(a.src, "rb").read())
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)  # data directories
    sec = dd + 4 * 8  # IMAGE_DIRECTORY_ENTRY_SECURITY
    off, size = struct.unpack_from("<II", data, sec)
    table = bytes(data[off:off + size])
    entries, i = [], 0
    while i + 8 <= len(table):
        length, rev, typ = struct.unpack_from("<IHH", table, i)
        if length < 8:
            break
        entries.append(table[i:i + length])
        i += (length + 7) & ~7
    keep = [e for e in entries if a.keep.encode() in e]
    print(f"{len(entries)} signatures, keeping {len(keep)}")
    if not keep:
        return 1
    new = b"".join(e + b"\0" * (((len(e) + 7) & ~7) - len(e)) for e in keep)
    out = data[:off] + new
    struct.pack_into("<II", out, sec, off, len(new))
    open(a.dst, "wb").write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
