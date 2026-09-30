"""A music_start goes out only once the room's stream is serving.

Office satellite, 2026-09-30 04:16Z: the first cast from the web UI since
the room's MPD daemon had restarted. The cast prepared MPD paused (play,
then an immediate pause) and sent music_start; the satellite's mpg123 was
refused within 229 ms, and the music_ready fallback unpaused MPD five
seconds later — to nobody. MPD opens its http output only when the player
is unpaused, and always_on only keeps it open after that, so a daemon that
has not played since it started has no stream after a paused prepare. It is
deterministic, not a race: measured on the real image (MPD 0.23.12), the
port still refused seconds after the prepare, and `pause 0` + `pause 1`
opened it within a few ms with the song still at 0.000.

Pinned here (no database, no Docker — a local socket stands in for the
room's stream port, a fake for its control connection):

* `probe_stream` believes only an HTTP 200 status line: a connection that
  is accepted and closed with nothing — Docker's port proxy in front of a
  container that refused, or a daemon restored paused — is not serving;
* `ensure_stream_serving` costs one probe when the stream is up; opens a
  closed one by priming the paused daemon; keeps priming while a slow
  decoder starts; gives up at once when MPD is stopped or unreachable, and
  within its bound when it never opens; never splits the pause pair when
  the turn waiting on it is cancelled;
* `send_music_start`: the stream is serving before the frame is sent, and
  the handshake is armed after it; every music_start in the core goes
  through it (a source scan), and the voice turn, the auto-resume and the
  admin cast path all reach it;
* `music_failed` (the satellite gave up): listed in `ready.features`;
  pauses MPD when the room is still meant to be playing that stream and no
  newer start is pending, and leaves it alone otherwise.
"""

from __future__ import annotations

import ast
import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domovoi.clients import mpd as mpd_module
from domovoi.clients.mpd import MPDStubClient, ensure_stream_serving, probe_stream
from domovoi.config import settings
from domovoi.main import app
from domovoi.models import Response
from domovoi.streaming import StreamSession, send_music_start
from domovoi.tests.test_streaming import _FakeTTS, _FakeWhisper, _hello, _patch_pipeline

ROOM = "office"
HOST = "127.0.0.1"


