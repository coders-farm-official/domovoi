"""Ogg (RFC 3533) carrying Vorbis, Opus (RFC 7845), FLAC or Speex: the
comment header of the first audio stream and the stream's length from
its last granule position."""

from __future__ import annotations

import struct
from typing import BinaryIO

from domovoi.media_tags._flac import parse_vorbis_comment, streaminfo_length
from domovoi.media_tags._model import VorbisTags
from domovoi.media_tags._util import TagError

_MAX_HEADER_PAGES = 4096
_TAIL = 256 * 1024


def _pages(f: BinaryIO, start: int = 0):
    """``(serial, granule, flags, segments, data)`` for each page from
    ``start``, until the file ends or stops being Ogg."""
    f.seek(start)
    while True:
        head = f.read(27)
        if len(head) < 27:
            return
        if head[:4] != b"OggS" or head[4] != 0:
            raise TagError("lost Ogg page sync")
        flags = head[5]
        granule, serial = struct.unpack_from("<qI", head, 6)
        lacing = f.read(head[26])
        if len(lacing) < head[26]:
            raise TagError("truncated Ogg page")
        data = f.read(sum(lacing))
        if len(data) < sum(lacing):
            raise TagError("truncated Ogg page")
        yield serial, granule, flags, lacing, data


def _packets(f: BinaryIO, serial: int, wanted: int) -> list[bytes]:
    """The first ``wanted`` packets of logical stream ``serial``."""
    packets: list[bytes] = []
    partial = b""
    for n, (s, _granule, _flags, lacing, data) in enumerate(_pages(f)):
        if n > _MAX_HEADER_PAGES:
            break
        if s != serial:
            continue
        pos = 0
        for seg in lacing:
            partial += data[pos:pos + seg]
            pos += seg
            if seg < 255:
                packets.append(partial)
                partial = b""
                if len(packets) >= wanted:
                    return packets
    return packets


def _last_granule(f: BinaryIO, serial: int, size: int) -> int | None:
    """The granule position of the last page of ``serial`` that has one."""
    span = _TAIL
    while True:
        begin = max(0, size - span)
        f.seek(begin)
        chunk = f.read(size - begin)
        best = None
        i = chunk.rfind(b"OggS")
        while i >= 0:
            head = chunk[i:i + 27]
            if len(head) == 27 and head[4] == 0:
                granule, s = struct.unpack_from("<qI", head, 6)
                if s == serial and granule != -1:
                    best = granule
                    break
            i = chunk.rfind(b"OggS", 0, i)
        if best is not None or begin == 0:
            return best
        span *= 4


def read_ogg(f: BinaryIO, size: int) -> tuple[str, float | None, VorbisTags | None]:
    """(kind, length, comments) of the first audio stream in the file."""
    first_pages = []
    for n, page in enumerate(_pages(f)):
        serial, _granule, flags, lacing, data = page
        if not flags & 0x02:            # past the beginning-of-stream pages
            break
        first_pages.append((serial, data))
        if n > 64:
            break
    if not first_pages:
        raise TagError("no Ogg stream")
    for serial, data in first_pages:
        if data.startswith(b"\x01vorbis"):
            if len(data) < 16:
                raise TagError("short Vorbis header")
            rate = struct.unpack_from("<I", data, 12)[0]
            packets = _packets(f, serial, 2)
            if len(packets) < 2 or not packets[1].startswith(b"\x03vorbis"):
                raise TagError("no Vorbis comment header")
            tags = parse_vorbis_comment(packets[1][7:])
            granule = _last_granule(f, serial, size)
            length = granule / rate if granule and rate else None
            return "ogg_vorbis", length, tags
        if data.startswith(b"OpusHead"):
            if len(data) < 19:
                raise TagError("short Opus header")
            pre_skip = struct.unpack_from("<H", data, 10)[0]
            packets = _packets(f, serial, 2)
            if len(packets) < 2 or not packets[1].startswith(b"OpusTags"):
                raise TagError("no Opus comment header")
            tags = parse_vorbis_comment(packets[1][8:])
            granule = _last_granule(f, serial, size)
            length = max(0, granule - pre_skip) / 48000 if granule else None
            return "ogg_opus", length, tags
        if data.startswith(b"\x7fFLAC"):
            if len(data) < 51 or data[9:13] != b"fLaC":
                raise TagError("short Ogg FLAC header")
            rate, _ = streaminfo_length(data[17:51])
            packets = _packets(f, serial, 2)
            tags = None
            if len(packets) >= 2 and len(packets[1]) >= 4 and packets[1][0] & 0x7F == 4:
                tags = parse_vorbis_comment(packets[1][4:])
            granule = _last_granule(f, serial, size)
            length = granule / rate if granule and rate else None
            return "ogg_flac", length, tags
        if data.startswith(b"Speex   "):
            if len(data) < 40:
                raise TagError("short Speex header")
            rate = struct.unpack_from("<I", data, 36)[0]
            packets = _packets(f, serial, 2)
            tags = parse_vorbis_comment(packets[1]) if len(packets) >= 2 else None
            granule = _last_granule(f, serial, size)
            length = granule / rate if granule and rate else None
            return "ogg_speex", length, tags
    raise TagError("no audio stream this reads in the Ogg file")
