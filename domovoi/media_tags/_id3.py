"""ID3v2.2 / 2.3 / 2.4 and ID3v1, read-only.

Written from the ID3v2.2, v2.3 and v2.4 informal standards (id3.org) and
the ID3v2 Chapter Frame Addendum. What Domovoi reads from a tag: the text
frames, APIC/PIC pictures, USLT/ULT and SYLT/SLT lyrics, CHAP chapters;
everything else is kept as raw bytes under its frame id.

A v2.2 tag's three-letter ids are reported as their v2.3/v2.4 names (TT2
as TIT2, PIC as APIC, ULT as USLT, SLT as SYLT), so a caller asks for one
name whatever the version.

What a tag may cost is bounded (2026-10-16 delta review B1/S2): compressed
frames are inflated only up to :data:`INFLATE_FRAME_MAX` each and
:data:`INFLATE_TAG_MAX` per tag, and only when the frame is one this reader
interprets; a chapter is not read inside another chapter; and the frames,
values and synced-lyrics entries count against the read's item budget
(``_util.MAX_ITEMS``), past which the rest of the tag is left unread.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from domovoi.media_tags._model import Tags
from domovoi.media_tags._util import (
    MAX_BLOCK,
    MAX_ITEMS,
    BudgetExceeded,
    TagError,
    budget,
    one_read,
    syncsafe,
    u32be,
)

#: The most one compressed frame may inflate to. A cover picture the
#: dashboard shows is at most 8 MiB, and text, lyrics and chapters are far
#: smaller; a frame that inflates past this is dropped, as one that doesn't
#: inflate at all always was.
INFLATE_FRAME_MAX = 8 * 1024 * 1024
#: The most all of one tag's compressed frames may inflate to together.
INFLATE_TAG_MAX = 16 * 1024 * 1024
#: The most values one text frame, and entries one synced-lyrics frame, may
#: have; a frame with more is dropped, so one such frame can't use up the
#: read's item budget and cost the frames after it.
MAX_FRAME_VALUES = 1024
MAX_SYNCED_ENTRIES = 8192

# ─── frames ──────────────────────────────────────────────────────────────


@dataclass
class Frame:
    id: str

    @property
    def hash_key(self) -> str:
        return self.id


@dataclass
class TextFrame(Frame):
    encoding: int = 3
    text: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return self.text[0] if self.text else ""


@dataclass
class UserTextFrame(Frame):
    """TXXX: a described text value."""

    encoding: int = 3
    desc: str = ""
    text: list[str] = field(default_factory=list)

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.desc}"

    def __str__(self) -> str:
        return self.text[0] if self.text else ""


@dataclass
class CommentFrame(Frame):
    """COMM."""

    encoding: int = 3
    lang: str = ""
    desc: str = ""
    text: str = ""

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.desc}:{self.lang}"

    def __str__(self) -> str:
        return self.text


@dataclass
class PictureFrame(Frame):
    """APIC (and v2.2's PIC, reported as APIC)."""

    encoding: int = 0
    mime: str = ""
    type: int = 0
    desc: str = ""
    data: bytes = b""

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.desc}"


@dataclass
class LyricsFrame(Frame):
    """USLT (and v2.2's ULT): unsynchronised lyrics."""

    encoding: int = 3
    lang: str = ""
    desc: str = ""
    text: str = ""

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.desc}:{self.lang}"

    def __str__(self) -> str:
        return self.text


@dataclass
class SyncedLyricsFrame(Frame):
    """SYLT (and v2.2's SLT). ``format`` 1 is MPEG frames, 2 milliseconds;
    ``text`` is ``(text, timestamp)`` pairs in frame order."""

    encoding: int = 3
    lang: str = ""
    format: int = 2
    type: int = 1
    desc: str = ""
    text: list[tuple[str, int]] = field(default_factory=list)

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.desc}:{self.lang}"


