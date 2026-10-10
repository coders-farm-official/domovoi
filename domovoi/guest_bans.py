"""Guest bans and the guests-seen record (V023) — shared by both processes.

A guest (no household credential) may change a playing room's queue and
use its pause / resume / skip / previous (``domovoi/guest_queue.py``)
"until they get banned by someone with admin privileges". This module is
the table work behind that sentence:

* :func:`record_guest` — the core notes every guest action it lets
  through: the guest's self-asserted device id, its source address, its
  user agent, the room and the action. An admin reads the list in
  Settings and bans from it.
* :func:`find_ban` — a ban matches the guest's device id OR its source
  address, on every room, for queue edits and transport alike. Both hops
  ask: the web hop so the dashboard gets the refusal before anything is
  proxied, the core because it is the authority (and a guest can call it
  directly).

**A guest ban is household policy, not a security boundary**, in exactly
the way a queue-device block is (``web/backend/api/music_queue.py``): a
device id is whatever the browser says (a cleared browser gets a new
one), and an address lasts as long as the DHCP lease. It keeps a known
phone out; it does not stop someone determined. The real gate is the
household device token, and the household switch turns guest access off
altogether. A ban is never consulted for a caller holding a household
credential.

Like ``admin_auth`` this module never imports the plugin runtime: the web
process imports it too.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from domovoi.db.session import session_scope

log = logging.getLogger(__name__)

# How long a guest stays in the "seen recently" list after its last action.
GUESTS_SEEN_RETENTION_DAYS = 30

# Caps on what a guest gets to write into the table. The device id and the
# user agent are the guest's own words; nothing here trusts them, but
# nothing here stores a novel either.
DEVICE_ID_MAX = 64
USER_AGENT_MAX = 300
ACTION_MAX = 40


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in str(value) if ch.isprintable()).strip()
    return cleaned[:limit] or None


def clean_device_id(value: str | None) -> str | None:
    """A guest's self-asserted device id as stored and matched: printable,
    trimmed, at most :data:`DEVICE_ID_MAX` characters; None when empty."""
    return _clip(value, DEVICE_ID_MAX)


async def find_ban(device_id: str | None, source: str | None) -> dict[str, Any] | None:
    """The ban covering this guest — by device id OR source address — or
    None. Raises when the database does not answer: the callers fail
    CLOSED for a guest (a ban that can't be checked is not a pass)."""
    device_id = clean_device_id(device_id)
    if not device_id and not source:
        return None
    async with session_scope() as s:
        row = (
            await s.execute(
                text(
                    """
                    SELECT id, device_id, source, note
                    FROM guest_bans
                    WHERE (device_id IS NOT NULL AND device_id = :device_id)
                       OR (source IS NOT NULL AND source = :source)
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"device_id": device_id, "source": source},
            )
        ).first()
    if row is None:
        return None
    return {"id": int(row[0]), "device_id": row[1], "source": row[2], "note": row[3]}


async def record_guest(
    *,
    device_id: str | None,
    source: str,
    user_agent: str | None,
    room_id: str | None,
    action: str,
) -> None:
    """Note one guest action (upsert on device id + address) and prune
    rows older than :data:`GUESTS_SEEN_RETENTION_DAYS`. Never raises: a
    record that fails is logged, and the guest's action goes ahead — this
    is the admin's view of who was here, not a gate."""
    try:
        async with session_scope() as s:
            await s.execute(
                text(
                    """
                    INSERT INTO guests_seen
                        (device_id, source, user_agent, room_id, last_action)
                    VALUES (:device_id, :source, :user_agent, :room_id, :action)
                    ON CONFLICT (device_id, source) DO UPDATE
                    SET user_agent  = COALESCE(EXCLUDED.user_agent, guests_seen.user_agent),
                        room_id     = EXCLUDED.room_id,
                        last_action = EXCLUDED.last_action,
                        last_seen   = now()
                    """
                ),
                {
                    "device_id": clean_device_id(device_id) or "",
                    "source": source,
                    "user_agent": _clip(user_agent, USER_AGENT_MAX),
                    "room_id": room_id,
                    "action": _clip(action, ACTION_MAX),
                },
            )
            await s.execute(
                text(
                    "DELETE FROM guests_seen WHERE last_seen < now() - "
                    f"interval '{GUESTS_SEEN_RETENTION_DAYS} days'"
                )
            )
    except Exception as e:  # noqa: BLE001 — bookkeeping, never a gate
        log.warning("could not record a guest action (%s in %s): %s", action, room_id, e)
