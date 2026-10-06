"""The two local lyric sources (contract §5.1, §5.2): a ``.lrc`` beside
the song, and the lyrics inside the audio file.

**The sidecar** ([F-S1]–[F-S5]). ``Lantern Song.flac``'s sidecar is a file
in the same folder whose name, casefolded, is ``lantern song.lrc`` (names
compared in Unicode NFC, so a Mac's decomposed "é" is the same "é"): on a
case-sensitive file system several may exist, and exactly
``Lantern Song.lrc`` wins, then ``Lantern Song.LRC``, then the rest in
code-point order; only the first is ever read. When there is none — or
the one there is Domovoi's own copy of LRCLIB's lyrics — a ``.lrc`` named
"Artist - Title" (whose stem is no audio file's stem there) is used, but
only when exactly one such file matches the song and no other song in that
folder matches that file. A file bigger than
:data:`~domovoi.lyrics.lrc.MAX_LRC_BYTES`, anything that is not a regular
file, a symlink resolving outside MUSIC_DIR and a symlink to nothing are
not read. A ``.lrc`` that does not look like LRC is read as plain lyrics,
its ID tags and stray time tags left out.

**Inside the file** ([E1]–[E6]): ID3 SYLT (millisecond timestamps), ID3
USLT (English first), Vorbis comments (LYRICS, UNSYNCEDLYRICS, UNSYNCED
LYRICS, SYNCEDLYRICS), the MP4 ``©lyr`` atom, ASF ``WM/Lyrics``, APEv2
``Lyrics`` — the first of them with TIMED lyrics, else the first with
words (timed beats plain, [M1]). LRC-formatted text in a tag counts as
timed; a SYLT frame synced by syllable or word (the ID3 way: a newline in
front of the first syllable of each line) is read line by line. mutagen
is optional (the ``real-clients`` extra): without it this source yields
nothing.

Everything here reads; nothing writes, renames or deletes a file. Every
function is blocking (the scan runs them in a worker thread) and never
raises. Nothing logs lyric text, or a file name above DEBUG.
"""

from __future__ import annotations

import errno
import logging
import os
import stat as stat_mod
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from domovoi.handlers.shared.spoken_names import alias_key, split_credit
from domovoi.lyrics.lrc import (
    EMPTY,
    MAX_LRC_BYTES,
    ParsedLyrics,
    decode_lrc_bytes,
    from_entries,
    looks_like_lrc,
    parse_lrc,
    parse_lrc_untimed,
    parse_lyrics_text,
)
from domovoi.workers.library_indexer import _AUDIO_EXTENSIONS

log = logging.getLogger(__name__)

#: The extensions the library indexer treats as audio (it ignores ``.lrc``).
AUDIO_EXTENSIONS: frozenset[str] = frozenset(_AUDIO_EXTENSIONS)

#: Vorbis comment fields holding lyrics, in the order they are tried ([E3]).
VORBIS_LYRIC_KEYS = ("LYRICS", "UNSYNCEDLYRICS", "UNSYNCED LYRICS", "SYNCEDLYRICS")
#: SYLT timestamp format 2: absolute milliseconds (1 = MPEG frames, ignored).
SYLT_MILLISECONDS = 2


# ─── Names ────────────────────────────────────────────────────────────────


def name_key(name: str) -> str:
    """A file name as names are compared here: Unicode NFC, casefolded. One
    tool writes "é" as one character, a copy from a Mac as "e" and an
    accent; to a person (and to most file systems' lookups) they are one
    name."""
    return unicodedata.normalize("NFC", name).casefold()


def audio_stem(name: str) -> str:
    """The file name without its last extension: ``Lantern Song.flac`` →
    ``Lantern Song``."""
    return Path(name).stem


def is_lrc_name(name: str) -> bool:
    return name.casefold().endswith(".lrc")


def sidecar_candidates(audio_name: str, names: Iterable[str]) -> list[str]:
    """[F-S1]/[F-S2]: the names in the audio file's folder that are its
    sidecar, best first."""
    stem = audio_stem(audio_name)
    want = name_key(f"{stem}.lrc")
    found = [n for n in names if name_key(n) == want]
    exact, upper = f"{stem}.lrc", f"{stem}.LRC"
    return sorted(found, key=lambda n: (0 if n == exact else 1 if n == upper else 2, n))


