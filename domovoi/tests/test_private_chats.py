"""Private chats (1.5.0, V086, design-notes PRIVATE-CHATS.md §2.9).

A home-server chat is private to the device whose KEY started it; the
house's admins read, share and delete every chat but never write in
another device's private one. These pin:

* the rule table, row by row (DB-free, ``chat_access.decide``);
* the device key: minted once, a hash only, never re-minted, the cookie
  flags, ``device_key_ok``, never logged;
* the routes: owner, another device, keyless, admin Bearer, admin cookie,
  pre-setup (no admin view), and the identical 404;
* spoofing: another device naming the owner's id every way it can;
* search and ``scope``; keyless and shared-screen creation (409s);
* the share switch, its race with a send, and unsharing;
* a device marked a shared screen later, and unmarked;
* photos to a phone (one phone, its catch-up read), room letters (shared);
* the V086 backfill; the core's phone voice turn; the phone grant binding.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from domovoi import chat_access
from domovoi.chat_access import Action, Thread, Viewer
from domovoi.db.session import engine, session_scope
from domovoi.tests.auth_testkit import (
    COOKIE,
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin_with_cookie,
    core_client,
    db_device_token,
    web_client,
)
from domovoi.tests.conftest import requires_db

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "migrations" / "V086__private_chats.sql"
RW = {"X-Requested-With": "domovoi-tests"}
KEY = chat_access.DEVICE_KEY_HEADER
MISSING = {"detail": "thread not found"}


# ═══ The rule (DB-free) ═══════════════════════════════════════════════════

OWNER = Viewer(device_id="dev-owner")
OTHER = Viewer(device_id="dev-other")
KEYLESS = Viewer()
SCREEN = Viewer(device_id="dev-screen", shared_screen=True)
ADMIN = Viewer(admin=True)
ADMIN_OWNER = Viewer(device_id="dev-owner", admin=True)

PRIVATE = Thread(id=1, owner_device_id="dev-owner", shared=False, private_columns=True)
SHARED = Thread(id=2, owner_device_id="dev-owner", shared=True, private_columns=True)
SHARED_NOBODY = Thread(id=3, owner_device_id=None, shared=True, private_columns=True)
ADMIN_ONLY = Thread(id=4, owner_device_id=None, shared=False, private_columns=True)
SCREEN_OWNED = Thread(id=5, owner_device_id="dev-screen", shared=False, private_columns=True)
HOUSEHOLD = Thread(id=6)  # a database without V086

R, W, S, D = Action.READ, Action.WRITE, Action.SHARE, Action.DELETE
OK = None


def _expect(viewer: Viewer, thread: Thread, action: Action):
    r = chat_access.decide(viewer, thread, action)
    return OK if r is None else (r.status, r.code)


@pytest.mark.parametrize(("viewer", "thread", "want"), [
    # The owning device: everything on its own and on a shared one it started.
    (OWNER, PRIVATE, {R: OK, W: OK, S: OK, D: OK}),
    (OWNER, SHARED, {R: OK, W: OK, S: OK, D: OK}),
    # Another device, or none: nothing private, read and write shared.
    (OTHER, PRIVATE, {a: (404, "thread_not_found") for a in (R, W, S, D)}),
    (KEYLESS, PRIVATE, {a: (404, "thread_not_found") for a in (R, W, S, D)}),
    (OTHER, SHARED, {R: OK, W: OK, S: (403, "not_yours"), D: (403, "not_yours")}),
    (KEYLESS, SHARED_NOBODY, {R: OK, W: OK, S: (403, "not_yours"), D: (403, "not_yours")}),
    (OTHER, ADMIN_ONLY, {a: (404, "thread_not_found") for a in (R, W, S, D)}),
    # A shared screen: never an owner, even of a chat it started.
    (SCREEN, SCREEN_OWNED, {a: (404, "thread_not_found") for a in (R, W, S, D)}),
    (SCREEN, SHARED, {R: OK, W: OK, S: (403, "not_yours"), D: (403, "not_yours")}),
    # An admin: reads, shares and deletes everything; writes in no other
    # device's private chat.
    (ADMIN, PRIVATE, {R: OK, W: (403, "admin_read_only"), S: OK, D: OK}),
    (ADMIN, ADMIN_ONLY, {R: OK, W: (403, "admin_read_only"), S: OK, D: OK}),
    (ADMIN, SHARED, {R: OK, W: OK, S: OK, D: OK}),
    (ADMIN, SCREEN_OWNED, {R: OK, W: (403, "admin_read_only"), S: OK, D: OK}),
    (ADMIN_OWNER, PRIVATE, {R: OK, W: OK, S: OK, D: OK}),
    # No V086: 1.4.x, everyone everything.
    (KEYLESS, HOUSEHOLD, {R: OK, W: OK, S: OK, D: OK}),
    (OTHER, HOUSEHOLD, {R: OK, W: OK, S: OK, D: OK}),
])
def test_the_rule_table(viewer: Viewer, thread: Thread, want: dict) -> None:
    for action, expected in want.items():
        assert _expect(viewer, thread, action) == expected, (viewer, thread.state, action)


def test_a_worker_writes_where_it_was_sent() -> None:
    assert chat_access.decide(chat_access.SYSTEM, PRIVATE, W) is None


def test_who_owns_a_new_chat() -> None:
    assert chat_access.new_thread(OWNER) == ("dev-owner", False)
    assert chat_access.new_thread(OWNER, True) == ("dev-owner", True)
    assert chat_access.new_thread(KEYLESS) == (None, True)
    assert chat_access.new_thread(SCREEN) == ("dev-screen", True)
    with pytest.raises(chat_access.ChatAccessRefused) as e:
        chat_access.new_thread(KEYLESS, False)
    assert (e.value.status_code, e.value.code) == (409, "device_key_missing")
    with pytest.raises(chat_access.ChatAccessRefused) as e:
        chat_access.new_thread(SCREEN, False)
    assert (e.value.status_code, e.value.code) == (409, "shared_screen")


def test_the_thread_fields() -> None:
    assert chat_access.flags(OWNER, PRIVATE) == {
        "shared": False, "mine": True, "can_write": True, "can_share": True, "can_delete": True,
        "admin_view": False}
    assert chat_access.flags(ADMIN, PRIVATE) == {
        "shared": False, "mine": False, "can_write": False, "can_share": True, "can_delete": True,
        "admin_view": True}
    assert chat_access.flags(OTHER, SHARED) == {
        "shared": True, "mine": False, "can_write": True, "can_share": False, "can_delete": False,
        "admin_view": False}


def test_the_migration() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "device_key_hash TEXT" in sql and "idx_devices_device_key" in sql and "UNIQUE INDEX" in sql
    assert "shared BOOLEAN NOT NULL DEFAULT false" in sql
    assert "owner_device_id TEXT;" in sql  # not a foreign key, like V017's device_id
    assert "ALTER TABLE help_deliveries ADD COLUMN IF NOT EXISTS device_id TEXT" in sql
    numbers = sorted(int(m.group(1)) for p in MIGRATION.parent.glob("V*__*.sql")
                     if (m := re.match(r"V(\d+)__", p.name)))
    assert numbers == list(range(1, numbers[-1] + 1)), "a gap in the migration numbers"


# ═══ DB-backed helpers ════════════════════════════════════════════════════


async def _device(c, base: dict[str, str], prefix: str = "dev") -> dict[str, str]:
    """A fresh device: register it and return its headers (with its key)."""
    dev = f"{prefix}-{uuid.uuid4().hex[:10]}"
    r = await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True}, headers=base)
    assert r.status_code == 200, r.text
    key = r.json()["device_key"]
    assert key, "V086 is applied in this test database"
    c.cookies.clear()
    return {**base, KEY: key, "X-Device-Id": dev, "_id": dev}


def _h(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if not k.startswith("_")}


async def _claimed(c) -> tuple[dict[str, str], dict[str, str], str]:
    """A claimed house: (household headers, admin headers, admin cookie)."""
    token, cookie = await claim_admin_with_cookie(c)
    c.cookies.clear()
    dt = await db_device_token()
    base = {**RW, HEADER: dt}
    return base, {**base, **bearer(token)}, cookie


async def _cleanup(*thread_ids: int) -> None:
    async with session_scope() as s:
        for t in thread_ids:
            await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": t})


# ═══ The device key ═══════════════════════════════════════════════════════


@requires_db
@pytest.mark.asyncio
async def test_the_device_key_is_minted_once_and_only_its_hash_kept(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    dev = f"dev-{uuid.uuid4().hex[:10]}"
    async with web_client() as c:
        r = await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True})
        key = r.json()["device_key"]
        assert key and r.json()["device_key_ok"] is True
        cookie = [h for h in r.headers.get_list("set-cookie") if h.startswith(chat_access.DEVICE_KEY_COOKIE + "=")]
        assert len(cookie) == 1
        flags = cookie[0].lower()
        assert "httponly" in flags and "samesite=strict" in flags and "path=/" in flags
        c.cookies.clear()
        # Registered again without it: no new key, and it says the key is lost.
        again = (await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True})).json()
        assert again["device_key"] is None and again["device_key_ok"] is False
        # With it: fine, and still no new key.
        ok = (await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True}, headers={**RW, KEY: key})).json()
        assert ok["device_key"] is None and ok["device_key_ok"] is True
    async with session_scope() as s:
        stored = (await s.execute(text("SELECT device_key_hash, device_key_at FROM devices WHERE device_id = :d"),
                                  {"d": dev})).one()
    assert stored[0] == hashlib.sha256(key.encode()).hexdigest() and stored[1] is not None
    assert key not in caplog.text


@requires_db
@pytest.mark.asyncio
async def test_an_upgraded_app_gets_its_key_on_its_next_start_without_pairing_again() -> None:
    """An app registers on every start. A row from before 1.5.0 (no key):
    a registration that doesn't ask for a key (a 1.4.x app) mints none, so
    nothing is minted that nobody keeps; the first one that asks (the
    updated app, or any browser) gets it, with no pairing; a repeat
    registration with the stored key changes nothing."""
    dev = f"android-{uuid.uuid4().hex[:10]}"
    async with session_scope() as s:
        await s.execute(text("INSERT INTO devices (device_id, name) VALUES (:d, 'old phone')"), {"d": dev})
        # Its chat from before the upgrade (V086 made it private to this id).
        tid = (await s.execute(text("INSERT INTO chat_threads (title, owner_device_id, shared) "
                                    "VALUES ('mine', :d, false) RETURNING id"), {"d": dev})).scalar_one()
    try:
        async with web_client() as c:
            old = (await c.post("/api/devices/register", json={"device_id": dev})).json()
            assert old["device_key"] is None and old["device_key_ok"] is None
            assert chat_access.DEVICE_KEY_COOKIE not in c.cookies
            new = (await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True})).json()
            key = new["device_key"]
            assert key and new["device_key_ok"] is True
            c.cookies.clear()
            for _ in range(2):
                again = (await c.post("/api/devices/register", json={"device_id": dev, "want_device_key": True},
                                      headers={**RW, KEY: key})).json()
                assert again["device_key"] is None and again["device_key_ok"] is True
            async with session_scope() as s:
                stored = (await s.execute(text("SELECT device_key_hash FROM devices WHERE device_id = :d"),
                                          {"d": dev})).scalar_one()
            assert stored == hashlib.sha256(key.encode()).hexdigest()
            # Its chat from before the upgrade is its own again.
            r = await c.get(f"/api/chat/threads/{tid}/messages", headers={**RW, KEY: key})
            assert r.status_code == 200 and r.json()["thread"]["mine"] is True
    finally:
        await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_two_devices_can_never_share_a_key_hash() -> None:
    from sqlalchemy.exc import IntegrityError

    async with session_scope() as s:
        await s.execute(text("INSERT INTO devices (device_id, name, device_key_hash) VALUES (:a, 'a', 'samehash'), "
                             "(:b, 'b', NULL)"), {"a": "dup-a-" + uuid.uuid4().hex[:6], "b": "dup-b-" + uuid.uuid4().hex[:6]})
    with pytest.raises(IntegrityError):
        async with session_scope() as s:
            await s.execute(text("INSERT INTO devices (device_id, name, device_key_hash) VALUES (:a, 'a', 'samehash')"),
                            {"a": "dup-c-" + uuid.uuid4().hex[:6]})
    async with session_scope() as s:
        await s.execute(text("DELETE FROM devices WHERE device_key_hash = 'samehash' OR device_id LIKE 'dup-%'"))


# ═══ The routes ═══════════════════════════════════════════════════════════


@requires_db
@pytest.mark.asyncio
async def test_a_private_chat_row_by_row(_db) -> None:
    async with web_client() as c:
        base, admin, cookie = await _claimed(c)
        owner = await _device(c, base, "owner")
        other = await _device(c, base, "other")
        t = (await c.post("/api/chat/threads", json={"title": "My diary"}, headers=_h(owner))).json()
        tid = t["id"]
        try:
            assert t["shared"] is False and t["mine"] is True and t["can_share"] is True
            path = f"/api/chat/threads/{tid}/messages"
            # Another device and a keyless caller: the missing-thread 404, byte for byte.
            missing = await c.get("/api/chat/threads/999999999/messages", headers=_h(owner))
            for who in (other, base):
                r = await c.get(path, headers=_h(who))
                assert (r.status_code, r.content) == (404, missing.content)
            assert missing.json() == MISSING
            # The owner reads; an admin reads, flagged, with the device's name.
            assert (await c.get(path, headers=_h(owner))).status_code == 200
            seen = await c.get(path, headers=admin)
            assert seen.status_code == 200
            th = seen.json()["thread"]
            assert th["admin_view"] is True and th["can_write"] is False and th["owner_name"] == owner["_id"]
            # The admin cookie on a read too, never on a write.
            c.cookies.set(COOKIE, cookie)
            assert (await c.get(path, headers=base)).status_code == 200
            c.cookies.clear()
            # An admin never writes in it.
            w = await c.patch(f"/api/chat/threads/{tid}", json={"title": "x"}, headers=admin)
            assert w.status_code == 403 and w.json()["code"] == "admin_read_only"
            # Another device can't share or delete what it can't see: 404.
            for method, body in (("PATCH", {"shared": True}), ("DELETE", None)):
                r = await c.request(method, f"/api/chat/threads/{tid}", json=body, headers=_h(other))
                assert r.status_code == 404 and r.json() == MISSING
            # The admin shares it: another device reads and writes, but
            # can't unshare or delete it.
            r = await c.patch(f"/api/chat/threads/{tid}", json={"shared": True}, headers=admin)
            assert r.status_code == 200 and r.json()["shared"] is True
            assert (await c.get(path, headers=_h(other))).status_code == 200
            assert (await c.patch(f"/api/chat/threads/{tid}", json={"title": "Ours"},
                                  headers=_h(other))).status_code == 200
            for method, body in (("PATCH", {"shared": False}), ("DELETE", None)):
                r = await c.request(method, f"/api/chat/threads/{tid}", json=body, headers=_h(other))
                assert r.status_code == 403 and r.json()["code"] == "not_yours"
            # The owner stops sharing it: hidden again.
            assert (await c.patch(f"/api/chat/threads/{tid}", json={"shared": False},
                                  headers=_h(owner))).json()["shared"] is False
            assert (await c.get(path, headers=_h(other))).status_code == 404
            # The lists: mine and shared for everyone; all, for an admin only.
            mine = [x["id"] for x in (await c.get("/api/chat/threads", headers=_h(other))).json()["threads"]]
            assert tid not in mine
            r = await c.get("/api/chat/threads", params={"scope": "all"}, headers=_h(other))
            assert r.status_code == 403 and r.json()["code"] == "admin_required"
            every = (await c.get("/api/chat/threads", params={"scope": "all"}, headers=admin)).json()["threads"]
            row = next(x for x in every if x["id"] == tid)
            assert row["admin_view"] is True and row["owner_name"] == owner["_id"]
            assert tid not in [x["id"] for x in (await c.get("/api/chat/threads", headers=admin)).json()["threads"]]
            # The owner deletes it.
            assert (await c.delete(f"/api/chat/threads/{tid}", headers=_h(owner))).status_code == 200
        finally:
            await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_before_setup_there_is_no_admin_view() -> None:
    async with session_scope() as s:
        if (await s.execute(text("SELECT 1 FROM admin_auth"))).first():
            pytest.skip("this lane's house is claimed")
    async with web_client() as c:
        owner = await _device(c, RW, "owner")
        other = await _device(c, RW, "other")
        tid = (await c.post("/api/chat/threads", json={}, headers=_h(owner))).json()["id"]
        try:
            assert (await c.get(f"/api/chat/threads/{tid}/messages", headers=_h(other))).status_code == 404
            assert (await c.get("/api/chat/threads", params={"scope": "all"}, headers=_h(other))).status_code == 403
        finally:
            await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_naming_the_owners_id_gets_nothing() -> None:
    """Spoofing (§2.9.3): A's id in X-Device-Id, the device-id cookie, the
    body, the query and phone_id, without A's key."""
    async with web_client() as c:
        owner = await _device(c, RW, "owner")
        other = await _device(c, RW, "other")
        a = owner["_id"]
        tid = (await c.post("/api/chat/threads", json={"title": "Secret"}, headers=_h(owner))).json()["id"]
        try:
            spoof = {**_h(other), "X-Device-Id": a, "X-Domovoi-Device-Id": a}
            c.cookies.set("domovoi-device-id", a)
            r = await c.get(f"/api/chat/threads/{tid}/messages", params={"device_id": a}, headers=spoof)
            assert r.status_code == 404
            r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": "hi", "device_id": a},
                             headers=spoof)
            assert r.status_code == 404
            files = {"audio": ("t.pcm", b"\0" * 64, "application/octet-stream")}
            r = await c.post(f"/api/chat/threads/{tid}/voice", data={"phone_id": a}, files=files, headers=spoof)
            assert r.status_code == 404
            c.cookies.clear()
            # The message the OTHER device sends in its own chat records it,
            # not the id it names.
            mine = (await c.post("/api/chat/threads", json={}, headers=_h(other))).json()["id"]
            await c.post(f"/api/chat/threads/{mine}/messages", json={"content": "hi", "device_id": a},
                         headers=_h(other))
            async with session_scope() as s:
                dev = (await s.execute(text("SELECT device_id FROM chat_messages WHERE thread_id = :t AND role = 'user'"),
                                       {"t": mine})).scalar_one()
            assert dev == other["_id"]
            await _cleanup(mine)
        finally:
            await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_search_never_finds_or_quotes_a_hidden_chat(_db) -> None:
    word = "okapi" + uuid.uuid4().hex[:8]
    async with web_client() as c:
        base, admin, _ = await _claimed(c)
        owner = await _device(c, base, "owner")
        other = await _device(c, base, "other")
        tid = (await c.post("/api/chat/threads", json={"title": "Plans"}, headers=_h(owner))).json()["id"]
        try:
            async with session_scope() as s:
                await s.execute(text("INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', :c)"),
                                {"t": tid, "c": f"the {word} is hidden here"})
            r = await c.get("/api/chat/threads", params={"q": word}, headers=_h(other))
            assert r.json()["threads"] == [] and word not in r.text
            assert [x["id"] for x in (await c.get("/api/chat/threads", params={"q": word},
                                                  headers=_h(owner))).json()["threads"]] == [tid]
            found = (await c.get("/api/chat/threads", params={"q": word, "scope": "all"},
                                 headers=admin)).json()["threads"]
            assert [x["id"] for x in found] == [tid] and word in found[0]["match_snippet"]
        finally:
            await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_a_keyless_caller_starts_shared_chats_and_cant_ask_for_a_private_one() -> None:
    async with web_client() as c:
        r = await c.post("/api/chat/threads", json={"shared": False})
        assert r.status_code == 409 and r.json() == {
            "detail": chat_access.KEY_MISSING.detail, "code": "device_key_missing"}
        t = (await c.post("/api/chat/threads", json={})).json()
        try:
            assert t["shared"] is True and t["mine"] is False and t["can_delete"] is False
            other = await _device(c, RW, "other")
            assert (await c.get(f"/api/chat/threads/{t['id']}/messages", headers=_h(other))).status_code == 200
        finally:
            await _cleanup(t["id"])


