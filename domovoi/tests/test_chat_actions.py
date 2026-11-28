"""Typed chat does what the house does (1.5.0): ``POST /v1/phone/text``
(domovoi/phone_voice.py ``run_phone_text_turn``) and the typed send's
router hop (web/backend/api/chat.py ``_router_turn``).

The owner typed "Please announce that Alex, Sam and Jo need another shot",
then "Announce that", then "Announce it to the rooms please" into the
dashboard chat; the chat model answered "I'll let them know…" and nothing
was announced: typed chat never reached the router. Pinned here:

* the core's text turn: the same router as the chat mic, the phone's
  session per thread, the reply row (``source = 'typed'``, ``model_role =
  'action'``), the device's person, the private-chats rule, fixed lines for
  a satellite's setting and for media with no player, ``fast_paths_only``
  (no model is ever asked, the chat's own handlers never run);
* the announce sequence ("announce that" and "announce it to the rooms"
  make the last announcement again, never the words "that" or "it to the
  rooms please"), a timer, a list, music;
* the web send: house commands go to the core and its reply is the
  house's; the core down, busy or refusing is a fixed line, never the
  model; plain questions, memory, print, photo search and house facts stay
  the chat's; "try again" is never offered on an action row;
* away chat (web/backend/away_bridge.py) reaches the same hop, as the phone
  its away key proved;
* the chat prompt's no-claims sentence.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, chat_access, phone_voice
from domovoi.chat_access import Viewer
from domovoi.clients import mpd as mpd_mod
from domovoi.clients import ollama
from domovoi.db.session import SessionLocal
from domovoi.handlers.intercom import WHAT_TO_ANNOUNCE_TEXT
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import _db  # noqa: F401 — fixture
from domovoi.tests.away_kit import away_db  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db

PHONE = "android-typed1"
ROOM = "phone-android-typed1"
SHOT = "Please announce that Alex, Sam and Jo need another shot"


# ─── Fixtures ─────────────────────────────────────────────────────────────


class _Satellite:
    """A room's live session: records what it is asked to announce."""

    def __init__(self) -> None:
        self.said: list[str] = []

    async def announce(self, message: str, kind: str = "user") -> None:
        self.said.append(message)


@pytest.fixture
async def own_threads():
    """The chats a test makes are deleted after it: a household-shared one
    left behind would show in every later test's chat list."""
    async with SessionLocal() as s:
        before = (await s.execute(text("SELECT COALESCE(max(id), 0) FROM chat_threads"))).scalar_one()
    yield
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM chat_threads WHERE id > :b"), {"b": before})
        await s.commit()


@pytest.fixture
def open_gate(own_threads):
    core_app.dependency_overrides[admin_auth.require_device_after_setup] = lambda: None
    phone_voice.reset_for_tests()
    yield
    core_app.dependency_overrides.pop(admin_auth.require_device_after_setup, None)
    phone_voice.reset_for_tests()


@pytest.fixture
def rooms(monkeypatch):
    """Two rooms with live satellites, for the intercom."""
    sats = {"kitchen": _Satellite(), "office": _Satellite()}
    monkeypatch.setattr(mpd_mod, "_room_ports", {"kitchen": (6651, 8051), "office": (6652, 8052)})
    monkeypatch.setattr(core_app.state, "active_sessions", dict(sats), raising=False)
    # What the streaming layer keeps on a running core (drop-in reads it).
    monkeypatch.setattr(core_app.state, "satellite_full_duplex", {}, raising=False)
    monkeypatch.setattr(core_app.state, "active_dropins", {}, raising=False)
    return sats


async def _fanned_out() -> None:
    """Let the intercom's background announcements run."""
    for _ in range(5):
        await asyncio.sleep(0)
    if phone_voice._BACKGROUND:
        await asyncio.gather(*list(phone_voice._BACKGROUND), return_exceptions=True)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")


async def _thread(*, owner: str | None = None, shared: bool = True) -> int:
    async with SessionLocal() as s:
        tid = (await s.execute(text("INSERT INTO chat_threads (title) VALUES (NULL) RETURNING id"))).scalar_one()
        if await chat_access.has_private_columns(s):
            await s.execute(text("UPDATE chat_threads SET shared = :sh, owner_device_id = :o WHERE id = :t"),
                            {"sh": shared, "o": owner, "t": tid})
        await s.commit()
    return int(tid)


async def _rows(thread_id: int) -> list[dict[str, Any]]:
    async with SessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT id, role, content, source, stats FROM chat_messages WHERE thread_id = :t ORDER BY id"
        ), {"t": thread_id})).mappings().all()
    return [dict(r) for r in rows]


async def _text(c: AsyncClient, thread_id: int, said: str, **extra: Any) -> dict[str, Any]:
    r = await c.post("/v1/phone/text", json={"text": said, "phone_id": PHONE, "thread_id": thread_id, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _device(device_id: str, *, person: str | None = None, shared_screen: bool = False) -> int | None:
    async with SessionLocal() as s:
        pid = None
        if person is not None:
            pid = (await s.execute(text("INSERT INTO people (name) VALUES (:n) RETURNING id"),
                                   {"n": person})).scalar_one()
        await s.execute(text(
            "INSERT INTO devices (device_id, name, owner_person_id, shared_screen) VALUES (:d, :d, :p, :sc)"
        ), {"d": device_id, "p": pid, "sc": shared_screen})
        await s.commit()
    return pid


# ─── The core's text turn ─────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_the_announce_sequence(db_session, open_gate, rooms) -> None:
    tid = await _thread()
    async with _client() as c:
        first = await _text(c, tid, SHOT)
        await _fanned_out()
        assert first["handled"] is True and first["matched_handler"] == "intercom", first
        assert first["reply"] == "Broadcasting to the house."
        # "that" introduces what is said; it is not said.
        assert rooms["kitchen"].said == ["Alex, Sam and Jo need another shot"]
        assert rooms["office"].said == ["Alex, Sam and Jo need another shot"]

        # A pointer at it makes it again, in this phone's session for the thread.
        again = await _text(c, tid, "Announce that")
        await _fanned_out()
        assert again["matched_handler"] == "intercom" and again["session_id"] == first["session_id"]
        rooms_again = await _text(c, tid, "Announce it to the rooms please")
        await _fanned_out()
        assert rooms_again["reply"] == "Broadcasting to the house."
    for sat in rooms.values():
        assert sat.said == ["Alex, Sam and Jo need another shot"] * 3   # never "that", never "it to the rooms please"

    rows = await _rows(tid)
    assert [r["role"] for r in rows] == ["user", "assistant"] * 3
    assert {r["source"] for r in rows} == {"typed"}
    reply = rows[1]
    assert reply["stats"]["model_role"] == "action"
    assert reply["stats"]["matched_handler"] == "intercom" and reply["stats"]["matched_path"] == "fast"
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT room_id, transcript FROM intents_log ORDER BY id"))).all()
    assert [r[0] for r in logged] == [ROOM] * 3


@requires_db
@pytest.mark.asyncio
async def test_announce_that_with_nothing_announced_asks_what(db_session, open_gate, rooms) -> None:
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, "Announce that")
        await _fanned_out()
        assert body["reply"] == "What should I announce?" == WHAT_TO_ANNOUNCE_TEXT
        assert body["expect_followup"] is True
        assert all(sat.said == [] for sat in rooms.values())
        # The next turn's words are the announcement: typed chat reads it
        # back first, and a yes broadcasts it.
        back = await _text(c, tid, "Dinner is ready")
        await _fanned_out()
        assert back["reply"] == "Announce \u201cDinner is ready\u201d to every room?"
        assert back["expect_followup"] is True and rooms["kitchen"].said == []
        reply = await _text(c, tid, "yes")
    await _fanned_out()
    assert reply["reply"] == "Broadcasting to the house."
    assert (reply["matched_handler"], reply["matched_path"]) == ("intercom", "confirmation")
    assert rooms["kitchen"].said == ["Dinner is ready"]
    assert (await _rows(tid))[1]["stats"]["expect_followup"] is True


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "who is coming tonight?", "what time is it", "How long until dinner", "is that ok?", "x " * 120,
])
async def test_a_question_or_a_long_message_after_the_ask_is_never_broadcast(
    db_session, open_gate, rooms, reply,
) -> None:
    tid = await _thread()
    async with _client() as c:
        await _text(c, tid, "Announce that", fast_paths_only=True)
        body = await _text(c, tid, reply.strip(), fast_paths_only=True)
        later = await _text(c, tid, "yes", fast_paths_only=True)
    await _fanned_out()
    assert all(sat.said == [] for sat in rooms.values())
    assert not body.get("reply", "").startswith("Announce")
    assert later["matched_handler"] != "intercom" or "Broadcasting" not in later["reply"]


