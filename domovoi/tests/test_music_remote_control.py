"""A room driven from the app and the dashboard (2026-10-01).

Three things the cast hand-off verification found on ft, with two rooms:

* **Skip threw a cast queue away.** ``/v1/admin/music/skip`` reaches
  ``MusicHandler._smart_skip``, whose "local library track" branch played
  ONE random library track: office went from Long Road (a queue of 4,
  cast from the app) to Damp Steps (a queue of 1). Now a room whose MPD
  queue holds two songs or more follows it (MPD's next; after the last
  song the queue ends), and the smart skip keeps the one-song plays it was
  written for. The spoken "next" / "skip" and the tool model's "next" go
  the same way.
* **A failed control answered 200.** A pause MPD never got answered HTTP
  200 with the spoken "I couldn't reach the music player.", and both
  clients, handing playback off, took the room for paused. The admin
  control route now answers 502 / 409 / 503 on ``Response.failure``; what
  a voice turn says is unchanged.
* **A cast from a paused phone started the room playing.** play-tracks
  takes ``start_paused``: the room waits paused at the position, held as a
  person's pause, until someone presses play.

Plus the web's missing ``previous`` proxy, and next/previous clearing a
person's pause (MPD plays the song it moves to).

Everything that can run without Postgres does (stub MPD, a fake session,
route functions called directly); the end-to-end checks through the
running apps are ``requires_db``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from domovoi import main as core_main
from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient
from domovoi.handlers.music import _NEXT_RE, MusicHandler
from domovoi.models import Context, Response
from domovoi.music_pause import note_paused_by_person, paused_by_person
from domovoi.tests.conftest import requires_db

UNREACHABLE = "I couldn't reach the music player."


# ─── fakes ──────────────────────────────────────────────────────────────────


class _Row:
    def __init__(self, id: int, title: str, artist: str, file_path: str) -> None:
        self.id, self.title, self.artist, self.file_path = id, title, artist, file_path


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeSession:
    """Just enough AsyncSession for ``_play_random``: its library pick
    answers one row; every other statement (the play-history write) finds
    nothing."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.statements.append(sql)
        if "FROM library_tracks" in sql:
            return _Result(_Row(77, "Somebody Else", "Random Band", "/music/Somebody Else.mp3"))
        return _Result(None)

    async def commit(self) -> None:
        pass


class _App:
    """An app with the state the music handler and the pause hold read."""

    class state:  # noqa: N801 — mirrors app.state
        current_playlist: dict = {}


class _BrokenMPD(MPDStubClient):
    """A room whose MPD doesn't answer (frozen, or its container gone)."""

    async def pause(self) -> None:
        raise ConnectionError("No response from server while reading MPD hello")

    async def resume(self) -> None:
        raise ConnectionError("No response from server while reading MPD hello")

    async def next(self) -> None:
        raise ConnectionError("No response from server while reading MPD hello")

    async def previous(self) -> None:
        raise ConnectionError("No response from server while reading MPD hello")

    async def stop(self) -> None:
        raise ConnectionError("No response from server while reading MPD hello")


def _specs(*titles: str) -> list[dict[str, str]]:
    return [{"title": t, "artist": "Handoff Band"} for t in titles]


async def _cast_queue(*titles: str) -> MPDStubClient:
    """kitchen's MPD after a cast of ``titles`` (play-tracks), playing."""
    mpd = MPDStubClient()
    await mpd.prepare_tracks(_specs(*titles))
    await mpd.resume()
    mpd_module._clients = {"kitchen": mpd}
    return mpd


async def _title(mpd: MPDStubClient) -> str | None:
    song = await mpd.current_song()
    return song.get("title") if song else None


def _ctx(app: Any = None) -> Context:
    return Context(session_id=None, room_id="kitchen", online=True, app=app or _App)


@pytest.fixture(autouse=True)
def _fresh_clients():
    saved = dict(mpd_module._clients)
    _App.state.current_playlist = {}
    if hasattr(_App.state, "music_paused_by_person"):
        del _App.state.music_paused_by_person
    yield
    mpd_module._clients = saved


