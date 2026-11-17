"""Messages between friend houses: the outbox, the inbox and the alerts.

Friend Houses ("serverless messaging and friend system between households"),
phase F3. One inbox for the whole house (owner decision 16).

**Sending.** :func:`send` signs the text into an envelope
(:mod:`domovoi.friend_houses.envelope`), queues it, and tries to deliver at
once. A message leaves the outbox only when the friend acknowledges that it
stored it. Undelivered messages are retried every few minutes, at once when
the friend says "I'm back" (an ``IM_BACK`` frame), and fail after
:data:`OUTBOX_DAYS` days, which the sender sees as "Not delivered"
(finding 32: never silent loss).

**Receiving.** The listener process checks the envelope (size, signature,
"from this friend to this house") and asks core to store it. :func:`on_message`
checks the friend is still a friend, de-duplicates on the message id
(finding 19), applies the per-friend quota, stores, and only then tells the
listener to acknowledge.

**Alerts** (owner decision 15): per friend, least intrusive by default.

=========  ===============================================================
``inbox``  nothing but the inbox (the default after accepting)
``sender`` the rooms hear "A message from <name>."
``read``   the rooms hear the name and the message
=========  ===============================================================

Read aloud, a friend's text can't be used against the house (finding 6):
wake words are taken out so a speaker in the next room can't be woken and
commanded, it is length-capped (finding 30), control and bidi characters
are gone, and announcements from one friend are spaced out. Friend text is
never handed to a model with tools: nothing in F3 puts it in chat at all.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from sqlalchemy import text

from domovoi.friend_houses import connect as fc
from domovoi.friend_houses import envelope as fe
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import session as fs

log = logging.getLogger(__name__)

ALERTS = ("inbox", "sender", "read")
OUTBOX_DAYS = 7
OUTBOX_MAX_PER_FRIEND = 200
INBOX_MAX_PER_FRIEND = 5000
READ_ALOUD_MAX_CHARS = 400
ANNOUNCE_GAP_SEC = 60.0
_FLUSHING: dict[bytes, asyncio.Lock] = {}
_LAST_ANNOUNCED: dict[bytes, float] = {}


class MessageError(Exception):
    """Something a person can fix; the message says how."""


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


# ─── sending ──────────────────────────────────────────────────────────────


async def _next_seq(s) -> tuple[bytes, int]:
    row = (await s.execute(text(
        "UPDATE friend_houses_state SET next_seq = next_seq + 1, updated_at = now() "
        "WHERE id = 1 RETURNING epoch, next_seq - 1"))).first()
    if row is None:
        raise MessageError("Friend Houses isn't running yet. Try again in a minute.")
    return bytes(row[0]), int(row[1])


async def send(friend_id: str, body: str) -> dict[str, Any]:
    """Queue a message to a friend and try to deliver it now."""
    from domovoi.friend_houses import store

    keys = fid.load()
    if keys is None:
        raise MessageError("Friend Houses isn't set up on this house yet.")
    async with _db() as s:
        friend = await store.friend(s, friend_id)
        if friend is None or friend.state != "active":
            raise MessageError("That house isn't a friend any more.")
        queued = (await s.execute(text(
            "SELECT count(*) FROM friend_houses_messages WHERE house_key = :h AND state = 'queued'"),
            {"h": friend.house_key})).scalar_one()
        if queued >= OUTBOX_MAX_PER_FRIEND:
            raise MessageError("Too many messages are waiting for that house. "
                               "They'll go when it's back online.")
        epoch, seq = await _next_seq(s)
        try:
            env = fe.text_message(keys, to=friend.house_key, text=body, epoch=epoch, seq=seq,
                                  sent_at=int(time.time()))
        except ValueError as e:
            raise MessageError(str(e).capitalize() + ".") from e
        row = (await s.execute(text(
            "INSERT INTO friend_houses_messages (house_key, friend_id, friend_name, direction, "
            "msg_id, epoch, seq, sent_at, body, envelope, state, expires_at) VALUES "
            "(:h, :f, :n, 'out', :m, :e, :q, :t, :b, :env, 'queued', "
            "now() + make_interval(days => :d)) RETURNING id, created_at"),
            {"h": friend.house_key, "f": friend.id, "n": friend.local_name, "m": env.msg_id,
             "e": env.epoch, "q": env.seq, "t": env.sent_at, "b": env.text,
             "env": env.to_bytes(), "d": OUTBOX_DAYS})).first()
    _service().spawn(flush(friend.house_key), "friend-houses-flush")
    return {"id": int(row[0]), "state": "queued", "created_at": row[1].isoformat()}


async def flush(house_key: bytes) -> int:
    """Deliver everything queued for one friend. Returns how many went.
    One flush per friend at a time."""
    from domovoi.friend_houses import store

    lock = _FLUSHING.setdefault(house_key, asyncio.Lock())
    if lock.locked():
        return 0
    async with lock:
        async with _db() as s:
            friend = await store.friend_by_house(s, house_key)
            queued = (await s.execute(text(
                "SELECT id, msg_id, envelope, attempts FROM friend_houses_messages "
                "WHERE house_key = :h AND direction = 'out' AND state = 'queued' "
                "AND expires_at > now() ORDER BY id"), {"h": house_key})).all()
        from domovoi.friend_houses import shared_lists

        changes = await shared_lists.queued_envelopes(house_key)
        if friend is None or friend.state != "active" or not (queued or changes):
            return 0
        keys = fid.load()
        hello = await _service().my_hello()
        if keys is None or hello is None:
            return 0
        sent = 0
        try:
            sess = await fc.open_session(
                _service().socks_path(), onion=fid.onion_address(friend.onion_key), keys=keys,
                me=hello, friend=fs.Friend(friend.house_key, friend.session_key))
        except (fc.Unreachable, fs.SessionRefused) as e:
            await _note_failure([r[0] for r in queued], str(e))
            return 0
        from domovoi.friend_houses import presence

        await presence.saw(house_key, sess.peer.statement)
        try:
            for row_id, msg_id, env, _attempts in queued:
                await sess.send(fs.Kind.MESSAGE, bytes(env))
                kind, body = await _await_ack(sess, bytes(msg_id))
                if kind is not fs.Kind.MESSAGE_ACK:
                    break
                async with _db() as s:
                    await s.execute(text(
                        "UPDATE friend_houses_messages SET state = 'sent', delivered_at = now(), "
                        "attempts = attempts + 1, last_error = NULL WHERE id = :i"), {"i": row_id})
                sent += 1
            # Shared-list changes ride the same session (F4), after the
            # messages, oldest batch first; each leaves the queue on its ack.
            if len(queued) == sent:
                for msg_id, env in changes:
                    await sess.send(fs.Kind.MESSAGE, env)
                    kind, _ = await _await_ack(sess, msg_id)
                    if kind is not fs.Kind.MESSAGE_ACK:
                        break
                    await shared_lists.mark_sent(msg_id)
        except fs.SessionRefused as e:
            await _note_failure([r[0] for r in queued[sent:]], str(e))
        finally:
            await sess.close()
        if sent:
            log.info("Friend Houses: delivered %d message(s)", sent)
        return sent


async def _await_ack(sess: fs.Session, msg_id: bytes) -> tuple[fs.Kind | None, bytes]:
    """The friend's ack for ``msg_id``, answering pings meanwhile. None when
    it doesn't come (the friend refused it, or is over its quota)."""
    while True:
        try:
            kind, body = await sess.recv(timeout=45)
        except fs.SessionRefused:
            return None, b""
        if kind is fs.Kind.MESSAGE_ACK and body == msg_id:
            return kind, body
        if kind is fs.Kind.PING:
            await sess.send(fs.Kind.PONG, body)
        elif kind is fs.Kind.BYE:
            return None, b""


