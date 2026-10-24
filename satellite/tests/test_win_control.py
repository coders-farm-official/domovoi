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
from pathlib import Path

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
        with pytest.raises(ConnectionError, match="not this user's"):
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


# ─── security review of wf/sat-windows: F2, the low-integrity squatter, W6 ──

LOW_SERVER = r"""
import _winapi, sys, time
name = sys.argv[1]
h = _winapi.CreateNamedPipe(name, 0x3 | 0x00080000, 0, 1, 65536, 65536, 0, _winapi.NULL)
print("created", flush=True)
_winapi.ConnectNamedPipe(h, _winapi.NULL)
try:
    _winapi.ReadFile(h, 65536)
    _winapi.WriteFile(h, b'{"ok": true, "verb": "status", "mic": "muted", "connection": "connected"}\n')
except OSError:
    pass
time.sleep(1)
"""


def _low_integrity_python(tmp_path):
    """A copy of this Python labelled low integrity: what it starts runs as
    this user in a sandbox (as a browser's renderer does). Nothing it runs
    writes anything (-B)."""
    import os
    import shutil
    import subprocess

    base = Path(win32.own_image_dir())
    d = tmp_path / "lowpy"
    d.mkdir()
    for pattern in ("python.exe", "python3*.dll", "vcruntime140*.dll"):
        for f in base.glob(pattern):
            shutil.copy2(f, d / f.name)
    exe = d / "python.exe"
    icacls = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
    r = subprocess.run([str(icacls), str(exe), "/setintegritylevel", "low"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"could not label a copy of python.exe low: {r.stdout} {r.stderr}")
    return exe


def test_the_client_refuses_a_low_integrity_process_sitting_on_the_name(tmp_path):
    """The reviewer's lowil_squat probe: a sandboxed process of this same
    user creates the pipe and answers "muted". The SID matches; the
    integrity level does not, so the tray never believes it."""
    import os
    import subprocess

    exe = _low_integrity_python(tmp_path)
    name = _name()
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    proc = subprocess.Popen([str(exe), "-B", "-s", "-c", LOW_SERVER, name], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    try:
        first = proc.stdout.readline().strip()
        if first != "created":
            pytest.skip(f"the low-integrity Python did not start: {proc.stderr.read()[-300:]}")
        rid = win32.process_integrity_rid(proc.pid)
        assert rid is not None and rid < win32.SECURITY_MANDATORY_MEDIUM_RID, rid
        assert win32.peer_is_this_user(proc.pid), "the same user: only the integrity level tells them apart"
        with pytest.raises(ConnectionError, match="low integrity"):
            control.ControlClient(name, timeout=3).call("status")
    finally:
        proc.kill()
        proc.wait(10)


def test_the_client_talks_only_to_this_install_s_python(monkeypatch, tmp_path):
    name = _name()
    stop = _serve(FakeEngine(), name)
    try:
        assert control.ControlClient(name).call("status")["ok"]
        monkeypatch.setattr(win32, "own_image_dir", lambda: str(tmp_path))
        with pytest.raises(ConnectionError, match="not this satellite's Python"):
            control.ControlClient(name).call("status")
    finally:
        stop()


def test_the_engine_refuses_a_low_integrity_client(monkeypatch):
    name = _name()
    stop = _serve(FakeEngine(), name)
    try:
        import os

        real = win32.process_integrity_rid
        monkeypatch.setattr(win32, "process_integrity_rid",
                            lambda pid: win32.SECURITY_MANDATORY_LOW_RID if pid == os.getpid() else real(pid))
        # The client's own check of the server would refuse too (same
        # process); skip it to see the engine's side.
        monkeypatch.setattr(control_win.PipeStream, "__init__", _raw_client)
        sock = control_win.PipeStream(name)
        try:
            sock.sendall(b'{"verb":"status"}\n')
            assert sock.recv(4096) == b"", "the engine answered a sandboxed client"
        finally:
            sock.close()
    finally:
        stop()


def _raw_client(self, name, timeout=3, **_kw):
    import _winapi

    self._winapi = _winapi
    self.timeout = timeout
    self._h = _winapi.CreateFile(name, control_win.GENERIC_READ | control_win.GENERIC_WRITE, 0, _winapi.NULL,
                                 control_win.OPEN_EXISTING, win32.FILE_FLAG_OVERLAPPED, _winapi.NULL)


def test_a_squatted_name_stops_the_engine_with_78_before_it_listens(monkeypatch, capsys):
    """F2: the engine used to carry on with no control channel (a live mic
    the tray called "not running", Mute disabled). Now it asks to exit 78
    and says why in the line the supervisor keeps for the tray."""
    import _winapi
    import types

    from satellite import engine_platform, pc_engine

    name = _name()
    squat = _winapi.CreateNamedPipe(name, 0x3 | win32.FILE_FLAG_OVERLAPPED | win32.FILE_FLAG_FIRST_PIPE_INSTANCE,
                                    0, 255, 4096, 4096, 0, _winapi.NULL)
    try:
        monkeypatch.setattr(control, "default_transport", lambda _d: control_win.NamedPipeTransport(name))
        asked = []
        eng = types.SimpleNamespace(
            _platform=types.SimpleNamespace(control_socket=True),
            cfg=types.SimpleNamespace(device=types.SimpleNamespace(name="windows_pc")),
            _watch_windows_stop=lambda: None, _request_exit=asked.append,
        )
        asyncio.run(pc_engine.SpareDeviceEngine._start_control(eng))
    finally:
        _winapi.CloseHandle(squat)
    assert asked == [engine_platform.EXIT_CONFIG] and getattr(eng, "_control_server", None) is None
    err = capsys.readouterr().err
    assert err.startswith("domovoi-satellite: not starting: another process holds this room's control pipe")


def test_the_client_lends_the_server_no_rights():
    """W6: the client opens the pipe with SECURITY_SQOS_PRESENT |
    SECURITY_IDENTIFICATION, so a server can learn who connected but never
    act as them: the token it gets is identification-level."""
    import _winapi

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentThread.restype = wintypes.HANDLE
    adv.ImpersonateNamedPipeClient.argtypes = (wintypes.HANDLE,)
    adv.OpenThreadToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.POINTER(wintypes.HANDLE))
    adv.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD))
    name = _name()
    h = _winapi.CreateNamedPipe(name, 0x3 | win32.FILE_FLAG_FIRST_PIPE_INSTANCE, 0, 1, 4096, 4096, 0, _winapi.NULL)
    box = {}

    def serve():
        try:
            _winapi.ConnectNamedPipe(h, _winapi.NULL)
        except OSError:
            pass
        _winapi.ReadFile(h, 16)
        if not adv.ImpersonateNamedPipeClient(wintypes.HANDLE(h)):
            box["error"] = ctypes.get_last_error()
            return
        try:
            tok = wintypes.HANDLE()
            if not adv.OpenThreadToken(k32.GetCurrentThread(), win32.TOKEN_QUERY, True, ctypes.byref(tok)):
                box["error"] = ctypes.get_last_error()
                return
            level, got = wintypes.DWORD(), wintypes.DWORD()
            adv.GetTokenInformation(tok, 9, ctypes.byref(level), 4, ctypes.byref(got))   # TokenImpersonationLevel
            box["level"] = level.value
            k32.CloseHandle(tok)
        finally:
            adv.RevertToSelf()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        sock = control_win.PipeStream(name, timeout=3)
        try:
            sock.sendall(b"x")
            t.join(5)
        finally:
            sock.close()
    finally:
        _winapi.CloseHandle(h)
    assert "error" not in box, box
    assert box.get("level") == 1, f"SecurityIdentification (1), not {box.get('level')}"



def test_the_installer_s_wait_reaches_an_engine_in_another_python_folder(monkeypatch, tmp_path):
    """Round-2 re-check: the installer waits from the staged Python in
    %TEMP% while the engine runs the install's own. Taking its own folder
    as the engine's, it never connected and waited out --wait (exit 4);
    it names the install's folder instead."""
    import io

    from satellite import pc_engine

    engine_dir = win32.own_image_dir()                       # where this "engine" (in-process) runs
    monkeypatch.setattr(win32, "own_image_dir", lambda: str(tmp_path / "stage" / "python"))   # the staged one
    name = _name()
    stop = _serve(FakeEngine({"connection": "connected", "room": {"id": "den", "name": "Den"}}), name)
    try:
        out = io.StringIO()
        assert pc_engine.wait_for_approval(name, timeout=3, poll=0.2, out=out) == "no_engine", \
            "without the install's folder the staged Python refuses the engine"
        got = pc_engine.wait_for_approval(name, timeout=10, poll=0.2, out=io.StringIO(), image_dir=engine_dir)
        assert got == "connected"
    finally:
        stop()