@requires_db
@pytest.mark.asyncio
async def test_a_shared_screen(_db) -> None:
    async with web_client() as c:
        base, admin, _ = await _claimed(c)
        tablet = await _device(c, base, "tablet")
        private = (await c.post("/api/chat/threads", json={}, headers=_h(tablet))).json()["id"]
        try:
            r = await c.patch(f"/api/devices/{tablet['_id']}/shared-screen", json={"shared_screen": True},
                              headers=admin)
            assert r.status_code == 200, r.text
            # Its earlier private chat is admin-only while it is one.
            assert (await c.get(f"/api/chat/threads/{private}/messages", headers=_h(tablet))).status_code == 404
            assert (await c.get(f"/api/chat/threads/{private}/messages", headers=admin)).status_code == 200
            # What it starts now is shared, and can't be private.
            r = await c.post("/api/chat/threads", json={"shared": False}, headers=_h(tablet))
            assert r.status_code == 409 and r.json()["code"] == "shared_screen"
            new = (await c.post("/api/chat/threads", json={}, headers=_h(tablet))).json()
            assert new["shared"] is True
            r = await c.patch(f"/api/chat/threads/{new['id']}", json={"shared": False}, headers=admin)
            assert r.status_code == 409 and r.json()["code"] == "shared_screen"
            await _cleanup(new["id"])
            # Unmarked: its chat is its own again.
            await c.patch(f"/api/devices/{tablet['_id']}/shared-screen", json={"shared_screen": False},
                          headers=admin)
            assert (await c.get(f"/api/chat/threads/{private}/messages", headers=_h(tablet))).status_code == 200
        finally:
            await _cleanup(private)