# ─── 1. skip follows a cast queue ──────────────────────────────────────────


async def test_skip_in_a_cast_queue_plays_the_queues_next_song() -> None:
    mpd = await _cast_queue("Long Road", "Far Hills", "Slow River", "Warm Stones")
    session = _FakeSession()

    response = await MusicHandler()._smart_skip(_ctx(), session)

    assert await _title(mpd) == "Far Hills", response.text
    assert len(await mpd.queue_list()) == 4, "the cast queue was replaced"
    assert response.text == "Next track."
    assert response.music_action is None
    assert response.failure is None
    assert not any("library_tracks" in s for s in session.statements), (
        "a random library track was picked for a room playing a queue"
    )


async def test_the_spoken_skip_follows_the_queue_too() -> None:
    mpd = await _cast_queue("Long Road", "Far Hills", "Slow River")
    m = _NEXT_RE.match("skip this song")
    assert m

    response = await MusicHandler()._next_from_match(m, _ctx(), _FakeSession())

    assert await _title(mpd) == "Far Hills"
    assert response.text == "Next track."


async def test_the_tool_models_next_is_the_same_skip() -> None:
    """The tool path used MPD's bare next: on a one-song play that just
    ended the music where the spoken "next" picks another track."""
    mpd = MPDStubClient()
    await mpd.play_filename("Old Barrels")  # a spoken "play X": one song
    mpd_module._clients = {"kitchen": mpd}

    response = await MusicHandler().execute_from_tool({"action": "next"}, _ctx(), _FakeSession())

    assert response.text.startswith("Playing Somebody Else"), response.text
    assert response.music_action == "start"


async def test_skip_on_a_queues_last_song_ends_the_queue() -> None:
    mpd = await _cast_queue("Long Road", "Far Hills")
    await mpd.next()  # on Far Hills, the last one
    session = _FakeSession()

    response = await MusicHandler()._smart_skip(_ctx(), session)

    assert response.text == "That was the last song in the queue."
    assert response.music_action == "stop"
    assert await mpd.state() == "stop"
    assert not any("library_tracks" in s for s in session.statements)


async def test_a_one_song_play_keeps_the_smart_skip() -> None:
    """What the smart skip was written for: a spoken "play X" (or a
    play-track click, a playlist's song) leaves one song queued, and MPD's
    next would just end the music."""
    mpd = MPDStubClient()
    await mpd.prepare_tracks(_specs("Old Barrels"))  # a cast of one song
    await mpd.resume()
    mpd_module._clients = {"kitchen": mpd}

    response = await MusicHandler()._smart_skip(_ctx(), _FakeSession())

    assert response.text.startswith("Playing Somebody Else"), response.text
    assert response.music_action == "start"


async def test_previous_in_a_cast_queue_goes_back_one_song() -> None:
    mpd = await _cast_queue("Long Road", "Far Hills", "Slow River")
    await mpd.next()

    response = await MusicHandler()._simple_ack("previous", _ctx())

    assert response.text == "Previous track."
    assert await _title(mpd) == "Long Road"
    assert len(await mpd.queue_list()) == 3


async def test_the_queue_place_is_read_by_songid_then_by_position() -> None:
    handler = MusicHandler()

    class _Real:
        """The daemon's shapes: currentsong carries string id/pos."""

        def __init__(self, current: dict) -> None:
            self.current = current

        async def queue_list(self) -> list[dict]:
            return [{"id": 10, "pos": 0}, {"id": 11, "pos": 1}, {"id": 12, "pos": 2}]

        async def current_song(self) -> dict:
            return self.current

    mpd_module._clients = {"kitchen": _Real({"id": "11", "pos": "1"})}
    assert await handler._queue_place("kitchen") == (1, 3)
    mpd_module._clients = {"kitchen": _Real({"id": "12"})}  # the stub's shape: no pos
    assert await handler._queue_place("kitchen") == (2, 3)
    mpd_module._clients = {"kitchen": _Real({"pos": "2"})}
    assert await handler._queue_place("kitchen") == (2, 3)
    mpd_module._clients = {"kitchen": _Real({})}
    assert await handler._queue_place("kitchen") is None