@requires_db
@pytest.mark.asyncio
async def test_no_to_the_read_back_announces_nothing(db_session, open_gate, rooms) -> None:
    tid = await _thread()
    async with _client() as c:
        await _text(c, tid, "Announce that")
        await _text(c, tid, "The pizza is here")
        no = await _text(c, tid, "no")
    await _fanned_out()
    assert no["reply"] == "Okay, I won't announce anything."
    assert all(sat.said == [] for sat in rooms.values())


@requires_db
@pytest.mark.asyncio
async def test_announce_that_reads_the_persons_request_never_an_old_pretend_reply(
    db_session, open_gate, rooms,
) -> None:
    """A thread from before typed chat reached the house: the request was
    answered by the model pretending. "Announce that" announces the
    person's own words, never the reply's."""
    tid = await _thread()
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', :q), "
            "(:t, 'assistant', 'I''ll let them know. Alex, Sam and Jo, you two need another round.')"
        ), {"t": tid, "q": SHOT})
        await s.commit()
    async with _client() as c:
        # A keyless caller, as the web names one: its rows have no device.
        body = await _text(c, tid, "Announce that", phone_id=f"chat-{tid}")
    await _fanned_out()
    assert body["reply"] == "Broadcasting to the house."
    assert rooms["kitchen"].said == ["Alex, Sam and Jo need another shot"]


@requires_db
@pytest.mark.asyncio
async def test_drop_in_from_typed_chat_is_the_mics_answer(db_session, open_gate, rooms) -> None:
    """A phone is no room: the mic answers "drop in on the kitchen" with the
    drop-in handler's own refusal, and typed chat gives the same words."""
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, "Drop in on the kitchen")
    assert body["matched_handler"] == "dropin"
    assert body["reply"] == "I can't tell which room you're in."


# ─── N20: what the intercom announces, every way in ───────────────────────


async def _say(said: str, *, room: str = "kitchen", session_id: Any = None, thread_id: int | None = None,
               phone: bool = False, person_id: int | None = None) -> Any:
    """One turn through the router, as a satellite (or the chat mic) routes it."""
    from domovoi.models import Context, Intent
    from domovoi.router import route

    ctx = Context(room_id=room, session_id=session_id, online=True, app=core_app, person_id=person_id,
                  device_kind="phone" if phone else None, chat_thread_id=thread_id,
                  trigger="push_to_talk" if phone else "wake_word")
    async with SessionLocal() as s:
        response = await route(Intent(transcript=said, room_id=room, session_id=session_id), ctx, s)
        await s.commit()
    return response


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(("said", "announced"), [
    ("Broadcast that dinner's ready", "dinner's ready"),
    ("Please announce that Alex, Sam and Jo need another shot", "Alex, Sam and Jo need another shot"),
    ("Announce Dinner is ready to everyone please", "Dinner is ready"),
    ("announce that the car is here, please.", "the car is here"),
    ("Tell everyone that Jo is home", "Jo is home"),
])
async def test_a_satellite_announces_the_message_as_said(db_session, rooms, said, announced) -> None:
    response = await _say(said)
    assert response.matched_handler == "intercom"
    assert response.announce_text == announced
    assert response.text == "Broadcasting to the house."


@requires_db
@pytest.mark.asyncio
async def test_a_satellite_repeats_on_a_pointer_and_asks_when_there_is_none(db_session, rooms) -> None:
    first = await _say("Please announce that Alex, Sam and Jo need another shot")
    sid = first.session_id
    again = await _say("Announce that", session_id=sid)
    rooms_ = await _say("Announce it to the rooms please", session_id=sid)
    for r in (again, rooms_):
        assert r.announce_text == "Alex, Sam and Jo need another shot"
        assert sorted(r.announce_to_rooms) == ["kitchen", "office"]

    fresh = await _say("Announce that", room="office")
    assert fresh.text == "What should I announce?" and fresh.expect_followup is True
    assert not fresh.announce_text
    answered = await _say("The pizza is here", room="office", session_id=fresh.session_id)
    assert answered.announce_text == "The pizza is here"
    # "never mind" takes the question back, and announces nothing.
    asked = await _say("announce it", room="office", session_id=None)
    dropped = await _say("never mind", room="office", session_id=asked.session_id)
    assert not dropped.announce_text and dropped.matched_handler == "intercom"
    # A command in reply is routed as one, never read out.
    asked = await _say("announce that", room="office", session_id=None)
    routed = await _say("what time is it", room="office", session_id=asked.session_id)
    assert routed.matched_handler == "clock" and not routed.announce_text


@requires_db
@pytest.mark.asyncio
async def test_an_old_announcement_is_not_repeated(db_session, rooms, monkeypatch) -> None:
    from domovoi.handlers import intercom

    first = await _say("announce dinner is ready")
    monkeypatch.setattr(intercom, "ANNOUNCE_AGAIN_WINDOW_SEC", -1.0)
    later = await _say("Announce that", session_id=first.session_id)
    assert later.text == "What should I announce?" and not later.announce_text


@requires_db
@pytest.mark.asyncio
async def test_the_chat_mic_announces_the_message_as_said(db_session, open_gate, rooms, monkeypatch) -> None:
    """The chat mic (/v1/phone/voice): the same cleaning, the same pointer."""
    from types import SimpleNamespace as NS

    import domovoi.voice_identifier as vi

    heard = ["Please announce that Alex, Sam and Jo need another shot.", "Announce it to the rooms, please."]

    class _Whisper:
        async def transcribe(self, pcm: bytes) -> str:
            return heard.pop(0)

    async def _identify(pcm, **kw):
        return NS(person_id=None, presence_tier="low", embedding=None, best_similarity=None,
                  denylisted=False, runner_up_similarity=None, ambiguous=False)

    monkeypatch.setattr(phone_voice, "get_whisper_client", lambda: _Whisper())
    monkeypatch.setattr(vi, "identify", _identify)
    pcm = (b"\x10\x00" * 16_000)
    tid = await _thread()
    async with _client() as c:
        for _ in range(2):
            r = await c.post("/v1/phone/voice", data={"phone_id": PHONE, "thread_id": str(tid), "speak": "false"},
                             files={"audio": ("t.pcm", pcm, "application/octet-stream")})
            assert r.status_code == 200, r.text
            assert r.json()["reply"] == "Broadcasting to the house."
    await _fanned_out()
    assert rooms["kitchen"].said == ["Alex, Sam and Jo need another shot"] * 2


