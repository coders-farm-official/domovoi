"""Genre and year for the tracks already in the library (V050, Up next P0).

The indexer reads a file's tags once, when it first sees the file, and
never updates a row it already has (``library_indexer.py``), so the rows
that were in the library before V050 have no genre and no year, and
"Rescan" would not give them any. This worker re-reads them: every row
whose ``tags_version`` is below the reader's
(:data:`domovoi.library_genres.TAGS_VERSION`), in id order,
:data:`BATCH_SIZE` per tick, and stamps the version on each.

**Paced and local.** One batch a tick (``library_tag_backfill_interval_sec``,
1 s), each file read in the bounded ``tags`` pool (two at a time, off the
event loop) — the same reads the indexer makes. Nothing leaves the house.
When a pass finds nothing more it checks again every
:data:`IDLE_RECHECK_SEC` (rows a provider download writes start at
version 0 and are picked up then). Measured 2026-11-12 on 2,000 generated
files (a tagged MP3 each, local disk, warm cache, Windows dev box): about
6 ms a file with its UPDATE, so a tick is the interval plus ~0.3 s, and
the pass is about two and a half minutes for 6,000 tracks and three
quarters of an hour for 100,000 (slower on a network share).

**What a read changes.** ``genre`` and ``genre_keys`` are set to what the
file says (``genre_source = 'tag'``, V052), replacing a genre MusicBrainz
gave; when the file says nothing they are NULL, except that a MusicBrainz
genre stays (``library_year_fetch.py``); ``year`` only when the file gives
one (``year_source = 'tag'``) — a year MusicBrainz gave earlier stays when
the file has none. Title, artist and album are never touched: the
household may have edited them. Each row is written under its own
savepoint: a row the database refuses is stamped read with no genre
instead, so one bad file can never roll back its batch and have the
same files re-read every second (security review of V050, finding 2).

**Files that are not there.** A MUSIC_DIR that is not there at all (an
unmounted drive) pauses the pass. Within a batch:

* a file here and there that is gone (moved, deleted) is stamped read with
  no genre, so the pass finishes; it is not retried until the reader's
  version is bumped;
* a batch MOSTLY of missing files (more than half, and at least
  :data:`MOSTLY_MISSING_MIN`) looks like a share or a drive mounted under
  MUSIC_DIR that is away (finding 4): those rows are left unread, the pass
  moves on past them, and the next pass tries them again. Only a row seen
  missing for longer than :data:`MISSING_GRACE_SEC` (this process's
  memory) is then stamped, so a folder that is really gone does not keep
  the Genres tab "reading" for ever.

**Progress** is in the database, so the web reads it without asking the
core: ``count(*) FILTER (WHERE tags_version >= TAGS_VERSION)`` of
``count(*)`` — the Genres tab says "reading genres from your files… N of
M" until they meet (web/backend/api/music_genres.py).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text

from domovoi import executors
from domovoi.config import settings
from domovoi.library_genres import TAGS_VERSION
from domovoi.workers.base import Worker
from domovoi.workers.library_indexer import _AUDIO_EXTENSIONS, _read_tags

log = logging.getLogger(__name__)

#: Rows read per tick.
BATCH_SIZE = 50
#: How often an idle backfill looks for rows again.
IDLE_RECHECK_SEC = 60.0
#: A batch is "mostly missing" when more than half its files are gone and
#: at least this many are.
MOSTLY_MISSING_MIN = 5
#: How long a row of a mostly-missing batch may stay missing before it is
#: stamped read anyway.
MISSING_GRACE_SEC = 6 * 3600.0

#: What a row the database refused, or a missing file, is written as.
_NOTHING: dict[str, Any] = {"genre": None, "genre_keys": None, "year": None}


def read_genre_year(path: Path) -> dict[str, Any] | None:
    """Blocking (the ``tags`` pool): the genre / genre_keys / year of the
    file at ``path``, or None when there is no such audio file. A file
    that can't be read gives empty values (``_read_tags`` is best-effort)."""
    try:
        if path.suffix.lower() not in _AUDIO_EXTENSIONS or not path.is_file():
            return None
    except OSError:
        return None
    tags = _read_tags(path)
    return {
        "genre": tags.get("genre"),
        "genre_keys": tags.get("genre_keys") or None,
        "year": tags.get("year"),
    }


class BackfillStore(Protocol):
    async def pending(self, version: int, limit: int, after_id: int) -> list[tuple[int, str]]: ...

    async def write(self, rows: list[tuple[int, dict[str, Any] | None]], version: int) -> int:
        """Write each row; returns how many the database refused (those
        are stamped with no genre instead)."""
        ...