@dataclass(frozen=True)
class DirRow:
    """One library row of a folder, as the "Artist - Title" rule sees it."""

    track_id: int
    audio_name: str
    artist: str | None
    title: str | None


def _row_keys(row: DirRow) -> frozenset[str]:
    artist = (row.artist or "").strip()
    title = (row.title or "").strip()
    if not artist or not title:
        return frozenset()
    keys = {alias_key(f"{artist} - {title}")}
    parts = split_credit(artist)
    if parts:
        keys.add(alias_key(f"{parts[0]} - {title}"))
    keys.discard("")
    return frozenset(keys)


def artist_title_matches(names: Sequence[str], rows: Sequence[DirRow]) -> dict[int, str]:
    """The "Artist - Title" half of [F-S3] for one folder: ``track_id`` →
    the one "Artist - Title" ``.lrc`` that matches it, whether or not the
    song also has a same-stem ``.lrc`` (the caller decides between the two:
    a same-stem file of the household's wins, Domovoi's own does not —
    see :func:`artist_title_sidecars`). Considered: ``.lrc`` files whose
    stem is no audio file's stem in the folder. A file matches a row when
    ``alias_key`` of its stem is ``alias_key("<artist> - <title>")`` for the
    row's full credit or its primary performer. A row gets a file only when
    exactly one file matches it and no other row of the folder matches that
    file."""
    audio_stems = {
        name_key(audio_stem(n)) for n in names
        if Path(n).suffix.lower() in AUDIO_EXTENSIONS
    }
    files = [n for n in names if is_lrc_name(n) and name_key(audio_stem(n)) not in audio_stems]
    if not files or not rows:
        return {}
    file_keys = {f: alias_key(audio_stem(f)) for f in files}
    matches: dict[int, list[str]] = {}
    for row in rows:
        keys = _row_keys(row)
        matches[row.track_id] = [f for f in files if file_keys[f] and file_keys[f] in keys]
    out: dict[int, str] = {}
    for row in rows:
        mine = matches[row.track_id]
        if len(mine) != 1:
            continue
        f = mine[0]
        if any(f in matches[r.track_id] for r in rows if r.track_id != row.track_id):
            continue
        out[row.track_id] = f
    return out


def artist_title_sidecars(names: Sequence[str], rows: Sequence[DirRow]) -> dict[int, str]:
    """[F-S3] for one folder, as it reads when no row has a ``.lrc`` of
    Domovoi's own: ``track_id`` → the "Artist - Title" ``.lrc`` it uses —
    :func:`artist_title_matches`, for the rows with no same-stem candidate.
    (The scan also lets a row whose only same-stem ``.lrc`` is Domovoi's
    own copy of LRCLIB's lyrics use its "Artist - Title" match: the
    household's file always wins over Domovoi's, [M1].)"""
    return {
        track_id: f for track_id, f in artist_title_matches(names, rows).items()
        if not any(r.track_id == track_id and sidecar_candidates(r.audio_name, names) for r in rows)
    }


# ─── Reading a sidecar ────────────────────────────────────────────────────


@dataclass(frozen=True)
class FileStat:
    """What the scan compares to see whether a file changed ([K5]):
    ``mtime_ns`` is :func:`change_stamp_ns` (stored in the ``*_mtime_ns``
    columns), ``size`` the size in bytes."""

    mtime_ns: int
    size: int


def change_stamp_ns(st: Any, *, posix: bool | None = None) -> int:
    """When a file last changed, as the scan sees it: its mtime — on POSIX
    the later of its mtime and its ctime. The ctime also moves on a chmod or
    a chown (a file the core could not read, made readable) and when a
    tagger puts a file's old mtime back after editing it; Windows'
    ``st_ctime`` is the creation time, so there it is the mtime alone.
    ``posix`` overrides the platform (tests)."""
    mtime = int(st.st_mtime_ns)
    if not (os.name != "nt" if posix is None else posix):
        return mtime
    ctime = getattr(st, "st_ctime_ns", None)
    return max(mtime, int(ctime)) if ctime is not None else mtime