@requires_db
@pytest.mark.asyncio
async def test_a_timer_and_a_list_from_typed_chat(db_session, open_gate) -> None:
    async with SessionLocal() as s:   # the two lists a new house starts with (V048)
        await s.execute(text("INSERT INTO lists (name, name_key, position) VALUES "
                             "('To-do', 'todo', 1), ('Shopping', 'shopping', 2)"))
        await s.commit()
    tid = await _thread()
    async with _client() as c:
        timer = await _text(c, tid, "set a timer for 5 minutes")
        listed = await _text(c, tid, "add milk to the shopping list")
    assert timer["matched_handler"] == "timer", timer
    assert listed["matched_handler"] == "lists", listed
    async with SessionLocal() as s:
        rooms_ = (await s.execute(text("SELECT room_id FROM timers"))).scalars().all()
        items = (await s.execute(text("SELECT text FROM list_items"))).scalars().all()
    assert rooms_ == [None]   # house-wide, as from the chat mic
    assert any("milk" in (i or "").lower() for i in items)


async def _a_song() -> None:
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO library_tracks (file_path, title, artist, album) "
            "VALUES ('/music/glass-harbor.mp3', 'Glass Harbor', 'Velvet Kites', 'Tide')"
        ))
        await s.commit()
    from domovoi.handlers.shared import library_match

    library_match.reset_for_tests()


@requires_db
@pytest.mark.asyncio
async def test_music_with_no_player_is_said_plainly_and_a_player_gets_the_actions(db_session, open_gate) -> None:
    await _a_song()
    tid = await _thread()
    async with _client() as c:
        web = await _text(c, tid, "play glass harbor")
        app = await _text(c, tid, "play glass harbor", plays_here=True)
    assert web["matched_handler"] == "music", web
    assert web["reply"] == phone_voice.CHAT_NO_PLAYER_TEXT and web["actions"] == []
    assert app["reply"].startswith("Playing") and app["actions"][0]["type"] == "play"
    rows = await _rows(tid)
    assert rows[1]["content"] == phone_voice.CHAT_NO_PLAYER_TEXT
    assert rows[1]["stats"]["fixed_reply"] == "no_player" and rows[1]["stats"]["actions_dropped"] == ["play"]


@requires_db
@pytest.mark.asyncio
async def test_a_satellite_setting_is_the_fixed_line(db_session, open_gate) -> None:
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, "fix the wifi")
    assert body["matched_handler"] == "wifi"
    assert body["reply"] == phone_voice.CHAT_ROOM_SETTING_TEXT
    assert body["reply"].startswith("I can't do that from chat yet.")


@requires_db
@pytest.mark.asyncio
async def test_fast_paths_only_leaves_a_question_to_the_chat_and_writes_nothing(db_session, open_gate) -> None:
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, "write me a poem about cats", fast_paths_only=True)
        answered = await _text(c, tid, "write me a poem about cats")
    assert body["handled"] is False and body["messages"] == [] and body["matched_path"] == "deferred"
    # Without it the house's own Q&A answers, as for the mic.
    assert answered["handled"] is True and answered["matched_path"] == "qa"
    assert len(await _rows(tid)) == 2
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT count(*) FROM intents_log"))).scalar_one()
    assert logged == 1


@requires_db
@pytest.mark.asyncio
async def test_a_row_already_written_gets_one_reply_only(db_session, open_gate) -> None:
    tid = await _thread()
    async with SessionLocal() as s:
        uid = (await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content) VALUES (:t, 'user', 'what time is it') RETURNING id"
        ), {"t": tid})).scalar_one()
        await s.commit()
    async with _client() as c:
        body = await _text(c, tid, "what time is it", user_message_id=uid)
        twice = await c.post("/v1/phone/text", json={"text": "what time is it", "phone_id": PHONE,
                                                     "thread_id": tid, "user_message_id": uid})
        other = await c.post("/v1/phone/text", json={"text": "set a timer", "phone_id": PHONE,
                                                     "thread_id": tid, "user_message_id": uid})
    assert body["matched_handler"] == "clock" and body["messages"][0]["id"] == uid
    assert twice.status_code == 409 and twice.json()["code"] == "already_answered"
    assert other.status_code == 404
    assert [r["role"] for r in await _rows(tid)] == ["user", "assistant"]


@requires_db
@pytest.mark.asyncio
async def test_yes_answers_the_question_the_last_typed_turn_parked(db_session, open_gate) -> None:
    from domovoi.confirmations import request_confirmation

    tid = await _thread()
    async with _client() as c:
        first = await _text(c, tid, "what time is it")
        async with SessionLocal() as s:
            await request_confirmation(s, uuid.UUID(first["session_id"]), kind="core.pending_memory_offer",
                                       handler="memory", data={"memory_id": 424242})
            await s.commit()
        second = await _text(c, tid, "yes")
    assert second["session_id"] == first["session_id"]
    assert (second["matched_path"], second["matched_handler"]) == ("confirmation", "memory")


@requires_db
@pytest.mark.asyncio
async def test_the_turn_is_the_devices_person_and_a_guest_device_is_nobodys(db_session, open_gate) -> None:
    """Credited as the mic credits a voice: the person the device KEY's
    device belongs to; a device nobody owns (a guest's) or a shared screen
    is nobody, as a voice the mic doesn't recognise."""
    owned, guest, screen = f"android-{uuid.uuid4().hex[:8]}", f"android-{uuid.uuid4().hex[:8]}", \
        f"tablet-{uuid.uuid4().hex[:8]}"
    alex = await _device(owned, person="alex")
    await _device(guest)
    await _device(screen, person="sam", shared_screen=True)
    tid = await _thread()
    for device in (owned, guest, screen):
        await phone_voice.run_phone_text_turn(
            core_app, phone_id=device, thread_id=tid, text_="what time is it",
            viewer=Viewer(device_id=device, shared_screen=device == screen),
        )
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT room_id, person_id FROM intents_log ORDER BY id"))).all()
    assert [tuple(r) for r in logged] == [("phone-" + owned, alex), ("phone-" + guest, None),
                                          ("phone-" + screen, None)]
    # A keyless caller can't name a device: a key speaks as itself only.
    with pytest.raises(phone_voice.PhoneTurnRefused) as e:
        await phone_voice.run_phone_text_turn(core_app, phone_id=guest, thread_id=tid, text_="hi",
                                              viewer=Viewer(device_id=owned))
    assert e.value.code == "not_this_phone"


