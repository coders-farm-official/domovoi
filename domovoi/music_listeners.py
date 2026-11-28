"""Listen along (1.5.0): a paired phone plays a room's music itself.

A phone that listens along is a LISTENER, keyed by its ``device_id`` (the
one every phone already sends, ``devices.device_id``). It plays the room's
live MPD stream on its own speaker or headphones. It is never a room: it is
not in any shared queue's rooms (domovoi/music_groups.py), so routing,
voice, the hand-off order and "the last room plays on alone" are unchanged,
and a listener never keeps a shared queue alive or ends one.

The rules (design note LISTEN-ALONG, owner-approved 2026-11-28):

* **It follows the music.** On join the HOST of what the room hears is
  recorded (``music_groups.host_of``). When the host hands its queue on
  (``music_groups._hand_off``), the listeners move with it and the phone
  sees a new ``stream_url`` on its next heartbeat. A member room leaving its
  group, or the dashboard ending the shared queue while the host plays on,
  changes nothing for a listener. A room playing alone that joins another
  room's shared queue takes its listeners along to that queue's host (its
  own player only waits, paused; the note doesn't name this case, and
  silence on a paused stream would be the alternative). The end of a join
  and every heartbeat check the host again (:func:`follow`), so a room
  that joined a shared queue while a phone was joining it still takes it.
* **The host stopping stops it.** A person's stop reaching the host (the
  voice reply's ``music_stop``, or ``room_music.dispatch_music``) marks the
  listeners ``stopped`` — ``ended`` when that stop was a skip past the
  queue's last song (``Response.data["queue_ended"]``); the host's player
  found stopped by itself (the queue ran out: :func:`look`, from the
  playback sweeper and from each heartbeat) marks them ``ended``. The next
  heartbeat answers ``listening: false`` with that reason, once.
* **One room per phone.** Joining somewhere else replaces the entry. At
  most :data:`MAX_LISTENERS` phones listen at once.
* **60 seconds without a heartbeat** and the listener is gone (phone off the
  Wi-Fi, app killed): its next heartbeat answers ``not_listening``.
* **A core restart** reloads the mirror and gives every listener 60 seconds
  from boot to send a heartbeat; anyone who doesn't is dropped.

State is in memory (this process routes every music command) and mirrored
to the database (V089: ``music_listeners``) for the web process's cards and
for a restart. Memory changes never wait on anything; the mirror is then
brought in line with memory as it is at that moment (:func:`_sync`), so
two changes racing can't leave the row behind the entry. Database trouble
is logged, never fatal.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text

log = logging.getLogger(__name__)

#: How often a listening phone sends its heartbeat (the join answer says so).
HEARTBEAT_SEC = 15
#: A listener with no heartbeat this long is dropped.
EXPIRE_SEC = 60.0
#: The host's player read "stop" for this long (looks in a row, never a
#: play or pause between them): its queue ran out. A new song being loaded
#: can read "stop" for a moment.
STOPPED_GRACE_SEC = 4.0
#: At most this many phones listen along at once, house-wide.
MAX_LISTENERS = 32
#: A listener's name when its device has none on record.
DEFAULT_NAME = "A phone"
#: The devices tier's id shape (web/backend/api/devices.py _DEVICE_ID_RE).
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,63}\Z")

REASON_STOPPED = "stopped"
REASON_ENDED = "ended"
REASON_NOT_LISTENING = "not_listening"

NOT_LISTENING_MESSAGE = "This phone isn't listening along any more."


def valid_device_id(device_id: str | None) -> bool:
    return bool(device_id) and DEVICE_ID_RE.match(device_id) is not None


@dataclass
class Listener:
    """One phone listening along."""

    device_id: str
    #: The room whose player it hears (a shared queue's host, or a room
    #: playing alone).
    host: str
    #: The room the phone joined from, for its answers and messages: the
    #: host it moved to when that room handed its queue on.
    room_id: str
    joined_at: datetime
    #: monotonic: the last join or heartbeat.
    seen: float
    name: str = DEFAULT_NAME
    #: That room's label from Settings → Rooms, when it has one.
    label: str | None = None
    #: The host's player state last read ("play" or "pause"): what an
    #: answer says when the player doesn't answer this time.
    state: str = "play"
    #: Set once the host stopped: ``stopped`` or ``ended``. The next
    #: heartbeat says so and forgets the listener.
    reason: str | None = None
    message: str | None = None

    @property
    def live(self) -> bool:
        return self.reason is None

    def to_dict(self) -> dict[str, str]:
        return {"device_id": self.device_id, "name": self.name}


_listeners: dict[str, Listener] = {}
#: host room -> monotonic: when its player was first read stopped, while
#: listeners hear it.
_stopped_since: dict[str, float] = {}
#: Mirror writes one at a time (each writes memory as it is then).
_db_lock = asyncio.Lock()

#: Mirror changes to the database. The suite's conftest turns it off for
#: tests that don't ask for the database (they share one test database).
PERSIST = True


def reset(*, persist: bool = True) -> None:
    """Forget everything (tests)."""
    global _db_lock, PERSIST
    PERSIST = persist
    _db_lock = asyncio.Lock()
    _listeners.clear()
    _stopped_since.clear()


def room_name(room_id: str, label: str | None = None) -> str:
    """How a message names a room: its label, else its id read as words
    ("Kitchen", "Dining room")."""
    name = " ".join((label or "").split())
    if name:
        return name
    words = " ".join(str(room_id or "").replace("_", " ").replace("-", " ").split())
    return words[:1].upper() + words[1:] if words else "the room"


def _expired(entry: Listener, now: float | None = None) -> bool:
    return (now if now is not None else time.monotonic()) - entry.seen > EXPIRE_SEC


def get(device_id: str | None) -> Listener | None:
    """The phone's entry (a stopped one too, until its next heartbeat), or
    None. An entry past its 60 seconds counts as gone."""
    if not device_id:
        return None
    entry = _listeners.get(device_id)
    if entry is None or _expired(entry):
        return None
    return entry


def listeners_of(host: str | None) -> list[Listener]:
    """The phones listening to ``host``'s player now, in the order they
    joined."""
    if not host:
        return []
    now = time.monotonic()
    out = [e for e in _listeners.values() if e.host == host and e.live and not _expired(e, now)]
    return sorted(out, key=lambda e: (e.joined_at, e.device_id))


def listing(host: str | None) -> list[dict[str, str]]:
    """``listeners`` for the API: ``[{device_id, name}]``."""
    return [e.to_dict() for e in listeners_of(host)]


def hosts() -> set[str]:
    """Every room some phone listens to now."""
    return {e.host for e in _listeners.values() if e.live and not _expired(e)}


def full_for(device_id: str) -> bool:
    """No room for another phone: MAX_LISTENERS others are listening (a
    phone already listening may always move)."""
    now = time.monotonic()
    others = sum(1 for d, e in _listeners.items() if d != device_id and not _expired(e, now))
    return others >= MAX_LISTENERS


# ── the database mirror ──


def _session_scope():
    from domovoi.db.session import session_scope

    return session_scope()


async def _sync(device_ids: Iterable[str]) -> None:
    """Bring the mirror rows of ``device_ids`` in line with memory as it is
    NOW: a live entry is written, anything else deleted. Called after each
    change, never inside one, so a slow database never holds a change up
    and the last write always matches the last change."""
    ids = sorted(set(device_ids))
    if not PERSIST or not ids:
        return
    async with _db_lock:
        now = time.monotonic()
        keep = [e for d in ids if (e := _listeners.get(d)) is not None
                and e.live and not _expired(e, now)]
        drop = [d for d in ids if d not in {e.device_id for e in keep}]
        try:
            async with _session_scope() as s:
                if drop:
                    await s.execute(
                        text("DELETE FROM music_listeners WHERE device_id = ANY(:ids)"),
                        {"ids": drop},
                    )
                for e in keep:
                    await s.execute(
                        text("INSERT INTO music_listeners (device_id, host_room_id, joined_at) "
                             "VALUES (:d, :h, :at) ON CONFLICT (device_id) DO UPDATE SET "
                             "host_room_id = EXCLUDED.host_room_id, joined_at = EXCLUDED.joined_at"),
                        {"d": e.device_id, "h": e.host, "at": e.joined_at},
                    )
        except Exception as e:  # noqa: BLE001 — memory decides; the mirror is best-effort
            log.warning("listen along: couldn't mirror %d listener(s): %s", len(ids), e)


async def device_name(device_id: str) -> str:
    """The device's name from ``devices``, else :data:`DEFAULT_NAME`."""
    if not PERSIST:
        return DEFAULT_NAME
    try:
        async with _session_scope() as s:
            row = (await s.execute(
                text("SELECT name FROM devices WHERE device_id = :d"), {"d": device_id},
            )).first()
    except Exception as e:  # noqa: BLE001 — a name is a nicety
        log.debug("listen along: no name for device=%s: %s", device_id, e)
        return DEFAULT_NAME
    name = " ".join(str(row[0] or "").split()) if row is not None else ""
    return name or DEFAULT_NAME


