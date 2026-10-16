"""WAV (RIFF) and AIFF (IFF): their length and the ID3 tag they carry in
an ``id3 `` / ``ID3 `` chunk."""

from __future__ import annotations

import math
import struct
from typing import BinaryIO

from domovoi.media_tags._id3 import ID3Tags, parse_id3v2
from domovoi.media_tags._util import MAX_BLOCK, TagError, budget, read_upto

_ID3_CHUNKS = (b"id3 ", b"ID3 ")


def _chunks(f: BinaryIO, start: int, end: int, big_endian: bool):
    fmt = ">I" if big_endian else "<I"
    allowance = budget()
    pos = start
    while pos + 8 <= end:
        allowance.step()
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        cid = head[:4]
        size = struct.unpack(fmt, head[4:])[0]
        yield cid, pos + 8, min(size, max(0, end - pos - 8))
        pos += 8 + size + (size & 1)


def _id3_chunk(f: BinaryIO, at: int, size: int) -> ID3Tags | None:
    if size > MAX_BLOCK:
        raise TagError("ID3 chunk too large")
    f.seek(at)
    return parse_id3v2(read_upto(f, size))


def read_wav(f: BinaryIO, size: int) -> tuple[float | None, ID3Tags | None]:
    f.seek(0)
    head = f.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise TagError("not a WAVE file")
    byte_rate = rate = block = 0
    data_size: int | None = None
    tags: ID3Tags | None = None
    for cid, at, n in _chunks(f, 12, size, big_endian=False):
        if cid == b"fmt ":
            f.seek(at)
            fmt = f.read(min(n, 16))
            if len(fmt) >= 14:
                _fmt, _channels, rate, byte_rate, block = struct.unpack_from("<HHIIH", fmt)
        elif cid == b"data":
            data_size = n
        elif cid in _ID3_CHUNKS and tags is None:
            tags = _id3_chunk(f, at, n)
    duration = None
    if data_size is not None and rate and block:
        duration = (data_size // block) / rate
    elif data_size is not None and byte_rate:
        duration = data_size / byte_rate
    return duration, tags


def _extended(b: bytes) -> float:
    """An 80-bit IEEE 754 extended float (AIFF's sample rate)."""
    exp = ((b[0] & 0x7F) << 8) | b[1]
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0 and mant == 0:
        return 0.0
    if exp == 0x7FFF:
        return math.inf
    value = mant * 2.0 ** (exp - 16383 - 63)
    return -value if b[0] & 0x80 else value


def read_aiff(f: BinaryIO, size: int) -> tuple[float | None, ID3Tags | None]:
    f.seek(0)
    head = f.read(12)
    if head[:4] != b"FORM" or head[8:12] not in (b"AIFF", b"AIFC"):
        raise TagError("not an AIFF file")
    duration = None
    tags: ID3Tags | None = None
    for cid, at, n in _chunks(f, 12, size, big_endian=True):
        if cid == b"COMM" and n >= 18:
            f.seek(at)
            comm = f.read(18)
            frames = struct.unpack_from(">I", comm, 2)[0]
            rate = _extended(comm[8:18])
            if rate and math.isfinite(rate):
                duration = frames / rate
        elif cid in _ID3_CHUNKS and tags is None:
            tags = _id3_chunk(f, at, n)
    return duration, tags