@requires_db
@pytest.mark.asyncio
async def test_unsharing_waits_for_a_send_in_flight_then_hides_the_chat() -> None:
    """§2.7: a write holds the thread row; the switch waits for it. After
    the switch, another device's next send is a 404."""
    async with web_client() as c:
        owner = await _device(c, RW, "owner")
        other = await _device(c, RW, "other")
        t = (await c.post("/api/chat/threads", json={"shared": True}, headers=_h(owner))).json()
        tid = t["id"]
        o_viewer = Viewer(device_id=owner["_id"])
        x_viewer = Viewer(device_id=other["_id"])
        try:
            from domovoi.db.session import SessionLocal

            async with SessionLocal() as send:
                await chat_access.thread_for(send, tid, x_viewer, Action.WRITE)   # the send's lock

                async def unshare() -> None:
                    async with session_scope() as s:
                        th = await chat_access.thread_for(s, tid, o_viewer, Action.SHARE, lock=True)
                        await chat_access.set_shared(s, th, False)

                switch = asyncio.create_task(unshare())
                await asyncio.sleep(0.5)
                assert not switch.done(), "the share switch did not wait for the send"
                await send.execute(text("INSERT INTO chat_messages (thread_id, role, content, device_id) "
                                        "VALUES (:t, 'user', 'in time', :d)"), {"t": tid, "d": other["_id"]})
                await send.commit()
                await asyncio.wait_for(switch, 10)
            r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": "too late"}, headers=_h(other))
            assert r.status_code == 404
            msgs = (await c.get(f"/api/chat/threads/{tid}/messages", headers=_h(owner))).json()["messages"]
            assert [m["content"] for m in msgs] == ["in time"]
        finally:
            await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_photos_go_to_one_phone_and_only_it_lists_them() -> None:
    from domovoi.help_desk import delivery

    async with web_client() as c:
        old = await _device(c, RW, "phone-old")
        new = await _device(c, RW, "phone-new")
        async with session_scope() as s:
            pid = (await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                                   {"n": "Photo " + uuid.uuid4().hex[:6]})).scalar_one()
            await s.execute(text("UPDATE devices SET owner_person_id = :p, last_seen_at = now() - interval '1 day' "
                                 "WHERE device_id = :d"), {"p": pid, "d": old["_id"]})
            await s.execute(text("UPDATE devices SET owner_person_id = :p, last_seen_at = now() WHERE device_id = :d"),
                            {"p": pid, "d": new["_id"]})
        async with session_scope() as s:
            phones = await delivery.phones_of(s, pid)
            sent = await delivery.deliver(s, person_id=pid, question="cats", steps="Here are 3 photos.",
                                          phones=phones, title="Photos: cats")
        try:
            async with session_scope() as s:
                row = (await s.execute(text("SELECT owner_device_id, shared FROM chat_threads WHERE id = :t"),
                                       {"t": sent.thread_id})).one()
            assert row == (new["_id"], False)

            def listed(h):
                return [d["id"] for d in h.json()["deliveries"]]

            assert sent.id in listed(await c.get("/api/help/deliveries", params={"person_id": pid},
                                                          headers=_h(new)))
            for who in (old, RW):
                assert sent.id not in listed(await c.get("/api/help/deliveries", params={"person_id": pid},
                                                                  headers=_h(who)))
            assert (await c.get(f"/api/chat/threads/{sent.thread_id}/messages", headers=_h(old))).status_code == 404
        finally:
            await _cleanup(sent.thread_id)
            async with session_scope() as s:
                await s.execute(text("UPDATE devices SET owner_person_id = NULL WHERE owner_person_id = :p"), {"p": pid})
                await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": pid})


