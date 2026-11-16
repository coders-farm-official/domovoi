"""Groups: rooms that share one music queue (1.3.0, domovoi/music_groups.py).

Pinned here (SHARED-QUEUES-PLAN.md §5 "Tests", with the owner's answers of
2026-11-14):

* the membership state machine: share, join, a member leaving, the last
  member leaving, the host leaving (the queue handed to the earliest
  member, from the same place), one shared queue per room, "just in here";
* routing: a member's commands reach the host's player, its satellite plays
  the host's stream, and the workers see each player once;
* the handshake keyed by the host: a member's rejoin never resumes a group
  a person paused, and a member's failure never pauses it; a pause in any
  room is the group's;
* guests: a guest in a member room reaches the group while it plays;
* the sweeper: a group whose host ran dry ends, a member offline for 30
  minutes leaves;
* voice: "play X upstairs" (a named group, no "in"), "play X everywhere",
  "play this in the garage too", "join the garage", "stop" in a group (the
  whole group, owner 2026-11-15) and "stop here" (this room alone),
  "leave the group", "just in here", "which rooms are playing this", "stop
  the music upstairs"; "play the jazz station" is never a group's;
* named groups' names; and, with the database, the mirror the web reads.
"""

from __future__ import annotations

import time

import pytest

from domovoi import music_groups
from domovoi.clients import mpd as mpd_module
from domovoi.music_groups import RoomGroup
from domovoi.room_music import drain
from domovoi.router import plan_route
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_rooms import (  # noqa: F401 — the fixture
    FakeDB,
    _app,
    _ctx,
    _rooms,
    _sat,
    _say,
    house,
)


def _group(gid: int, name: str, rooms: tuple[str, ...]) -> RoomGroup:
    g = RoomGroup(id=gid, name=name, rooms=tuple(sorted(rooms)))
    music_groups._groups[gid] = g
    return g


async def _playing(house, room: str, song: str = "song.mp3") -> None:
    await house.clients[room].prepare_files([song, "next.mp3"])
    await house.clients[room].resume()


# ─── the state machine ────────────────────────────────────────────────────


async def test_share_routes_members_to_the_host(house) -> None:
    app = _app()
    await _playing(house, "garage")
    sh = await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    await drain()
    assert sh is not None and sh.host == "garage"
    assert sh.rooms == ["garage", "dining_room", "jordan_office"]
    assert music_groups.host_of("dining_room") == "garage"
    assert music_groups.is_member("dining_room") and not music_groups.is_member("garage")
    # Routing: the member's player is the host's; its own is still there.
    assert mpd_module.get_mpd_client_for("dining_room") is house.clients["garage"]
    assert mpd_module.own_mpd_client("dining_room") is house.clients["dining_room"]
    url = mpd_module.own_stream_url_for("garage")
    assert mpd_module.mpd_stream_url_for("dining_room") == url
    # The members' satellites were sent to the host's stream, and recorded
    # so a reconnect rejoins it.
    assert app.state.active_sessions["dining_room"].started == [url]
    assert app.state.resumable_music["jordan_office"] == url
    assert music_groups.describe("jordan_office") == {
        "id": sh.id, "name": None, "host_room": "garage",
        "rooms": ["garage", "dining_room", "jordan_office"], "group_id": None, "everywhere": False,
    }


async def test_joining_pauses_the_rooms_own_player_and_keeps_its_queue(house) -> None:
    await _playing(house, "garage")
    await _playing(house, "dining_room", "mine.mp3")
    await music_groups.share(_app(), "garage", ["dining_room"])
    own = house.clients["dining_room"]
    assert own._state == "pause"
    assert [e["file"] for e in await own.queue_list()][0] == "mine.mp3"


async def test_no_worker_sees_a_host_twice(house) -> None:
    await _playing(house, "garage")
    await music_groups.share(_app(), "garage", ["dining_room", "jordan_office"])
    seen = [id(c) for _r, c in mpd_module.iter_mpd_clients()]
    assert len(seen) == len(set(seen))  # each room's own player, once


