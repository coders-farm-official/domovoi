"""Groups: the review's ten findings on wf/groups-130, one test (or more)
each. Every one of these failed before its fix.

1. a room's stale pause from before it joined silenced the group once it
   hosted after a hand-off;
2. a hand-off that the new host's player refused still stopped the old
   host (and moved now-playing and Up next);
3. a room leaving after 30 minutes offline kept its resume link to the
   host's stream;
4. a group's bare name poached later bands' sentences;
5. a room with no player of its own made the groups touch another room's
   player;
6. a member's skip was noted under the member, not the host the listen
   logger looks at;
7. "play X everywhere" from a member left its group before the play worked;
8. a podcast or a book aimed at a group with no rooms crashed (and a group
   silently picked one room);
9. a restart lost the members' join order (the hand-off order);
10. a group was changed in memory before the database committed.
"""

from __future__ import annotations

import re
import time

import pytest

from domovoi import music_groups, music_listens
from domovoi.clients import mpd as mpd_module
from domovoi.handlers.base import FastPath, Handler
from domovoi.music_groups import RoomGroup
from domovoi.music_pause import note_paused_by_person, paused_by_person
from domovoi.room_music import drain
from domovoi.router import first_fast_path, goes_to_the_tool_model, plan_route
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_music_rooms import (  # noqa: F401 — the fixture
    FakeDB,
    _app,
    _ctx,
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


def _paused_app():
    app = _app()
    app.state.music_paused_by_person = set()
    return app


# ── 1 ──


async def test_1_a_stale_pause_from_before_joining_never_silences_the_group(house) -> None:
    app = _paused_app()
    await _playing(house, "garage")
    note_paused_by_person(app, "dining_room", True)  # its own music, paused earlier
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    assert "dining_room" not in app.state.music_paused_by_person
    await music_groups.leave(app, "garage")  # the queue moves to dining_room
    assert music_groups.share_of("jordan_office").host == "dining_room"
    assert not paused_by_person(app, "dining_room")
    assert not paused_by_person(app, "jordan_office")


async def test_1_a_hand_off_drops_the_new_hosts_own_stale_flag(house) -> None:
    app = _paused_app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])
    await music_groups.share(app, "garage", ["jordan_office"])
    # A flag of its own that got in while it was a member (raw, as an older
    # core could leave it).
    app.state.music_paused_by_person.add("dining_room")
    await music_groups.leave(app, "garage")
    assert not paused_by_person(app, "jordan_office")


async def test_1_a_group_pause_still_moves_with_the_queue(house) -> None:
    app = _paused_app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    note_paused_by_person(app, "jordan_office", True)  # "pause" said in a member
    await music_groups.leave(app, "garage")
    assert paused_by_person(app, "jordan_office")
    assert app.state.music_paused_by_person == {"dining_room"}


# ── 2 ──


async def test_2_a_refused_hand_off_passes_to_the_next_member(house, monkeypatch) -> None:
    app = _app()
    await _playing(house, "garage", "one.mp3")
    await music_groups.share(app, "garage", ["dining_room"])
    await music_groups.share(app, "garage", ["jordan_office"])

    async def _refuse(*a, **kw):
        return []

    monkeypatch.setattr(house.clients["dining_room"], "prepare_files", _refuse)
    left = await music_groups.leave(app, "garage")
    assert left.new_host == "jordan_office" and not left.ended
    sh = music_groups.share_of("dining_room")
    assert sh.host == "jordan_office" and sh.members == ["dining_room"]
    assert [e["file"] for e in await house.clients["jordan_office"].queue_list()][0] == "one.mp3"
    assert house.clients["garage"]._state == "stop"


async def test_2_a_hand_off_nobody_can_take_ends_the_group_cleanly(house, monkeypatch) -> None:
    from domovoi.now_playing import NOW_PLAYING

    app = _app()
    await _playing(house, "garage", "one.mp3")
    await music_groups.share(app, "garage", ["dining_room"])

    async def _boom(*a, **kw):
        raise RuntimeError("player gone")

    monkeypatch.setattr(house.clients["dining_room"], "prepare_files", _boom)
    NOW_PLAYING._stamps.pop("garage", None)
    left = await music_groups.leave(app, "garage")
    await drain()
    assert left.ended and left.new_host is None
    assert music_groups.shares() == []
    # Nothing half-moved: the host was never switched while its player
    # played on; the group ended, the member was stopped, and so was the
    # player of the room that left.
    assert music_groups.host_of("dining_room") == "dining_room"
    assert {"type": "music_stop"} in app.state.active_sessions["dining_room"].frames
    assert house.clients["garage"]._state == "stop"
    assert "dining_room" not in app.state.resumable_music


# ── 3 ──