@dataclass
class ChapterFrame(Frame):
    """CHAP: one chapter, its times in milliseconds and its own frames
    (usually a TIT2 title)."""

    element_id: str = ""
    start_time: int = 0
    end_time: int = 0
    start_offset: int = 0
    end_offset: int = 0
    sub_frames: list[Frame] = field(default_factory=list)

    @property
    def hash_key(self) -> str:
        return f"{self.id}:{self.element_id}"


@dataclass
class RawFrame(Frame):
    data: bytes = b""


class ID3Tags(Tags):
    """An ID3 tag's frames in file order. ``get("TIT2")`` answers the text
    values of the TIT2 frames; a key with a colon (``TXXX:desc``,
    ``USLT:desc:lang``) matches a frame's full key and answers frames."""

    kind = "id3"

    def __init__(self, frames: list[Frame] | None = None, version: tuple[int, int] = (4, 0)) -> None:
        super().__init__([(f.hash_key, f) for f in (frames or [])])
        self.version = version

    @property
    def frames(self) -> list[Frame]:
        return [f for _, f in self.items]

    def getall(self, frame_id: str) -> list[Frame]:
        """Every frame with this id (``APIC``, ``USLT``, ``CHAP``), in order."""
        return [f for f in self.frames if f.id == frame_id]

    def get(self, key: str) -> list[Any]:
        if not isinstance(key, str):
            return []
        if ":" in key:
            return [f for f in self.frames if f.hash_key == key]
        out: list[Any] = []
        for f in self.frames:
            if f.id != key:
                continue
            if isinstance(f, (TextFrame, UserTextFrame)):
                out.extend(f.text)
            else:
                out.append(f)
        return out

    def __contains__(self, key: object) -> bool:
        return bool(self.get(key)) if isinstance(key, str) else False

    def add(self, frame: Frame) -> None:
        self.items.append((frame.hash_key, frame))


# ─── text ────────────────────────────────────────────────────────────────

_WIDE = (1, 2)


def _terminator(encoding: int) -> bytes:
    return b"\x00\x00" if encoding in _WIDE else b"\x00"


def _find_terminator(encoding: int, data: bytes, start: int = 0) -> int:
    """Index of the string terminator at or after ``start`` (aligned to two
    bytes for UTF-16), or -1."""
    if encoding not in _WIDE:
        return data.find(b"\x00", start)
    i = start
    while True:
        i = data.find(b"\x00\x00", i)
        if i < 0:
            return -1
        if (i - start) % 2 == 0:
            return i
        i += 1


def _decode(encoding: int, data: bytes, order: list[str] | None = None) -> str:
    """Text in one of ID3's four encodings. ``order`` carries a UTF-16 byte
    order from one value to the next when a later one has no BOM."""
    if encoding == 0:
        return data.decode("latin-1")
    if encoding == 3:
        return data.decode("utf-8", "replace")
    if encoding == 2:
        return data.decode("utf-16-be", "replace")
    if encoding == 1:
        if data[:2] == b"\xff\xfe":
            if order is not None:
                order[:] = ["utf-16-le"]
            return data[2:].decode("utf-16-le", "replace")
        if data[:2] == b"\xfe\xff":
            if order is not None:
                order[:] = ["utf-16-be"]
            return data[2:].decode("utf-16-be", "replace")
        codec = order[0] if order else "utf-16-le"
        return data.decode(codec, "replace")
    raise TagError(f"unknown ID3 text encoding {encoding}")


def _take_string(encoding: int, data: bytes, pos: int) -> tuple[str, int]:
    """A terminated string at ``pos`` and the position after it (the end of
    ``data`` when it has no terminator)."""
    end = _find_terminator(encoding, data, pos)
    if end < 0:
        return _decode(encoding, data[pos:]), len(data)
    return _decode(encoding, data[pos:end]), end + len(_terminator(encoding))


