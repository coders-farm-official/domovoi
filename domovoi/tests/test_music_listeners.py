"""Listen along (1.5.0, domovoi/music_listeners.py): a paired phone plays a
room's music itself, as a listener of that room's queue.

Pinned here, against the design note LISTEN-ALONG (§1, §2, §5, §8):

* the three core routes and their answers: join, leave and the heartbeat,
  with every refusal ``code``;
* a listener follows the music: a hand-off moves it (and its stream URL), a
  member room leaving or the dashboard ending the shared queue does not;
* the host stopping stops it: a person's stop answers ``stopped``, the
  queue running out ``ended``;
* expiry after 60 seconds without a heartbeat, and the reload after a core
  restart;
* one room per phone; a listener never keeps a shared queue alive and never
  changes its rooms;
* the tiers: household only, a guest has no way in, away chat has no op;
* the web's proxies, and ``listeners`` on the now-playing cards and the
  group listing.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth, lan_address, music_groups, music_listeners
from domovoi.clients import mpd as mpd_module
from domovoi.models import Response
from domovoi.room_music import dispatch_music, drain
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_groups import _playing
from domovoi.tests.test_music_rooms import (  # noqa: F401 — the fixture
    _app,
    _ctx,
    _rooms,
    _say,
    house,
)
from domovoi.tests.test_music_under_capture import room  # noqa: F401 — the fixture

PHONE = "android-sam"
PHONE_2 = "android-alex"
DIALED = "192.168.1.20"
JOIN = "/v1/admin/music/listen/join"
LEAVE = "/v1/admin/music/listen/leave"
BEAT = "/v1/admin/music/listen/heartbeat"
LISTEN_ROUTES = [JOIN, LEAVE, BEAT]
WEB_LISTEN_ROUTES = ["/api/music/listen/join", "/api/music/listen/leave",
                     "/api/music/listen/heartbeat"]
JOIN_FIELDS = {"room_id", "host_room", "stream_url", "state", "group", "listeners",
               "heartbeat_sec"}


def _stream(room: str) -> str:
    """The stream a phone that dialed DIALED is handed for ``room``'s player."""
    return lan_address.stream_url_for_peer(mpd_module.own_stream_url_for(room), DIALED)


@pytest.fixture
async def core(house, monkeypatch):
    """The real core app on the household tier (the gate is pinned by the
    tier tests below), over the test house's rooms, as the web proxy
    reaches it: the phone dialed DIALED."""
    from domovoi import music_groups_routes
    from domovoi.main import app as core_app

    async def _house(request):
        return _rooms()

    monkeypatch.setattr(music_groups_routes, "_house", _house)
    core_app.dependency_overrides[admin_auth.require_device] = lambda: None
    core_app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test",
                               headers={"X-Forwarded-Host": DIALED}) as c:
            yield c
    finally:
        core_app.dependency_overrides.pop(admin_auth.require_device, None)
        core_app.dependency_overrides.pop(admin_auth.require_device_read, None)


async def _join(c: AsyncClient, room: str, device: str = PHONE) -> dict[str, Any]:
    r = await c.post(JOIN, json={"room_id": room, "device_id": device})
    assert r.status_code == 200, r.text
    return r.json()


async def _beat(c: AsyncClient, device: str = PHONE) -> dict[str, Any]:
    r = await c.post(BEAT, json={"device_id": device})
    assert r.status_code == 200, r.text  # always 200
    return r.json()


def _refused(r, status: int, code: str) -> str:
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert set(detail) == {"code", "message"} and detail["code"] == code, detail
    assert detail["message"].endswith("."), detail  # a sentence the phone may show
    return detail["message"]


# ─── join, leave, heartbeat ───────────────────────────────────────────────


