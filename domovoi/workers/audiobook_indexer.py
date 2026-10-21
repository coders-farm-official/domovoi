"""Indexes ``AUDIOBOOKS_DIR`` into the ``audiobooks`` table.

The spoken-audio sibling of ``library_indexer``. Audiobooks come in two
on-disk shapes and this walker handles both:

  * A single ``.m4b`` (or ``.m4a`` / ``.mp3``) file with EMBEDDED chapters
    — the common Audible-style layout. Chapters are read via ffprobe
    (``-show_chapters``), with the tags read by ``domovoi.media_tags``.
  * A PER-CHAPTER FOLDER — one audio file per chapter, played in filename
    order. The folder is the book; each file is a chapter and its
    ``start_sec`` is the cumulative duration of the files before it.

Idempotent: upserts by ``file_path`` (the .m4b path OR the folder path), so
re-runs are cheap and a re-index picks up new books / chapter edits.

Beside the tags it keeps what a phone needs to save a book (V032): the
book's size, the ``files`` to fetch (one per chapter file for a folder book,
the file itself otherwise, each with its size and length), and where a
cover was found (``artwork``: ``embedded`` or ``image``; the picture itself
is read on request by the web's cover route, never stored).

A book whose file or folder is gone is PRUNED (with its resume positions):
deleting a book from the Files page, or from the app, takes it off the list
at the reindex that follows. A walk that finds the folder empty prunes only
what the caller says it just deleted, so an unmounted share doesn't empty
the list (and lose everyone's place) until it comes back.

Fires from three places, mirroring library_indexer:
  * Domovoi startup (background sweep) so a curated library Just Works.
  * The ``audiobook_indexer`` poll loop (slow cadence — books arrive rarely).
  * The ``POST /api/audiobooks/reindex`` web endpoint, and the Files
    page's upload / delete into the audiobooks library.

Metadata is best-effort: a book with no tags still indexes with its
filename/folder name as the title so it's playable and resumable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.mpd_library_sync import update_rooms_for
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

# Single-file audiobook containers. .m4b is the canonical chaptered format;
# .m4a / .mp3 single files are treated as one-chapter books.
_BOOK_FILE_EXTENSIONS = frozenset({".m4b", ".m4a", ".mp3", ".ogg", ".opus", ".flac"})
# Files that count as a chapter inside a per-chapter folder.
_CHAPTER_FILE_EXTENSIONS = _BOOK_FILE_EXTENSIONS

# Cover images beside a book, in order of preference (any case): in a
# folder book's own folder, the album-folder names the music cover route
# takes; beside a single-file book, an image with the book's own name
# ("Dune.m4b" + "Dune.jpg") — never a cover.jpg loose in AUDIOBOOKS_DIR,
# which would belong to every single-file book at once.
COVER_FOLDER_STEMS = ("cover", "folder", "front", "album")
COVER_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# The music cover route's cap: a bigger picture is ignored.
COVER_MAX_BYTES = 8 * 1024 * 1024
# Where a picture can be built into a file: ID3 APIC, MP4 covr, WMA
# WM/Picture, Vorbis/Opus METADATA_BLOCK_PICTURE and the older COVERART
# (FLAC's own PICTURE blocks are ``MediaFile.pictures``).
_PICTURE_TAGS = ("APIC", "covr", "WM/Picture", "metadata_block_picture", "coverart")


def _image_head_ok(head: bytes) -> bool:
    """JPEG, PNG, GIF or WebP by its first bytes (the music cover route's
    sniff): a ``cover.jpg`` holding anything else is no cover."""
    return (
        head[:3] == b"\xff\xd8\xff"
        or head[:8] == b"\x89PNG\r\n\x1a\n"
        or head[:6] in (b"GIF87a", b"GIF89a")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def find_cover_image(book: Path, is_folder: bool, root: Path) -> Path | None:
    """The image file that is ``book``'s cover, or None: for a folder book
    the first of cover / folder / front / album (.jpg / .jpeg / .png /
    .webp, any case) in its folder; for a single-file book an image with
    its name. It must resolve inside ``root`` (a link out is not followed),
    be a regular non-empty file no bigger than COVER_MAX_BYTES, and start
    like an image. Blocking. Shared with the web's cover route."""
    folder = book if is_folder else book.parent
    stems = COVER_FOLDER_STEMS if is_folder else (book.stem.lower(),)
    found: list[tuple[tuple[int, int, str], str]] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                stem, ext = os.path.splitext(entry.name)
                stem, ext = stem.lower(), ext.lower()
                if stem in stems and ext in COVER_IMAGE_EXTS:
                    found.append(((stems.index(stem), COVER_IMAGE_EXTS.index(ext), entry.name), entry.path))
    except OSError:
        return None
    root_resolved = root.resolve(strict=False)
    for _, candidate in sorted(found):
        resolved = Path(candidate).resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
            st = resolved.stat()
            if not resolved.is_file() or not 0 < st.st_size <= COVER_MAX_BYTES:
                continue
            with resolved.open("rb") as f:
                if _image_head_ok(f.read(16)):
                    return resolved
        except (ValueError, OSError):
            continue
    return None