class FakeRoom:
    """One room's MPD as the core sees it: a stream port that answers
    ``200`` only once the output has been opened (before that it accepts
    and closes, like Docker's proxy in front of a refusing container), and
    the control-side ``prime_stream_output``. ``opens_on`` is which prime
    finds the decoder started (a slow stream URL needs several)."""

    def __init__(self, state: str = "pause", *, serving: bool = False, opens_on: int = 1):
        self.state = state
        self.serving = serving
        self.opens_on = opens_on
        self.primes = 0
        self.probes = 0
        self.pauses: list[int] = []
        self.pair_gap = 0.0
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def __aenter__(self) -> "FakeRoom":
        self.server = await asyncio.start_server(self._client, HOST, 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _client(self, reader, writer) -> None:
        self.probes += 1
        if self.serving:
            await reader.readline()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\n\r\n")
            writer.write(b"\xff\xfb" + b"\x00" * 400)
            await writer.drain()
        writer.close()

    # The control connection (RealMPDClient.prime_stream_output's contract).
    async def prime_stream_output(self) -> str:
        self.primes += 1
        state = self.state
        if state == "pause":
            self.pauses.append(0)
            if self.primes >= self.opens_on:
                self.serving = True
            await asyncio.sleep(self.pair_gap)
            self.pauses.append(1)
        return state


@pytest.fixture
def real_mode(monkeypatch):
    """What the core looks like outside the test suite: no stubs, one room
    provisioned. Yields a function that installs a FakeRoom as that room's
    MPD."""
    monkeypatch.setattr(settings, "use_stubs", False)
    assert settings.mpd_host == HOST

    def install(room: FakeRoom) -> None:
        monkeypatch.setitem(mpd_module._room_ports, ROOM, (16650, room.port))
        monkeypatch.setitem(mpd_module._clients, ROOM, room)

    return install


def url_for(room: FakeRoom) -> str:
    # The host part is the LAN address the satellite dials; the core probes
    # the same port on its own loopback.
    return f"http://192.0.2.10:{room.port}"


# ─── probe_stream ──────────────────────────────────────────────────────────


async def test_the_probe_believes_only_a_200() -> None:
    async with FakeRoom(serving=True) as room:
        assert await probe_stream(HOST, room.port) is True
    async with FakeRoom(serving=False) as room:
        # Accepted, then closed with no bytes: what the office port did
        # from the core's side at 04:16Z.
        assert await probe_stream(HOST, room.port) is False


async def test_the_probe_refuses_other_answers_and_no_listener() -> None:
    async def not_found(reader, writer):
        await reader.readline()
        writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(not_found, HOST, 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await probe_stream(HOST, port) is False
    finally:
        server.close()
        await server.wait_closed()
    # Nothing listening there any more.
    assert await probe_stream(HOST, port, timeout=0.3) is False


# ─── ensure_stream_serving ─────────────────────────────────────────────────


async def test_a_serving_stream_costs_one_probe_and_no_prime(real_mode) -> None:
    async with FakeRoom(serving=True) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, url_for(room)) is True
        assert time.monotonic() - started < 0.5
        assert room.probes == 1 and room.primes == 0


async def test_a_fresh_daemons_closed_stream_is_opened_and_left_paused(real_mode) -> None:
    async with FakeRoom("pause", serving=False) as room:
        real_mode(room)
        assert await ensure_stream_serving(ROOM, url_for(room)) is True
        assert room.primes == 1
        assert room.pauses == [0, 1]            # opened, and paused again
        assert room.serving


async def test_a_slow_decoder_is_waited_out(real_mode) -> None:
    async with FakeRoom("pause", serving=False, opens_on=4) as room:
        real_mode(room)
        assert await ensure_stream_serving(ROOM, url_for(room), timeout=3.0) is True
        assert room.primes == 4
        assert room.pauses == [0, 1] * 4


async def test_a_playing_daemon_is_probed_not_toggled(real_mode) -> None:
    """"play" opens the output by itself once its decoder starts."""
    async with FakeRoom("play", serving=False) as room:
        real_mode(room)
        assert await ensure_stream_serving(ROOM, url_for(room), timeout=0.2) is False
        assert room.pauses == []


async def test_a_stopped_daemon_is_given_up_on_at_once(real_mode) -> None:
    async with FakeRoom("stop", serving=False) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, url_for(room), timeout=3.0) is False
        assert time.monotonic() - started < 0.5
        assert room.primes == 1 and room.pauses == []


async def test_an_unreachable_control_port_is_given_up_on_at_once(real_mode) -> None:
    class Down(FakeRoom):
        async def prime_stream_output(self) -> str:
            raise ConnectionRefusedError("control port")

    async with Down(serving=False) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, url_for(room), timeout=3.0) is False
        assert time.monotonic() - started < 0.5


async def test_a_stream_that_never_opens_is_bounded(real_mode) -> None:
    async with FakeRoom("pause", serving=False, opens_on=10**6) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, url_for(room), timeout=0.4) is False
        assert 0.35 < time.monotonic() - started < 1.5
        assert room.primes >= 3


async def test_the_default_bound_is_the_setting(real_mode, monkeypatch) -> None:
    monkeypatch.setattr(settings, "music_stream_ready_timeout_sec", 0.2)
    async with FakeRoom("pause", serving=False, opens_on=10**6) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, url_for(room)) is False
        assert time.monotonic() - started < 1.0


async def test_a_cancelled_wait_never_splits_the_pause_pair(real_mode) -> None:
    """A wake or a barge-in cancels the turn that is waiting here. `pause 0`
    without its `pause 1` would leave the song playing to nobody."""
    async with FakeRoom("pause", serving=False, opens_on=10**6) as room:
        room.pair_gap = 0.2
        real_mode(room)
        task = asyncio.create_task(ensure_stream_serving(ROOM, url_for(room)))
        await asyncio.sleep(0.05)               # inside the first pair
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)
        assert room.pauses == [0, 1]


