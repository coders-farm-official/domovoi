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
  * The ``/v1/library/reindex`` admin endpoint — manual trigger.

Metadata strategy:

  1. ID3 / Vorbis / MP4 tags via ``mutagen`` (title, artist, album,
     duration in seconds).
  2. If title or artist is empty, parse the filename. Reuses the core
     ``library_naming.parse_title_artist`` so the "Artist - Title.mp3"
     parsing rules are consistent across every ingest path (this
     indexer + provider plugins via ``sdk.library``).
  3. Last-resort fall back: title = filename stem, artist = NULL.

Files are recorded under ``added_via='manual'`` so the audit trail
distinguishes hand-placed files from provider downloads (which stamp
``added_via='voice'``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.library_naming import parse_title_artist as _parse_title_artist

log = logging.getLogger(__name__)


# Extensions mutagen can read. We skip everything else to avoid
# pointless inserts of cover art / lyrics / random binaries that
# happen to live in MUSIC_DIR.
_AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".oga",
    ".opus", ".wav", ".wma", ".aac", ".alac",
})


def _read_tags(path: Path) -> dict[str, Any]:
    """Pull title / artist / album / duration_sec from a file's tags.

    Returns a dict with whatever was present; missing keys map to None.
    Best-effort: a corrupt file or a format mutagen can't parse just
    yields an empty dict, the indexer falls through to filename
    parsing.

    mutagen's ``File`` returns format-specific tag dicts (ID3, Vorbis,
    MP4), so the same key has different names depending on container.
    We try a few common names per field rather than special-casing
    every format.
    """
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {}

    try:
        f = MutagenFile(str(path))
    except Exception as e:
        log.debug("mutagen open failed for %s: %s", path, e)
        return {}
    if f is None:
        return {}

    out: dict[str, Any] = {"title": None, "artist": None, "album": None, "duration_sec": None}

    # Duration is stable across formats (info.length).
    info = getattr(f, "info", None)
    if info is not None:
        length = getattr(info, "length", None)
        if isinstance(length, (int, float)) and length > 0:
            out["duration_sec"] = int(length)

    # Tags vary by format. Common keys per field, in priority order.
    # Many mutagen tag values are lists (or special list-like classes);
    # take the first element and stringify.
    tags = getattr(f, "tags", None)
    if tags is None:
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

    out["title"] = _first("TIT2", "title", "©nam", "Title")
    out["artist"] = _first("TPE1", "artist", "©ART", "Artist", "albumartist", "TPE2")
    out["album"] = _first("TALB", "album", "©alb", "Album")
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
    (worst case: the bare filename stem)."""
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

    async with session_scope() as session:
        for path in music_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _AUDIO_EXTENSIONS:
                continue
            scanned += 1

            try:
                meta = _resolve_metadata_for(path)
                # INSERT ... SELECT WHERE NOT EXISTS, rather than
                # INSERT ... ON CONFLICT DO NOTHING.
                #
                # Both no-op on duplicate file_path, but the
                # ON CONFLICT form pre-allocates ``nextval('library_
                # tracks_id_seq')`` BEFORE the conflict check fires and
                # Postgres doesn't roll the sequence back — so every
                # boot's re-scan of a 767-track library burns 767
                # sequence values for zero actual inserts. After a few
                # restarts, library_tracks.id values skip into the
                # thousands while the real row count barely moves.
                #
                # The SELECT/WHERE-NOT-EXISTS form only calls
                # ``nextval()`` when the SELECT yields a row, so the
                # sequence advances exactly once per real insert.
                # Still uses rowcount to distinguish actual insert vs.
                # no-op for the scanned/inserted/skipped counts.
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
                if (result.rowcount or 0) > 0:
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
