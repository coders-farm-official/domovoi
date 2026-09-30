"""Which rooms' music a person paused.

The core pauses and resumes a room's MPD itself all the time: a handler
queues a song paused, and the music_start → music_ready handshake (or its
fallback, domovoi/streaming.py) resumes it once the satellite's player is
listening. That same handshake follows every music_start — the auto-resume
after a voice turn, the restart after an announcement, the restore after a
drop-in call — so without a record of who paused it, any of them un-paused
music a person had paused. Since timers and reminders are announced in
every room (domovoi/timer_delivery.py), a kitchen timer un-paused the
office.

So a pause a person asked for (``MusicHandler``'s pause: voice, the
dashboard, the app) is recorded here, per room, and the handshake keeps
such a room paused: the satellite's player still comes back (it rejoins
the paused stream, so a later "resume" is heard at once), MPD stays where
the person left it. Resume, stop, and starting something new clear it.

In memory, like ``resumable_music``: a core restart forgets it, and MPD's
own paused state is kept anyway.
"""

from __future__ import annotations

from typing import Any

_ATTR = "music_paused_by_person"


def _rooms(app: Any, *, create: bool) -> set[str] | None:
    state = getattr(app, "state", None) if app is not None else None
    if state is None:
        return None
    rooms = getattr(state, _ATTR, None)
    if rooms is None and create:
        rooms = set()
        setattr(state, _ATTR, rooms)
    return rooms


def note_paused_by_person(app: Any, room_id: str | None, paused: bool) -> None:
    """Record (``paused=True``) or forget a person's pause in ``room_id``."""
    if not room_id:
        return
    rooms = _rooms(app, create=paused)
    if rooms is None:
        return
    if paused:
        rooms.add(room_id)
    else:
        rooms.discard(room_id)


def paused_by_person(app: Any, room_id: str) -> bool:
    """Whether a person paused ``room_id``'s music and nobody resumed,
    stopped or replaced it since."""
    rooms = _rooms(app, create=False)
    return bool(rooms) and room_id in rooms
