"""'Add a satellite to the Kitchen' (1.5.0, design §2).

The admin's "Add a satellite" in a room's drawer mints the spare-device
enrolment payload with a unique device name in ``room=`` ("Kitchen 2") and
the room's id in ``in=`` (``in=kitchen``). A device that reads ``in=`` (the
Pi and PC installers today, the phone apps from slice M) sends it in its
hello as ``requested_room``. An older one ignores the key and connects as
the id it makes from the name, ``kitchen_2``; so the server also remembers
the invitation here, and a device that parks for approval under that id
with no ``requested_room`` of its own gets the room from it. Either way
the approval card pre-selects the Kitchen, and a person still decides.

Kept in ``household_settings`` under :data:`KEY` (V045), so a restart
between the code and the device's first hello loses nothing. An
invitation lasts :data:`TTL_DAYS`; at most :data:`MAX_INVITES` are kept,
the oldest dropped first.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import text

KEY = "room_invites"
TTL_DAYS = 30
MAX_INVITES = 50


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _load(s: Any, now: dt.datetime) -> dict[str, dict[str, str]]:
    row = (await s.execute(text("SELECT value FROM household_settings WHERE key = :k"),
                           {"k": KEY})).first()
    value = row[0] if row is not None else None
    if isinstance(value, str):
        value = json.loads(value)
    items = value.get("invites") if isinstance(value, dict) else None
    out: dict[str, dict[str, str]] = {}
    cut = now - dt.timedelta(days=TTL_DAYS)
    for sid, inv in (items or {}).items():
        try:
            if isinstance(inv, dict) and dt.datetime.fromisoformat(inv["at"]) >= cut:
                out[sid] = {"room": str(inv["room"]), "at": str(inv["at"])}
        except (KeyError, ValueError):
            continue
    return out


async def _save(s: Any, items: dict[str, dict[str, str]]) -> None:
    keep = dict(sorted(items.items(), key=lambda kv: kv[1]["at"])[-MAX_INVITES:])
    await s.execute(text(
        "INSERT INTO household_settings (key, value, updated_at) "
        "VALUES (:k, CAST(:v AS jsonb), now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
    ), {"k": KEY, "v": json.dumps({"invites": keep})})


async def remember(s: Any, satellite_id: str, room_id: str, *,
                   now: dt.datetime | None = None) -> None:
    now = now or _now()
    items = await _load(s, now)
    items[satellite_id] = {"room": room_id, "at": now.isoformat(timespec="seconds")}
    await _save(s, items)


async def room_for(s: Any, satellite_id: str, *, now: dt.datetime | None = None) -> str | None:
    """The room ``satellite_id`` was invited into, if it was (and still
    exists)."""
    inv = (await _load(s, now or _now())).get(satellite_id)
    if inv is None:
        return None
    row = (await s.execute(text("SELECT 1 FROM rooms WHERE room_id = :r"),
                           {"r": inv["room"]})).first()
    return inv["room"] if row is not None else None


async def forget(s: Any, satellite_id: str) -> None:
    items = await _load(s, _now())
    if items.pop(satellite_id, None) is not None:
        await _save(s, items)


async def _taken(s: Any, sid: str) -> bool:
    return (await s.execute(text(
        "SELECT 1 FROM rooms WHERE room_id = :r "
        "UNION ALL SELECT 1 FROM satellites WHERE satellite_id = :r "
        "UNION ALL SELECT 1 FROM satellite_pairings WHERE satellite_id = :r "
        "UNION ALL SELECT 1 FROM satellite_approvals WHERE satellite_id = :r "
        "UNION ALL SELECT 1 FROM mpd_rooms WHERE room_id = :r LIMIT 1"
    ), {"r": sid})).first() is not None


async def invite(s: Any, room_id: str, room_name: str, *,
                 name: str | None = None) -> dict[str, str]:
    """A unique device name for a new satellite of ``room_id`` and the id
    a device makes from it (``satellite.enrolment.room_id_for``), and
    remember the invitation. ``name``: reuse this one (the same code asked
    for again, e.g. for the USB stick) when its id is still free or was
    invited into this same room."""
    from satellite import enrolment

    now = _now()
    items = await _load(s, now)
    if name:
        try:
            sid = enrolment.room_id_for(name.strip())
        except enrolment.EnrolmentError:
            sid = None
        if sid is not None and (
                (items.get(sid) or {}).get("room") == room_id or not await _taken(s, sid)):
            await remember(s, sid, room_id, now=now)
            return {"name": name.strip(), "satellite_id": sid}
    base = room_name.strip()
    try:
        enrolment.room_id_for(base)
    except enrolment.EnrolmentError:
        base = room_id                    # a name with no Latin letter: the id reads fine
    n = 2
    while True:
        suffix = f" {n}"
        cand = base[:enrolment.MAX_ROOM_NAME_LEN - len(suffix)].rstrip() + suffix
        try:
            sid = enrolment.room_id_for(cand)
        except enrolment.EnrolmentError:
            # Too long an id: shorten the base until it fits.
            base = base[:max(1, len(base) - 4)]
            continue
        if sid not in items and not await _taken(s, sid):
            await remember(s, sid, room_id, now=now)
            return {"name": cand, "satellite_id": sid}
        n += 1
