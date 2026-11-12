"""Genre and year for the tracks already in the library (V050, Up next P0).

The indexer reads a file's tags once, when it first sees the file, and
never updates a row it already has (``library_indexer.py``), so the rows
that were in the library before V050 have no genre and no year, and
"Rescan" would not give them any. This worker re-reads them: every row
whose ``tags_version`` is below the reader's
(:data:`domovoi.workers.library_indexer.TAGS_VERSION`), oldest first,
:data:`BATCH_SIZE` per tick, and stamps the version on each.

**Paced and local.** One batch a tick (``library_tag_backfill_interval_sec``,
1 s), each file read in the bounded ``tags`` pool (two at a time, off the
event loop) — the same reads the indexer makes. Nothing leaves the house.
When nothing is left it checks again every :data:`IDLE_RECHECK_SEC`
(rows a provider download writes start at version 0 and are picked up
then). Measured 2026-11-12 on 2,000 generated files (a tagged MP3 each,
local disk, warm cache, Windows dev box): about 6 ms a file with its
UPDATE, so a tick is the interval plus ~0.3 s, and the pass is about two
and a half minutes for 6,000 tracks and three quarters of an hour for
100,000 (slower on a network share).

**What a read changes.** ``genre`` and ``genre_keys`` are set to what the
file says (NULL when it says nothing); ``year`` only when the file gives
one (``year_source = 'tag'``) — a year MusicBrainz gave earlier stays when
the file has none. Title, artist and album are never touched: the
household may have edited them.

**A file that is not there** (moved, deleted) is stamped read with no
genre, so the pass finishes; it is not retried until the reader's version
is bumped. A MUSIC_DIR that is not there at all (an unmounted drive or
share) pauses the pass instead, so a library is never stamped empty
because its disk was away.

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
from domovoi.workers.base import Worker
from domovoi.library_genres import TAGS_VERSION
from domovoi.workers.library_indexer import _AUDIO_EXTENSIONS, _read_tags

log = logging.getLogger(__name__)

#: Rows read per tick.
BATCH_SIZE = 50
#: How often an idle backfill looks for rows again.
IDLE_RECHECK_SEC = 60.0


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
    async def pending(self, version: int, limit: int) -> list[tuple[int, str]]: ...

    async def write(self, rows: list[tuple[int, dict[str, Any] | None]], version: int) -> None: ...


_PENDING_SQL = text(
    "SELECT id, file_path FROM library_tracks WHERE tags_version < :v "
    "ORDER BY tags_version, id LIMIT :n"
)
# year / year_source change only when the file gives a year; the
# tags_version guard makes a row another writer already read a no-op.
_WRITE_SQL = text(
    "UPDATE library_tracks SET genre = :g, genre_keys = CAST(:gk AS text[]), "
    "year = COALESCE(CAST(:y AS smallint), year), "
    "year_source = CASE WHEN CAST(:y AS smallint) IS NULL THEN year_source ELSE 'tag' END, "
    "tags_version = :v "
    "WHERE id = :id AND tags_version < :v"
)


class DbBackfillStore:
    async def pending(self, version: int, limit: int) -> list[tuple[int, str]]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            res = await s.execute(_PENDING_SQL, {"v": version, "n": limit})
            return [(int(r[0]), str(r[1])) for r in res.all()]

    async def write(self, rows: list[tuple[int, dict[str, Any] | None]], version: int) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            for track_id, meta in rows:
                meta = meta or {}
                await s.execute(_WRITE_SQL, {
                    "id": track_id, "v": version,
                    "g": meta.get("genre"), "gk": meta.get("genre_keys"), "y": meta.get("year"),
                })
            await s.commit()


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

    async def tick(self) -> dict[str, int]:
        result = {"read": 0, "missing": 0}
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

        rows = await self._store.pending(TAGS_VERSION, BATCH_SIZE)
        if not rows:
            self._idle_until = now + IDLE_RECHECK_SEC
            return result
        started = self._clock()
        out: list[tuple[int, dict[str, Any] | None]] = []
        for track_id, file_path in rows:
            meta = await executors.run("tags", read_genre_year, Path(file_path))
            if meta is None:
                result["missing"] += 1
            else:
                result["read"] += 1
            out.append((track_id, meta))
        await self._store.write(out, TAGS_VERSION)
        log.debug("tag backfill: %d read, %d missing in %.2f s",
                  result["read"], result["missing"], self._clock() - started)
        return result


__all__ = ["BATCH_SIZE", "LibraryTagBackfill", "read_genre_year"]