async def test_a_member_leaving_and_the_last_one_ending_it(house) -> None:
    app = _app()
    await _playing(house, "garage")
    sh = await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    left = await music_groups.leave(app, "dining_room")
    assert left is not None and not left.was_host and not left.ended
    assert music_groups.host_of("dining_room") == "dining_room"
    assert sh.rooms == ["garage", "jordan_office"]
    left = await music_groups.leave(app, "jordan_office")
    assert left.ended and music_groups.share_of("garage") is None
    assert house.clients["garage"]._state == "play"  # the host plays on, alone
    assert await music_groups.leave(app, "garage") is None


async def test_the_host_leaving_hands_the_queue_to_the_earliest_member(house) -> None:
    app = _app()
    await _playing(house, "garage", "one.mp3")
    await house.clients["garage"].seek_to(42)
    await music_groups.share(app, "garage", ["dining_room"])
    await music_groups.share(app, "garage", ["jordan_office"])
    left = await music_groups.leave(app, "garage")
    await drain()
    assert left.was_host and left.new_host == "dining_room" and not left.ended
    sh = music_groups.share_of("jordan_office")
    assert sh.host == "dining_room" and sh.members == ["jordan_office"]
    new = house.clients["dining_room"]
    assert [e["file"] for e in await new.queue_list()] == ["one.mp3", "next.mp3"]
    assert new._state == "pause"  # the members' music_ready resumes it
    assert house.clients["garage"]._state == "stop"
    url = mpd_module.own_stream_url_for("dining_room")
    assert app.state.active_sessions["jordan_office"].started[-1] == url
    assert app.state.active_sessions["dining_room"].started[-1] == url


async def test_one_shared_queue_per_room(house) -> None:
    app = _app()
    await _playing(house, "garage")
    await _playing(house, "jordan_office")
    await music_groups.share(app, "garage", ["dining_room", "living_room"])
    await music_groups.share(app, "jordan_office", ["dining_room"])
    assert music_groups.host_of("dining_room") == "jordan_office"
    assert music_groups.share_of("garage").members == ["living_room"]
    # A host that joins someone else's: its own listeners get its queue.
    await music_groups.share(app, "jordan_office", ["garage"])
    assert music_groups.host_of("garage") == "jordan_office"
    assert music_groups.host_of("living_room") == "living_room" or \
        music_groups.share_of("living_room").host == "living_room"


async def test_just_in_here_keeps_the_music_in_a_member_room(house) -> None:
    app = _app()
    await _playing(house, "garage", "one.mp3")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    await music_groups.keep_only(app, "jordan_office")
    await drain()
    assert music_groups.shares() == []
    assert [e["file"] for e in await house.clients["jordan_office"].queue_list()][0] == "one.mp3"
    assert {"type": "music_stop"} in app.state.active_sessions["garage"].frames
    assert {"type": "music_stop"} in app.state.active_sessions["dining_room"].frames
    assert "dining_room" not in app.state.resumable_music


# ─── the handshake, keyed by the host ─────────────────────────────────────


async def test_a_pause_is_the_groups_and_a_rejoin_keeps_it(house) -> None:
    from domovoi.music_pause import note_paused_by_person, paused_by_person
    from domovoi.streaming import consume_music_ready

    app = _app()
    app.state.music_paused_by_person = set()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    note_paused_by_person(app, "dining_room", True)  # "pause" said in the member
    assert paused_by_person(app, "garage")
    await house.clients["garage"].pause()
    # The member's satellite comes back after a turn: its music_ready must
    # not resume the group's player.
    app.state.pending_music_start["dining_room"] = {"url": "x", "task": None}
    await consume_music_ready(app, "dining_room")
    assert house.clients["garage"]._state == "pause"


async def test_a_members_failure_never_pauses_the_host(house) -> None:
    from domovoi.streaming import consume_music_failed

    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    url = app.state.resumable_music["dining_room"]
    await consume_music_failed(app, "dining_room", {"stream_url": url, "reason": "x"})
    assert house.clients["garage"]._state == "play"


