"""Friending, from making a friend file to two houses being friends.

Friend Houses ("serverless messaging and friend system between households"),
phase F2. The design is DESIGN-friend-file-and-handshake.md (kept with the
Friend Houses notes, outside the repo); the owner's decisions 11–14 set the
limits. In plain steps, with house A inviting and house B asking:

1. **An admin at A makes a friend file** (:func:`make_invite`): valid for
   the household's default number of days, or a number chosen for this file,
   1 to 30. At most :data:`MAX_OPEN_INVITES` are open at once. A's pairing
   onion runs while any is open.
2. **An admin at B imports it** (:func:`preview`, then :func:`send_request`).
   B makes a Tor client key for A, authorizes it on its own onion, and asks
   on A's pairing onion (:mod:`domovoi.friend_houses.pairing`). Both houses
   now hold the same 4-digit code. If A can't be reached, B tries again
   every few minutes until the file expires.
3. **A's listener reports the request**; core uses the invite up (once,
   atomically) and lists the request for an admin (:func:`on_request`).
   Nothing is announced: no notifications by default (owner decision 2).
4. **An admin at A accepts** (:func:`accept`) and names the friend. A adds
   the friend, authorizes a key for B, and tells B over B's onion with an
   ACCEPT frame carrying that key (retried until B is reached).
   **Declining** stores nothing and tells B nothing (finding 29).
5. **B's listener reports the accept**; core checks it matches a request B
   sent and adds the friend (:func:`on_accepted`).

Both houses then **announce the new friend aloud and log it** (owner
decision 13), and show it "Not verified" until someone compares the codes
and says so (:func:`mark_verified`, decision 14).

Every route that reaches this is admin-only, GETs included (finding 10).
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from domovoi.friend_houses import connect as fc
from domovoi.friend_houses import friend_file as ff
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import pairing as fp
from domovoi.friend_houses import presence
from domovoi.friend_houses import session as fs
from domovoi.friend_houses import store
from domovoi.friend_houses import tor_config as tc

log = logging.getLogger(__name__)

MAX_OPEN_INVITES = 10
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 30, 7
REQUEST_WAIT_DAYS = 30        # a request nobody answers is dropped after this
ACCEPT_GRACE_DAYS = 1         # how long past the file's expiry B keeps waiting
NAME_MAX = 60


class FriendingError(Exception):
    """Something a person can fix; the message says how."""


def _db():
    from domovoi.db.session import session_scope

    return session_scope()


def _service():
    from domovoi.friend_houses import service

    return service


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(6)


def _local_name(name: object) -> str:
    cleaned = ff.clean_name(str(name or ""))[:NAME_MAX].strip()
    if not cleaned:
        raise FriendingError("Give the house a name, for example \"The Smiths\".")
    return cleaned


def _require_on() -> fid.HouseKeys:
    from domovoi.config import settings

    if not getattr(settings, "friend_houses_enabled", False):
        raise FriendingError("Friend Houses is off. Turn it on first.")
    from domovoi import egress

    if egress.policy() == "never":
        raise FriendingError("The internet answer is \"never\", so Friend Houses can't connect.")
    return fid.load() or fid.create()


# ─── settings ─────────────────────────────────────────────────────────────


async def invite_days_default() -> int:
    async with _db() as s:
        value = (await store.household_settings(s)).get("invite_days", DEFAULT_DAYS)
    try:
        return min(MAX_DAYS, max(MIN_DAYS, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


async def set_invite_days_default(days: int) -> int:
    days = _days(days)
    async with _db() as s:
        current = await store.household_settings(s)
        current["invite_days"] = days
        await store.save_household_settings(s, current)
    return days


def _days(days: object) -> int:
    try:
        value = int(days)  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise FriendingError(f"Choose between {MIN_DAYS} and {MAX_DAYS} days.") from exc
    if not MIN_DAYS <= value <= MAX_DAYS:
        raise FriendingError(f"Choose between {MIN_DAYS} and {MAX_DAYS} days.")
    return value


# ─── 1. friend files ──────────────────────────────────────────────────────


async def make_invite(*, days: int | None = None, label: str | None = None,
                      created_by: str | None = None) -> dict[str, Any]:
    """Make a friend file. Returns the invite, with ``file`` (text and a
    download name) when the pairing address is ready, which on a first run
    can take a few minutes (the Tor image is built)."""
    _require_on()
    days = _days(days) if days is not None else await invite_days_default()
    label = ff.clean_name(label)[:80] if label else None
    async with _db() as s:
        if len(await store.open_invites(s)) >= MAX_OPEN_INVITES:
            raise FriendingError(
                f"There are already {MAX_OPEN_INVITES} unused friend files. Cancel one first.")
        invite_id = secrets.token_hex(16)
        await store.insert_invite(s, invite_id=invite_id, label=label, days=days,
                                  created_by=created_by)
        await _set_notice(s, None)  # a new file answers a "file closed" notice
    await _service().reconcile_within(60.0)
    log.info("Friend Houses: made a friend file (%d days)", days)
    return await invite_with_file(invite_id)


async def invite_with_file(invite_id: str) -> dict[str, Any]:
    """An open invite and, when the pairing address exists, its file. The
    file can be downloaded again while the invite is unused (the secret is
    derived, never stored)."""
    async with _db() as s:
        row = await store.invite(s, invite_id)
    if row is None or row["used_at"] or row["cancelled_at"] or row["expires_at"].timestamp() <= time.time():
        raise FriendingError("That friend file has been used, cancelled or has expired.")
    out = _invite_view(row)
    keys = fid.load()
    pairing = _service().pairing_onion()
    if keys is None or pairing is None:
        out["file"] = None
        return out
    f = ff.make(keys, pairing_onion=pairing, invite_id=bytes.fromhex(invite_id),
                expires_day=int(row["expires_at"].timestamp() // 86400) + 1,
                name=await house_name())
    out["file"] = {"text": f.to_text(), "filename": ff.filename_for(f.name)}
    return out


def _invite_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "label": row["label"],
            "created_at": row["created_at"].isoformat(), "expires_at": row["expires_at"].isoformat()}


async def list_invites() -> list[dict[str, Any]]:
    async with _db() as s:
        return [_invite_view(r) for r in await store.open_invites(s)]


async def cancel_invite(invite_id: str) -> bool:
    async with _db() as s:
        done = await store.cancel_invite(s, invite_id)
    if done:
        await _service().reconcile()
    return done


async def house_name() -> str:
    """What this house calls itself in friend files: the household's name
    when one is set, else "A Domovoi house"."""
    async with _db() as s:
        name = (await store.household_settings(s)).get("house_name")
    return ff.clean_name(name or "") or "A Domovoi house"


async def set_house_name(name: str) -> str:
    cleaned = _local_name(name)[: ff.NAME_MAX_BYTES]
    async with _db() as s:
        current = await store.household_settings(s)
        current["house_name"] = cleaned
        await store.save_household_settings(s, current)
    return cleaned


# ─── 2. importing a friend file (house B) ─────────────────────────────────


async def preview(text: str) -> dict[str, Any]:
    """What a friend file says, before anything is sent."""
    f = ff.parse(text)
    keys = fid.load()
    if keys is not None and f.house_key == keys.house_key:
        raise FriendingError("That's this house's own friend file.")
    expired = (f.expires_day * 86400) < time.time()
    async with _db() as s:
        already = await store.friend_by_house(s, f.house_key)
        asked = await store.request_by_house(s, "out", f.house_key)
    from domovoi.friend_houses import blocking

    return {"name": f.name, "fingerprint": f.fingerprint, "expired": expired,
            "already_friends": already is not None, "already_asked": asked is not None,
            "blocked": await blocking.is_blocked(f.house_key)}


async def send_request(text: str, *, local_name: str, by: str | None = None) -> dict[str, Any]:
    """Import a friend file: record the request and start asking. Returns
    the request; the code appears once the other house has been reached."""
    keys = _require_on()
    f = ff.parse(text)
    name = _local_name(local_name)
    if f.house_key == keys.house_key:
        raise FriendingError("That's this house's own friend file.")
    if f.expires_day * 86400 < time.time():
        raise FriendingError("That friend file has expired. Ask for a new one.")
    from domovoi.friend_houses import blocking

    if await blocking.is_blocked(f.house_key):
        raise FriendingError("You blocked that house. Unblock it first if you want to be friends again.")
    reach = tc.ReachKey.generate()
    days = max(1, int((f.expires_day * 86400 - time.time()) // 86400) + 1 + ACCEPT_GRACE_DAYS)
    request_id = _new_id("r")
    async with _db() as s:
        if await store.friend_by_house(s, f.house_key):
            raise FriendingError("You're already friends with that house.")
        if await store.request_by_house(s, "out", f.house_key):
            raise FriendingError("You've already asked that house. Wait for them to accept.")
        if await store.session_key_taken(s, f.session_key):
            # A file naming a key a friend (or another request) already
            # uses would take that friend's place in the listener's list
            # and lock it out (audit R6 FH-11). No honest file does that.
            raise FriendingError("That friend file clashes with a friend house you already have. "
                                 "Ask for a new one.")
        await store.insert_request(
            s, id=request_id, direction="out", state="unsent", house_key=f.house_key,
            session_key=f.session_key, their_name=f.name, local_name=name,
            invite_id=f.invite_id.hex(), reach_public=reach.public, reach_gift=reach.private,
            friend_file=f.to_text(), days=min(days, MAX_DAYS + ACCEPT_GRACE_DAYS))
    log.info("Friend Houses: friend file imported; asking %s", f.fingerprint)
    await _service().reconcile_within(60.0)
    _service().schedule_deliveries()
    return {"id": request_id, "state": "unsent"}


async def deliver_request(row: dict[str, Any]) -> bool:
    """Try once to send an outgoing request. True when it arrived."""
    keys = fid.load()
    hello = await _service().my_hello()
    if keys is None or hello is None:
        return False
    f = ff.parse(row["friend_file"])
    try:
        r, w = await fc.open_stream(_service().socks_path(), onion=f.pairing_address)
        code = await fp.ask(r, w, file=f, keys=keys, statement=hello.statement,
                            reach_private=row["reach_gift"], name=await house_name())
        w.close()
    except (fc.Unreachable, fp.PairingRefused) as e:
        stop = isinstance(e, fp.PairingRefused) and e.after_nonces
        async with _db() as s:
            if stop:
                # Broken off after the nonces were out: not retried on its
                # own (each run is a try at the code). Dropping the file
                # stops the retries; the person decides what next.
                await store.update_request(s, row["id"], attempts=row["attempts"] + 1,
                                           friend_file=None, last_error=STOPPED_ERROR)
            else:
                await store.update_request(s, row["id"], attempts=row["attempts"] + 1,
                                           last_error=str(e)[:200])
        if stop:
            log.warning("Friend Houses: a friend request was broken off partway; not retrying it")
        return False
    async with _db() as s:
        # Sent: the file and our copy of the key we gave them are no longer needed.
        await store.update_request(s, row["id"], state="sent", code=code, friend_file=None,
                                   reach_gift=None, last_error=None,
                                   attempts=row["attempts"] + 1)
    log.info("Friend Houses: friend request delivered (code shown on both houses)")
    return True


STOPPED_ERROR = ("Stopped: the other house broke off partway through. Withdraw this "
                 "request and import the friend file again, or ask for a new one.")


# ─── 3. a request arrives (house A) ───────────────────────────────────────


async def on_request(event: dict[str, Any]) -> None:
    """The listener's ``request`` event. Uses the invite up first; a second
    request on the same file (or a used, cancelled or expired one) is
    dropped without a trace."""
    try:
        invite_id = str(event["invite"])
        statement = fid.AddressStatement.parse(bytes.fromhex(event["statement"]))
        reach_private = bytes.fromhex(event["reach_private"])
        code = str(event["code"])
        their_name = ff.clean_name(str(event.get("name", "")))
    except (KeyError, ValueError, TypeError):
        log.warning("Friend Houses: a malformed request event was dropped")
        return
    if statement is None or len(reach_private) != 32 or not (len(code) == 4 and code.isdigit()):
        log.warning("Friend Houses: a request event failed its checks and was dropped")
        return
    from domovoi.friend_houses import blocking

    if await blocking.is_blocked(statement.house_key):
        log.info("Friend Houses: a request from a blocked house was dropped")
        return
    async with _db() as s:
        if not await store.use_invite(s, invite_id):
            log.info("Friend Houses: a request on a used or closed friend file was dropped")
            return
        if await store.friend_by_house(s, statement.house_key) or \
                await store.request_by_house(s, "in", statement.house_key):
            log.info("Friend Houses: a repeat request from a known house was dropped")
            return
        await store.insert_request(
            s, id=_new_id("r"), direction="in", state="waiting", house_key=statement.house_key,
            session_key=statement.session_key, statement=statement.to_bytes(),
            their_name=their_name, code=code, invite_id=invite_id,
            reach_private=reach_private, days=REQUEST_WAIT_DAYS)
    log.info("Friend Houses: a friend request arrived (%s)", fid.fingerprint(statement.house_key))
    await _service().reconcile()  # the invite is used: maybe no pairing onion now


INVITE_CLOSED_NOTICE = ("A friend file was closed after repeated failed tries. "
                        "If you still want to connect, make a new one.")


async def _set_notice(s, notice: dict[str, Any] | None) -> None:
    current = await store.household_settings(s)
    if current.get("notice") == notice:
        return
    if notice is None:
        current.pop("notice", None)
    else:
        current["notice"] = notice
    await store.save_household_settings(s, current)


async def notice() -> dict[str, Any] | None:
    """The one notice the dashboard's Friend Houses page shows until it is
    dismissed (or a new friend file is made): ``{"kind", "text", "at"}``."""
    async with _db() as s:
        value = (await store.household_settings(s)).get("notice")
    return value if isinstance(value, dict) else None


async def dismiss_notice() -> None:
    async with _db() as s:
        await _set_notice(s, None)


async def on_invite_closed(event: dict[str, Any]) -> None:
    """The listener closed a friend file after several requests on it were
    broken off partway (pairing.ABANDONED_MAX): close it for good, and say
    so on the dashboard."""
    invite_id = str(event.get("invite", ""))
    if len(invite_id) != 32:
        return
    if await cancel_invite(invite_id):
        async with _db() as s:
            await _set_notice(s, {"kind": "invite_closed", "text": INVITE_CLOSED_NOTICE,
                                  "at": datetime.now(timezone.utc).isoformat()})
        log.warning("Friend Houses: %s", INVITE_CLOSED_NOTICE)


# ─── 4. accepting (house A) ───────────────────────────────────────────────


async def accept(request_id: str, *, local_name: str, by: str | None = None) -> dict[str, Any]:
    _require_on()
    name = _local_name(local_name)
    gift = tc.ReachKey.generate()
    friend_id = _new_id("f")
    async with _db() as s:
        row = await store.request(s, request_id)
        if row is None or row["direction"] != "in" or row["state"] != "waiting":
            raise FriendingError("That request isn't waiting any more.")
        statement = fid.AddressStatement.parse(row["statement"])
        if statement is None:
            raise FriendingError("That request is damaged; ask them to send a new one.")
        already = await store.friend_by_house(s, statement.house_key) is not None
        if already:
            # Both houses imported each other's files and theirs accepted
            # first: already friends (this used to be a 500, R6 FH-11).
            await store.delete_request(s, request_id)
    if already:
        raise FriendingError("You're already friends with that house.")
    async with _db() as s:
        if await store.address_clash(s, statement.house_key, statement.onion_key, statement.session_key):
            raise FriendingError("That house's address clashes with another friend house's. "
                                 "Ask them for a new friend file.")
        # Our own request to the same house is done with (R6 FH-11).
        await store.delete_requests_for(s, statement.house_key, "out")
        await store.insert_friend(
            s, id=friend_id, house_key=statement.house_key, session_key=statement.session_key,
            onion_key=statement.onion_key, statement=statement.to_bytes(),
            statement_at=statement.issued_at, reach_public=gift.public,
            reach_private=row["reach_private"], local_name=name, code=row["code"], added_by=by)
        await store.update_request(s, request_id, state="accepted", local_name=name,
                                   reach_public=gift.public, reach_gift=gift.private)
    await _new_friend(name)
    await _service().reconcile_within(60.0)
    _service().schedule_deliveries()
    return {"friend_id": friend_id, "name": name}


async def withdraw(request_id: str) -> bool:
    """Take back a request this house sent (a file imported by mistake,
    say): its author loses the way in it gave them (audit R6 FH-11)."""
    async with _db() as s:
        row = await store.request(s, request_id)
        if row is None or row["direction"] != "out" or row["state"] not in ("unsent", "sent"):
            return False
        done = await store.delete_request(s, request_id)
    if done:
        await _service().reconcile()  # withdraw their reach key from Tor
    return done


async def decline(request_id: str) -> bool:
    """Forget the request. The other house is never told."""
    async with _db() as s:
        row = await store.request(s, request_id)
        if row is None or row["direction"] != "in" or row["state"] != "waiting":
            return False
        return await store.delete_request(s, request_id)


async def deliver_accept(row: dict[str, Any]) -> bool:
    """Tell the asking house it was accepted. True when it arrived."""
    keys = fid.load()
    hello = await _service().my_hello()
    statement = fid.AddressStatement.parse(row["statement"] or b"")
    if keys is None or hello is None or statement is None or row["reach_gift"] is None:
        return False
    friend = fs.Friend(statement.house_key, statement.session_key)
    try:
        sess = await fc.open_session(_service().socks_path(), onion=statement.onion, keys=keys,
                                     me=hello, friend=friend)
        try:
            await sess.send(fs.Kind.ACCEPT, row["reach_gift"])
            kind, _ = await sess.recv(timeout=60)
            if kind is not fs.Kind.ACCEPT_OK:
                raise fs.SessionRefused("no confirmation")
        finally:
            await sess.close()
    except (fc.Unreachable, fs.SessionRefused) as e:
        async with _db() as s:
            await store.update_request(s, row["id"], attempts=row["attempts"] + 1,
                                       last_error=str(e)[:200])
        return False
    async with _db() as s:
        await store.delete_request(s, row["id"])  # the gift is gone with it
    log.info("Friend Houses: the accept reached the new friend")
    return True


# ─── 5. accepted (house B) ────────────────────────────────────────────────


async def on_accepted(event: dict[str, Any]) -> None:
    """The listener's ``accepted`` event: the house B asked has accepted."""
    try:
        statement = fid.AddressStatement.parse(bytes.fromhex(event["statement"]))
        gift = bytes.fromhex(event["reach_private"])
    except (KeyError, ValueError, TypeError):
        log.warning("Friend Houses: a malformed accept event was dropped")
        return
    if statement is None or len(gift) != 32:
        return
    from domovoi.friend_houses import blocking

    if await blocking.is_blocked(statement.house_key):
        return
    async with _db() as s:
        existing = await store.friend_by_house(s, statement.house_key)
        if existing is not None:
            return  # a repeat accept (our ACCEPT_OK was lost): already friends
        row = await store.request_by_house(s, "out", statement.house_key)
        if row is None or row["state"] != "sent" or row["session_key"] != statement.session_key:
            log.info("Friend Houses: an accept that matches no request was dropped")
            return
        name = row["local_name"] or row["their_name"] or "A friend house"
        if await store.address_clash(s, statement.house_key, statement.onion_key, statement.session_key):
            log.warning("Friend Houses: an accept naming another friend's address was dropped")
            return
        await store.insert_friend(
            s, id=_new_id("f"), house_key=statement.house_key, session_key=statement.session_key,
            onion_key=statement.onion_key, statement=statement.to_bytes(),
            statement_at=statement.issued_at, reach_public=row["reach_public"],
            reach_private=gift, local_name=name, code=row["code"], added_by="accepted")
        await store.delete_requests_for(s, statement.house_key)  # both directions (R6 FH-11)
    await _new_friend(name)
    await _service().reconcile()