@requires_db
@pytest.mark.asyncio
async def test_a_letter_taken_by_voice_is_shared_with_nobody_as_owner() -> None:
    from domovoi.voice_docs import letters

    tid = await letters._start_thread({"title": "Letter to the council", "room": "kitchen"}, "please fix the road")
    try:
        async with session_scope() as s:
            row = (await s.execute(text("SELECT owner_device_id, shared FROM chat_threads WHERE id = :t"),
                                   {"t": tid})).one()
        assert row == (None, True)
    finally:
        await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_the_v086_backfill() -> None:
    """Existing chats become private to the device of their first user
    message; none leaves them admin-only. Re-run on rows set back to how a
    1.4.x house has them."""
    update = MIGRATION.read_text(encoding="utf-8").split("UPDATE chat_threads t", 1)[1].split(";", 1)[0]
    async with session_scope() as s:
        a = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('a') RETURNING id"))).scalar_one()
        b = (await s.execute(text("INSERT INTO chat_threads (title) VALUES ('b') RETURNING id"))).scalar_one()
        await s.execute(text("INSERT INTO chat_messages (thread_id, role, content, device_id) VALUES "
                             "(:a, 'assistant', 'x', 'dev-late'), (:a, 'user', 'first', 'dev-first'), "
                             "(:a, 'user', 'second', 'dev-second'), (:b, 'user', 'nobody', NULL)"),
                        {"a": a, "b": b})
        await s.execute(text("UPDATE chat_threads SET owner_device_id = NULL, shared = false WHERE id IN (:a, :b)"),
                        {"a": a, "b": b})
        await s.execute(text("UPDATE chat_threads t" + update + " AND t.id IN (:a, :b)"), {"a": a, "b": b})
        rows = dict((await s.execute(text("SELECT id, owner_device_id FROM chat_threads WHERE id IN (:a, :b)"),
                                     {"a": a, "b": b})).all())
        shared = (await s.execute(text("SELECT bool_or(shared) FROM chat_threads WHERE id IN (:a, :b)"),
                                  {"a": a, "b": b})).scalar_one()
    try:
        assert rows == {a: "dev-first", b: None}
        assert shared is False
        assert Thread(id=b, owner_device_id=None, shared=False, private_columns=True).state == "admin-only"
    finally:
        await _cleanup(a, b)


