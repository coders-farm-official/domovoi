"""Playlists the music files name, kept as Domovoi playlists (V058).

Two sources, both in the music folder, both read locally:

* **.m3u / .m3u8 files** anywhere under ``MUSIC_DIR`` (a phone app's
  ``<music>/<app>/Playlists/<name>.m3u``, a player's export): the
  playlist is named after the file, its songs are its lines that aren't
  ``#`` comments, in order — a path relative to the playlist file's
  folder, an absolute path, or a ``file://`` address. A line naming a
  file the library doesn't have (or a web address) is skipped.
* **The songs' own PLAYLIST tags** (``TXXX:PLAYLIST`` ...,
  ``library_tracks.file_playlists``, read by the indexer and the tag
  backfill): each song is in every playlist it names, in the library's
  album / file order. A playlist both name gets the .m3u's songs first,
  then the tagged songs it doesn't already have.

**How a playlist from files behaves.** It is created, or updated, by name
on every sync (a rescan, and the tag backfill's end of a pass), marked
``playlists.imported_from = 'files'`` with ``import_name`` the name the
files give it. **The files win:** its songs are always what the files say,
and Domovoi refuses to edit it (``web/backend/api/playlists.py``,
the voice "add to playlist"); "Copy to a new playlist" makes an editable
copy. A playlist from files that no file names any more is removed. A
household playlist (made in Domovoi) is never touched: when it has the same
name, the imported one is called "<name> (from files)".

Nothing here writes a file. :func:`sync_file_playlists` runs where the
indexer runs (the core, after a library walk); the parsing is pure.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from sqlalchemy import text

from domovoi.config import settings

log = logging.getLogger(__name__)

PLAYLIST_EXTENSIONS = frozenset({".m3u", ".m3u8"})
#: Largest playlist file read, and most entries taken from one.
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 5000
#: Most playlist files looked at in one sync.
MAX_PLAYLIST_FILES = 500
#: Longest playlist name kept.
MAX_NAME_CHARS = 120
CLASH_SUFFIX = " (from files)"


def path_key(path: str | os.PathLike[str]) -> str:
    """A path as compared: normalised, case-blind where the OS is."""
    return os.path.normcase(os.path.normpath(os.fspath(path)))


def parse_m3u(text_: str, base_dir: Path) -> list[str]:
    """The song paths an M3U/M3U8 playlist lists, in order, as absolute
    paths (not checked to exist). Comments (``#EXTM3U``, ``#EXTINF``) and
    blank lines are skipped, and so are web addresses. Pure."""
    out: list[str] = []
    for raw in text_.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            parts = urlsplit(line)
            if parts.scheme.lower() != "file":
                continue
            line = unquote(parts.path)
            # file:///C:/x -> /C:/x on Windows
            if len(line) > 3 and line[0] == "/" and line[2] == ":":
                line = line[1:]
        line = line.replace("\\", "/") if os.sep == "/" else line
        p = Path(line)
        if not p.is_absolute():
            p = base_dir / p
        out.append(os.path.normpath(str(p)))
        if len(out) >= MAX_ENTRIES:
            break
    return out


def _decode(data: bytes, ext: str) -> str:
    if ext == ".m3u8" or data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def playlist_name_of(path: Path) -> str:
    name = " ".join(path.stem.split())[:MAX_NAME_CHARS].strip()
    return name or "Playlist"


def find_playlist_files(music_dir: Path) -> list[Path]:
    """Every .m3u/.m3u8 under ``music_dir`` (blocking), Domovoi's trash
    folder excluded, at most :data:`MAX_PLAYLIST_FILES`."""
    from domovoi.library_trash import TRASH_DIR_NAME

    out: list[Path] = []
    for p in music_dir.rglob("*"):
        if p.suffix.lower() not in PLAYLIST_EXTENSIONS or TRASH_DIR_NAME in p.parts:
            continue
        try:
            if not p.is_file() or p.stat().st_size > MAX_PLAYLIST_BYTES:
                continue
        except OSError:
            continue
        out.append(p)
        if len(out) >= MAX_PLAYLIST_FILES:
            break
    return sorted(out)


def read_playlist_files(music_dir: Path) -> dict[str, list[str]]:
    """``{name: [absolute song path, ...]}`` from every playlist file
    (blocking). Two files with one name are joined, in path order."""
    lists: dict[str, list[str]] = {}
    for p in find_playlist_files(music_dir):
        try:
            data = p.read_bytes()
        except OSError as e:
            log.debug("playlists from files: couldn't read %s: %s", p, e)
            continue
        entries = parse_m3u(_decode(data, p.suffix.lower()), p.parent)
        lists.setdefault(playlist_name_of(p), []).extend(entries)
    return lists


def build_playlists(
    m3u_lists: dict[str, list[str]],
    tracks: Iterable[tuple[int, str, list[str] | None]],
) -> dict[str, list[int]]:
    """``{name: [track id, ...]}``: each M3U list's songs that the library
    has, then (for any name) the tracks whose PLAYLIST tag names it, in the
    order ``tracks`` come (``(id, file_path, file_playlists)``). A name
    matches case-blind; the first spelling seen is kept. A playlist with no
    library song is left out. Pure."""
    rows = list(tracks)
    by_path = {path_key(fp): tid for tid, fp, _ in rows}
    out: dict[str, list[int]] = {}
    spelling: dict[str, str] = {}

    def add(name: str, tid: int) -> None:
        key = name.casefold()
        shown = spelling.setdefault(key, name)
        ids = out.setdefault(shown, [])
        if tid not in ids:
            ids.append(tid)

    for name, paths in m3u_lists.items():
        for p in paths:
            tid = by_path.get(path_key(p))
            if tid is not None:
                add(name, tid)
    for tid, _fp, names in rows:
        for name in names or ():
            n = " ".join(str(name).split())[:MAX_NAME_CHARS]
            if n:
                add(n, tid)
    return {k: v for k, v in out.items() if v}


async def _apply(session: Any, wanted: dict[str, list[int]]) -> dict[str, int]:
    """Create / update / remove the imported playlists so they are
    ``wanted``. The caller commits."""
    counts = {"created": 0, "updated": 0, "removed": 0}
    existing = {
        str(r.import_name).casefold(): r
        for r in (await session.execute(text(
            "SELECT id, name, import_name FROM playlists WHERE imported_from = 'files'"
        ))).all()
    }
    household = {
        str(r[0]).casefold()
        for r in (await session.execute(text(
            "SELECT name FROM playlists WHERE imported_from IS NULL"
        ))).all()
    }
    for name, ids in wanted.items():
        row = existing.pop(name.casefold(), None)
        shown = name if name.casefold() not in household else f"{name}{CLASH_SUFFIX}"
        if row is None:
            taken = (await session.execute(
                text("SELECT 1 FROM playlists WHERE lower(name) = lower(:n)"), {"n": shown},
            )).first()
            if taken is not None:
                log.info("playlists from files: %r is taken; not imported", shown)
                continue
            pid = (await session.execute(text(
                "INSERT INTO playlists (name, description, imported_from, import_name) "
                "VALUES (:n, :d, 'files', :i) RETURNING id"
            ), {"n": shown, "d": "From your music files", "i": name})).scalar_one()
            counts["created"] += 1
        else:
            pid = int(row.id)
            current = [int(r[0]) for r in (await session.execute(text(
                "SELECT track_id FROM playlist_tracks WHERE playlist_id = :p ORDER BY position, id"
            ), {"p": pid})).all()]
            if current == ids and row.name == shown:
                continue
            if row.name != shown:
                await session.execute(
                    text("UPDATE playlists SET name = :n WHERE id = :p"), {"n": shown, "p": pid},
                )
            counts["updated"] += 1
        await session.execute(text("DELETE FROM playlist_tracks WHERE playlist_id = :p"), {"p": pid})
        if ids:
            await session.execute(text(
                "INSERT INTO playlist_tracks (playlist_id, track_id, position) "
                "SELECT :p, t.id, t.ord - 1 FROM unnest(CAST(:ids AS int[])) "
                "WITH ORDINALITY AS t(id, ord) "
                "WHERE EXISTS (SELECT 1 FROM library_tracks WHERE id = t.id)"
            ), {"p": pid, "ids": ids})
        await session.execute(
            text("UPDATE playlists SET updated_at = now() WHERE id = :p"), {"p": pid},
        )
    for row in existing.values():
        await session.execute(text("DELETE FROM playlists WHERE id = :p"), {"p": int(row.id)})
        counts["removed"] += 1
    if any(counts.values()):
        await session.execute(text("SELECT pg_notify('playlists_changed', 'imported')"))
    return counts


async def sync_file_playlists() -> dict[str, int]:
    """Read the playlist files and the PLAYLIST tags, and bring the
    imported playlists in line (module docstring). Returns
    ``{"created", "updated", "removed"}``; nothing when the music folder
    isn't there (an unmounted drive must not empty the playlists)."""
    from domovoi.db.session import session_scope

    music_dir = Path(settings.music_dir).expanduser()
    if not music_dir.is_dir():
        return {"created": 0, "updated": 0, "removed": 0}
    m3u = await asyncio.to_thread(read_playlist_files, music_dir)
    async with session_scope() as session:
        rows = (await session.execute(text(
            "SELECT id, file_path, file_playlists FROM library_tracks "
            "ORDER BY lower(coalesce(album, '')), file_path, id"
        ))).all()
        wanted = build_playlists(m3u, ((int(r[0]), str(r[1]), r[2]) for r in rows))
        counts = await _apply(session, wanted)
        await session.commit()
    if any(counts.values()):
        log.info("playlists from files: %s", counts)
    return counts


async def is_imported(session: Any, playlist_id: int) -> bool:
    """Whether the playlist was made from files (and so is read-only)."""
    row = (await session.execute(
        text("SELECT imported_from FROM playlists WHERE id = :id"), {"id": playlist_id},
    )).first()
    return row is not None and row[0] is not None


READ_ONLY_MESSAGE = (
    "This playlist comes from your music files, so it changes when they do. "
    "Copy it to a new playlist to change it in Domovoi."
)

# What :func:`attach_track` did.
ATTACHED = "attached"
ATTACH_MISSING = "missing"      # the playlist is gone (a soft reference)
ATTACH_IMPORTED = "imported"    # made from files: read-only, nothing written

# Why a finished download wasn't put on the playlist it asked for, kept on
# its acquisition row (``result.attach_skipped``).
ATTACH_IMPORTED_NOTE = (
    "Not added to the playlist: it comes from your music files, "
    "so it is read-only in Domovoi."
)


async def attach_track(session: Any, playlist_id: int, track_id: int) -> str:
    """Append ``track_id`` to the end of playlist ``playlist_id`` (no-op if
    it is already there) and fire ``playlists_changed``, unless the
    playlist is gone (:data:`ATTACH_MISSING`) or was made from the music
    files (:data:`ATTACH_IMPORTED`): read-only everywhere, as the web route
    refuses it (409, :data:`READ_ONLY_MESSAGE`), so nothing is written.
    The one place a finished download (``domovoi/acquisitions.py``) and a
    plugin's ingest (``domovoi/sdk/library.py``) put a song on a playlist."""
    row = (await session.execute(
        text("SELECT imported_from FROM playlists WHERE id = :pid"), {"pid": playlist_id},
    )).first()
    if row is None:
        return ATTACH_MISSING
    if row[0] is not None:
        return ATTACH_IMPORTED
    await session.execute(
        text(
            """
            INSERT INTO playlist_tracks (playlist_id, track_id, position)
            VALUES (
                :pid, :tid,
                COALESCE((SELECT MAX(position) + 1 FROM playlist_tracks
                          WHERE playlist_id = :pid), 0)
            )
            ON CONFLICT (playlist_id, track_id) DO NOTHING
            """
        ),
        {"pid": playlist_id, "tid": track_id},
    )
    await session.execute(text("SELECT pg_notify('playlists_changed', 'attached')"))
    return ATTACHED


__all__ = [
    "ATTACHED",
    "ATTACH_IMPORTED",
    "ATTACH_IMPORTED_NOTE",
    "ATTACH_MISSING",
    "READ_ONLY_MESSAGE",
    "attach_track",
    "build_playlists",
    "is_imported",
    "parse_m3u",
    "read_playlist_files",
    "sync_file_playlists",
]