# ─── the stub moves through its queue the way the daemon does ─────────────


async def test_the_stub_walks_its_queue() -> None:
    mpd = MPDStubClient()
    await mpd.prepare_tracks(_specs("One", "Two"), start_sec=40)
    assert await mpd.state() == "pause"
    await mpd.next()  # MPD plays the song it moves to, paused or not
    assert (await _title(mpd), await mpd.state()) == ("Two", "play")
    await mpd.previous()
    assert (await _title(mpd), await mpd.state()) == ("One", "play")
    assert await mpd.elapsed_sec() == 0.0
    await mpd.previous()  # the first song starts again
    assert await _title(mpd) == "One"
    await mpd.next()
    await mpd.next()  # past the last song: stopped
    assert await mpd.state() == "stop"


# ─── next / previous end a person's pause ────────────────────────────────


@pytest.mark.parametrize("action", ["next", "previous"])
async def test_next_and_previous_end_a_persons_pause(action: str) -> None:
    """MPD plays the song next/previous moves to, so a pause hold left in
    place kept the room paused after the next voice turn's auto-resume."""
    await _cast_queue("Long Road", "Far Hills", "Slow River")
    note_paused_by_person(_App, "kitchen", True)

    await MusicHandler()._simple_ack(action, _ctx())

    assert not paused_by_person(_App, "kitchen")


async def test_a_skip_within_the_queue_ends_a_persons_pause() -> None:
    await _cast_queue("Long Road", "Far Hills", "Slow River")
    note_paused_by_person(_App, "kitchen", True)

    await MusicHandler()._smart_skip(_ctx(), _FakeSession())

    assert not paused_by_person(_App, "kitchen")


async def test_pause_still_holds() -> None:
    await _cast_queue("Long Road", "Far Hills")
    await MusicHandler()._simple_ack("pause", _ctx())
    assert paused_by_person(_App, "kitchen")


# ─── 2. a control that did not happen says so ──────────────────────────────


@pytest.mark.parametrize("action", ["pause", "resume", "stop", "next", "previous"])
async def test_an_unreachable_player_is_marked_and_the_room_hears_the_same(action: str) -> None:
    mpd_module._clients = {"kitchen": _BrokenMPD()}

    response = await MusicHandler()._simple_ack(action, _ctx())

    assert response.text == UNREACHABLE  # what the room hears: unchanged
    assert response.failure == "unreachable"


async def test_a_refused_skip_is_marked_not_playing() -> None:
    mpd_module._clients = {"kitchen": MPDStubClient()}  # stopped
    response = await MusicHandler()._simple_ack("next", _ctx())
    assert response.text == "Nothing is playing right now."
    assert response.failure == "not_playing"


async def test_a_skip_in_a_queue_whose_player_is_gone_is_marked() -> None:
    class _QueueThenGone(MPDStubClient):
        async def next(self) -> None:
            raise ConnectionError("Connection refused")

    mpd = _QueueThenGone()
    await mpd.prepare_tracks(_specs("Long Road", "Far Hills"))
    mpd_module._clients = {"kitchen": mpd}

    response = await MusicHandler()._smart_skip(_ctx(), _FakeSession())

    assert response.failure == "unreachable"
    assert response.text == UNREACHABLE


async def test_a_one_song_skip_whose_player_is_gone_is_marked() -> None:
    class _Gone(MPDStubClient):
        async def prepare_search(self, query):
            raise ConnectionError("Connection refused")

        async def prepare_filename(self, *substrings):
            raise ConnectionError("Connection refused")

    mpd = _Gone()
    await mpd.play_filename("Old Barrels")
    mpd_module._clients = {"kitchen": mpd}

    response = await MusicHandler()._smart_skip(_ctx(), _FakeSession())

    assert (response.text, response.failure) == (UNREACHABLE, "unreachable")


