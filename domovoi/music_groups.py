"""Groups: rooms that share one music queue (1.3.0).

Two things live here (SHARED-QUEUES-PLAN.md; the owner's answers of
2026-11-14 decide every rule below):

* **Named groups** — a lasting set of rooms the household made and named
  ("upstairs", "the north wing"). Nothing plays because a group exists:
  "play jazz upstairs" starts a shared queue across its rooms. "Everywhere"
  is every room and needs no group.
* **The shared queue** — one room's music player, the HOST, heard in other
  rooms too, its MEMBERS: their satellites play the host's stream (model A,
  sync A1). Nothing new runs on the server; each room keeps its own MPD.

The rules:

* **Routing.** A member's music commands reach the host's player:
  ``clients.mpd.get_mpd_client_for`` and ``mpd_stream_url_for`` ask
  :func:`host_of`. So "play X" in a member room changes the music in every
  room of the group, a skip or a queue edit is the group's, and so are pause
  and resume (owner answers 1 and 4). A guest's skip or add reaches the
  whole group exactly when the guest's room plays the group's queue (answer
  3): the guest checks read the routed player. Workers that must see each
  player once (rescans, the listen logger, Up next's extender) use
  ``own_mpd_client`` / ``iter_mpd_clients``.
* **Joining** pauses the room's own player — its queue is kept, like a
  radio switched off — and points its satellite at the host's stream.
* **Stop** in a room stops only that room: it leaves the group (answer 1).
  When the host leaves while others listen, the queue is handed to the
  earliest member that joined: the same files from the same place, Up next
  moved along, the other members pointed at the new host's stream (a gap of
  a second or two for them). The last room left plays on alone and the
  shared queue ends.
* **Ends by itself** when the host's player stops (the queue ran out: the
  playback sweeper), and a member offline for 30 minutes leaves.
* **A core restart** keeps a shared queue that changed within two hours;
  older ones are ended.

State is in memory (this process routes every music command) and mirrored
to the database (V060: room_groups, room_group_rooms, shared_queues,
shared_queue_rooms) for the web process and for a restart. Database
trouble is logged, never fatal: the music plays on as decided.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text

log = logging.getLogger(__name__)

#: What a shared queue started with "everywhere" is called.
EVERYWHERE_NAME = "Everywhere"
#: A member whose satellite has been gone this long leaves.
OFFLINE_LEAVE_SEC = 30 * 60.0
#: At boot, a shared queue untouched this long is ended.
STALE_AT_BOOT_SEC = 2 * 3600.0
#: Ended shared queues are kept as history this many days.
ENDED_KEEP_DAYS = 30
#: Sweeper looks in a row that must find the host stopped before the shared
#: queue ends (a new song being loaded can read "stop" for a moment).
STOPPED_LOOKS_TO_END = 2

NAME_MAX = 40
#: Words a group's name may not hold: "play the jazz station" is the radio's,
#: and a group must never be taken for a station, a stream or a list.
RESERVED_NAME_WORDS = frozenset({
    "station", "stations", "radio", "fm", "am", "channel", "channels", "stream",
    "streams", "podcast", "podcasts", "playlist", "playlists", "queue", "song",
    "songs", "music", "album", "albums",
})


# ─── Named groups ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoomGroup:
    """A named, lasting set of rooms."""

    id: int
    name: str
    rooms: tuple[str, ...]

    @property
    def key(self) -> str:
        return name_key(self.name)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "rooms": list(self.rooms)}


_groups: dict[int, RoomGroup] = {}


def name_key(name: str) -> str:
    """How a group's name compares with spoken words (rooms.room_key)."""
    from domovoi.handlers.shared.rooms import room_key

    return room_key(name or "")


def groups() -> list[RoomGroup]:
    """Every named group, by name."""
    return sorted(_groups.values(), key=lambda g: g.name.lower())


def group(group_id: int | None) -> RoomGroup | None:
    return _groups.get(int(group_id)) if group_id is not None else None


def group_by_words(said: str) -> RoomGroup | None:
    """The named group ``said`` names ("upstairs", "the north wing"), or None."""
    key = name_key(said)
    if not key:
        return None
    for g in _groups.values():
        if g.key == key:
            return g
    return None