async def test_join_answers_with_the_rooms_stream(core, house) -> None:
    await _playing(house, "garage")
    body = await _join(core, "garage")
    assert set(body) == JOIN_FIELDS
    assert body["room_id"] == "garage" and body["host_room"] == "garage"
    assert body["stream_url"] == _stream("garage") and body["stream_url"].endswith(":8051")
    assert body["state"] == "play"
    assert body["group"] is None
    assert body["listeners"] == [{"device_id": PHONE, "name": "A phone"}]
    assert body["heartbeat_sec"] == 15
    # A paused room is still one to listen to (the phone shows paused).
    await house.clients["dining_room"].prepare_files(["a.mp3"])
    body = await _join(core, "dining_room", PHONE_2)
    assert body["state"] == "pause"


async def test_the_stream_is_on_the_address_the_phone_dialed(core, house) -> None:
    """The web passes the address the phone dialed on; without it the
    request's own host is used (a caller that reached the core directly)."""
    await _playing(house, "garage")
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": PHONE},
                        headers={"X-Forwarded-Host": "192.168.1.30:6369"})
    assert r.json()["stream_url"] == lan_address.stream_url_for_peer(
        mpd_module.own_stream_url_for("garage"), "192.168.1.30")


async def test_join_refusals_carry_a_code_and_a_sentence(core, house) -> None:
    r = await core.post(JOIN, json={"room_id": "garage"})
    _refused(r, 400, "no_device")
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": "  "})
    _refused(r, 400, "no_device")
    r = await core.post(JOIN, json={"room_id": "attic", "device_id": PHONE})
    _refused(r, 404, "no_such_room")
    r = await core.post(JOIN, json={"device_id": PHONE})
    _refused(r, 404, "no_such_room")
    # Adopted, never connected: no music player of its own.
    r = await core.post(JOIN, json={"room_id": "porch", "device_id": PHONE})
    assert _refused(r, 409, "no_player") == "Porch has no music player yet."
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": PHONE})
    assert _refused(r, 409, "nothing_playing") == "Nothing is playing in Garage."
    r = await core.post(JOIN, json={"room_id": "dining_room", "device_id": PHONE})
    assert _refused(r, 409, "nothing_playing") == "Nothing is playing in Dining Room."
    assert music_listeners.get(PHONE) is None  # nothing was recorded


async def test_joining_a_member_room_listens_to_its_host(core, house) -> None:
    await _playing(house, "garage")
    sh = await music_groups.share(_app(), "garage", ["dining_room"])
    body = await _join(core, "dining_room")
    assert body["room_id"] == "dining_room" and body["host_room"] == "garage"
    assert body["stream_url"] == _stream("garage")
    assert body["group"] == sh.to_dict()
    # A member room whose own player waits paused: the phone hears the
    # group's music, never the room's own queue.
    assert music_listeners.get(PHONE).host == "garage"


async def test_leave(core, house) -> None:
    r = await core.post(LEAVE, json={"device_id": PHONE})
    assert _refused(r, 404, "not_listening")
    r = await core.post(LEAVE, json={})
    _refused(r, 400, "no_device")
    await _playing(house, "garage")
    sh = await music_groups.share(_app(), "garage", ["dining_room"])
    await _join(core, "dining_room")
    r = await core.post(LEAVE, json={"device_id": PHONE})
    assert r.status_code == 200 and r.json() == {"left": "garage"}
    # Leaving changes nothing in any room.
    assert house.clients["garage"]._state == "play"
    assert sh.rooms == ["garage", "dining_room"] and music_groups.shares() == [sh]
    r = await core.post(LEAVE, json={"device_id": PHONE})
    _refused(r, 404, "not_listening")


async def test_heartbeat(core, house) -> None:
    body = await _beat(core)
    assert body == {"listening": False, "reason": "not_listening",
                    "message": music_listeners.NOT_LISTENING_MESSAGE}
    assert (await core.post(BEAT, json={})).json()["reason"] == "not_listening"
    await _playing(house, "garage")
    joined = await _join(core, "garage")
    body = await _beat(core)
    assert body.pop("listening") is True
    assert body == joined
    await house.clients["garage"].pause()
    assert (await _beat(core))["state"] == "pause"


# ─── it follows the music ─────────────────────────────────────────────────