def _library_root() -> Path:
    return Path(settings.audiobooks_dir).expanduser().resolve(strict=False)


def inside_library(p: Path, root: Path | None = None) -> bool:
    """Does ``p`` resolve inside AUDIOBOOKS_DIR? A chapter file, a book file
    or a book folder that is a link out of the library is no book: the
    audio route refuses to serve it, so the indexer doesn't list it."""
    try:
        p.resolve(strict=False).relative_to(root or _library_root())
        return True
    except (ValueError, OSError):
        return False


def chapter_files(folder: Path) -> list[Path]:
    """A folder book's chapter files, in play order (filename order); a
    file that links out of the library is left out (out of the folder
    itself, for a folder that isn't in AUDIOBOOKS_DIR at all)."""
    root = _library_root()
    if not inside_library(folder, root):
        root = folder.resolve(strict=False)
    return sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _CHAPTER_FILE_EXTENSIONS
        and inside_library(p, root)
    )


async def _ffprobe_chapters(path: Path) -> tuple[list[dict[str, Any]] | None, int | None]:
    """Return (chapters, duration_sec) via ffprobe. Best-effort — a missing
    ffprobe binary or an unparseable file yields (None, None).

    Chapters come back as ``[{"title": str, "start_sec": int}, ...]`` sorted
    by start time. Duration is the container's format-level duration.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_chapters", "-show_format", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
        log.debug("ffprobe unavailable/failed for %s: %s", path, e)
        return None, None
    try:
        data = json.loads(out.decode(errors="replace") or "{}")
    except json.JSONDecodeError:
        return None, None

    chapters: list[dict[str, Any]] = []
    for i, ch in enumerate(data.get("chapters") or []):
        try:
            start = float(ch.get("start_time", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        title = (ch.get("tags") or {}).get("title") or f"Chapter {i + 1}"
        chapters.append({"title": str(title), "start_sec": int(start)})
    chapters.sort(key=lambda c: c["start_sec"])

    duration: int | None = None
    fmt = data.get("format") or {}
    try:
        if fmt.get("duration") is not None:
            duration = int(float(fmt["duration"]))
    except (TypeError, ValueError):
        duration = None
    return (chapters or None), duration


def _tags_of(f: Any) -> dict[str, Any]:
    """title / author / narrator / duration_sec from a read file."""
    if f is None:
        return {}
    out: dict[str, Any] = {}
    if f.duration is not None and f.duration > 0:
        out["duration_sec"] = int(f.duration)
    tags = f.tags
    if not tags:
        return out
    out["title"] = tags.first("TIT2", "title", "\xa9nam", "Title", "\xa9alb", "album")
    out["author"] = tags.first("TPE1", "artist", "\xa9ART", "Author", "\xa9wrt", "composer")
    out["narrator"] = tags.first("\xa9nrt", "narrator", "TCOM", "composer")
    return out


def _has_picture(f: Any) -> bool:
    """Whether a read file carries a picture of any kind. Presence only:
    the cover route picks among them (and drops ones it can't draw)."""
    if f is None:
        return False
    if getattr(f, "pictures", None):
        return True
    tags = f.tags
    if not tags:
        return False
    try:
        return any(key in tags for key in _PICTURE_TAGS)
    except Exception:
        return False


def _read_tags(path: Path) -> dict[str, Any]:
    """title / author / narrator / duration_sec from tags (best-effort,
    ``domovoi.media_tags``). A name a format can't hold is just absent:
    before 2026-10-15 the MP4 names probed against a tagged FLAC, Ogg or
    Opus file's Vorbis comments raised, and the book was not indexed."""
    return _read_file(path)[0]


def _read_file(path: Path) -> tuple[dict[str, Any], bool, int | None]:
    """One read of ``path``: its tags (as :func:`_read_tags`), whether a
    picture is built into it, and its size in bytes (None if unreadable)."""
    from domovoi import media_tags

    try:
        size: int | None = path.stat().st_size
    except OSError:
        size = None
    try:
        f = media_tags.read(path)
    except Exception:
        return {}, False, size
    return _tags_of(f), _has_picture(f), size


async def read_tags(path: Path) -> dict[str, Any]:
    """:func:`_read_tags` in the ``tags`` pool, never on the event loop
    (2026-10-16 delta review B2: a slow file had stalled the core's loop
    at every sweep)."""
    from domovoi import executors

    return await executors.run("tags", _read_tags, path)


async def _read_file_off_loop(path: Path) -> tuple[dict[str, Any], bool, int | None]:
    from domovoi import executors

    return await executors.run("tags", _read_file, path)


async def _cover_image_off_loop(book: Path, is_folder: bool) -> Path | None:
    from domovoi import executors

    root = Path(settings.audiobooks_dir).expanduser()
    return await executors.run("tags", find_cover_image, book, is_folder, root)


async def _resolve_book_file(path: Path) -> dict[str, Any]:
    """Metadata for a single-file audiobook."""
    chapters, ff_dur = await _ffprobe_chapters(path)
    tags, embedded, size = await _read_file_off_loop(path)
    title = tags.get("title") or path.stem
    duration = ff_dur or tags.get("duration_sec")
    artwork = "embedded" if embedded else (
        "image" if await _cover_image_off_loop(path, False) is not None else None)
    return {
        "title": title,
        "author": tags.get("author"),
        "narrator": tags.get("narrator"),
        "chapters": chapters,
        "duration_sec": duration,
        "is_folder": False,
        "size_bytes": size,
        "files": [{
            "file": path.name, "title": title, "start_sec": 0,
            "duration_sec": duration, "size_bytes": size,
        }],
        "artwork": artwork,
    }


async def _resolve_book_folder(folder: Path) -> dict[str, Any] | None:
    """Metadata for a per-chapter folder. One chapter per audio file, in
    filename order; each chapter's ``start_sec`` is the cumulative duration
    of the files before it. Returns None if the folder has no audio."""
    files = chapter_files(folder)
    if not files:
        return None
    chapters: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    cursor = 0
    total = 0
    sizes_known = True
    author = narrator = None
    embedded = False
    for i, f in enumerate(files):
        tags, has_pic, size = await _read_file_off_loop(f)
        author = author or tags.get("author")
        narrator = narrator or tags.get("narrator")
        if i == 0:
            # The cover route reads the first chapter's picture.
            embedded = has_pic
        title = tags.get("title") or f.stem
        chapters.append({"title": title, "start_sec": cursor, "file": f.name})
        dur = tags.get("duration_sec")
        dur = dur if isinstance(dur, int) and dur > 0 else None
        entries.append({
            "file": f.name, "title": title, "start_sec": cursor,
            "duration_sec": dur, "size_bytes": size,
        })
        if size is None:
            sizes_known = False
        else:
            total += size
        if dur:
            cursor += dur
    artwork = "embedded" if embedded else (
        "image" if await _cover_image_off_loop(folder, True) is not None else None)
    return {
        "title": folder.name,
        "author": author,
        "narrator": narrator,
        "chapters": chapters,
        "duration_sec": cursor or None,
        "is_folder": True,
        "size_bytes": total if sizes_known else None,
        "files": entries,
        "artwork": artwork,
    }


async def _upsert_book(session, file_path: str, meta: dict[str, Any]) -> int:
    """INSERT ... ON CONFLICT (file_path) DO UPDATE, returning whether it was
    a fresh insert (1) or an update (0) is tracked by the caller via rowcount.
    Refreshes chapters/duration on re-index so tag edits propagate; the
    size, the files and where the cover is are what the walk found NOW (a
    cover.jpg taken away clears ``artwork``)."""
    result = await session.execute(
        text(
            """
            INSERT INTO audiobooks
                (title, author, narrator, chapters, file_path, is_folder,
                 duration_sec, added_via, size_bytes, files, artwork)
            VALUES
                (:title, :author, :narrator, CAST(:chapters AS JSONB), :fp,
                 :is_folder, :dur, 'index', :size, CAST(:files AS JSONB), :artwork)
            ON CONFLICT (file_path) DO UPDATE SET
                title = EXCLUDED.title,
                author = COALESCE(EXCLUDED.author, audiobooks.author),
                narrator = COALESCE(EXCLUDED.narrator, audiobooks.narrator),
                chapters = COALESCE(EXCLUDED.chapters, audiobooks.chapters),
                duration_sec = COALESCE(EXCLUDED.duration_sec, audiobooks.duration_sec),
                size_bytes = EXCLUDED.size_bytes,
                files = EXCLUDED.files,
                artwork = EXCLUDED.artwork
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {
            "title": meta["title"],
            "author": meta.get("author"),
            "narrator": meta.get("narrator"),
            "chapters": json.dumps(meta.get("chapters")) if meta.get("chapters") else None,
            "fp": file_path,
            "is_folder": meta.get("is_folder", False),
            "dur": meta.get("duration_sec"),
            "size": meta.get("size_bytes"),
            "files": json.dumps(meta.get("files")) if meta.get("files") else None,
            "artwork": meta.get("artwork"),
        },
    )
    row = result.first()
    return 1 if (row and row[0]) else 0


def _path_key(p: str | os.PathLike[str]) -> str:
    """One spelling of a path, for comparing a stored one with a walked
    or a deleted one (case folded where the OS folds it)."""
    return os.path.normcase(os.path.normpath(os.fspath(p)))


def _still_a_book(file_path: str, is_folder: bool) -> bool:
    """Is the book a stored row names still on disk? A file book: its file
    is there. A folder book: its folder is there and holds a chapter."""
    p = Path(file_path)
    try:
        if not inside_library(p):
            return False
        if is_folder:
            return p.is_dir() and bool(chapter_files(p))
        return p.is_file()
    except OSError:
        # Can't tell (a share that answers with an error): keep it.
        return True


async def _prune(session, seen: set[str], *, walked_any: bool, removed: set[str]) -> int:
    """Delete the rows whose book is gone, with their resume positions.

    A row the walk saw (or failed on) stays. Of the rest, one whose file or
    folder is no longer a book goes — but when the walk found nothing at
    all in AUDIOBOOKS_DIR only the ones in ``removed`` (just deleted by the
    caller) go: a folder with no book in it (empty, or holding only stray
    files) is what a share that isn't mounted looks like, and pruning then
    would lose every book's id and everyone's place until it came back."""
    rows = (await session.execute(
        text("SELECT id, file_path, is_folder FROM audiobooks")
    )).all()
    gone: list[int] = []
    for book_id, file_path, is_folder in rows:
        key = _path_key(file_path)
        if key in seen:
            continue
        if not walked_any and key not in removed and (
            _path_key(Path(file_path).resolve(strict=False)) not in removed
        ):
            continue
        if await asyncio.to_thread(_still_a_book, file_path, bool(is_folder)):
            continue
        gone.append(int(book_id))
    if not gone:
        if rows and not walked_any:
            log.warning(
                "audiobook indexer: no book found in AUDIOBOOKS_DIR; keeping %d listed "
                "book(s) (a drive or share not mounted?)", len(rows),
            )
        return 0
    await session.execute(
        text("DELETE FROM playback_positions WHERE item_type = 'audiobook' "
             "AND item_id = ANY(:ids)"),
        {"ids": gone},
    )
    await session.execute(text("DELETE FROM audiobooks WHERE id = ANY(:ids)"), {"ids": gone})
    return len(gone)


async def index_audiobooks_dir(*, removed: Iterable[str | os.PathLike[str]] = ()) -> dict[str, int]:
    """Walk ``AUDIOBOOKS_DIR``, upsert every book and prune the ones that
    are gone. Top-level ``.m4b``/audio files are single-file books;
    top-level directories are per-chapter folders. ``removed``: absolute
    paths the caller just deleted (pruned even when the folder walks empty;
    see :func:`_prune`). Returns ``{"scanned": N, "inserted": M,
    "errors": E, "pruned": P}``."""
    root = Path(settings.audiobooks_dir).expanduser()
    if not root.exists() or not root.is_dir():
        log.info("audiobook indexer: AUDIOBOOKS_DIR=%s missing; skipping", root)
        return {"scanned": 0, "inserted": 0, "errors": 0, "pruned": 0}

    log.info("audiobook indexer: walking %s", root)
    scanned = inserted = errors = pruned = 0
    added: list[Path] = []
    seen: set[str] = set()
    library = root.resolve(strict=False)
    async with session_scope() as session:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            try:
                if not inside_library(entry, library):
                    # A book file or folder linked in from outside the
                    # library: the audio route would refuse to play it.
                    log.info("audiobook indexer: %s links out of AUDIOBOOKS_DIR; skipped", entry)
                    continue
                if entry.is_file() and entry.suffix.lower() in _BOOK_FILE_EXTENSIONS:
                    seen.add(_path_key(entry))
                    meta = await _resolve_book_file(entry)
                    fp = str(entry)
                elif entry.is_dir():
                    meta = await _resolve_book_folder(entry)
                    if meta is None:
                        continue
                    seen.add(_path_key(entry))
                    fp = str(entry)
                else:
                    continue
                scanned += 1
                new = await _upsert_book(session, fp, meta)
                inserted += new
                if new:
                    added.append(entry)
            except Exception as e:
                errors += 1
                seen.add(_path_key(entry))  # a book that failed to read stays listed
                log.warning("audiobook indexer: failed on %s: %s", entry, e)

        # "The folder walked empty" means no BOOK was found: a stray
        # desktop.ini or .DS_Store in an unmounted mount point is not a
        # sign the share is there (2026-10-20 review).
        try:
            pruned = await _prune(
                session, seen, walked_any=bool(seen),
                removed={_path_key(Path(p).resolve(strict=False)) for p in removed}
                | {_path_key(p) for p in removed},
            )
        except Exception as e:  # noqa: BLE001 — the books just indexed still count
            log.warning("audiobook indexer: pruning failed: %s", e)
            pruned = 0

        if inserted > 0 or pruned > 0:
            await session.execute(
                text("SELECT pg_notify('podcasts_changed', :p)"),
                {"p": f"audiobooks inserted={inserted} pruned={pruned}"},
            )
        await session.commit()

    log.info(
        "audiobook indexer: scanned=%d inserted=%d pruned=%d errors=%d",
        scanned, inserted, pruned, errors,
    )
    if added:
        # The rooms' MPDs play a book by its path in their own database,
        # which a Windows or network folder never tells them about (F-V023).
        await update_rooms_for(added, reason="audiobook indexer")
    return {"scanned": scanned, "inserted": inserted, "errors": errors, "pruned": pruned}


class AudiobookIndexer(Worker):
    """Slow poll loop that re-indexes ``AUDIOBOOKS_DIR``. Books arrive rarely,
    so the cadence is generous; the interactive paths (startup sweep, the
    reindex endpoint) call ``index_audiobooks_dir`` directly.

    Declarative registration (design §4.5): gated by
    ``audiobook_indexer_enabled``, suppressed under stubs."""

    name = "audiobook_indexer"
    enabled_setting = "audiobook_indexer_enabled"
    interval_setting = "audiobook_indexer_interval_sec"
    stub_suppressed = True

    async def tick(self) -> dict[str, int]:
        return await index_audiobooks_dir()