async def load() -> None:
    """Read the listeners kept before a restart (the core's startup). Each
    gets EXPIRE_SEC from now to send a heartbeat; whoever doesn't is
    dropped by the next :func:`upkeep`."""
    from domovoi import music_groups

    try:
        async with _session_scope() as s:
            rows = (await s.execute(text(
                "SELECT l.device_id, l.host_room_id, l.joined_at, d.name "
                "FROM music_listeners l LEFT JOIN devices d ON d.device_id = l.device_id"
            ))).all()
    except Exception as e:  # noqa: BLE001 — nobody listens along this run
        log.warning("listen along: couldn't read listeners at startup: %s", e)
        return
    _listeners.clear()
    _stopped_since.clear()
    now = time.monotonic()
    changed: list[str] = []
    for device_id, host, joined_at, name in rows:
        if not device_id or not host:
            continue
        # A host that is a member of a shared queue now (it can't be after
        # a clean hand-off) hears that queue's host.
        hears = music_groups.host_of(host) or host
        _listeners[device_id] = Listener(
            device_id=device_id, host=hears, room_id=hears,
            joined_at=joined_at or datetime.now(timezone.utc), seen=now,
            name=" ".join(str(name or "").split()) or DEFAULT_NAME,
        )
        if hears != host:
            changed.append(device_id)
    await _sync(changed)
    log.info("listen along: %d listener(s) at startup", len(_listeners))