def group_phrases() -> list[tuple[str, ...]]:
    """Every group's name as words, longest first: places a command may end
    with and no preposition before them ("play jazz upstairs")."""
    import re

    out: set[tuple[str, ...]] = set()
    for g in _groups.values():
        words = tuple(re.findall(r"[a-z0-9']+", g.name.lower().replace("’", "'")))
        if words:
            out.add(words)
            if words[0] != "the":
                out.add(("the",) + words)
    return sorted(out, key=len, reverse=True)


class GroupNameError(ValueError):
    """A name a group can't have; the message says why, in plain words."""


def check_name(name: str, *, room_keys: Iterable[str] = (), exclude_id: int | None = None) -> str:
    """The name, tidied, or :class:`GroupNameError`. ``room_keys``: the
    house's rooms (rooms.room_key of each id and label): a group may not
    answer to a room's name."""
    from domovoi.handlers.shared import rooms as rooms_mod

    clean = " ".join(str(name or "").split())
    if not clean:
        raise GroupNameError("Give the group a name.")
    if len(clean) > NAME_MAX:
        raise GroupNameError(f"A group's name is at most {NAME_MAX} characters.")
    key = name_key(clean)
    if not key:
        raise GroupNameError("A group's name needs letters or numbers.")
    words = {w.lower().strip("'’") for w in clean.split()}
    if words & RESERVED_NAME_WORDS:
        raise GroupNameError(
            "A group's name can't use words like station, radio or playlist, "
            "or saying it would play something else."
        )
    already = rooms_mod.read_place(clean, [])
    if already is not None and already.kind in ("here", "everywhere"):
        raise GroupNameError("That name already means a place: pick another.")
    if key in set(room_keys):
        raise GroupNameError("A room is already called that: pick another name.")
    for g in _groups.values():
        if g.key == key and g.id != exclude_id:
            raise GroupNameError(f"There is already a group called {g.name}.")
    return clean


def _clean_rooms(rooms: Iterable[str]) -> tuple[str, ...]:
    from domovoi.models import is_phone_room

    out: list[str] = []
    for r in rooms or ():
        r = str(r or "").strip()
        if r and len(r) <= 64 and not is_phone_room(r) and r not in out:
            out.append(r)
    return tuple(sorted(out))


async def create_group(s: Any, name: str, rooms: Iterable[str], *, room_keys: Iterable[str] = ()) -> RoomGroup:
    clean = check_name(name, room_keys=room_keys)
    picked = _clean_rooms(rooms)
    if len(picked) < 2:
        raise GroupNameError("A group needs at least two rooms.")
    row = (await s.execute(
        text("INSERT INTO room_groups (name, name_key) VALUES (:n, :k) RETURNING id"),
        {"n": clean, "k": name_key(clean)},
    )).first()
    gid = int(row[0])
    for r in picked:
        await s.execute(
            text("INSERT INTO room_group_rooms (group_id, room_id) VALUES (:g, :r)"),
            {"g": gid, "r": r},
        )
    # Committed first: memory never holds a group the database doesn't.
    await s.commit()
    g = RoomGroup(id=gid, name=clean, rooms=picked)
    _groups[gid] = g
    return g


async def update_group(
    s: Any, group_id: int, *, name: str | None = None, rooms: Iterable[str] | None = None,
    room_keys: Iterable[str] = (),
) -> RoomGroup | None:
    g = _groups.get(int(group_id))
    if g is None:
        return None
    new_name = g.name if name is None else check_name(name, room_keys=room_keys, exclude_id=g.id)
    new_rooms = g.rooms if rooms is None else _clean_rooms(rooms)
    if len(new_rooms) < 2:
        raise GroupNameError("A group needs at least two rooms.")
    await s.execute(
        text("UPDATE room_groups SET name = :n, name_key = :k, updated_at = now() WHERE id = :g"),
        {"n": new_name, "k": name_key(new_name), "g": g.id},
    )
    if rooms is not None:
        await s.execute(text("DELETE FROM room_group_rooms WHERE group_id = :g"), {"g": g.id})
        for r in new_rooms:
            await s.execute(
                text("INSERT INTO room_group_rooms (group_id, room_id) VALUES (:g, :r)"),
                {"g": g.id, "r": r},
            )
    await s.commit()
    out = RoomGroup(id=g.id, name=new_name, rooms=new_rooms)
    _groups[g.id] = out
    return out