async def _note_failure(row_ids: list[int], reason: str) -> None:
    if not row_ids:
        return
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_messages SET attempts = attempts + 1, last_error = :e "
            "WHERE id = ANY(:ids)"), {"e": reason[:200], "ids": row_ids})


async def flush_all() -> None:
    """The retry pass: fail what's too old, then try every friend with mail."""
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_messages SET state = 'failed', "
            "last_error = 'Not delivered: their house was offline for " + str(OUTBOX_DAYS) +
            " days.' WHERE direction = 'out' AND state = 'queued' AND expires_at <= now()"))
        houses = [bytes(r[0]) for r in await s.execute(text(
            "SELECT house_key FROM friend_houses_messages WHERE direction = 'out' AND state = 'queued' "
            "UNION SELECT sp.house_key FROM friend_houses_space_ops o "
            "JOIN friend_houses_spaces sp ON sp.id = o.space_id "
            "WHERE o.direction = 'out' AND o.state = 'queued'"))]
    for house in houses:
        try:
            await flush(house)
        except Exception:  # noqa: BLE001 — one friend never blocks the others
            log.exception("Friend Houses: delivering to a friend failed")


async def say_im_back() -> None:
    """Tell every friend this house is online again, so their outboxes
    flush to it now instead of on their next retry (REVIEW 2.2)."""
    from domovoi.friend_houses import store

    keys = fid.load()
    hello = await _service().my_hello()
    if keys is None or hello is None:
        return
    async with _db() as s:
        friends = await store.active_friends(s)

    async def one(f) -> None:
        try:
            sess = await fc.open_session(
                _service().socks_path(), onion=fid.onion_address(f.onion_key), keys=keys,
                me=hello, friend=fs.Friend(f.house_key, f.session_key))
        except (fc.Unreachable, fs.SessionRefused):
            return
        try:
            await sess.send(fs.Kind.IM_BACK)
        finally:
            await sess.close()
        from domovoi.friend_houses import presence

        await presence.saw(f.house_key, sess.peer.statement)
        async with _db() as s:  # it now has this house's current address
            await s.execute(text(
                "UPDATE friend_houses_friends SET announce_needed = FALSE WHERE house_key = :h"),
                {"h": f.house_key})

    await asyncio.gather(*(one(f) for f in friends), return_exceptions=True)
    await flush_all()


