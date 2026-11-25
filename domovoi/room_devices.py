"""The live room registry: which satellites are in which room (1.5.0).

A room is the place; a satellite is a device in one room (V073). A
satellite connects on ``/v1/stream/{satellite_id}`` and the server decides
its room (``satellites.room_id``). Two live views sit on ``app.state``:

* ``active_satellites``: satellite id → its live :class:`StreamSession`;
* ``active_sessions``: room id → the room's speaker, one live session per
  room. Every pre-1.5.0 reader keeps working on it: a room's timers,
  announcements, music and replies go to that session as before. The
  first device of a room to connect holds the slot; a reconnect of the
  same device takes it back; when it leaves, another live device of the
  room takes it over.

Plus a cached ``satellite → room`` map of every known device (loaded at
start, updated on hello, approve, move, merge and delete), so
:meth:`RoomDevices.room_of` and :meth:`RoomDevices.devices_in` are
synchronous (room playout, ``domovoi/room_playout``, reads them per call).

Every device from before 1.5.0 has satellite id = room id, so the views
agree with the old ones until a room holds several devices.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import HTTPException, Request

from domovoi.room_ids import is_room_id

log = logging.getLogger(__name__)


class RoomDevices:
    def __init__(self, state: Any) -> None:
        # The two live maps are read through ``state`` on every call, so a
        # caller (or a test) that swaps ``app.state.active_sessions`` for a
        # new dict is still the one this registry reads and writes.
        self._state = state
        self._room: dict[str, str] = {}  # satellite -> room, every known device
        self._on_move: list[Callable[[str], Any]] = []

    @property
    def sessions(self) -> dict[str, Any]:
        """room -> the room's speaker session (``app.state.active_sessions``)."""
        d = getattr(self._state, "active_sessions", None)
        if not isinstance(d, dict):
            d = {}
            self._state.active_sessions = d
        return d

    @property
    def satellites(self) -> dict[str, Any]:
        """satellite -> its live session (``app.state.active_satellites``)."""
        d = getattr(self._state, "active_satellites", None)
        if not isinstance(d, dict):
            d = {}
            self._state.active_satellites = d
        return d

    # ── the known devices (DB view) ─────────────────────────────────────

    async def refresh(self) -> None:
        """Reload the ``satellite → room`` map from ``satellites``."""
        from sqlalchemy import text

        from domovoi.db.session import session_scope

        async with session_scope() as s:
            rows = (await s.execute(text("SELECT satellite_id, room_id FROM satellites"))).all()
        self._room = {r[0]: r[1] for r in rows}

    def note(self, satellite_id: str, room_id: str | None) -> None:
        """Record (or, with None, forget) where a device is."""
        if room_id is None:
            self._room.pop(satellite_id, None)
        else:
            self._room[satellite_id] = room_id

    def room_of(self, satellite_id: str) -> str | None:
        live = self.satellites.get(satellite_id)
        if live is not None:
            return live.room_id
        return self._room.get(satellite_id)

    def devices_in(self, room_id: str) -> list[str]:
        """Every satellite in the room, live or not, by id."""
        ids = {s for s, r in self._room.items() if r == room_id}
        ids |= {s for s, sess in self.satellites.items() if sess.room_id == room_id}
        return sorted(ids)

    def is_shared(self, room_id: str) -> bool:
        return len(self.devices_in(room_id)) > 1

    # ── the live devices ───────────────────────────────────────────────

    def live_in(self, room_id: str) -> list[Any]:
        return [self.satellites[s] for s in sorted(self.satellites)
                if self.satellites[s].room_id == room_id]

    def listeners(self, room_id: str) -> list[Any]:
        """Live devices of the room with a running mic."""
        return [s for s in self.live_in(room_id) if getattr(s, "mic_enabled", True)]

    def speakers(self, room_id: str) -> list[Any]:
        """Live devices of the room that play its sound (all of them today)."""
        return self.live_in(room_id)

    def screens(self, room_id: str) -> list[Any]:
        return [s for s in self.live_in(room_id) if getattr(s, "room_display", None) is not None]

    def aec_device(self, room_id: str) -> Any | None:
        """The room's first live device with echo cancellation, if any."""
        for s in self.live_in(room_id):
            if s.abilities.allows("full_duplex"):
                return s
        return None

    def session_for(self, any_id: str) -> Any | None:
        """The live session for a satellite id, else the speaker of the
        room by that id (the admin routes take either)."""
        return self.satellites.get(any_id) or self.sessions.get(any_id)

    # ── sessions ───────────────────────────────────────────────────────

    def register(self, session: Any) -> Any | None:
        """A device passed hello. Returns the older session of the SAME
        device it replaces (the caller closes it), else None. Another
        device already speaking for the room keeps that slot."""
        sid, room = session.satellite_id, session.room_id
        old = self.satellites.get(sid)
        self.satellites[sid] = session
        self._room[sid] = room
        speaker = self.sessions.get(room)
        if speaker is None or speaker is old or speaker.satellite_id == sid:
            self.sessions[room] = session
        return old if old is not session else None

    def unregister(self, session: Any) -> bool:
        """The device's socket ended. True when it was the device's live
        session (a reconnect that replaced it leaves everything alone)."""
        sid = session.satellite_id
        if self.satellites.get(sid) is not session:
            self._drop_speaker(session)
            return False
        del self.satellites[sid]
        self._drop_speaker(session)
        return True

    def _drop_speaker(self, session: Any) -> None:
        room = session.room_id
        if self.sessions.get(room) is not session:
            return
        others = [s for s in self.live_in(room) if s is not session]
        if others:
            self.sessions[room] = others[0]
        else:
            self.sessions.pop(room, None)

    def evict(self, session: Any) -> None:
        """A send to the session failed: it is gone from both views."""
        self.unregister(session)

    # ── moves ──────────────────────────────────────────────────────────

    def on_move(self, callback: Callable[[str], Any]) -> None:
        """Call ``callback(satellite_id)`` after every move (room playout
        rebinds the device's speaker to its new room's timeline)."""
        if callback not in self._on_move:
            self._on_move.append(callback)

    def moved(self, satellite_id: str, room_id: str) -> Any | None:
        """The device is now in ``room_id`` (the DB already says so).
        Rebinds its live session, if any, and returns it."""
        self._room[satellite_id] = room_id
        live = self.satellites.get(satellite_id)
        if live is not None and live.room_id != room_id:
            self._drop_speaker(live)
            live.room_id = room_id
            if self.sessions.get(room_id) is None:
                self.sessions[room_id] = live
        for cb in list(self._on_move):
            try:
                cb(satellite_id)
            except Exception as e:  # noqa: BLE001 — a listener never breaks a move
                log.warning("room move: listener failed for %s: %s", satellite_id, e)
        return live