@requires_db
@pytest.mark.asyncio
async def test_the_core_voice_turn_checks_the_key(_db, monkeypatch) -> None:
    async with web_client() as c:
        base, admin, _ = await _claimed(c)
        owner = await _device(c, base, "owner")
        other = await _device(c, base, "other")
        tid = (await c.post("/api/chat/threads", json={}, headers=_h(owner))).json()["id"]
    try:
        async with core_client() as core:
            def turn(who, phone_id):
                return core.post("/v1/phone/voice", headers=_h(who),
                                 data={"phone_id": phone_id, "thread_id": str(tid)},
                                 files={"audio": ("t.pcm", b"\0" * 64, "application/octet-stream")})

            r = await turn(other, other["_id"])
            assert r.status_code == 404 and r.json()["code"] == "thread_not_found"
            r = await turn(base, owner["_id"])  # keyless, naming the owner
            assert r.status_code == 404
            r = await turn(owner, other["_id"])  # the owner's key, another phone's id
            assert r.status_code == 403 and r.json()["code"] == "not_this_phone"
            r = await turn(owner, owner["_id"])
            assert r.status_code == 200 and r.json()["heard"] is False  # too short: checked, nothing written
            r = await core.post("/v1/phone/voice", headers=admin,
                                data={"phone_id": "android-admin1", "thread_id": str(tid)},
                                files={"audio": ("t.pcm", b"\0" * 64, "application/octet-stream")})
            assert r.status_code == 403 and r.json()["code"] == "admin_read_only"
    finally:
        await _cleanup(tid)


