"""Saves the place in the book of every room that is playing one.

A room plays an audiobook from its own MPD ("play my book in the kitchen",
domovoi/handlers/spoken_audio.py), and its place is kept per room and
person in ``playback_positions``. Until 2026-10-20 that place was written
only when a voice command moved it (play, skip, chapter), so after an hour
of listening "resume my book" there started where it had last started, and
the phone's "Continue from the kitchen (2 h 13 m)" (``GET
/api/audiobooks/{id}/positions``) would have offered a place an hour stale.

Each tick asks every room's MPD what it is doing. A room that is PLAYING a
file under ``audiobooks/`` has its place written: MPD's elapsed in the file,
plus, for a per-chapter folder book, that file's start in the book. The row
written is the one the voice path made when the book started there — the
most recent for this book and room, whoever's it is — so a person's place
is only ever moved by what their own command started; a room with no such
row (nothing started by voice) is left alone rather than given a place
nobody owns.

Suppressed under stubs (no real MPD); the tick is unit-tested with stub
clients.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text

from domovoi.config import settings
from domovoi.workers.base import Worker

log = logging.getLogger(__name__)

_PREFIX = "audiobooks/"


def _start_of(file_name: str, files: Any, chapters: Any) -> int | None:
    """A folder book file's start in the book, from the stored ``files``
    (V032) or the chapters that name it; None when neither does."""
    for entries in (files, chapters):
        for e in entries if isinstance(entries, list) else []:
            if isinstance(e, dict) and e.get("file") == file_name:
                return int(e.get("start_sec") or 0)
    return None


async def _room_place(client: Any) -> tuple[str, float] | None:
    """``(MPD file, elapsed)`` when the room is playing an audiobook file."""
    try:
        if await client.state() != "play":
            return None
        song = await client.current_song() or {}
        elapsed = await client.elapsed_sec()
    except Exception as e:  # noqa: BLE001 — one room's player never stops the rest
        log.debug("room book positions: MPD probe failed: %s", e)
        return None
    f = str(song.get("file") or "")
    if not f.startswith(_PREFIX) or elapsed is None:
        return None
    return f, float(elapsed)


async def save_room_book_positions(clients: list[tuple[str, Any]] | None = None) -> int:
    """One pass over the rooms; returns how many places were written."""
    from domovoi.clients.mpd import iter_mpd_clients
    from domovoi.db.session import session_scope
    from domovoi.models import is_phone_room

    rooms = clients if clients is not None else iter_mpd_clients()
    playing: list[tuple[str, str, float]] = []
    for room, client in rooms:
        if not room or is_phone_room(room):
            continue
        place = await _room_place(client)
        if place is not None:
            playing.append((room, place[0], place[1]))
    if not playing:
        return 0

    base = Path(settings.audiobooks_dir).expanduser()
    written = 0
    async with session_scope() as s:
        for room, mpd_file, elapsed in playing:
            rel = PurePosixPath(mpd_file[len(_PREFIX):])
            if ".." in rel.parts:
                continue
            host = base.joinpath(*rel.parts)
            row = (await s.execute(
                text(
                    "SELECT id, file_path, is_folder, files, chapters FROM audiobooks "
                    "WHERE file_path = :single OR (is_folder AND file_path = :folder) "
                    "ORDER BY is_folder LIMIT 1"
                ),
                {"single": str(host), "folder": str(host.parent)},
            )).mappings().first()
            if row is None:
                continue
            pos = elapsed
            if row["is_folder"]:
                start = _start_of(host.name, row["files"], row["chapters"])
                if start is None:
                    continue
                pos += start
            result = await s.execute(
                text(
                    """
                    UPDATE playback_positions SET position_sec = :pos, updated_at = now()
                     WHERE id = (SELECT id FROM playback_positions
                                  WHERE item_type = 'audiobook' AND item_id = :id
                                    AND device_id = :room
                                  ORDER BY updated_at DESC LIMIT 1)
                    """
                ),
                {"pos": int(pos), "id": int(row["id"]), "room": room},
            )
            written += int(result.rowcount or 0)
        if written:
            await s.execute(text("SELECT pg_notify('podcast_positions_changed', 'rooms')"))
        await s.commit()
    return written


class RoomBookPositions(Worker):
    """Every ``room_book_positions_interval_sec`` (30 s), the place of each
    room playing an audiobook. Always on; suppressed under stubs."""

    name = "room_book_positions"
    enabled_setting = None
    interval_setting = "room_book_positions_interval_sec"
    stub_suppressed = True

    async def tick(self) -> int:
        return await save_room_book_positions()