async def test_a_hand_off_moves_listeners_and_their_stream(core, house) -> None:
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    before = await _join(core, "garage")
    await _join(core, "jordan_office", PHONE_2)
    left = await music_groups.leave(app, "garage")  # the host leaves
    assert left.new_host == "dining_room"
    body = await _beat(core)
    assert body["listening"] is True
    assert body["host_room"] == "dining_room"
    assert body["stream_url"] == _stream("dining_room") != before["stream_url"]
    # It joined from the room that left: it is with the music now.
    assert body["room_id"] == "dining_room"
    assert body["group"]["host_room"] == "dining_room"
    other = await _beat(core, PHONE_2)
    assert other["host_room"] == "dining_room" and other["room_id"] == "jordan_office"
    assert [x["device_id"] for x in other["listeners"]] == [PHONE, PHONE_2]


async def test_a_member_room_leaving_or_the_shared_queue_ending_changes_nothing(core, house) -> None:
    app = _app()
    await _playing(house, "garage")
    sh = await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    await _join(core, "jordan_office")
    await music_groups.leave(app, "jordan_office", silence=True)  # "stop here"
    body = await _beat(core)
    assert body["listening"] is True and body["host_room"] == "garage"
    assert body["stream_url"] == _stream("garage")
    # The dashboard ends the shared queue: the members stop, the host plays
    # on alone, and so does the phone.
    await music_groups.end(app, sh.id, silence_members=True)
    await drain()
    body = await _beat(core)
    assert body["listening"] is True and body["host_room"] == "garage"
    assert body["group"] is None


async def test_a_room_joining_a_shared_queue_takes_its_listeners_along(core, house) -> None:
    """A room playing alone that joins another room's shared queue: its own
    player waits, paused, and its phones hear what the room hears now."""
    await _playing(house, "garage")
    await _playing(house, "dining_room", "mine.mp3")
    await _join(core, "dining_room")
    await music_groups.share(_app(), "garage", ["dining_room"])
    body = await _beat(core)
    assert body["listening"] is True
    assert body["host_room"] == "garage" and body["room_id"] == "dining_room"
    assert body["stream_url"] == _stream("garage")


async def test_joining_a_second_room_moves_the_phone(core, house) -> None:
    await _playing(house, "garage")
    await _playing(house, "dining_room")
    await _join(core, "garage")
    body = await _join(core, "dining_room")
    assert body["host_room"] == "dining_room"
    assert music_listeners.listing("garage") == []
    assert music_listeners.listing("dining_room") == [{"device_id": PHONE, "name": "A phone"}]


async def test_a_listener_never_keeps_a_shared_queue_alive_nor_joins_its_rooms(core, house) -> None:
    app = _app()
    await _playing(house, "garage")
    sh = await music_groups.share(app, "garage", ["dining_room"])
    await _join(core, "dining_room")
    await _join(core, "garage", PHONE_2)
    assert sh.rooms == ["garage", "dining_room"]
    assert PHONE not in music_groups._by_room and PHONE_2 not in music_groups._by_room
    left = await music_groups.leave(app, "dining_room")  # the last member
    assert left.ended and music_groups.shares() == []
    # The host plays on alone, and the phones with it.
    body = await _beat(core)
    assert body["listening"] is True and body["host_room"] == "garage" and body["group"] is None


# ─── the host stopping ────────────────────────────────────────────────────


async def test_stop_in_a_group_stops_its_listeners(core, house) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room"])
    await _join(core, "garage")
    await _say("stop", _ctx(app=app))  # said in the member room: the group's stop
    body = await _beat(core)
    assert body == {"listening": False, "reason": "stopped",
                    "message": "The music in Garage stopped."}
    # Told once; then it simply isn't listening.
    assert (await _beat(core))["reason"] == "not_listening"


async def test_a_dispatched_stop_stops_a_rooms_listeners(core, house) -> None:
    """The dashboard's stop, a cleared queue: a ``music_stop`` sent to the
    room (room_music.dispatch_music)."""
    await _playing(house, "dining_room")
    await _join(core, "dining_room")
    assert music_listeners.listing("dining_room")
    await dispatch_music(_app(), Response(text="", music_action="stop"), "dining_room")
    assert music_listeners.listing("dining_room") == []  # off the cards at once
    body = await _beat(core)
    assert body["reason"] == "stopped"
    assert body["message"] == "The music in Dining Room stopped."


