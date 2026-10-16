"""Walks ``MUSIC_DIR`` and populates ``library_tracks`` for every audio
file found. Idempotent: re-runs use ``ON CONFLICT (file_path) DO
NOTHING`` so already-indexed files are cheap.

Why this exists: provider download pipelines write ``library_tracks`` rows for
every track it downloads, so anything added via "add to my library"
shows up. But files placed in ``MUSIC_DIR`` by hand (drag-and-drop,
rsync from another machine, an old collection moved over) only
appeared in MPD's index — the core's metadata view stayed
empty. That broke:

  * Random-play (``play a song`` / ``shuffle``) — picks from
    ``library_tracks``, so an unindexed library returns "your library
    is empty."
  * LibraryHandler's ``how many songs`` / ``what did I add today`` /
    ``do I have X`` queries — same DB lookup.
  * Add-to-library dedup (acquisition enqueue) — couldn't catch
    "manually-placed file already exists" cases.

Indexer fires from three places:

  * Domovoi startup (background task in lifespan) — first-boot
    sweep so manually-curated libraries Just Work after the first
    `python -m domovoi.main` cycle.
  * The voice "rescan my library" / "update the music library"
    command — pairs with the existing MPD ``update`` so both indexes
    stay in sync.
  * The ``/v1/admin/library/reindex`` admin endpoint — the
    dashboard's "Rescan library" button. Admin-gated since CORE-4,
    because it walks the whole tree and rescans every room's MPD.
  * The web upload handler, via ``index_paths`` — the BOUNDED variant,
    covering only the files that one request just wrote, so a
    device-tier upload does not have to reach an admin route to be
    indexed (F-A017).

Metadata strategy:

  1. ID3 / Vorbis / MP4 / ASF tags via ``domovoi.media_tags`` (title,
     artist, album, duration in seconds).
  2. If title or artist is empty, parse the filename. Reuses the core
     ``library_naming.parse_title_artist`` so the "Artist - Title.mp3"
     parsing rules are consistent across every ingest path (this
     indexer + provider plugins via ``sdk.library``).
  3. Last-resort fall back: title = filename stem, artist = NULL.

Files are recorded under ``added_via='manual'`` so the audit trail
distinguishes hand-placed files from provider downloads (which stamp
``added_via='voice'``).

Never on the event loop, and never twice (2026-10-16 delta review B2):
the walk and every tag read run in worker threads (the read in
``domovoi.executors``' bounded ``tags`` pool), and a file whose path is
already in ``library_tracks`` is skipped BEFORE its tags are read. An
indexed row is never updated from the file (a changed file keeps its row,
as it always has), so its path is all "already indexed" means, and a boot
re-reads only the files that are new since the last one. (Before, every
boot read every file's tags on the loop just to find each row already
there, and one slow file could stall the core past its loop watchdog.)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from domovoi import executors
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.library_naming import parse_title_artist as _parse_title_artist

log = logging.getLogger(__name__)


# Extensions the tag reader (domovoi.media_tags) reads. We skip everything else to avoid
# pointless inserts of cover art / lyrics / random binaries that
# happen to live in MUSIC_DIR.
_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".oga",
    ".opus", ".wav", ".wma", ".aac", ".alac",
})


def _read_tags(path: Path) -> dict[str, Any]:
    """Pull title / artist / album / duration_sec from a file's tags.

    Returns a dict with whatever was present; missing keys map to None.
    Best-effort: a corrupt file or a format the reader doesn't know just
    yields an empty dict, the indexer falls through to filename
    parsing.

    Each container keeps its tags under its own names (ID3 frames,
    Vorbis fields, MP4 atoms, ASF attributes), so the same field has a
    different name per format: ``Tags.first`` tries a few common names
    per field, in priority order, and a name a format can't hold is just
    absent. A multi-valued field gives its first value (an ID3v2.4 frame
    with two artists gives the first; before 2026-10-15 it gave both
    joined by a NUL, which Postgres refuses).
    """
    from domovoi import media_tags

    try:
        f = media_tags.read(path)
    except Exception as e:
        log.debug("tag read failed for %s: %s", path, e)
        return {}
    if f is None:
        return {}

    out: dict[str, Any] = {"title": None, "artist": None, "album": None, "duration_sec": None}

    if f.duration is not None and f.duration > 0:
        out["duration_sec"] = int(f.duration)

    tags = f.tags
    if tags is None:
        return out

    out["title"] = tags.first("TIT2", "title", "©nam", "Title")
    # WMA keeps the artist in Author and the album in WM/AlbumTitle, and
    # MP4 the album artist in aART (read since 2026-10-15).
    out["artist"] = tags.first("TPE1", "artist", "©ART", "Artist", "Author", "albumartist",
                               "TPE2", "aART", "WM/AlbumArtist")
    out["album"] = tags.first("TALB", "album", "©alb", "Album", "WM/AlbumTitle")
    return out


def _fallback_filename_parse(path: Path) -> tuple[str | None, str | None]:
    """When tags are missing, parse "Artist - Title.mp3" via the same
    helper every ingest path uses. Returns (artist, title); either may
    be None if the filename doesn't fit the pattern.
    """
    artist, title = _parse_title_artist(path.stem)
    if title is None:
        # No "Artist - Title" structure. Use the bare stem as title;
        # leave artist None and let the user fix it later via tags.
        return None, path.stem.strip() or None
    return artist, title


def _resolve_metadata_for(path: Path) -> dict[str, Any]:
    """Best-effort metadata pulled from tags first, filename fallback
    second. Always returns a dict with at least ``title`` populated
    (worst case: the bare filename stem). Blocking: the indexer runs it in
    the ``tags`` pool (:func:`read_metadata`)."""
    tags = _read_tags(path)
    title = tags.get("title")
    artist = tags.get("artist")
    if not title or not artist:
        fn_artist, fn_title = _fallback_filename_parse(path)
        title = title or fn_title
        artist = artist or fn_artist
    if not title:
        title = path.stem
    return {
        "title": title,
        "artist": artist,
        "album": tags.get("album"),
        "duration_sec": tags.get("duration_sec"),
    }


async def read_metadata(path: Path) -> dict[str, Any]:
    """:func:`_resolve_metadata_for`, in the ``tags`` pool: off the event
    loop, two files at a time at most."""
    return await executors.run("tags", _resolve_metadata_for, path)


async def _is_indexed(session: Any, path: Path) -> bool:
    row = (await session.execute(
        text("SELECT 1 FROM library_tracks WHERE file_path = :fp"), {"fp": str(path)},
    )).first()
    return row is not None


async def _indexed_paths(session: Any) -> set[str]:
    """Every ``file_path`` already in the library, in one query (a sweep
    checks each file it finds against it before reading any tags)."""
    rows = await session.execute(text("SELECT file_path FROM library_tracks"))
    return {r[0] for r in rows}


async def _insert_track(session: Any, path: Path, *, known_new: bool = False) -> bool:
    """INSERT one audio file into ``library_tracks``; no-op when its
    ``file_path`` is already there, which is checked BEFORE the file's
    tags are read (``known_new``: the caller just checked). Returns True
    when a row was really added (so callers can tell inserted from
    skipped).

    Split out of :func:`index_music_dir` so the bounded post-write path
    (:func:`index_paths`) writes rows exactly the way the full sweep
    does — same metadata resolution, same idempotent INSERT, same
    ``added_via='manual'`` audit stamp.

    INSERT ... SELECT WHERE NOT EXISTS, rather than INSERT ... ON
    CONFLICT DO NOTHING.

    Both no-op on duplicate file_path, but the ON CONFLICT form
    pre-allocates ``nextval('library_tracks_id_seq')`` BEFORE the
    conflict check fires and Postgres doesn't roll the sequence back —
    so every boot's re-scan of a 767-track library burns 767 sequence
    values for zero actual inserts. After a few restarts,
    library_tracks.id values skip into the thousands while the real row
    count barely moves.

    The SELECT/WHERE-NOT-EXISTS form only calls ``nextval()`` when the
    SELECT yields a row, so the sequence advances exactly once per real
    insert. Still uses rowcount to distinguish actual insert vs. no-op
    (a row another writer added between the check and the insert).
    """
    if not known_new and await _is_indexed(session, path):
        return False
    meta = await read_metadata(path)
    result = await session.execute(
        text(
            """
            INSERT INTO library_tracks
              (file_path, title, artist, album, duration_sec, added_via)
            SELECT :fp, :t, :a, :al, :d, 'manual'
            WHERE NOT EXISTS (
              SELECT 1 FROM library_tracks WHERE file_path = :fp
            )
            """
        ),
        {
            "fp": str(path),
            "t": meta["title"],
            "a": meta["artist"],
            "al": meta["album"],
            "d": meta["duration_sec"],
        },
    )
    return (result.rowcount or 0) > 0


def _under_music_dir(path: Path) -> Path | None:
    """``path`` resolved, if it really is a file inside MUSIC_DIR with an
    audio extension — otherwise None.

    :func:`index_paths` is reached from an upload handler, so its
    argument is caller-derived and gets checked here rather than
    trusted: a name that resolves outside MUSIC_DIR (``..``, a symlink,
    an absolute path from somewhere else) indexes nothing.
    """
    music_dir = Path(settings.music_dir).expanduser()
    try:
        root = music_dir.resolve(strict=False)
        resolved = Path(path).expanduser().resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    if resolved.suffix.lower() not in _AUDIO_EXTENSIONS:
        return None
    if not resolved.is_file():
        return None
    return resolved


async def index_paths(paths: Iterable[Path | str]) -> dict[str, int]:
    """Index exactly ``paths`` into ``library_tracks`` — the BOUNDED
    counterpart of :func:`index_music_dir`. Same counts dict.

    Why this exists (F-A017). Uploading into the library is an ordinary
    household action on the device tier, but the post-write index it
    depends on used to be the core's ``/v1/admin/library/reindex``,
    which CORE-4 moved to the admin tier — rightly, because it walks the
    whole tree and rescans every room's MPD. The upload then wrote its
    files and indexed nothing: a phone's upload reported success and the
    track never appeared.

    The gate is not the problem; the hop was. A post-write index is an
    implicit consequence of a write the caller was already allowed to
    make, and the work is bounded by what that one request wrote — so it
    runs in-process here, and the admin gate on the user-triggered
    library-wide sweep stays exactly where CORE-4 put it.

    Paths that are not audio files, are not files at all, or resolve
    outside MUSIC_DIR are counted as errors and indexed not at all.
    """
    wanted: list[Path] = []
    errors = 0
    for raw in paths:
        resolved = _under_music_dir(Path(raw))
        if resolved is None:
            errors += 1
            log.warning("library indexer: refusing to index %s", raw)
            continue
        wanted.append(resolved)

    inserted = skipped = 0
    if wanted:
        async with session_scope() as session:
            for path in wanted:
                try:
                    if await _insert_track(session, path):
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    errors += 1
                    log.warning("library indexer: failed on %s: %s", path, e)

            if inserted > 0:
                # Same commit-coupled NOTIFY the full sweep fires, so the
                # dashboard's Library / Stats views refetch the moment
                # the uploaded rows land.
                await session.execute(
                    text("SELECT pg_notify('library_changed', :payload)"),
                    {"payload": f"inserted={inserted}"},
                )

            await session.commit()

    log.info(
        "library indexer: indexed %d path(s) — inserted=%d skipped=%d errors=%d",
        len(wanted), inserted, skipped, errors,
    )
    return {
        "scanned": len(wanted),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
    }


def _audio_files(music_dir: Path) -> list[Path]:
    """Every audio file under ``music_dir`` (blocking: a worker thread)."""
    return [p for p in music_dir.rglob("*")
            if p.suffix.lower() in _AUDIO_EXTENSIONS and p.is_file()]


async def index_music_dir() -> dict[str, int]:
    """Walk ``MUSIC_DIR`` and INSERT ... ON CONFLICT DO NOTHING into
    library_tracks for every audio file. Returns counts:
    ``{"scanned": N, "inserted": M, "skipped": S, "errors": E}``.

    ``scanned`` = total audio files visited.
    ``inserted`` = rows actually added (new files).
    ``skipped`` = rows already present (file_path collision).
    ``errors`` = files that raised — count is logged, but failures
    are non-fatal so a single corrupt MP3 doesn't stop the sweep.
    """
    music_dir = Path(settings.music_dir).expanduser()
    if not music_dir.exists() or not music_dir.is_dir():
        log.warning(
            "library indexer: MUSIC_DIR=%s does not exist or is not a "
            "directory; skipping",
            music_dir,
        )
        return {"scanned": 0, "inserted": 0, "skipped": 0, "errors": 0}

    log.info("library indexer: walking %s", music_dir)
    scanned = inserted = skipped = errors = 0
    found = await asyncio.to_thread(_audio_files, music_dir)

    async with session_scope() as session:
        known = await _indexed_paths(session)
        for path in found:
            scanned += 1
            if str(path) in known:
                skipped += 1        # already indexed: its tags are not read again
                continue
            try:
                if await _insert_track(session, path, known_new=True):
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                log.warning("library indexer: failed on %s: %s", path, e)

        if inserted > 0:
            # Wake the web dashboard's LISTEN task so the Library / Stats
            # views refetch the moment new rows land — the web "Upload"
            # flow and the voice "rescan my library" path both lean on
            # this for a hands-free refresh. Same in-transaction
            # pg_notify pattern the downloads / calendar / radio mutation
            # sites use; the web poll loop is the ~1.5 s fallback if the
            # NOTIFY is ever missed. Only fire when something actually
            # changed (skip the no-op re-scans every boot does).
            await session.execute(
                text("SELECT pg_notify('library_changed', :payload)"),
                {"payload": f"inserted={inserted}"},
            )

        await session.commit()

    log.info(
        "library indexer: scanned=%d inserted=%d skipped=%d errors=%d",
        scanned, inserted, skipped, errors,
    )
    return {"scanned": scanned, "inserted": inserted, "skipped": skipped, "errors": errors}