@requires_db
@pytest.mark.asyncio
async def test_a_phone_grant_is_bound_to_the_device_key(_db) -> None:
    async with web_client() as c:
        base, admin, _ = await _claimed(c)
        phone = await _device(c, base, "android")
        dev = phone["_id"]
        r = await c.post("/api/auth/phone-grant", json={"device_id": dev, "label": "Pixel"}, headers=admin)
        assert r.status_code == 403  # the admin's Bearer, but not that phone's key
        r = await c.post("/api/auth/phone-grant", json={"device_id": dev, "label": "Pixel"},
                         headers={**admin, KEY: phone[KEY]})
        assert r.status_code == 200, r.text
        grant = r.json()["grant"]
        r = await c.post("/api/auth/phone-grant/exchange", json={"grant": grant, "device_id": dev}, headers=base)
        assert r.status_code == 401  # the grant and the id, without the key
        from domovoi import admin_auth

        admin_auth.LOGIN_BACKOFF.reset()  # the refused try above counted against this address
        r = await c.post("/api/auth/phone-grant/exchange", json={"grant": grant, "device_id": dev},
                         headers={**base, KEY: phone[KEY]})
        assert r.status_code == 200 and r.json()["token"]
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM admin_phone_grants WHERE device_id = :d"), {"d": dev})