async def test_a_guest_in_a_member_room_reaches_the_group_while_it_plays(house) -> None:
    from domovoi import guest_queue

    await _playing(house, "garage")
    guest_queue.clear_activity_cache()
    assert not (await guest_queue.room_activity("dining_room")).active
    await music_groups.share(_app(), "garage", ["dining_room"])
    guest_queue.clear_activity_cache()
    assert (await guest_queue.room_activity("dining_room")).active
    await music_groups.leave(_app(), "dining_room")
    guest_queue.clear_activity_cache()
    # Playing on its own again (its own player is paused): its own only.
    assert not (await guest_queue.room_activity("dining_room")).active


# ─── the sweeper ──────────────────────────────────────────────────────────


async def test_a_group_whose_host_ran_dry_ends(house) -> None:
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    await house.clients["garage"].stop()
    await music_groups.upkeep(app)
    assert music_groups.share_of("garage") is not None  # one look is not enough
    await music_groups.upkeep(app)
    await drain()
    assert music_groups.share_of("garage") is None
    assert {"type": "music_stop"} in app.state.active_sessions["dining_room"].frames


async def test_a_member_offline_for_30_minutes_leaves(house, monkeypatch) -> None:
    app = _app(online=[r for r in ("garage", "dining_room", "jordan_office")])
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    del app.state.active_sessions["dining_room"]
    await music_groups.upkeep(app)
    assert "dining_room" in music_groups.share_of("garage").members
    sh = music_groups.share_of("garage")
    sh.offline_since["dining_room"] = time.monotonic() - music_groups.OFFLINE_LEAVE_SEC - 1
    await music_groups.upkeep(app)
    assert sh.members == ["jordan_office"]


# ─── names ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name, ok", [
    ("Upstairs", True),
    ("The north wing", True),
    ("the jazz station", False),     # a radio word
    ("Radio room", False),
    ("Everywhere", False),           # already a place
    ("the whole house", False),
    ("garage", False),               # a room's name
    ("", False),
    ("x" * 41, False),
])
def test_what_a_group_may_be_called(name, ok) -> None:
    keys = set()
    for r in _rooms():
        keys |= r.keys()
    if ok:
        assert music_groups.check_name(name, room_keys=keys)
    else:
        with pytest.raises(music_groups.GroupNameError):
            music_groups.check_name(name, room_keys=keys)


def test_two_groups_cant_answer_to_the_same_words() -> None:
    _group(1, "Upstairs", ("garage", "dining_room"))
    with pytest.raises(music_groups.GroupNameError):
        music_groups.check_name("the upstairs")
    assert music_groups.check_name("Upstairs", exclude_id=1) == "Upstairs"


# ─── voice ────────────────────────────────────────────────────────────────


def test_a_group_path_takes_nothing_without_a_group() -> None:
    plan = plan_route("play jazz upstairs")
    assert plan is not None and plan.handler.name == "music"
    assert plan.fast_path.method.__name__ != "_room_group_from_match"


def test_play_the_jazz_station_is_never_a_groups() -> None:
    _group(1, "Jazz", ("garage", "dining_room"))
    _group(2, "Upstairs", ("garage", "dining_room"))
    plan = plan_route("play the jazz station")
    assert plan is not None
    assert plan.fast_path.method.__name__ == "_play_any_from_match"
    assert plan_route("play jazz upstairs").fast_path.method.__name__ == "_room_group_from_match"


async def test_play_upstairs_plays_here_and_the_group_hears_it(house) -> None:
    _group(1, "Upstairs", ("dining_room", "garage", "jordan_office"))
    ctx = _ctx()
    resp = await _say("play weezer upstairs", ctx, FakeDB())
    assert resp.text == "Playing Weezer upstairs, shuffled."
    assert resp.music_action == "start"
    sh = music_groups.share_of("dining_room")
    assert sh.host == "dining_room" and set(sh.members) == {"garage", "jordan_office"}
    assert sh.group_id == 1 and sh.name == "Upstairs"


