"""Saves the place in the book of every room that is playing one.

A room plays an audiobook from its own MPD ("play my book in the kitchen",
domovoi/handlers/spoken_audio.py), and its place is kept per room and
person in ``playback_positions``. Until 2026-10-20 that place was written
only when a voice command moved it (play, skip, chapter), so after an hour
of listening "resume my book" there started where it had last started, and
the phone's "Continue from the kitchen (2 h 13 m)" (``GET
/api/audiobooks/{id}/positions``) would have offered a place an hour stale.

When the voice path starts or resumes a book in a room it records WHO
asked, in :data:`ROOM_BOOKS`: the book, the room's device slot, the person
(None for an unidentified listener) and the MPD files the book is made of.
Each tick asks every recorded room's MPD what it is doing; a room PLAYING
one of its book's files has exactly that slot written — MPD's elapsed in
the file, plus, for a per-chapter folder book, that file's start in the
book. Nobody else's place is ever touched: an unidentified listener writes
only the anonymous slot, a person only their own, and a room whose player
has moved on to something else (music, another book started some other
way) is forgotten and writes nothing. (Review 2026-10-20: the first cut
wrote the newest place for the book and room, whoever's, so a book playing
there for any other reason moved a person's two-hour place to 5 s.)

The record is in memory, in the core process: after a restart a room is
saved again from the next voice command that starts its book.

Suppressed under stubs (no real MPD); the tick is unit-tested with stub
clients.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from domovoi.workers.base import Worker

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoomBook:
    """A book a voice command started in a room, and whose place it is."""

    item_id: int
    device_id: str              # the room's slot in playback_positions
    person_id: int | None       # None: the unidentified listener's slot
    starts: dict[str, int] = field(default_factory=dict)  # MPD file → start in the book


ROOM_BOOKS: dict[str, RoomBook] = {}
_LOCK = threading.Lock()


def record_room_book(room_id: str, book: RoomBook) -> None:
    """The voice path: ``book`` is what now plays in ``room_id``, for
    ``book.person_id``. Replaces whatever the room had."""
    with _LOCK:
        ROOM_BOOKS[room_id] = book


def forget_room(room_id: str) -> None:
    with _LOCK:
        ROOM_BOOKS.pop(room_id, None)


async def _room_place(client: Any) -> tuple[str, float] | None:
    """``(MPD file, elapsed)`` when the room's player is playing."""
    try:
        if await client.state() != "play":
            return None
        song = await client.current_song() or {}
        elapsed = await client.elapsed_sec()
    except Exception as e:  # noqa: BLE001 — one room's player never stops the rest
        log.debug("room book positions: MPD probe failed: %s", e)
        return None
    f = str(song.get("file") or "")
    if not f or elapsed is None:
        return None
    return f, float(elapsed)


async def save_room_book_positions(clients: list[tuple[str, Any]] | None = None) -> int:
    """One pass over the recorded rooms; returns how many places were
    written. ``clients``: ``(room, MPD client)`` pairs (default: every
    provisioned room's)."""
    from domovoi.clients.mpd import iter_mpd_clients
    from domovoi.db.session import session_scope

    with _LOCK:
        recorded = dict(ROOM_BOOKS)
    if not recorded:
        return 0
    rooms = clients if clients is not None else iter_mpd_clients()
    writes: list[tuple[RoomBook, int]] = []
    for room, client in rooms:
        book = recorded.get(room)
        if book is None:
            continue
        place = await _room_place(client)
        if place is None:
            continue  # paused or stopped: the place last written stands
        mpd_file, elapsed = place
        if mpd_file not in book.starts:
            # The room plays something else now: this book is not playing.
            with _LOCK:
                if ROOM_BOOKS.get(room) is book:
                    del ROOM_BOOKS[room]
            continue
        writes.append((book, int(book.starts[mpd_file] + elapsed)))
    if not writes:
        return 0

    written = 0
    async with session_scope() as s:
        for book, pos in writes:
            result = await s.execute(
                text(
                    """
                    UPDATE playback_positions SET position_sec = :pos, updated_at = now()
                     WHERE item_type = 'audiobook' AND item_id = :id
                       AND device_id = :dev
                       AND person_id IS NOT DISTINCT FROM :pid
                    """
                ),
                {"pos": pos, "id": book.item_id, "dev": book.device_id, "pid": book.person_id},
            )
            written += int(result.rowcount or 0)
        if written:
            await s.execute(text("SELECT pg_notify('podcast_positions_changed', 'rooms')"))
        await s.commit()
    return written


class RoomBookPositions(Worker):
    """Every ``room_book_positions_interval_sec`` (30 s), the place of each
    room playing the audiobook a voice command started there. Always on;
    suppressed under stubs."""

    name = "room_book_positions"
    enabled_setting = None
    interval_setting = "room_book_positions_interval_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        return await save_room_book_positions()
