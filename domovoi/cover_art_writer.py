"""Putting a front cover into a music file, and taking it out again (V058).

The one place Domovoi changes a music file. Used by the Cover Art Archive
worker (``domovoi/workers/library_cover_art.py``) to write a cover, and by
the dashboard's **Undo** to put a file back as it was. Domovoi's tag
library only reads (``domovoi.media_tags``; mutagen is GPL and not used),
so this writes the two formats whose structure lets a picture be swapped
without touching anything else, from the format specifications:

* **MP3** (ID3v2.3 / 2.4 at the start of the file): the tag's frames are
  kept byte for byte, the front-cover pictures (``APIC`` type 3, and type 0
  "Other", which taggers use for the cover) are taken out, one ``APIC``
  type 3 is added. A file with no ID3 tag gets an ID3v2.3 tag holding only
  the picture. A tag that is unsynchronised as a whole, has a footer, or is
  ID3v2.2 is not written (rare, and rewriting it safely needs more than
  this module does).
* **FLAC**: the ``PICTURE`` blocks of type 3 / 0 are replaced by one of
  type 3; every other metadata block is kept byte for byte; the padding is
  remade. A FLAC file with an ID3 tag in front is not written.

Everything else (M4A/MP4, Ogg Vorbis/Opus, WMA, WAV ...) is **not written**
(:func:`writable_kind` None): the worker keeps the picture in Domovoi's own
cover folder instead and the dashboard shows it from there.

**How a write is done** (:func:`write_front_cover`): the file is read, a
new copy is made beside it (same folder, so same drive) with the new
tags and the audio copied unchanged, the copy is read back (it must parse,
hold the new cover, keep the length, and its audio bytes must hash the
same as the original's), the original is checked not to have changed
since it was read (size and modification time), and only then does the
copy replace it in one rename. A folder or file Domovoi may not write is
skipped (:class:`CoverWriteSkipped`, with a plain reason).

**The snapshot.** Before the first write to a file, the pictures it is
about to lose are saved, as they were (the raw frames or blocks: bytes,
type, description), in a JSON file in Domovoi's cover folder
(``~/.domovoi/cover_art/originals``), never in the music folder. Later
writes keep that first snapshot, so :func:`undo` always returns the file
to how it was before Domovoi touched it: it takes out the picture Domovoi
wrote and puts the saved ones back (a file that had none just loses the
added picture; a tag Domovoi had to create is removed again).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Pictures Domovoi treats as "the cover": ID3/FLAC type 3 (front) and 0
#: (other — what a tagger that doesn't ask writes).
COVER_TYPES = (3, 0)
FRONT = 3
#: Padding left after a written tag / metadata (so a tagger editing the
#: file later rarely has to rewrite it whole).
PADDING = 2048
#: Largest picture written.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
#: Largest tag / metadata read whole.
MAX_TAG_BYTES = 64 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


class CoverWriteSkipped(Exception):
    """The file was not changed; ``str(e)`` says why, in plain words."""


@dataclass
class _Frame:
    fid: str
    flags: int
    body: bytes


@dataclass
class _Parsed:
    kind: str                       # "id3" | "flac"
    head_len: int                   # bytes before the audio
    version: int = 3                # ID3 major version
    frames: list[_Frame] = field(default_factory=list)   # id3 frames / flac blocks (fid = str(type))
    created: bool = False           # no ID3 tag was there


# ─── images ─────────────────────────────────────────────────────────────


def sniff_image(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a JPEG or PNG from its header; None otherwise."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    pos = 2
    n = len(data)
    while pos + 4 <= n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if pos + 4 > n:
            break
        seg = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if pos + 9 <= n:
                h, w = struct.unpack(">HH", data[pos + 5:pos + 9])
                return w, h
            return None
        pos += 2 + seg
    return None


def same_picture(a: bytes | None, b: bytes | None) -> bool:
    """Byte-identical, or the same length and the same dimensions (a copy
    re-saved by another tagger is not worth a rewrite)."""
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) == len(b) and image_size(a) is not None and image_size(a) == image_size(b)


# ─── reading ─────────────────────────────────────────────────────────────


def _syncsafe(b: bytes) -> int:
    n = 0
    for byte in b:
        n = (n << 7) | (byte & 0x7F)
    return n


def _to_syncsafe(n: int) -> bytes:
    if n >= 1 << 28:
        raise CoverWriteSkipped("the tag would be too large")
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def writable_kind(path: Path) -> str | None:
    """``"id3"`` (an MP3 Domovoi can write), ``"flac"``, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
    except OSError:
        return None
    if head[:4] == b"fLaC":
        return "flac"
    ext = path.suffix.lower()
    if ext != ".mp3":
        return None
    if head[:3] == b"ID3":
        major, flags = head[3], head[5]
        if major not in (3, 4) or flags & 0x80 or (major == 4 and flags & 0x10):
            return None
        return "id3"
    # No tag at all: a bare MPEG stream (a frame sync at the start).
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "id3"
    return None