async def test_play_in_a_group_from_outside_it_plays_in_one_of_its_rooms(house) -> None:
    _group(1, "The north wing", ("garage", "jordan_office"))
    ctx = _ctx()
    resp = await _say("play weezer in the north wing", ctx, FakeDB())
    assert resp.text == "Playing Weezer in the north wing, shuffled."
    assert resp.music_action is None  # nothing for the room that asked
    sh = music_groups.share_of("garage")
    assert sh is not None and set(sh.rooms) == {"garage", "jordan_office"}
    assert music_groups.share_of("dining_room") is None


async def test_play_x_in_a_member_room_changes_the_groups_music(house) -> None:
    await _playing(house, "garage", "old.mp3")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room"])
    resp = await _say("play weezer", _ctx(app=app), FakeDB())
    assert resp.music_action == "start"
    assert resp.music_stream_url == mpd_module.own_stream_url_for("garage")
    assert [e["file"] for e in await house.clients["garage"].queue_list()] == ["Weezer.mp3"]
    assert music_groups.host_of("dining_room") == "garage"  # still together


# Stop (owner, 2026-11-15, reversing "stop is per room" of 2026-11-14): a
# bare stop is the whole group's, as pause is; "stop here" is this room's.


async def test_stop_in_a_member_room_stops_the_whole_group(house) -> None:
    await _playing(house, "garage")
    app = _app()
    ctx = _ctx(app=app)
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    resp = await _say("stop", ctx, FakeDB())
    assert resp.text == "Stopped in the dining room, the garage and the jordan office."
    assert resp.music_action == "stop"
    assert house.clients["garage"]._state == "stop"  # the host's player
    assert music_groups.shares() == []
    assert {"type": "music_stop"} in _sat(ctx, "garage").frames
    assert {"type": "music_stop"} in _sat(ctx, "jordan_office").frames
    assert "jordan_office" not in app.state.resumable_music
    # Its queue is kept, stopped: "resume" in the old host plays it again.
    assert [e["file"] for e in await house.clients["garage"].queue_list()][0] == "song.mp3"


async def test_stop_the_music_in_the_host_room_stops_the_whole_group(house) -> None:
    await _playing(house, "garage")
    app = _app()
    ctx = _ctx(room="garage", app=app)
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    resp = await _say("stop the music", ctx, FakeDB())
    assert resp.music_action == "stop"
    assert house.clients["garage"]._state == "stop"
    assert music_groups.shares() == []
    assert {"type": "music_stop"} in _sat(ctx, "dining_room").frames
    # Nobody took the queue over: no member's own player plays.
    assert house.clients["dining_room"]._state != "play"


async def test_stop_everywhere_share_says_everywhere(house) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room"], everywhere=True)
    resp = await _say("stop", _ctx(app=app), FakeDB())
    assert resp.text == "Stopped everywhere."
    assert music_groups.shares() == []


@pytest.mark.parametrize("said", [
    "stop here", "stop in here", "stop just here", "stop the music here",
    "only stop this room", "stop the music in here", "stop here only",
    "turn off the music in here", "stop the music in this room",
])
async def test_stop_here_stops_only_this_room(house, said) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    resp = await _say(said, _ctx(app=app), FakeDB())
    assert resp.text == "Stopped here. The music plays on in the garage and the jordan office."
    assert resp.music_action == "stop"
    assert house.clients["garage"]._state == "play"
    assert music_groups.share_of("dining_room") is None
    assert set(music_groups.share_of("garage").rooms) == {"garage", "jordan_office"}


async def test_stop_here_in_the_host_room_hands_the_queue_on(house) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    resp = await _say("stop here", _ctx(room="garage", app=app), FakeDB())
    assert resp.music_action == "stop"
    assert music_groups.share_of("dining_room").host == "dining_room"


async def test_stop_the_music_in_another_room_stops_its_group(house) -> None:
    await _playing(house, "garage")
    app = _app()
    ctx = _ctx(room="living_room", app=app)
    await music_groups.share(app, "garage", ["jordan_office"])
    resp = await _say("stop the music in jordan's office", ctx, FakeDB())
    assert resp.text == "Stopped in the garage and the jordan office."
    assert music_groups.shares() == []
    assert house.clients["garage"]._state == "stop"
    assert {"type": "music_stop"} in _sat(ctx, "jordan_office").frames
    assert {"type": "music_stop"} in _sat(ctx, "garage").frames