async def test_a_stream_no_room_here_serves_is_not_waited_on(real_mode) -> None:
    """A URL whose port belongs to no provisioned room (a stale resumable
    entry, say): nothing here can open it, so no prime and no wait."""
    async with FakeRoom("pause", serving=False) as room:
        real_mode(room)
        started = time.monotonic()
        assert await ensure_stream_serving(ROOM, "http://192.0.2.10:1") is False
        assert time.monotonic() - started < 0.2
        assert room.primes == 0 and room.probes == 0


async def test_the_start_survives_a_check_that_blows_up(monkeypatch) -> None:
    async def broken(room_id, stream_url=None, *, timeout=None):
        raise RuntimeError("probe bug")

    monkeypatch.setattr(mpd_module, "ensure_stream_serving", broken)
    sess, fake_app = FakeSession(), FakeApp()
    await send_music_start(fake_app, sess, ROOM, "http://test.local:8051")
    try:
        assert [p for p, _ in sess.sent] == [
            {"type": "music_start", "stream_url": "http://test.local:8051"},
        ]
    finally:
        _drop_pending(fake_app)


async def test_stub_mode_checks_nothing(monkeypatch) -> None:
    assert settings.use_stubs
    assert await ensure_stream_serving("anywhere", "http://nowhere:1") is True


# ─── send_music_start ──────────────────────────────────────────────────────


class FakeSession:
    def __init__(self, room: FakeRoom | None = None) -> None:
        self.room = room
        self.sent: list[tuple[dict, bool | None]] = []

    async def _safe_send_text(self, payload: dict) -> None:
        serving = await probe_stream(HOST, self.room.port) if self.room else None
        self.sent.append((payload, serving))


class FakeApp:
    def __init__(self) -> None:
        self.state = type("S", (), {})()
        self.state.pending_music_start = {}


def _drop_pending(fake_app: FakeApp) -> None:
    for entry in fake_app.state.pending_music_start.values():
        entry["task"].cancel()


async def test_the_frame_goes_out_only_after_the_stream_serves(real_mode) -> None:
    async with FakeRoom("pause", serving=False) as room:
        real_mode(room)
        sess, fake_app = FakeSession(room), FakeApp()
        await send_music_start(fake_app, sess, ROOM, url_for(room))
        try:
            assert sess.sent == [({"type": "music_start", "stream_url": url_for(room)}, True)]
            pending = fake_app.state.pending_music_start[ROOM]
            assert pending["url"] == url_for(room)
            assert not pending["task"].done()
        finally:
            _drop_pending(fake_app)


async def test_the_frame_still_goes_out_when_the_stream_wont_open(real_mode, monkeypatch) -> None:
    """Not a veto: the satellite retries, and the fallback resumes MPD."""
    monkeypatch.setattr(settings, "music_stream_ready_timeout_sec", 0.1)
    async with FakeRoom("stop", serving=False) as room:
        real_mode(room)
        sess, fake_app = FakeSession(room), FakeApp()
        await send_music_start(fake_app, sess, ROOM, url_for(room))
        try:
            assert [p for p, _ in sess.sent] == [{"type": "music_start", "stream_url": url_for(room)}]
            assert ROOM in fake_app.state.pending_music_start
        finally:
            _drop_pending(fake_app)


def test_every_music_start_in_the_core_goes_through_send_music_start() -> None:
    """Five places used to build the frame themselves (the voice turn, the
    auto-resume, the intercom tail, the drop-in restore, the admin cast).
    A new one that does will skip the stream check — so there is one."""
    root = Path(__file__).resolve().parents[1]
    builders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "tests" in path.relative_to(root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Dict) and any(
                    isinstance(v, ast.Constant) and v.value == "music_start"
                    for v in node.values
                ):
                    builders.append(f"{path.relative_to(root).as_posix()}:{fn.name}")
    assert sorted(set(builders)) == ["streaming.py:send_music_start"], builders


# ─── the paths reach it ────────────────────────────────────────────────────