# ─── receiving ────────────────────────────────────────────────────────────


async def on_message(event: dict[str, Any]) -> bool:
    """The listener's ``message`` event. Returns whether it is stored (a
    repeat of a stored message counts: the sender just missed our ack)."""
    from domovoi.friend_houses import store

    keys = fid.load()
    try:
        house = bytes.fromhex(event["house"])
        env = fe.parse(bytes.fromhex(event["envelope"]), sender=house,
                       recipient=keys.house_key if keys else b"")
    except (KeyError, ValueError, TypeError, fe.BadEnvelope):
        log.warning("Friend Houses: a message event failed its checks and was dropped")
        return False
    if env.kind == fe.KIND_SPACE:
        async with _db() as s:
            friend = await store.friend_by_house(s, house)
        if friend is None or friend.state != "active":
            return False
        from domovoi.friend_houses import shared_lists

        return await shared_lists.on_envelope(friend, env)
    async with _db() as s:
        friend = await store.friend_by_house(s, house)
        if friend is None or friend.state != "active":
            return False  # removed since it connected
        existing = (await s.execute(text(
            "SELECT 1 FROM friend_houses_messages WHERE house_key = :h AND direction = 'in' "
            "AND msg_id = :m"), {"h": house, "m": env.msg_id})).first()
        if existing:
            return True
        count = (await s.execute(text(
            "SELECT count(*) FROM friend_houses_messages WHERE house_key = :h AND direction = 'in'"),
            {"h": house})).scalar_one()
        if count >= INBOX_MAX_PER_FRIEND:
            log.warning("Friend Houses: %s's inbox is full; their message waits with them",
                        friend.local_name)
            return False
        await s.execute(text(
            "INSERT INTO friend_houses_messages (house_key, friend_id, friend_name, direction, "
            "msg_id, epoch, seq, sent_at, body, envelope, state) VALUES "
            "(:h, :f, :n, 'in', :m, :e, :q, :t, :b, :env, 'unread')"),
            {"h": house, "f": friend.id, "n": friend.local_name, "m": env.msg_id, "e": env.epoch,
             "q": env.seq, "t": env.sent_at, "b": fe.clean_text(env.text), "env": env.to_bytes()})
        alert = await _alert_for(s, friend.id)
    log.info("Friend Houses: a message from %s", friend.local_name)
    _service().spawn(_alert(friend.house_key, friend.local_name, alert, env.text), "friend-houses-alert")
    return True