async def test_stop_here_in_a_member_room_leaves_the_phone_playing(core, house) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room"])
    await _join(core, "dining_room")
    await _say("stop here", _ctx(app=app))
    # The reply's music_stop reaches the room that left, which no phone hears.
    await dispatch_music(app, Response(text="", music_action="stop"), "dining_room")
    body = await _beat(core)
    assert body["listening"] is True and body["host_room"] == "garage"


async def test_the_queue_running_out_ends_listening(core, house, monkeypatch) -> None:
    await _playing(house, "garage")
    await _join(core, "garage")
    await house.clients["garage"].stop()
    # One look at a stopped player is not the end: a new song being loaded
    # can read "stop" for a moment.
    await music_listeners.upkeep()
    assert music_listeners.get(PHONE).live
    await _playing(house, "garage")
    await music_listeners.upkeep()  # playing again: the clock starts over
    assert "garage" not in music_listeners._stopped_since
    await house.clients["garage"].stop()
    monkeypatch.setattr(music_listeners, "STOPPED_GRACE_SEC", 0.0)
    await music_listeners.upkeep()
    body = await _beat(core)
    assert body == {"listening": False, "reason": "ended",
                    "message": "The music in Garage finished."}


async def test_a_heartbeat_finds_the_queue_ran_out(core, house, monkeypatch) -> None:
    monkeypatch.setattr(music_listeners, "STOPPED_GRACE_SEC", 0.0)
    await _playing(house, "garage")
    await _join(core, "garage")
    await house.clients["garage"].stop()
    assert (await _beat(core))["reason"] == "ended"


async def test_a_shared_queue_that_ran_dry_ends_its_listeners(core, house) -> None:
    """The groups' sweeper ending a shared queue whose host stopped by
    itself (music_groups.upkeep) tells the phones ``ended``."""
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    await _join(core, "dining_room")
    await house.clients["garage"].stop()
    for _ in range(music_groups.STOPPED_LOOKS_TO_END):
        await music_groups.upkeep(app)
    assert music_groups.shares() == []
    body = await _beat(core)
    assert body["reason"] == "ended"
    assert body["message"] == "The music in Dining Room finished."


async def test_a_voice_stop_in_the_room_stops_its_listeners(room) -> None:
    """The voice reply's own ``music_stop`` (streaming.StreamSession), the
    usual "stop" said in the room that plays."""
    from domovoi.tests.test_music_under_capture import ROOM

    await music_listeners.join(PHONE, ROOM, room_id=ROOM)
    room.reply = Response(text="Stopped.", matched_handler="music", matched_path="fast",
                          online=True, music_action="stop")
    await room.turn()
    assert "music_stop" in room.kinds()
    entry = music_listeners._listeners[PHONE]
    assert entry.reason == "stopped"
    assert entry.message == "The music in Dining room stopped."


async def test_a_skip_past_the_last_song_ends_listening(core, house) -> None:
    """The queue someone built is over: "ended", as when it runs out."""
    from domovoi.handlers.music import MusicHandler

    await _playing(house, "dining_room")
    await house.clients["dining_room"].next()  # on the queue's last song
    await _join(core, "dining_room")
    resp = await MusicHandler()._skip_in_queue(_ctx(), 1, 2)
    assert resp.music_action == "stop" and resp.data == {"queue_ended": True}
    await dispatch_music(_app(), resp, "dining_room")
    assert (await _beat(core))["reason"] == "ended"


# ─── review fixes (2026-11-28) ────────────────────────────────────────────


