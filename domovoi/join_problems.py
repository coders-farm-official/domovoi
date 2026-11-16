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


# ─── the lines themselves: one wording, used everywhere ───────────────────

def addr_refused(addr: str) -> None:
    note("addr_refused", addr,
         f"A room tried to join at {addr}, but this server couldn't prove it's "
         "the one at that address. Add that address to the server's trusted "
         "addresses (TRUSTED_HOSTS), or set the room up with the server's own "
         "address.")


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