@requires_db
@pytest.mark.asyncio
async def test_a_private_chat_holds(db_session, open_gate) -> None:
    owner, other = f"android-{uuid.uuid4().hex[:8]}", f"android-{uuid.uuid4().hex[:8]}"
    await _device(owner)
    await _device(other)
    tid = await _thread(owner=owner, shared=False)

    async def turn(viewer: Viewer, phone_id: str) -> Any:
        return await phone_voice.run_phone_text_turn(core_app, phone_id=phone_id, thread_id=tid,
                                                     text_="set a timer for 5 minutes", viewer=viewer)

    for viewer, phone_id, code in ((Viewer(device_id=other), other, "thread_not_found"),
                                   (Viewer(), "chat-keyless", "thread_not_found"),
                                   (Viewer(admin=True), "admin-phone", "admin_read_only")):
        with pytest.raises(phone_voice.PhoneTurnRefused) as e:
            await turn(viewer, phone_id)
        assert e.value.code == code
    assert await _rows(tid) == []
    ok = await turn(Viewer(device_id=owner), owner)
    assert ok["matched_handler"] == "timer"


@pytest.mark.asyncio
async def test_only_the_webs_away_hop_can_name_a_device(monkeypatch, tmp_path) -> None:
    """chat_access.bridge_viewer: the device header counts only with the
    core's bridge secret, from loopback; anything else is a 401."""
    from fastapi import HTTPException
    from starlette.requests import Request

    from domovoi.away import keys as away_keys

    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    away_keys.reset_bridge_secret()
    secret = away_keys.bridge_secret()
    assert away_keys.read_bridge_secret() == secret

    def req(headers: dict[str, str], host: str = "127.0.0.1") -> Request:
        return Request({"type": "http", "method": "POST", "path": "/v1/phone/text", "client": (host, 1),
                        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})

    base = Viewer(admin=True)
    assert await chat_access.bridge_viewer(req({}), base) is base
    for headers, host in (
        ({chat_access.AWAY_DEVICE_HEADER: "android-x"}, "127.0.0.1"),
        ({chat_access.AWAY_BRIDGE_HEADER: secret}, "127.0.0.1"),
        ({chat_access.AWAY_BRIDGE_HEADER: "f" * 64, chat_access.AWAY_DEVICE_HEADER: "android-x"}, "127.0.0.1"),
        ({chat_access.AWAY_BRIDGE_HEADER: secret, chat_access.AWAY_DEVICE_HEADER: "android-x"}, "192.168.1.30"),
    ):
        with pytest.raises(HTTPException) as e:
            await chat_access.bridge_viewer(req(headers, host), base)
        assert e.value.status_code == 401
    away_keys.reset_bridge_secret()


# ─── The web's typed send ─────────────────────────────────────────────────


def _sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.split("\n\n"):
        name, data = "message", ""
        for ln in block.split("\n"):
            if ln.startswith("event: "):
                name = ln[7:].strip()
            elif ln.startswith("data: "):
                data += ln[6:]
        if data:
            events.append((name, json.loads(data)))
    return events


def _done(body: str) -> dict[str, Any]:
    return [d for e, d in _sse(body) if e == "done"][-1]


@pytest.fixture
def web(monkeypatch, open_gate):
    """The web's typed send, its router hop answered by the real core app
    in-process (``hop.calls``; ``hop.down`` makes it unreachable,
    ``hop.status`` makes it answer that), its model a stub that records."""
    import web.backend.api.chat as chat_api

    hop = SimpleNamespace(calls=[], down=False, status=None, model_calls=0, timeout=None, before=None,
                          late=[])

    class _Hop:
        async def __aenter__(self):
            self.c = AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test")
            await self.c.__aenter__()
            return self

        async def __aexit__(self, *a):
            await self.c.__aexit__(*a)
            return False

        async def post(self, url, **kw):
            hop.calls.append({"url": url, "json": kw.get("json"), "headers": kw.get("headers") or {}})
            if hop.before is not None:
                await hop.before()
            if hop.down:
                raise httpx.ConnectError("connection refused")
            if hop.status is not None:
                return httpx.Response(hop.status, json={"detail": "x", "code": "busy" if hop.status == 409 else "x"})
            if hop.timeout == "never":
                raise httpx.ReadTimeout("the core took too long")
            if hop.timeout == "late":
                # The core answers a moment after the web gave up on it.
                async def answer_late():
                    await asyncio.sleep(0.2)
                    async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test") as c2:
                        await c2.post(url, **kw)

                hop.late.append(asyncio.create_task(answer_late()))
                raise httpx.ReadTimeout("the core took too long")
            return await self.c.post(url, **kw)

    async def _stream(messages, model, **kw):
        hop.model_calls += 1
        yield "From the model."

    async def _tags(*a, **kw):
        return []

    monkeypatch.setattr(chat_api, "domovoi_url", lambda: "http://test")
    monkeypatch.setattr(chat_api, "_core_client", lambda: _Hop())
    monkeypatch.setattr(chat_api.ollama_client, "chat_stream", _stream)
    monkeypatch.setattr(chat_api.ollama_client, "tags_or_none", _tags)
    monkeypatch.setattr(chat_api, "schedule_title_job", lambda *_a, **_k: None)
    return hop


@pytest.fixture
def tool_model(monkeypatch):
    """Every call the core makes to the tool model (the router's tool
    tier): typed chat must make none."""
    calls: list[str] = []
    real = ollama.OllamaStubClient.route

    async def route(self, transcript, *a, **kw):
        calls.append(transcript)
        return await real(self, transcript, *a, **kw)

    monkeypatch.setattr(ollama.OllamaStubClient, "route", route)
    return SimpleNamespace(calls=calls)


def _web() -> AsyncClient:
    from web.backend.main import app as web_app

    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={"X-Requested-With": "domovoi-tests"})


def _texts(hop) -> list[str]:
    return [c["json"]["text"] for c in hop.calls if c["url"].endswith("/v1/phone/text")]


async def _send(c: AsyncClient, tid: int, content: str) -> dict[str, Any]:
    r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": content})
    assert r.status_code == 200, r.text
    return _done(r.text)


@requires_db
@pytest.mark.asyncio
async def test_the_owners_announce_sequence_from_the_dashboard(db_session, web, rooms) -> None:
    tid = await _thread()
    async with _web() as c:
        first = await _send(c, tid, SHOT)
        second = await _send(c, tid, "Announce that")
        third = await _send(c, tid, "Announce it to the rooms please")
        listed = (await c.get(f"/api/chat/threads/{tid}/messages")).json()["messages"]
    await _fanned_out()
    for done in (first, second, third):
        assert done["content"] == "Broadcasting to the house."
        assert done["stats"]["model_role"] == "action" and done["stats"]["matched_handler"] == "intercom"
        assert done["retryable"] is False and done["source"] == "typed"
    assert web.model_calls == 0
    assert _texts(web) == [SHOT, "Announce that", "Announce it to the rooms please"]
    assert web.calls[0]["json"]["fast_paths_only"] is True and web.calls[0]["json"]["plays_here"] is False
    for sat in rooms.values():
        assert sat.said == ["Alex, Sam and Jo need another shot"] * 3
    # One row each: the typed one (the web's) and the reply (the core's).
    assert [(m["role"], m["content"]) for m in listed][:2] == [("user", SHOT), ("assistant", "Broadcasting to the house.")]
    assert len(listed) == 6


