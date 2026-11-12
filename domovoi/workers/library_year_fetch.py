"""A year from MusicBrainz for tracks whose tags give none (V050, Up next P0).

A track's year comes from its own tags first (the indexer and the tag
backfill). Some files carry no date at all; when such a track has a
MusicBrainz recording id (song recognition or a provider download found
it), MusicBrainz knows the recording's first release, and this worker
asks for it: ``GET /ws/2/recording/<id>?fmt=json``, reading
``first-release-date``.

**What leaves the house:** the recording id (a public MusicBrainz
identifier) in the request path, and the project User-Agent. No title, no
artist, no file name, nothing about who listens.

**Gated, checked before every tick and again before every request**
(:func:`_egress_allowed`, the one gate):

* ``library_enricher_enabled`` — the existing song-recognition switch (it
  is what gives tracks their recording ids, and it already sends a file's
  fingerprint out), read live;
* ``egress.internet_allowed()`` — never under Internet "never";
* the connectivity probe reporting online (no probe = not online);
* no shutdown under way.

The request itself goes through the shared MusicBrainz client
(``domovoi/clients/musicbrainz.py``): one request a second process-wide,
shared with the alias fetch and the download pipeline's lookups, and its
``egress.require_destination`` check. The id is checked to be a UUID
before it is put into the path.

**Polite.** At most :data:`BATCH_SIZE` recordings per tick
(``library_year_fetch_interval_sec``, 30 s). MusicBrainz answering 503/429
pauses the fetch :data:`RATE_LIMIT_PAUSE_SEC`; no answer (or internet
turned off between the gate and the request) :data:`UNAVAILABLE_PAUSE_SEC`
— and neither marks the track asked, so it is asked again after the pause.
An answer (with a date or without one) and a 404 mark every track with
that recording id asked (``year_checked_at``), so each is asked once. A
refused request (another 4xx) marks that one track asked, so one bad id
cannot block the queue, and pauses too, so a refusal of every request
costs one track per pause, not the library.

**Only rows the tags already had their say on** (``tags_version`` at the
reader's version) and with no year; a year MusicBrainz gives never
replaces one from the file, and ``year_source`` says which it is.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from domovoi import egress, lifecycle
from domovoi.clients.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzRateLimited,
    MusicBrainzRejected,
    MusicBrainzUnavailable,
    get_musicbrainz_client,
    is_mbid,
)
from domovoi.config import settings
from domovoi.workers.base import Worker
from domovoi.library_genres import TAGS_VERSION

log = logging.getLogger(__name__)

#: Recordings asked about per tick (the client paces them at one a second).
BATCH_SIZE = 25
#: Pause after MusicBrainz answers 503 / 429.
RATE_LIMIT_PAUSE_SEC = 60.0
#: Pause after no answer, or a refused request.
UNAVAILABLE_PAUSE_SEC = 300.0


def _probe_online() -> bool:
    """The connectivity probe's verdict; no probe registered is NOT online
    (this gate decides whether anything leaves the house)."""
    from domovoi.connectivity import current_probe

    probe = current_probe()
    return probe is not None and bool(probe.online)


def _egress_allowed() -> bool:
    """THE gate (module docstring): the song-recognition switch, the
    internet answer, no shutdown, the probe online."""
    if not settings.library_enricher_enabled:
        return False
    if not egress.internet_allowed():
        return False
    if lifecycle.shutting_down():
        return False
    return _probe_online()


@dataclass
class _Status:
    state: str = "idle"           # off | offline | running | done | rate_limited | paused
    paused_until: float | None = None
    last_error: str | None = None


_STATUS = _Status()


def year_fetch_status() -> dict[str, Any]:
    return {"state": _STATUS.state, "last_error": _STATUS.last_error}


def reset_status_for_tests() -> None:
    global _STATUS
    _STATUS = _Status()


class YearFetchStore(Protocol):
    async def due(self, version: int, limit: int) -> list[str]: ...

    async def record(self, recording_id: str, year: int | None) -> None: ...

    async def record_refused(self, recording_id: str) -> None: ...


_DUE_SQL = text(
    "SELECT musicbrainz_recording_id, min(id) AS first FROM library_tracks "
    "WHERE year IS NULL AND musicbrainz_recording_id IS NOT NULL "
    "AND year_checked_at IS NULL AND tags_version >= :v "
    "GROUP BY musicbrainz_recording_id ORDER BY first LIMIT :n"
)
# Every track with this recording id: a year only where there is none yet
# (the CASEs read the row as it was before this UPDATE).
_RECORD_SQL = text(
    "UPDATE library_tracks SET "
    "year = CASE WHEN year IS NULL THEN CAST(:y AS smallint) ELSE year END, "
    "year_source = CASE WHEN year IS NULL AND CAST(:y AS smallint) IS NOT NULL "
    "THEN 'musicbrainz' ELSE year_source END, "
    "year_checked_at = now() "
    "WHERE musicbrainz_recording_id = :mbid AND year_checked_at IS NULL"
)


class DbYearFetchStore:
    async def due(self, version: int, limit: int) -> list[str]:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            res = await s.execute(_DUE_SQL, {"v": version, "n": limit})
            return [str(r[0]) for r in res.all()]

    async def record(self, recording_id: str, year: int | None) -> None:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await s.execute(_RECORD_SQL, {"mbid": recording_id, "y": year})
            await s.commit()

    async def record_refused(self, recording_id: str) -> None:
        await self.record(recording_id, None)


class LibraryYearFetch(Worker):
    """See the module docstring."""

    name = "library_year_fetch"
    # Self-gated per tick (the switch applies without a restart, and the
    # runner never starts a worker that was off at boot).
    enabled_setting = None
    interval_setting = "library_year_fetch_interval_sec"
    stub_suppressed = True
    requires_online = True

    def __init__(self, *, client: MusicBrainzClient | None = None,
                 store: YearFetchStore | None = None) -> None:
        self._client = client
        self._store: YearFetchStore = store or DbYearFetchStore()

    def _mb(self) -> MusicBrainzClient:
        return self._client or get_musicbrainz_client()

    async def tick(self) -> dict[str, int]:
        result = {"asked": 0, "years": 0}
        if not _egress_allowed():
            _STATUS.state = "off" if not settings.library_enricher_enabled else "offline"
            return result
        now = time.time()
        if _STATUS.paused_until is not None and now < _STATUS.paused_until:
            return result
        _STATUS.paused_until = None
        due = await self._store.due(TAGS_VERSION, BATCH_SIZE)
        if not due:
            _STATUS.state = "done"
            return result
        _STATUS.state = "running"
        client = self._mb()
        for mbid in due:
            if not _egress_allowed():
                _STATUS.state = "off" if not settings.library_enricher_enabled else "offline"
                break
            if not is_mbid(mbid):
                # Not an id MusicBrainz could answer: never sent, marked asked.
                await self._store.record_refused(mbid)
                continue
            try:
                year = await client.recording_year(mbid)
            except MusicBrainzRateLimited:
                _STATUS.paused_until = time.time() + RATE_LIMIT_PAUSE_SEC
                _STATUS.state, _STATUS.last_error = "rate_limited", "rate_limited"
                break
            except MusicBrainzUnavailable as e:
                _STATUS.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
                _STATUS.state, _STATUS.last_error = "paused", f"unavailable: {e}"[:64]
                break
            except MusicBrainzRejected as e:
                await self._store.record_refused(mbid)
                _STATUS.paused_until = time.time() + UNAVAILABLE_PAUSE_SEC
                _STATUS.state, _STATUS.last_error = "paused", f"rejected: {e}"[:64]
                break
            result["asked"] += 1
            await self._store.record(mbid, year)
            if year is not None:
                result["years"] += 1
            _STATUS.last_error = None
        if result["asked"]:
            log.info("MusicBrainz year fetch: %d recordings asked, %d years",
                     result["asked"], result["years"])
        return result


__all__ = ["BATCH_SIZE", "LibraryYearFetch", "year_fetch_status"]