@pytest.mark.parametrize("said", [
    "stop the music in jordan's office only", "stop the music just in jordan's office",
    "only stop the music in jordan's office", "stop the music only in jordan's office",
])
async def test_stop_in_another_room_only_stops_that_room(house, said) -> None:
    await _playing(house, "garage")
    app = _app()
    ctx = _ctx(room="living_room", app=app)
    await music_groups.share(app, "garage", ["jordan_office", "dining_room"])
    resp = await _say(said, ctx, FakeDB())
    assert resp.text == "Stopped the music in Jordan's Office."
    assert music_groups.share_of("jordan_office") is None
    assert set(music_groups.share_of("garage").rooms) == {"garage", "dining_room"}
    assert house.clients["garage"]._state == "play"
    assert {"type": "music_stop"} in _sat(ctx, "jordan_office").frames


async def test_stop_in_a_room_in_no_group_is_unchanged(house) -> None:
    await _playing(house, "dining_room")
    for said in ("stop", "stop here"):
        await _playing(house, "dining_room")
        resp = await _say(said, _ctx(), FakeDB())
        assert resp.text == "Stopped."
        assert resp.music_action == "stop"
        assert house.clients["dining_room"]._state == "stop"


@pytest.mark.parametrize("said, method", [
    ("stop", "_stop_from_match"),
    ("stop the music", "_stop_from_match"),
    ("stop here", "_stop_here_from_match"),
    ("stop in here", "_stop_here_from_match"),
    ("stop just here", "_stop_here_from_match"),
    ("stop the music here", "_stop_here_from_match"),
    ("only stop this room", "_stop_here_from_match"),
    ("stop the music in here", "_stop_here_from_match"),
    ("stop the music in the office", "_room_control_from_match"),
    ("stop the music in the office only", "_stop_only_from_match"),
    ("stop the music just in the office", "_stop_only_from_match"),
    ("leave the group", "_group_leave_from_match"),
    ("just in here", "_group_leave_from_match"),
    # A song with "here" in its name is still a play, never a stop.
    ("play wish you were here", "_play_any_from_match"),
    ("play here comes the sun", "_play_any_from_match"),
    ("play stop right here", "_play_any_from_match"),
])
def test_stop_phrases_route(said, method) -> None:
    plan = plan_route(said)
    assert plan is not None and plan.handler.name == "music", (said, plan)
    assert plan.fast_path.method.__name__ == method


async def test_play_this_in_the_garage_too(house) -> None:
    await _playing(house, "dining_room")
    ctx = _ctx()
    resp = await _say("play this in the garage too", ctx, FakeDB())
    await drain()
    assert resp.text == "Playing this in the garage too."
    assert music_groups.host_of("garage") == "dining_room"
    assert _sat(ctx, "garage").started == [mpd_module.own_stream_url_for("dining_room")]


async def test_share_this_with_the_group_and_play_this_everywhere(house) -> None:
    await _playing(house, "dining_room")
    _group(1, "Upstairs", ("garage", "jordan_office"))
    resp = await _say("share this with upstairs", _ctx(), FakeDB())
    assert resp.text == "Playing this upstairs too."
    assert set(music_groups.share_of("dining_room").members) == {"garage", "jordan_office"}
    resp = await _say("play this everywhere", _ctx(), FakeDB())
    assert resp.text == "Playing this everywhere too."


async def test_join_the_garage(house) -> None:
    await _playing(house, "garage")
    ctx = _ctx()
    resp = await _say("join the garage", ctx, FakeDB())
    await drain()
    assert resp.text == "Playing the music from the garage here too."
    assert music_groups.host_of("dining_room") == "garage"
    resp = await _say("play the garage's music in here", ctx, FakeDB())
    assert resp.text == "This room is already playing that."


async def test_join_a_quiet_room(house) -> None:
    resp = await _say("join the garage", _ctx(), FakeDB())
    assert resp.text == "Nothing is playing in the garage right now."


