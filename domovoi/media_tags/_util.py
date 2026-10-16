"""Shared pieces of the tag reader: the error type, careful byte reads, and
the budget every read keeps to."""

from __future__ import annotations

import contextvars
import functools
import struct
from contextlib import contextmanager
from typing import BinaryIO, Callable, Iterator, TypeVar

#: The most any single structure is allowed to claim. A cover picture or a
#: lyrics frame is kilobytes to a few megabytes; a size field beyond this is
#: a corrupt file, and believing it would read the whole file into memory.
MAX_BLOCK = 64 * 1024 * 1024

#: What one read (one call of ``read``, ``read_fileobj`` or ``read_id3``)
#: may take into memory, all structures together: the bytes read from the
#: file into a structure, the frames it inflates and the packets it
#: assembles. Python's copies of those bytes come and go within one
#: structure, so a read's peak stays a small multiple of this however the
#: file is made (2026-10-16 delta review B1/S2: a 1 MiB file had inflated
#: to 2 GiB, a 512 MiB file had been read whole).
READ_BUDGET = 64 * 1024 * 1024

#: How many things one read may make: frames and sub-frames, text values,
#: synced-lyrics entries, comments, MP4 items, ASF attributes, APE items.
#: A real file has dozens to a few thousand; a crafted one of zero-length
#: frames had made millions of objects (25 times the file's size).
MAX_ITEMS = 16_384

#: How many structures one read may step over: boxes, chunks, metadata
#: blocks, pages, objects. Real files have hundreds at most.
MAX_STEPS = 200_000


class TagError(ValueError):
    """The file is of a known kind but its structure is broken."""


class BudgetExceeded(TagError):
    """The file asks for more than one read may hold (:data:`READ_BUDGET`,
    :data:`MAX_ITEMS`, :data:`MAX_STEPS`): it is treated as broken."""


class Budget:
    """What is left of one read's allowance."""

    __slots__ = ("bytes_left", "items_left", "steps_left")

    def __init__(self, nbytes: int | None = None, items: int | None = None,
                 steps: int | None = None) -> None:
        self.bytes_left = READ_BUDGET if nbytes is None else nbytes
        self.items_left = MAX_ITEMS if items is None else items
        self.steps_left = MAX_STEPS if steps is None else steps

    def fits(self, n: int) -> bool:
        return 0 <= n <= self.bytes_left

    def spend(self, n: int) -> None:
        if n < 0 or n > self.bytes_left:
            raise BudgetExceeded(f"{n} more bytes is over the read's budget")
        self.bytes_left -= n

    def item(self, n: int = 1) -> None:
        if n > self.items_left:
            raise BudgetExceeded("too many items for one read")
        self.items_left -= n

    def step(self) -> None:
        if self.steps_left <= 0:
            raise BudgetExceeded("too many structures for one read")
        self.steps_left -= 1


_current: contextvars.ContextVar[Budget | None] = contextvars.ContextVar("media_tags_budget", default=None)


@contextmanager
def reading() -> Iterator[Budget]:
    """The budget of the read in progress, or a fresh one for a read that
    starts here: nested calls (a FLAC file behind an ID3 tag, an ID3 tag
    inside a WAV chunk) share their caller's."""
    budget = _current.get()
    if budget is not None:
        yield budget
        return
    budget = Budget()
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def budget() -> Budget:
    """The budget of the read in progress. Outside one (a parser called on
    its own), an unshared fresh one, so a bare call is still bounded per
    structure."""
    return _current.get() or Budget()


F = TypeVar("F", bound=Callable)


def one_read(fn: F) -> F:
    """Run ``fn`` as (part of) one read: the public entry points."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with reading():
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def read_exact(f: BinaryIO, n: int) -> bytes:
    """``n`` bytes from ``f``, or TagError when the file ends first. The
    bytes count against the read's budget."""
    if n < 0 or n > MAX_BLOCK:
        raise TagError(f"implausible size {n}")
    budget().spend(n)
    data = f.read(n)
    if len(data) != n:
        raise TagError("file ends inside a structure")
    return data


def read_upto(f: BinaryIO, n: int) -> bytes:
    """Up to ``n`` bytes from ``f`` (fewer where the file ends), counted
    against the read's budget."""
    if n < 0 or n > MAX_BLOCK:
        raise TagError(f"implausible size {n}")
    budget().spend(n)
    return f.read(n)


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
