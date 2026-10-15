"""Shared pieces of the tag reader: the error type and careful byte reads."""

from __future__ import annotations

import struct
from typing import BinaryIO

#: The most any single structure is allowed to claim. A cover picture or a
#: lyrics frame is kilobytes to a few megabytes; a size field beyond this is
#: a corrupt file, and believing it would read the whole file into memory.
MAX_BLOCK = 64 * 1024 * 1024


class TagError(ValueError):
    """The file is of a known kind but its structure is broken."""


def read_exact(f: BinaryIO, n: int) -> bytes:
    """``n`` bytes from ``f``, or TagError when the file ends first."""
    if n < 0 or n > MAX_BLOCK:
        raise TagError(f"implausible size {n}")
    data = f.read(n)
    if len(data) != n:
        raise TagError("file ends inside a structure")
    return data


def file_size(f: BinaryIO) -> int:
    here = f.tell()
    f.seek(0, 2)
    size = f.tell()
    f.seek(here)
    return size


def syncsafe(b: bytes) -> int:
    """An ID3v2 "synchsafe" integer: seven bits a byte, big-endian."""
    n = 0
    for byte in b:
        n = (n << 7) | (byte & 0x7F)
    return n


def u32be(b: bytes, at: int = 0) -> int:
    return struct.unpack_from(">I", b, at)[0]


def u32le(b: bytes, at: int = 0) -> int:
    return struct.unpack_from("<I", b, at)[0]
