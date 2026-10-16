"""APEv2 tags (and APEv1), at the end of a file or before its ID3v1 block,
from the APE tag item specification (wiki.hydrogenaud.io)."""

from __future__ import annotations

import struct
from typing import BinaryIO

from domovoi.media_tags._model import APETags, APEValue
from domovoi.media_tags._util import MAX_BLOCK, TagError, budget, read_upto


def _footer_at(f: BinaryIO, end: int) -> tuple[int, int, int, int] | None:
    """(version, tag size, item count, flags) of a footer ending at ``end``."""
    if end < 32:
        return None
    f.seek(end - 32)
    foot = f.read(32)
    if foot[:8] != b"APETAGEX":
        return None
    return struct.unpack_from("<IIII", foot, 8)


def read_ape(f: BinaryIO, size: int) -> APETags | None:
    """The APE tag at the end of the file (before an ID3v1 block, if one is
    there); None when the file has none."""
    ends = [size]
    if size >= 128:
        f.seek(size - 128)
        if f.read(3) == b"TAG":
            ends.insert(0, size - 128)
    for end in ends:
        footer = _footer_at(f, end)
        if footer is None:
            continue
        _version, tag_size, count, _flags = footer
        if tag_size < 32 or tag_size > min(end, MAX_BLOCK):
            raise TagError("implausible APE tag size")
        f.seek(end - tag_size)
        return parse_items(read_upto(f, tag_size - 32), count)
    return None


def read_ape_at_start(f: BinaryIO) -> APETags | None:
    """A tag the file starts with (a header with the "has header" flag)."""
    f.seek(0)
    head = f.read(32)
    if head[:8] != b"APETAGEX":
        return None
    _version, tag_size, count, _flags = struct.unpack_from("<IIII", head, 8)
    if tag_size < 32 or tag_size > MAX_BLOCK:
        raise TagError("implausible APE tag size")
    return parse_items(read_upto(f, tag_size - 32), count)


def parse_items(data: bytes, count: int) -> APETags:
    items: list[tuple[str, APEValue]] = []
    allowance = budget()
    pos = 0
    for _ in range(count):
        if pos + 8 > len(data):
            break
        allowance.item()
        vlen, flags = struct.unpack_from("<II", data, pos)
        pos += 8
        end = data.find(b"\x00", pos)
        if end < 0:
            break
        key = data[pos:end].decode("ascii", "replace")
        pos = end + 1
        raw = data[pos:pos + vlen]
        if len(raw) != vlen:
            break
        pos += vlen
        kind = (flags >> 1) & 3
        values = raw.decode("utf-8", "replace").split("\x00") if kind != 1 else []
        items.append((key, APEValue(kind, bytes(raw), values)))
    return APETags(items)
