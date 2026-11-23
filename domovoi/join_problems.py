"""Why a room could not join — said where an admin looks (Settings → Rooms).

When a room device cannot join for a reason only the server knows (it
would not sign its identity for the address the device dialled, the room
is already paired with another device, too many requests from one
address, ...), the device sits on its red or violet ring while the server
logs the reason in a journal nobody reads. This keeps the newest few of
those reasons in memory, one plain line each, for the dashboard.

Bounded on purpose, because most of these are reached by unauthenticated
traffic: at most :data:`MAX_KEPT` lines (the newest), one line per kind
and subject (a repeat bumps its count and time rather than adding a
line), a line touched at most once per :data:`REPEAT_SEC`, and lines
older than :data:`MAX_AGE_SEC` are dropped. In memory: a restart clears
them, and the next attempt says it again.
"""

from __future__ import annotations

import threading
import time
from typing import Any

MAX_KEPT = 5
REPEAT_SEC = 30.0
MAX_AGE_SEC = 24 * 3600.0
_TEXT_MAX = 300

_clock = time.time
_lock = threading.Lock()
_items: list[dict[str, Any]] = []


def note(kind: str, subject: str, text: str) -> None:
    """Record one reason. ``subject`` (an address, a room) groups repeats.
    Never raises: this is said on paths that must not break."""
    try:
        now = _clock()
        key = (str(kind)[:40], str(subject)[:120])
        with _lock:
            for i, item in enumerate(_items):
                if (item["kind"], item["subject"]) == key:
                    if now - item["at"] < REPEAT_SEC:
                        item["count"] += 1
                        return
                    _items.pop(i)
                    item.update(at=now, text=str(text)[:_TEXT_MAX], count=item["count"] + 1)
                    _items.insert(0, item)
                    return
            _items.insert(0, {"kind": key[0], "subject": key[1],
                              "text": str(text)[:_TEXT_MAX], "at": now, "count": 1})
            del _items[MAX_KEPT:]
    except Exception:  # noqa: BLE001
        pass


def recent() -> list[dict[str, Any]]:
    """The kept lines, newest first: ``{kind, subject, text, at, count}``
    (``at`` in epoch seconds)."""
    now = _clock()
    with _lock:
        _items[:] = [i for i in _items if now - i["at"] <= MAX_AGE_SEC]
        return [dict(i) for i in _items]


def clear() -> None:
    with _lock:
        _items.clear()
        _probes.clear()
        _streams.clear()


# ─── a room set up for a different server (S3-4) ──────────────────────────
#
# A room device holds its server to the key it was set up with, and checks
# it before every connect (GET /v1/health?challenge=…&addr=…). Pinned to
# another house, or to this one before it was reinstalled with a new key,
# it gets a valid answer from THIS server, sees a different key, and never
# opens a session: the server never hears its room's name, so it can't be
# listed with the rooms. What the server does see is the pattern: an
# address that keeps asking for the proof and never opens a stream. That
# is said here as one more line. Nothing is decided on it — it is text for
# an admin, and the device trusts this server no more than before.

#: An address that asked for the proof and opened no stream for this long.
#: A device whose server this is connects within seconds of its check; a
#: refusing one asks again about once a minute (its reconnect backoff).
OTHER_SERVER_AFTER_SEC = 150.0
#: An address that has not asked for this long starts over.
_PROBE_FORGET_SEC = 900.0
_PROBES_MAX = 64
_probes: dict[str, tuple[float, float]] = {}   # address -> (first, last)
_streams: dict[str, int] = {}                   # address -> open room sockets


def identity_checked(source: str) -> None:
    """A room device at ``source`` had this server prove its identity for
    the address it dialled. Never raises."""
    try:
        now = _clock()
        source = str(source)[:120]
        with _lock:
            if _streams.get(source):
                # A device already talking to this server (the clock helper,
                # a second check): not one that refuses it.
                _probes.pop(source, None)
                return
            first, last = _probes.get(source, (now, now))
            if now - last > _PROBE_FORGET_SEC:
                first = now
            if source not in _probes and len(_probes) >= _PROBES_MAX:
                del _probes[min(_probes, key=lambda k: _probes[k][1])]
            _probes[source] = (first, now)
            if now - first < OTHER_SERVER_AFTER_SEC:
                return
        set_up_for_another_server(source)
    except Exception:  # noqa: BLE001
        pass


def stream_opened(source: str) -> None:
    """A room socket opened from ``source``: whatever is there talks to this
    server, so it is not one that refuses it. Never raises."""
    try:
        source = str(source)[:120]
        with _lock:
            _streams[source] = _streams.get(source, 0) + 1
            _probes.pop(source, None)
            _items[:] = [i for i in _items
                         if (i["kind"], i["subject"]) != ("other_server", source)]
    except Exception:  # noqa: BLE001
        pass


def stream_closed(source: str) -> None:
    try:
        source = str(source)[:120]
        with _lock:
            left = _streams.get(source, 0) - 1
            if left > 0:
                _streams[source] = left
            else:
                _streams.pop(source, None)
    except Exception:  # noqa: BLE001
        pass


# ─── the lines themselves: one wording, used everywhere ───────────────────

def addr_refused(addr: str) -> None:
    note("addr_refused", addr,
         f"A room tried to join at {addr}, but this server couldn't prove it's "
         "the one at that address. If nobody set a room up with it, don't trust "
         "it: something may be relaying a room's questions. If you did, add it "
         "to TRUSTED_HOSTS, or use the server's own address.")


def paired_elsewhere(room_id: str) -> None:
    note("paired_elsewhere", room_id,
         f"A device asked to join as {room_id}, but {room_id} is paired with a "
         "different device. If you replaced or set up that room again, reset "
         "its pairing in the room's drawer.")


def no_key(room_id: str) -> None:
    note("no_key", room_id,
         f"A device without a pairing key asked to join as {room_id}. Set it "
         "up again so it gets one.")


def name_taken(room_id: str) -> None:
    note("name_taken", room_id,
         f"Two devices are asking to join as {room_id}. Reject the one you "
         "don't want below, or wait a few minutes and the newest one takes "
         "its place.")


def throttled(source: str) -> None:
    note("throttled", source,
         f"Too many join requests came from {source}, so this server is "
         "ignoring it for a few minutes.")


def bad_code(room_id: str) -> None:
    note("bad_code", room_id,
         f"A device asked to join as {room_id} with a code no Domovoi room "
         "device uses. Set it up again to get a new one.")


def set_up_for_another_server(source: str) -> None:
    note("other_server", source,
         f"A room device at {source} checks this server's identity but never "
         "joins: it was set up for a different Domovoi server (another house, "
         "or this one before a reinstall) and trusts only that one. If it's "
         "yours, set it up again for this server.")