async def delete_group(s: Any, group_id: int) -> bool:
    g = _groups.get(int(group_id))
    if g is None:
        return False
    await s.execute(text("DELETE FROM room_groups WHERE id = :g"), {"g": g.id})
    # Committed first: a delete the database refused leaves the group named.
    await s.commit()
    _groups.pop(g.id, None)
    for sh in _shares.values():
        if sh.group_id == g.id:
            sh.group_id = None  # the queue plays on, without the name
    return True


# ─── Shared queues ────────────────────────────────────────────────────────


@dataclass
class Share:
    """A live shared queue: ``host``'s player heard in ``members`` too
    (in the order they joined)."""

    id: str
    host: str
    members: list[str] = field(default_factory=list)
    group_id: int | None = None
    everywhere: bool = False
    started_by: str | None = None
    #: monotonic: when each member's satellite was first seen gone.
    offline_since: dict[str, float] = field(default_factory=dict)
    #: When each member joined (wall clock, strictly increasing in join
    #: order): the hand-off order, kept across a restart.
    joined_at: dict[str, datetime] = field(default_factory=dict)
    #: sweeper looks in a row that found the host stopped.
    stopped_looks: int = 0

    @property
    def rooms(self) -> list[str]:
        return [self.host, *self.members]

    @property
    def name(self) -> str | None:
        g = _groups.get(self.group_id) if self.group_id is not None else None
        if g is not None:
            return g.name
        return EVERYWHERE_NAME if self.everywhere else None

    def to_dict(self) -> dict[str, Any]:
        """The ``group`` field of now-playing and the queue (API)."""
        return {
            "id": self.id,
            "name": self.name,
            "host_room": self.host,
            "rooms": self.rooms,
            "group_id": self.group_id,
            "everywhere": self.everywhere,
        }


_shares: dict[str, Share] = {}
_by_room: dict[str, str] = {}
_lock = asyncio.Lock()


def _joined_now(sh: Share) -> datetime:
    """Now, but after every member already in ``sh``: the join order is the
    hand-off order, even for rooms joined in the same instant."""
    now = datetime.now(timezone.utc)
    latest = max(sh.joined_at.values(), default=None)
    if latest is not None and now <= latest:
        now = latest + timedelta(microseconds=1)
    return now


def host_of(room_id: str | None) -> str | None:
    """The room whose music player ``room_id`` hears: its shared queue's
    host, else the room itself."""
    if not room_id:
        return room_id
    sid = _by_room.get(room_id)
    if sid is None:
        return room_id
    sh = _shares.get(sid)
    return sh.host if sh is not None else room_id


def share_of(room_id: str | None) -> Share | None:
    if not room_id:
        return None
    sid = _by_room.get(room_id)
    return _shares.get(sid) if sid is not None else None


def is_member(room_id: str | None) -> bool:
    """In a shared queue and not its host (its own player is waiting)."""
    sh = share_of(room_id)
    return sh is not None and sh.host != room_id


def shares() -> list[Share]:
    return list(_shares.values())


def describe(room_id: str | None) -> dict[str, Any] | None:
    """``room_id``'s shared queue for the API, or None."""
    sh = share_of(room_id)
    return sh.to_dict() if sh is not None else None


def reset(*, persist: bool = True) -> None:
    """Forget everything (tests)."""
    global _lock, PERSIST
    PERSIST = persist
    _lock = asyncio.Lock()
    _groups.clear()
    _shares.clear()
    _by_room.clear()


# ── the database mirror ──


def _session_scope():
    from domovoi.db.session import session_scope

    return session_scope()


#: Mirror changes to the database. The suite's conftest turns it off for
#: tests that don't ask for the database (they share one test database).
PERSIST = True


