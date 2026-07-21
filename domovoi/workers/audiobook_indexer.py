"""Indexes ``AUDIOBOOKS_DIR`` into the ``audiobooks`` table.

The spoken-audio sibling of ``library_indexer``. Audiobooks come in two
on-disk shapes and this walker handles both:

  * A single ``.m4b`` (or ``.m4a`` / ``.mp3``) file with EMBEDDED chapters
    — the common Audible-style layout. Chapters are read via ffprobe
    (``-show_chapters``) with a mutagen fallback for tags.
  * A PER-CHAPTER FOLDER — one audio file per chapter, played in filename
    order. The folder is the book; each file is a chapter and its
    ``start_sec`` is the cumulative duration of the files before it.

Idempotent: upserts by ``file_path`` (the .m4b path OR the folder path), so
re-runs are cheap and a re-index picks up new books / chapter edits.

Fires from three places, mirroring library_indexer:
  * Domovoi startup (background sweep) so a curated library Just Works.
  * The ``audiobook_indexer`` poll loop (slow cadence — books arrive rarely).
  * The ``POST /api/audiobooks/reindex`` web admin endpoint.

Metadata is best-effort: a book with no tags still indexes with its
filename/folder name as the title so it's playable and resumable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

# Single-file audiobook containers. .m4b is the canonical chaptered format;
# .m4a / .mp3 single files are treated as one-chapter books.
_BOOK_FILE_EXTENSIONS = frozenset({".m4b", ".m4a", ".mp3", ".ogg", ".opus", ".flac"})
# Files that count as a chapter inside a per-chapter folder.
_CHAPTER_FILE_EXTENSIONS = _BOOK_FILE_EXTENSIONS


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


def _mutagen_tags(path: Path) -> dict[str, Any]:
    """title / author / narrator / duration_sec from tags (best-effort)."""
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {}
    try:
        f = MutagenFile(str(path))
    except Exception:
        return {}
    if f is None:
        return {}
    out: dict[str, Any] = {}
    info = getattr(f, "info", None)
    length = getattr(info, "length", None) if info is not None else None
    if isinstance(length, (int, float)) and length > 0:
        out["duration_sec"] = int(length)
    tags = getattr(f, "tags", None)
    if not tags:
        return out

    def _first(*keys: str) -> str | None:
        for k in keys:
            if k in tags:
                v = tags[k]
                if isinstance(v, list):
                    v = v[0] if v else None
                if v is None:
                    continue
                s = str(v).strip()
                if s:
                    return s
        return None

    out["title"] = _first("TIT2", "title", "\xa9nam", "Title", "\xa9alb", "album")
    out["author"] = _first("TPE1", "artist", "\xa9ART", "Author", "\xa9wrt", "composer")
    out["narrator"] = _first("\xa9nrt", "narrator", "TCOM", "composer")
    return out


async def _resolve_book_file(path: Path) -> dict[str, Any]:
    """Metadata for a single-file audiobook."""
    chapters, ff_dur = await _ffprobe_chapters(path)
    tags = _mutagen_tags(path)
    return {
        "title": tags.get("title") or path.stem,
        "author": tags.get("author"),
        "narrator": tags.get("narrator"),
        "chapters": chapters,
        "duration_sec": ff_dur or tags.get("duration_sec"),
        "is_folder": False,
    }


async def _resolve_book_folder(folder: Path) -> dict[str, Any] | None:
    """Metadata for a per-chapter folder. One chapter per audio file, in
    filename order; each chapter's ``start_sec`` is the cumulative duration
    of the files before it. Returns None if the folder has no audio."""
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _CHAPTER_FILE_EXTENSIONS
    )
    if not files:
        return None
    chapters: list[dict[str, Any]] = []
    cursor = 0
    author = narrator = None
    for i, f in enumerate(files):
        tags = _mutagen_tags(f)
        author = author or tags.get("author")
        narrator = narrator or tags.get("narrator")
        chapters.append({
            "title": tags.get("title") or f.stem,
            "start_sec": cursor,
            "file": f.name,
        })
        dur = tags.get("duration_sec")
        if isinstance(dur, int) and dur > 0:
            cursor += dur
    return {
        "title": folder.name,
        "author": author,
        "narrator": narrator,
        "chapters": chapters,
        "duration_sec": cursor or None,
        "is_folder": True,
    }


async def _upsert_book(session, file_path: str, meta: dict[str, Any]) -> int:
    """INSERT ... ON CONFLICT (file_path) DO UPDATE, returning whether it was
    a fresh insert (1) or an update (0) is tracked by the caller via rowcount.
    Refreshes chapters/duration on re-index so tag edits propagate."""
    result = await session.execute(
        text(
            """
            INSERT INTO audiobooks
                (title, author, narrator, chapters, file_path, is_folder,
                 duration_sec, added_via)
            VALUES
                (:title, :author, :narrator, CAST(:chapters AS JSONB), :fp,
                 :is_folder, :dur, 'index')
            ON CONFLICT (file_path) DO UPDATE SET
                title = EXCLUDED.title,
                author = COALESCE(EXCLUDED.author, audiobooks.author),
                narrator = COALESCE(EXCLUDED.narrator, audiobooks.narrator),
                chapters = COALESCE(EXCLUDED.chapters, audiobooks.chapters),
                duration_sec = COALESCE(EXCLUDED.duration_sec, audiobooks.duration_sec)
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
        },
    )
    row = result.first()
    return 1 if (row and row[0]) else 0


async def index_audiobooks_dir() -> dict[str, int]:
    """Walk ``AUDIOBOOKS_DIR`` and upsert every book. Top-level ``.m4b``/audio
    files are single-file books; top-level directories are per-chapter folders.
    Returns ``{"scanned": N, "inserted": M, "errors": E}``."""
    root = Path(settings.audiobooks_dir).expanduser()
    if not root.exists() or not root.is_dir():
        log.info("audiobook indexer: AUDIOBOOKS_DIR=%s missing; skipping", root)
        return {"scanned": 0, "inserted": 0, "errors": 0}

    log.info("audiobook indexer: walking %s", root)
    scanned = inserted = errors = 0
    async with session_scope() as session:
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            try:
                if entry.is_file() and entry.suffix.lower() in _BOOK_FILE_EXTENSIONS:
                    meta = await _resolve_book_file(entry)
                    fp = str(entry)
                elif entry.is_dir():
                    meta = await _resolve_book_folder(entry)
                    if meta is None:
                        continue
                    fp = str(entry)
                else:
                    continue
                scanned += 1
                inserted += await _upsert_book(session, fp, meta)
            except Exception as e:
                errors += 1
                log.warning("audiobook indexer: failed on %s: %s", entry, e)

        if inserted > 0:
            await session.execute(
                text("SELECT pg_notify('podcasts_changed', :p)"),
                {"p": f"audiobooks inserted={inserted}"},
            )
        await session.commit()

    log.info(
        "audiobook indexer: scanned=%d inserted=%d errors=%d",
        scanned, inserted, errors,
    )
    return {"scanned": scanned, "inserted": inserted, "errors": errors}


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