@requires_db
@pytest.mark.asyncio
async def test_a_bare_announce_that_asks_and_the_next_message_is_announced(db_session, web, rooms) -> None:
    tid = await _thread()
    async with _web() as c:
        asked = await _send(c, tid, "Announce that")
        told = await _send(c, tid, "Dinner is ready")   # no command words: the follow-up takes it there
        await _fanned_out()
        assert rooms["kitchen"].said == []
        done = await _send(c, tid, "yes")
    await _fanned_out()
    assert asked["content"] == "What should I announce?"
    assert told["content"] == "Announce \u201cDinner is ready\u201d to every room?"
    assert done["content"] == "Broadcasting to the house."
    assert rooms["kitchen"].said == ["Dinner is ready"]
    assert web.model_calls == 0


@requires_db
@pytest.mark.asyncio
async def test_try_again_is_never_offered_on_an_action(db_session, web) -> None:
    tid = await _thread()
    async with _web() as c:
        await _send(c, tid, "set a timer for 5 minutes")
        r = await c.post(f"/api/chat/threads/{tid}/retry")
    assert r.status_code == 409 and r.json()["code"] == "not_retryable"


@requires_db
@pytest.mark.asyncio
async def test_a_satellite_setting_from_the_dashboard_is_the_fixed_line(db_session, web) -> None:
    tid = await _thread()
    async with _web() as c:
        done = await _send(c, tid, "fix the wifi")
    assert done["content"] == phone_voice.CHAT_ROOM_SETTING_TEXT and web.model_calls == 0


@requires_db
@pytest.mark.asyncio
async def test_play_from_the_dashboard_plays_nowhere_and_says_so(db_session, web) -> None:
    await _a_song()
    tid = await _thread()
    async with _web() as c:
        done = await _send(c, tid, "play glass harbor")
    assert done["content"] == phone_voice.CHAT_NO_PLAYER_TEXT and web.model_calls == 0


@requires_db
@pytest.mark.asyncio
async def test_the_core_down_busy_or_refusing_is_a_fixed_line_never_the_model(db_session, web) -> None:
    import web.backend.api.chat as chat_api

    tid = await _thread()
    async with _web() as c:
        web.down = True
        down = await _send(c, tid, "set a timer for 5 minutes")
        web.down, web.status = False, 409
        busy = await _send(c, tid, "announce dinner is ready")
        web.status = 403
        refused = await _send(c, tid, "add milk to the shopping list")
    assert down["content"] == chat_api.ROUTER_UNREACHABLE_TEXT
    assert down["content"].startswith("I can't do that from chat yet.")
    assert down["stats"] == {"model_role": "action", "error_code": "core_unreachable"}
    assert busy["content"] == chat_api.ROUTER_BUSY_TEXT and busy["stats"]["error_code"] == "core_busy"
    assert refused["content"] == chat_api.CHAT_CANT_TEXT and refused["stats"]["error_code"] == "core_refused"
    assert web.model_calls == 0
    assert all(not d["retryable"] for d in (down, busy, refused))


@requires_db
@pytest.mark.asyncio
async def test_a_plain_question_still_goes_to_the_model(db_session, web, monkeypatch, tool_model) -> None:
    import web.backend.api.chat as chat_api

    tid = await _thread()
    async with _web() as c:
        world = await _send(c, tid, "what is the capital of france?")
        # The state poll hasn't reached the core: no speculative hop.
        hello = await _send(c, tid, "hello there")
        monkeypatch.setattr(chat_api, "get_cached_snapshot", lambda: {"active_rooms": []})
        # The core is up: it is asked (a plugin's fast path only it knows),
        # takes only its fast tiers, asks no model, and the chat's model
        # answers; nothing of the house's is written.
        poem = await _send(c, tid, "write me a poem about cats")
        web.down = True
        offline = await _send(c, tid, "write me a poem about dogs")
    for done in (world, hello, poem, offline):
        assert done["content"] == "From the model." and done["stats"]["model_role"] == "chat"
    assert _texts(web) == ["write me a poem about cats", "write me a poem about dogs"]
    assert web.model_calls == 4 and tool_model.calls == []
    rows = await _rows(tid)
    assert [r["role"] for r in rows] == ["user", "assistant"] * 4


@requires_db
@pytest.mark.asyncio
async def test_the_chats_own_steps_still_win(db_session, web, monkeypatch) -> None:
    """Memory, house facts, print and the photo search answer before the
    router hop, exactly as before: the core is never asked."""
    import web.backend.api.chat as chat_api
    from web.backend.api import chat_memory

    monkeypatch.setattr(chat_api, "get_cached_snapshot", lambda: {"active_rooms": []})
    tid = await _thread()
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, images) VALUES "
            "(:t, 'user', 'a photo', CAST(:img AS JSONB))"
        ), {"t": tid, "img": json.dumps([{"token": uuid.uuid4().hex, "name": "receipt.jpg"}])})
        await s.commit()
    async with _web() as c:
        memory = await _send(c, tid, "remember that I like tea")
        facts = await _send(c, tid, "do you record me?")
        printed = await _send(c, tid, "print this")
        photos = await _send(c, tid, "show me photos of the dog")
    assert memory["stats"]["model_role"] == chat_memory.MODEL_ROLE
    assert facts["stats"]["model_role"] == "house_facts"
    assert printed["stats"]["model_role"] == "draft" and "receipt.jpg" in printed["content"]
    assert photos["stats"]["model_role"] == "media"
    assert _texts(web) == []


@requires_db
@pytest.mark.asyncio
async def test_a_follow_up_goes_back_to_the_house(db_session, web) -> None:
    """The house asked something back (stats.expect_followup): the next
    typed message answers it there, whatever it says."""
    tid = await _thread()
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, source, stats) VALUES "
            "(:t, 'user', 'play something', 'typed', NULL), "
            "(:t, 'assistant', 'Did you mean the album or the song?', 'typed', "
            "CAST('{\"model_role\": \"action\", \"expect_followup\": true}' AS JSONB))"
        ), {"t": tid})
        await s.commit()
    async with _web() as c:
        await _send(c, tid, "the second one")
        await _send(c, tid, "and how are you")   # the last reply asked nothing: the chat's
    assert _texts(web) == ["the second one"]


