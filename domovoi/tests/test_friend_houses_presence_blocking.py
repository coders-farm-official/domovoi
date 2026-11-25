"""Friend Houses presence (presence.py) and blocking (blocking.py), against
the test database, with Tor and the service faked."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi.friend_houses import blocking as fb
from domovoi.friend_houses import connect as fc
from domovoi.friend_houses import friend_file as ff
from domovoi.friend_houses import friending as fr
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import messages as fm
from domovoi.friend_houses import presence as fp
from domovoi.friend_houses import session as fs
from domovoi.friend_houses import store
from domovoi.tests.conftest import requires_db
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)

pytestmark = requires_db

ONION_A, ONION_B = bytes([5]) * 32, bytes([6]) * 32


@pytest.fixture
async def house(tmp_path, monkeypatch, db_session):
    from domovoi import egress
    from domovoi.config import settings

    us = fid.create(tmp_path / "us" / "house-keys.json")
    them = fid.create(tmp_path / "them" / "house-keys.json")
    other = fid.create(tmp_path / "other" / "house-keys.json")
    monkeypatch.setattr(fid, "keys_path", lambda: tmp_path / "us" / "house-keys.json")
    monkeypatch.setattr(settings, "friend_houses_enabled", True, raising=False)
    monkeypatch.setattr(egress, "policy", lambda: "always")
    calls: list[str] = []

    async def my_hello():
        return fs.Hello(us.address_statement(bytes([8]) * 32, int(time.time())), b"\x01" * 8)

    async def reconcile():
        calls.append("reconcile")

    async def reconcile_within(timeout=60.0):
        calls.append("reconcile")
        return True

    async def move():
        calls.append("move")
        return True

    fake = SimpleNamespace(my_hello=my_hello, socks_path=lambda: tmp_path / "socks",
                           schedule_reconcile=lambda: calls.append("schedule_reconcile"),
                           reconcile=reconcile, reconcile_within=reconcile_within,
                           move_to_new_address=move, schedule_deliveries=lambda: None,
                           pairing_onion=lambda: bytes([7]) * 32,
                           spawn=lambda coro, name: coro.close())
    for mod in (fp, fb, fr, fm):
        monkeypatch.setattr(mod, "_service", lambda: fake)

    async def new_friend(name):
        calls.append(f"announced {name}")

    monkeypatch.setattr(fr, "_new_friend", new_friend)
    async with fp._db() as s:
        await store.set_epoch(s, b"\x01" * 8, "marker")
        for fid_, keys, onion, name in (("fthem", them, ONION_A, "The Smiths"),
                                        ("fother", other, ONION_B, "The Joneses")):
            await store.insert_friend(
                s, id=fid_, house_key=keys.house_key, session_key=keys.session_key,
                onion_key=onion, statement=keys.address_statement(onion, 100).to_bytes(),
                statement_at=100, reach_public=b"\x01" * 32, reach_private=b"\x02" * 32,
                local_name=name, code="1234", added_by="t")
    yield SimpleNamespace(us=us, them=them, other=other, calls=calls, tmp=tmp_path)


async def _friend(house_key: bytes):
    async with fp._db() as s:
        return await store.friend_by_house(s, house_key)


# ─── presence ─────────────────────────────────────────────────────────────


async def test_heard_from_means_online(house) -> None:
    assert (await fr.list_friends())[0]["online"] is False
    await fp.saw(house.them.house_key)
    friends = {f["name"]: f for f in await fr.list_friends()}
    assert friends["The Smiths"]["online"] is True and friends["The Smiths"]["last_seen_at"]
    assert friends["The Joneses"]["online"] is False
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=fp.ONLINE_WITHIN_SEC + 5)
    assert not fp.online(long_ago)


async def test_a_newer_statement_moves_the_friend_and_an_older_one_doesnt(house) -> None:
    new_onion = bytes([9]) * 32
    await fp.saw(house.them.house_key, house.them.address_statement(new_onion, 200).to_bytes())
    assert (await _friend(house.them.house_key)).onion_key == new_onion
    assert "schedule_reconcile" in house.calls  # Tor's key file names the onion
    await fp.saw(house.them.house_key, house.them.address_statement(ONION_A, 150).to_bytes())
    assert (await _friend(house.them.house_key)).onion_key == new_onion


async def test_a_statement_from_another_house_is_ignored(house) -> None:
    forged = house.other.address_statement(bytes([9]) * 32, 999)
    await fp.saw(house.them.house_key, forged)
    assert (await _friend(house.them.house_key)).onion_key == ONION_A


async def test_the_check_pings_quiet_friends_and_tells_them_a_new_address(house, monkeypatch) -> None:
    async with fp._db() as s:
        await s.execute(text("UPDATE friend_houses_friends SET announce_needed = TRUE"))
    pinged: list[bytes] = []

    class _Sess:
        def __init__(self, friend):
            self.friend = friend
            self.peer = SimpleNamespace(statement=None)

        async def ping(self, timeout=45):
            pinged.append(self.friend.house_key)
            return 0.1

        async def close(self):
            pass

    async def open_session(socks, *, onion, keys, me, friend, timeout=0):
        if friend.house_key == house.other.house_key:
            raise fc.Unreachable("timed out")
        return _Sess(friend)

    monkeypatch.setattr(fc, "open_session", open_session)
    assert await fp.check_all() == 1
    assert pinged == [house.them.house_key]
    async with fp._db() as s:
        flags = dict((bytes(r[0]), r[1]) for r in await s.execute(text(
            "SELECT house_key, announce_needed FROM friend_houses_friends")))
    assert flags[house.them.house_key] is False   # reached: told
    assert flags[house.other.house_key] is True   # offline: still to tell
    pinged.clear()
    await fp.check_all()
    assert pinged == []  # heard from just now, nothing waiting for it


# ─── blocking ─────────────────────────────────────────────────────────────


async def _a_message_from(house) -> None:
    from domovoi.friend_houses import envelope as fe

    env = fe.text_message(house.them, to=house.us.house_key, text="Hello", epoch=b"\x09" * 8, seq=1,
                          sent_at=int(time.time()))
    assert await fm.on_message({"event": "message", "house": house.them.house_key.hex(),
                                "envelope": env.to_bytes().hex()})


async def test_blocking_removes_keeps_the_messages_and_moves(house) -> None:
    await _a_message_from(house)
    out = await fb.block("fthem")
    assert out == {"blocked": True, "new_address": True, "messages_deleted": 0}
    assert "move" in house.calls
    assert [f["name"] for f in await fr.list_friends()] == ["The Joneses"]
    assert (await fb.blocked())[0]["name"] == "The Smiths"
    convo = await fm.conversations()
    assert convo[0]["name"] == "The Smiths" and convo[0]["friend_id"] is None  # kept
    async with fp._db() as s:
        need = (await s.execute(text(
            "SELECT announce_needed FROM friend_houses_friends WHERE id = 'fother'"))).scalar_one()
    assert need is True  # the remaining friend will be told the new address


async def test_blocking_can_delete_the_conversation(house) -> None:
    await _a_message_from(house)
    out = await fb.block("fthem", keep_messages=False)
    assert out["messages_deleted"] == 1 and await fm.conversations() == []


async def test_a_blocked_house_cant_ask_again(house) -> None:
    await fb.block("fthem")
    made = await fr.make_invite()
    statement = house.them.address_statement(ONION_A, 300)
    await fr.on_request({"event": "request", "invite": made["id"], "statement": statement.to_bytes().hex(),
                         "reach_private": (b"\x09" * 32).hex(), "name": "Smiths", "code": "111111"})
    assert await fr.list_requests() == []
    assert len(await fr.list_invites()) == 1  # not used up by a blocked house


async def test_this_house_wont_send_a_blocked_house_a_request(house) -> None:
    await fb.block("fthem")
    their_file = ff.make(house.them, pairing_onion=bytes([7]) * 32, invite_id=b"\x33" * 16,
                         expires_day=int(time.time() // 86400) + 5, name="The Smiths").to_text()
    assert (await fr.preview(their_file))["blocked"] is True
    with pytest.raises(fr.FriendingError, match="blocked"):
        await fr.send_request(their_file, local_name="Smiths")


async def test_unblocking(house) -> None:
    await fb.block("fthem")
    assert await fb.unblock(house.them.house_key.hex())
    assert await fb.blocked() == []
    assert not await fb.unblock(house.them.house_key.hex())
    assert not await fb.unblock("not hex")


async def test_blocking_an_unknown_friend(house) -> None:
    with pytest.raises(fb.BlockError):
        await fb.block("nobody")


# ─── audit R6 FH-13: one onion, one friend ───────────────────────────────


async def test_fh13_a_friend_cant_take_another_friends_onion(house) -> None:
    await fp.saw(house.them.house_key, house.them.address_statement(ONION_B, 500).to_bytes())
    assert (await _friend(house.them.house_key)).onion_key == ONION_A  # the Joneses' onion: ignored
    assert "schedule_reconcile" not in house.calls


async def test_fh13_the_database_refuses_two_friends_on_one_onion(house) -> None:
    from sqlalchemy.exc import IntegrityError

    extra = fid.create(house.tmp / "extra" / "house-keys.json")
    with pytest.raises(IntegrityError):
        async with fp._db() as s:
            await store.insert_friend(
                s, id="fextra", house_key=extra.house_key, session_key=extra.session_key,
                onion_key=ONION_A, statement=extra.address_statement(ONION_A, 100).to_bytes(),
                statement_at=100, reach_public=b"\x01" * 32, reach_private=b"\x02" * 32,
                local_name="Extra", code="1234", added_by="t")