# ── changes ──


async def join(
    device_id: str, host: str, *, room_id: str, label: str | None = None,
    name: str | None = None, state: str = "play",
) -> Listener:
    """``device_id`` listens to ``host``'s player, joined from ``room_id``.
    A phone listening elsewhere moves (one room per phone)."""
    # Strictly after every listener already here: the cards list them in
    # join order, even for phones joined in the same instant (the clock's
    # step can be coarse).
    joined_at = datetime.now(timezone.utc)
    latest = max((e.joined_at for e in _listeners.values()), default=None)
    if latest is not None and joined_at <= latest:
        joined_at = latest + timedelta(microseconds=1)
    entry = Listener(
        device_id=device_id, host=host, room_id=room_id, label=label,
        joined_at=joined_at, seen=time.monotonic(),
        name=name or DEFAULT_NAME, state=state,
    )
    _listeners[device_id] = entry
    log.info("listen along: device=%s listens to room=%s (joined from %s)", device_id, host, room_id)
    await _sync([device_id])
    return entry


async def leave(device_id: str) -> Listener | None:
    """The phone stops listening; None when it wasn't (a stopped or expired
    entry is forgotten too). Nothing changes in any room."""
    entry = _listeners.pop(device_id, None)
    if entry is None:
        return None
    await _sync([device_id])
    if not entry.live or _expired(entry):
        return None
    log.info("listen along: device=%s left room=%s", device_id, entry.host)
    return entry


def touch(entry: Listener) -> None:
    """A heartbeat."""
    entry.seen = time.monotonic()


async def forget(device_id: str, entry: Listener | None = None) -> None:
    """Drop a phone's entry after its heartbeat was told it is over: only
    ``entry`` itself (a join that replaced it meanwhile stays), or, with no
    entry, only one that has expired."""
    current = _listeners.get(device_id)
    if current is None:
        return
    if entry is not None and current is not entry:
        return
    if entry is None and not _expired(current):
        return
    _listeners.pop(device_id, None)
    await _sync([device_id])


async def move_host(old: str, new: str, *, follow_room: bool = True) -> int:
    """``old``'s listeners hear ``new``'s player now: ``old`` handed its
    queue to ``new`` (music_groups._hand_off), or ``old``, playing alone,
    joined ``new``'s shared queue (``follow_room=False``: the room the
    phone joined from still hears the music). Returns how many moved."""
    if not old or not new or old == new:
        return 0
    moving = [e for e in _listeners.values() if e.host == old and e.live]
    for e in moving:
        e.host = new
        if follow_room and e.room_id == old:
            # The room it joined from plays nothing of this queue now.
            e.room_id, e.label = new, None
    _stopped_since.pop(old, None)
    if moving:
        log.info("listen along: %d listener(s) moved from room=%s to room=%s",
                 len(moving), old, new)
        await _sync(e.device_id for e in moving)
    return len(moving)