async def test_play_and_now_playing_mark_an_unreachable_player() -> None:
    mpd_module._clients = {"kitchen": _BrokenMPD()}

    class _Down(_BrokenMPD):
        async def prepare_search(self, query):
            raise ConnectionError("Connection refused")

        async def current_song(self):
            raise ConnectionError("Connection refused")

    mpd_module._clients = {"kitchen": _Down()}
    handler = MusicHandler()
    played = await handler._play({"any": "long road"}, _ctx(), _FakeSession())
    asked = await handler._now_playing(_ctx())
    assert (played.text, played.failure) == (UNREACHABLE, "unreachable")
    assert (asked.text, asked.failure) == (UNREACHABLE, "unreachable")


def test_the_failure_mark_never_goes_on_the_wire() -> None:
    r = Response(text=UNREACHABLE, failure="unreachable")
    assert "failure" not in r.model_dump()
    assert r.failure == "unreachable"


def _routed(response: Response, monkeypatch) -> list:
    """admin_music_action with the router answering ``response``."""
    dispatched: list = []

    async def _route(transcript: str, room_id: str) -> Response:
        dispatched.append(("route", transcript, room_id))
        return response

    async def _dispatch(resp: Response, room_id: str, **kw: Any) -> None:
        dispatched.append(("dispatch", resp.music_action, room_id))

    monkeypatch.setattr(core_main, "_admin_route_intent", _route)
    monkeypatch.setattr(core_main, "_admin_dispatch_music", _dispatch)
    return dispatched


@pytest.mark.parametrize(
    ("failure", "status", "detail"),
    [
        ("unreachable", 502, "couldn't reach the music player in office"),
        ("not_playing", 409, "nothing is playing in office"),
        ("no_speakers", 503, "no satellite has connected"),
    ],
)
async def test_the_admin_control_answers_non_2xx_on_a_failure(
    monkeypatch, failure: str, status: int, detail: str
) -> None:
    text = "I can't play anything yet — no satellite has connected" if failure == "no_speakers" else UNREACHABLE
    _routed(Response(text=text, matched_handler="music", failure=failure), monkeypatch)

    with pytest.raises(HTTPException) as e:
        await core_main.admin_music_action("pause", "office")

    assert e.value.status_code == status
    assert detail in e.value.detail


async def test_a_control_that_happened_answers_ok(monkeypatch) -> None:
    calls = _routed(Response(text="Paused.", matched_handler="music"), monkeypatch)

    body = await core_main.admin_music_action("pause", "office")

    assert body["ok"] is True and body["text"] == "Paused."
    assert calls[0] == ("route", "pause the music", "office")


async def test_the_router_marks_a_house_with_no_speakers() -> None:
    from domovoi.router import _no_speakers_yet

    assert _no_speakers_yet(None, _ctx()).failure == "no_speakers"


# ─── 3. a cast from a paused phone waits paused ────────────────────────────


class _Session:
    """A connected satellite: records the music_start the cast sends."""

    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_music(self, url: str) -> bool:
        self.started.append(url)
        return True


@pytest.fixture
def core_state(monkeypatch):
    """main.app.state as the lifespan leaves it, for _admin_dispatch_music."""
    st = core_main.app.state
    for name, value in (
        ("active_sessions", {}),
        ("resumable_music", {}),
        ("current_playlist", {}),
        ("pending_music_start", {}),
        ("music_paused_by_person", set()),
    ):
        monkeypatch.setattr(st, name, value, raising=False)
    return st


def _start(room: str = "office") -> Response:
    return Response(text="ok", matched_handler="music", music_action="start",
                    music_stream_url=f"http://mpd/{room}.mp3")