async def test_3_leaving_after_30_minutes_offline_drops_the_resume_link(house) -> None:
    app = _app(online=["garage", "dining_room", "jordan_office"])
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    assert "dining_room" in app.state.resumable_music
    del app.state.active_sessions["dining_room"]
    await music_groups.upkeep(app)
    music_groups.share_of("garage").offline_since["dining_room"] = (
        time.monotonic() - music_groups.OFFLINE_LEAVE_SEC - 1
    )
    await music_groups.upkeep(app)
    assert music_groups.share_of("dining_room") is None
    assert "dining_room" not in app.state.resumable_music


async def test_3_every_leave_drops_the_resume_link(house) -> None:
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room", "jordan_office"])
    await music_groups.leave(app, "jordan_office")
    assert "jordan_office" not in app.state.resumable_music
    await music_groups.leave(app, "garage")  # the host leaves: the queue moves on
    assert "garage" not in app.state.resumable_music
    assert app.state.resumable_music["dining_room"] == mpd_module.own_stream_url_for("dining_room")


# ── 4 ──


_NOT_MUSIC = [
    "do i have anything for the kids",
    "what is good for the kids",
    "tell a joke to the kids",
]


@pytest.mark.parametrize("said", _NOT_MUSIC)
def test_4_a_groups_name_takes_no_other_handlers_sentence(said) -> None:
    before = plan_route(said)
    tool_before = goes_to_the_tool_model(said)
    _group(1, "The kids", ("garage", "dining_room"))
    after = plan_route(said)
    assert (after and (after.handler.name, after.fast_path.method.__name__)) == (
        before and (before.handler.name, before.fast_path.method.__name__))
    assert goes_to_the_tool_model(said) == tool_before


def test_4_a_later_band_keeps_its_sentence() -> None:
    from domovoi.handlers import HANDLERS

    class _Plugin(Handler):
        name = "storyteller"
        priority_band = 500

        async def execute(self, intent, ctx, session):
            return None

        async def _tell(self, m, ctx, session):
            return None

    plugin = _Plugin()
    plugin.fast_paths = [FastPath(re.compile(r"^tell a joke to (.+)$"), _Plugin._tell)]
    _group(1, "The kids", ("garage", "dining_room"))
    hit = first_fast_path("tell a joke to the kids", [*HANDLERS, plugin])
    assert hit is not None and hit[0].name == "storyteller"


def test_4_plays_and_controls_aimed_at_a_group_are_still_taken() -> None:
    _group(1, "The kids", ("garage", "dining_room"))
    for said in ("play jazz for the kids", "stop the music the kids", "pause the kids"):
        plan = plan_route(said)
        assert plan is not None and plan.handler.name == "music", said
    assert plan_route("play jazz the kids").fast_path.method.__name__ == "_room_group_from_match"


# ── 5 ──


async def test_5_a_room_with_no_player_never_pauses_another_rooms(house, monkeypatch) -> None:
    from domovoi.config import settings

    await _playing(house, "dining_room", "mine.mp3")  # the first provisioned room
    await _playing(house, "garage")
    monkeypatch.setattr(settings, "use_stubs", False)
    # porch: adopted, never connected — no player of its own.
    await music_groups.share(_app(), "garage", ["porch"])
    assert house.clients["dining_room"]._state == "play"
    assert mpd_module.own_player("porch") is None


async def test_5_a_room_with_no_player_is_passed_over_in_a_hand_off(house, monkeypatch) -> None:
    from domovoi.config import settings

    app = _app()
    await _playing(house, "garage", "one.mp3")
    await music_groups.share(app, "garage", ["porch"])
    await music_groups.share(app, "garage", ["jordan_office"])
    monkeypatch.setattr(settings, "use_stubs", False)
    left = await music_groups.leave(app, "garage")
    assert left.new_host == "jordan_office"
    assert music_groups.share_of("porch").host == "jordan_office"


@requires_db
async def test_5_the_routes_refuse_a_room_with_no_player(house, db_session, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi import admin_auth
    from domovoi.main import app as core_app

    fake = _app()
    for name in ("active_sessions", "resumable_music", "pending_music_start"):
        monkeypatch.setattr(core_app.state, name, getattr(fake.state, name), raising=False)
    core_app.dependency_overrides[admin_auth.require_device] = lambda: None

    async def _house(request):
        from domovoi.tests.test_music_rooms import _rooms

        return _rooms()

    from domovoi import music_groups_routes

    monkeypatch.setattr(music_groups_routes, "_house", _house)
    await _playing(house, "garage")
    try:
        async with AsyncClient(transport=ASGITransport(app=core_app), base_url="http://test") as c:
            r = await c.post("/v1/admin/music/shared/join",
                             json={"room_id": "porch", "host_room": "garage"})
            assert r.status_code == 409
            r = await c.post("/v1/admin/music/shared",
                             json={"host_room": "garage", "rooms": ["porch"]})
            assert r.status_code == 409
            assert music_groups.shares() == []
    finally:
        core_app.dependency_overrides.pop(admin_auth.require_device, None)


# ── 6 ──


async def test_6_a_members_skip_is_noted_under_the_host(house) -> None:
    await _playing(house, "garage")
    await music_groups.share(_app(), "garage", ["dining_room"])
    music_listens.SKIP_STAMPS.clear()
    music_listens.note_skip("dining_room")
    assert music_listens.take_skip("garage", 0.0)


# ── 7 ──


async def test_7_a_failed_play_everywhere_from_a_member_keeps_it_in_its_group(
    house, monkeypatch,
) -> None:
    from domovoi.handlers.music import MusicHandler
    from domovoi.models import Response

    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["dining_room"])

    async def _nothing_found(self, ask, transcript, ctx, session):
        return Response(text="I couldn't find zzqx.", matched_handler="music")

    monkeypatch.setattr(MusicHandler, "_here", _nothing_found)
    resp = await _say("play zzqx everywhere", _ctx(app=app), FakeDB())
    assert resp.music_action != "start"
    assert music_groups.host_of("dining_room") == "garage"
    assert music_groups.share_of("garage").members == ["dining_room"]