def file_stat(st: os.stat_result) -> FileStat:
    return FileStat(change_stamp_ns(st), int(st.st_size))


def stat_file(path: str | os.PathLike[str]) -> FileStat | None:
    """:class:`FileStat` of ``path``, through a symlink to its target. None
    when nothing readable is there: no file, a symlink to nothing, a
    symlink loop — none of them is a sidecar (the writer still finds the
    name in the folder and writes nothing beside it, [W4]). A stat refused
    for another reason falls back to the link itself, so the file is still
    looked at (and its read then says why not)."""
    try:
        st = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            return None
        try:
            st = os.lstat(path)
        except OSError:
            return None
    return file_stat(st)


def can_read(path: str | os.PathLike[str]) -> bool:
    """Whether the file at ``path`` opens for reading right now (a file the
    scan cannot read gives no verdict about its lyrics: it is looked at
    again on the next tick)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return False
    try:
        os.close(fd)
    except OSError:
        pass
    return True


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class SidecarBytes:
    """A sidecar's bytes, or why there are none: ``missing``,
    ``outside_music_dir`` (a symlink whose target is outside MUSIC_DIR),
    ``not_file``, ``too_large``, ``unreadable``."""

    data: bytes | None
    error: str | None = None

    def __repr__(self) -> str:  # never the file's lyrics ([C3])
        size = "None" if self.data is None else f"{len(self.data)} bytes"
        return f"SidecarBytes(data={size}, error={self.error!r})"


def read_sidecar_bytes(path: str | os.PathLike[str], music_root: Path | None) -> SidecarBytes:
    """The bytes of the sidecar at ``path`` ([F-S4]). Blocking, never
    raises. ``music_root`` is MUSIC_DIR resolved; a symlink is read only
    when its target is inside it."""
    p = Path(path)
    try:
        lst = os.lstat(p)
    except FileNotFoundError:
        return SidecarBytes(None, "missing")
    except OSError:
        return SidecarBytes(None, "unreadable")
    try:
        if stat_mod.S_ISLNK(lst.st_mode):
            if music_root is None:
                return SidecarBytes(None, "outside_music_dir")
            try:
                target = p.resolve(strict=True)
            except FileNotFoundError:
                return SidecarBytes(None, "missing")
            except RuntimeError:
                # A symlink loop (Python ≤ 3.12 raises RuntimeError for one;
                # 3.13+ an OSError with ELOOP, below): nothing to read.
                return SidecarBytes(None, "not_file")
            if not _inside(target, music_root):
                return SidecarBytes(None, "outside_music_dir")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(p, flags)
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                return SidecarBytes(None, "not_file")
            if st.st_size > MAX_LRC_BYTES:
                return SidecarBytes(None, "too_large")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_LRC_BYTES:
                    return SidecarBytes(None, "too_large")
                chunks.append(chunk)
            return SidecarBytes(b"".join(chunks))
        finally:
            os.close(fd)
    except IsADirectoryError:
        return SidecarBytes(None, "not_file")
    except FileNotFoundError:
        return SidecarBytes(None, "missing")         # gone since the lstat
    except PermissionError:
        # Windows refuses to open a directory with PermissionError too.
        try:
            if p.is_dir():
                return SidecarBytes(None, "not_file")
        except OSError:
            pass
        return SidecarBytes(None, "unreadable")
    except OSError as e:
        if e.errno == errno.ELOOP:
            return SidecarBytes(None, "not_file")
        return SidecarBytes(None, "unreadable")


def parse_sidecar(data: bytes | None) -> ParsedLyrics:
    """The lyrics in a sidecar's bytes ([F-S4]): decoded, then LRC — or,
    when it does not look like LRC, plain lyrics without its ID tags and
    stray time tags (:func:`~domovoi.lyrics.lrc.parse_lrc_untimed`, [P10]).
    :data:`~domovoi.lyrics.lrc.EMPTY` for no bytes."""
    if data is None:
        return EMPTY
    text = decode_lrc_bytes(data)
    return parse_lrc(text) if looks_like_lrc(text) else parse_lrc_untimed(text)


# ─── Lyrics inside the file ───────────────────────────────────────────────


@dataclass(frozen=True)
class EmbeddedLyrics:
    """Lyrics read from an audio file's tags. ``detail`` names the tag
    (``"SYLT"``, ``"USLT"``, ``"USLT (LRC)"``, ``"LYRICS"``, ``"©lyr"``,
    ``"WM/Lyrics"``, ``"APE Lyrics"`` …)."""

    detail: str
    lyrics: ParsedLyrics


def _too_big(text: str) -> bool:
    return len(text) > MAX_LRC_BYTES or len(text.encode("utf-8", "replace")) > MAX_LRC_BYTES


def _from_text(detail: str, text: Any) -> EmbeddedLyrics | None:
    """[E4]: a tag's text through the LRC / plain reader."""
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    if not s.strip() or _too_big(s):
        return None
    parsed = parse_lyrics_text(s)
    if parsed.plain is None:
        return None
    return EmbeddedLyrics(f"{detail} (LRC)" if parsed.synced is not None else detail, parsed)


