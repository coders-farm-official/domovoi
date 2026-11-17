"""Blocking a friend house: end it for good, and move to a new address.

Friend Houses ("serverless messaging and friend system between households").
**Remove** (friending.unfriend) already stops a house reaching this one: the
listener refuses it from its next connection and Tor's key for it goes at
the next reload. **Block** goes further:

1. **Keep or save first.** The conversation stays (marked "no longer a
   friend") unless the admin chooses to delete it; the dashboard offers a
   saved copy with the signed proof beforehand (finding 31).
2. **Refused for good.** The house goes on a blocked list. A friend request
   (from a new friend file) or an accept from it is dropped without a word,
   and this house won't send it one, until an admin unblocks it.
3. **A new address.** This house's onion is deleted and Tor makes a new one.
   The blocked house is left with an address that no longer exists, so it
   can't even knock (and can't keep narrowing down this house's Tor guard,
   finding 25). The remaining friends learn the new address from the signed
   statement this house sends them on its next connection: at once with an
   "I'm back" to each, and on every retry pass until each is reached
   (presence.check_all). A friend that is offline meanwhile can't reach this
   house until then; this house reaches it, not the other way round.

Moving costs everyone a few minutes: Tor restarts and publishes the new
address (spike S1), so messages either way wait in the outboxes meanwhile.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from domovoi.friend_houses import identity as fid

log = logging.getLogger(__name__)


class BlockError(Exception):
    """Something a person can fix; the message says how."""


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


async def is_blocked(house_key: bytes) -> bool:
    async with _db() as s:
        return bool((await s.execute(text(
            "SELECT 1 FROM friend_houses_blocked WHERE house_key = :h"), {"h": house_key})).first())


async def block(friend_id: str, *, keep_messages: bool = True, new_address: bool = True) -> dict[str, Any]:
    from domovoi.friend_houses import messages, store

    async with _db() as s:
        friend = await store.friend(s, friend_id)
        if friend is None:
            raise BlockError("No such friend house.")
        await s.execute(text(
            "INSERT INTO friend_houses_blocked (house_key, name) VALUES (:h, :n) "
            "ON CONFLICT (house_key) DO NOTHING"), {"h": friend.house_key, "n": friend.local_name})
        await s.execute(text("DELETE FROM friend_houses_requests WHERE house_key = :h"),
                        {"h": friend.house_key})
        await store.delete_friend(s, friend_id)
        if new_address:
            await s.execute(text(
                "UPDATE friend_houses_friends SET announce_needed = TRUE WHERE state = 'active'"))
    deleted = 0 if keep_messages else await messages.delete_conversation(friend.house_key)
    log.warning("Friend Houses: a friend house was blocked%s",
                "; moving this house to a new address" if new_address else "")
    moved = await _service().move_to_new_address() if new_address else False
    if not moved:
        await _service().reconcile()
    return {"blocked": True, "new_address": moved, "messages_deleted": deleted}


async def unblock(house_hex: str) -> bool:
    try:
        house = bytes.fromhex(house_hex)
    except ValueError:
        return False
    async with _db() as s:
        res = await s.execute(text("DELETE FROM friend_houses_blocked WHERE house_key = :h"),
                              {"h": house})
    return bool(res.rowcount)


async def blocked() -> list[dict[str, Any]]:
    async with _db() as s:
        rows = await s.execute(text(
            "SELECT house_key, name, blocked_at FROM friend_houses_blocked ORDER BY blocked_at"))
        return [{"house": bytes(r[0]).hex(), "name": r[1], "fingerprint": fid.fingerprint(bytes(r[0])),
                 "blocked_at": r[2].isoformat()} for r in rows]
