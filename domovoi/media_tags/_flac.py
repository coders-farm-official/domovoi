"""FLAC metadata blocks, Vorbis comments and FLAC picture blocks.

From the FLAC format specification (xiph.org, RFC 9639) and the Vorbis I
specification's comment header. The picture block layout is shared by
FLAC's PICTURE block and the base64 ``METADATA_BLOCK_PICTURE`` comment Ogg
files carry their covers in.

PICTURE blocks are kept only while they fit :data:`PICTURES_BUDGET` (the
rest are skipped unread), so a file of many large pictures costs no more
than a few (2026-10-16 delta review S2), and leaves room for its comments.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

from domovoi.media_tags._model import Picture, VorbisTags
from domovoi.media_tags._util import TagError, budget, one_read, read_exact, u32be, u32le

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)

#: What one FLAC stream's PICTURE blocks may hold together, of the read's
#: budget. A cover the dashboard shows is at most 8 MiB.
PICTURES_BUDGET = 32 * 1024 * 1024


@one_read
def parse_vorbis_comment(data: bytes, *, framing: bool = False) -> VorbisTags:
    """A Vorbis comment block: vendor string, then ``NAME=value`` fields
    (UTF-8). A field with no ``=`` is not a field and is skipped."""
    try:
        pos = 0
        n = u32le(data, pos)
        pos += 4
        vendor = data[pos:pos + n].decode("utf-8", "replace")
        pos += n
        count = u32le(data, pos)
        pos += 4
    except struct.error as e:
        raise TagError("truncated Vorbis comment header") from e
    items: list[tuple[str, str]] = []
    allowance = budget()
    for _ in range(count):
        allowance.item()
        if pos + 4 > len(data):
            raise TagError("truncated Vorbis comment")
        n = u32le(data, pos)
        pos += 4
        if pos + n > len(data):
            raise TagError("truncated Vorbis comment")
        raw = data[pos:pos + n]
        pos += n
        key, sep, value = raw.partition(b"=")
        if not sep:
            continue
        try:
            name = key.decode("ascii")
        except UnicodeDecodeError:
            continue
        items.append((name, value.decode("utf-8", "replace")))
    return VorbisTags(items, vendor)


@one_read
def parse_picture(data: bytes) -> Picture:
    """A FLAC picture block's body: type, MIME type, description,
    dimensions, then the image."""
    try:
        pos = 0
        ptype = u32be(data, pos)
        n = u32be(data, pos + 4)
        mime = data[pos + 8:pos + 8 + n].decode("ascii", "replace")
        pos += 8 + n
        n = u32be(data, pos)
        desc = data[pos + 4:pos + 4 + n].decode("utf-8", "replace")
        pos += 4 + n + 16          # width, height, depth, colours
        n = u32be(data, pos)
        image = data[pos + 4:pos + 4 + n]
    except struct.error as e:
        raise TagError("truncated picture block") from e
    if len(image) != n:
        raise TagError("truncated picture block")
    return Picture(ptype, mime, desc, bytes(image))


def streaminfo_length(info: bytes) -> tuple[int, float | None]:
    """(sample rate, length in seconds) from a STREAMINFO body; the length
    is None when the stream doesn't say how many samples it has."""
    if len(info) < 18:
        raise TagError("short STREAMINFO")
    packed = int.from_bytes(info[10:18], "big")
    rate = packed >> 44
    total = packed & ((1 << 36) - 1)
    if rate <= 0:
        raise TagError("STREAMINFO with no sample rate")
    return rate, (total / rate if total else None)


def read_flac(f: BinaryIO, start: int) -> tuple[float | None, VorbisTags | None, list[Picture]]:
    """The metadata blocks after the ``fLaC`` marker at ``start``."""
    f.seek(start)
    if f.read(4) != b"fLaC":
        raise TagError("not a FLAC stream")
    duration: float | None = None
    tags: VorbisTags | None = None
    pictures: list[Picture] = []
    pictures_left = PICTURES_BUDGET
    allowance = budget()
    first = True
    while True:
        allowance.step()
        head = read_exact(f, 4)
        last, btype = head[0] & 0x80, head[0] & 0x7F
        size = int.from_bytes(head[1:4], "big")
        if btype == 127 or (first and btype != STREAMINFO):
            raise TagError("invalid FLAC metadata block")
        if btype == PICTURE and (size > pictures_left or not allowance.fits(size)):
            f.seek(size, 1)        # past what the pictures may hold: skipped unread
        elif btype in (STREAMINFO, VORBIS_COMMENT, PICTURE):
            body = read_exact(f, size)
            if btype == STREAMINFO:
                _, duration = streaminfo_length(body)
            elif btype == VORBIS_COMMENT:
                block = parse_vorbis_comment(body)
                if tags is None:
                    tags = block
                else:
                    tags.items.extend(block.items)
            else:
                pictures_left -= size
                pictures.append(parse_picture(body))
        else:
            f.seek(size, 1)
        first = False
        if last:
            break
    return duration, tags, pictures