async def test_a_room_joining_a_shared_queue_during_a_join_still_takes_the_phone(
    core, house, monkeypatch,
) -> None:
    """The host is read before the join's awaits: a room that joins a shared
    queue meanwhile must not leave the phone on its own paused player."""
    await _playing(house, "garage")
    await _playing(house, "dining_room", "mine.mp3")
    real_name = music_listeners.device_name

    async def _name_while_the_room_joins(device_id):
        await music_groups.share(_app(), "garage", ["dining_room"])
        return await real_name(device_id)

    monkeypatch.setattr(music_listeners, "device_name", _name_while_the_room_joins)
    body = await _join(core, "dining_room")
    assert body["host_room"] == "garage" and body["room_id"] == "dining_room"
    assert body["stream_url"] == _stream("garage")
    assert body["state"] == "play"


async def test_a_heartbeat_checks_the_host_again(core, house) -> None:
    await _playing(house, "garage")
    await _playing(house, "dining_room", "mine.mp3")
    await _join(core, "dining_room")
    await music_groups.share(_app(), "garage", ["dining_room"])
    music_listeners.get(PHONE).host = "dining_room"  # a move that was missed
    body = await _beat(core)
    assert body["listening"] is True and body["host_room"] == "garage"
    assert body["stream_url"] == _stream("garage")


async def test_forget_drops_only_the_entry_it_was_given(core, house) -> None:
    await _playing(house, "garage")
    await _join(core, "garage")
    old = music_listeners.get(PHONE)
    await _join(core, "garage")  # the phone joined again meanwhile
    await music_listeners.forget(PHONE, old)
    assert music_listeners.get(PHONE) is not None and music_listeners.get(PHONE) is not old
    await music_listeners.forget(PHONE)  # no entry: only an expired one goes
    assert music_listeners.get(PHONE) is not None
    music_listeners.get(PHONE).seen = time.monotonic() - music_listeners.EXPIRE_SEC - 1
    await music_listeners.forget(PHONE)
    assert PHONE not in music_listeners._listeners


@pytest.mark.parametrize("device", ["ab", "bad id", "-leading", "x" * 65, "phone/1"])
async def test_a_device_id_must_have_the_devices_tiers_shape(core, house, device) -> None:
    await _playing(house, "garage")
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": device})
    _refused(r, 400, "bad_device")
    r = await core.post(LEAVE, json={"device_id": device})
    _refused(r, 400, "bad_device")
    assert (await _beat(core, device))["reason"] == "not_listening"
    assert music_listeners._listeners == {}


async def test_the_web_refuses_a_bad_device_id_before_the_core(monkeypatch) -> None:
    from web.backend.api import music_groups as web_groups
    from web.backend.main import app as web_app

    async def _post(*a, **kw):
        raise AssertionError("reached the core")

    monkeypatch.setattr(web_groups, "post_admin", _post)
    web_app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                               headers={"X-Requested-With": "domovoi-tests"}) as c:
            for path in ("/api/music/listen/join", "/api/music/listen/leave"):
                r = await c.post(path, json={"room_id": "garage", "device_id": "bad id"})
                _refused(r, 400, "bad_device")
                r = await c.post(path, json={"room_id": "garage"})
                _refused(r, 400, "no_device")
            r = await c.post("/api/music/listen/heartbeat", json={"device_id": "bad id"})
            assert r.status_code == 200 and r.json()["reason"] == "not_listening"
    finally:
        web_app.dependency_overrides.pop(admin_auth.require_device, None)


async def test_at_most_32_phones_listen(core, house, monkeypatch) -> None:
    assert music_listeners.MAX_LISTENERS == 32
    monkeypatch.setattr(music_listeners, "MAX_LISTENERS", 2)
    await _playing(house, "garage")
    await _playing(house, "dining_room")
    await _join(core, "garage")
    await _join(core, "garage", PHONE_2)
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": "android-third"})
    assert _refused(r, 409, "too_many_listeners") == "Too many phones are listening along already."
    # A phone already listening may still move.
    assert (await _join(core, "dining_room", PHONE_2))["host_room"] == "dining_room"
    # An expired one leaves its place.
    music_listeners.get(PHONE).seen = time.monotonic() - music_listeners.EXPIRE_SEC - 1
    assert (await _join(core, "garage", "android-third"))["host_room"] == "garage"


