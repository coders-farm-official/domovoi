"""Listening history: the songs each player played, and for whom
(``music_listens``, V051; Up next phase P1, 2026-11-12).

Imported by BOTH processes, so nothing here touches FastAPI or a process's
own session factory: every function that reads or writes takes a session.

Who writes:

* **rooms** — the core's :class:`domovoi.workers.listen_logger.ListenLogger`
  watches each room's music player and writes a song when it ends (the
  player moved on, stopped, or was skipped). A skip says so through
  :func:`note_skip`, which every room skip path calls.
* **browsers and phones** — ``POST /api/music/listens`` (web), in batches,
  late if the phone was offline.

Whose a listen is (``person_id``; NULL = the household):

* a room's: :func:`room_listener` — the identified voice that last started
  music in the room, or the claimed person of the phone or browser that
  last replaced its queue (:func:`set_room_listener`);
* a phone's or browser's: the listener it names (the "listening as"
  choice), else the person the device is claimed by, unless it is a shared
  screen (web/backend/api/music_listens.py).

How long: ``settings.music_listens_keep`` ("365" days by default, "90", or
"off" = record nothing and keep nothing). The core's
:class:`domovoi.workers.music_listens_pruner.MusicListensPruner` deletes
what is older; a save of the setting prunes at once (:func:`schedule_prune_now`).

Who may delete what: web/backend/api/music_listens.py and
docs/SECURITY_PRIVACY.md, "Listening history".
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text

from domovoi.config import music_listens_keep_days, normalize_music_listens_keep

log = logging.getLogger(__name__)

PLAYERS = ("room", "browser", "android", "ios")
ORIGINS = ("explicit", "list", "auto")
OUTCOMES = ("finished", "skipped", "stopped")

#: A song played this share of its length or more counts as finished, a
#: skip or not (a skip in the last seconds of an outro is not a dislike).
FINISHED_SHARE = 0.9

#: How old a late listen may be. A phone offline for longer drops it.
MAX_LATE = timedelta(days=7)
#: How far in the future a start time may be: a phone's clock that runs a
#: few minutes fast still logs; anything further is dropped.
MAX_CLOCK_AHEAD = timedelta(minutes=10)
#: The longest song a listen may describe (a day): bounds played_sec and
#: duration_sec against nonsense from a client.
MAX_SECONDS = 86_400


def outcome_for(played_sec: float, duration_sec: float | None, skipped: bool,
                ended: bool = False) -> str:
    """``finished`` when the song ran to its own end (``ended``: a player's
    natural end, whatever seconds were really heard) or once 90 % or more
    played (whatever ended it), else ``skipped`` when a skip ended it, else
    ``stopped``. The one rule every player follows (the browser's copy is
    ``ListenLog`` in player.jsx, the phone's ``ListenTracker.outcomeFor``).

    ``played_sec`` is the time really heard: a pause or a seek forward adds
    nothing, so a song seeked to its last seconds and left to end is a
    short ``finished`` listen (owner decision 2026-11-14)."""
    if ended:
        return "finished"
    if duration_sec and duration_sec > 0 and played_sec >= FINISHED_SHARE * duration_sec:
        return "finished"
    return "skipped" if skipped else "stopped"


# ─── Skip stamps (core only: in memory) ──────────────────────────────────

#: room_id -> time.monotonic() of the last skip asked for there. Written by
#: every room skip path (MusicHandler._smart_skip: the voice "next", the
#: dashboard's and the app's skip); read and consumed by the ListenLogger
#: when it closes the song that was playing.
SKIP_STAMPS: dict[str, float] = {}


def note_skip(room_id: str | None) -> None:
    """A person skipped the song playing in ``room_id``."""
    if room_id:
        SKIP_STAMPS[room_id] = time.monotonic()


def take_skip(room_id: str, since: float) -> bool:
    """Was there a skip in ``room_id`` at or after ``since`` (monotonic)?
    Consumes the stamp either way, so one skip marks one song."""
    stamp = SKIP_STAMPS.pop(room_id, None)
    return stamp is not None and stamp >= since


# ─── The setting ─────────────────────────────────────────────────────────


def keep_days(keep: object) -> int | None:
    """Days kept for a raw ``music_listens_keep``; None = don't keep."""
    return music_listens_keep_days(keep)


def recording(keep: object) -> bool:
    """Whether new listens are recorded at all under ``keep``."""
    return normalize_music_listens_keep(keep) != "off"


# ─── Writes ──────────────────────────────────────────────────────────────

_INSERT = text(
    """
    INSERT INTO music_listens (
        track_id, person_id, room_id, device_id, player, session_id, origin,
        started_at, played_sec, duration_sec, outcome
    )
    SELECT CAST(:track_id AS int),
           (SELECT id FROM people WHERE id = CAST(:person_id AS int)),
           CAST(:room_id AS text), CAST(:device_id AS text), CAST(:player AS text),
           CAST(:session_id AS uuid), CAST(:origin AS text),
           CAST(:started_at AS timestamptz), CAST(:played_sec AS int),
           CAST(:duration_sec AS int), CAST(:outcome AS text)
    WHERE EXISTS (SELECT 1 FROM library_tracks WHERE id = CAST(:track_id AS int))
    ON CONFLICT DO NOTHING
    """
)


def clean_listen(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """``row`` ready for :func:`insert_listens`, or None when it must be
    dropped: an unknown player / origin / outcome, a start time more than
    :data:`MAX_LATE` old or more than :data:`MAX_CLOCK_AHEAD` ahead, no room
    and no device. Seconds are clamped to 0 .. :data:`MAX_SECONDS`."""
    now = now or datetime.now(timezone.utc)
    started = row.get("started_at")
    if not isinstance(started, datetime):
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if started < now - MAX_LATE or started > now + MAX_CLOCK_AHEAD:
        return None
    if row.get("player") not in PLAYERS or row.get("origin") not in ORIGINS:
        return None
    if row.get("outcome") not in OUTCOMES:
        return None
    if not row.get("room_id") and not row.get("device_id"):
        return None
    try:
        track_id = int(row["track_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if track_id < 1:
        return None

    def _secs(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return max(0, min(MAX_SECONDS, int(round(float(v)))))
        except (TypeError, ValueError):
            return None

    played = _secs(row.get("played_sec"))
    if played is None:
        return None
    session_id = row.get("session_id")
    return {
        "track_id": track_id,
        "person_id": row.get("person_id"),
        "room_id": row.get("room_id"),
        "device_id": row.get("device_id"),
        "player": row["player"],
        "session_id": str(session_id) if session_id else None,
        "origin": row["origin"],
        "started_at": started,
        "played_sec": played,
        "duration_sec": _secs(row.get("duration_sec")),
        "outcome": row["outcome"],
    }


async def insert_listens(session: Any, rows: Iterable[dict[str, Any]]) -> int:
    """Insert cleaned rows (:func:`clean_listen`); returns how many were
    NEW. A row whose song is not in the library is skipped, as is one the
    table already has (the same device or room, song and start time: a
    batch sent twice); a person id nobody has is stored as the household."""
    added = 0
    for row in rows:
        result = await session.execute(_INSERT, row)
        added += result.rowcount or 0
    return added


# ─── Whose a room's music is ─────────────────────────────────────────────


async def set_room_listener(
    session: Any, room_id: str, person_id: int | None, device_id: str | None = None,
) -> None:
    """Record who ``room_id``'s music is for now: a voice's person (None
    for an unrecognised one), or the claimed person of the device that
    replaced the room's queue. A person id nobody has is the household."""
    await session.execute(
        text(
            """
            INSERT INTO music_room_listeners (room_id, person_id, device_id, set_at)
            VALUES (:room, (SELECT id FROM people WHERE id = CAST(:person AS int)),
                    CAST(:device AS text), now())
            ON CONFLICT (room_id) DO UPDATE
            SET person_id = EXCLUDED.person_id, device_id = EXCLUDED.device_id,
                set_at = EXCLUDED.set_at
            """
        ),
        {"room": room_id, "person": person_id, "device": device_id},
    )


async def room_listener(
    session: Any, room_id: str, *, since: datetime | None = None,
) -> int | None:
    """The person ``room_id``'s music is for, or None (the household).

    ``since``: when the room last stopped. A record from before that is
    about music that has since ended, so it is ignored (the household):
    a room someone started yesterday is not theirs today because nobody
    said otherwise (review 2026-11-12)."""
    row = (
        await session.execute(
            text("SELECT person_id, set_at FROM music_room_listeners WHERE room_id = :room"),
            {"room": room_id},
        )
    ).first()
    if row is None or row[0] is None:
        return None
    if since is not None and row[1] is not None and row[1] < since:
        return None
    return int(row[0])


async def forget_room_listener(session: Any, room_id: str, *, before: datetime) -> int:
    """A room stopped long enough: whose its music was (recorded before
    ``before``) is forgotten, and the room is the household's until someone
    starts music there again."""
    result = await session.execute(
        text("DELETE FROM music_room_listeners WHERE room_id = :room AND set_at < :before"),
        {"room": room_id, "before": before},
    )
    return result.rowcount or 0


async def note_voice_start(room_id: str | None, person_id: int | None) -> None:
    """A voice turn started music in its own room: the music is for the
    person who spoke (None when voice identification named nobody).
    Best-effort, on its own session: a failure here never fails the turn."""
    if not room_id:
        return
    try:
        from domovoi.db.session import session_scope

        async with session_scope() as s:
            await set_room_listener(s, room_id, person_id)
    except Exception as e:  # noqa: BLE001 — attribution is a nicety
        log.debug("listens: room listener for %s not recorded: %s", room_id, e)


# ─── Deletes ─────────────────────────────────────────────────────────────


async def prune(session: Any, keep: object, *, now: datetime | None = None) -> int:
    """Delete what ``keep`` no longer keeps: older than its days, or
    everything when it is "off". Returns the rows deleted."""
    days = keep_days(keep)
    if days is None:
        result = await session.execute(text("DELETE FROM music_listens"))
    else:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
        result = await session.execute(
            text("DELETE FROM music_listens WHERE started_at < :cutoff"), {"cutoff": cutoff},
        )
    return result.rowcount or 0


async def delete_person(session: Any, person_id: int) -> int:
    """Delete one person's listening history (their rows only; the
    household's and other people's stay). Returns the rows deleted."""
    result = await session.execute(
        text("DELETE FROM music_listens WHERE person_id = :p"), {"p": person_id},
    )
    return result.rowcount or 0


async def delete_all(session: Any) -> int:
    """Delete every listen, everyone's and the household's, and forget who
    each room's music was for. Returns the listens deleted."""
    result = await session.execute(text("DELETE FROM music_listens"))
    await session.execute(text("DELETE FROM music_room_listeners"))
    return result.rowcount or 0


async def summary(session: Any) -> dict[str, Any]:
    """Counts only, never a song: ``{total, oldest_at, household,
    people: [{person_id, name, listens}]}``."""
    total, oldest, household = (
        await session.execute(
            text(
                "SELECT COUNT(*), MIN(started_at), "
                "COUNT(*) FILTER (WHERE person_id IS NULL) FROM music_listens"
            )
        )
    ).one()
    people = (
        await session.execute(
            text(
                """
                SELECT p.id, p.name, COUNT(l.id)
                FROM people p LEFT JOIN music_listens l ON l.person_id = p.id
                GROUP BY p.id, p.name
                ORDER BY lower(p.name), p.id
                """
            )
        )
    ).all()
    return {
        "total": int(total or 0),
        "oldest_at": oldest,
        "household": int(household or 0),
        "people": [
            {"person_id": int(r[0]), "name": r[1], "listens": int(r[2] or 0)} for r in people
        ],
    }


# ─── The setting's save (core) ───────────────────────────────────────────

_PRUNE_TASKS: set[asyncio.Task] = set()
#: Under "Don't keep", the second prune after a save (see schedule_prune_now).
SECOND_PRUNE_AFTER_SEC = 60.0


def schedule_prune_now() -> None:
    """The reapply hook of ``music_listens_keep``: a save deletes what the
    new choice no longer keeps at once instead of at the pruner's next
    tick. Runs in the background; a failure is logged, and the pruner gets
    it on its next tick anyway."""
    from domovoi.config import settings

    async def _once() -> None:
        try:
            from domovoi.db.session import session_scope

            async with session_scope() as s:
                n = await prune(s, settings.music_listens_keep)
            if n:
                log.info("listening history: %d listen%s deleted on save", n, "" if n == 1 else "s")
        except Exception as e:  # noqa: BLE001
            log.warning("listening history: prune on save failed: %s", e)

    async def _run() -> None:
        await _once()
        if not recording(settings.music_listens_keep):
            # "Don't keep": a player that was mid-batch when the setting was
            # saved (a phone's outbox, a browser's flush already accepted
            # before the web saw the new value) may still land rows. Prune
            # once more a minute later (review 2026-11-12).
            await asyncio.sleep(SECOND_PRUNE_AFTER_SEC)
            if not recording(settings.music_listens_keep):
                await _once()

    try:
        task = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        return
    _PRUNE_TASKS.add(task)
    task.add_done_callback(_PRUNE_TASKS.discard)