async def _alert_for(s, friend_id: str) -> str:
    row = (await s.execute(text("SELECT alert FROM friend_houses_friends WHERE id = :i"),
                           {"i": friend_id})).first()
    return str(row[0]) if row and row[0] in ALERTS else "inbox"


# ─── alerts ───────────────────────────────────────────────────────────────

_STOCK_WAKE_PHRASES = ("hey jarvis", "alexa", "hey mycroft", "hey rhasspy", "domovoi", "domo")


async def wake_phrases() -> list[str]:
    """Every phrase a speaker in this house might wake to: the stock and
    published words, the assistant's own name, and the household's trained
    words."""
    phrases = set(_STOCK_WAKE_PHRASES)
    try:
        from domovoi import wake_default

        for word in wake_default.PUBLISHED.values():
            phrases.add(word.phrase.lower())
        for _name, phrase in wake_default.BUILTIN_WORDS.values():
            phrases.add(phrase.lower())
    except Exception:  # noqa: BLE001 — the stock list still applies
        pass
    try:
        from domovoi import phone_voice

        phrases.update(n for n in phone_voice._wake_names() if n)
    except Exception:  # noqa: BLE001
        pass
    try:
        async with _db() as s:
            for (phrase,) in await s.execute(text("SELECT phrase FROM wake_words")):
                if phrase:
                    phrases.add(str(phrase).lower())
    except Exception:  # noqa: BLE001 — the built-in phrases still apply
        pass
    return sorted(phrases, key=len, reverse=True)


def safe_to_say(body: str, phrases: list[str], *, limit: int = READ_ALOUD_MAX_CHARS) -> str:
    """A friend's text made safe to speak: no wake phrases anywhere (a
    satellite in the next room hearing "Hey Jarvis, unlock the door" must
    hear only "unlock the door"), no control characters, capped."""
    said = fe.clean_text(body).replace("\n", " ")
    for phrase in phrases:
        words = [re.escape(w) for w in phrase.split()]
        if not words:
            continue
        pattern = r"(?<![\w])" + r"[\s,.!?-]*".join(words) + r"(?![\w])"
        said = re.sub(pattern, " ", said, flags=re.IGNORECASE)
    try:
        from domovoi import phone_voice

        kept = [w for w in said.split() if not phone_voice._sounds_like_the_name(
            re.sub(r"[^a-z]", "", w.lower()) or "-", greeted=True)]
        said = " ".join(kept)
    except Exception:  # noqa: BLE001 — the exact-phrase pass above still ran
        said = " ".join(said.split())
    # What a removed phrase leaves at the edges (", unlock the door").
    said = said.strip(" ,;:-")
    if len(said) > limit:
        said = said[:limit].rsplit(" ", 1)[0] + "… The rest is in the Friend Houses inbox."
    return said.strip()


async def _alert(house: bytes, name: str, alert: str, body: str) -> None:
    if alert == "inbox":
        return
    now = time.monotonic()
    if now - _LAST_ANNOUNCED.get(house, 0.0) < ANNOUNCE_GAP_SEC:
        return  # spaced out: the inbox has the rest (finding 30)
    _LAST_ANNOUNCED[house] = now
    from domovoi.speech_sanitize import sanitize_for_speech

    if alert == "read":
        spoken = f"A message from {name}: {safe_to_say(body, await wake_phrases())}"
    else:
        spoken = f"A message from {name}."
    try:
        from domovoi.sdk.speech import SpeechAPI

        await SpeechAPI("core").announce(None, sanitize_for_speech(spoken))
    except Exception as e:  # noqa: BLE001 — the inbox has it regardless
        log.info("Friend Houses: announcing a message failed: %s", e)