@pytest.mark.parametrize("forwarded", ["fe80::1", "[fe80::1]", "[fe80::1]:6369"])
async def test_an_ipv6_dialed_address_is_read_whole(core, house, forwarded) -> None:
    await _playing(house, "garage")
    r = await core.post(JOIN, json={"room_id": "garage", "device_id": PHONE},
                        headers={"X-Forwarded-Host": forwarded})
    expected = lan_address.stream_url_for_peer(mpd_module.own_stream_url_for("garage"), "fe80::1")
    assert r.json()["stream_url"] == expected
    if lan_address.is_auto(lan_address.settings.mpd_http_base):
        assert expected == "http://[fe80::1]:8051"


async def test_the_web_sends_an_ipv6_dialed_address_bracketed(monkeypatch) -> None:
    from web.backend.api import music_groups as web_groups
    from web.backend.main import app as web_app

    sent: list[dict] = []

    async def _post(path, body=None, timeout=30.0, headers=None):
        sent.append(headers or {})
        return 200, {"left": "garage"}

    monkeypatch.setattr(web_groups, "post_admin", _post)
    web_app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=web_app),
                               base_url="http://[fe80::1]:6369",
                               headers={"X-Requested-With": "domovoi-tests"}) as c:
            await c.post("/api/music/listen/leave", json={"device_id": PHONE})
    finally:
        web_app.dependency_overrides.pop(admin_auth.require_device, None)
    assert sent[0]["X-Forwarded-Host"] == "[fe80::1]"


async def test_a_player_that_doesnt_answer_keeps_its_last_state(core, house, monkeypatch) -> None:
    from domovoi import music_groups_routes

    await _playing(house, "garage")
    await _join(core, "garage")
    await house.clients["garage"].pause()
    assert (await _beat(core))["state"] == "pause"

    async def _silent(host):
        return None

    monkeypatch.setattr(music_groups_routes, "_player_state", _silent)
    body = await _beat(core)
    assert body["listening"] is True and body["state"] == "pause"  # never a made-up "stop"


# ─── expiry and restarts ──────────────────────────────────────────────────


async def test_a_phone_silent_for_a_minute_is_dropped(core, house) -> None:
    await _playing(house, "garage")
    await _join(core, "garage")
    entry = music_listeners.get(PHONE)
    entry.seen = time.monotonic() - 30
    assert (await _beat(core))["listening"] is True  # a heartbeat keeps it
    entry.seen = time.monotonic() - music_listeners.EXPIRE_SEC - 1
    assert music_listeners.listing("garage") == []  # off the cards already
    await music_listeners.upkeep()
    assert PHONE not in music_listeners._listeners
    body = await _beat(core)
    assert body["reason"] == "not_listening"


@requires_db
async def test_listeners_survive_a_restart_and_the_web_reads_them(core, house, db_session) -> None:
    from sqlalchemy import text as sql

    from domovoi.db.session import session_scope
    from web.backend.api import music_groups as web_groups

    music_groups.PERSIST = True
    music_listeners.PERSIST = True
    async with session_scope() as s:
        await s.execute(sql("INSERT INTO devices (device_id, name, platform) "
                            "VALUES (:d, :n, 'android')"), {"d": PHONE, "n": "Sam's phone"})
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    body = await _join(core, "dining_room")
    assert body["listeners"] == [{"device_id": PHONE, "name": "Sam's phone"}]
    await _join(core, "garage", PHONE_2)  # no devices row: "A phone"
    web_groups.forget()
    assert (await web_groups.live_listeners()) == {"garage": [
        {"device_id": PHONE, "name": "Sam's phone"},
        {"device_id": PHONE_2, "name": "A phone"},
    ]}
    live = await web_groups.live_groups()
    assert live["dining_room"]["listeners"] == [
        {"device_id": PHONE, "name": "Sam's phone"},
        {"device_id": PHONE_2, "name": "A phone"},
    ]

    # A restart: both come back, each with a minute to send a heartbeat.
    music_groups.reset()
    music_listeners.reset()
    await music_groups.load()
    await music_listeners.load()
    assert [e.device_id for e in music_listeners.listeners_of("garage")] == [PHONE, PHONE_2]
    assert music_listeners.get(PHONE).name == "Sam's phone"
    assert (await _beat(core))["listening"] is True
    music_listeners.get(PHONE_2).seen = time.monotonic() - music_listeners.EXPIRE_SEC - 1
    await music_listeners.upkeep()
    web_groups.forget()
    assert (await web_groups.live_listeners()) == {"garage": [
        {"device_id": PHONE, "name": "Sam's phone"}]}

    # A hand-off moves the row; the host stopping drops it.
    await music_groups.leave(app, "garage")
    web_groups.forget()
    assert list(await web_groups.live_listeners()) == ["dining_room"]
    await music_listeners.note_stopped("dining_room")
    web_groups.forget()
    assert await web_groups.live_listeners() == {}
    music_listeners.reset()
    await music_listeners.load()
    assert music_listeners._listeners == {}


