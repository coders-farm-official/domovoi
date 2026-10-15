"""ASF (WMA): the header object's attributes and the file's play length,
from the Advanced Systems Format specification (revision 01.20.05)."""

from __future__ import annotations

import struct
import uuid
from typing import BinaryIO

from domovoi.media_tags._model import ASFTags, ASFValue
from domovoi.media_tags._util import MAX_BLOCK, TagError, read_exact


def _guid(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


HEADER = _guid("75B22630-668E-11CF-A6D9-00AA0062CE6C")
FILE_PROPERTIES = _guid("8CABDCA1-A947-11CF-8EE4-00C00C205365")
CONTENT_DESCRIPTION = _guid("75B22633-668E-11CF-A6D9-00AA0062CE6C")
EXTENDED_CONTENT = _guid("D2D0A440-E307-11D2-97F0-00A0C95EA850")
HEADER_EXTENSION = _guid("5FBF03B5-A92E-11CF-8EE3-00C00C205365")
METADATA = _guid("C5F8CBEA-5BAF-4877-8467-AA8C44FA4CCA")
METADATA_LIBRARY = _guid("44231C94-9498-49D1-A141-1D134E457054")

_CONTENT_NAMES = ("Title", "Author", "Copyright", "Description", "Rating")


def _text(raw: bytes) -> str:
    return raw.decode("utf-16-le", "replace").rstrip("\x00")


def _value(dtype: int, raw: bytes, *, in_metadata: bool) -> ASFValue:
    if dtype == 0:
        return ASFValue(0, _text(raw))
    if dtype == 2:
        n = 2 if in_metadata else 4
        return ASFValue(2, bool(int.from_bytes(raw[:n], "little")))
    if dtype in (3, 4, 5):
        return ASFValue(dtype, int.from_bytes(raw, "little"))
    return ASFValue(dtype, bytes(raw))


def _objects(data: bytes, pos: int):
    while pos + 24 <= len(data):
        guid = data[pos:pos + 16]
        size = struct.unpack_from("<Q", data, pos + 16)[0]
        if size < 24 or pos + size > len(data):
            raise TagError("ASF object runs past its header")
        yield guid, data[pos + 24:pos + size]
        pos += size


def _metadata_records(body: bytes, items: list[tuple[str, ASFValue]]) -> None:
    count = struct.unpack_from("<H", body, 0)[0]
    pos = 2
    for _ in range(count):
        _lang, _stream, nlen, dtype, dlen = struct.unpack_from("<HHHHI", body, pos)
        pos += 12
        name = _text(body[pos:pos + nlen])
        pos += nlen
        raw = body[pos:pos + dlen]
        pos += dlen
        items.append((name, _value(dtype, raw, in_metadata=True)))


def read_asf(f: BinaryIO) -> tuple[float | None, ASFTags | None]:
    f.seek(0)
    head = read_exact(f, 30)
    if head[:16] != HEADER:
        raise TagError("not an ASF file")
    size = struct.unpack_from("<Q", head, 16)[0]
    if size < 30 or size > MAX_BLOCK:
        raise TagError("implausible ASF header size")
    data = head + read_exact(f, size - 30)
    items: list[tuple[str, ASFValue]] = []
    duration: float | None = None
    try:
        for guid, body in _objects(data, 30):
            if guid == FILE_PROPERTIES and len(body) >= 64:
                play, _send, preroll = struct.unpack_from("<QQQ", body, 40)
                length = play / 1e7 - preroll / 1000.0
                duration = length if length > 0 else None
            elif guid == CONTENT_DESCRIPTION:
                lengths = struct.unpack_from("<5H", body, 0)
                pos = 10
                for name, n in zip(_CONTENT_NAMES, lengths):
                    text = _text(body[pos:pos + n])
                    pos += n
                    if text:
                        items.append((name, ASFValue(0, text)))
            elif guid == EXTENDED_CONTENT:
                count = struct.unpack_from("<H", body, 0)[0]
                pos = 2
                for _ in range(count):
                    nlen = struct.unpack_from("<H", body, pos)[0]
                    name = _text(body[pos + 2:pos + 2 + nlen])
                    pos += 2 + nlen
                    dtype, vlen = struct.unpack_from("<HH", body, pos)
                    raw = body[pos + 4:pos + 4 + vlen]
                    pos += 4 + vlen
                    items.append((name, _value(dtype, raw, in_metadata=False)))
            elif guid == HEADER_EXTENSION and len(body) >= 22:
                for sub, sbody in _objects(body, 22):
                    if sub in (METADATA, METADATA_LIBRARY):
                        _metadata_records(sbody, items)
    except struct.error as e:
        raise TagError("truncated ASF object") from e
    return duration, (ASFTags(items) if items else None)