# ─── friends ──────────────────────────────────────────────────────────────


async def _new_friend(name: str) -> None:
    """Every new friend is announced aloud and logged (owner decision 13),
    so an addition nobody in the house made can't go unnoticed (finding 3).
    The dashboard and the phones also show it for a week (the status's
    ``changes``), and during the house's quiet hours the announcement waits
    for them to end instead of being dropped (audit R6 FH-8)."""
    log.warning("Friend Houses: %s is now a friend house", name)
    from domovoi.friend_houses import messages

    said = messages.safe_name(name, await messages.wake_phrases())
    await announce(f"{said} is now a Friend House.")


HELD_MAX = 20  # announcements waiting for quiet hours to end


def _house_quiet_now() -> bool:
    from domovoi import quiet_hours

    hours = quiet_hours.house_hours()
    return hours is not None and quiet_hours.in_window(quiet_hours._local_now(), *hours)


async def announce(text: str) -> None:
    """A trust change said aloud in the rooms: now, or (in the house's
    quiet hours) as soon as they end. Never dropped."""
    if _house_quiet_now():
        async with _db() as s:
            current = await store.household_settings(s)
            held = list(current.get("held_announcements") or [])[-(HELD_MAX - 1):]
            held.append(text[:200])
            current["held_announcements"] = held
            await store.save_household_settings(s, current)
        log.info("Friend Houses: quiet hours; the announcement waits for them to end")
        return
    await _speak(text)