def _newline_beside_text(body: Any) -> bool:
    """An entry whose newline sits against words — ``"\\nand"`` (the ID3
    way to start a line) or ``"door\\n"`` — rather than a newline alone (a
    blank line in a frame of whole lines)."""
    if not isinstance(body, str):
        return False
    s = body.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in s or not s.strip():
        return False
    return s.startswith("\n") or s.endswith("\n") or "\n" in s.strip()


def sylt_lines(pairs: Sequence[tuple[Any, Any]]) -> list[tuple[Any, str]]:
    """The ``(ms, text)`` entries of a SYLT frame as lines.

    A frame of whole lines — one entry per line, a newline alone at most
    (a blank line) — is returned as it is. A frame synced by syllable or
    word, the way ID3v2.4 describes it (a newline in front of the first
    syllable of a line, a space in front of a new word), is joined: a line
    starts at each newline and takes its first entry's time, and the
    entries after it are added on exactly as they are (they carry their
    own spaces). A newline at the END of an entry starts the line at the
    next entry, which some taggers write instead. The caller cleans and
    sorts them (:func:`~domovoi.lyrics.lrc.from_entries`)."""
    ordered = sorted(
        ((ts, body) for ts, body in pairs if isinstance(body, str)),
        key=lambda e: e[0] if isinstance(e[0], int) else 0,
    )
    if not any(_newline_beside_text(body) for _, body in ordered):
        return list(pairs)
    lines: list[list[Any]] = []
    starts_line = True
    for ts, body in ordered:
        pieces = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for k, piece in enumerate(pieces):
            if k:
                starts_line = True          # a newline: what follows starts a line
            if not piece:
                continue
            if starts_line or not lines:
                lines.append([ts, piece])
                starts_line = False
            else:
                lines[-1][1] += piece
    return [(ts, text) for ts, text in lines]


def _id3_candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    # 1. SYLT in milliseconds, with text; the frame with the most entries.
    sylts = []
    for frame in tags.getall("SYLT"):
        if getattr(frame, "format", None) != SYLT_MILLISECONDS:
            continue
        pairs = [(ts, body) for body, ts in (frame.text or [])]
        if any(isinstance(body, str) and body.strip() for _, body in pairs):
            sylts.append(pairs)
    for pairs in sorted(sylts, key=len, reverse=True):
        parsed = from_entries(sylt_lines(pairs))
        if parsed.plain is not None:
            yield EmbeddedLyrics("SYLT", parsed)
            break
    # 2. USLT: English, then unknown language, then any; the longest first.
    def rank(frame: Any) -> tuple[int, int]:
        lang = str(getattr(frame, "lang", "") or "").strip("\x00 ").lower()
        tier = 0 if lang == "eng" else 1 if lang in ("xxx", "und", "") else 2
        return (tier, -len(str(getattr(frame, "text", "") or "")))

    for frame in sorted(tags.getall("USLT"), key=rank):
        yield _from_text("USLT", getattr(frame, "text", None))