# ─── Away chat reaches the same hop ───────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_an_away_send_reaches_the_house_as_its_phone(away_db, monkeypatch, rooms, web) -> None:
    """chat.send "announce dinner is ready" from away: the bridge relays it
    into the home send, whose router hop names the phone its away key
    proved (with the core's bridge secret), and the core announces it in
    the phone's own private chat."""
    from domovoi.away import keys as away_keys
    from domovoi.friend_houses import service as svc
    from domovoi.friend_houses.noise import public_bytes
    from domovoi.tests.auth_testkit import web_client
    from domovoi.tests.away_kit import claimed, enroll, lines, phone
    from web.backend.api import away as away_api

    # The house: claimed, the real core gate (the household token the
    # bridge's request carries is forwarded), away chat on, a phone enrolled.
    core_app.dependency_overrides.pop(admin_auth.require_device_after_setup, None)
    monkeypatch.setattr(core_app.state, "active_sessions", dict(rooms), raising=False)

    async def tell(_request):
        return True

    async def state(_device):
        return "ready"

    monkeypatch.setattr(away_api, "tell_core", tell)
    monkeypatch.setattr(away_api, "core_state", state)
    monkeypatch.setattr(svc, "sys", SimpleNamespace(platform="linux"))
    away_keys.reset_bridge_secret()
    secret = away_keys.bridge_secret()
    try:
        async with web_client() as c:
            base, _admin, _token = await claimed(c)
            p = await phone(c, base)
            await enroll(c, p)
            body = {"device_id": p["id"], "noise_pub": public_bytes(p["noise"]).hex(),
                    "request": {"op": "chat.send", "chat_id": None, "client_id": str(uuid.uuid4()),
                                "text": "announce dinner is ready"},
                    "picture": ""}
            r = await c.post("/api/away/bridge", json=body, headers={**base, away_api.BRIDGE_HEADER: secret})
            assert r.status_code == 200, r.text
            out = lines(r.content)
        await _fanned_out()
        final = out[-1]["b"]
        assert final["type"] == "done", out
        assert final["message"]["content"] == "Broadcasting to the house."
        (call,) = web.calls
        assert call["json"]["phone_id"] == p["id"] and call["json"]["text"] == "announce dinner is ready"
        assert call["headers"][chat_access.AWAY_DEVICE_HEADER] == p["id"]
        assert call["headers"][chat_access.AWAY_BRIDGE_HEADER] == secret
        assert rooms["kitchen"].said == ["dinner is ready"]
        async with SessionLocal() as s:
            owner = (await s.execute(text(
                "SELECT owner_device_id FROM chat_threads t JOIN chat_messages m ON m.thread_id = t.id "
                "WHERE m.content = 'announce dinner is ready'"
            ))).scalar_one()
        assert owner == p["id"]
    finally:
        away_keys.reset_bridge_secret()


# ─── The chat prompt's backstop ───────────────────────────────────────────


def test_the_chat_prompt_never_claims_a_house_action() -> None:
    p = ollama.chat_system_prompt()
    assert "If you were asked to do something in the house, it wasn't done" in p
    assert "never say you did it or will" in p and "saying it in a room" in p
    # No feature named (so it never says it can't set timers or print), and
    # no "Domovoi does those" in the third person.
    assert "timer" not in p.lower() and "does those" not in p
    # The memory and printing sentences stay.
    assert "You can't save or delete memories yourself" in p and "never say you can't print" in p
    assert "something in the house" not in ollama.voice_qa_system_prompt()


# ─── Review round 1: no model from typed chat, prose, guards ──────────────

#: Chit-chat a fast path or the tool model could take for a command: the
#: chat's model answers it, nothing is done, no model is asked by the house.
CHIT_CHAT = [
    "thanks",
    "I'm thinking of announcing my engagement",
    "remind me what we talked about",
    "I need to add milk to my diet",
    "it's too dark in here",
    "delete everything",
    "I'm sad today",            # voice_profile's "I'm <name>": the chat's own
    "forget me",                # voice_profile, never from typed chat
    "my name is Bob",
    "call me Bob",
    "never mind",
    "say that again",
    "help",
    "how do I fix a leaky faucet?",
    "cancel my subscription to netflix, how do I do it?",
    "I hate when people tell me what to do",
    "remind me what you said earlier",
    "stop being so formal",
    "it's too cold, I'm cold",
]

#: Prose a music, intercom or lists fast path takes: never acted on.
PROSE = [
    "play a game with me",
    "play chess with me",
    "play the role of a pirate",
    "play devil's advocate",
    "play the devil's advocate for me",
    "shuffle the words in this sentence",
    "tell everyone I'm proud of them is what I'd say at the wedding, right?",
    "announce my engagement at the party, should I?",
    "add a paragraph about dogs?",
    "pause for a second, let me think",
]


async def _no_house_action(tid: int, rooms: dict | None = None) -> None:
    rows = await _rows(tid)
    assert all((r["stats"] or {}).get("model_role") != "action" for r in rows if r["role"] == "assistant"), rows
    async with SessionLocal() as s:
        plays = (await s.execute(text("SELECT count(*) FROM media_plays"))).scalar_one()
        timers = (await s.execute(text("SELECT count(*) FROM timers"))).scalar_one()
    assert (plays, timers) == (0, 0)
    for sat in (rooms or {}).values():
        assert sat.said == []


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("said", CHIT_CHAT + PROSE)
async def test_chit_chat_and_prose_go_to_the_model_with_no_action_and_no_model_call(
    db_session, web, rooms, tool_model, monkeypatch, said,
) -> None:
    import web.backend.api.chat as chat_api

    await _a_song()
    # The core answers (so a message no core fast path takes is asked about
    # a plugin's): still no model call there, nothing done.
    monkeypatch.setattr(chat_api, "get_cached_snapshot", lambda: {"active_rooms": []})
    tid = await _thread()
    async with _web() as c:
        done = await _send(c, tid, said)
    await _fanned_out()
    assert done["stats"]["model_role"] != "action", (said, done)
    assert tool_model.calls == []
    await _no_house_action(tid, rooms)


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("said", ["forget me", "remember that jo likes tulips", "my name is jo", "help",
                                  "say that again", "never mind", "let's have a chat",
                                  "could you let everyone know dinner is ready", "what is a mortgage"])
async def test_the_core_never_runs_the_chats_own_handlers_or_a_model_for_typed_chat(
    db_session, open_gate, tool_model, said,
) -> None:
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, said, fast_paths_only=True)
    assert body["handled"] is False and body["messages"] == [], body
    assert tool_model.calls == []
    async with SessionLocal() as s:
        logged = (await s.execute(text("SELECT count(*) FROM intents_log"))).scalar_one()
        people = (await s.execute(text("SELECT count(*) FROM people"))).scalar_one()
        memories = (await s.execute(text("SELECT count(*) FROM memories"))).scalar_one()
    assert (logged, people, memories) == (0, 0, 0)
    assert await _rows(tid) == []


@requires_db
@pytest.mark.asyncio
async def test_long_or_multi_line_messages_never_reach_the_house(db_session, web, monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_cached_snapshot", lambda: {"active_rooms": []})
    tid = await _thread()
    async with _web() as c:
        long_ = await _send(c, tid, "announce " + "dinner is ready " * 30)
        lines = await _send(c, tid, "set a timer\nfor 5 minutes")
    assert _texts(web) == []
    assert long_["stats"]["model_role"] == lines["stats"]["model_role"] == "chat"
    async with _client() as core:
        body = await _text(core, tid, "set a timer for 5 minutes " + "x" * 400, fast_paths_only=True)
    assert body["handled"] is False


@requires_db
@pytest.mark.asyncio
async def test_documents_help_and_lookups_beat_fast_paths(db_session, web, monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    monkeypatch.setattr(chat_api, "get_cached_snapshot", lambda: {"active_rooms": []})
    docs = await _thread()
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, documents) VALUES "
            "(:t, 'user', 'here it is', CAST(:d AS JSONB))"
        ), {"t": docs, "d": json.dumps([{"token": uuid.uuid4().hex, "name": "notes.txt"}])})
        await s.commit()
    helped = await _thread()
    async with SessionLocal() as s:
        await s.execute(text("UPDATE chat_threads SET title = 'Help: timers' WHERE id = :t"), {"t": helped})
        await s.commit()
    plain = await _thread()
    async with _web() as c:
        read = await _send(c, docs, "read it to me")
        doc_timer = await _send(c, docs, "set a timer for 5 minutes")
        help_q = await _send(c, plain, "how do I set a timer on my oven?")
        help_thread = await _send(c, helped, "set a timer for 5 minutes")
        lookup = await _send(c, plain, "what time is it")
    assert _texts(web) == []
    for done in (read, doc_timer, help_q, help_thread, lookup):
        assert done["stats"].get("model_role") != "action", done
    assert read["stats"]["model_role"] == doc_timer["stats"]["model_role"] == "documents"


