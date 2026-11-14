"""A playlist played in a room: the whole list becomes the room's queue.

Owner decision 2026-11-12. Before, a room played a playlist one song at a
time: the core kept the playlist's place in memory and "next" fetched the
following song. Now the room's music player (MPD) holds the whole playlist,
so the room's queue tools (jump, reorder, remove, add, play next) work on
it like on any queue, and Up next carries on when it is nearly over.

* **Load** — the playlist's songs, in order or shuffled, by their exact
  file (no tag search). The first :data:`FIRST_CHUNK` replace the queue at
  once, so the room starts without waiting on a long playlist; the rest
  are appended in the background, :data:`REST_CHUNK` at a time
  (:data:`LOADERS`). The loader stops when something else replaces or
  clears the queue (the player's ``queue_generation`` moves on), and Up
  next adds nothing while it runs (:data:`domovoi.up_next.rooms.LOADING`).
* **Skipped** — a song whose file isn't in the music folder, or that MPD
  refuses (not in its database), is left out and counted.
* **Up next** — a ``continue`` session is armed with the playlist as its
  source (the virtual Favorites list: its songs by id, named "favorites"),
  so picks join when 5 or fewer songs are left; with Autoplay off the
  queue ends with the playlist.
* **A copy** — the queue is a copy of the playlist. Nothing here, and
  nothing a queue edit does, writes the playlist's rows; only "save as
  playlist" writes one, and it always makes a new one. Room plays no
  longer move the playlist's ``resume_position`` either: a room plays a
  playlist from its top.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

log = logging.getLogger(__name__)

#: Songs that replace the room's queue before it starts.
FIRST_CHUNK = 50
#: Songs per background append after that.
REST_CHUNK = 100

FAVORITES_ID = 0
FAVORITES_NAME = "Favorites"

#: room id -> the task appending the rest of a long playlist.
LOADERS: dict[str, asyncio.Task] = {}


class PlaylistMissing(Exception):
    """No playlist with that id."""


class PlaylistEmpty(Exception):
    """The playlist has no songs."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class NothingPlayable(Exception):
    """None of the playlist's songs could be queued."""

    def __init__(self, name: str, total: int) -> None:
        super().__init__(name)
        self.name = name
        self.total = total


@dataclass
class RoomPlaylistPlay:
    playlist_id: int
    name: str
    mode: str                     # "ordered" | "shuffle"
    total: int                    # songs in the playlist
    queued: int                   # songs in the room's queue so far
    skipped: int                  # songs left out so far
    loading: bool                 # the rest is still being added
    first: dict[str, Any]         # the song playing first: id, title, artist
    session_id: str | None        # the room's Up next session


async def playlist_rows(s: Any, playlist_id: int) -> tuple[str, list[dict[str, Any]]]:
    """The playlist's name and its songs in playlist order (Favorites: by
    id, as it always played). Raises :class:`PlaylistMissing`."""
    if playlist_id == FAVORITES_ID:
        rows = (await s.execute(text(
            "SELECT id, title, artist, file_path FROM library_tracks "
            "WHERE favorited ORDER BY id"))).mappings().all()
        return FAVORITES_NAME, [dict(r) for r in rows]
    row = (await s.execute(text("SELECT name FROM playlists WHERE id = :p"),
                           {"p": playlist_id})).first()
    if row is None:
        raise PlaylistMissing(str(playlist_id))
    rows = (await s.execute(text(
        "SELECT lt.id, lt.title, lt.artist, lt.file_path FROM playlist_tracks pt "
        "JOIN library_tracks lt ON lt.id = pt.track_id "
        "WHERE pt.playlist_id = :p ORDER BY pt.position, pt.id"), {"p": playlist_id})).mappings().all()
    return str(row[0]), [dict(r) for r in rows]