def _walk_id3(data: bytes, major: int, start: int, end: int) -> list[_Frame]:
    from domovoi.media_tags._id3 import _frame_bits

    bits = _frame_bits(data, major, start, end)
    frames: list[_Frame] = []
    pos = start
    while pos + 10 <= end:
        if data[pos] == 0:
            break
        fid = data[pos:pos + 4]
        if not all(48 <= c <= 57 or 65 <= c <= 90 for c in fid):
            break
        raw = data[pos + 4:pos + 8]
        size = _syncsafe(raw) if bits == 7 else struct.unpack(">I", raw)[0]
        flags = struct.unpack(">H", data[pos + 8:pos + 10])[0]
        if pos + 10 + size > end:
            raise CoverWriteSkipped("the file's tag is damaged")
        frames.append(_Frame(fid.decode("ascii"), flags, bytes(data[pos + 10:pos + 10 + size])))
        pos += 10 + size
    return frames


def _parse(path: Path, kind: str) -> _Parsed:
    with open(path, "rb") as f:
        head = f.read(10)
        if kind == "id3":
            if head[:3] != b"ID3":
                return _Parsed("id3", 0, 3, [], created=True)
            major, flags = head[3], head[5]
            size = _syncsafe(head[6:10])
            if size > MAX_TAG_BYTES:
                raise CoverWriteSkipped("the file's tag is too large")
            data = head + f.read(size)
            if len(data) < 10 + size:
                raise CoverWriteSkipped("the file is cut short")
            start = 10
            if flags & 0x40:  # an extended header: skipped (and not written back)
                if major == 3:
                    start = 14 + struct.unpack(">I", data[10:14])[0]
                else:
                    start = 10 + _syncsafe(data[10:14])
            return _Parsed("id3", 10 + size, major, _walk_id3(data, major, start, 10 + size))
        # FLAC
        if head[:4] != b"fLaC":
            raise CoverWriteSkipped("not a FLAC file Domovoi can write")
        f.seek(4)
        blocks: list[_Frame] = []
        total = 4
        while True:
            h = f.read(4)
            if len(h) < 4:
                raise CoverWriteSkipped("the file is cut short")
            last, btype = h[0] & 0x80, h[0] & 0x7F
            size = int.from_bytes(h[1:4], "big")
            if btype == 127 or total > MAX_TAG_BYTES:
                raise CoverWriteSkipped("the file's metadata is damaged")
            body = f.read(size)
            if len(body) < size:
                raise CoverWriteSkipped("the file is cut short")
            total += 4 + size
            blocks.append(_Frame(str(btype), 0, body))
            if last:
                break
        if not blocks or blocks[0].fid != "0":
            raise CoverWriteSkipped("the file's metadata is damaged")
        return _Parsed("flac", total, 0, blocks)