def _split_values(encoding: int, data: bytes) -> list[str]:
    """A text frame's values: NUL-separated, trailing terminators dropped."""
    term = _terminator(encoding)
    parts: list[bytes] = []
    pos = 0
    allowance = budget()
    while pos <= len(data):
        if len(parts) >= MAX_FRAME_VALUES:
            raise TagError("too many values in one frame")
        allowance.item()
        end = _find_terminator(encoding, data, pos)
        if end < 0:
            parts.append(data[pos:])
            break
        parts.append(data[pos:end])
        pos = end + len(term)
        if pos == len(data):
            break
    while parts and parts[-1] == b"":
        parts.pop()
    order: list[str] = []
    return [_decode(encoding, p, order) for p in parts]


# ─── frame bodies ────────────────────────────────────────────────────────

_V22_IDS = {
    "TT1": "TIT1", "TT2": "TIT2", "TT3": "TIT3", "TP1": "TPE1", "TP2": "TPE2",
    "TP3": "TPE3", "TP4": "TPE4", "TCM": "TCOM", "TXT": "TEXT", "TLA": "TLAN",
    "TCO": "TCON", "TAL": "TALB", "TPA": "TPOS", "TRK": "TRCK", "TRC": "TSRC",
    "TYE": "TYER", "TDA": "TDAT", "TIM": "TIME", "TRD": "TRDA", "TMT": "TMED",
    "TFT": "TFLT", "TBP": "TBPM", "TCR": "TCOP", "TPB": "TPUB", "TEN": "TENC",
    "TSS": "TSSE", "TOF": "TOFN", "TLE": "TLEN", "TSI": "TSIZ", "TDY": "TDLY",
    "TKE": "TKEY", "TOT": "TOAL", "TOA": "TOPE", "TOL": "TOLY", "TOR": "TORY",
    "TXX": "TXXX", "TCP": "TCMP", "TS2": "TSO2", "TSA": "TSOA", "TSC": "TSOC",
    "TSP": "TSOP", "TST": "TSOT", "WAF": "WOAF", "WAR": "WOAR", "WAS": "WOAS",
    "WCM": "WCOM", "WCP": "WCOP", "WPB": "WPUB", "WXX": "WXXX", "IPL": "IPLS",
    "MCI": "MCDI", "ETC": "ETCO", "MLL": "MLLT", "STC": "SYTC", "ULT": "USLT",
    "SLT": "SYLT", "COM": "COMM", "RVA": "RVAD", "EQU": "EQUA", "REV": "RVRB",
    "PIC": "APIC", "GEO": "GEOB", "CNT": "PCNT", "POP": "POPM", "BUF": "RBUF",
    "CRA": "AENC", "LNK": "LINK",
}

