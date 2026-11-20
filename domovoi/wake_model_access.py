"""Who may download a wake model this household trained (CORE-43, round 6).

The ``/v1/wake-models`` channel carries two kinds of file. The published
words ("Domovoi", openWakeWord's stock words) are public: anyone can
download them from where they are published. A word trained HERE — every
custom word, and "Domovoi (tuned to this home)" above all — was shaped by
the household's own recordings, and the security notes promise it is
served only to this server's own rooms. The routes used to be open to any
LAN host, so a guest phone could read the manifest and take the tuned
model away: a classifier shaped by the household's voices, usable offline
to craft audio that fires the house's wake word.

A household-trained model body is now served only to:

* an address a room that passed pairing has connected from (its current
  session, or one this process saw in the last :data:`ROOM_PEER_TTL_SEC`):
  every Pi, PC and phone room fetches its models from the same address its
  room socket comes from, and none of them sends a credential on these
  fetches, so the address is what ties the download to a room;
* a household device or an admin (the device tier: ``X-Device-Token`` or
  an admin Bearer), and anyone before first-run setup, as on every other
  device-tier route.

What stays open: the published models, the manifest and its signed form
(names and digests only: a room checks its cache against them before it
has a session, and a cached model needs no download). The channel is plain
HTTP on the LAN, so a host that can already read the room's traffic can
still read a download; this closes the open door, not the wire.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

#: How long an address a paired room connected from stays allowed after its
#: session ends. A phone room checks its models before it reconnects; a
#: re-trained word it missed while away needs this.
ROOM_PEER_TTL_SEC = 30 * 24 * 3600.0
#: At most this many remembered addresses (oldest dropped first).
ROOM_PEER_MAX = 256

_ROOM_PEERS: OrderedDict[str, float] = OrderedDict()


def _host(conn: Any) -> str | None:
    client = getattr(conn, "client", None)
    host = getattr(client, "host", None)
    return str(host) if host else None


def remember_room_peer(ws: Any) -> None:
    """Note the address of a room socket that has just passed pairing."""
    host = _host(ws)
    if host is None:
        return
    _ROOM_PEERS[host] = time.monotonic()
    _ROOM_PEERS.move_to_end(host)
    while len(_ROOM_PEERS) > ROOM_PEER_MAX:
        _ROOM_PEERS.popitem(last=False)


def forget_room_peers() -> None:
    """Tests: start with no remembered rooms."""
    _ROOM_PEERS.clear()


def is_room_peer(host: str | None, active_sessions: dict[str, Any] | None = None) -> bool:
    """Whether ``host`` is the address of a connected room, or of one that
    passed pairing within :data:`ROOM_PEER_TTL_SEC`."""
    if not host:
        return False
    for sess in (active_sessions or {}).values():
        if _host(getattr(sess, "ws", None)) == host:
            return True
    seen = _ROOM_PEERS.get(host)
    return seen is not None and time.monotonic() - seen <= ROOM_PEER_TTL_SEC


def is_public_model(rel: str) -> bool:
    """A published word's file (and its companion), which anyone may have."""
    from domovoi import wake_default

    name = rel.rsplit("/", 1)[-1]
    for word in wake_default.PUBLISHED.values():
        if name == word.file or name == f"{word.file}.json":
            return True
    return False


async def may_download(request: Any, rel: str) -> bool:
    """The gate for one model body: see the module docstring."""
    if is_public_model(rel):
        return True
    sessions = getattr(getattr(getattr(request, "app", None), "state", None), "active_sessions", None)
    if is_room_peer(_host(request), sessions):
        return True
    from domovoi import admin_auth

    try:
        result = await admin_auth.check_device_request(request)
    except admin_auth.AuthCheckUnavailable:
        return False
    return result in ("ok", "admin", "pre-setup")