async def _persist(sh: Share, *, ended: bool = False) -> None:
    if not PERSIST:
        return
    try:
        async with _session_scope() as s:
            if ended:
                await s.execute(
                    text("UPDATE shared_queues SET ended_at = now(), updated_at = now() "
                         "WHERE id = :id AND ended_at IS NULL"),
                    {"id": sh.id},
                )
                await s.execute(text("DELETE FROM shared_queue_rooms WHERE queue_id = :id"), {"id": sh.id})
                return
            # One live shared queue per host: an older row left behind (a
            # restart that lost track of it) is ended.
            await s.execute(
                text("UPDATE shared_queues SET ended_at = now() WHERE host_room_id = :host "
                     "AND ended_at IS NULL AND id <> :id"),
                {"host": sh.host, "id": sh.id},
            )
            # A room hears one shared queue: whatever row it had elsewhere
            # goes, and so does this queue's row of a room that left. A
            # member's own row stays as it is (its joined_at is the
            # hand-off order).
            await s.execute(
                text("DELETE FROM shared_queue_rooms WHERE "
                     "(queue_id <> :id AND room_id = ANY(:rooms)) "
                     "OR (queue_id = :id AND NOT (room_id = ANY(:members)))"),
                {"id": sh.id, "rooms": sh.rooms, "members": list(sh.members)},
            )
            await s.execute(
                text(
                    "INSERT INTO shared_queues (id, host_room_id, group_id, everywhere, started_by_device) "
                    "VALUES (:id, :host, :g, :ev, :dev) "
                    "ON CONFLICT (id) DO UPDATE SET host_room_id = EXCLUDED.host_room_id, "
                    "group_id = EXCLUDED.group_id, everywhere = EXCLUDED.everywhere, updated_at = now()"
                ),
                {"id": sh.id, "host": sh.host, "g": sh.group_id, "ev": sh.everywhere,
                 "dev": sh.started_by},
            )
            for room in sh.members:
                await s.execute(
                    text("INSERT INTO shared_queue_rooms (queue_id, room_id, joined_at) "
                         "VALUES (:id, :r, :at) ON CONFLICT (queue_id, room_id) "
                         "DO UPDATE SET joined_at = EXCLUDED.joined_at"),
                    {"id": sh.id, "r": room, "at": sh.joined_at.get(room) or _joined_now(sh)},
                )
    except Exception as e:  # noqa: BLE001 — memory decides; the mirror is best-effort
        log.warning("groups: couldn't save shared queue %s: %s", sh.id, e)


async def load() -> None:
    """Read named groups and live shared queues at boot (the core's
    startup). A shared queue untouched for STALE_AT_BOOT_SEC is ended."""
    try:
        async with _session_scope() as s:
            await s.execute(text(
                "DELETE FROM shared_queues WHERE ended_at IS NOT NULL "
                f"AND ended_at < now() - interval '{ENDED_KEEP_DAYS} days'"
            ))
            await s.execute(text(
                "UPDATE shared_queues SET ended_at = now() WHERE ended_at IS NULL "
                f"AND updated_at < now() - interval '{int(STALE_AT_BOOT_SEC)} seconds'"
            ))
            await s.execute(text(
                "DELETE FROM shared_queue_rooms r USING shared_queues q "
                "WHERE r.queue_id = q.id AND q.ended_at IS NOT NULL"
            ))
            grows = (await s.execute(text(
                "SELECT g.id, g.name, coalesce(array_agg(r.room_id ORDER BY r.room_id) "
                "FILTER (WHERE r.room_id IS NOT NULL), '{}') "
                "FROM room_groups g LEFT JOIN room_group_rooms r ON r.group_id = g.id "
                "GROUP BY g.id, g.name"
            ))).all()
            qrows = (await s.execute(text(
                "SELECT q.id::text, q.host_room_id, q.group_id, q.everywhere, q.started_by_device, "
                "coalesce(array_agg(r.room_id ORDER BY r.joined_at, r.room_id) "
                "FILTER (WHERE r.room_id IS NOT NULL), '{}'), "
                "coalesce(array_agg(r.joined_at ORDER BY r.joined_at, r.room_id) "
                "FILTER (WHERE r.room_id IS NOT NULL), '{}') "
                "FROM shared_queues q LEFT JOIN shared_queue_rooms r ON r.queue_id = q.id "
                "WHERE q.ended_at IS NULL GROUP BY q.id"
            ))).all()
    except Exception as e:  # noqa: BLE001 — no groups this run; music still plays
        log.warning("groups: couldn't read groups at startup: %s", e)
        return
    _groups.clear()
    for gid, name, rooms in grows:
        _groups[int(gid)] = RoomGroup(id=int(gid), name=name, rooms=tuple(rooms or ()))
    _shares.clear()
    _by_room.clear()
    for sid, host, gid, everywhere, dev, members, joined in qrows:
        when = dict(zip(members or [], joined or []))
        members = [m for m in (members or []) if m != host and m not in _by_room]
        if not members or host in _by_room:
            await _persist(Share(id=sid, host=host), ended=True)
            continue
        sh = Share(id=sid, host=host, members=members, group_id=gid,
                   everywhere=bool(everywhere), started_by=dev,
                   joined_at={m: when[m] for m in members if when.get(m) is not None})
        _shares[sid] = sh
        for r in sh.rooms:
            _by_room[r] = sid
    log.info("groups: %d named group(s), %d shared queue(s) at startup", len(_groups), len(_shares))