def _apic_info(fr: _Frame, major: int) -> tuple[int, str, str, bytes] | None:
    """(type, mime, desc, data) of an APIC frame body; None when the frame
    is compressed / encrypted / unsynchronised (left as it is)."""
    if fr.fid != "APIC":
        return None
    if major == 4 and fr.flags & 0x000F:   # v2.4: compression, encryption, unsync, length
        if fr.flags & 0x000E:
            return None
    if major == 3 and fr.flags & 0x00C0:   # v2.3: compression / encryption
        return None
    body = fr.body[4:] if (major == 4 and fr.flags & 0x0001) else fr.body
    try:
        from domovoi.media_tags._id3 import _parse_frame

        pf = _parse_frame("APIC", body, major, 7)
    except Exception:  # noqa: BLE001
        return None
    if pf is None or not hasattr(pf, "type"):
        return None
    return int(pf.type), str(pf.mime), str(pf.desc), bytes(pf.data)


def _flac_pic_info(fr: _Frame) -> tuple[int, str, str, bytes] | None:
    if fr.fid != "6":
        return None
    try:
        from domovoi.media_tags import parse_picture

        pic = parse_picture(fr.body)
    except Exception:  # noqa: BLE001
        return None
    return int(pic.type or 0), pic.mime, pic.desc, bytes(pic.data)


def _pic_info(parsed: _Parsed, fr: _Frame) -> tuple[int, str, str, bytes] | None:
    return _apic_info(fr, parsed.version) if parsed.kind == "id3" else _flac_pic_info(fr)


def current_cover(path: Path, kind: str | None = None) -> bytes | None:
    """The file's front cover as Domovoi would write over it (type 3, else
    type 0), or None. Only for formats Domovoi writes."""
    kind = kind or writable_kind(path)
    if kind is None:
        return None
    parsed = _parse(path, kind)
    best: tuple[int, bytes] | None = None
    for fr in parsed.frames:
        info = _pic_info(parsed, fr)
        if info and info[0] in COVER_TYPES:
            rank = COVER_TYPES.index(info[0])
            if best is None or rank < best[0]:
                best = (rank, info[3])
    return best[1] if best else None


def has_any_picture(path: Path) -> bool:
    kind = writable_kind(path)
    if kind is None:
        return False
    parsed = _parse(path, kind)
    return any(_pic_info(parsed, fr) for fr in parsed.frames)


# ─── building ────────────────────────────────────────────────────────────


def _apic_body(image: bytes, mime: str) -> bytes:
    return b"\x00" + mime.encode("latin-1") + b"\x00" + bytes([FRONT]) + b"\x00" + image


def _flac_picture_body(image: bytes, mime: str) -> bytes:
    w, h = image_size(image) or (0, 0)
    m = mime.encode("ascii")
    return (struct.pack(">I", FRONT) + struct.pack(">I", len(m)) + m + struct.pack(">I", 0)
            + struct.pack(">IIII", w, h, 24, 0) + struct.pack(">I", len(image)) + image)


def _id3_bytes(parsed: _Parsed, frames: list[_Frame]) -> bytes:
    if parsed.created and not frames:
        return b""
    major = parsed.version
    body = bytearray()
    for fr in frames:
        size = _to_syncsafe(len(fr.body)) if major == 4 else struct.pack(">I", len(fr.body))
        body += fr.fid.encode("ascii") + size + struct.pack(">H", fr.flags) + fr.body
    body += b"\x00" * PADDING
    return b"ID3" + bytes((major, 0, 0)) + _to_syncsafe(len(body)) + bytes(body)


def _flac_bytes(frames: list[_Frame]) -> bytes:
    blocks = [fr for fr in frames if fr.fid != "1"] + [_Frame("1", 0, b"\x00" * PADDING)]
    out = bytearray(b"fLaC")
    for i, fr in enumerate(blocks):
        if len(fr.body) >= 1 << 24:
            raise CoverWriteSkipped("a picture is too large for a FLAC block")
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes((last | int(fr.fid),)) + len(fr.body).to_bytes(3, "big") + fr.body
    return bytes(out)


def _head_bytes(parsed: _Parsed, frames: list[_Frame]) -> bytes:
    return _id3_bytes(parsed, frames) if parsed.kind == "id3" else _flac_bytes(frames)


