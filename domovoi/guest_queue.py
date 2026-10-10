"""Guests on a playing room — the core's half.

Owner decisions, 2026-10-10: "Guests should be able to edit queues that
rooms are actively playing", and they get the transport controls of such
a room too, "until they get banned by someone with admin privileges".

A caller with NO household credential (no ``X-Device-Token``, no admin
session) may, for a room that is **actively playing** — MPD state
``play`` or ``pause`` with a current song:

* read that room's queue, add to it, remove entries from it and move
  entries in it;
* pause, resume, skip and go back (``/v1/admin/music/{action}/{room_id}``
  with an action in :data:`GUEST_TRANSPORT_ACTIONS`).

Not clear and not stop (each ends the room's session), not removing the
entry playing now (a skip by the back door — the skip button is right
there), and nothing at all on an idle room. A guest an admin banned
(``domovoi/guest_bans.py``: by device id or source address, every room)
may not act at all, and a guest's actions are throttled per address.

The core is AUTHORITATIVE: it is the only process that can see MPD, so it
decides "playing" at the moment of each request, and it checks the ban
itself because a guest can call it directly. The web hop passes a guest
through to it (``web/backend/api/music_queue.py``) and never lends it a
credential.

What this module promises:

* **"Active" never starts anything.** :func:`room_activity` looks at a
  room's player only if it is already running: a room missing from the
  live port map (``clients.mpd._room_ports``) has no player and is simply
  not active. It never calls ``_ensure_room_mpd`` / ``ensure_room`` or
  anything else that provisions or starts a container, and the queue
  routes skip their own ``_ensure_room_mpd`` for a guest
  (:func:`guest_activity`).
* **Household callers are untouched.** Every gate here runs
  :func:`admin_auth.check_queue_guest` first, which judges any request
  carrying a credential by exactly the gate the route wore before
  (``require_device`` / ``require_device_read``). A ban, the throttle and
  the activity check apply to guests only.
* **One dependency per route.** Opening another room route to guests is
  one line: ``dependencies=[Depends(require_device_or_active_room_guest)]``
  on a route whose path names ``{room_id}``.

The household switch is ``settings.guest_queue_editing`` (default on),
read per request, so a change in Settings applies at once. Off = the
routes answer exactly as they did before this module existed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from domovoi import admin_auth, guest_bans
from domovoi.config import settings

log = logging.getLogger(__name__)

# MPD states that mean a listening session is in progress. ``stop`` (or a
# player that does not answer) is idle.
ACTIVE_STATES = ("play", "pause")

# The transport actions a guest may take on a playing room. ``next`` is
# the route's alias of ``skip``. Not ``stop``: like clearing the queue it
# ends the room's session, and that stays the household's.
GUEST_TRANSPORT_ACTIONS = frozenset({"pause", "resume", "skip", "next", "previous"})

# How long the activity check waits on a room's player. A container that
# is up answers in milliseconds; one that is wedged must not hold a
# guest's request (or a worker) open.
ACTIVITY_TIMEOUT_SEC = 2.0

# Where the gate leaves its finding for the handler (request.state).
_STATE_ATTR = "domovoi_queue_guest"


@dataclass(frozen=True)
class RoomActivity:
    """What the gate learned about the room for a guest. ``song_id`` is
    the MPD songid of the entry playing now, when MPD named one."""

    active: bool
    song_id: int | None = None


INACTIVE = RoomActivity(active=False)


def _running_player(room_id: str) -> Any | None:
    """The client for ``room_id``'s player if — and only if — this
    process already has one: never a fallback to another room (which is
    what ``get_mpd_client_for`` does for an unknown id) and never a new
    provisioning. Building a ``RealMPDClient`` for a known port opens no
    connection and starts nothing."""
    from domovoi.clients import mpd as mpd_module

    if settings.use_stubs:
        # Stub mode: a room "has a player" when a test (or USE_STUBS
        # dev) seeded one; get_mpd_client_for would invent a fresh stub.
        return mpd_module._clients.get(room_id)
    if room_id not in mpd_module._room_ports:
        return None
    return mpd_module.get_mpd_client_for(room_id)


async def room_activity(room_id: str) -> RoomActivity:
    """Whether ``room_id`` is actively playing: MPD ``play`` or ``pause``
    with a current song. A room with no running player, a player that
    refuses the connection or does not answer within
    :data:`ACTIVITY_TIMEOUT_SEC`, ``stop``, or no current song: not
    active. Reads ``status`` and ``currentsong`` only."""
    player = _running_player(room_id)
    if player is None:
        return INACTIVE
    try:
        state = await asyncio.wait_for(player.state(), ACTIVITY_TIMEOUT_SEC)
        if state not in ACTIVE_STATES:
            return INACTIVE
        song = await asyncio.wait_for(player.current_song(), ACTIVITY_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001 — a player that can't say is not playing
        log.debug("guest: %s activity check failed: %s", room_id, e)
        return INACTIVE
    if not song:
        return INACTIVE
    raw = song.get("id") if song.get("id") is not None else song.get("Id")
    try:
        song_id = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        song_id = None
    return RoomActivity(active=True, song_id=song_id)


def _refused_as_before() -> HTTPException:
    """What the device tier says to a request carrying nothing — the answer
    a guest gets for anything that is not a guest's to do."""
    return HTTPException(
        status_code=401,
        detail=f"{admin_auth.DEVICE_TOKEN_HEADER} or admin session required",
    )


