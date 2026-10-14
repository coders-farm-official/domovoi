"""The engine's local control channel (satellite/control.py) against its
fixture, docs/uml/satellite-control-fixture.json.

Three layers: :func:`control.handle` answers every fixture exchange; the
asyncio server frames, streams ``watch`` and refuses what it should over a
loopback transport (so this runs on any OS); and on POSIX the real Unix
socket is made 0600 and served to :class:`control.ControlClient`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from satellite import control

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "docs" / "uml" / "satellite-control-fixture.json").read_text(encoding="utf-8")
)
POSIX = sys.platform != "win32"


class FakeEngine:
    """The engine as the fixture describes it."""

    def __init__(self, overrides: dict | None = None):
        self.server: control.ControlServer | None = None
        eng = copy.deepcopy(FIXTURE["engine"])
        self.status = eng["status"]
        self.code = eng["approval_code"]
        self.logs = eng["logs"]
        self.volume_ok = True
        self.ran: list[str] = []
        self.identified = 0
        self.changes = 0
        self.pending = control.PendingActions(on_change=self._changed)
        overrides = dict(overrides or {})
        if "volume_available" in overrides:
            self.volume_ok = overrides.pop("volume_available")
        for item in overrides.pop("pending", []):
            action = self.pending.offer(item["kind"], item["summary"],
                                        (lambda k=item["kind"]: self.ran.append(k)),
                                        now=item["received_at"])
            # The fixture names the id, so a test can approve it by name.
            with self.pending._lock:
                self.pending._items[item["kind"]].id = item["id"]
            del action
        self.status.update(overrides)

    def _changed(self):
        self.changes += 1
        if self.server is not None:
            self.server.notify()

    # EngineApi
    def control_status(self):
        return {**copy.deepcopy(self.status), "pending": self.pending.list()}

    def control_set_muted(self, muted):
        self.status["mic"] = "muted" if muted else "listening"
        self._changed()

    def control_volume(self, level):
        if level is not None:
            self.status["volume"] = level
        return self.status["volume"]

    def control_volume_available(self):
        return self.volume_ok

    def control_approval_code(self):
        waiting = self.status["connection"] == "awaiting_approval"
        return waiting, self.code

    def control_logs_path(self):
        return dict(self.logs)

    def control_identify(self):
        self.identified += 1
        return True

    def control_pending(self):
        return self.pending


# ─── the protocol's tables are the fixture's ──────────────────────────────

def test_the_verbs_errors_and_states_are_the_fixture_s():
    assert FIXTURE["protocol"] == control.PROTOCOL
    assert FIXTURE["verbs"] == control.VERBS
    assert FIXTURE["errors"] == control.ERRORS
    assert FIXTURE["connections"] == control.CONNECTIONS
    assert tuple(FIXTURE["mic_states"]) == control.MIC_STATES
    assert tuple(FIXTURE["output_states"]) == control.OUTPUT_STATES
    assert FIXTURE["pending_kinds"] == control.PENDING_KINDS
    assert tuple(FIXTURE["permissions"]) == control.PERMISSIONS
    assert FIXTURE["framing"]["max_line_bytes"] == control.MAX_LINE
    assert set(FIXTURE["engine"]["status"]) == set(FIXTURE["status"])


def test_every_verb_has_an_exchange():
    used = {x["request"].get("verb") for x in FIXTURE["exchanges"] if isinstance(x["request"], dict)}
    assert set(control.VERBS) - {"watch"} <= used


@pytest.mark.parametrize("ex", FIXTURE["exchanges"], ids=lambda x: x["name"])
def test_every_exchange_answers_as_the_fixture_says(ex):
    engine = FakeEngine(ex.get("engine"))
    got = control.handle(engine, copy.deepcopy(ex["request"]))
    want = dict(ex["response"])
    if want.pop("$status", False):
        want.update(engine.control_status())
    if want["ok"]:
        assert got == want
    else:
        assert {k: got.get(k) for k in want} == want
        assert got.get("message")
    deadline = time.monotonic() + 2
    while sorted(engine.ran) != sorted(ex.get("ran", [])) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(engine.ran) == sorted(ex.get("ran", []))


def test_a_bug_in_the_engine_is_an_answer_not_a_crash():
    engine = FakeEngine()

    def boom():
        raise RuntimeError("kaput")
    engine.control_logs_path = boom
    got = control.handle(engine, {"verb": "logs-path"})
    assert got["ok"] is False and got["error"] == "failed" and "kaput" in got["message"]


# ─── pending actions ("ask me") ───────────────────────────────────────────

def test_a_newer_action_of_the_same_kind_replaces_the_older():
    p = control.PendingActions()
    ran = []
    first = p.offer("update", "one", lambda: ran.append(1), now=1.0)
    second = p.offer("update", "two", lambda: ran.append(2), now=2.0)
    p.offer("restart", "r", lambda: ran.append(3), now=3.0)
    assert [x["summary"] for x in p.list()] == ["two", "r"]
    assert p.approve(first.id) == []
    assert [x["id"] for x in p.approve(second.id)] == [second.id]
    deadline = time.monotonic() + 2
    while ran != [2] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ran == [2]
    assert [x["kind"] for x in p.list()] == ["restart"]


def test_an_unknown_kind_is_a_programming_error():
    with pytest.raises(ValueError):
        control.PendingActions().offer("reboot", "x", lambda: None)


def test_a_summary_is_bounded():
    a = control.PendingActions().offer("config", "k" * 1000, lambda: None)
    assert len(a.summary) == 300


# ─── the server, over a loopback transport (any OS) ───────────────────────

class LoopbackTransport:
    """Test-only: the same server on 127.0.0.1, so the framing and the
    watch stream are exercised where there are no Unix sockets."""

    def __init__(self):
        self.port = None
        self._server = None

    def describe(self):
        return f"127.0.0.1:{self.port}"

    async def start(self, handler):
        self._server = await asyncio.start_server(handler, "127.0.0.1", 0, limit=control.MAX_LINE + 2)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self):
        self._server.close()
        await self._server.wait_closed()


async def _line(reader, timeout=3.0):
    return json.loads(await asyncio.wait_for(reader.readline(), timeout))


async def _closed(reader, timeout=3.0) -> bool:
    """Whether the server closed the connection (Windows reports a reset)."""
    try:
        return await asyncio.wait_for(reader.read(), timeout) == b""
    except ConnectionError:
        return True


async def test_requests_on_one_connection_are_answered_in_order():
    engine = FakeEngine()
    transport = LoopbackTransport()
    server = control.ControlServer(engine, transport)
    engine.server = server
    await server.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", transport.port)
        w.write(b'{"verb":"status","req":1}\n{"verb":"code","req":2}\nnot json\n')
        await w.drain()
        a, b, c = await _line(r), await _line(r), await _line(r)
        assert (a["req"], a["ok"], a["connection"]) == (1, True, "awaiting_approval")
        assert (b["req"], b["code"]) == (2, "482913")
        assert (c["ok"], c["error"]) == (False, "bad_request")
        w.close()
    finally:
        await server.close()


async def test_watch_streams_every_change_and_only_changes():
    engine = FakeEngine({"connection": "connected", "mic": "listening"})
    transport = LoopbackTransport()
    server = control.ControlServer(engine, transport)
    engine.server = server
    await server.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", transport.port)
        w.write(b'{"verb":"watch"}\n')
        await w.drain()
        first = await _line(r)
        assert first["ok"] and first["mic"] == "listening"
        # The mic thread starts a capture: the mic-live indicator.
        engine.status["mic"] = "capturing"
        server.notify()
        ev = await _line(r)
        assert ev["event"] == "status" and ev["mic"] == "capturing"
        # A notify with nothing changed sends nothing.
        server.notify()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(r.readline(), 0.3)
        # Requests still work on a watching connection.
        w.write(b'{"verb":"mute","req":"m"}\n')
        await w.drain()
        lines = [await _line(r), await _line(r)]
        reply = next(x for x in lines if x.get("verb") == "mute")
        event = next(x for x in lines if x.get("event") == "status")
        assert reply["ok"] and reply["mic"] == "muted" and event["mic"] == "muted"
        w.close()
    finally:
        await server.close()


async def test_an_over_long_line_is_refused_and_the_connection_closed():
    engine = FakeEngine()
    transport = LoopbackTransport()
    server = control.ControlServer(engine, transport)
    await server.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", transport.port)
        w.write(b'{"verb":"status","pad":"' + b"x" * (control.MAX_LINE + 10) + b'"}\n')
        await w.drain()
        got = await _line(r)
        assert got["error"] == "bad_request"
        assert await _closed(r)
    finally:
        await server.close()


async def test_an_idle_connection_is_closed(monkeypatch):
    monkeypatch.setattr(control, "IDLE_TIMEOUT_SEC", 0.2)
    transport = LoopbackTransport()
    server = control.ControlServer(FakeEngine(), transport)
    await server.start()
    try:
        r, _w = await asyncio.open_connection("127.0.0.1", transport.port)
        assert await _closed(r)
    finally:
        await server.close()


async def test_connections_past_the_limit_are_closed(monkeypatch):
    monkeypatch.setattr(control, "MAX_CLIENTS", 2)
    transport = LoopbackTransport()
    server = control.ControlServer(FakeEngine(), transport)
    await server.start()
    try:
        held = [await asyncio.open_connection("127.0.0.1", transport.port) for _ in range(2)]
        for _r, w in held:
            w.write(b'{"verb":"watch"}\n')
            await w.drain()
        for r, _w in held:
            await _line(r)
        r3, w3 = await asyncio.open_connection("127.0.0.1", transport.port)
        w3.write(b'{"verb":"status"}\n')
        await w3.drain()
        assert await _closed(r3)
    finally:
        await server.close()


def test_the_default_transport_is_the_socket_in_the_config_dir(tmp_path):
    if not POSIX:
        # Windows: the user's named pipe (satellite/tests/test_win_platform.py).
        from satellite import control_win

        assert isinstance(control.default_transport(tmp_path), control_win.NamedPipeTransport)
        assert control.is_pipe_address(control.default_address(tmp_path))
        return
    assert control.default_address(tmp_path) == tmp_path / "control.sock"
    t = control.default_transport(tmp_path)
    assert isinstance(t, control.UnixSocketTransport)
    assert t.path == tmp_path / "control.sock"


# ─── the real Unix socket (POSIX) ─────────────────────────────────────────

posix_only = pytest.mark.skipif(not POSIX, reason="Unix sockets: the Linux container runs these")


def _serve_in_thread(engine, path):
    """Run a ControlServer on its own loop in a thread; returns a stopper."""
    ready = threading.Event()
    box = {}

    def run():
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        server = control.ControlServer(engine, control.UnixSocketTransport(path))
        engine.server = server
        box["server"] = server
        loop.run_until_complete(server.start())
        ready.set()
        loop.run_forever()
        loop.run_until_complete(server.close())
        loop.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)

    def stop():
        box["loop"].call_soon_threadsafe(box["loop"].stop)
        t.join(5)
    return stop


@posix_only
def test_the_socket_is_0600_in_a_0700_directory_and_answers_the_client(tmp_path):
    cfg = tmp_path / "cfg"
    path = cfg / "control.sock"
    engine = FakeEngine({"connection": "connected", "mic": "listening"})
    stop = _serve_in_thread(engine, path)
    try:
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o700
        client = control.ControlClient(path)
        assert client.call("status")["connection"] == "connected"
        assert client.call("mute")["mic"] == "muted"
        assert client.call("unmute")["mic"] == "listening"
        assert client.call("volume", level=30)["volume"] == 30
    finally:
        stop()
    assert not path.exists(), "the socket is removed when the engine stops"


@posix_only
def test_the_client_watches_the_mic_go_live(tmp_path):
    path = tmp_path / "control.sock"
    engine = FakeEngine({"connection": "connected", "mic": "listening"})
    stop = _serve_in_thread(engine, path)
    try:
        stream = control.ControlClient(path).watch()
        assert next(stream)["mic"] == "listening"
        engine.status["mic"] = "capturing"
        engine.server.notify()
        assert next(stream)["mic"] == "capturing"
        stream.close()
    finally:
        stop()


@posix_only
def test_a_stale_socket_is_replaced_but_a_file_is_never(tmp_path):
    path = tmp_path / "control.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    stop = _serve_in_thread(FakeEngine(), path)
    try:
        assert control.ControlClient(path).call("status")["ok"]
    finally:
        stop()
    path.write_text("not a socket")
    transport = control.UnixSocketTransport(path)
    with pytest.raises(OSError):
        asyncio.run(transport.start(lambda r, w: None))
    assert path.read_text() == "not a socket"


@posix_only
def test_a_client_with_no_engine_gets_a_connection_error(tmp_path):
    with pytest.raises(ConnectionError):
        control.ControlClient(tmp_path / "nothing.sock").call("status")


@posix_only
def test_peer_uid_is_read_from_the_socket(tmp_path):
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert control.peer_uid(a) == os.getuid()
    finally:
        a.close()
        b.close()