async def test_a_paused_cast_is_held_as_a_persons_pause(core_state) -> None:
    sat = _Session()
    core_state.active_sessions["office"] = sat

    await core_main._admin_dispatch_music(_start(), "office", start_paused=True)

    assert sat.started == ["http://mpd/office.mp3"], "the room's player must still join the stream"
    assert paused_by_person(core_main.app, "office")


async def test_a_playing_cast_clears_an_old_pause(core_state) -> None:
    core_state.active_sessions["office"] = _Session()
    note_paused_by_person(core_main.app, "office", True)

    await core_main._admin_dispatch_music(_start(), "office")

    assert not paused_by_person(core_main.app, "office")


async def test_a_paused_cast_to_a_room_with_no_satellite_is_not_resumed(core_state, monkeypatch) -> None:
    import domovoi.streaming as streaming

    resumed: list[str] = []

    async def _resume(room_id: str) -> None:
        resumed.append(room_id)

    monkeypatch.setattr(streaming, "_resume_mpd_for_room", _resume)

    await core_main._admin_dispatch_music(_start("den"), "den", start_paused=True)
    assert resumed == []
    await core_main._admin_dispatch_music(_start("den"), "den")
    assert resumed == ["den"]


async def test_the_rooms_music_ready_leaves_a_paused_cast_paused(core_state) -> None:
    """The whole handshake: the cast prepared MPD paused at the position,
    the satellite's player joins and says music_ready, and MPD stays put
    until a person presses play."""
    import asyncio

    from domovoi.streaming import consume_music_ready

    mpd = MPDStubClient()
    await mpd.prepare_tracks(_specs("Long Road", "Far Hills"), start_sec=151)
    mpd_module._clients = {"office": mpd}

    class _Sat:
        async def start_music(self, url: str) -> bool:
            task = asyncio.get_running_loop().create_future()
            core_state.pending_music_start["office"] = {"url": url, "task": task}
            return True

    core_state.active_sessions["office"] = _Sat()
    await core_main._admin_dispatch_music(_start(), "office", start_paused=True)
    await consume_music_ready(core_main.app, "office")

    assert await mpd.state() == "pause"
    assert await mpd.elapsed_sec() == 151.0

    await MusicHandler()._simple_ack("resume", Context(room_id="office", app=core_main.app))
    assert await mpd.state() == "play"
    assert not paused_by_person(core_main.app, "office")


def test_the_core_body_takes_start_paused() -> None:
    from domovoi.main import _AdminPlayTracksBody

    assert _AdminPlayTracksBody(room_id="den", track_ids=[1]).start_paused is False
    assert _AdminPlayTracksBody(room_id="den", track_ids=[1], start_paused=True).start_paused is True


# ─── the web's proxies ──────────────────────────────────────────────────────


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})


def _web_posts(monkeypatch, answer=(200, {"ok": True})) -> list:
    import web.backend.api.music as music_api

    sent: list = []

    async def _post_admin(path: str, body: Any = None, headers: Any = None, **kw: Any):
        sent.append((path, body))
        return answer

    monkeypatch.setattr(music_api, "post_admin", _post_admin)
    return sent


async def test_the_web_proxies_previous(monkeypatch) -> None:
    import web.backend.api.music as music_api

    sent = _web_posts(monkeypatch)
    r = await music_api.previous("office", _request())
    assert r.status_code == 200
    assert sent == [("/v1/admin/music/previous/office", None)]


def test_the_web_previous_is_on_the_device_tier() -> None:
    from domovoi import admin_auth
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    def _calls(dependant):
        for dep in getattr(dependant, "dependencies", ()):
            if dep.call is not None:
                yield dep.call
            yield from _calls(dep)

    for rc in iter_route_contexts(web_app.routes):
        if getattr(rc, "path", None) == "/api/music/previous/{room_id}" and "POST" in (rc.methods or set()):
            assert admin_auth.require_device in list(_calls(rc.dependant))
            return
    raise AssertionError("POST /api/music/previous/{room_id} is missing from the web app")