async def set_alert(friend_id: str, alert: str) -> bool:
    if alert not in ALERTS:
        raise MessageError("Choose one of: inbox only, say who it's from, read it aloud.")
    async with _db() as s:
        res = await s.execute(text(
            "UPDATE friend_houses_friends SET alert = :a WHERE id = :i AND state = 'active'"),
            {"a": alert, "i": friend_id})
    return bool(res.rowcount)


# ─── the inbox ────────────────────────────────────────────────────────────


def _view(r: Any) -> dict[str, Any]:
    return {"id": int(r[0]), "direction": r[1], "body": r[2], "state": r[3],
            "sent_at": int(r[4]), "created_at": r[5].isoformat(),
            "delivered_at": r[6].isoformat() if r[6] else None, "last_error": r[7]}


async def conversation(house_key: bytes, *, before: int | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
    """One friend's messages, newest last, ``limit`` at a time."""
    limit = max(1, min(int(limit), 200))
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT id, direction, body, state, sent_at, created_at, delivered_at, last_error "
            "FROM friend_houses_messages WHERE house_key = :h "
            "AND (CAST(:b AS BIGINT) IS NULL OR id < :b) ORDER BY id DESC LIMIT :n"),
            {"h": house_key, "b": before, "n": limit})).all()
    return [_view(r) for r in reversed(rows)]


async def conversations() -> list[dict[str, Any]]:
    """Every friend (and former friend) with messages: name, unread count,
    the last message."""
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT m.house_key, max(m.friend_name), max(m.friend_id), "
            "count(*) FILTER (WHERE m.state = 'unread'), max(m.id), "
            "count(*) FILTER (WHERE m.state = 'failed') "
            "FROM friend_houses_messages m GROUP BY m.house_key ORDER BY max(m.id) DESC"))).all()
    return [{"house": bytes(r[0]).hex(), "name": r[1], "friend_id": r[2], "unread": int(r[3]),
             "last_id": int(r[4]), "not_delivered": int(r[5])} for r in rows]


async def unread_total() -> int:
    async with _db() as s:
        return int((await s.execute(text(
            "SELECT count(*) FROM friend_houses_messages WHERE state = 'unread'"))).scalar_one())


async def mark_read(house_key: bytes) -> int:
    async with _db() as s:
        res = await s.execute(text(
            "UPDATE friend_houses_messages SET state = 'read' WHERE house_key = :h "
            "AND state = 'unread'"), {"h": house_key})
    return int(res.rowcount or 0)


async def delete_conversation(house_key: bytes) -> int:
    async with _db() as s:
        res = await s.execute(text("DELETE FROM friend_houses_messages WHERE house_key = :h"),
                              {"h": house_key})
    return int(res.rowcount or 0)


async def export(house_key: bytes) -> dict[str, Any]:
    """Every message with one friend, each with its signed envelope, so it
    can be kept as proof of what was said (owner decision 12; finding 31:
    keep or export before deleting). Anyone can check an envelope against
    the sender's public house key."""
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT direction, body, sent_at, created_at, envelope, max(friend_name) OVER () "
            "FROM friend_houses_messages WHERE house_key = :h ORDER BY id"),
            {"h": house_key})).all()
    keys = fid.load()
    return {
        "format": "domovoi-friend-houses-export-v1",
        "with": {"name": rows[0][5] if rows else None, "house_key": house_key.hex(),
                 "fingerprint": fid.fingerprint(house_key)},
        "this_house": {"house_key": keys.house_key.hex() if keys else None},
        "messages": [{"direction": r[0], "text": r[1], "sent_at": int(r[2]),
                      "stored_at": r[3].isoformat(), "envelope": bytes(r[4]).hex(),
                      "signature_checks": fe.verify_stored(bytes(r[4]))} for r in rows],
    }


__all__ = ["ALERTS", "MessageError", "conversation", "conversations", "delete_conversation",
           "export", "flush", "flush_all", "mark_read", "on_message", "safe_to_say", "say_im_back",
           "send", "set_alert", "unread_total", "wake_phrases"]