# ─── the group listing and the now-playing cards ─────────────────────────


async def test_the_group_listing_carries_listeners(core, house) -> None:
    await _playing(house, "garage")
    await music_groups.share(_app(), "garage", ["dining_room"])
    await _join(core, "dining_room")
    shared = (await core.get("/v1/admin/music/groups")).json()["shared"]
    assert shared[0]["listeners"] == [{"device_id": PHONE, "name": "A phone"}]
    assert shared[0]["rooms"] == ["garage", "dining_room"]


def _patch_card_reads(monkeypatch, *, groups: dict, listeners: dict) -> None:
    from web.backend.api import music as web_music
    from web.backend.api import music_groups as web_groups

    async def _live():
        return groups

    async def _listeners():
        return listeners

    async def _read_mpd(host, port):
        return "play", {"file": "a.mp3", "Title": "A", "Id": "5"}, 3.0

    async def _ports(room):
        return (6651, 8051) if room == "garage" else None

    async def _row(song):
        return False, None

    monkeypatch.setattr(web_groups, "live_groups", _live)
    monkeypatch.setattr(web_groups, "live_listeners", _listeners)
    monkeypatch.setattr(web_music, "_read_mpd", _read_mpd)
    monkeypatch.setattr(web_music, "_host_ports", _ports)
    monkeypatch.setattr(web_music, "_lookup_library_row", _row)


SAM = {"device_id": PHONE, "name": "Sam's phone"}


async def test_now_playing_cards_carry_their_hosts_listeners(monkeypatch) -> None:
    from web.backend.api import music as web_music

    info = {"id": "q1", "name": None, "host_room": "garage", "rooms": ["garage", "dining_room"],
            "group_id": None, "everywhere": False, "listeners": [SAM]}
    _patch_card_reads(monkeypatch, groups={"garage": info, "dining_room": info},
                      listeners={"garage": [SAM]})
    card = await web_music._now_playing_for(("dining_room", 6650, 8050))
    assert [x.model_dump() for x in card.listeners] == [SAM]  # the host's, heard here too
    assert [x.model_dump() for x in card.group.listeners] == [SAM]
    alone = await web_music._now_playing_for(("jordan_office", 6652, 8052))
    assert alone.listeners == []


async def test_now_playing_names_listeners_to_the_household_only(monkeypatch) -> None:
    from types import SimpleNamespace

    from web.backend.api import music as web_music

    _patch_card_reads(monkeypatch, groups={}, listeners={"garage": [SAM]})

    async def _rooms_list():
        return [("garage", 6651, 8051)]

    async def _no_provenance(cards):
        return None

    monkeypatch.setattr(web_music, "_list_provisioned_rooms", _rooms_list)
    monkeypatch.setattr(web_music, "_attach_queue_provenance", _no_provenance)
    tier = {"now": "no-auth"}

    async def _check(request):
        return tier["now"]

    monkeypatch.setattr(web_music, "check_device_request", _check)
    cards = await web_music.now_playing(SimpleNamespace())
    assert cards[0].listeners == []  # the open kiosk read names nobody
    tier["now"] = "ok"
    cards = await web_music.now_playing(SimpleNamespace())
    assert [x.model_dump() for x in cards[0].listeners] == [SAM]