async def test_a_failed_control_passes_through_the_web(monkeypatch) -> None:
    import web.backend.api.music as music_api

    _web_posts(monkeypatch, answer=(502, {"detail": "couldn't reach the music player in office"}))
    r = await music_api.pause("office", _request())
    assert r.status_code == 502


async def test_the_web_forwards_start_paused_only_when_set(monkeypatch) -> None:
    import web.backend.api.music as music_api
    from web.backend.schemas import CastTracksRequest

    sent = _web_posts(monkeypatch, answer=(200, {"played": True}))
    await music_api.play_tracks(
        CastTracksRequest(room_id="office", track_ids=[3, 4], start_sec=151.5, start_paused=True), _request()
    )
    await music_api.play_tracks(CastTracksRequest(room_id="office", track_ids=[3]), _request())

    assert sent[0][1] == {"room_id": "office", "track_ids": [3, 4], "start_sec": 151.5, "start_paused": True}
    assert sent[1][1] == {"room_id": "office", "track_ids": [3]}


# ─── end to end through the core (Postgres) ────────────────────────────────


@requires_db
async def test_skip_through_the_core_follows_the_cast_queue() -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        mpd = await _cast_queue("Long Road", "Far Hills", "Slow River")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/music/skip/kitchen")
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True
            assert await _title(mpd) == "Far Hills"
            r = await client.post("/v1/admin/music/previous/kitchen")
            assert r.status_code == 200, r.text
            assert await _title(mpd) == "Long Road"
            # The spoken form, through the router's fast path.
            r = await client.post("/v1/intent", json={"transcript": "next song", "room_id": "kitchen"})
            assert r.status_code == 200, r.text
            assert await _title(mpd) == "Far Hills"
            assert len(await mpd.queue_list()) == 3


@requires_db
async def test_a_pause_the_player_never_got_is_a_502_through_the_core() -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        mpd_module._clients = {"kitchen": _BrokenMPD()}
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/v1/admin/music/pause/kitchen")
            assert r.status_code == 502, r.text
            assert "couldn't reach the music player" in r.json()["detail"]
            # The voice turn still says what it said.
            r = await client.post("/v1/intent", json={"transcript": "pause the music", "room_id": "kitchen"})
            assert r.status_code == 200, r.text
            assert r.json()["text"] == UNREACHABLE


@requires_db
async def test_a_paused_cast_through_the_core_waits_paused(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text as sql_text

    from domovoi.db.session import engine
    from domovoi.main import app
    from domovoi.tests.conftest import TABLES_TO_TRUNCATE

    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with engine.begin() as conn:
            await conn.execute(
                sql_text(f"TRUNCATE {', '.join(TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
            )
            ids = []
            for title in ("Long Road", "Far Hills"):
                row = await conn.execute(
                    sql_text(
                        "INSERT INTO library_tracks (file_path, title, artist) "
                        "VALUES (:fp, :t, 'Handoff Band') RETURNING id"
                    ),
                    {"fp": f"/music/{title}.mp3", "t": title},
                )
                ids.append(int(row.scalar_one()))
        mpd = MPDStubClient()
        mpd_module._clients = {"kitchen": mpd}
        # A provisioned room has a stream; the stub-mode one has none, and
        # without one no start is dispatched at all.
        monkeypatch.setattr(mpd_module, "mpd_stream_url_for", lambda room: f"http://mpd/{room}.mp3")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/v1/admin/music/play-tracks",
                json={"room_id": "kitchen", "track_ids": ids, "start_sec": 151, "start_paused": True},
            )
            assert r.status_code == 200, r.text
            assert r.json()["paused"] is True
            assert await mpd.state() == "pause"
            assert paused_by_person(app, "kitchen")

            r = await client.post(
                "/v1/admin/music/play-tracks", json={"room_id": "kitchen", "track_ids": ids},
            )
            assert r.status_code == 200, r.text
            assert r.json()["paused"] is False
            assert not paused_by_person(app, "kitchen")
            assert await mpd.state() == "play"  # no satellite: resumed at once
