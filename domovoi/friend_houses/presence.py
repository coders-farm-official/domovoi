"""Presence: whether a friend's house is online, and its newest address.

Friend Houses ("serverless messaging and friend system between households").
Owner decision 18: presence is only whether a friend's **house** is online
right now. Nothing about the people in it, its rooms, or what it is doing.

**Heard from.** Every connection that works, either way, counts: a friend
calling this house (the listener's ``connected`` and ``im_back`` events),
this house delivering a message, an accept, an "I'm back". Between those,
:func:`check_all` pings each friend not heard from for a while, every
:data:`CHECK_EVERY_SEC`. A house is **online** when it was heard from in
the last :data:`ONLINE_WITHIN_SEC`.

Friends already learn when a house is on from how fast it answers (finding
29), so showing it costs no extra privacy; the check adds one short Tor
connection per friend per 10 minutes.

**Newest address.** Every connection also carries the other house's signed
address statement. When it is newer than the one kept (issued later, signed
by the same house key), it replaces it (finding 4). That is how a friend
that moved to a new address after a block (:mod:`domovoi.friend_houses.blocking`)
is followed: its first connection to each friend brings the new address.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from domovoi.friend_houses import identity as fid

log = logging.getLogger(__name__)

ONLINE_WITHIN_SEC = 15 * 60
CHECK_EVERY_SEC = 10 * 60


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


async def saw(house_key: bytes, statement: bytes | fid.AddressStatement | None = None) -> None:
    """Record that ``house_key`` was just heard from, and take its address
    statement when it is newer than the one kept."""
    parsed = statement if isinstance(statement, fid.AddressStatement) else (
        fid.AddressStatement.parse(statement) if statement else None)
    moved = False
    async with _db() as s:
        row = (await s.execute(text(
            "SELECT id, statement_at, onion_key, session_key FROM friend_houses_friends "
            "WHERE house_key = :h AND state = 'active'"), {"h": house_key})).first()
        if row is None:
            return
        await s.execute(text(
            "UPDATE friend_houses_friends SET last_seen_at = now() WHERE id = :i"), {"i": row[0]})
        if parsed is not None and parsed.house_key == house_key and parsed.issued_at > int(row[1]):
            moved = bytes(row[2]) != parsed.onion_key
            if bytes(row[3]) != parsed.session_key:
                log.warning("Friend Houses: a friend house changed its connection key (signed by its house key)")
            await s.execute(text(
                "UPDATE friend_houses_friends SET onion_key = :o, session_key = :k, statement = :st, "
                "statement_at = :at WHERE id = :i"),
                {"o": parsed.onion_key, "k": parsed.session_key, "st": parsed.to_bytes(),
                 "at": parsed.issued_at, "i": row[0]})
    if moved:
        log.info("Friend Houses: a friend house moved to a new address; following it")
        _service().schedule_reconcile()  # Tor's key file names the onion


def online(last_seen_at: datetime | None, *, now: float | None = None) -> bool:
    if last_seen_at is None:
        return False
    current = now if now is not None else time.time()
    return current - last_seen_at.timestamp() <= ONLINE_WITHIN_SEC


def view(last_seen_at: datetime | None) -> dict[str, Any]:
    return {"online": online(last_seen_at),
            "last_seen_at": last_seen_at.isoformat() if last_seen_at else None}


async def check_all() -> int:
    """Ping every friend not heard from in :data:`CHECK_EVERY_SEC` (and tell
    any still waiting for this house's new address). Returns how many
    answered."""
    from domovoi.friend_houses import connect as fc
    from domovoi.friend_houses import session as fs
    from domovoi.friend_houses import store

    keys = fid.load()
    hello = await _service().my_hello()
    if keys is None or hello is None:
        return 0
    cutoff = datetime.fromtimestamp(time.time() - CHECK_EVERY_SEC + 30, tz=timezone.utc)
    async with _db() as s:
        friends = await store.active_friends(s)
        stale = {bytes(r[0]): bool(r[1]) for r in await s.execute(text(
            "SELECT house_key, announce_needed FROM friend_houses_friends WHERE state = 'active' "
            "AND (last_seen_at IS NULL OR last_seen_at < :c OR announce_needed)"), {"c": cutoff})}
    answered = 0
    for f in friends:
        if f.house_key not in stale:
            continue
        try:
            sess = await fc.open_session(_service().socks_path(), onion=fid.onion_address(f.onion_key),
                                         keys=keys, me=hello, friend=fs.Friend(f.house_key, f.session_key))
        except (fc.Unreachable, fs.SessionRefused):
            continue
        try:
            await sess.ping(timeout=45)
            answered += 1
        except fs.SessionRefused:
            continue
        finally:
            await sess.close()
        await saw(f.house_key, sess.peer.statement)
        if stale[f.house_key]:
            async with _db() as s:
                await s.execute(text(
                    "UPDATE friend_houses_friends SET announce_needed = FALSE WHERE house_key = :h"),
                    {"h": f.house_key})
    return answered