# ── the satellites ──


def _point(app: Any, room_id: str, url: str | None) -> None:
    """Send ``room_id``'s satellite to ``url`` (the host's stream). Recorded
    as its resume intent first, so a satellite that is offline now joins on
    reconnect. Not a new play: a person's pause of the group holds."""
    if app is None or not url:
        return
    state = getattr(app, "state", None)
    resumable = getattr(state, "resumable_music", None)
    if resumable is not None:
        resumable[room_id] = url
    sess = (getattr(state, "active_sessions", None) or {}).get(room_id)
    if sess is None:
        return
    from domovoi.room_music import run_later

    async def _start() -> None:
        start = getattr(sess, "start_music", None)
        if start is not None:
            await start(url)

    run_later(_start(), name=f"group-join-{room_id}")


def _silence(app: Any, room_id: str) -> None:
    """Stop ``room_id``'s satellite (it was playing a host's stream that it
    no longer hears)."""
    if app is None:
        return
    from domovoi.room_music import deliver_later

    deliver_later(app, room_id, music_action="stop")


def _paused_rooms(app: Any) -> set[str] | None:
    """The raw set of rooms a person paused (domovoi/music_pause.py), by
    each room's own id — not redirected to a host."""
    from domovoi.music_pause import _rooms

    return _rooms(app, create=False)


def _forget_own_pause(app: Any, room_id: str) -> None:
    """A room joining a shared queue drops a pause of its own from before:
    while it is a member its flag would be the host's, and once it hosts
    after a hand-off a stale flag would keep every room silent."""
    rooms = _paused_rooms(app)
    if rooms is not None:
        rooms.discard(room_id)


def _forget_resume(app: Any, room_id: str) -> None:
    """A room that leaves stops meaning to play the host's stream: its
    resume link goes, so neither the next turn's auto-resume nor a ready
    signal brings back the group's music (or its own waiting queue)."""
    state = getattr(app, "state", None) if app is not None else None
    resumable = getattr(state, "resumable_music", None)
    if resumable is not None:
        resumable.pop(room_id, None)
    pending = getattr(state, "pending_music_start", None)
    if pending is not None:
        entry = pending.pop(room_id, None)
        task = entry.get("task") if isinstance(entry, dict) else None
        if task is not None and hasattr(task, "done") and not task.done():
            task.cancel()


def has_own_player(room_id: str | None) -> bool:
    """Whether ``room_id`` has a music player of its own (a room adopted
    but never connected has none: it can listen to a host, never be one)."""
    from domovoi.clients.mpd import own_player

    return own_player(room_id) is not None


async def _pause_own(room_id: str) -> None:
    """A joining room's own player waits, paused, its queue kept. A room
    with no player of its own has nothing to pause (never another room's)."""
    from domovoi.clients.mpd import own_player

    try:
        client = own_player(room_id)
        if client is None:
            return
        if await client.state() == "play":
            await client.pause()
    except Exception as e:  # noqa: BLE001 — a room without its own player
        log.debug("groups: room=%s own player not paused: %s", room_id, e)