@requires_db
@pytest.mark.asyncio
async def test_a_keyless_caller_cant_name_a_keyed_phone(db_session, open_gate) -> None:
    keyed = f"android-{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        await s.execute(text("INSERT INTO devices (device_id, name, device_key_hash) VALUES (:d, :d, :h)"),
                        {"d": keyed, "h": uuid.uuid4().hex * 2})
        await s.commit()
    tid = await _thread()
    for turn in (
        phone_voice.run_phone_text_turn(core_app, phone_id=keyed, thread_id=tid, text_="what time is it",
                                        viewer=Viewer()),
        phone_voice.run_phone_turn(core_app, phone_id=keyed, thread_id=tid, audio=b"\\0" * 64, viewer=Viewer()),
    ):
        with pytest.raises(phone_voice.PhoneTurnRefused) as e:
            await turn
        assert (e.value.status, e.value.code) == (403, "not_this_phone")
    # The web's own name for a keyless caller, and the phone with its key.
    ok = await phone_voice.run_phone_text_turn(core_app, phone_id=f"chat-{tid}", thread_id=tid,
                                               text_="what time is it", viewer=Viewer())
    mine = await phone_voice.run_phone_text_turn(core_app, phone_id=keyed, thread_id=tid,
                                                 text_="what time is it", viewer=Viewer(device_id=keyed))
    assert ok["matched_handler"] == mine["matched_handler"] == "clock"


