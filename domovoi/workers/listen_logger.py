"""The rooms' listening history (Up next P1, 2026-11-12).

Every few seconds (``playback_sweeper_interval_sec``, 5 s) this reads each
room's music player once (``playback_snapshot``: state, songid, file,
elapsed, length) and, when the song it saw last is over — the player moved
to another song, stopped, or lost its song — writes one ``music_listens``
row for it (domovoi/music_listens.py):

* ``outcome``: ``finished`` when 90 % or more of it played, else
  ``skipped`` when a skip ended it (a skip stamp, :func:`music_listens.note_skip`,
  written by every room skip path since the song began), else ``stopped``;
* ``played_sec``: how far the song really moved while it was watched (a
  seek forward adds nothing), plus, when the song was playing at the last
  look, the time since then up to its end — so a song that ran out between
  two looks counts as played to the end;
* ``started_at``: when it began (the first look, less how far in it was);
* ``person_id``: who the room's music was for when the song began
  (``music_room_listeners``: the voice that started it, or the claimed
  person of the phone or browser that replaced the queue). A record from
  before the room last stopped is ignored, and one a room has been stopped
  for 30 minutes is forgotten, so yesterday's starter doesn't own today's
  music (review 2026-11-12).

Only songs in the library are written (a stream, a podcast, a file the
library doesn't know is watched and not recorded), nothing is written under
"Keep listening history: Don't keep", and a probe that fails leaves the
room's song open for the next look. This closes the old gap where a song
the player moved on to by itself was never logged anywhere.

Skipped under ``USE_STUBS`` like the playback sweeper; the tick is tested
with stub players (domovoi/tests/test_music_listens.py).

Since Up next P4 the core registers :class:`~domovoi.workers.up_next_extender.UpNextExtender`,
which is this logger with the rooms' Up next on top (its two hooks,
:meth:`ListenLogger._after_look` and :meth:`ListenLogger._song_ended`), so
each room's player is still read once per tick.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text

from domovoi import music_listens
from domovoi.clients.mpd import iter_mpd_clients
from domovoi.config import settings
from domovoi.db.session import session_scope
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

#: The longest gap between two looks still counted as playing time: a tick
#: that came late (a busy loop, the worker paused) must not credit a song
#: with minutes nobody saw.
_MAX_GAP_SEC = 30.0

#: A room stopped this long is the household's again: whose its music was
#: is forgotten (music_listens.forget_room_listener).
ROOM_LISTENER_IDLE = timedelta(minutes=30)


@dataclass
class _Song:
    songid: Any
    file: str | None
    track_id: int | None
    duration: float | None
    started_at: datetime
    first_seen: float          # monotonic
    last_seen: float           # monotonic
    last_elapsed: float
    last_state: str
    played: float
    person_id: int | None


async def track_for_file(session: Any, uri: str | None) -> tuple[int, int | None] | None:
    """The library song (id, duration_sec) a player's file is, or None.

    The player's file is its path under the music folder
    ("Artist/Album/01 Song.flac"); the library keeps the full path on this
    server, in either slash. A stream (``http…``) is never a library song."""
    if not uri or "://" in uri:
        return None
    rel = uri.replace("\\", "/").lstrip("/")
    row = (
        await session.execute(
            text(
                """
                SELECT id, duration_sec FROM library_tracks
                WHERE right(replace(file_path, chr(92), '/'), length(:rel) + 1) = '/' || :rel
                   OR replace(file_path, chr(92), '/') = :rel
                ORDER BY length(file_path)
                LIMIT 1
                """
            ),
            {"rel": rel},
        )
    ).first()
    return (int(row[0]), row[1]) if row is not None else None


class ListenLogger(Worker):
    name = "listen_logger"
    enabled_setting = None
    interval_setting = "playback_sweeper_interval_sec"
    stub_suppressed = True

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._clock = clock
        self._now = now
        self._songs: dict[str, _Song] = {}
        # room -> wall time of the last look that found it playing or
        # paused (the stop happened after it); and room -> when it stopped
        # (that last playing look), while it stays stopped.
        self._last_active: dict[str, datetime] = {}
        self._stopped_at: dict[str, datetime] = {}
        # room -> when it last stopped, kept once it plays again: whose its
        # music is must have been said after that.
        self._last_stop: dict[str, datetime] = {}
        self._forgotten: set[str] = set()

    async def tick(self) -> int:
        """One look at every room. Returns the listens written."""
        written = 0
        for room_id, client in iter_mpd_clients():
            try:
                written += await self._look(room_id, client)
            except Exception as e:  # noqa: BLE001 — one room never stops the rest
                log.debug("listen logger: room %s: %s", room_id, e)
        return written

    # ── Hooks (the Up next extender, workers/up_next_extender.py) ──

    async def _after_look(self, room_id: str, client: Any, snap: dict[str, Any]) -> None:
        """Called after each successful look at a room, with its snapshot."""

    async def _song_ended(self, room_id: str, song: "_Song", played: float,
                          outcome: str) -> tuple[str | None, str]:
        """A song ended (whatever gets written): ``(session_id, origin)`` for
        its listen row. Here: no session, ``explicit``."""
        return None, "explicit"

    async def _look(self, room_id: str, client: Any) -> int:
        snap_fn = getattr(client, "playback_snapshot", None)
        if snap_fn is None:
            return 0
        try:
            snap = await snap_fn()
        except Exception as e:  # noqa: BLE001 — unreachable: look again next tick
            log.debug("listen logger: probe of %s failed: %s", room_id, e)
            return 0
        try:
            return await self._observe(room_id, snap)
        finally:
            await self._after_look(room_id, client, snap)

    async def _observe(self, room_id: str, snap: dict[str, Any]) -> int:
        now = self._clock()
        state = snap.get("state") or "stop"
        songid = snap.get("songid")
        elapsed = float(snap.get("elapsed") or 0.0)
        current = self._songs.get(room_id)
        playing = state in ("play", "pause") and songid is not None
        await self._track_stops(room_id, playing)

        if current is not None and playing and current.songid == songid:
            self._advance(current, now, elapsed, state)
            return 0

        written = 0
        if current is not None:
            written = await self._close(room_id, current, now)
            self._songs.pop(room_id, None)
        if playing:
            self._songs[room_id] = await self._open(room_id, snap, now, elapsed, state)
        return written

    async def _track_stops(self, room_id: str, playing: bool) -> None:
        wall = self._now()
        if playing:
            self._last_active[room_id] = wall
            if room_id in self._stopped_at:
                self._last_stop[room_id] = self._stopped_at.pop(room_id)
            self._forgotten.discard(room_id)
            return
        stopped = self._stopped_at.get(room_id)
        if stopped is None:
            # Stopped since the last look that found it active (or since now,
            # for a room never seen playing in this run).
            stopped = self._last_active.get(room_id, wall)
            self._stopped_at[room_id] = stopped
        if room_id not in self._forgotten and wall - stopped >= ROOM_LISTENER_IDLE:
            self._forgotten.add(room_id)
            try:
                async with session_scope() as s:
                    await music_listens.forget_room_listener(s, room_id, before=stopped)
            except Exception as e:  # noqa: BLE001 — retried by the next idle room
                self._forgotten.discard(room_id)
                log.debug("listen logger: forgetting %s's listener failed: %s", room_id, e)

    def _advance(self, song: _Song, now: float, elapsed: float, state: str) -> None:
        moved = elapsed - song.last_elapsed
        gap = now - song.last_seen
        # Forward by no more than the time that passed (a seek forward is
        # not listening); backwards (a seek back, "previous" restarting the
        # song) adds nothing and moves the mark back.
        if moved > 0:
            song.played += min(moved, gap + 1.0, _MAX_GAP_SEC)
        song.last_elapsed = elapsed
        song.last_seen = now
        song.last_state = state

    async def _open(self, room_id: str, snap: dict[str, Any], now: float,
                    elapsed: float, state: str) -> _Song:
        track = None
        person = None
        try:
            async with session_scope() as s:
                track = await track_for_file(s, snap.get("file"))
                if track is not None:
                    # A record from before the room last stopped is about
                    # music that has ended.
                    person = await music_listens.room_listener(
                        s, room_id, since=self._last_stop.get(room_id))
        except Exception as e:  # noqa: BLE001 — unknown song: watched, not written
            log.debug("listen logger: song lookup for %s failed: %s", room_id, e)
        duration = snap.get("duration")
        if not duration and track is not None and track[1]:
            duration = float(track[1])
        # Seen for the first time this far in: it began that long ago, and
        # that much of it played (the worker can't tell a seek from a
        # first look after a restart; at most one interval is unseen).
        return _Song(
            songid=snap.get("songid"),
            file=snap.get("file"),
            track_id=track[0] if track is not None else None,
            duration=float(duration) if duration else None,
            started_at=self._now() - timedelta(seconds=max(0.0, elapsed)),
            first_seen=now,
            last_seen=now,
            last_elapsed=elapsed,
            last_state=state,
            played=max(0.0, elapsed),
            person_id=person,
        )

    async def _close(self, room_id: str, song: _Song, now: float) -> int:
        played = song.played
        if song.last_state == "play":
            # It was playing at the last look: count the time since, up to
            # its end (a song that ran out between two looks finished).
            extra = min(now - song.last_seen, _MAX_GAP_SEC)
            if song.duration:
                extra = min(extra, max(0.0, song.duration - song.last_elapsed))
            played += max(0.0, extra)
        skipped = music_listens.take_skip(room_id, song.first_seen)
        outcome = music_listens.outcome_for(played, song.duration, skipped)
        session_id, origin = await self._song_ended(room_id, song, played, outcome)
        if song.track_id is None or played < 1.0:
            return 0
        if not music_listens.recording(settings.music_listens_keep):
            return 0
        row = music_listens.clean_listen(
            {
                "track_id": song.track_id,
                "person_id": song.person_id,
                "room_id": room_id,
                "device_id": None,
                "player": "room",
                "session_id": session_id,
                "origin": origin,
                "started_at": song.started_at,
                "played_sec": played,
                "duration_sec": song.duration,
                "outcome": outcome,
            },
            now=self._now(),
        )
        if row is None:
            return 0
        try:
            async with session_scope() as s:
                return await music_listens.insert_listens(s, [row])
        except Exception as e:  # noqa: BLE001 — a lost row, never a stopped worker
            log.warning("listen logger: writing %s's listen failed: %s", room_id, e)
            return 0
