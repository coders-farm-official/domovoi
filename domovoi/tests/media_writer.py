"""Tagged media for the tests, written byte by byte from the format specs.

The tests used to have mutagen (GPL-2.0) tag their generated files; this
writer replaces it, so the suite needs no tagging library and never skips
for want of one. It writes what a tagger writes — ID3v2.2/2.3/2.4 (with
unsynchronisation, extended headers, compressed frames), ID3v1, FLAC
metadata blocks, Ogg pages with real CRCs, MP4 ``ilst`` atoms, ASF header
objects, APEv2 — and is deliberately independent of the reader under test
(``domovoi.media_tags``): nothing here imports it.

Frame builders take mutagen-like keyword arguments so a test reads the way
it did: ``USLT(encoding=3, lang="eng", desc="", text=...)``.
"""

from __future__ import annotations

import struct
import uuid
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# ─── raw audio ───────────────────────────────────────────────────────────


def silent_mp3(frames: int = 8) -> bytes:
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz, mono, all-zero side info: a
    real (silent) stream a reader syncs to."""
    header = bytes([0xFF, 0xFB, 0x90, 0xC4])
    frame_len = 144 * 128000 // 44100
    return (header + b"\x00" * (frame_len - 4)) * frames


# ─── ID3v2 ───────────────────────────────────────────────────────────────


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _enc_text(encoding: int, text: str, *, bom: bool = True) -> bytes:
    if encoding == 0:
        return text.encode("latin-1")
    if encoding == 1:
        return (b"\xff\xfe" if bom else b"") + text.encode("utf-16-le")
    if encoding == 2:
        return text.encode("utf-16-be")
    return text.encode("utf-8")


def _term(encoding: int) -> bytes:
    return b"\x00\x00" if encoding in (1, 2) else b"\x00"


@dataclass
class Frame:
    """An ID3v2 frame to write: its v2.3/v2.4 id and how to build its body
    for a given version. ``flags`` are v2.4 format flags (0x08 compress,
    0x02 unsync, 0x01 data length indicator); v2.3 gets compression only."""

    id: str
    parts: Sequence[object] = ()
    flags: int = 0
    sub_frames: list["Frame"] = field(default_factory=list)

    def body(self, version: int) -> bytes:
        return _BUILDERS[self.kind](self, version)

    @property
    def kind(self) -> str:
        if self.id == "TXXX":
            return "TXXX"
        if self.id.startswith("T"):
            return "text"
        return self.id


def text_frame(fid: str, *values: str, encoding: int = 3, flags: int = 0) -> Frame:
    return Frame(fid, (encoding, list(values)), flags)


def TIT2(text: str | Sequence[str], encoding: int = 3) -> Frame:  # noqa: N802 — mutagen-like
    vals = [text] if isinstance(text, str) else list(text)
    return text_frame("TIT2", *vals, encoding=encoding)


def TXXX(*, desc: str, text: str, encoding: int = 3) -> Frame:  # noqa: N802
    return Frame("TXXX", (encoding, desc, text))


def APIC(*, encoding: int = 3, mime: str, type: int, desc: str, data: bytes) -> Frame:  # noqa: N802,A002
    return Frame("APIC", (encoding, mime, type, desc, data))


def USLT(*, encoding: int = 3, lang: str, desc: str, text: str) -> Frame:  # noqa: N802
    return Frame("USLT", (encoding, lang, desc, text))


def COMM(*, encoding: int = 3, lang: str, desc: str, text: str) -> Frame:  # noqa: N802
    return Frame("COMM", (encoding, lang, desc, text))


def SYLT(*, encoding: int = 3, lang: str, format: int, type: int, desc: str,  # noqa: N802,A002
         text: Sequence[tuple[str, int]]) -> Frame:
    return Frame("SYLT", (encoding, lang, format, type, desc, list(text)))


def CHAP(*, element_id: str, start_time: int, end_time: int,  # noqa: N802
         sub_frames: Iterable[Frame] = ()) -> Frame:
    return Frame("CHAP", (element_id, start_time, end_time), sub_frames=list(sub_frames))


def _b_text(fr: Frame, version: int) -> bytes:
    enc, values = fr.parts
    term = _term(enc)
    return bytes([enc]) + term.join(_enc_text(enc, v) for v in values)


def _b_txxx(fr: Frame, version: int) -> bytes:
    enc, desc, text = fr.parts
    return bytes([enc]) + _enc_text(enc, desc) + _term(enc) + _enc_text(enc, text)


_PIC_FORMATS = {"image/jpeg": b"JPG", "image/png": b"PNG", "image/gif": b"GIF"}


def _b_apic(fr: Frame, version: int) -> bytes:
    enc, mime, ptype, desc, data = fr.parts
    if version == 2:
        head = _PIC_FORMATS.get(mime, mime.split("/")[-1].upper().encode()[:3].ljust(3, b" "))
    else:
        head = mime.encode("latin-1") + b"\x00"
    return bytes([enc]) + head + bytes([ptype]) + _enc_text(enc, desc) + _term(enc) + data


def _b_lang_desc_text(fr: Frame, version: int) -> bytes:
    enc, lang, desc, text = fr.parts
    return bytes([enc]) + lang.encode("latin-1")[:3].ljust(3, b"\x00") + _enc_text(enc, desc) + _term(enc) + _enc_text(enc, text)


def _b_sylt(fr: Frame, version: int) -> bytes:
    enc, lang, fmt, ctype, desc, entries = fr.parts
    out = bytes([enc]) + lang.encode("latin-1")[:3] + bytes([fmt, ctype]) + _enc_text(enc, desc) + _term(enc)
    for text, ts in entries:
        out += _enc_text(enc, text) + _term(enc) + struct.pack(">I", ts)
    return out


def _b_chap(fr: Frame, version: int) -> bytes:
    element_id, start, end = fr.parts
    out = element_id.encode("latin-1") + b"\x00" + struct.pack(">IIII", start, end, 0xFFFFFFFF, 0xFFFFFFFF)
    return out + b"".join(_frame_bytes(sub, version, False) for sub in fr.sub_frames)


_BUILDERS = {"text": _b_text, "TXXX": _b_txxx, "APIC": _b_apic, "USLT": _b_lang_desc_text,
             "COMM": _b_lang_desc_text, "SYLT": _b_sylt, "CHAP": _b_chap}

_V22 = {"TIT2": "TT2", "TPE1": "TP1", "TPE2": "TP2", "TALB": "TAL", "TCOM": "TCM", "TRCK": "TRK",
        "APIC": "PIC", "USLT": "ULT", "SYLT": "SLT", "COMM": "COM", "TXXX": "TXX"}


def _unsync(data: bytes) -> bytes:
    out = bytearray()
    for i, b in enumerate(data):
        out.append(b)
        if b == 0xFF and (i + 1 == len(data) or data[i + 1] >= 0xE0 or data[i + 1] == 0):
            out.append(0)
    return bytes(out)


def _frame_bytes(fr: Frame, version: int, plain_sizes: bool, tag_unsync: bool = False) -> bytes:
    body = fr.body(version)
    if version == 2:
        return _V22[fr.id].encode() + len(body).to_bytes(3, "big") + body
    if version == 3:
        flags = 0
        if fr.flags & 0x08:
            body = struct.pack(">I", len(body)) + zlib.compress(body)
            flags |= 0x80
        return fr.id.encode() + struct.pack(">I", len(body)) + bytes([0, flags]) + body
    flags = 0
    extra = b""
    plain_len = len(body)
    if fr.flags & 0x08:
        body = zlib.compress(body)
        flags |= 0x08 | 0x01
    if fr.flags & 0x02 or tag_unsync:
        body = _unsync(body)
        flags |= 0x02
    if fr.flags & 0x01 or flags & 0x01:
        extra = _syncsafe(plain_len)
        flags |= 0x01
    body = extra + body
    size = struct.pack(">I", len(body)) if plain_sizes else _syncsafe(len(body))
    return fr.id.encode() + size + bytes([0, flags]) + body


def id3_tag(frames: Iterable[Frame], *, version: int = 4, unsync: bool = False,
            ext_header: bool = False, footer: bool = False, padding: int = 32,
            plain_sizes: bool = False) -> bytes:
    """A whole ID3v2.``version`` tag. ``unsync`` unsynchronises the tag
    (v2.2/v2.3: the whole tag; v2.4: every frame); ``plain_sizes`` writes
    v2.4 frame sizes as plain 32-bit integers, as some old writers did."""
    flags = 0
    data = b""
    if ext_header and version == 3:
        data += struct.pack(">IHI", 6, 0, padding)
        flags |= 0x40
    elif ext_header and version == 4:
        data += _syncsafe(6) + b"\x01\x00"
        flags |= 0x40
    data += b"".join(_frame_bytes(f, version, plain_sizes, unsync and version == 4) for f in frames)
    data += b"\x00" * (0 if footer else padding)
    if unsync:
        flags |= 0x80
        if version < 4:
            data = _unsync(data)
    if footer and version == 4:
        flags |= 0x10
    header = b"ID3" + bytes([version, 0, flags]) + _syncsafe(len(data))
    out = header + data
    if footer and version == 4:
        out += b"3DI" + bytes([version, 0, flags]) + _syncsafe(len(data))
    return out


def id3v1(title: str = "", artist: str = "", album: str = "", year: str = "",
          comment: str = "", track: int = 0, genre: int = 255) -> bytes:
    def fit(s: str, n: int) -> bytes:
        return s.encode("latin-1")[:n].ljust(n, b"\x00")
    c = fit(comment, 28) + b"\x00" + bytes([track]) if track else fit(comment, 30)
    return b"TAG" + fit(title, 30) + fit(artist, 30) + fit(album, 30) + fit(year, 4) + c + bytes([genre])


def write_mp3(path: Path, frames: Iterable[Frame] = (), *, audio: bytes | None = None,
              v1: bytes = b"", **tag_kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = list(frames)
    tag = id3_tag(frames, **tag_kw) if frames else b""
    path.write_bytes(tag + (silent_mp3() if audio is None else audio) + v1)
    return path


# ─── FLAC and Vorbis comments ────────────────────────────────────────────


def vorbis_comment(items: Iterable[tuple[str, str]], vendor: str = "domovoi tests") -> bytes:
    items = list(items)
    v = vendor.encode()
    out = struct.pack("<I", len(v)) + v + struct.pack("<I", len(items))
    for key, value in items:
        raw = key.encode("ascii") + b"=" + value.encode("utf-8")
        out += struct.pack("<I", len(raw)) + raw
    return out


def _items(comments: dict[str, Sequence[str]] | Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    if isinstance(comments, dict):
        return [(k, v) for k, vs in comments.items() for v in vs]
    return list(comments)


def flac_picture(ptype: int, mime: str, data: bytes, desc: str = "") -> bytes:
    """A FLAC PICTURE block body (also what METADATA_BLOCK_PICTURE base64s)."""
    m, d = mime.encode("ascii"), desc.encode("utf-8")
    return (struct.pack(">II", ptype, len(m)) + m + struct.pack(">I", len(d)) + d
            + struct.pack(">IIIII", 0, 0, 0, 0, len(data)) + data)


def streaminfo(rate: int = 44100, samples: int = 0, channels: int = 1, bits: int = 16) -> bytes:
    info = struct.pack(">HH", 4096, 4096) + b"\x00" * 6
    info += ((rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples).to_bytes(8, "big")
    return info + b"\x00" * 16


def flac_file(comments=(), pictures: Sequence[tuple[int, str, bytes]] = (), *,
              rate: int = 44100, samples: int = 0, id3_prefix: bytes = b"") -> bytes:
    blocks = [(0, streaminfo(rate, samples))]
    if comments:
        blocks.append((4, vorbis_comment(_items(comments))))
    for ptype, mime, data in pictures:
        blocks.append((6, flac_picture(ptype, mime, data)))
    out = id3_prefix + b"fLaC"
    for i, (btype, body) in enumerate(blocks):
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes([last | btype]) + len(body).to_bytes(3, "big") + body
    return out


def bare_flac() -> bytes:
    """``fLaC`` and one (last) STREAMINFO block: 44.1 kHz, mono, 16 bit."""
    return flac_file()


# ─── Ogg ─────────────────────────────────────────────────────────────────

def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        table.append(r & 0xFFFFFFFF)
    return table


_CRC = _crc_table()


def ogg_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) & 0xFF) ^ b]
    return crc


def ogg_stream(packets: Sequence[tuple[bytes, int]], serial: int = 0x0D0E,
               max_page: int = 4096) -> bytes:
    """One logical stream; each ``(packet, granule)`` starts a new page and
    spills over continuation pages when it is longer than ``max_page``."""
    out = b""
    seq = 0
    step = max(255, (max_page // 255) * 255)   # a page a packet continues past ends on a full segment
    for n, (packet, granule) in enumerate(packets):
        chunks = [packet[i:i + step] for i in range(0, len(packet), step)] or [b""]
        if len(packet) % step == 0 and packet:
            chunks.append(b"")                  # the packet's 0-length closing segment
        for c, chunk in enumerate(chunks):
            last_chunk = c == len(chunks) - 1
            lacing = [255] * (len(chunk) // 255)
            if last_chunk:
                lacing.append(len(chunk) % 255)
            flags = (0x01 if c else 0) | (0x02 if seq == 0 else 0)
            if n == len(packets) - 1 and last_chunk:
                flags |= 0x04
            pos = granule if last_chunk else -1
            head = b"OggS" + bytes([0, flags]) + struct.pack("<qIII", pos, serial, seq, 0) + bytes([len(lacing)]) + bytes(lacing)
            page = head + chunk
            crc = ogg_crc(page)
            out += page[:22] + struct.pack("<I", crc) + page[26:]
            seq += 1
    return out


def ogg_file(kind: str, comments=(), *, granule: int | None = None) -> bytes:
    """An Opus or a Vorbis stream: identification header, a comment
    header, Vorbis' setup header, then one audio packet."""
    items = _items(comments)
    if kind == "opus":
        head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, 48000, 0, 0)
        tags = b"OpusTags" + vorbis_comment(items, "test")
        return ogg_stream([(head, 0), (tags, 0), (b"\xf8\xff\xfe", 960 if granule is None else granule)])
    ident = b"\x01vorbis" + struct.pack("<IBIiii", 0, 1, 44100, 0, 128000, 0) + bytes([0xB8, 1])
    comment = b"\x03vorbis" + vorbis_comment(items, "test") + b"\x01"
    return ogg_stream([(ident, 0), (comment, 0), (b"\x05vorbis" + b"\x00" * 8, 0),
                       (b"\x00" * 4, 44100 if granule is None else granule)])