_PENDING_SQL = text(
    "SELECT id, file_path FROM library_tracks WHERE tags_version < :v AND id > :after "
    "ORDER BY id LIMIT :n"
)
# year / year_source change only when the file gives a year; genre /
# genre_keys are the file's, except that a MusicBrainz genre (V052) stays
# when the file names none (the CASEs read the row as it was before this
# UPDATE); the tags_version guard makes a row another writer already read
# a no-op.
_WRITE_SQL = text(
    "UPDATE library_tracks SET "
    "genre = CASE WHEN CAST(:gk AS text[]) IS NULL AND genre_source = 'musicbrainz' "
    "THEN genre ELSE CAST(:g AS text) END, "
    "genre_keys = CASE WHEN CAST(:gk AS text[]) IS NULL AND genre_source = 'musicbrainz' "
    "THEN genre_keys ELSE CAST(:gk AS text[]) END, "
    "genre_source = CASE WHEN CAST(:gk AS text[]) IS NOT NULL THEN 'tag' "
    "WHEN genre_source = 'musicbrainz' THEN genre_source ELSE NULL END, "
    "year = COALESCE(CAST(:y AS smallint), year), "
    "year_source = CASE WHEN CAST(:y AS smallint) IS NULL THEN year_source ELSE 'tag' END, "
    "tags_version = :v "
    "WHERE id = :id AND tags_version < :v"
)


class DbBackfillStore:
    async def pending(self, version: int, limit: int, after_id: int) -> list[tuple[int, str]]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            res = await s.execute(_PENDING_SQL, {"v": version, "n": limit, "after": after_id})
            return [(int(r[0]), str(r[1])) for r in res.all()]

    async def write(self, rows: list[tuple[int, dict[str, Any] | None]], version: int) -> int:
        from domovoi.db.session import session_scope

        refused = 0
        async with session_scope() as s:
            for track_id, meta in rows:
                params = {"id": track_id, "v": version}
                try:
                    async with s.begin_nested():
                        await s.execute(_WRITE_SQL, {**params, **_params(meta or _NOTHING)})
                except Exception as e:  # noqa: BLE001 — one refused row never sinks the batch
                    refused += 1
                    log.warning("tag backfill: couldn't save the genre of track %s (%s); "
                                "stamping it with none", track_id, type(e).__name__)
                    await s.execute(_WRITE_SQL, {**params, **_params(_NOTHING)})
            await s.commit()
        return refused


def _params(meta: dict[str, Any]) -> dict[str, Any]:
    return {"g": meta.get("genre"), "gk": meta.get("genre_keys"), "y": meta.get("year")}


class LibraryTagBackfill(Worker):
    """See the module docstring."""

    name = "library_tag_backfill"
    enabled_setting = None
    interval_setting = "library_tag_backfill_interval_sec"
    stub_suppressed = True

    def __init__(self, *, store: BackfillStore | None = None,
                 clock: Any = time.monotonic) -> None:
        self._store: BackfillStore = store or DbBackfillStore()
        self._clock = clock
        self._idle_until: float | None = None
        self._warned_no_dir = False
        # The pass's position: rows after this id are next. A row skipped
        # because its batch was mostly missing stays behind it until the
        # next pass.
        self._after_id = 0
        # Row id -> when it was first seen missing in a mostly-missing batch.
        self._missing_since: dict[int, float] = {}

    async def tick(self) -> dict[str, int]:
        result = {"read": 0, "missing": 0, "deferred": 0, "refused": 0}
        now = self._clock()
        if self._idle_until is not None and now < self._idle_until:
            return result
        self._idle_until = None
        music_dir = Path(settings.music_dir).expanduser()
        if not music_dir.is_dir():
            if not self._warned_no_dir:
                log.warning("tag backfill: MUSIC_DIR %s is not there; waiting for it", music_dir)
                self._warned_no_dir = True
            self._idle_until = now + IDLE_RECHECK_SEC
            return result
        self._warned_no_dir = False

        rows = await self._store.pending(TAGS_VERSION, BATCH_SIZE, self._after_id)
        if not rows:
            # End of a pass: the next one starts from the top (and retries
            # what a mostly-missing batch left behind) after a pause.
            self._after_id = 0
            self._idle_until = now + IDLE_RECHECK_SEC
            return result
        started = self._clock()
        read: list[tuple[int, dict[str, Any] | None]] = []
        missing: list[int] = []
        for track_id, file_path in rows:
            meta = await executors.run("tags", read_genre_year, Path(file_path))
            if meta is None:
                missing.append(track_id)
            else:
                read.append((track_id, meta))
        self._after_id = rows[-1][0]

        mostly_missing = len(missing) >= MOSTLY_MISSING_MIN and 2 * len(missing) > len(rows)
        out = list(read)
        for track_id in missing:
            if not mostly_missing:
                out.append((track_id, None))
                continue
            since = self._missing_since.setdefault(track_id, now)
            if now - since >= MISSING_GRACE_SEC:
                out.append((track_id, None))
            else:
                result["deferred"] += 1
        if mostly_missing and result["deferred"]:
            log.info("tag backfill: %d of %d files in a batch are not there (a drive or share "
                     "away?); leaving them for the next pass", len(missing), len(rows))
        for track_id, meta in out:
            if meta is None:
                result["missing"] += 1
                self._missing_since.pop(track_id, None)
        result["read"] = len(read)
        if out:
            result["refused"] = await self._store.write(out, TAGS_VERSION)
        log.debug("tag backfill: %s in %.2f s", result, self._clock() - started)
        return result


__all__ = ["BATCH_SIZE", "LibraryTagBackfill", "read_genre_year"]