async def test_join_needs_a_paired_satellite(house) -> None:
    await _playing(house, "garage")
    app = _app(unpaired=("dining_room",))
    resp = await _say("join the garage", _ctx(app=app), FakeDB())
    assert "isn't paired" in resp.text
    assert music_groups.share_of("dining_room") is None


async def test_play_mozarts_music_is_still_a_play(house) -> None:
    resp = await _say("play mozart's music", _ctx(), FakeDB())
    assert resp is None or "the garage" not in (resp.text or "")


async def test_which_rooms_leave_and_just_here(house) -> None:
    await _playing(house, "garage")
    app = _app()
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    resp = await _say("which rooms are playing this", _ctx(app=app), FakeDB())
    assert resp.text == "The garage, Jordan's Office and here."
    resp = await _say("leave the group", _ctx(room="jordan_office", app=app), FakeDB())
    assert resp.music_action == "stop"
    assert music_groups.share_of("jordan_office") is None
    resp = await _say("just in here", _ctx(app=app), FakeDB())
    assert resp.text == "Just in here now. The other rooms have stopped."
    assert music_groups.shares() == []
    resp = await _say("which rooms are playing this", _ctx(app=app), FakeDB())
    assert resp.text in ("Just this room.", "Nothing is playing here right now.")


async def test_stop_the_music_upstairs_ends_the_group(house) -> None:
    _group(1, "Upstairs", ("dining_room", "garage", "jordan_office"))
    ctx = _ctx()
    await _say("play weezer upstairs", ctx, FakeDB())
    await house.clients["dining_room"].resume()
    resp = await _say("stop the music upstairs", ctx, FakeDB())
    await drain()
    assert resp.text == "Stopped the music upstairs."
    assert resp.music_action == "stop"
    assert music_groups.shares() == []
    assert {"type": "music_stop"} in _sat(ctx, "garage").frames


async def test_pause_upstairs_pauses_the_groups_player_once(house) -> None:
    _group(1, "Upstairs", ("dining_room", "garage"))
    ctx = _ctx()
    await _say("play weezer upstairs", ctx, FakeDB())
    await house.clients["dining_room"].resume()
    resp = await _say("pause the music upstairs", ctx, FakeDB())
    assert resp.text == "Paused the music upstairs."
    assert house.clients["dining_room"]._state == "pause"


# ─── the database mirror ──────────────────────────────────────────────────


@requires_db
async def test_groups_and_shared_queues_survive_a_restart_and_the_web_reads_them(house, db_session) -> None:
    from domovoi.db.session import session_scope
    from web.backend.api import music_groups as web_groups

    music_groups.PERSIST = True
    async with session_scope() as s:
        g = await music_groups.create_group(s, "Upstairs", ["garage", "dining_room"])
    await _playing(house, "garage")
    sh = await music_groups.share(_app(), "garage", ["dining_room"], group_id=g.id)
    music_groups.reset()  # a restart
    await music_groups.load()
    assert [x.name for x in music_groups.groups()] == ["Upstairs"]
    assert music_groups.host_of("dining_room") == "garage"
    assert music_groups.share_of("garage").id == sh.id
    web_groups.forget()
    live = await web_groups.live_groups()
    assert live["dining_room"]["host_room"] == "garage"
    assert live["dining_room"]["name"] == "Upstairs"
    assert await web_groups.host_room_for("dining_room") == "garage"
    await music_groups.leave(_app(), "dining_room")
    web_groups.forget()
    assert await web_groups.live_groups() == {}
    async with session_scope() as s:
        assert await music_groups.delete_group(s, g.id)
    music_groups.reset()
    await music_groups.load()
    assert music_groups.groups() == [] and music_groups.shares() == []


# ─── the core routes ──────────────────────────────────────────────────────