@requires_db
@pytest.mark.asyncio
async def test_the_chat_event_still_tells_every_client_to_read_again() -> None:
    from web.backend import realtime

    before = await realtime._snapshot_chat()
    async with web_client() as c:
        owner = await _device(c, RW, "owner")
        tid = (await c.post("/api/chat/threads", json={}, headers=_h(owner))).json()["id"]
    try:
        after = await realtime._snapshot_chat()
        assert set(after) == {"changed"} and after != before
    finally:
        await _cleanup(tid)


# ═══ "My" surfaces: the caller's device by its key; the claim key ═══════


@requires_db
@pytest.mark.asyncio
async def test_my_commands_follow_the_device_key_and_the_claim_key_still_works(_db) -> None:
    """devices.caller_device_row / proves_claim: a person's mix (one of the
    "my" writes) is changed by that person's own claimed device, proven by
    its device key alone (no claim key needed), never by another device
    naming its id; an app from before 1.5.0 (a row with no device key)
    still proves it with the claim key."""
    async with web_client() as c:
        # A claimed house: before setup every caller counts as an admin here.
        base, _, _ = await _claimed(c)
        async with session_scope() as s:
            pid = (await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                                   {"n": "Mix " + uuid.uuid4().hex[:6]})).scalar_one()
        mine = await _device(c, base, "phone")
        other = await _device(c, base, "other")
        try:
            r = await c.put(f"/api/devices/{mine['_id']}/person", json={"person_id": pid}, headers=_h(mine))
            assert r.status_code == 200 and r.json()["claim_key"], r.text
            mix = f"/api/people/{pid}/music-mix"
            assert (await c.put(mix, json={}, headers=_h(mine))).status_code == 200  # the key alone
            spoof = {**_h(other), "X-Device-Id": mine["_id"], "X-Domovoi-Device-Id": mine["_id"]}
            assert (await c.put(mix, json={}, headers=spoof)).status_code == 403
            keyless = {**base, "X-Device-Id": mine["_id"]}
            assert (await c.put(mix, json={}, headers=keyless)).status_code == 403
            # Releasing and re-claiming needs no claim key either.
            r = await c.put(f"/api/devices/{mine['_id']}/person", json={"person_id": None}, headers=_h(mine))
            assert r.status_code == 200, r.text
            # An app from before 1.5.0: its row has no device key, and the
            # claim key it got proves it, named by its id.
            old = f"android-old{uuid.uuid4().hex[:6]}"
            async with session_scope() as s:
                await s.execute(text("INSERT INTO devices (device_id, name) VALUES (:d, 'old phone')"), {"d": old})
            r = await c.put(f"/api/devices/{old}/person", json={"person_id": pid}, headers={**base, "X-Device-Id": old})
            claim = r.json()["claim_key"]
            assert r.status_code == 200 and claim
            ok = await c.put(mix, json={}, headers={**base, "X-Device-Id": old, "X-Domovoi-Claim-Key": claim})
            assert ok.status_code == 200
            bad = await c.put(mix, json={}, headers={**base, "X-Device-Id": old})
            assert bad.status_code == 403 and bad.json()["code"] == "claim_key_required"
        finally:
            async with session_scope() as s:
                await s.execute(text("UPDATE devices SET owner_person_id = NULL WHERE owner_person_id = :p"), {"p": pid})
                await s.execute(text("DELETE FROM person_music_mix WHERE person_id = :p"), {"p": pid}) \
                    if False else None
                await s.execute(text("DELETE FROM people WHERE id = :p"), {"p": pid})


@requires_db
@pytest.mark.asyncio
async def test_a_keyed_device_is_never_named_by_its_id_alone() -> None:
    from starlette.requests import Request

    from web.backend.api import devices as devices_api

    def req(headers: dict[str, str]) -> Request:
        return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    async with web_client() as c:
        mine = await _device(c, RW, "phone")
    async with session_scope() as s:
        by_key = await devices_api.caller_device_row(s, req({KEY: mine[KEY]}))
        by_id = await devices_api.caller_device_row(s, req({"X-Device-Id": mine["_id"]}))
    assert by_key is not None and by_key.device_id == mine["_id"] and by_key.proof == "device_key"
    assert by_id is None