def registry(app: Any) -> RoomDevices:
    """The app's registry, created on first use."""
    state = app.state
    reg = getattr(state, "room_devices", None)
    if not isinstance(reg, RoomDevices):
        reg = RoomDevices(state)
        state.room_devices = reg
    return reg


# ── ids, and DB helpers for the admin routes ──────────────────────────────
#
# A satellite id and a room id follow ONE rule, domovoi/room_ids.py (every
# device from before 1.5.0 has its room's id); nothing here keeps its own.


async def require_id_paths(request: Request) -> None:
    """Route dependency: 422 when a ``{room_id}``, ``{other_room_id}`` or ``{satellite_id}``
    path parameter is not an id the house can have."""
    for key, what in (("room_id", "room"), ("satellite_id", "satellite"),
                      ("other_room_id", "room")):
        value = request.path_params.get(key)
        if value is not None and not is_room_id(value):
            raise HTTPException(status_code=422, detail=f"not a {what} id")

ROOM_ID_MAX = 32


async def new_room_id(s: Any, name: str) -> str:
    """A fresh room id from a name ("Kitchen 2" → ``kitchen_2``): the
    WebSocket-path alphabet, unique against rooms, devices, pairings and
    requests, so no device that says hello later lands in it by accident."""
    import re

    from sqlalchemy import text

    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "room"
    if not base[0].isalpha():
        base = f"room_{base}"
    base = base[:ROOM_ID_MAX]
    n = 1
    while True:
        cand = base if n == 1 else f"{base[:ROOM_ID_MAX - len(str(n)) - 1]}_{n}"
        taken = (await s.execute(text(
            "SELECT 1 FROM rooms WHERE room_id = :r "
            "UNION ALL SELECT 1 FROM satellites WHERE satellite_id = :r "
            "UNION ALL SELECT 1 FROM satellite_pairings WHERE satellite_id = :r "
            "UNION ALL SELECT 1 FROM satellite_approvals WHERE satellite_id = :r "
            "UNION ALL SELECT 1 FROM mpd_rooms WHERE room_id = :r LIMIT 1"
        ), {"r": cand})).first()
        if taken is None:
            from domovoi.room_ids import ROOM_ID_RE

            assert ROOM_ID_RE.fullmatch(cand), cand
            return cand
        n += 1


async def tokenless_refusal(
    s: Any, room_id: str, joining: list[str], *, paired: set[str] | None = None,
) -> str | None:
    """Owner answer Q13: a device without a pairing key may be a room on
    its own, never share one. With ``joining`` added to ``room_id``, the
    reason that breaks it (None when it doesn't). ``paired``: devices that
    count as having a key though their pairing is not written yet (one
    being approved)."""
    from sqlalchemy import text

    rows = (await s.execute(text(
        "SELECT s.satellite_id, (p.satellite_id IS NOT NULL) AS paired "
        "FROM satellites s LEFT JOIN satellite_pairings p USING (satellite_id) "
        "WHERE s.room_id = :r OR s.satellite_id = ANY(:j)"
    ), {"r": room_id, "j": list(joining)})).all()
    keyed = {r[0]: bool(r[1]) for r in rows}
    members = {r[0] for r in rows}
    for sid in joining:
        members.add(sid)
        keyed.setdefault(sid, False)
    for sid in paired or ():
        keyed[sid] = True
    if len(members) < 2:
        return None
    lone = sorted(m for m in members if not keyed.get(m))
    if lone:
        return (f"{', '.join(lone)} has no pairing key, so it can only be a room on "
                "its own — pair it first")
    return None