@requires_db
async def test_the_core_routes(house, db_session, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi import admin_auth
    from domovoi.main import app as core_app

    music_groups.PERSIST = True
    fake = _app()
    for name in ("active_sessions", "resumable_music", "pending_music_start"):
        monkeypatch.setattr(core_app.state, name, getattr(fake.state, name), raising=False)
    core_app.dependency_overrides[admin_auth.require_device] = lambda: None
    core_app.dependency_overrides[admin_auth.require_device_read] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test") as c:
            r = await c.post("/v1/admin/music/groups",
                             json={"name": "Upstairs", "rooms": ["garage", "dining_room"]})
            assert r.status_code == 200, r.text
            gid = r.json()["id"]
            r = await c.post("/v1/admin/music/groups",
                             json={"name": "the jazz station", "rooms": ["garage", "dining_room"]})
            assert r.status_code == 400 and "station" in r.json()["detail"]
            r = await c.post("/v1/admin/music/groups",
                             json={"name": "Attic", "rooms": ["garage", "nowhere"]})
            assert r.status_code == 400
            r = await c.patch(f"/v1/admin/music/groups/{gid}", json={"name": "The north wing"})
            assert r.json()["name"] == "The north wing"
            # Nothing plays in the garage yet.
            r = await c.post("/v1/admin/music/shared/join",
                             json={"room_id": "dining_room", "host_room": "garage"})
            assert r.status_code == 409
            await _playing(house, "garage")
            r = await c.post("/v1/admin/music/shared/join",
                             json={"room_id": "dining_room", "host_room": "garage"})
            assert r.status_code == 200, r.text
            share = r.json()
            assert share["host_room"] == "garage" and share["rooms"] == ["garage", "dining_room"]
            r = await c.get("/v1/admin/music/groups")
            body = r.json()
            assert body["groups"][0]["name"] == "The north wing"
            assert body["shared"][0]["id"] == share["id"]
            r = await c.get("/v1/admin/music/queue/dining_room")
            assert r.status_code == 200 and r.json()["group"]["host_room"] == "garage"
            r = await c.post("/v1/admin/music/shared", json={
                "host_room": "dining_room", "rooms": ["jordan_office"]})
            assert r.status_code == 200
            assert r.json()["rooms"] == ["garage", "dining_room", "jordan_office"]
            r = await c.post("/v1/admin/music/shared/leave", json={"room_id": "jordan_office"})
            assert r.status_code == 200 and r.json()["group"]["rooms"] == ["garage", "dining_room"]
            r = await c.post(f"/v1/admin/music/shared/{share['id']}/end")
            assert r.status_code == 200 and music_groups.shares() == []
            r = await c.post("/v1/admin/music/shared/leave", json={"room_id": "dining_room"})
            assert r.status_code == 404
            r = await c.delete(f"/v1/admin/music/groups/{gid}")
            assert r.status_code == 200 and music_groups.groups() == []
    finally:
        core_app.dependency_overrides.pop(admin_auth.require_device, None)
        core_app.dependency_overrides.pop(admin_auth.require_device_read, None)


# ─── the web's now-playing card ───────────────────────────────────────────


async def test_a_member_rooms_card_is_its_hosts_song(monkeypatch) -> None:
    from web.backend.api import music as web_music
    from web.backend.api import music_groups as web_groups

    info = {"id": "q1", "name": "Upstairs", "host_room": "garage",
            "rooms": ["garage", "dining_room"], "group_id": 1, "everywhere": False}

    async def _live():
        return {"garage": info, "dining_room": info}

    read: list[int] = []

    async def _read_mpd(host, port):
        read.append(port)
        return "play", {"file": "a.mp3", "Title": "A", "Id": "5"}, 3.0

    async def _ports(room):
        return (6651, 8051) if room == "garage" else None

    async def _row(song):
        return False, None

    monkeypatch.setattr(web_groups, "live_groups", _live)
    monkeypatch.setattr(web_music, "_read_mpd", _read_mpd)
    monkeypatch.setattr(web_music, "_host_ports", _ports)
    monkeypatch.setattr(web_music, "_lookup_library_row", _row)
    card = await web_music._now_playing_for(("dining_room", 6650, 8050))
    assert read == [6651]  # the host's player, not its own
    assert card.room_id == "dining_room" and card.state == "play"
    assert card.stream_url.endswith(":8051")
    assert card.group is not None and card.group.host_room == "garage"
    assert card.group.name == "Upstairs"