@pytest.fixture
def spy_ensure(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def spy(room_id, stream_url=None, *, timeout=None):
        calls.append((room_id, stream_url))
        return True

    monkeypatch.setattr(mpd_module, "ensure_stream_serving", spy)
    return calls


@pytest.fixture(autouse=True)
def _accept_every_hello(monkeypatch):
    async def _accept(self, ctrl):
        return True

    monkeypatch.setattr(StreamSession, "_validate_pairing", _accept)


def _turn(ws) -> None:
    ws.send_text(json.dumps({"type": "utterance_start", "trigger": "wake_word"}))
    ws.send_bytes(b"\x00" * 1024)
    ws.send_text(json.dumps({"type": "utterance_end"}))
    assert ws.receive_json()["type"] == "transcript"
    assert ws.receive_json()["type"] == "response_start"
    ws.receive_bytes()
    assert ws.receive_json()["type"] == "response_end"


def test_a_voice_play_checks_the_stream_before_music_start(monkeypatch, spy_ensure) -> None:
    url = "http://test.local:8051"
    _patch_pipeline(
        monkeypatch, whisper=_FakeWhisper("play creep"), tts=_FakeTTS(),
        response=Response(text="Playing creep.", matched_handler="music",
                          matched_path="fast", online=True,
                          music_action="start", music_stream_url=url),
    )
    with TestClient(app) as client:
        app.state.resumable_music.clear()
        app.state.pending_music_start.clear()
        with client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
            _hello(ws, ROOM)
            _turn(ws)
            assert ws.receive_json() == {"type": "music_start", "stream_url": url}
            assert spy_ensure == [(ROOM, url)]


def test_the_auto_resume_after_a_turn_checks_the_stream(monkeypatch, spy_ensure) -> None:
    url = "http://test.local:8051"
    _patch_pipeline(
        monkeypatch, whisper=_FakeWhisper("what time is it"), tts=_FakeTTS(),
        response=Response(text="It's noon.", matched_handler="time",
                          matched_path="fast", online=True),
    )
    with TestClient(app) as client:
        app.state.resumable_music.clear()
        app.state.pending_music_start.clear()
        app.state.resumable_music[ROOM] = url
        try:
            with client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
                _hello(ws, ROOM)
                _turn(ws)
                assert ws.receive_json() == {"type": "music_start", "stream_url": url}
                assert spy_ensure == [(ROOM, url)]
        finally:
            app.state.resumable_music.clear()


async def test_an_admin_cast_checks_the_stream_before_music_start(real_mode, monkeypatch) -> None:
    """The owner's path: /v1/admin/music/play-track → _admin_dispatch_music."""
    from domovoi import main as main_module

    async with FakeRoom("pause", serving=False) as room:
        real_mode(room)
        sess, url = FakeSession(room), url_for(room)
        monkeypatch.setattr(app.state, "active_sessions", {ROOM: sess}, raising=False)
        monkeypatch.setattr(app.state, "resumable_music", {}, raising=False)
        monkeypatch.setattr(app.state, "current_playlist", {}, raising=False)
        monkeypatch.setattr(app.state, "pending_music_start", {}, raising=False)
        response = Response(text="ok", matched_handler="music",
                            music_action="start", music_stream_url=url)
        await main_module._admin_dispatch_music(response, ROOM)
        try:
            assert sess.sent == [({"type": "music_start", "stream_url": url}, True)]
            assert room.pauses == [0, 1]
            assert ROOM in app.state.pending_music_start
        finally:
            for entry in app.state.pending_music_start.values():
                entry["task"].cancel()


# ─── music_failed ──────────────────────────────────────────────────────────


def _failed_then_flush(ws, url: str) -> None:
    ws.send_text(json.dumps({
        "type": "music_failed", "stream_url": url,
        "reason": "connection failed: Connection refused", "attempts": 6,
    }))
    ws.send_text(json.dumps({"type": "ping"}))
    # Handled, not answered with `error`.
    assert ws.receive_json() == {"type": "pong"}


@pytest.fixture
def playing_room():
    stub = MPDStubClient()
    stub._state = "play"
    stub._song = {"file": "stub/creep.mp3"}
    mpd_module._clients[ROOM] = stub
    yield stub
    mpd_module._clients.pop(ROOM, None)


def test_ready_lists_music_failed(monkeypatch) -> None:
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())
    with TestClient(app) as client, client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
        assert "music_failed" in _hello(ws, ROOM)["features"]