async def _speak(text: str) -> None:
    try:
        from domovoi.sdk.speech import SpeechAPI
        from domovoi.speech_sanitize import sanitize_for_speech

        await SpeechAPI("core").announce(None, sanitize_for_speech(text))
    except Exception as e:  # noqa: BLE001 — the log line and the status are the record
        log.info("Friend Houses: an announcement failed: %s", e)


async def announce_held() -> int:
    """Say what waited for quiet hours (run by the service's retry loop)."""
    if _house_quiet_now():
        return 0
    async with _db() as s:
        current = await store.household_settings(s)
        held = list(current.get("held_announcements") or [])
        if not held:
            return 0
        current["held_announcements"] = []
        await store.save_household_settings(s, current)
    for text_ in held:
        await _speak(str(text_))
    return len(held)


async def recent_changes(days: int = 7) -> list[dict[str, Any]]:
    """Trust changes of the last week, newest first, for the dashboard's
    and the phones' notice: new friends and lists shared with a friend."""
    async with _db() as s:
        rows = (await s.execute(text(
            "SELECT 'friend' AS kind, local_name, NULL AS list, added_at FROM friend_houses_friends "
            "WHERE added_at > now() - make_interval(days => :d) "
            "UNION ALL SELECT 'shared_list', f.local_name, sp.title, sp.created_at "
            "FROM friend_houses_spaces sp JOIN friend_houses_friends f ON f.id = sp.friend_id "
            "WHERE sp.origin = 'us' AND sp.state = 'active' "
            "AND sp.created_at > now() - make_interval(days => :d) "
            "ORDER BY 4 DESC LIMIT 20"), {"d": days})).all()
    return [{"kind": r[0], "friend": r[1], "list": r[2], "at": r[3].isoformat()} for r in rows]


