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

**Quiet hours** (domovoi/quiet_hours.py): a room in its quiet hours says
nothing when the message comes. The rooms that aren't quiet say it then;
each quiet room says it once its own quiet hours end (:func:`say_held`,
run with the held new-friend announcements on the service's retry pass),
if the message is still unread, the friend still wants it said and it
came in the last :data:`HELD_SAY_HOURS` hours. The incoming row's
otherwise unused ``last_error`` keeps which rooms said it
(``quiet_hours:{json}``), so no room says it twice, across restarts too.
A conversation shows ``held: "quiet_hours"`` when no room said it on
arrival, and ``read_out_at`` once one said it afterwards;
:func:`quiet_hours_now` tells the friends page.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
import unicodedata
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
#: An incoming row's mark (``last_error``): quiet hours held its alert back.
HELD_QUIET = "quiet_hours"
#: Whether an alert held for quiet hours is said once they end. It is.
QUIET_READ_LATER = True
#: A held alert older than this is left to the inbox.
HELD_SAY_HOURS = 24
ANNOUNCE_GAP_SEC = 60.0
_FLUSHING: dict[bytes, asyncio.Lock] = {}
_LAST_ANNOUNCED: dict[bytes, float] = {}
# Friends with a flush already scheduled for the end of their alert gap.
_GAP_FLUSH: set[bytes] = set()
#: A held batch read aloud says at most this many of its messages.
BATCH_READ_MAX = 3


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
    _service().spawn(_flush_now(friend.house_key), "friend-houses-flush")
    return {"id": int(row[0]), "state": "queued", "created_at": row[1].isoformat()}


#: How long a spoken "yes, send it" waits for the friend's ack before it
#: says the message is waiting instead of "Sent".
SEND_WAIT_SEC = 4.0
_SEND_POLL_SEC = 0.25


async def delivery_state(row_id: int) -> str | None:
    """An outgoing message's state now: ``queued``, ``sent`` (the friend
    acknowledged it) or ``failed``; None when it's gone."""
    async with _db() as s:
        return (await s.execute(text(
            "SELECT state FROM friend_houses_messages WHERE id = :i AND direction = 'out'"),
            {"i": int(row_id)})).scalar_one_or_none()


async def wait_delivered(row_id: int, timeout: float | None = None) -> str | None:
    """Wait up to ``timeout`` seconds (:data:`SEND_WAIT_SEC`) for the send's
    own delivery try; returns the state then. Only ``sent`` means delivered."""
    deadline = time.monotonic() + (SEND_WAIT_SEC if timeout is None else timeout)
    while True:
        state = await delivery_state(row_id)
        if state != "queued" or time.monotonic() >= deadline:
            return state
        await asyncio.sleep(_SEND_POLL_SEC)


async def last_sent(house_key: bytes | None = None) -> dict[str, Any] | None:
    """The newest message this house sent (to one friend, or to any), with
    the friend's name: what "did my message send?" answers from."""
    async with _db() as s:
        r = (await s.execute(text(
            "SELECT id, state, friend_name, last_error FROM friend_houses_messages "
            "WHERE direction = 'out' AND (CAST(:h AS BYTEA) IS NULL OR house_key = :h) "
            "ORDER BY id DESC LIMIT 1"), {"h": house_key})).first()
    if r is None:
        return None
    return {"id": int(r[0]), "state": r[1], "name": r[2] or "", "last_error": r[3]}


async def _flush_now(house_key: bytes) -> None:
    """A send's own delivery try, under the retry pass's deadline."""
    try:
        await asyncio.wait_for(flush(house_key), FLUSH_DEADLINE_SEC)
    except asyncio.TimeoutError:
        log.info("Friend Houses: delivering to a friend took too long; next round")


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
    it doesn't come (the friend refused it, or is over its quota), or the
    friend pings more than :data:`fs.PONGS_PER_WAIT` times meanwhile."""
    answered = 0
    while True:
        try:
            kind, body = await sess.recv(timeout=45)
        except fs.SessionRefused:
            return None, b""
        if kind is fs.Kind.MESSAGE_ACK and body == msg_id:
            return kind, body
        if kind is fs.Kind.PING:
            answered += 1
            if answered > fs.PONGS_PER_WAIT:
                return None, b""
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
    # Friends at once (a few at a time), each with a deadline: one that
    # never acks and keeps the session alive can't hold up the rest (audit
    # R6 FH-15).
    gate = asyncio.Semaphore(FLUSH_AT_ONCE)

    async def one(house: bytes) -> None:
        async with gate:
            try:
                await asyncio.wait_for(flush(house), FLUSH_DEADLINE_SEC)
            except asyncio.TimeoutError:
                log.info("Friend Houses: delivering to a friend took too long; next round")
            except Exception:  # noqa: BLE001 — one friend never blocks the others
                log.exception("Friend Houses: delivering to a friend failed")

    await asyncio.gather(*(one(h) for h in houses))


FLUSH_AT_ONCE = 4
FLUSH_DEADLINE_SEC = 300.0


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


BIGINT_MAX = (1 << 63) - 1
SENT_AT_MAX = 253_402_300_799  # 9999-12-31: the last second a date can show


def _display_time(sent_at: int) -> int:
    """The sender's clock is unsigned 64-bit and the sender's to choose: a
    value no date can show (2^62 crashed the Android app; 2^63 and up
    failed the insert, so the message was never acked and everything the
    friend sent after it waited behind it) becomes the time it arrived.
    The signed envelope keeps the original, for the export."""
    return sent_at if 0 < sent_at <= SENT_AT_MAX else int(time.time())


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
    # Explicit dispatch, default refuse (review D12): text and list changes
    # only. Plugin mail has its own event and its own service, and is never
    # stored as a person's message, whatever its body says.
    if env.kind not in (fe.KIND_TEXT, fe.KIND_SPACE):
        log.warning("Friend Houses: a message event of another kind was refused")
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
        row_id = (await s.execute(text(
            "INSERT INTO friend_houses_messages (house_key, friend_id, friend_name, direction, "
            "msg_id, epoch, seq, sent_at, body, envelope, state) VALUES "
            "(:h, :f, :n, 'in', :m, :e, :q, :t, :b, :env, 'unread') RETURNING id"),
            {"h": house, "f": friend.id, "n": friend.local_name, "m": env.msg_id, "e": env.epoch,
             "q": min(env.seq, BIGINT_MAX), "t": _display_time(env.sent_at),
             "b": fe.clean_text(env.text), "env": env.to_bytes()})).scalar_one()
        alert = await _alert_for(s, friend.id)
    log.info("Friend Houses: a message from %s", friend.local_name)
    _service().spawn(_alert(friend.house_key, friend.local_name, alert, env.text, int(row_id)),
                     "friend-houses-alert")
    return True


async def _alert_for(s, friend_id: str) -> str:
    row = (await s.execute(text("SELECT alert FROM friend_houses_friends WHERE id = :i"),
                           {"i": friend_id})).first()
    return str(row[0]) if row and row[0] in ALERTS else "inbox"


# ─── alerts ───────────────────────────────────────────────────────────────

# The stock words, and their names alone: some wake models fire on the
# name without the "hey".
_STOCK_WAKE_PHRASES = ("hey jarvis", "alexa", "hey mycroft", "hey rhasspy", "domovoi", "domo",
                       "jarvis", "mycroft", "rhasspy")


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


def safe_name(name: str, phrases: list[str]) -> str:
    """A friend house's name as the rooms may say it: the same filter as
    its messages (a house can call itself "Hey Jarvis"; R6 FH-7)."""
    return safe_to_say(name or "", phrases, limit=60) or "A friend house"


# How many clean-then-filter passes a text may take to settle. One that
# hasn't settled by then is cut down to plain words (see speakable).
_SETTLE_PASSES = 32
# Words of a phrase may be spread over this many spoken words ("a lexa").
_SPREAD_MAX = 8


def _fold(word: str) -> str:
    """A word as the loose match compares it: compatibility-folded (full-
    width and styled letters become plain ones), accents off, lower case,
    letters and digits only, and repeated letters once ("Jarviss")."""
    s = unicodedata.normalize("NFKD", word)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def _near(heard: str, phrase: str) -> bool:
    """Equal, or (for a phrase of five letters or more) one letter added,
    dropped or changed ("jarvus")."""
    if heard == phrase:
        return True
    if len(phrase) < 5 or abs(len(heard) - len(phrase)) > 1:
        return False
    if len(heard) == len(phrase):
        return sum(a != b for a, b in zip(heard, phrase)) <= 1
    short, long_ = sorted((heard, phrase), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


@functools.lru_cache(maxsize=4096)
def _sounds(word: str) -> tuple[str, ...]:
    """A folded word's phonemes as the house's speech front end says it
    (ARPAbet, stress marks off): "jarvice" and "jarvis" give the same."""
    from domovoi.phonemizer import get_phonemizer
    from domovoi.phonemizer.convert import base

    if len(word) > 40:  # longer than any wake phrase run together
        return ()
    try:
        phones, _src = get_phonemizer().word_arpa(word)
    except Exception:  # noqa: BLE001 — the spelling match still runs
        return ()
    return tuple(base(p) for p in phones)


def _near_sound(heard: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Equal, or (for a phrase of five phonemes or more) one phoneme
    added, dropped or changed."""
    if heard == phrase:
        return True
    if len(phrase) < 5 or abs(len(heard) - len(phrase)) > 1:
        return False
    if len(heard) == len(phrase):
        return sum(a != b for a, b in zip(heard, phrase)) <= 1
    short, long_ = sorted((heard, phrase), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _sound_targets(phrases: list[str]) -> set[tuple[str, ...]]:
    """Each wake phrase as phonemes; empty when the speech front end can't
    load (the spelling match still runs)."""
    try:
        from domovoi.phonemizer import get_phonemizer

        get_phonemizer()
    except Exception:  # noqa: BLE001
        log.warning("Friend Houses: the sound match is unavailable", exc_info=True)
        return set()
    return {t for t in (sum((_sounds(w) for w in (_fold(x) for x in p.split()) if w), ())
                        for p in phrases) if t}


def _strip_loose(said: str, phrases: list[str]) -> str:
    """``said`` without any run of words that, folded and run together,
    is (or is one letter from) a wake phrase: "Hey Jarviss", "a lexa",
    fullwidth letters; or that SOUNDS like one as the house would say it
    ("hey jarvice", "aleksa")."""
    targets = {t for t in (_fold("".join(p.split())) for p in phrases) if t}
    if not targets:
        return said
    sound_targets = _sound_targets(phrases)
    words = said.split()
    folded = [_fold(w) for w in words]
    drop = [False] * len(words)
    longest = max(len(t) for t in targets) + 1
    longest_sound = max((len(t) for t in sound_targets), default=0) + 1
    for i in range(len(words)):
        if not folded[i] or drop[i]:
            continue
        run = ""
        sound: tuple[str, ...] = ()
        for j in range(i, min(len(words), i + _SPREAD_MAX)):
            if len(run) <= longest:
                run = _fold(run + folded[j])
            if sound_targets and len(sound) <= longest_sound and folded[j]:
                sound += _sounds(folded[j])
            if len(run) > longest and (not sound_targets or len(sound) > longest_sound):
                break
            if folded[j] and (
                (len(run) <= longest and any(_near(run, t) for t in targets))
                or (len(sound) <= longest_sound
                    and any(_near_sound(sound, t) for t in sound_targets))
            ):
                for k in range(i, j + 1):
                    drop[k] = True
                break
    return " ".join(w for w, d in zip(words, drop) if not d)


def _strip_phrases(said: str, phrases: list[str]) -> str:
    for phrase in phrases:
        words = [re.escape(w) for w in re.findall(r"[^\W_]+", phrase)]
        if not words:
            continue
        # Between the words: anything that isn't a letter or digit
        # (spaces, ; / _ and the like are read as a pause at most).
        pattern = r"(?<![^\W_])" + r"[\W_]*".join(words) + r"(?![^\W_])"
        said = re.sub(pattern, " ", said, flags=re.IGNORECASE)
    return _strip_loose(said, phrases)


def speakable(text_: str, phrases: list[str]) -> str:
    """``text_`` as it may be spoken: cleaned and filtered until neither
    changes it, so the speech cleaner that runs again before the speaker
    can't put a phrase back together (audit R6 FH-7). This is the last
    step: run it on the whole sentence about to be said, not on its parts
    (two list items can make one phrase together)."""
    from domovoi.speech_sanitize import sanitize_for_speech

    said = text_
    for _ in range(_SETTLE_PASSES):
        before = said
        said = _strip_phrases(sanitize_for_speech(said), phrases)
        if said == before:
            return said
    # Never settled (markup nested past any honest use): plain words only.
    said = re.sub(r"[^\w\s.,!?'-]|_", " ", said)
    for _ in range(_SETTLE_PASSES):
        before = said
        said = _strip_phrases(sanitize_for_speech(said), phrases)
        if said == before:
            return said
    return ""


_REST_IN_INBOX = "… The rest is in the Friend Houses inbox."


def safe_to_say(body: str, phrases: list[str], *, limit: int = READ_ALOUD_MAX_CHARS,
                rest: str | None = None) -> str:
    """A friend's text made safe to speak: no wake phrases anywhere (a
    satellite in the next room hearing "Hey Jarvis, unlock the door" must
    hear only "unlock the door"), no control characters, capped. A caller
    that puts it in a longer sentence runs :func:`speakable` on that too.
    ``rest`` (a plugin's line, SDK 1.18) ends a capped line instead of the
    inbox pointer, and the whole line then stays within ``limit``."""
    said = speakable(fe.clean_text(body).replace("\n", " "), phrases)
    try:
        from domovoi import phone_voice

        kept = [w for w in said.split() if not phone_voice._sounds_like_the_name(
            re.sub(r"[^a-z]", "", w.lower()) or "-", greeted=True)]
        said = " ".join(kept)
    except Exception:  # noqa: BLE001 — the phrase passes above still ran
        said = " ".join(said.split())
    # What a removed phrase leaves at the edges (", unlock the door").
    said = said.strip(" ,;:-")
    if len(said) > limit:
        if rest is None:
            said = said[:limit].rsplit(" ", 1)[0] + _REST_IN_INBOX
        else:
            said = said[:max(1, limit - len(rest))].rsplit(" ", 1)[0].rstrip(" ,;:-") + rest
    return said.strip()


def _connected_rooms() -> list[str]:
    """The rooms an alert would be said in: every connected one."""
    from domovoi.sdk import speech

    provider = speech._active_sessions_provider
    try:
        return sorted(str(r) for r in ((provider() or {}) if provider is not None else {}))
    except Exception:  # noqa: BLE001 — then the house's own hours
        return []


async def quiet_hours_now() -> dict[str, Any] | None:
    """For the friends page: None, or which rooms are quiet now (``all``
    or ``some`` of the connected ones, the house's hours when none is),
    ``until`` when ("HH:MM", the house's clock) and ``read_later``
    (whether a held alert is said afterwards: yes, see :func:`say_held`)."""
    from domovoi import quiet_hours

    now = await quiet_hours.quiet_now(_connected_rooms())
    return None if now is None else {**now, "read_later": QUIET_READ_LATER}


def held_state(mark: Any) -> dict[str, Any] | None:
    """An incoming row's quiet-hours mark: ``held`` (no room said it on
    arrival), ``said`` (the rooms that have) and ``read_out_at`` (when a
    held one was first said afterwards). None: no mark."""
    if not isinstance(mark, str) or not mark.startswith(HELD_QUIET):
        return None
    state: dict[str, Any] = {"held": True, "said": [], "read_out_at": None}
    rest = mark[len(HELD_QUIET):].lstrip(":")
    if rest:
        try:
            got = json.loads(rest)
        except ValueError:
            got = {}
        if isinstance(got, dict):
            state["held"] = bool(got.get("held", True))
            state["said"] = sorted({r for r in got.get("said") or [] if isinstance(r, str)})
            at = got.get("read_out_at")
            state["read_out_at"] = at if isinstance(at, str) else None
            state["gap"] = bool(got.get("gap", False))
    return state


async def _mark_held(row_id: int, state: dict[str, Any]) -> None:
    async with _db() as s:
        await s.execute(text(
            "UPDATE friend_houses_messages SET last_error = :q WHERE id = :i AND direction = 'in'"),
            {"q": HELD_QUIET + ":" + json.dumps(state, separators=(",", ":")), "i": row_id})


def _alert_text(alert: str, name: str, bodies: list[str], phrases: list[str]) -> str:
    """What the rooms hear: the name (and, for ``read``, the one text)."""
    from domovoi.speech_sanitize import sanitize_for_speech

    name = safe_name(name, phrases)
    if alert == "read" and len(bodies) == 1:
        spoken = f"A message from {name}: {safe_to_say(bodies[0], phrases)}"
    elif len(bodies) > 1:
        spoken = f"{len(bodies)} messages from {name}."
    else:
        spoken = f"A message from {name}."
    return speakable(sanitize_for_speech(spoken), phrases)


def _more_text(alert: str, name: str, bodies: list[str], phrases: list[str]) -> str:
    """What the rooms hear for messages that waited out a friend's alert
    gap: one announcement for them all."""
    from domovoi.speech_sanitize import sanitize_for_speech

    name, n = safe_name(name, phrases), len(bodies)
    if n == 1:
        spoken = (f"Another message from {name}: {safe_to_say(bodies[0], phrases)}" if alert == "read"
                  else f"Another message from {name}.")
    elif alert == "read":
        said = [safe_to_say(b, phrases).rstrip(" .!?") for b in bodies[:BATCH_READ_MAX]]
        spoken = f"{n} more messages from {name}: " + ". ".join(t for t in said if t)
        spoken += f", and {n - BATCH_READ_MAX} more in Friend Houses." if n > BATCH_READ_MAX else "."
    else:
        spoken = f"{n} more messages from {name}."
    return speakable(sanitize_for_speech(spoken), phrases)


def _schedule_gap_flush(house: bytes, delay: float) -> None:
    """Say what waited in a friend's alert gap once the gap ends: one
    flush per friend (the retry pass is the backstop after a restart)."""
    if house in _GAP_FLUSH:
        return
    _GAP_FLUSH.add(house)

    async def later() -> None:
        try:
            await asyncio.sleep(max(delay, 0.0) + 0.05)  # past the gap, never on its edge
        finally:
            _GAP_FLUSH.discard(house)
        await say_held(house)

    _service().spawn(later(), "friend-houses-gap")


async def _announce(room: str | None, said: str) -> list[str]:
    try:
        from domovoi.sdk.speech import SpeechAPI

        return list(await SpeechAPI("core").announce(room, said) or [])
    except Exception as e:  # noqa: BLE001 — the inbox has it regardless
        log.info("Friend Houses: announcing a message failed: %s", e)
        return []


async def _alert(house: bytes, name: str, alert: str, body: str, row_id: int | None = None) -> None:
    if alert == "inbox":
        return
    from domovoi import quiet_hours

    rooms = _connected_rooms()
    if rooms:
        quiet = await quiet_hours.quiet_rooms(rooms)
    else:  # no room to say it in: the house's hours say whether it waits
        quiet = ["(house)"] if await quiet_hours.quiet_now([]) else []
    now = time.monotonic()
    waited = now - _LAST_ANNOUNCED.get(house, 0.0)
    if waited < ANNOUNCE_GAP_SEC:
        # Spaced out (finding 30): held, and said together when the gap
        # ends (a quiet room says it after its quiet hours, as below).
        if row_id is not None:
            await _mark_held(row_id, {"held": len(quiet) >= max(len(rooms), 1), "said": [],
                                      "read_out_at": None, "gap": True})
            _schedule_gap_flush(house, ANNOUNCE_GAP_SEC - waited)
        return
    reached: list[str] = []
    if len(quiet) < max(len(rooms), 1):
        # A quiet room refuses it (StreamSession.announce); the others say
        # it, and that starts the friend's gap.
        _LAST_ANNOUNCED[house] = now
        reached = await _announce(None, _alert_text(alert, name, [body], await wake_phrases()))
    if quiet and row_id is not None:
        # Kept for the quiet rooms, said when their quiet hours end.
        await _mark_held(row_id, {"held": not reached, "said": sorted(reached), "read_out_at": None})
        log.info("Friend Houses: quiet hours; a message waits for %s",
                 "them to end" if not reached else "the quiet rooms' hours to end")


async def say_held(house: bytes | None = None) -> int:
    """Say the messages quiet hours or a friend's alert gap held back, in
    each connected room that isn't quiet now and hasn't said them: still
    unread, the friend's alert still on, at most :data:`HELD_SAY_HOURS`
    old, and that friend's gap over. One friend's messages go together per
    room, and that counts as the friend's alert for the next gap. ``house``:
    that friend only. Returns how many times a room said something."""
    from datetime import datetime, timezone

    from domovoi import quiet_hours

    rooms = _connected_rooms()
    if not rooms:
        return 0
    quiet = set(await quiet_hours.quiet_rooms(rooms))
    awake = [r for r in rooms if r not in quiet]
    if not awake:
        return 0
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT m.id, m.house_key, m.body, m.last_error, f.local_name, f.alert "
            "FROM friend_houses_messages m JOIN friend_houses_friends f ON f.id = m.friend_id "
            "WHERE m.direction = 'in' AND m.state = 'unread' AND m.last_error LIKE :q "
            "AND f.state = 'active' AND f.alert IN ('sender', 'read') "
            "AND m.created_at > now() - make_interval(hours => :h) ORDER BY m.id"),
            {"q": HELD_QUIET + "%", "h": HELD_SAY_HOURS})).all()
    by_friend: dict[bytes, list] = {}
    now = time.monotonic()
    for r in rows:
        key = bytes(r[1])
        if (house is not None and key != house) or now - _LAST_ANNOUNCED.get(key, 0.0) < ANNOUNCE_GAP_SEC:
            continue
        st = held_state(r[3])
        if st is not None:
            by_friend.setdefault(bytes(r[1]), []).append((r, st))
    if not by_friend:
        return 0
    phrases = await wake_phrases()
    spoke = 0
    for key, group in by_friend.items():
        name, alert = group[0][0][4], group[0][0][5]
        for room in awake:
            due = [(r, st) for r, st in group if room not in st["said"]]
            if not due:
                continue
            words = (_more_text if any(st.get("gap") for _, st in due) else _alert_text)(
                alert, name, [r[2] for r, _ in due], phrases)
            if not await _announce(room, words):
                continue
            spoke += 1
            _LAST_ANNOUNCED[key] = time.monotonic()
            at = datetime.now(timezone.utc).isoformat()
            for r, st in due:
                st["said"] = sorted(set(st["said"]) | {room})
                if st["held"] and not st["read_out_at"]:
                    st["read_out_at"] = at
                await _mark_held(int(r[0]), st)
    return spoke


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
    incoming = r[1] == "in"
    return {"id": int(r[0]), "direction": r[1], "body": r[2], "state": r[3],
            "sent_at": int(r[4]), "created_at": r[5].isoformat(),
            "delivered_at": r[6].isoformat() if r[6] else None,
            "last_error": None if incoming else r[7],
            **_held_view(r[7] if incoming else None)}


def _held_view(mark: Any) -> dict[str, Any]:
    st = held_state(mark)
    if st is None or not st["held"]:
        return {"held": None, "read_out_at": None}
    return {"held": HELD_QUIET, "read_out_at": st["read_out_at"]}


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
           "export", "flush", "flush_all", "held_state", "mark_read", "on_message",
           "quiet_hours_now", "safe_to_say", "say_held", "say_im_back", "send", "set_alert",
           "speakable", "unread_total", "wake_phrases"]