def test_music_failed_pauses_a_room_that_plays_to_nobody(monkeypatch, playing_room, caplog) -> None:
    url = "http://test.local:8051"
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())
    with TestClient(app) as client:
        app.state.resumable_music[ROOM] = url
        app.state.pending_music_start.pop(ROOM, None)
        try:
            with client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
                _hello(ws, ROOM)
                _failed_then_flush(ws, url)
            assert playing_room._state == "pause"
            # Kept, so the next turn's auto-resume tries again.
            assert app.state.resumable_music[ROOM] == url
        finally:
            app.state.resumable_music.clear()
    assert "could not play" in caplog.text


def test_music_failed_leaves_a_newer_start_alone(monkeypatch, playing_room) -> None:
    url = "http://test.local:8051"
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())
    with TestClient(app) as client:
        app.state.resumable_music[ROOM] = url
        app.state.pending_music_start[ROOM] = {"url": url, "task": None}
        try:
            with client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
                _hello(ws, ROOM)
                _failed_then_flush(ws, url)
            assert playing_room._state == "play"
        finally:
            app.state.resumable_music.clear()
            app.state.pending_music_start.clear()


def test_music_failed_leaves_a_room_that_moved_on_alone(monkeypatch, playing_room) -> None:
    _patch_pipeline(monkeypatch, whisper=_FakeWhisper())
    with TestClient(app) as client:
        app.state.resumable_music.clear()     # stopped since
        app.state.pending_music_start.pop(ROOM, None)
        with client.websocket_connect(f"/v1/stream/{ROOM}") as ws:
            _hello(ws, ROOM)
            _failed_then_flush(ws, "http://test.local:8051")
        assert playing_room._state == "play"


# ─── adding to an idle room's queue starts it ──────────────────────────────


async def test_queue_add_to_an_idle_room_leaves_mpd_paused_on_the_first_track(monkeypatch) -> None:
    """POST /v1/admin/music/queue/{room}/add to a stopped, empty room says
    `started` and sends music_start. It used to "start" with `resume` —
    `pause 0`, a no-op on a stopped MPD (measured on 0.23.12) — so MPD never
    played and the satellite was sent to a stream with nothing behind it.
    Now it lands paused on the first added track, like every other start,
    and the music_ready handshake unpauses it."""
    from contextlib import asynccontextmanager

    from domovoi import main as main_module
    from domovoi.handlers.shared import play_history

    class _Rows:
        def all(self):
            return [(7, "Radiohead/Creep.mp3", "Creep", "Radiohead")]

    class _Db:
        async def execute(self, *args, **kwargs):
            return _Rows()

    @asynccontextmanager
    async def fake_scope():
        yield _Db()

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(main_module, "session_scope", fake_scope)
    monkeypatch.setattr(main_module, "_log_queue_edit", nothing)
    monkeypatch.setattr(play_history, "record_media_play", nothing)
    stub = MPDStubClient()
    monkeypatch.setitem(mpd_module._clients, ROOM, stub)
    monkeypatch.setattr(mpd_module, "mpd_stream_url_for", lambda room: "http://test.local:8051")
    sess = FakeSession()
    monkeypatch.setattr(app.state, "active_sessions", {ROOM: sess}, raising=False)
    monkeypatch.setattr(app.state, "resumable_music", {}, raising=False)
    monkeypatch.setattr(app.state, "current_playlist", {}, raising=False)
    monkeypatch.setattr(app.state, "pending_music_start", {}, raising=False)

    result = await main_module.admin_music_queue_add(
        ROOM, main_module._AdminQueueAddBody(track_ids=[7]),
    )
    try:
        assert result["started"] is True
        assert await stub.state() == "pause"
        assert (await stub.current_song())["title"] == "Creep"
        assert [p["type"] for p, _ in sess.sent] == ["music_start"]
        assert ROOM in app.state.pending_music_start
    finally:
        for entry in app.state.pending_music_start.values():
            entry["task"].cancel()


async def test_start_paused_on_an_empty_queue_starts_nothing() -> None:
    stub = MPDStubClient()
    assert await stub.start_paused() is False
    assert await stub.state() == "stop"