async def follow(entry: Listener) -> None:
    """Check ``entry``'s host again: a host that has become a member of a
    shared queue since (it joined one while this phone was joining it, or
    while the change missed it) hands its listeners to that queue's host."""
    from domovoi import music_groups

    hears = music_groups.host_of(entry.host) or entry.host
    if entry.live and hears != entry.host:
        await move_host(entry.host, hears, follow_room=False)


def _message(entry: Listener, reason: str) -> str:
    where = room_name(entry.room_id, entry.label)
    if reason == REASON_ENDED:
        return f"The music in {where} finished."
    return f"The music in {where} stopped."


async def host_stopped(host: str, reason: str) -> int:
    """``host``'s player stopped: its listeners are told ``reason`` on their
    next heartbeat (``stopped``: a person stopped it; ``ended``: its queue
    ran out). Their rows go now, so the cards stop showing them. Returns
    how many were marked."""
    marked = [e for e in _listeners.values() if e.host == host and e.live]
    for e in marked:
        e.reason = reason
        e.message = _message(e, reason)
    _stopped_since.pop(host, None)
    if marked:
        log.info("listen along: room=%s %s; %d listener(s) told", host, reason, len(marked))
        await _sync(e.device_id for e in marked)
    return len(marked)


async def note_stopped(room_id: str | None, response: Any = None) -> int:
    """A person's stop reached ``room_id`` (a voice reply's or a dispatched
    ``music_stop``, ``response`` the reply that carried it): when it is a
    host some phone listens to, those phones stop with it — ``ended`` when
    the stop was a skip past the queue's last song. A member room that left
    its group first ("stop here") hears itself only, and no phone listens
    to it."""
    if not room_id or not _listeners:
        return 0
    from domovoi import music_groups

    host = music_groups.host_of(room_id) or room_id
    if not listeners_of(host):
        return 0
    data = getattr(response, "data", None)
    ended = isinstance(data, dict) and bool(data.get("queue_ended"))
    return await host_stopped(host, REASON_ENDED if ended else REASON_STOPPED)


async def look(host: str, state: str | None) -> bool:
    """One read of ``host``'s player (the sweeper's, or a heartbeat's).
    Found stopped for STOPPED_GRACE_SEC, its listeners are told ``ended``;
    returns whether that happened. None (no answer) changes nothing."""
    if state is None:
        return False
    if state in ("play", "pause"):
        _stopped_since.pop(host, None)
        for e in _listeners.values():
            if e.host == host:
                e.state = state
        return False
    now = time.monotonic()
    since = _stopped_since.setdefault(host, now)
    if now - since < STOPPED_GRACE_SEC:
        return False
    return await host_stopped(host, REASON_ENDED) > 0


async def _state_of(host: str) -> str | None:
    from domovoi.clients.mpd import own_player

    try:
        client = own_player(host)
        if client is None:
            return None
        return await client.state()
    except Exception as e:  # noqa: BLE001 — unreachable: look again
        log.debug("listen along: host room=%s not answering: %s", host, e)
        return None


async def upkeep(app: Any = None) -> None:
    """One sweeper look (workers/playback_state_sweeper.py): listeners with
    no heartbeat for EXPIRE_SEC go, and a host whose player stopped by
    itself tells its listeners ``ended``."""
    now = time.monotonic()
    gone = [d for d, e in _listeners.items() if _expired(e, now)]
    for d in gone:
        _listeners.pop(d, None)
    if gone:
        log.info("listen along: %d listener(s) dropped (no heartbeat for %.0f s)",
                 len(gone), EXPIRE_SEC)
        await _sync(gone)
    for host in sorted(hosts()):
        await look(host, await _state_of(host))
    for host in list(_stopped_since):
        if host not in hosts():
            _stopped_since.pop(host, None)


__all__ = [
    "DEFAULT_NAME",
    "DEVICE_ID_RE",
    "EXPIRE_SEC",
    "HEARTBEAT_SEC",
    "MAX_LISTENERS",
    "Listener",
    "device_name",
    "follow",
    "forget",
    "full_for",
    "get",
    "host_stopped",
    "hosts",
    "join",
    "leave",
    "listeners_of",
    "listing",
    "load",
    "look",
    "move_host",
    "note_stopped",
    "reset",
    "room_name",
    "upkeep",
    "valid_device_id",
]