# ─── the safe replace ────────────────────────────────────────────────────


def _stat_key(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def _check_writable(path: Path) -> None:
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        raise CoverWriteSkipped("the music folder is read-only for Domovoi")


def _hash_tail(path: Path, start: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(start)
        while True:
            chunk = f.read(_COPY_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _replace_head(path: Path, parsed: _Parsed, frames: list[_Frame], stat_before: tuple[int, int],
                  expect_cover: bytes | None) -> tuple[int, int]:
    """Write ``frames`` as the file's tag / metadata through a verified copy
    and an atomic rename; returns the new (size, mtime_ns)."""
    from domovoi import media_tags

    head = _head_bytes(parsed, frames)
    before = media_tags.read(path)
    audio_hash = _hash_tail(path, parsed.head_len)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".domovoi-tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            out.write(head)
            src.seek(parsed.head_len)
            while True:
                chunk = src.read(_COPY_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        try:
            import shutil

            shutil.copymode(path, tmp)
        except OSError:
            pass
        # Read the copy back: it must parse, keep the length and the audio,
        # and hold the cover that was meant (or none of Domovoi's on undo).
        after = media_tags.read(tmp)
        if after is None or (before is not None and after.kind != before.kind):
            raise CoverWriteSkipped("the written copy didn't read back; the file was left as it was")
        if before is not None and before.duration and after.duration is not None \
                and abs(before.duration - after.duration) > 0.5:
            raise CoverWriteSkipped("the written copy's length differs; the file was left as it was")
        if _hash_tail(tmp, len(head)) != audio_hash:
            raise CoverWriteSkipped("the written copy's audio differs; the file was left as it was")
        if expect_cover is not None and current_cover(tmp, parsed.kind) != expect_cover:
            raise CoverWriteSkipped("the written copy lacks the cover; the file was left as it was")
        if _stat_key(path) != stat_before:
            raise CoverWriteSkipped("the file changed while Domovoi was writing; left as it is")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return _stat_key(path)


# ─── snapshots ───────────────────────────────────────────────────────────


def _frame_json(parsed: _Parsed, fr: _Frame) -> dict[str, Any]:
    info = _pic_info(parsed, fr)
    return {
        "id": fr.fid, "flags": fr.flags, "body": base64.b64encode(fr.body).decode("ascii"),
        "type": info[0] if info else None, "mime": info[1] if info else None,
        "desc": info[2] if info else None,
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def read_snapshot(path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class WriteResult:
    size: int
    mtime_ns: int
    had_art: bool
    created_tag: bool
    snapshot_path: str


def write_front_cover(path: Path, image: bytes, *, snapshot_path: Path,
                      expect_stat: tuple[int, int] | None = None,
                      only_if_missing: bool = False) -> WriteResult:
    """Make ``image`` the file's front cover (module docstring). Raises
    :class:`CoverWriteSkipped` when the file is left as it was: a format
    Domovoi doesn't write, a read-only folder, a file that changed since
    ``expect_stat`` (size, mtime_ns) or during the write, a copy that
    didn't verify, the same picture already there, or (``only_if_missing``)
    art already there. The first snapshot at ``snapshot_path`` is kept."""
    path = Path(path)
    mime = sniff_image(image)
    if mime is None or len(image) > MAX_IMAGE_BYTES:
        raise CoverWriteSkipped("the picture isn't a JPEG or PNG Domovoi writes")
    kind = writable_kind(path)
    if kind is None:
        raise CoverWriteSkipped("Domovoi doesn't write pictures into this kind of file")
    _check_writable(path)
    stat_before = _stat_key(path)
    if expect_stat is not None and tuple(expect_stat) != stat_before:
        raise CoverWriteSkipped("the file changed since Domovoi read it")
    parsed = _parse(path, kind)
    keep: list[_Frame] = []
    removed: list[_Frame] = []
    existing: bytes | None = None
    for fr in parsed.frames:
        info = _pic_info(parsed, fr)
        if info and info[0] in COVER_TYPES:
            removed.append(fr)
            if existing is None or info[0] == FRONT:
                existing = info[3]
        else:
            keep.append(fr)
    if only_if_missing and (removed or any(_pic_info(parsed, fr) for fr in keep)):
        raise CoverWriteSkipped("the file already has art")
    if existing is not None and same_picture(existing, image):
        raise CoverWriteSkipped("same")
    snap = read_snapshot(snapshot_path)
    if snap is None:
        # The first write: keep what the file is about to lose.
        snap = {
            "format": parsed.kind, "version": parsed.version, "created_tag": parsed.created,
            "had_art": bool(removed), "frames": [_frame_json(parsed, fr) for fr in removed],
        }
    new_frame = _Frame("APIC", 0, _apic_body(image, mime)) if kind == "id3" else \
        _Frame("6", 0, _flac_picture_body(image, mime))
    # The picture goes in front of the others (players show the first).
    frames = [new_frame, *keep] if kind == "id3" else [keep[0], new_frame, *keep[1:]]
    snap["written_sha256"] = hashlib.sha256(image).hexdigest()
    _write_json(Path(snapshot_path), snap)
    size, mtime = _replace_head(path, parsed, frames, stat_before, image)
    return WriteResult(size, mtime, bool(snap.get("had_art")), bool(snap.get("created_tag")),
                       str(snapshot_path))


def undo(path: Path, snapshot_path: str | os.PathLike[str]) -> tuple[int, int]:
    """Put the file back as it was before Domovoi's first cover write:
    the picture Domovoi wrote out, the saved pictures back in. Raises
    :class:`CoverWriteSkipped` when that can't be done safely."""
    path = Path(path)
    snap = read_snapshot(snapshot_path)
    if snap is None:
        raise CoverWriteSkipped("Domovoi has no record of the file's original pictures")
    kind = writable_kind(path)
    if kind is None or kind != snap.get("format"):
        raise CoverWriteSkipped("the file is no longer one Domovoi wrote")
    _check_writable(path)
    stat_before = _stat_key(path)
    parsed = _parse(path, kind)
    written = snap.get("written_sha256")
    keep = []
    for fr in parsed.frames:
        info = _pic_info(parsed, fr)
        if info and info[0] == FRONT and hashlib.sha256(info[3]).hexdigest() == written:
            continue
        keep.append(fr)
    restored = [_Frame(str(f["id"]), int(f.get("flags") or 0), base64.b64decode(f["body"]))
                for f in snap.get("frames") or []]
    if kind == "id3":
        frames = [*restored, *keep]
        if snap.get("created_tag") and not frames:
            parsed.created = True
    else:
        frames = [keep[0], *restored, *keep[1:]]
    return _replace_head(path, parsed, frames, stat_before, None)


__all__ = [
    "CoverWriteSkipped",
    "WriteResult",
    "current_cover",
    "has_any_picture",
    "image_size",
    "same_picture",
    "sniff_image",
    "undo",
    "writable_kind",
    "write_front_cover",
]


# ─── Domovoi's own copies (formats it doesn't write) ─────────────────────


def cover_cache_dir() -> Path:
    from domovoi.config import settings

    return Path(settings.cover_art_dir).expanduser() / "cache"


def cover_cache_path(track_id: int, mime: str) -> Path:
    return cover_cache_dir() / f"{int(track_id)}{'.png' if mime == 'image/png' else '.jpg'}"


def cover_cache_file(track_id: int) -> tuple[Path, os.stat_result, str] | None:
    """Domovoi's own copy of a track's cover, ``(path, stat, mime)``, or
    None (blocking: a stat or two)."""
    for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png")):
        p = cover_cache_dir() / f"{int(track_id)}{ext}"
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > 0:
            return p, st, mime
    return None


def remove_cached_cover(track_id: int) -> bool:
    removed = False
    for ext in (".jpg", ".png"):
        try:
            (cover_cache_dir() / f"{int(track_id)}{ext}").unlink()
            removed = True
        except OSError:
            pass
    return removed