async def assert_guest_not_banned(request: Request) -> None:
    """403 when an admin banned this guest (by the device id it sends, or
    by its address). Fails CLOSED: a ban that can't be checked is a 503
    for the guest, never a pass."""
    try:
        ban = await guest_bans.find_ban(
            admin_auth.guest_device_id(request), admin_auth.request_source(request)
        )
    except Exception as e:  # noqa: BLE001 — the DB did not answer
        log.warning("guest ban check failed: %s", e)
        raise HTTPException(
            status_code=503, detail="couldn't check guest access right now"
        ) from e
    if ban is not None:
        raise HTTPException(status_code=403, detail=admin_auth.GUEST_BANNED_DETAIL)


def _action_label(request: Request, *, read: bool) -> str:
    action = request.path_params.get("action")
    if action:
        return str(action)
    if read:
        return "queue read"
    return "queue " + request.url.path.rstrip("/").rsplit("/", 1)[-1]


async def _gate(request: Request, room_id: str, *, read: bool) -> None:
    guest = await admin_auth.check_queue_guest(
        request, read=read, guests_allowed=bool(settings.guest_queue_editing)
    )
    if not guest:
        return
    action = request.path_params.get("action")
    if action is not None and action not in GUEST_TRANSPORT_ACTIONS:
        # stop (and anything that isn't a transport verb): the household's.
        raise _refused_as_before()
    if not read:
        # Before the database and the MPD look: an idle room hammered by
        # a script costs the script its budget, not the room a status read.
        admin_auth.charge_guest_queue_edit(request)
        await assert_guest_not_banned(request)
    activity = await room_activity(room_id)
    if not activity.active:
        raise HTTPException(status_code=403, detail=admin_auth.GUEST_ROOM_IDLE_DETAIL)
    setattr(request.state, _STATE_ATTR, activity)
    if not read:
        await guest_bans.record_guest(
            device_id=admin_auth.guest_device_id(request),
            source=admin_auth.request_source(request),
            user_agent=admin_auth.request_user_agent(request),
            room_id=room_id,
            action=_action_label(request, read=read),
        )


async def require_device_or_active_room_guest(request: Request, room_id: str) -> None:
    """Dependency for a guest-capable room ACTION — a queue edit (add /
    remove / move) or a transport verb: the device tier, exactly as
    before, or a guest while ``room_id`` is playing, not banned, within
    its per-address budget. On the transport route only the verbs in
    :data:`GUEST_TRANSPORT_ACTIONS` are a guest's."""
    await _gate(request, room_id, read=False)


async def require_device_read_or_active_room_guest(request: Request, room_id: str) -> None:
    """Dependency for the room-queue READ: the device tier's read half,
    exactly as before, or a guest while ``room_id`` is playing. A banned
    guest may still read (it is told so by the web hop); the ban is about
    changing the music."""
    await _gate(request, room_id, read=True)


def guest_activity(request: Request) -> RoomActivity | None:
    """The gate's finding when THIS request is a guest's (always an
    active room, by construction), or None for a household caller. A
    handler that would start or provision a room's player checks this
    first and does not, for a guest."""
    return getattr(request.state, _STATE_ATTR, None)