def bare_ogg(kind: str) -> bytes:
    return ogg_file(kind)


# ─── MP4 ─────────────────────────────────────────────────────────────────


def atom(name: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I", 8 + len(payload)) + name + payload


def full_atom(name: bytes, flags: int, payload: bytes) -> bytes:
    return atom(name, struct.pack(">I", flags) + payload)


def mp4_data(dtype: int, value: bytes) -> bytes:
    return atom(b"data", struct.pack(">II", dtype, 0) + value)


def mp4_text(name: str, *values: str) -> tuple[bytes, list[bytes]]:
    return name.encode("latin-1"), [mp4_data(1, v.encode("utf-8")) for v in values]


def mp4_cover(data: bytes, png: bool = False) -> tuple[bytes, list[bytes]]:
    return b"covr", [mp4_data(14 if png else 13, data)]


def m4a_file(items: Sequence[tuple[bytes, list[bytes]]] = (), *, timescale: int = 44100,
             duration: int = 44100) -> bytes:
    """ftyp + a moov with one sound track (and ``udta.meta.ilst`` when
    there are items) + an empty mdat."""
    mvhd = full_atom(b"mvhd", 0, struct.pack(">IIII", 0, 0, 1000, 1000) + b"\x00" * 80)
    mdhd = full_atom(b"mdhd", 0, struct.pack(">IIIIHH", 0, 0, timescale, duration, 0, 0))
    hdlr = full_atom(b"hdlr", 0, b"\x00" * 4 + b"soun" + b"\x00" * 13)
    trak = atom(b"trak", full_atom(b"tkhd", 7, b"\x00" * 80) + atom(b"mdia", mdhd + hdlr))
    udta = b""
    if items:
        ilst = atom(b"ilst", b"".join(atom(name, b"".join(datas)) for name, datas in items))
        meta_hdlr = full_atom(b"hdlr", 0, b"\x00" * 4 + b"mdirappl" + b"\x00" * 9)
        udta = atom(b"udta", full_atom(b"meta", 0, meta_hdlr + ilst))
    return (atom(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42isom")
            + atom(b"moov", mvhd + trak + udta) + atom(b"mdat", b"\x00" * 16))


def bare_m4a() -> bytes:
    return m4a_file()


# ─── ASF ─────────────────────────────────────────────────────────────────


def _guid(text: str) -> bytes:
    return uuid.UUID(text).bytes_le


def _asf_obj(guid: str, body: bytes) -> bytes:
    return _guid(guid) + struct.pack("<Q", 24 + len(body)) + body


def _utf16z(s: str) -> bytes:
    return s.encode("utf-16-le") + b"\x00\x00"


def asf_picture(ptype: int, mime: str, data: bytes, desc: str = "") -> bytes:
    """A ``WM/Picture`` value."""
    return bytes([ptype]) + struct.pack("<I", len(data)) + _utf16z(mime) + _utf16z(desc) + data


def asf_file(*, title: str = "", author: str = "", extended: Sequence[tuple[str, int, bytes]] = (),
             play_100ns: int = 30_000_000, preroll_ms: int = 0) -> bytes:
    """An ASF header (file properties, content description, extended
    content description) and an empty data object. ``extended`` items are
    ``(name, ASF type, raw value)``; text is :func:`asf_text`."""
    fp = (b"\x00" * 16 + struct.pack("<QQQQQQ", 0, 0, 0, play_100ns, play_100ns, preroll_ms)
          + struct.pack("<IIII", 2, 0, 0, 0))
    objs = _asf_obj("8CABDCA1-A947-11CF-8EE4-00C00C205365", fp)
    strings = [_utf16z(s) if s else b"" for s in (title, author, "", "", "")]
    objs += _asf_obj("75B22633-668E-11CF-A6D9-00AA0062CE6C",
                     struct.pack("<5H", *(len(s) for s in strings)) + b"".join(strings))
    if extended:
        body = struct.pack("<H", len(extended))
        for name, dtype, raw in extended:
            n = _utf16z(name)
            body += struct.pack("<H", len(n)) + n + struct.pack("<HH", dtype, len(raw)) + raw
        objs += _asf_obj("D2D0A440-E307-11D2-97F0-00A0C95EA850", body)
    count = 3 if extended else 2
    header_body = struct.pack("<IBB", count, 1, 2) + objs
    header = _guid("75B22630-668E-11CF-A6D9-00AA0062CE6C") + struct.pack("<Q", 24 + len(header_body)) + header_body
    data = _guid("75B22636-668E-11CF-A6D9-00AA0062CE6C") + struct.pack("<Q", 50) + b"\x00" * 26
    return header + data


def asf_text(s: str) -> bytes:
    return _utf16z(s)


# ─── APEv2 ───────────────────────────────────────────────────────────────


def ape_tag(items: Sequence[tuple[str, str | bytes]], *, header: bool = False) -> bytes:
    """An APEv2 tag (footer, and a header when asked). A ``str`` value is
    text (several values NUL-separated), ``bytes`` binary."""
    body = b""
    for key, value in items:
        raw = value.encode("utf-8") if isinstance(value, str) else value
        flags = 0 if isinstance(value, str) else 2
        body += struct.pack("<II", len(raw), flags) + key.encode("ascii") + b"\x00" + raw
    size = len(body) + 32
    flags = 0x80000000 if header else 0

    def block(is_header: bool) -> bytes:
        f = flags | (0x20000000 if is_header else 0)
        return b"APETAGEX" + struct.pack("<IIII", 2000, size, len(items), f) + b"\x00" * 8

    return (block(True) if header else b"") + body + block(False)


# ─── WAV / AIFF ──────────────────────────────────────────────────────────


def wav_file(*, seconds: float = 0.5, rate: int = 8000, id3: bytes = b"", chunk_id: bytes = b"id3 ") -> bytes:
    frames = int(seconds * rate)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data = b"\x00\x00" * frames
    chunks += b"data" + struct.pack("<I", len(data)) + data
    if id3:
        chunks += chunk_id + struct.pack("<I", len(id3)) + id3 + (b"\x00" if len(id3) & 1 else b"")
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