async def list_friends() -> list[dict[str, Any]]:
    async with _db() as s:
        rows = await store.active_friends(s)
    return [{"id": f.id, "name": f.local_name, "fingerprint": fid.fingerprint(f.house_key),
             "house": f.house_key.hex(), "alert": f.alert, **presence.view(f.last_seen_at),
             "code": f.code, "verified": f.verified_at is not None,
             "added_at": f.added_at.isoformat() if f.added_at else None} for f in rows]


async def list_requests() -> list[dict[str, Any]]:
    async with _db() as s:
        rows = await store.requests(s)
    return [{"id": r["id"], "direction": r["direction"], "state": r["state"],
             "their_name": r["their_name"], "name": r["local_name"],
             "fingerprint": fid.fingerprint(r["house_key"]), "code": r["code"],
             "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
             "trying": r["direction"] == "out" and r["state"] == "unsent" and bool(r["friend_file"]),
             "last_error": r["last_error"]} for r in rows]


async def mark_verified(friend_id: str) -> bool:
    async with _db() as s:
        return await store.mark_verified(s, friend_id)


async def rename(friend_id: str, local_name: str) -> bool:
    async with _db() as s:
        return await store.rename_friend(s, friend_id, _local_name(local_name))


async def unfriend(friend_id: str) -> bool:
    """Remove a friend. The listener refuses it from the next connection;
    Tor's key is withdrawn at the next (batched) reload."""
    async with _db() as s:
        row = await store.friend(s, friend_id)
        done = await store.delete_friend(s, friend_id)
        if done and row is not None:
            # An accept still waiting to be delivered goes too (R6 FH-11).
            await store.delete_requests_for(s, row.house_key)
    if done:
        log.warning("Friend Houses: a friend house was removed")
        await _service().reconcile()
    return done


# ─── retries ──────────────────────────────────────────────────────────────


async def deliver_pending() -> None:
    """One pass: send unsent requests, deliver unsent accepts, drop expired."""
    async with _db() as s:
        dropped = await store.drop_expired(s)
        outgoing = await store.requests(s, direction="out", states=("unsent",))
        accepts = await store.requests(s, direction="in", states=("accepted",))
    for row in outgoing:
        if row["friend_file"]:  # none: stopped (see deliver_request)
            await deliver_request(row)
    for row in accepts:
        await deliver_accept(row)
    if dropped:
        await _service().reconcile()


async def on_event(event: dict[str, Any]) -> None:
    kind = event.get("event")
    try:
        if kind == "request":
            await on_request(event)
        elif kind == "accepted":
            await on_accepted(event)
        elif kind == "invite_closed":
            await on_invite_closed(event)
    except Exception:  # noqa: BLE001 — one bad event never stops the others
        log.exception("Friend Houses: handling a %s event failed", kind)


__all__ = ["FriendingError", "accept", "cancel_invite", "decline", "deliver_pending",
           "list_friends", "list_invites", "list_requests", "make_invite", "mark_verified",
           "on_event", "preview", "rename", "send_request", "unfriend"]