@requires_db
@pytest.mark.asyncio
async def test_a_hop_that_times_out_picks_up_the_cores_late_reply(db_session, web, monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    monkeypatch.setattr(chat_api, "ROUTER_LATE_GRACE_SEC", 5.0)
    monkeypatch.setattr(chat_api, "_ROUTER_LATE_POLL_SEC", 0.05)
    web.timeout = "late"   # the core answers a moment after the web gave up
    tid = await _thread()
    async with _web() as c:
        r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": "set a timer for 5 minutes"})
    events = _sse(r.text)
    assert events[0] == ("status", {"state": "house_command"})
    done = _done(r.text)
    assert done["stats"]["model_role"] == "action" and done["stats"]["matched_handler"] == "timer"
    assert [x["role"] for x in await _rows(tid)] == ["user", "assistant"]


@requires_db
@pytest.mark.asyncio
async def test_a_hop_that_times_out_writes_nothing_and_says_it_is_still_working(db_session, web, monkeypatch) -> None:
    import web.backend.api.chat as chat_api

    monkeypatch.setattr(chat_api, "ROUTER_LATE_GRACE_SEC", 0.2)
    monkeypatch.setattr(chat_api, "_ROUTER_LATE_POLL_SEC", 0.05)
    web.timeout = "never"
    tid = await _thread()
    async with _web() as c:
        r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": "set a timer for 5 minutes"})
    errors = [d for e, d in _sse(r.text) if e == "error"]
    assert errors == [{"detail": chat_api.ROUTER_STILL_WORKING_TEXT, "code": "core_timeout"}]
    assert "nothing was done" not in r.text
    rows = await _rows(tid)
    assert [x["role"] for x in rows] == ["user"]
    # The core's reply, when it lands, is the only one.
    async with _client() as core:
        late = await _text(core, tid, "set a timer for 5 minutes", user_message_id=rows[0]["id"],
                           fast_paths_only=True)
    assert late["matched_handler"] == "timer"
    assert [x["role"] for x in await _rows(tid)] == ["user", "assistant"]


@requires_db
@pytest.mark.asyncio
async def test_one_reply_per_message_whoever_writes_first(db_session, web) -> None:
    """The web's fixed line went in (the core was down): the core's own
    reply to that row, arriving later, is refused, not written twice."""
    tid = await _thread()
    web.down = True
    async with _web() as c:
        down = await _send(c, tid, "set a timer for 5 minutes")
    assert down["stats"]["error_code"] == "core_unreachable"
    user_id = (await _rows(tid))[0]["id"]
    async with _client() as core:
        r = await core.post("/v1/phone/text", json={"text": "set a timer for 5 minutes", "phone_id": PHONE,
                                                     "thread_id": tid, "user_message_id": user_id})
    assert r.status_code == 409 and r.json()["code"] == "already_answered"
    assert [x["role"] for x in await _rows(tid)] == ["user", "assistant"]


@requires_db
@pytest.mark.asyncio
async def test_a_chat_deleted_mid_hop_is_a_clean_not_found(db_session, web) -> None:
    tid = await _thread()
    web.before = lambda: _delete_thread(tid)
    async with _web() as c:
        r = await c.post(f"/api/chat/threads/{tid}/messages", json={"content": "set a timer for 5 minutes"})
    assert r.status_code == 200
    errors = [d for e, d in _sse(r.text) if e == "error"]
    assert errors and errors[-1]["code"] == "thread_not_found"


async def _delete_thread(tid: int) -> None:
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM chat_threads WHERE id = :t"), {"t": tid})
        await s.commit()


@requires_db
@pytest.mark.asyncio
async def test_before_setup_a_house_command_says_so_plainly(db_session, web) -> None:
    import web.backend.api.chat as chat_api

    web.status = 501
    tid = await _thread()
    async with _web() as c:
        done = await _send(c, tid, "set a timer for 5 minutes")
    assert done["content"] == chat_api.ROUTER_SETUP_TEXT and done["stats"]["error_code"] == "core_not_set_up"
    assert "setup is finished" in done["content"] and web.model_calls == 0


@requires_db
@pytest.mark.asyncio
async def test_a_play_with_no_player_records_nothing(db_session, open_gate) -> None:
    await _a_song()
    tid = await _thread()
    async with _client() as c:
        body = await _text(c, tid, "play glass harbor", fast_paths_only=True)
        nothing = await _text(c, tid, "play the song about purple elephants", fast_paths_only=True)
    assert body["reply"] == phone_voice.CHAT_NO_PLAYER_TEXT
    # Nothing found: the chat's model answers it, not the house's line.
    assert nothing["handled"] is False
    async with SessionLocal() as s:
        plays = (await s.execute(text("SELECT count(*) FROM media_plays"))).scalar_one()
        ctx_rows = (await s.execute(text("SELECT context FROM sessions WHERE room_id = :r"), {"r": ROOM})).all()
    assert plays == 0
    assert all("last_played_track" not in (dict(r[0] or {})) for r in ctx_rows)


@requires_db
@pytest.mark.asyncio
async def test_announce_that_reads_only_this_phones_own_requests(db_session, open_gate, rooms) -> None:
    mine, other = f"android-{uuid.uuid4().hex[:8]}", f"android-{uuid.uuid4().hex[:8]}"
    await _device(mine)
    await _device(other)
    tid = await _thread()
    async with SessionLocal() as s:
        await s.execute(text(
            "INSERT INTO chat_messages (thread_id, role, content, device_id) VALUES (:t, 'user', :q, :d), "
            "(:t, 'user', 'Please announce that the cake is ready', NULL)"
        ), {"t": tid, "q": SHOT, "d": other})
        await s.commit()
    body = await phone_voice.run_phone_text_turn(core_app, phone_id=mine, thread_id=tid, text_="Announce that",
                                                 viewer=Viewer(device_id=mine), fast_paths_only=True)
    await _fanned_out()
    assert body["reply"] == "What should I announce?"
    assert all(sat.said == [] for sat in rooms.values())


@pytest.mark.parametrize(("message", "kept"), [
    ("the cat is in the house", "the cat is in the house"),
    ("dinner is ready in the house", "dinner is ready in the house"),
    ("to everyone dinner is ready", "dinner is ready"),
    ("in every room, the movie starts now", "the movie starts now"),
    ("dinner is ready to everyone please", "dinner is ready"),
    ("dinner is ready, please", "dinner is ready"),
    ("that the car is here, to the house", "the car is here"),
])
def test_routing_words_are_dropped_only_as_routing(message, kept) -> None:
    from domovoi.handlers.intercom import _clean_message

    assert _clean_message(message) == kept


@requires_db
@pytest.mark.asyncio
async def test_a_satellite_keeps_the_words_of_the_message(db_session, rooms) -> None:
    response = await _say("announce the cat is in the house")
    assert response.announce_text == "the cat is in the house"


# ─── Review round 2: polite requests as questions, "remember to" ──────────

#: Said to the chat mic and typed, each must reach the same handler (or,
#: for the mic's model turns, none of the house's from typed chat).
PARITY = [
    "Can you announce that dinner is ready?",
    "could you announce dinner is ready",
    "can u announce dinner is ready",
    "pls announce dinner is ready?",
    "would you tell everyone dinner is ready?",
    "Could you turn it up?",
    "can you pause the music?",
    "can you play music?",
    "can you play some jazz?",
    "Drop in on the kitchen",
    "set a timer for 5 minutes",
    "add milk to the shopping list",
    "remember to call mom",
    "remember to call mom at 5",
    "Can you tell me a joke?",
]


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("said", PARITY)
async def test_chat_matches_the_mic(db_session, open_gate, rooms, tool_model, said) -> None:
    """chat == mic: a typed message reaches the handler the chat mic's
    fast tiers give it; where the mic would ask a model, typed chat leaves
    it to the chat's own model (and no model is asked at the house)."""
    async with SessionLocal() as s:
        await s.execute(text("INSERT INTO lists (name, name_key, position) VALUES ('Shopping', 'shopping', 2) "
                             "ON CONFLICT DO NOTHING"))
        await s.commit()
    mic_tid, typed_tid = await _thread(), await _thread()
    mic = await _say(said, room=ROOM, phone=True, thread_id=mic_tid)
    mic_fast = mic.matched_path in ("fast", "confirmation")
    tool_model.calls.clear()
    async with _client() as c:
        typed = await _text(c, typed_tid, said, fast_paths_only=True, plays_here=True)
    assert tool_model.calls == []
    if mic_fast:
        assert typed["handled"] is True and typed["matched_handler"] == mic.matched_handler, (said, typed)
    else:
        assert typed["handled"] is False, (said, typed)


@requires_db
@pytest.mark.asyncio
async def test_a_polite_request_asked_as_a_question_is_done_from_the_dashboard(db_session, web, rooms) -> None:
    await _a_song()
    tid = await _thread()
    async with _web() as c:
        announced = await _send(c, tid, "Can you announce that dinner is ready?")
        louder = await _send(c, tid, "Could you turn it up?")
        pause = await _send(c, tid, "can you pause the music?")
        joke = await _send(c, tid, "Can you tell me a joke?")
    await _fanned_out()
    # As a typed "announce dinner is ready": done, said back, not pretended.
    assert announced["content"] == "Broadcasting to the house."
    assert rooms["kitchen"].said == ["dinner is ready"]
    # The dashboard has no player: the house's own plain line, not the model's.
    for done in (louder, pause):
        assert done["content"] == phone_voice.CHAT_NO_PLAYER_TEXT and done["stats"]["model_role"] == "action"
    assert joke["stats"]["model_role"] == "chat" and web.model_calls == 1


@requires_db
@pytest.mark.asyncio
async def test_remember_to_is_a_memory_typed_and_by_voice(db_session, open_gate, tool_model) -> None:
    owned = f"android-{uuid.uuid4().hex[:8]}"
    alex = await _device(owned, person="alex")
    tid = await _thread()
    typed = await phone_voice.run_phone_text_turn(
        core_app, phone_id=owned, thread_id=tid, text_="Remember to call Mom.",
        viewer=Viewer(device_id=owned), fast_paths_only=True,
    )
    voice = await _say("remember to water the plants", person_id=alex)
    assert typed["reply"] == "I'll remember: call Mom." and typed["matched_handler"] == "memory"
    assert voice.text == "I'll remember: water the plants." and voice.matched_handler == "memory"
    async with SessionLocal() as s:
        bodies = (await s.execute(text("SELECT body FROM memories WHERE person_id = :p ORDER BY id"),
                                  {"p": alex})).scalars().all()
    assert bodies == ["call Mom", "water the plants"]
    assert tool_model.calls == []


@requires_db
@pytest.mark.asyncio
async def test_remember_to_with_a_time_is_a_reminder_typed_and_by_voice(db_session, open_gate, tool_model) -> None:
    tid = await _thread()
    async with _client() as c:
        typed = await _text(c, tid, "remember to call mom at 5", fast_paths_only=True)
        later = await _text(c, tid, "remember to call mom in 20 minutes", fast_paths_only=True)
    voice = await _say("remember to call mom tomorrow at 9")
    assert typed["matched_handler"] == later["matched_handler"] == voice.matched_handler == "reminder"
    async with SessionLocal() as s:
        reminders = (await s.execute(text("SELECT count(*) FROM timers WHERE message IS NOT NULL"))).scalar_one()
        memories = (await s.execute(text("SELECT count(*) FROM memories"))).scalar_one()
    assert (reminders, memories) == (3, 0)
    assert tool_model.calls == []


@requires_db
@pytest.mark.asyncio
async def test_remember_to_from_the_dashboard_is_the_houses_answer_and_stored_once(db_session, web, tool_model) -> None:
    """The web's own memory step leaves "remember to" alone, so the house
    answers it and nothing is stored twice. A keyless browser is nobody:
    the memory handler's own answer (as for an unknown voice), no model."""
    tid = await _thread()
    async with _web() as c:
        done = await _send(c, tid, "remember to call mom")
        fact = await _send(c, tid, "remember that the wifi password is on the fridge")
        name = await _send(c, tid, "do you remember my name?")
    assert done["stats"]["model_role"] == "action" and done["stats"]["matched_handler"] == "memory"
    assert _texts(web) == ["remember to call mom"]
    assert fact["stats"]["model_role"] == "memory"          # the chat's own memory step, as before
    assert name["stats"]["model_role"] == "chat"
    assert web.model_calls == 1 and tool_model.calls == []
    async with SessionLocal() as s:
        stored = (await s.execute(text("SELECT count(*) FROM memories WHERE body ILIKE '%call mom%'"))).scalar_one()
    assert stored == 0