def _vorbis_candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    by_key: dict[str, list[str]] = {}
    for item in tags:
        try:
            key, value = item
        except (TypeError, ValueError):
            continue
        if isinstance(key, str) and isinstance(value, str):
            by_key.setdefault(key.upper(), []).append(value)
    for key in VORBIS_LYRIC_KEYS:
        values = by_key.get(key)
        if values:
            yield _from_text(key, "\n".join(values))


def _mp4_candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    values = tags.get("\xa9lyr") or []
    texts = [v for v in values if isinstance(v, str)]
    if texts:
        yield _from_text("\xa9lyr", "\n".join(texts))


def _asf_candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    texts = []
    for item in tags:
        try:
            key, value = item
        except (TypeError, ValueError):
            continue
        if isinstance(key, str) and key.lower() == "wm/lyrics":
            texts.append(str(value))
    if texts:
        yield _from_text("WM/Lyrics", "\n".join(texts))


def _ape_candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    from mutagen.apev2 import TEXT

    value = tags.get("Lyrics")
    if value is not None and getattr(value, "kind", None) == TEXT:
        yield _from_text("APE Lyrics", "\n".join(str(v) for v in value))


def _candidates(tags: Any) -> Iterator[EmbeddedLyrics | None]:
    """[E3]: every place lyrics may sit in ``tags``, in order."""
    from mutagen._vorbis import VComment
    from mutagen.apev2 import APEv2
    from mutagen.asf import ASFTags
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags

    if isinstance(tags, ID3):
        yield from _id3_candidates(tags)
    elif isinstance(tags, VComment):
        yield from _vorbis_candidates(tags)
    elif isinstance(tags, MP4Tags):
        yield from _mp4_candidates(tags)
    elif isinstance(tags, ASFTags):
        yield from _asf_candidates(tags)
    elif isinstance(tags, APEv2):
        yield from _ape_candidates(tags)


def best_candidate(found: Iterable[EmbeddedLyrics | None]) -> EmbeddedLyrics | None:
    """[E3] as amended (2026-10-06 review): of the places that hold lyrics,
    in [E3]'s order, the first with TIMED lyrics, else the first with words
    — timed beats plain within a file as it does between sources ([M1]),
    so a FLAC with both a plain LYRICS and an LRC SYNCEDLYRICS shows the
    timed one (ID3 already tries SYLT before USLT)."""
    first: EmbeddedLyrics | None = None
    for c in found:
        if c is None:
            continue
        if c.lyrics.synced is not None:
            return c
        if first is None:
            first = c
    return first


def read_embedded(path: str | os.PathLike[str], *, track_id: int | None = None) -> EmbeddedLyrics | None:
    """The lyrics inside the audio file at ``path`` ([E1]–[E6];
    :func:`best_candidate`), or None. Blocking (run it in a worker thread);
    never raises — a problem is logged at DEBUG as its exception type and
    the track id."""
    try:
        import mutagen
    except ImportError:
        return None
    try:
        f = mutagen.File(os.fspath(path))
        tags = getattr(f, "tags", None) if f is not None else None
        if tags is None:
            return None
        return best_candidate(_candidates(tags))
    except Exception as e:  # noqa: BLE001 — [E5]
        log.debug("lyrics: track %s: tags not read (%s)", track_id, type(e).__name__)
        return None


__all__ = [
    "AUDIO_EXTENSIONS",
    "DirRow",
    "EmbeddedLyrics",
    "FileStat",
    "SidecarBytes",
    "VORBIS_LYRIC_KEYS",
    "artist_title_matches",
    "artist_title_sidecars",
    "audio_stem",
    "best_candidate",
    "can_read",
    "change_stamp_ns",
    "file_stat",
    "is_lrc_name",
    "name_key",
    "parse_sidecar",
    "read_embedded",
    "read_sidecar_bytes",
    "sidecar_candidates",
    "stat_file",
    "sylt_lines",
]