_PIC_FORMATS = {"JPG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "BMP": "image/bmp"}


def _parse_frame(fid: str, body: bytes, major: int, bpi: int, depth: int = 0,
                 state: "_TagState | None" = None) -> Frame | None:
    """One frame's body as a :class:`Frame`; None when it is too short to be
    one (a malformed frame is dropped, not fatal to the tag), and for a
    chapter inside a chapter (``depth`` > 0): the addendum's chapters hold
    titles and pictures, and nesting them let every level copy the rest of
    the tag again (the review's 4 MiB file reached 3 GiB)."""
    try:
        if fid == "TXXX":
            enc = body[0]
            desc, pos = _take_string(enc, body, 1)
            return UserTextFrame(fid, enc, desc, _split_values(enc, body[pos:]))
        if fid.startswith("T"):
            enc = body[0]
            return TextFrame(fid, enc, _split_values(enc, body[1:]))
        if fid == "APIC":
            enc = body[0]
            if major == 2:
                fmt = body[1:4].decode("latin-1").upper()
                mime = _PIC_FORMATS.get(fmt, "image/" + fmt.lower())
                pos = 4
            else:
                end = body.find(b"\x00", 1)
                if end < 0:
                    return None
                mime = body[1:end].decode("latin-1")
                pos = end + 1
            ptype = body[pos]
            desc, pos = _take_string(enc, body, pos + 1)
            return PictureFrame(fid, enc, mime, ptype, desc, bytes(body[pos:]))
        if fid in ("USLT", "COMM"):
            enc = body[0]
            lang = body[1:4].decode("latin-1")
            desc, pos = _take_string(enc, body, 4)
            text = _decode(enc, body[pos:]).rstrip("\x00")
            cls = LyricsFrame if fid == "USLT" else CommentFrame
            return cls(fid, enc, lang, desc, text)
        if fid == "SYLT":
            enc = body[0]
            lang = body[1:4].decode("latin-1")
            fmt, ctype = body[4], body[5]
            desc, pos = _take_string(enc, body, 6)
            entries: list[tuple[str, int]] = []
            order: list[str] = []
            allowance = budget()
            while pos < len(body):
                if len(entries) >= MAX_SYNCED_ENTRIES:
                    raise TagError("too many synced lyrics entries")
                allowance.item()
                end = _find_terminator(enc, body, pos)
                if end < 0:
                    break
                text = _decode(enc, body[pos:end], order)
                pos = end + len(_terminator(enc))
                if pos + 4 > len(body):
                    break
                entries.append((text, u32be(body, pos)))
                pos += 4
            return SyncedLyricsFrame(fid, enc, lang, fmt, ctype, desc, entries)
        if fid == "CHAP":
            if depth > 0:
                return None
            end = body.find(b"\x00")
            if end < 0 or end + 17 > len(body):
                return None
            element = body[:end].decode("latin-1")
            pos = end + 1
            times = [u32be(body, pos + 4 * i) for i in range(4)]
            subs = list(_walk_frames(body, major, False, bpi, pos + 16, len(body),
                                     depth=depth + 1, state=state))
            return ChapterFrame(fid, element, times[0], times[1], times[2], times[3], subs)
    except (IndexError, TagError):
        return None
    return RawFrame(fid, bytes(body))


# ─── the frame walk ──────────────────────────────────────────────────────

_ID4 = re.compile(rb"[A-Z0-9]{4}\Z")
_ID3 = re.compile(rb"[A-Z0-9]{3}\Z")


def _deunsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def _headers(data: bytes, major: int, bpi: int, start: int = 0, end: int | None = None):
    """``(frame id, flags, body start, body size)`` for each frame header in
    ``data[start:end]``, until padding, the end, something that is not a
    frame header, or :data:`MAX_ITEMS` headers. The last item may be
    ``None`` to say the walk ran into a header that doesn't fit (used to
    pick how a v2.4 tag's frame sizes are written). Offsets, not slices:
    walking a tag copies none of it."""
    end = len(data) if end is None else min(end, len(data))
    size_len, head = (3, 6) if major == 2 else (4, 10)
    valid = _ID3 if major == 2 else _ID4
    pos = start
    seen = 0
    while pos + head <= end and seen < MAX_ITEMS:
        if data[pos] == 0:
            return
        fid = data[pos:pos + size_len]
        if not valid.match(fid):
            yield None
            return
        if major == 2:
            size = int.from_bytes(data[pos + 3:pos + 6], "big")
            flags = 0
        else:
            raw = data[pos + 4:pos + 8]
            size = syncsafe(raw) if bpi == 7 else u32be(raw)
            flags = int.from_bytes(data[pos + 8:pos + 10], "big")
        body = pos + head
        if body + size > end:
            yield None
            return
        yield fid.decode("ascii"), flags, body, size
        seen += 1
        pos = body + size


def _clean_walk(data: bytes, major: int, bpi: int, start: int = 0, end: int | None = None) -> bool:
    for item in _headers(data, major, bpi, start, end):
        if item is None:
            return False
    return True


def _frame_bits(data: bytes, major: int, start: int = 0, end: int | None = None) -> int:
    """How this tag writes frame sizes: v2.4 says syncsafe (7 bits a byte),
    but some writers used plain 32-bit sizes. Syncsafe unless only the plain
    reading walks the tag cleanly."""
    if major != 4:
        return 8
    if _clean_walk(data, major, 7, start, end):
        return 7
    if _clean_walk(data, major, 8, start, end):
        return 8
    return 7


class _TagState:
    """What one tag has left to inflate (shared with its chapters' frames)."""

    __slots__ = ("inflate_left",)

    def __init__(self) -> None:
        self.inflate_left = INFLATE_TAG_MAX


def _interpreted(fid: str) -> bool:
    """A frame this reader makes something of; any other is kept raw."""
    return fid.startswith("T") or fid in ("APIC", "USLT", "COMM", "SYLT", "CHAP")


def _inflate(fid: str, data: bytes, state: _TagState) -> bytes | None:
    """A compressed frame's body, inflated; None drops the frame: one this
    reader doesn't interpret (its inflated bytes would only be kept, never
    read), one that isn't a whole zlib stream, or one that would inflate
    past :data:`INFLATE_FRAME_MAX`, what is left of :data:`INFLATE_TAG_MAX`
    or what is left of the read's budget. No more than that is ever made to
    find out (``zlib.decompress`` had made all of it: delta review B1)."""
    if not _interpreted(fid):
        return None
    allowance = budget()
    limit = min(INFLATE_FRAME_MAX, state.inflate_left, allowance.bytes_left)
    if limit <= 0:
        return None
    d = zlib.decompressobj()
    try:
        # One byte past the limit tells "exactly the limit" from "more".
        out = d.decompress(data, limit + 1)
    except zlib.error:
        return None
    if len(out) > limit or not d.eof:
        return None          # bigger than allowed, or cut short
    state.inflate_left -= len(out)
    allowance.spend(len(out))
    return out


def _walk_frames(data: bytes, major: int, tag_unsync: bool, bpi: int, start: int = 0,
                 end: int | None = None, *, depth: int = 0, state: _TagState | None = None):
    """The frames in ``data[start:end]``, each counted against the read's
    item budget: :class:`BudgetExceeded` ends the walk, and the caller keeps
    the frames that came before."""
    state = state or _TagState()
    allowance = budget()
    for item in _headers(data, major, bpi, start, end):
        if item is None:
            return
        allowance.item()
        fid, flags, at, size = item
        if depth and fid == "CHAP":
            continue               # a chapter in a chapter: not read (_parse_frame)
        if major == 2:
            fid = _V22_IDS.get(fid, fid)
            body = data[at:at + size]
        elif major == 3:
            fmt = flags & 0xFF
            pos = 0
            if fmt & 0x80:
                pos += 4           # decompressed size
            if fmt & 0x40:
                continue           # encrypted: unreadable
            if fmt & 0x20:
                pos += 1           # group id
            body = data[at + min(pos, size):at + size]
            if fmt & 0x80:
                body = _inflate(fid, body, state)
                if body is None:
                    continue
        else:
            fmt = flags & 0xFF
            pos = 0
            if fmt & 0x40:
                pos += 1           # group id
            if fmt & 0x04:
                continue           # encrypted: unreadable
            if fmt & 0x01:
                pos += 4           # data length indicator
            body = data[at + min(pos, size):at + size]
            if fmt & 0x02 or tag_unsync:
                body = _deunsync(body)
            if fmt & 0x08:
                body = _inflate(fid, body, state)
                if body is None:
                    continue
        frame = _parse_frame(fid, body, major, bpi, depth, state)
        if frame is not None:
            yield frame


# ─── the tag ─────────────────────────────────────────────────────────────


def tag_size(header: bytes) -> int | None:
    """The full length of the ID3v2 tag starting with ``header`` (10 bytes),
    header and footer included; None when it isn't one."""
    if len(header) < 10 or header[:3] != b"ID3" or header[3] == 0xFF or header[4] == 0xFF:
        return None
    if any(b & 0x80 for b in header[6:10]):
        return None
    size = 10 + syncsafe(header[6:10])
    if header[3] == 4 and header[5] & 0x10:
        size += 10
    return size


@one_read
def parse_id3v2(data: bytes) -> ID3Tags | None:
    """The tag that ``data`` starts with (the whole tag, header included);
    None when it isn't an ID3v2 tag of a version this reads (2, 3, 4). A
    tag of more frames than one read may make (``_util.MAX_ITEMS``) gives
    the frames that fit."""
    size = tag_size(data[:10])
    if size is None:
        return None
    major, minor, flags = data[3], data[4], data[5]
    if major not in (2, 3, 4):
        return None
    body, start, end = data, 10, min(len(data), 10 + syncsafe(data[6:10]))
    tags = ID3Tags(version=(major, minor))
    if major == 2 and flags & 0x40:
        return tags            # v2.2 "compression": no scheme was ever defined
    if flags & 0x80 and major < 4:
        body = _deunsync(data[start:end])
        start, end = 0, len(body)
    if flags & 0x40 and major == 3 and end - start >= 4:
        start += 4 + u32be(body, start)
    elif flags & 0x40 and major == 4 and end - start >= 4:
        start += syncsafe(body[start:start + 4])
    bpi = _frame_bits(body, major, start, end)
    try:
        for frame in _walk_frames(body, major, bool(flags & 0x80) and major == 4, bpi, start, end):
            tags.add(frame)
    except BudgetExceeded:
        pass
    return tags


def read_id3v2_at(f: BinaryIO, offset: int = 0) -> tuple[ID3Tags | None, int]:
    """The ID3v2 tag at ``offset`` and its length (0 when there is none)."""
    f.seek(offset)
    header = f.read(10)
    size = tag_size(header)
    if size is None or size > MAX_BLOCK:
        return None, 0
    budget().spend(size)
    f.seek(offset)
    data = f.read(size)          # a truncated file gives what is there
    return parse_id3v2(data), size


# ─── ID3v1 ───────────────────────────────────────────────────────────────

GENRES_COUNT = 192


def _v1_text(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("latin-1").rstrip(" ")


def parse_id3v1(block: bytes) -> list[Frame]:
    """The frames of a 128-byte ``TAG`` block (title, artist, album, year,
    comment, track): only the ones it fills in."""
    if len(block) != 128 or block[:3] != b"TAG":
        return []
    frames: list[Frame] = []
    for fid, raw in (("TIT2", block[3:33]), ("TPE1", block[33:63]), ("TALB", block[63:93]),
                     ("TDRC", block[93:97])):
        text = _v1_text(raw)
        if text:
            frames.append(TextFrame(fid, 0, [text]))
    comment = block[97:127]
    if comment[28] == 0 and comment[29] != 0:
        frames.append(TextFrame("TRCK", 0, [str(comment[29])]))
        comment = comment[:28]
    text = _v1_text(comment)
    if text:
        frames.append(CommentFrame("COMM", 0, "eng", "ID3v1 Comment", text))
    return frames


def read_id3v1(f: BinaryIO, size: int) -> list[Frame]:
    if size < 128:
        return []
    f.seek(size - 128)
    return parse_id3v1(f.read(128))


def merge_v1(tags: ID3Tags | None, v1: list[Frame]) -> ID3Tags | None:
    """ID3v1 frames the v2 tag doesn't have, added after its own."""
    if not v1:
        return tags
    if tags is None:
        tags = ID3Tags(version=(1, 1))
    have = {f.hash_key for f in tags.frames}
    for frame in v1:
        if frame.hash_key not in have:
            tags.add(frame)
    return tags