def _url_for(host: str) -> str | None:
    from domovoi.clients.mpd import own_stream_url_for

    try:
        return own_stream_url_for(host)
    except Exception as e:  # noqa: BLE001
        log.warning("groups: no stream for host room=%s: %s", host, e)
        return None


def _move_stamp(old: str, new: str) -> None:
    """The now-playing source stamp follows the queue to its new host."""
    import dataclasses

    from domovoi.now_playing import NOW_PLAYING

    stamps = getattr(NOW_PLAYING, "_stamps", None)
    if not isinstance(stamps, dict):
        return
    st = stamps.pop(old, None)
    if st is not None:
        stamps[new] = dataclasses.replace(st, room_id=new)


@dataclass(frozen=True)
class _HandOff:
    """What a hand-off did: ``ok`` the new host took the queue (or there was
    nothing to take); ``session_id`` the old host's Up next session, for the
    caller to move once the new host is the host."""

    ok: bool
    session_id: str | None = None


async def _hand_off(app: Any, old: str, new: str) -> _HandOff:
    """``old``'s queue continues on ``new``'s player: the same files from
    the same place, paused (the members' music_ready resumes it), a
    person's pause kept and ``new``'s own stale one dropped. Only once
    ``new`` took at least one file does ``old``'s player stop and the
    now-playing stamp move; a refusal (no player of its own, every file
    refused, the player not answering) leaves ``old`` playing as it was
    and answers ``ok=False``."""
    from domovoi.clients.mpd import own_mpd_client, own_player

    src = own_mpd_client(old)
    dst = own_player(new)
    if dst is None:
        log.warning("groups: hand-off %s -> %s: %s has no player of its own", old, new, new)
        return _HandOff(ok=False)
    session_id: str | None = None
    try:
        snap = await src.playback_snapshot()
        entries = await src.queue_list()
    except Exception as e:  # noqa: BLE001
        log.warning("groups: hand-off %s -> %s: couldn't read %s's queue: %s", old, new, old, e)
        return _HandOff(ok=False)
    uris = [str(e.get("file") or "") for e in entries]
    state = snap.get("state") or "stop"
    paused = _paused_rooms(app)
    if any(uris) and state in ("play", "pause"):
        pos = snap.get("pos") or 0
        try:
            taken = await dst.prepare_files(
                uris, start_sec=float(snap.get("elapsed") or 0.0), start_index=int(pos),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("groups: hand-off %s -> %s failed: %s", old, new, e)
            return _HandOff(ok=False)
        if not taken:
            log.warning("groups: hand-off %s -> %s: %s's player took none of the queue",
                        old, new, new)
            return _HandOff(ok=False)
        try:
            from domovoi.up_next import store as up_next_store

            async with _session_scope() as s:
                st = await up_next_store.active_for(s, f"room:{old}")
            session_id = st.sess.id if st is not None else None
        except Exception as e:  # noqa: BLE001 — the music moved; the mix may not
            log.debug("groups: no Up next session read for room=%s: %s", old, e)
        _move_stamp(old, new)
    if paused is not None:
        was_paused = old in paused
        paused.discard(old)
        paused.discard(new)  # a pause of its own from before it joined
        if was_paused:
            paused.add(new)
    try:
        await src.stop()
    except Exception as e:  # noqa: BLE001
        log.debug("groups: %s's player didn't stop after the hand-off: %s", old, e)
    log.info("groups: shared queue handed from room=%s to room=%s", old, new)
    return _HandOff(ok=True, session_id=session_id)


# ── changes ──


async def share(
    app: Any,
    host: str,
    rooms: Iterable[str],
    *,
    group_id: int | None = None,
    everywhere: bool = False,
    started_by: str | None = None,
) -> Share | None:
    """``rooms`` hear ``host``'s music: a shared queue is started, or the
    one ``host`` hosts grows. A room in another shared queue leaves it
    first. None when no other room was given."""
    async with _lock:
        wanted = [r for r in dict.fromkeys(rooms) if r and r != host]
        cur = share_of(host)
        if cur is not None and cur.host != host:
            # The host was listening to someone else's: it plays its own now.
            await _leave_locked(app, host, silence=False)
        sh = share_of(host)
        if sh is None:
            if not wanted:
                return None
            sh = Share(id=str(uuid.uuid4()), host=host, started_by=started_by)
            _shares[sh.id] = sh
            _by_room[host] = sh.id
        if group_id is not None:
            sh.group_id = group_id
        if everywhere:
            sh.everywhere = True
        url = _url_for(host)
        joined: list[str] = []
        for room in wanted:
            if room in sh.members:
                joined.append(room)
                continue
            other = share_of(room)
            if other is not None:
                await _leave_locked(app, room, silence=False)
            await _pause_own(room)
            _forget_own_pause(app, room)
            sh.joined_at[room] = _joined_now(sh)
            sh.members.append(room)
            _by_room[room] = sh.id
            joined.append(room)
        await _persist(sh)
        for room in joined:
            _point(app, room, url)
        log.info("groups: room=%s shares its music with %s", host, ", ".join(sh.members))
        return sh


@dataclass(frozen=True)
class Left:
    """What :func:`leave` did: the shared queue it was in, whether ``room``
    hosted it, the room that hosts it now (None when it ended)."""

    share: Share
    was_host: bool
    new_host: str | None
    ended: bool


async def leave(app: Any, room_id: str, *, silence: bool = False) -> Left | None:
    """``room_id`` leaves its shared queue; None when it was in none.
    ``silence``: also stop its satellite (callers that answer with a
    ``music_stop`` of their own leave it False)."""
    async with _lock:
        return await _leave_locked(app, room_id, silence=silence)


async def _leave_locked(app: Any, room_id: str, *, silence: bool) -> Left | None:
    sh = share_of(room_id)
    if sh is None:
        return None
    if room_id != sh.host:
        sh.members.remove(room_id)
        sh.offline_since.pop(room_id, None)
        sh.joined_at.pop(room_id, None)
        _by_room.pop(room_id, None)
        _forget_resume(app, room_id)
        if silence:
            _silence(app, room_id)
        if not sh.members:
            await _end_locked(app, sh, silence_members=False)
            return Left(share=sh, was_host=False, new_host=None, ended=True)
        await _persist(sh)
        return Left(share=sh, was_host=False, new_host=sh.host, ended=False)
    if not sh.members:
        await _end_locked(app, sh, silence_members=False)
        return Left(share=sh, was_host=True, new_host=None, ended=True)
    old = sh.host
    # The earliest member that can take the queue hosts it now; one that
    # can't (no player of its own, refused every file) is passed over.
    new: str | None = None
    handed = _HandOff(ok=False)
    for candidate in list(sh.members):
        handed = await _hand_off(app, old, candidate)
        if handed.ok:
            new = candidate
            break
    if new is None:
        # Nobody could take it: the group ends cleanly — the members stop,
        # and so does the room that left (its player too).
        log.warning("groups: no room of %s's group could take its queue; it ends", old)
        await _end_locked(app, sh, silence_members=True)
        _forget_resume(app, old)
        if silence:
            _silence(app, old)
        try:
            from domovoi.clients.mpd import own_mpd_client

            await own_mpd_client(old).stop()
        except Exception as e:  # noqa: BLE001
            log.debug("groups: %s's player didn't stop: %s", old, e)
        return Left(share=sh, was_host=True, new_host=None, ended=True)
    session_id = handed.session_id
    sh.members.remove(new)
    sh.offline_since.pop(new, None)
    sh.joined_at.pop(new, None)
    sh.host = new
    _by_room.pop(old, None)
    _by_room[new] = sh.id
    _forget_resume(app, old)
    if session_id:
        from domovoi.clients.mpd import own_mpd_client
        from domovoi.up_next import rooms as up_next_rooms

        await up_next_rooms.move_here(new, session_id, own_mpd_client(new))
    if silence:
        _silence(app, old)
    url = _url_for(new)
    # The new host plays its own stream now; the others follow it there.
    for room in sh.rooms:
        _point(app, room, url)
    if not sh.members:
        await _end_locked(app, sh, silence_members=False)
        return Left(share=sh, was_host=True, new_host=new, ended=True)
    await _persist(sh)
    return Left(share=sh, was_host=True, new_host=new, ended=False)


async def end(app: Any, share_id: str, *, silence_members: bool = True) -> Share | None:
    """End a shared queue. ``silence_members``: the members' satellites stop
    (the host plays on, alone, unless the caller stops it)."""
    async with _lock:
        sh = _shares.get(share_id)
        if sh is None:
            return None
        await _end_locked(app, sh, silence_members=silence_members)
        return sh


async def _end_locked(app: Any, sh: Share, *, silence_members: bool) -> None:
    _shares.pop(sh.id, None)
    for room in sh.rooms:
        if _by_room.get(room) == sh.id:
            _by_room.pop(room, None)
    if silence_members:
        resumable = getattr(getattr(app, "state", None), "resumable_music", None)
        for room in sh.members:
            if resumable is not None:
                resumable.pop(room, None)
            _silence(app, room)
    await _persist(sh, ended=True)
    log.info("groups: shared queue of room=%s ended", sh.host)


async def keep_only(app: Any, room_id: str) -> Share | None:
    """Only ``room_id`` keeps the music ("just in here"): the other rooms of
    its shared queue stop, and the queue moves to ``room_id``'s own player
    when it was a member. None when it shared nothing."""
    async with _lock:
        sh = share_of(room_id)
        if sh is None:
            return None
        if sh.host == room_id:
            await _end_locked(app, sh, silence_members=True)
            return sh
        resumable = getattr(getattr(app, "state", None), "resumable_music", None)
        for other in [m for m in sh.members if m != room_id]:
            await _leave_locked(app, other, silence=True)
            if resumable is not None:
                resumable.pop(other, None)
        old = sh.host
        await _leave_locked(app, old, silence=True)  # hands the queue to room_id
        if resumable is not None:
            resumable.pop(old, None)
        return sh


async def end_all(app: Any, *, silence_members: bool = True) -> list[Share]:
    """End every shared queue ("stop everywhere")."""
    async with _lock:
        out = list(_shares.values())
        for sh in out:
            await _end_locked(app, sh, silence_members=silence_members)
        return out


async def upkeep(app: Any) -> None:
    """One sweeper look (workers/playback_state_sweeper.py): a shared queue
    whose host's player has stopped ends — its members' satellites stop —
    and a member offline for OFFLINE_LEAVE_SEC leaves."""
    from domovoi.clients.mpd import own_mpd_client

    sessions = getattr(getattr(app, "state", None), "active_sessions", None) or {}
    now = time.monotonic()
    for sh in list(_shares.values()):
        try:
            state = await own_mpd_client(sh.host).state()
        except Exception as e:  # noqa: BLE001 — unreachable: look again
            log.debug("groups: host room=%s not answering: %s", sh.host, e)
            state = None
        if state == "stop":
            sh.stopped_looks += 1
            if sh.stopped_looks >= STOPPED_LOOKS_TO_END:
                await end(app, sh.id, silence_members=True)
                continue
        elif state is not None:
            sh.stopped_looks = 0
        for room in list(sh.members):
            if room in sessions:
                sh.offline_since.pop(room, None)
                continue
            since = sh.offline_since.setdefault(room, now)
            if now - since >= OFFLINE_LEAVE_SEC:
                log.info("groups: room=%s offline for %.0f min; it leaves", room,
                         (now - since) / 60)
                await leave(app, room, silence=False)


__all__ = [
    "EVERYWHERE_NAME",
    "GroupNameError",
    "Left",
    "RoomGroup",
    "Share",
    "check_name",
    "create_group",
    "delete_group",
    "describe",
    "end",
    "end_all",
    "group",
    "group_by_words",
    "group_phrases",
    "groups",
    "host_of",
    "is_member",
    "keep_only",
    "leave",
    "load",
    "share",
    "share_of",
    "shares",
    "update_group",
    "upkeep",
]
