"""MP4 / M4A / M4B (ISO/IEC 14496-12 boxes): the iTunes-style metadata
list (``moov.udta.meta.ilst``) and the sound track's length.

The box tree is walked with seeks, so a long file's sample tables and its
media data are never read.
"""

from __future__ import annotations

import struct
from typing import Any, BinaryIO, Iterator

from domovoi.media_tags._model import MP4Tags
from domovoi.media_tags._util import MAX_BLOCK, TagError, read_exact

def _boxes(f: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """``(type, payload start, payload end)`` of each box in [start, end)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        payload = pos + 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            payload = pos + 16
        elif size == 0:
            size = end - pos
        if size < payload - pos or pos + size > end:
            raise TagError(f"box {kind!r} runs past its parent")
        yield kind, payload, pos + size
        pos += size


def _child(f: BinaryIO, start: int, end: int, kind: bytes) -> tuple[int, int] | None:
    for k, a, b in _boxes(f, start, end):
        if k == kind:
            return a, b
    return None


def _meta_children(f: BinaryIO, start: int, end: int) -> int:
    """Where a ``meta`` box's children start: after its version and flags
    (ISO, iTunes), or straight away (some QuickTime writers)."""
    f.seek(start)
    head = f.read(12)
    if len(head) >= 12 and head[8:12] in (b"hdlr", b"ilst", b"keys", b"free"):
        return start + 4
    if len(head) >= 8 and head[4:8] in (b"hdlr", b"ilst", b"keys", b"free"):
        return start
    return start + 4


def _read(f: BinaryIO, start: int, end: int) -> bytes:
    if end - start > MAX_BLOCK:
        raise TagError("box too large")
    f.seek(start)
    return read_exact(f, end - start)


_TEXT_TYPES = (1, 2)


def _item_values(name: str, body: bytes) -> list[Any]:
    """The ``data`` boxes of one ilst item, decoded."""
    values: list[Any] = []
    pos = 0
    while pos + 8 <= len(body):
        size, kind = struct.unpack_from(">I4s", body, pos)
        if size < 8 or pos + size > len(body):
            break
        if kind == b"data" and size >= 16:
            dtype = int.from_bytes(body[pos + 9:pos + 12], "big")
            raw = body[pos + 16:pos + size]
            values.append(_decode(name, dtype, raw))
        pos += size
    return values


def _decode(name: str, dtype: int, raw: bytes) -> Any:
    if name == "covr":
        return bytes(raw)
    if name in ("trkn", "disk"):
        if len(raw) >= 6:
            return struct.unpack_from(">HH", raw, 2)
        return bytes(raw)
    if name == "tmpo" and len(raw) == 2:
        return struct.unpack(">H", raw)[0]
    if name in ("cpil", "pgap", "pcst", "hdvd") and len(raw) == 1:
        return bool(raw[0])
    if name.startswith("----"):
        return bytes(raw)
    if dtype == 1 or (dtype == 0 and (name.startswith("\xa9") or name in _TEXTUAL)):
        return raw.decode("utf-8", "replace")
    if dtype == 2:
        return raw.decode("utf-16-be", "replace")
    if dtype == 21 and len(raw) in (1, 2, 4, 8):
        return int.from_bytes(raw, "big", signed=True)
    if dtype == 22 and len(raw) in (1, 2, 4, 8):
        return int.from_bytes(raw, "big", signed=False)
    return bytes(raw)


_TEXTUAL = {"aART", "cprt", "desc", "ldes", "purd", "soal", "soar", "sonm", "soaa",
            "soco", "sosn", "tvsh", "tvnn", "tven", "catg", "keyw", "purl", "egid"}


def _parse_ilst(body: bytes) -> MP4Tags:
    items: list[tuple[str, Any]] = []
    pos = 0
    while pos + 8 <= len(body):
        size, kind = struct.unpack_from(">I4s", body, pos)
        if size < 8 or pos + size > len(body):
            break
        inner = body[pos + 8:pos + size]
        name = kind.decode("latin-1")
        if kind == b"----":
            mean = sub = ""
            data: list[Any] = []
            p = 0
            while p + 8 <= len(inner):
                s, k = struct.unpack_from(">I4s", inner, p)
                if s < 8 or p + s > len(inner):
                    break
                if k == b"mean":
                    mean = inner[p + 12:p + s].decode("utf-8", "replace")
                elif k == b"name":
                    sub = inner[p + 12:p + s].decode("utf-8", "replace")
                p += s
            data = _item_values("----", inner)
            name = f"----:{mean}:{sub}"
            for v in data:
                items.append((name, v))
        else:
            for v in _item_values(name, inner):
                items.append((name, v))
        pos += size
    return MP4Tags(items)


def _sound_duration(f: BinaryIO, moov: tuple[int, int]) -> float | None:
    for kind, a, b in _boxes(f, *moov):
        if kind != b"trak":
            continue
        mdia = _child(f, a, b, b"mdia")
        if mdia is None:
            continue
        hdlr = _child(f, *mdia, b"hdlr")
        if hdlr is None:
            continue
        body = _read(f, *hdlr)
        if body[8:12] != b"soun":
            continue
        mdhd = _child(f, *mdia, b"mdhd")
        if mdhd is None:
            return None
        body = _read(f, *mdhd)
        if body[:1] == b"\x01" and len(body) >= 32:
            scale, length = struct.unpack_from(">IQ", body, 20)
        elif len(body) >= 20:
            scale, length = struct.unpack_from(">II", body, 12)
        else:
            return None
        return length / scale if scale else None
    return None


def read_mp4(f: BinaryIO, size: int) -> tuple[float | None, MP4Tags | None]:
    moov = _child(f, 0, size, b"moov")
    if moov is None:
        raise TagError("no moov box")
    duration = _sound_duration(f, moov)
    tags: MP4Tags | None = None
    for path in ((b"udta", b"meta"), (b"meta",)):
        box: tuple[int, int] | None = moov
        for name in path:
            box = _child(f, *box, name) if box else None
        if box is None:
            continue
        ilst = _child(f, _meta_children(f, *box), box[1], b"ilst")
        if ilst is not None:
            tags = _parse_ilst(_read(f, *ilst))
            break
    return duration, tags