def _context(playlist_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the Up next session names as its source."""
    from domovoi.up_next.session import IDS_CAP

    if playlist_id == FAVORITES_ID:
        return {"kind": "ids", "track_ids": [int(r["id"]) for r in rows][:IDS_CAP],
                "name": "favorites"}
    return {"kind": "playlist", "ref": str(playlist_id)}


async def play_in_room(
    room_id: str,
    mpd: Any,
    *,
    playlist_id: int,
    shuffle: bool = False,
    person_id: int | None = None,
    autoplay: bool | None = None,
    up_next: bool = True,
) -> RoomPlaylistPlay:
    """Replace ``room_id``'s queue with the playlist and arm its Up next.
    MPD is left paused on the first song, as every start leaves it (the
    caller's music_start starts it). Raises :class:`PlaylistMissing`,
    :class:`PlaylistEmpty`, :class:`NothingPlayable`; an MPD failure
    propagates with the room's queue as it was."""
    from domovoi.db.session import session_scope
    from domovoi.handlers.shared.library_match import mpd_file_for_library_path
    from domovoi.up_next import rooms as up_next_rooms

    async with session_scope() as s:
        name, rows = await playlist_rows(s, playlist_id)
    if not rows:
        raise PlaylistEmpty(name)
    order = list(rows)
    if shuffle:
        random.shuffle(order)
    playable: list[tuple[dict[str, Any], str]] = []
    skipped = 0
    for r in order:
        uri = mpd_file_for_library_path(r.get("file_path") or "")
        if uri:
            playable.append((r, uri))
        else:
            skipped += 1

    _stop_loader(room_id)
    at = 0
    queued: list[dict[str, Any]] = []
    while at < len(playable) and not queued:
        chunk = playable[at: at + FIRST_CHUNK]
        at += len(chunk)
        queued = await mpd.prepare_files([u for _, u in chunk])
        skipped += len(chunk) - len(queued)
    if not queued:
        raise NothingPlayable(name, len(rows))
    first_file = str(queued[0].get("file") or "")
    first = next((r for r, u in playable if u == first_file), playable[0][0])

    session_id: str | None = None
    if up_next:
        try:
            session_id = await up_next_rooms.start_from_request(
                room_id, track_id=int(first["id"]), context=_context(playlist_id, rows),
                person_id=person_id, autoplay=autoplay, client=mpd, whole_list=True,
            )
        except Exception as e:  # noqa: BLE001 — the playlist plays without it
            log.info("up next: the playlist in %s armed nothing: %s", room_id, e)
            await up_next_rooms.end(room_id, "a playlist")
    else:
        await up_next_rooms.end(room_id, "a playlist without up next")

    rest = playable[at:]
    if rest:
        up_next_rooms.LOADING.add(room_id)
        LOADERS[room_id] = asyncio.create_task(
            _load_rest(room_id, mpd, rest, generation=getattr(mpd, "queue_generation", 0), name=name),
            name=f"room-playlist-{room_id}",
        )
    else:
        up_next_rooms.LOADING.discard(room_id)
    return RoomPlaylistPlay(
        playlist_id=playlist_id, name=name, mode="shuffle" if shuffle else "ordered",
        total=len(rows), queued=len(queued), skipped=skipped, loading=bool(rest),
        first={"id": int(first["id"]), "title": first.get("title"), "artist": first.get("artist")},
        session_id=session_id,
    )


def _stop_loader(room_id: str) -> None:
    task = LOADERS.pop(room_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _load_rest(room_id: str, mpd: Any, rest: list[tuple[dict[str, Any], str]], *,
                     generation: Any, name: str) -> int:
    """Append the rest of a long playlist, a chunk at a time, while the
    room's queue is still the one the play made. Returns the songs MPD
    refused."""
    from domovoi.up_next import rooms as up_next_rooms

    refused = 0
    added = 0
    me = asyncio.current_task()
    try:
        for n in range(0, len(rest), REST_CHUNK):
            if getattr(mpd, "queue_generation", 0) != generation:
                log.info("room playlist %r in %s: the queue was replaced; %d songs not added",
                         name, room_id, len(rest) - n)
                break
            answers = await mpd.queue_add_files([u for _, u in rest[n: n + REST_CHUNK]])
            if getattr(mpd, "queue_generation", 0) != generation:
                # Replaced while this chunk was on its way: it isn't the
                # playlist's queue any more, so take the chunk back out.
                mine = [int(a["id"]) for a in answers if a and a.get("id") is not None]
                if mine:
                    await mpd.queue_remove(mine)
                log.info("room playlist %r in %s: the queue was replaced; %d songs not added",
                         name, room_id, len(rest) - n)
                break
            got = sum(1 for a in answers if a)
            added += got
            refused += len(answers) - got
        if refused:
            log.info("room playlist %r in %s: %d songs skipped (not in the music player's database)",
                     name, room_id, refused)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — what was added plays; the rest is lost
        log.warning("room playlist %r in %s: adding the rest failed after %d songs: %s",
                    name, room_id, added, e)
    finally:
        if LOADERS.get(room_id) is me:
            LOADERS.pop(room_id, None)
            up_next_rooms.LOADING.discard(room_id)
        elif room_id not in LOADERS:
            up_next_rooms.LOADING.discard(room_id)
    return refused