# ─── the web proxies ──────────────────────────────────────────────────────


async def test_the_web_proxies_pass_the_dialed_address_and_the_answer(monkeypatch) -> None:
    from web.backend.api import music_groups as web_groups
    from web.backend.main import app as web_app

    calls: list[tuple[str, dict, dict]] = []

    async def _post(path, body=None, timeout=30.0, headers=None):
        calls.append((path, body, headers or {}))
        if path.endswith("/join"):
            return 409, {"detail": {"code": "nothing_playing",
                                    "message": "Nothing is playing in Garage."}}
        if path.endswith("/leave"):
            return 200, {"left": "garage"}
        return 200, {"listening": False, "reason": "not_listening", "message": "x."}

    monkeypatch.setattr(web_groups, "post_admin", _post)
    web_app.dependency_overrides[admin_auth.require_device] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=web_app),
                               base_url=f"http://{DIALED}:6369",
                               headers={"X-Requested-With": "domovoi-tests",
                                        admin_auth.DEVICE_TOKEN_HEADER: "household"}) as c:
            r = await c.post("/api/music/listen/join", json={"room_id": "garage", "device_id": PHONE})
            assert r.status_code == 409
            assert r.json()["detail"] == {"code": "nothing_playing",
                                          "message": "Nothing is playing in Garage."}
            r = await c.post("/api/music/listen/leave", json={"device_id": PHONE})
            assert r.json() == {"left": "garage"}
            r = await c.post("/api/music/listen/heartbeat", json={"device_id": PHONE})
            assert r.status_code == 200 and r.json()["reason"] == "not_listening"
    finally:
        web_app.dependency_overrides.pop(admin_auth.require_device, None)
    assert [p for p, _b, _h in calls] == LISTEN_ROUTES
    assert calls[0][1] == {"room_id": "garage", "device_id": PHONE}
    for _p, _b, headers in calls:
        assert headers["X-Forwarded-Host"] == DIALED
        assert headers[admin_auth.DEVICE_TOKEN_HEADER] == "household"


# ─── tiers: the household's only ──────────────────────────────────────────


def test_the_core_routes_are_the_household_tier() -> None:
    from domovoi import guest_queue
    from domovoi.tests.test_core_route_tiers import _gates_for

    for path in LISTEN_ROUTES:
        gates = _gates_for("POST", path)
        assert admin_auth.require_device in gates, path
        assert guest_queue.require_device_or_active_room_guest not in gates, path


def test_the_web_proxies_are_the_household_tier() -> None:
    from web.backend.api import music_queue
    from domovoi.tests.test_route_auth_matrix import ROUTES, _dependency_calls

    by_key = {(a, m, p): d for a, m, p, d in ROUTES}
    for path in WEB_LISTEN_ROUTES:
        calls = list(_dependency_calls(by_key[("web", "POST", path)]))
        assert admin_auth.require_device in calls, path
        assert music_queue.require_device_or_guest not in calls, path


async def test_a_guest_phone_cant_listen_along(house, monkeypatch) -> None:
    """A guest (no household token) on a room that is playing — the case
    where a guest may skip or add — gets no way in, at either hop, and no
    name lands on the listener list (§5)."""
    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import install_fake_db
    from web.backend.main import app as web_app

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"}, device_token="household")
    await _playing(house, "garage")
    guest = {"X-Requested-With": "XMLHttpRequest", "X-Domovoi-Device-Id": "browser-guest"}
    for app, routes in ((core_app, LISTEN_ROUTES), (web_app, WEB_LISTEN_ROUTES)):
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://test", headers=guest) as c:
            for path in routes:
                r = await c.post(path, json={"room_id": "garage", "device_id": "browser-guest"})
                assert r.status_code == 401, (path, r.text)
    assert music_listeners._listeners == {}


def test_away_chat_has_no_listen_op() -> None:
    """Home network only in 1.5.0: the away REQ op list has no listen op
    (and the stream is a LAN address anyway)."""
    from domovoi.away import wire

    assert not [op for op in wire.OPS if "listen" in op or "music" in op]
