"""The control channel over the Windows named pipe (satellite/control_win.py).

The same verbs and framing as the Unix socket, held to the same fixture
(docs/uml/satellite-control-fixture.json), plus what the pipe adds: a DACL
naming only this user, the name claimed so nobody can squat it, and both
ends checking the other's user. Windows only. Every pipe here has a name of
its own, never the real satellite's.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import sys
import threading
import time
import uuid
from ctypes import wintypes

import pytest

from satellite import control

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="named pipes: Windows")

if sys.platform == "win32":
    from satellite import control_win, win32
    from satellite.tests.test_control_protocol import FIXTURE, FakeEngine


def _name() -> str:
    return win32.PIPE_STEM + "test-" + uuid.uuid4().hex


def _serve(engine, name):
    ready = threading.Event()
    box = {}

    def run():
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        server = control.ControlServer(engine, control_win.NamedPipeTransport(name, limit=control.MAX_LINE + 2))
        engine.server = server
        box["server"] = server
        try:
            loop.run_until_complete(server.start())
        except Exception as e:  # noqa: BLE001
            box["error"] = e
            ready.set()
            return
        ready.set()
        loop.run_forever()
        loop.run_until_complete(server.close())
        loop.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert ready.wait(5)
    if "error" in box:
        raise box["error"]

    def stop():
        box["loop"].call_soon_threadsafe(box["loop"].stop)
        t.join(5)
    return stop


@pytest.mark.parametrize("ex", FIXTURE["exchanges"] if sys.platform == "win32" else [], ids=lambda x: x["name"])
def test_every_fixture_exchange_over_the_pipe(ex):
    engine = FakeEngine(ex.get("engine"))
    name = _name()
    stop = _serve(engine, name)
    try:
        req = ex["request"]
        sock = control_win.PipeStream(name, timeout=5)
        try:
            sock.sendall(json.dumps(req).encode("utf-8") + b"\n")
            got = next(control.ControlClient._lines(sock))
        finally:
            sock.close()
    finally:
        stop()
    want = dict(ex["response"])
    if want.pop("$status", False):
        want.update(engine.control_status())
    assert {k: got.get(k) for k in want} == want


def test_the_client_watches_the_mic_go_live_over_the_pipe():
    engine = FakeEngine({"connection": "connected", "mic": "listening"})
    name = _name()
    stop = _serve(engine, name)
    try:
        c = control.ControlClient(name)
        assert c.call("mute")["mic"] == "muted" and c.call("unmute")["mic"] == "listening"
        stream = c.watch()
        assert next(stream)["mic"] == "listening"
        engine.status["mic"] = "capturing"
        engine.server.notify()
        assert next(stream)["mic"] == "capturing"
        stream.close()
    finally:
        stop()


def test_nobody_can_take_the_name_first():
    """A pipe already holding the name (another user who signed in first,
    say) is never joined: the engine's channel stays off instead."""
    name = _name()
    stop = _serve(FakeEngine(), name)
    try:
        with pytest.raises(OSError, match="already held"):
            asyncio.run(control_win.NamedPipeTransport(name).start(lambda r, w: None))
    finally:
        stop()


def test_no_engine_is_a_connection_error_not_a_hang():
    t0 = time.monotonic()
    with pytest.raises(ConnectionError):
        control.ControlClient(_name()).call("status")
    assert time.monotonic() - t0 < 5


def test_the_client_refuses_a_pipe_served_by_someone_else(monkeypatch):
    name = _name()
    stop = _serve(FakeEngine(), name)
    try:
        monkeypatch.setattr(win32, "peer_is_this_user", lambda pid, own_sid=None: False)
        with pytest.raises(ConnectionError, match="not yours"):
            control.ControlClient(name).call("status")
    finally:
        stop()


def test_the_engine_closes_a_client_of_another_user(monkeypatch):
    name = _name()
    stop = _serve(FakeEngine(), name)
    try:
        real = win32.peer_is_this_user
        # The client's check of the server passes; the server's check of
        # the client says "another user".
        monkeypatch.setattr(control_win.win32, "peer_is_this_user",
                            lambda pid, own_sid=None: pid != __import__("os").getpid() and real(pid, own_sid))
        sock = control_win.PipeStream.__new__(control_win.PipeStream)
        import _winapi

        sock._winapi = _winapi
        sock.timeout = 3
        sock._h = _winapi.CreateFile(name, control_win.GENERIC_READ | control_win.GENERIC_WRITE, 0,
                                     _winapi.NULL, control_win.OPEN_EXISTING, win32.FILE_FLAG_OVERLAPPED, _winapi.NULL)
        try:
            sock.sendall(b'{"verb":"status"}\n')
            assert sock.recv(4096) == b"", "the engine answered a stranger"
        finally:
            sock.close()
    finally:
        stop()


def _pipe_sddl(handle: int) -> str:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psd = wintypes.LPVOID()
    dacl = wintypes.LPVOID()
    rc = advapi.GetSecurityInfo(wintypes.HANDLE(handle), 6, 4, None, None, ctypes.byref(dacl), None,
                                ctypes.byref(psd))
    assert rc == 0, rc
    text = wintypes.LPWSTR()
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), wintypes.LPVOID)
    assert advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(psd, 1, 4, ctypes.byref(text), None)
    out = text.value
    kernel.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
    kernel.LocalFree(psd)
    return out


def test_the_pipe_s_dacl_is_this_user_alone_and_remote_clients_are_refused(monkeypatch):
    import _winapi

    calls = []
    real = _winapi.CreateNamedPipe

    def spy(*args):
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(_winapi, "CreateNamedPipe", spy)
    name = _name()
    sec = win32.PipeSecurity()
    h = win32.create_pipe_instance(name, sec, first=True)
    try:
        sddl = _pipe_sddl(h)
    finally:
        _winapi.CloseHandle(h)
        sec.close()
    sid = win32.current_user_sid()
    assert sddl == f"D:P(A;;FA;;;{sid})", sddl
    open_mode, pipe_mode = calls[0][1], calls[0][2]
    assert open_mode & win32.FILE_FLAG_FIRST_PIPE_INSTANCE
    assert pipe_mode & win32.PIPE_REJECT_REMOTE_CLIENTS