async def test_7_play_everywhere_from_a_member_plays_on_its_groups_player(house) -> None:
    app = _app()
    await _playing(house, "garage", "old.mp3")
    await music_groups.share(app, "garage", ["dining_room"])
    resp = await _say("play weezer everywhere", _ctx(app=app), FakeDB())
    assert resp.music_action == "start"
    assert [e["file"] for e in await house.clients["garage"].queue_list()] == ["Weezer.mp3"]
    sh = music_groups.share_of("dining_room")
    assert sh.host == "garage" and "jordan_office" in sh.members


# ── 8 ──


async def test_8_a_podcast_for_a_group_answers_one_room_at_a_time(house) -> None:
    _group(1, "The north wing", ("attic_a", "attic_b"))  # rooms the house doesn't have
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.models import Context

    spoken = HANDLER_BY_NAME["spoken_audio"]
    for said in ("play the latest episode of the daily in the north wing",
                 "resume my audiobook in the north wing"):
        plan = plan_route(said)
        assert plan is not None and plan.handler.name == "spoken_audio", said
        resp = await plan.fast_path.method(plan.handler, plan.match, _ctx(), FakeDB())
        assert resp is not None and resp.text.startswith("I can only play that in one room"), said
    resp = await spoken.spoken_room_tool_call(
        {"action": "play_latest", "show": "the daily", "room": "the north wing"},
        Context(room_id="dining_room", app=_app()), FakeDB(),
    )
    assert resp.text.startswith("I can only play that in one room")
    # A group holding the asking room is still a group: not "here" alone.
    _group(2, "Downstairs", ("dining_room", "garage"))
    plan = plan_route("play the latest episode of the daily in downstairs")
    assert plan is not None and plan.handler.name == "spoken_audio"
    resp = await plan.fast_path.method(plan.handler, plan.match, _ctx(), FakeDB())
    assert resp.text.startswith("I can only play that in one room")
    resp = await spoken.spoken_room_tool_call(
        {"action": "play_latest", "show": "the daily", "room": "downstairs"},
        Context(room_id="dining_room", app=_app()), FakeDB(),
    )
    assert resp.text.startswith("I can only play that in one room")


# ── 9 ──


@requires_db
async def test_9_a_restart_keeps_the_join_order(house, db_session) -> None:
    music_groups.PERSIST = True
    app = _app()
    await _playing(house, "garage")
    await music_groups.share(app, "garage", ["jordan_office"])
    await music_groups.share(app, "garage", ["dining_room"])  # later, and sorts first
    music_groups.reset()
    await music_groups.load()
    assert music_groups.share_of("garage").members == ["jordan_office", "dining_room"]
    left = await music_groups.leave(app, "garage")
    assert left.new_host == "jordan_office"


# ── 10 ──


class _RefusingSession:
    """A session whose commit fails (the database went away mid-change)."""

    class _Row:
        def first(self):
            return (41,)

    async def execute(self, stmt, params=None):
        return self._Row()

    async def commit(self):
        raise RuntimeError("the database went away")


async def test_10_a_group_the_database_never_kept_is_not_in_memory() -> None:
    with pytest.raises(RuntimeError):
        await music_groups.create_group(_RefusingSession(), "Upstairs", ["garage", "dining_room"])
    assert music_groups.groups() == []


async def test_10_a_delete_the_database_refused_keeps_the_group() -> None:
    _group(5, "Upstairs", ("garage", "dining_room"))
    with pytest.raises(RuntimeError):
        await music_groups.delete_group(_RefusingSession(), 5)
    assert [g.name for g in music_groups.groups()] == ["Upstairs"]


async def test_10_a_rename_the_database_refused_keeps_the_old_name() -> None:
    _group(5, "Upstairs", ("garage", "dining_room"))
    with pytest.raises(RuntimeError):
        await music_groups.update_group(_RefusingSession(), 5, name="Top floor")
    assert [g.name for g in music_groups.groups()] == ["Upstairs"]
