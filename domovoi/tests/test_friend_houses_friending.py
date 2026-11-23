"""Friending end to end in the core (domovoi/friend_houses/friending.py),
against the test database, with Tor and the service faked.

Each test plays one house. "A" makes friend files and accepts; "B" imports
a file, asks, and is accepted. The other house is a second key file, so the
signatures, codes and keys are real.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from domovoi.friend_houses import connect as fc
from domovoi.friend_houses import friend_file as ff
from domovoi.friend_houses import friending as fr
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import pairing as fp
from domovoi.friend_houses import session as fs
from domovoi.tests.conftest import requires_db
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)

pytestmark = requires_db

PAIRING = bytes([7]) * 32
ONION = bytes([8]) * 32


@pytest.fixture
async def house(tmp_path, monkeypatch, db_session):
    from domovoi import egress
    from domovoi.config import settings

    await db_session.execute(text("DELETE FROM household_settings WHERE key = 'friend_houses'"))
    await db_session.commit()
    keys_path = tmp_path / "us" / "house-keys.json"
    monkeypatch.setattr(fid, "keys_path", lambda: keys_path)
    monkeypatch.setattr(settings, "friend_houses_enabled", True, raising=False)
    monkeypatch.setattr(egress, "policy", lambda: "always")
    calls: list[str] = []
    announced: list[str] = []

    async def reconcile_within(timeout=60.0):
        calls.append("reconcile")
        return True

    async def reconcile():
        calls.append("reconcile")

    async def my_hello():
        keys = fid.load()
        return fs.Hello(keys.address_statement(ONION, int(time.time())), b"\x01" * 8)

    fake = SimpleNamespace(
        reconcile_within=reconcile_within, reconcile=reconcile,
        schedule_deliveries=lambda: calls.append("deliver"),
        pairing_onion=lambda: PAIRING, my_hello=my_hello, socks_path=lambda: tmp_path / "socks",
    )
    monkeypatch.setattr(fr, "_service", lambda: fake)

    async def new_friend(name):
        announced.append(name)

    monkeypatch.setattr(fr, "_new_friend", new_friend)
    yield SimpleNamespace(calls=calls, announced=announced, tmp=tmp_path)
    await db_session.execute(text("DELETE FROM household_settings WHERE key = 'friend_houses'"))
    await db_session.commit()


def _other(tmp_path, name: str) -> fid.HouseKeys:
    return fid.create(tmp_path / name / "house-keys.json")


# ─── A: friend files ──────────────────────────────────────────────────────


async def test_off_means_no_friend_files(house, monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "friend_houses_enabled", False, raising=False)
    with pytest.raises(fr.FriendingError, match="off"):
        await fr.make_invite()


async def test_never_means_no_friend_files(house, monkeypatch) -> None:
    from domovoi import egress

    monkeypatch.setattr(egress, "policy", lambda: "never")
    with pytest.raises(fr.FriendingError, match="never"):
        await fr.make_invite()


async def test_a_friend_file_uses_the_household_default_or_a_chosen_length(house) -> None:
    made = await fr.make_invite(label="for the Smiths")
    f = ff.parse(made["file"]["text"])
    assert f.pairing_onion == PAIRING and f.invite_id.hex() == made["id"]
    assert f.house_key == fid.load().house_key
    assert made["file"]["filename"].endswith(".domovoi-friend")
    days = lambda m: round((ff.parse(m["file"]["text"]).expires_day * 86400 - time.time()) / 86400)  # noqa: E731
    assert days(made) in (7, 8)          # the default, rounded up to a whole day
    assert await fr.set_invite_days_default(30) == 30
    assert days(await fr.make_invite()) in (30, 31)
    assert days(await fr.make_invite(days=1)) in (1, 2)
    for bad in (0, 31, "x"):
        with pytest.raises(fr.FriendingError, match="between 1 and 30"):
            await fr.make_invite(days=bad)


async def test_no_more_than_ten_open_friend_files(house) -> None:
    for _ in range(fr.MAX_OPEN_INVITES):
        await fr.make_invite()
    with pytest.raises(fr.FriendingError, match="Cancel one"):
        await fr.make_invite()
    first = (await fr.list_invites())[0]["id"]
    assert await fr.cancel_invite(first)
    await fr.make_invite()


async def test_the_house_name_goes_in_the_file(house) -> None:
    await fr.set_house_name("The Smiths‮")
    made = await fr.make_invite()
    assert ff.parse(made["file"]["text"]).name == "The Smiths"


# ─── A: a request arrives, is accepted ────────────────────────────────────


def _request_event(invite_id: str, asker: fid.HouseKeys, code: str = "1234") -> dict:
    return {"event": "request", "invite": invite_id,
            "statement": asker.address_statement(bytes([5]) * 32, int(time.time())).to_bytes().hex(),
            "reach_private": (b"\x09" * 32).hex(), "name": "The Patels", "code": code}


async def test_a_request_uses_the_file_up_once(house) -> None:
    made = await fr.make_invite()
    b = _other(house.tmp, "b")
    await fr.on_request(_request_event(made["id"], b))
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "c")))  # same file again
    reqs = await fr.list_requests()
    assert len(reqs) == 1 and reqs[0]["direction"] == "in" and reqs[0]["state"] == "waiting"
    assert reqs[0]["their_name"] == "The Patels" and reqs[0]["code"] == "1234"
    assert await fr.list_invites() == []
    with pytest.raises(fr.FriendingError):
        await fr.invite_with_file(made["id"])


async def test_a_request_on_a_cancelled_file_leaves_nothing(house) -> None:
    made = await fr.make_invite()
    await fr.cancel_invite(made["id"])
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "b")))
    assert await fr.list_requests() == []


async def test_a_malformed_event_is_dropped(house) -> None:
    made = await fr.make_invite()
    event = _request_event(made["id"], _other(house.tmp, "b"))
    # Flip a bit of the signature: always broken. (Writing "00" over the last
    # byte left it valid whenever that byte already was 00: 1 run in 256 that
    # stored a real request, the CI flake of 2026-11-17.)
    raw = bytearray.fromhex(event["statement"])
    raw[-1] ^= 0x01
    event["statement"] = raw.hex()
    await fr.on_request(event)
    await fr.on_request({"event": "request"})
    assert await fr.list_requests() == []
    assert len(await fr.list_invites()) == 1  # not used up by junk


async def test_declining_stores_nothing_and_tells_nobody(house) -> None:
    made = await fr.make_invite()
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "b")))
    req = (await fr.list_requests())[0]
    assert await fr.decline(req["id"])
    assert await fr.list_requests() == [] and await fr.list_friends() == []
    assert house.announced == []


async def test_accepting_adds_the_friend_announces_it_and_delivers(house, monkeypatch) -> None:
    made = await fr.make_invite()
    b = _other(house.tmp, "b")
    await fr.on_request(_request_event(made["id"], b, code="0042"))
    req = (await fr.list_requests())[0]
    result = await fr.accept(req["id"], local_name="Patels")
    assert house.announced == ["Patels"]
    friends = await fr.list_friends()
    assert friends[0]["name"] == "Patels" and friends[0]["code"] == "0042"
    assert friends[0]["verified"] is False  # "Not verified" until compared
    assert friends[0]["id"] == result["friend_id"]

    sent: list = []

    class _Sess:
        async def send(self, kind, body=b""):
            sent.append((kind, body))

        async def recv(self, timeout=60):
            return fs.Kind.ACCEPT_OK, b""

        async def close(self):
            sent.append("closed")

    async def open_session(socks, *, onion, keys, me, friend, timeout=0):
        assert friend.house_key == b.house_key and onion == fid.onion_address(bytes([5]) * 32)
        return _Sess()

    monkeypatch.setattr(fc, "open_session", open_session)
    await fr.deliver_pending()
    assert sent[0][0] is fs.Kind.ACCEPT and len(sent[0][1]) == 32
    assert await fr.list_requests() == []  # delivered: the gift is gone with the row
    assert await fr.mark_verified(result["friend_id"])
    assert (await fr.list_friends())[0]["verified"] is True


async def test_an_unreachable_friend_keeps_the_accept_for_later(house, monkeypatch) -> None:
    made = await fr.make_invite()
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "b")))
    await fr.accept((await fr.list_requests())[0]["id"], local_name="Patels")

    async def unreachable(*a, **k):
        raise fc.Unreachable("timed out reaching the friend house")

    monkeypatch.setattr(fc, "open_session", unreachable)
    await fr.deliver_pending()
    req = (await fr.list_requests())[0]
    assert req["state"] == "accepted" and "timed out" in req["last_error"]


@pytest.mark.parametrize("name", ["", "   ", "‮"])
async def test_a_friend_needs_a_name(house, name) -> None:
    made = await fr.make_invite()
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "b")))
    with pytest.raises(fr.FriendingError, match="name"):
        await fr.accept((await fr.list_requests())[0]["id"], local_name=name)


# ─── B: importing a file, asking, being accepted ──────────────────────────


def _their_file(tmp_path, *, days=7, name="The Smiths") -> tuple[fid.HouseKeys, str]:
    a = _other(tmp_path, "a")
    f = ff.make(a, pairing_onion=PAIRING, invite_id=b"\x33" * 16,
                expires_day=int(time.time() // 86400) + days, name=name)
    return a, f.to_text()


async def test_preview_says_what_the_file_is_without_sending(house) -> None:
    a, text_ = _their_file(house.tmp)
    seen = await fr.preview(text_)
    assert seen["name"] == "The Smiths" and seen["fingerprint"] == fid.fingerprint(a.house_key)
    assert not seen["expired"] and not seen["already_friends"]
    assert await fr.list_requests() == []


async def test_own_and_expired_files_are_refused(house) -> None:
    fid.create()
    own = ff.make(fid.load(), pairing_onion=PAIRING, invite_id=b"\x01" * 16,
                  expires_day=int(time.time() // 86400) + 3, name="Us")
    with pytest.raises(fr.FriendingError, match="own"):
        await fr.send_request(own.to_text(), local_name="Us")
    _, old = _their_file(house.tmp, days=-2)
    with pytest.raises(fr.FriendingError, match="expired"):
        await fr.send_request(old, local_name="Smiths")


async def test_asking_then_being_accepted(house, monkeypatch) -> None:
    a, text_ = _their_file(house.tmp)
    req = await fr.send_request(text_, local_name="Smiths")
    assert req["state"] == "unsent" and "deliver" in house.calls
    with pytest.raises(fr.FriendingError, match="already asked"):
        await fr.send_request(text_, local_name="Smiths")

    async def open_stream(socks, *, onion, timeout=0):
        assert onion == fid.onion_address(PAIRING)
        return None, SimpleNamespace(close=lambda: None)

    async def ask(r, w, *, file, keys, statement, reach_private, name):
        assert statement.house_key == keys.house_key and len(reach_private) == 32
        return "5678"

    monkeypatch.setattr(fc, "open_stream", open_stream)
    monkeypatch.setattr(fp, "ask", ask)
    await fr.deliver_pending()
    sent = (await fr.list_requests())[0]
    assert sent["state"] == "sent" and sent["code"] == "5678"

    # A's accept arrives (the listener checked the session; core checks the match).
    statement = a.address_statement(ONION, int(time.time()))
    await fr.on_accepted({"event": "accepted", "house": a.house_key.hex(),
                          "statement": statement.to_bytes().hex(),
                          "reach_private": (b"\x44" * 32).hex()})
    friends = await fr.list_friends()
    assert [f["name"] for f in friends] == ["Smiths"] and friends[0]["code"] == "5678"
    assert house.announced == ["Smiths"]
    assert await fr.list_requests() == []
    # A repeat accept (our ACCEPT_OK was lost) changes nothing.
    await fr.on_accepted({"event": "accepted", "house": a.house_key.hex(),
                          "statement": statement.to_bytes().hex(),
                          "reach_private": (b"\x44" * 32).hex()})
    assert len(await fr.list_friends()) == 1


async def test_an_accept_nobody_asked_for_is_dropped(house) -> None:
    stranger = _other(house.tmp, "x")
    await fr.on_accepted({"event": "accepted", "house": stranger.house_key.hex(),
                          "statement": stranger.address_statement(ONION, 1).to_bytes().hex(),
                          "reach_private": (b"\x44" * 32).hex()})
    assert await fr.list_friends() == [] and house.announced == []


async def test_unfriending_removes_the_friend(house, monkeypatch) -> None:
    made = await fr.make_invite()
    await fr.on_request(_request_event(made["id"], _other(house.tmp, "b")))
    result = await fr.accept((await fr.list_requests())[0]["id"], local_name="Patels")
    assert await fr.unfriend(result["friend_id"])
    assert await fr.list_friends() == []


# ─── audit R6 FH-11: friending state holes ───────────────────────────────


async def _a_friend(keys: fid.HouseKeys, fid_: str = "fv") -> None:
    from domovoi.friend_houses import store

    async with fr._db() as s:
        await store.insert_friend(
            s, id=fid_, house_key=keys.house_key, session_key=keys.session_key, onion_key=ONION,
            statement=keys.address_statement(ONION, 100).to_bytes(), statement_at=100,
            reach_public=b"\x01" * 32, reach_private=b"\x02" * 32, local_name="V", code="1234",
            added_by="t")


def _file_of(keys: fid.HouseKeys, name="X") -> str:
    return ff.make(keys, pairing_onion=PAIRING, invite_id=b"\x44" * 16,
                   expires_day=int(time.time() // 86400) + 5, name=name).to_text()


async def test_fh11_a_file_naming_a_friends_session_key_is_refused(house) -> None:
    # The PoC: X's file carries friend V's public session key; importing it
    # replaced V's entry in the listener's list and locked V out.
    fid.create(fid.keys_path())
    v = _other(house.tmp, "v")
    await _a_friend(v)
    x = _other(house.tmp, "x")
    x_with_vs_key = fid.HouseKeys(house_seed=x.house_seed, session_private=v.session_private)
    with pytest.raises(fr.FriendingError, match="clashes"):
        await fr.send_request(_file_of(x_with_vs_key), local_name="X")
    assert await fr.list_requests() == []


async def test_fh11_a_request_this_house_sent_can_be_taken_back(house) -> None:
    fid.create(fid.keys_path())
    x = _other(house.tmp, "x")
    made = await fr.send_request(_file_of(x), local_name="X")
    house.calls.clear()
    assert await fr.withdraw(made["id"])
    assert await fr.list_requests() == [] and "reconcile" in house.calls
    assert not await fr.withdraw(made["id"])


async def test_fh11_removing_a_friend_clears_its_requests(house) -> None:
    from domovoi.friend_houses import store

    fid.create(fid.keys_path())
    v = _other(house.tmp, "v")
    await _a_friend(v)
    async with fr._db() as s:
        await store.insert_request(
            s, id="rleft", direction="in", state="accepted", house_key=v.house_key,
            session_key=v.session_key, their_name="V", local_name="V", days=5)
    assert await fr.unfriend("fv")
    assert await fr.list_requests() == []


async def test_fh8_a_new_friend_in_quiet_hours_is_said_when_they_end(house, monkeypatch) -> None:
    # Audit R6 FH-8: the announcement was a notice, and quiet hours (on by
    # default, 22:00-07:00) dropped it for good.
    from domovoi.friend_houses import messages

    async def phrases():
        return ["hey jarvis"]

    monkeypatch.setattr(messages, "wake_phrases", phrases)
    said: list[str] = []

    async def speak(text_):
        said.append(text_)
        return True

    monkeypatch.setattr(fr, "_speak", speak)
    quiet = {"now": True}
    monkeypatch.setattr(fr, "_house_quiet_now", lambda: quiet["now"])
    await fr.announce("The Smiths are now a Friend House.")
    assert said == []
    assert await fr.announce_held() == 0  # still quiet
    quiet["now"] = False
    assert await fr.announce_held() == 1 and said == ["The Smiths are now a Friend House."]
    assert await fr.announce_held() == 0


async def test_an_announcement_no_room_heard_waits_and_is_tried_again(house, monkeypatch) -> None:
    rooms: list[str] = []

    class _Speech:
        def __init__(self, slug):
            pass

        async def announce(self, room, said):
            return list(rooms)

    monkeypatch.setattr("domovoi.sdk.speech.SpeechAPI", _Speech)
    monkeypatch.setattr(fr, "_house_quiet_now", lambda: False)
    await fr.announce("The Smiths are now a Friend House.")
    assert await fr.announce_held() == 0  # still nobody to hear it
    rooms.append("kitchen")
    assert await fr.announce_held() == 1
    assert await fr.announce_held() == 0


async def test_fh8_the_weeks_changes_name_new_friends(house) -> None:
    fid.create(fid.keys_path())
    v = _other(house.tmp, "v")
    await _a_friend(v)
    changes = await fr.recent_changes()
    assert changes[0]["kind"] == "friend" and changes[0]["friend"] == "V"


async def test_a_request_broken_off_partway_is_not_retried_and_closes_the_file(
        house, monkeypatch) -> None:
    # B: a run that failed after the nonces were out stops the retries.
    _a, text_ = _their_file(house.tmp)
    await fr.send_request(text_, local_name="Smiths")
    tries: list[int] = []

    async def open_stream(socks, *, onion, timeout=0):
        return None, SimpleNamespace(close=lambda: None)

    async def ask(r, w, **kw):
        tries.append(1)
        raise fp.PairingRefused("the other house didn't take the request", after_nonces=True)

    monkeypatch.setattr(fc, "open_stream", open_stream)
    monkeypatch.setattr(fp, "ask", ask)
    await fr.deliver_pending()
    await fr.deliver_pending()
    req = (await fr.list_requests())[0]
    assert tries == [1] and req["trying"] is False and req["last_error"] == fr.STOPPED_ERROR

    # A: the listener's "closed" event closes the file for good.
    made = await fr.make_invite()
    assert await fr.notice() is None
    await fr.on_event({"event": "invite_closed", "invite": made["id"]})
    assert made["id"] not in [i["id"] for i in await fr.list_invites()]


async def test_a_closed_friend_file_leaves_a_notice_until_dismissed_or_replaced(house) -> None:
    from domovoi import main

    made = await fr.make_invite()
    await fr.on_event({"event": "invite_closed", "invite": made["id"]})
    shown = (await main.admin_friend_houses_status())["notice"]
    assert shown["kind"] == "invite_closed" and shown["text"] == fr.INVITE_CLOSED_NOTICE
    await fr.make_invite()  # a new file answers it
    assert await fr.notice() is None
    other = await fr.make_invite()
    await fr.on_event({"event": "invite_closed", "invite": other["id"]})
    assert await fr.notice() is not None
    await main.admin_friend_houses_dismiss_notice()
    assert (await main.admin_friend_houses_status())["notice"] is None
