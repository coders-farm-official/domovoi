"""The satellite's half of a support session (satellite/support_shell.py).

Pins: only a Pi on a prepared card with the root-owned server pin, a
watchdog, no root unit running code its account can write, no sudo and no
free-argument sudo rule declares it (no spare PCs or phones in 1.5.0);
every open, input and allow must carry the pinned server's signature for
this satellite, this connection and this device's own support key (H3);
every frame the device sends is signed with that key, in one rising
sequence, under a direction label of its own; a device with no key or no
sandbox never declares it; the shell's sandbox (satellite/support_sandbox.py:
the mount plan anywhere, the real namespaces on Linux where they can be
built); the device's own limits (60-minute
cap, the session's time and idle limits, 16 KB frames, 64 KB/s, 4 KB
input, one at a time, busy refusals); input never blocks the caller (M3);
only key presses count as activity; echo reporting; the end (the shell's
group hung up, `support_closed` only when the device ended it, the service
restart through the client's `on_end`, an exit when the restart can't be
asked for). A fake PTY everywhere, and the real one on Linux (skipped
elsewhere, run in CI).
"""

from __future__ import annotations

import base64
import inspect
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from satellite import client  # the audio stand-ins hold during collection (conftest)
from satellite import server_identity as si
from satellite import support_shell as sh

# ─── who declares it ──────────────────────────────────────────────────────


def _pi(tmp_path: Path, model: str = "Raspberry Pi Zero 2 W Rev 1.0", prepared: bool = True):
    m = tmp_path / "model"
    m.write_text(model + "\x00")
    state = tmp_path / "state"
    if prepared:
        state.mkdir()
    return m, state


SUDO_LIST = """Matching Defaults entries for domovoi on sat:
    env_reset

User domovoi may run the following commands on sat:
    (root) NOPASSWD: /usr/bin/systemctl --no-block restart domovoi-satellite.service
    (root) NOPASSWD: /usr/local/sbin/domovoi-apply-payload
    (root) NOPASSWD: /usr/local/sbin/domovoi-reboot ""
"""


def _run(rc: int | None = None, exc: Exception | None = None, listing: str = SUDO_LIST,
         list_rc: int = 0):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if exc is not None:
            raise exc
        if argv == ["sudo", "-n", "-l"]:
            return SimpleNamespace(returncode=list_rc, stdout=listing)
        return SimpleNamespace(returncode=rc)

    return run, calls


def declare(model, state, run, **kw):
    kw.setdefault("sandbox", lambda: True)
    kw.setdefault("root_pin", lambda: True)
    kw.setdefault("root_path", lambda: None)
    kw.setdefault("watchdog", lambda: "/run/systemd/notify")
    return sh.should_declare(is_pc=kw.pop("is_pc", False), platform=kw.pop("platform", "linux"),
                             model_path=model, state_dir=state, run=run, **kw)


def test_a_prepared_pi_without_sudo_declares_it(tmp_path) -> None:
    model, state = _pi(tmp_path)
    run, calls = _run(rc=1)
    assert declare(model, state, run)
    assert calls == [["sudo", "-n", "true"], ["sudo", "-n", "-l"]]


def test_a_pi_whose_account_can_sudo_never_declares_it(tmp_path) -> None:
    model, state = _pi(tmp_path)
    run, _ = _run(rc=0)
    assert not declare(model, state, run)
    run, _ = _run(exc=subprocess.TimeoutExpired("sudo", 5))
    assert not declare(model, state, run)


def test_no_root_pin_no_watchdog_or_a_root_path_never_declares_it(tmp_path) -> None:
    """H3/M1/M3: the support frames are checked against the ROOT-owned pin;
    the ability needs the watchdog; any known way from this account to root
    keeps it off."""
    model, state = _pi(tmp_path)
    run, calls = _run(rc=1)
    assert not declare(model, state, run, root_pin=lambda: False)
    assert not declare(model, state, run, watchdog=lambda: None)
    assert not declare(model, state, run,
                       root_path=lambda: "domovoi-provisioning.service (/home/alex/domovoi)")
    assert calls == []
    for loose in ("    (root) NOPASSWD: /opt/xvf3800/xvf_host\n",
                  "    (root) NOPASSWD: /opt/xvf3800/xvf_host *\n",
                  "    (ALL : ALL) ALL\n"):
        run, _ = _run(rc=1, listing=SUDO_LIST + loose)
        assert not declare(model, state, run), loose
    run, _ = _run(rc=1, list_rc=1)            # can't list the rules: don't offer it
    assert not declare(model, state, run)


def test_no_sandbox_never_declares_it(tmp_path) -> None:
    """M3: a device that can't build the shell's sandbox (no unprivileged
    user namespaces) doesn't offer sessions, sudo or not."""
    model, state = _pi(tmp_path)
    run, _ = _run(rc=1)
    assert not declare(model, state, run, sandbox=lambda: False)
    run, _ = _run(exc=FileNotFoundError("sudo"))
    assert not declare(model, state, run, sandbox=lambda: False)
    assert declare(model, state, run, sandbox=lambda: True)


def test_the_sandbox_probe_runs_the_helper_isolated(tmp_path) -> None:
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw))
        return SimpleNamespace(returncode=0)

    assert sh.sandbox_available(run=run, home="/home/alex")
    argv, kw = seen[0]
    assert argv == [sys.executable, "-I", str(sh.SANDBOX_SCRIPT), "--probe", "--home", "/home/alex"]
    assert kw["stdin"] == subprocess.DEVNULL and kw["timeout"] > 0
    assert not sh.sandbox_available(run=lambda *a, **k: SimpleNamespace(returncode=72), home="/h")
    assert not sh.sandbox_available(
        run=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)), home="/h")


def test_no_support_key_means_not_offered() -> None:
    shell = sh.SupportShell(declared=True, emit=lambda f: None, key=lambda: None)
    assert not shell.declared and shell.support_key is None
    assert shell.new_connection() is None


def test_the_device_key_is_made_once_private_and_kept(tmp_path) -> None:
    path = tmp_path / ".domovoi" / "support_key"
    key = sh.DeviceKey.load_or_create(path)
    assert len(key.public) == 32
    if sys.platform != "win32":
        assert (path.stat().st_mode & 0o777) == 0o600
    again = sh.DeviceKey.load_or_create(path)
    assert again.public_b64 == key.public_b64
    # The vendored fallback signs exactly as cryptography does (Ed25519 is
    # deterministic), so a Pi on either backend makes the same signature.
    from satellite import _ed25519

    seed = bytes.fromhex(path.read_text().strip())
    assert _ed25519.sign(seed, b"m") == key.sign(b"m")
    path.write_text("not hex")
    with pytest.raises(ValueError):
        sh.DeviceKey.load_or_create(path)


def test_a_root_unit_running_the_accounts_code_is_found(tmp_path) -> None:
    units = tmp_path / "units"
    units.mkdir()
    (units / "domovoi-provisioning.service").write_text(
        "[Service]\nUser=root\nExecStart=/home/alex/satellite-venv/bin/python -m x\n"
        "WorkingDirectory=/home/alex/domovoi\n")
    (units / "domovoi-satellite.service").write_text(
        "[Service]\nUser=alex\nExecStart=/home/alex/satellite-venv/bin/python -m satellite.client\n")
    mine = lambda p: str(p).replace("\\", "/").startswith("/home/alex")  # noqa: E731
    found = sh.root_unit_runs_my_code((units,), writable=mine)
    assert found and found.startswith("domovoi-provisioning.service")
    (units / "domovoi-provisioning.service").write_text(
        "[Service]\nExecStart=/usr/local/lib/domovoi/provision\n")
    assert sh.root_unit_runs_my_code((units,), writable=mine) is None


def test_only_a_pi_on_a_prepared_card(tmp_path) -> None:
    run, calls = _run(rc=1)
    model, state = _pi(tmp_path)
    assert not declare(model, state, run, is_pc=True)
    assert not declare(model, state, run, platform="win32")
    assert not declare(model, state, run, platform="darwin")
    other = tmp_path / "other-model"
    other.write_text("Generic x86 board")
    assert not declare(other, state, run)
    assert not declare(tmp_path / "none", state, run)
    assert not declare(model, tmp_path / "not-prepared", run)
    assert calls == []          # sudo is never even asked off a prepared Pi


def test_the_client_says_it_in_its_hello_only_when_declared() -> None:
    hello = inspect.getsource(client.Satellite._hello_frame)
    assert '"support_shell": bool(getattr(self, "_support", None) and self._support.declared)' in hello
    assert 'hello["support_nonce"] = support.new_connection()' in hello
    assert 'hello["support_key"] = support.support_key' in hello
    init = inspect.getsource(client.Satellite.__init__)
    assert "support_shell.should_declare(is_pc=pc_engine.is_pc(cfg))" in init
    assert "satellite_id=lambda: self.cfg.room_id" in init


def test_a_spare_pc_never_declares_it() -> None:
    src = Path(sh.__file__).read_text(encoding="utf-8")
    assert "if is_pc or not platform.startswith(\"linux\")" in src


def test_the_unit_gives_the_client_a_watchdog_socket() -> None:
    unit = (Path(sh.__file__).parent / "domovoi-satellite.service").read_text(encoding="utf-8")
    assert "\nNotifyAccess=main\n" in unit


# ─── the light can't be turned off remotely ──────────────────────────────


def test_a_remote_set_config_cannot_darken_the_light_while_offered() -> None:
    changes = {"leds.enabled": False, "leds.brightness": 0, "leds.num_leds": 0,
               "wake.threshold": 0.5}
    kept, refused = sh.screen_led_changes(dict(changes), declared=True)
    assert kept == {"wake.threshold": 0.5}
    assert sorted(refused) == ["leds.brightness", "leds.enabled", "leds.num_leds"]
    kept, _ = sh.screen_led_changes({"leds.enabled": True, "leds.brightness": 16}, declared=True)
    assert kept == {"leds.enabled": True, "leds.brightness": 16}
    kept, refused = sh.screen_led_changes(dict(changes), declared=False)
    assert kept == changes and refused == []
    src = inspect.getsource(client.Satellite._handle_text_frame)
    assert "self._screen_support_leds(changes)" in src
    assert "self._screen_support_leds(changes)" in inspect.getsource(
        client.Satellite._apply_config_changes)


# ─── the session, on a fake PTY ───────────────────────────────────────────


class FakePty:
    def __init__(self) -> None:
        self.spawned: tuple[int, int] | None = None
        self.written: list[bytes] = []
        self.output: list[bytes] = []
        self.echo = True
        self.ended = False
        self.sizes: list[tuple[int, int]] = []
        self.reads: list[int] = []

    def spawn(self, cols, rows):
        self.spawned = (cols, rows)

    def read(self, max_bytes, timeout):
        self.reads.append(max_bytes)
        if self.ended:
            return None
        if not self.output:
            return b""
        chunk = self.output.pop(0)
        if len(chunk) > max_bytes:
            self.output.insert(0, chunk[max_bytes:])
            chunk = chunk[:max_bytes]
        return chunk

    def write(self, data, still_open=lambda: True):
        self.written.append(data)

    def resize(self, cols, rows):
        self.sizes.append((cols, rows))

    def echo_on(self):
        return self.echo

    def terminate(self):
        self.ended = True
        return 0


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()
WALL = 1_800_000_000.0
SAT = "kitchen"


def _pub(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


FP = si.fingerprint_for(_pub(KEY))
DEVICE_SEED = bytes(range(32))
DEVICE = sh.DeviceKey(DEVICE_SEED)


def plain(frame: dict) -> dict:
    """A frame the device sent, checked against its key and read back as
    its type, session id and signed fields (nothing travels outside the
    signature but the type and the session id)."""
    signed_part = frame["signed"]
    assert set(frame) == {"type", "session_id", "signed"}
    doc = signed_part["doc"]
    Ed25519PrivateKey.from_private_bytes(DEVICE_SEED).public_key().verify(
        base64.b64decode(signed_part["signature"]), si.support_reply_message(doc))
    assert doc["kind"] == frame["type"] and doc["session_id"] == frame["session_id"]
    assert doc["satellite_id"] == SAT
    fields = {k: v for k, v in doc.items() if k not in ("kind", "satellite_id", "nonce", "session_id", "seq")}
    return {"type": frame["type"], "session_id": frame["session_id"], **fields}


def signed(doc: dict, key=KEY) -> dict:
    return {"algorithm": "ed25519", "public_key": base64.b64encode(_pub(key)).decode(),
            "doc": doc, "signature": base64.b64encode(key.sign(si.support_message(doc))).decode()}


def make(declared: bool = True, busy=None, allow: bool = True):
    sent: list[dict] = []
    ends: list[tuple[str, bool]] = []
    opened: list[bool] = []
    pty = FakePty()
    clock = Clock()
    shell = sh.SupportShell(
        declared=declared, emit=sent.append, on_open=lambda: opened.append(True),
        on_end=lambda reason, by_server: ends.append((reason, by_server)),
        busy=busy or (lambda: None), backend_factory=lambda: pty, clock=clock,
        satellite_id=lambda: SAT, pinned=lambda: FP, wall=lambda: WALL,
        key=lambda: DEVICE,
    )
    # The tests drive step() and the input pump themselves.
    shell._thread = None
    w = SimpleNamespace(shell=shell, sent=sent, ends=ends, opened=opened, pty=pty, clock=clock)
    shell.new_connection()
    if allow and declared:
        shell.handle(allow_frame(w, True))
        assert shell.allowed
    return w


def doc(w, kind: str, **fields) -> dict:
    return {"kind": kind, "satellite_id": SAT, "nonce": w.shell.nonce,
            "satellite_key": DEVICE.public_b64, "issued_at": int(WALL), **fields}


def allow_frame(w, allowed: bool, **over) -> dict:
    return {"type": "support_allow", "signed": signed({**doc(w, "support_allow", allowed=allowed), **over})}


def open_frame(w, sid: str = "s1", key=KEY, **over) -> dict:
    fields = {"session_id": sid, "cols": 100, "rows": 30, "max_sec": 1800, "idle_sec": 600}
    d = {**doc(w, "support_open", **fields), **over}
    return {"type": "support_open", "session_id": sid, "signed": signed(d, key)}


def input_frame(w, data: bytes, seq: int, sid: str = "s1", active: bool = True, **over) -> dict:
    d = {**doc(w, "support_input", session_id=sid, seq=seq, active=active,
               data=base64.b64encode(data).decode()), **over}
    return {"type": "support_input", "session_id": sid, "signed": signed(d)}


def opened(w, monkeypatch):
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w.shell.handle(open_frame(w))
    assert plain(w.sent[-1]) == {"type": "support_opened", "session_id": "s1"}
    return w


def typed(w, data: bytes, seq: int, **kw) -> None:
    w.shell.handle(input_frame(w, data, seq, **kw))
    w.shell.pump_input("s1")


# ─── H3: only the pinned server, for this satellite and connection ───────


def _refused(w) -> str:
    assert w.sent[-1]["type"] == "support_refused", w.sent[-1]
    assert w.pty.spawned is None
    return plain(w.sent[-1])["reason"]


def test_an_unsigned_open_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make()
    w.shell.handle({"type": "support_open", "session_id": "x", "cols": 80, "rows": 24,
                    "max_sec": 3600, "idle_sec": 3600})
    assert "not signed" in _refused(w)


def test_a_forged_open_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make()
    w.shell.handle(open_frame(w, key=OTHER_KEY))            # another key
    _refused(w)
    frame = open_frame(w)
    frame["signed"]["doc"]["max_sec"] = 3600                # changed under the signature
    w.shell.handle(frame)
    _refused(w)
    frame = open_frame(w)
    frame["signed"]["signature"] = base64.b64encode(b"\0" * 64).decode()
    w.shell.handle(frame)
    _refused(w)
    w.shell.handle(open_frame(w, kind="support_allow"))     # signed for another kind
    _refused(w)


def test_a_wrong_satellite_stale_or_other_connection_open_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make()
    w.shell.handle(open_frame(w, satellite_id="bedroom"))
    _refused(w)
    w.shell.handle(open_frame(w, issued_at=int(WALL) - si.SUPPORT_MAX_SKEW_SEC - 1))
    _refused(w)
    w.shell.handle(open_frame(w, nonce="0" * 32))
    _refused(w)
    # Signed by the server, but naming another device key: the key in the
    # hello was swapped on the way, so this device refuses it.
    w.shell.handle(open_frame(w, satellite_key=base64.b64encode(b"k" * 32).decode()))
    _refused(w)
    w.shell.handle(allow_frame(w, False, satellite_key=base64.b64encode(b"k" * 32).decode()))
    assert w.shell.allowed


def test_a_replayed_open_is_refused(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    frame = open_frame(w)
    w.shell.handle({"type": "support_close", "session_id": "s1", "reason": "admin_ended"})
    w.pty.spawned = None
    w.shell.handle(frame)                                    # same connection, same open
    assert _refused(w) == "that open was already used"
    old_nonce = w.shell.nonce
    w.shell.new_connection()                                 # a new connection
    assert w.shell.nonce != old_nonce
    w.shell.handle(allow_frame(w, True))
    w.shell.handle(frame)                                    # signed for the old one
    _refused(w)


def test_no_open_before_a_signed_allow_on_this_connection(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make(allow=False)
    w.shell.handle(open_frame(w))
    assert _refused(w) == "support sessions are off for this satellite"
    w.shell.handle({"type": "support_allow", "allowed": True})            # unsigned
    w.shell.handle(allow_frame(w, True, nonce="1" * 32))                   # other connection
    assert not w.shell.allowed
    w.shell.handle(allow_frame(w, True))
    assert w.shell.allowed
    w.shell.new_connection()                                 # disarmed on every connect
    assert not w.shell.allowed


def test_a_signed_disallow_ends_an_open_session(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.shell.handle(allow_frame(w, False))
    assert not w.shell.allowed and not w.shell.active
    assert w.ends == [("switch_off", True)]


def test_no_pinned_key_refuses_everything(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make(allow=False)
    w.shell._verify = sh._verifier_for(lambda: SAT, lambda: None, lambda: WALL)
    w.shell.handle(allow_frame(w, True))
    assert not w.shell.allowed
    # Without its own key to name, nothing the server signs is taken either.
    w.shell._verify = sh._verifier_for(lambda: SAT, lambda: FP, lambda: WALL, lambda: None)
    w.shell.handle(allow_frame(w, True))
    assert not w.shell.allowed


def test_every_frame_the_device_sends_is_signed_in_one_rising_sequence(monkeypatch) -> None:
    """The satellite's frames: its own key, the satellite-to-server label
    (never the server's message), this connection's nonce, one sequence
    across every kind, restarted on a new connection."""
    w = opened(make(), monkeypatch)
    w.pty.output = [b"hello"]
    w.shell.step()
    w.pty.echo = False
    w.shell.step()
    w.shell.close("exit")
    frames = [f for f in w.sent if f["type"] != "support_allow"]
    assert [f["type"] for f in frames] == ["support_opened", "support_output", "support_echo",
                                           "support_closed"]
    docs = [f["signed"]["doc"] for f in frames]
    assert [d["seq"] for d in docs] == [1, 2, 3, 4]
    assert {d["nonce"] for d in docs} == {w.shell.nonce}
    assert [plain(f) for f in frames][1:] == [
        {"type": "support_output", "session_id": "s1", "data": base64.b64encode(b"hello").decode()},
        {"type": "support_echo", "session_id": "s1", "on": False},
        {"type": "support_closed", "session_id": "s1", "reason": "exit", "exit_code": 0}]
    pub = Ed25519PrivateKey.from_private_bytes(DEVICE_SEED).public_key()
    with pytest.raises(Exception):     # not the server's message: the label is in the bytes
        pub.verify(base64.b64decode(frames[0]["signed"]["signature"]), si.support_message(docs[0]))
    assert si.support_reply_message(docs[0]).startswith(si.SUPPORT_CONTEXT + b"\nsatellite-to-server\n")
    w.shell.new_connection()
    w.shell.handle(allow_frame(w, True))
    w.shell.handle(open_frame(w, sid="s9"))
    assert w.sent[-1]["signed"]["doc"]["seq"] == 1 and w.sent[-1]["signed"]["doc"]["nonce"] == w.shell.nonce


def test_input_must_be_signed_in_sequence_for_this_session(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.shell.handle({"type": "support_input", "session_id": "s1",
                    "data": base64.b64encode(b"id\r").decode()})          # unsigned
    typed(w, b"ls\r", 1)
    typed(w, b"again\r", 1)                                  # replayed number
    typed(w, b"x\r", 2, sid="other")                         # another session
    frame = input_frame(w, b"y\r", 3)
    frame["signed"]["doc"]["data"] = base64.b64encode(b"rm -rf ~\r").decode()
    w.shell.handle(frame)
    w.shell.pump_input("s1")
    assert w.pty.written == [b"ls\r"]
    typed(w, b"pwd\r", 7)
    assert w.pty.written == [b"ls\r", b"pwd\r"]


# ─── the session's own rules ─────────────────────────────────────────────


def test_refusals(monkeypatch) -> None:
    monkeypatch.setattr(sh.threading.Thread, "start", lambda self: None)
    w = make(declared=False)
    w.shell.handle(open_frame(w))
    assert w.sent[-1]["type"] == "support_refused" and w.pty.spawned is None
    w = make(busy=lambda: "the room is in a drop-in call")
    w.shell.handle(open_frame(w))
    assert plain(w.sent[-1]) == {"type": "support_refused", "session_id": "s1",
                                 "reason": "the room is in a drop-in call"}
    w = make()
    w.shell.handle(open_frame(w, sid="s0", max_sec=3601))   # over 60 minutes
    assert w.sent[-1]["type"] == "support_refused" and w.pty.spawned is None
    w.shell.handle(open_frame(w))
    assert w.sent[-1]["type"] == "support_opened" and w.opened == [True]
    w.shell.handle(open_frame(w, sid="s2"))                 # one at a time
    assert plain(w.sent[-1]) == {"type": "support_refused", "session_id": "s2",
                                 "reason": "a support session is already open"}


def test_input_and_resize(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    assert w.pty.spawned == (100, 30)
    typed(w, b"ls\r", 1)
    assert w.pty.written == [b"ls\r"]
    typed(w, b"x" * (sh.INPUT_FRAME_MAX + 1), 2)
    assert w.pty.written == [b"ls\r"]
    w.shell.handle({"type": "support_resize", "session_id": "s1", "cols": 9999, "rows": 1})
    assert w.pty.sizes == [(sh.COLS_RANGE[1], sh.ROWS_RANGE[0])]


def test_input_never_blocks_the_caller_and_a_stalled_shell_ends_it(monkeypatch) -> None:
    """M3: a shell that stops reading can't freeze the event loop. The
    caller only queues; a full queue ends the session."""
    w = opened(make(), monkeypatch)
    monkeypatch.undo()          # real threads from here (the writer never started)
    gate = threading.Event()
    w.pty.write = lambda data, still_open=lambda: True: gate.wait(5)   # a PTY that never drains
    started = time.monotonic()
    for i in range(sh.INPUT_QUEUE_MAX + 5):
        w.shell.handle(input_frame(w, b"y" * 4000, i + 1))
    assert time.monotonic() - started < 2.0
    deadline = time.monotonic() + 5
    while w.shell.active and time.monotonic() < deadline:
        time.sleep(0.01)
    gate.set()
    assert not w.shell.active
    assert w.ends == [("input_stalled", False)]
    assert plain(w.sent[-1])["reason"] == "input_stalled"


def test_only_key_presses_count_as_activity(monkeypatch) -> None:
    """L29: a terminal's automatic answer (active false) keeps nothing alive."""
    w = opened(make(), monkeypatch)
    w.clock.t += 500
    typed(w, b"\x1b[1;1R", 1, active=False)
    assert w.pty.written == [b"\x1b[1;1R"]
    w.clock.t += 101
    assert not w.shell.step()
    assert w.ends == [("idle", False)]


def test_output_frames_and_the_rate(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    monkeypatch.setattr(sh.time, "sleep", lambda s: None)
    w.pty.output = [b"z" * (200 * 1024)]
    for _ in range(20):
        w.shell.step()
    frames = [f for f in w.sent if f["type"] == "support_output"]
    sizes = [len(base64.b64decode(plain(f)["data"])) for f in frames]
    assert max(sizes) <= sh.OUTPUT_FRAME_MAX
    assert sum(sizes) <= sh.OUTPUT_RATE_MAX                  # no time passed: one second's worth
    seqs = [f["signed"]["doc"]["seq"] for f in frames]
    assert seqs == list(range(seqs[0], seqs[0] + len(frames)))
    w.clock.t += 1.0                                         # a second later, another
    for _ in range(20):
        w.shell.step()
    sizes = [len(base64.b64decode(plain(f)["data"])) for f in w.sent if f["type"] == "support_output"]
    assert sum(sizes) <= 2 * sh.OUTPUT_RATE_MAX and sum(sizes) > sh.OUTPUT_RATE_MAX


def test_echo_changes_are_reported(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.pty.echo = False
    w.shell.step()
    assert {"type": "support_echo", "session_id": "s1", "on": False} in [plain(f) for f in w.sent[1:]]
    w.pty.echo = True
    typed(w, b"\r", 1)
    assert plain(w.sent[-1]) == {"type": "support_echo", "session_id": "s1", "on": True}


def test_its_own_time_and_idle_limits(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.clock.t += 599
    assert w.shell.step()
    w.pty.output = [b"tail -f output"]
    w.shell.step()                                          # output is not input
    w.clock.t += 2
    assert not w.shell.step()
    assert plain(w.sent[-1]) == {"type": "support_closed", "session_id": "s1", "reason": "idle",
                                 "exit_code": 0}
    assert w.ends == [("idle", False)] and w.pty.ended

    w = opened(make(), monkeypatch)
    for i in range(4):
        w.clock.t += 500
        typed(w, b"\r", i + 1)
    assert not w.shell.step()
    assert w.ends == [("time_limit", False)]


def test_the_shell_exiting(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.pty.ended = True
    assert not w.shell.step()
    assert plain(w.sent[-1])["reason"] == "exit" and w.ends == [("exit", False)]


def test_the_server_ending_it_and_a_dropped_socket(monkeypatch) -> None:
    w = opened(make(), monkeypatch)
    w.shell.handle({"type": "support_close", "session_id": "s1", "reason": "admin_ended"})
    assert w.pty.ended and w.ends == [("admin_ended", True)]
    assert w.sent[-1]["type"] == "support_opened"            # no support_closed back
    assert not w.shell.active

    w = opened(make(), monkeypatch)
    w.shell.connection_lost()
    assert w.ends == [("connection_lost", True)] and w.pty.ended
    assert not w.shell.allowed


def _restart_harness(monkeypatch, restarted: bool):
    sat = object.__new__(client.Satellite)
    calls: list[str] = []
    sat._leds = SimpleNamespace(set_support=lambda on: calls.append(f"leds {on}"), state="idle")
    sat.playback_active = SimpleNamespace(is_set=lambda: False)
    sat._restart_service = lambda: calls.append("restart") or restarted
    monkeypatch.setattr(client.time, "sleep", lambda s: None)
    monkeypatch.setattr(client.os, "_exit", lambda code: calls.append(f"exit {code}"))

    class NowThread:
        def __init__(self, target, **kw):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(client.threading, "Thread", NowThread)
    sat._support_ended("idle", False)
    return calls


def test_the_client_restarts_its_service_when_a_session_ends(monkeypatch) -> None:
    assert _restart_harness(monkeypatch, True) == ["leds False", "restart"]


def test_a_failed_restart_exits_non_zero_so_systemd_clears_the_unit(monkeypatch) -> None:
    """M3: a restart that can't be asked for must not leave the shell's
    leftovers running."""
    assert _restart_harness(monkeypatch, False) == ["leds False", "restart", "exit 1"]


def test_opening_arms_the_watchdog_and_drops_dumpability(monkeypatch) -> None:
    sat = object.__new__(client.Satellite)
    calls: list[str] = []
    sat._leds = SimpleNamespace(set_support=lambda on: calls.append(f"leds {on}"))
    monkeypatch.setattr(sh, "make_undumpable", lambda: calls.append("undumpable") or True)
    monkeypatch.setattr(sh, "sd_notify", lambda msg, path=None: calls.append(msg) or True)
    sat.loop = None
    sat._support_opened()
    assert calls == ["leds True", "undumpable",
                     f"WATCHDOG_USEC={sh.WATCHDOG_SEC * 1_000_000}\nWATCHDOG=1"]
    src = inspect.getsource(client.Satellite._feed_support_watchdog)
    assert 'sd_notify("WATCHDOG=1")' in src and "while self._support.active" in src


def test_sd_notify_without_a_socket_is_a_quiet_no(monkeypatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sh.notify_socket() is None
    assert sh.sd_notify("WATCHDOG=1") is False


def test_the_client_refuses_while_busy() -> None:
    sat = object.__new__(client.Satellite)
    sat.dropin_active = SimpleNamespace(is_set=lambda: True)
    sat.wake_recording = SimpleNamespace(is_set=lambda: False)
    assert sat._support_busy() == "the room is in a drop-in call"
    sat.dropin_active = SimpleNamespace(is_set=lambda: False)
    sat.wake_recording = SimpleNamespace(is_set=lambda: True)
    assert sat._support_busy() == "the room is recording wake-word clips"


# ─── the real PTY (Linux; CI runs it) ─────────────────────────────────────


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a PTY needs Linux")
def test_a_real_pty_runs_a_shell_as_this_user() -> None:
    import os

    pty = sh.PtyBackend(sandboxed=False)
    pty.spawn(80, 24)
    try:
        out = b""
        deadline = time.monotonic() + 10

        def drain(want: bytes) -> bytes:
            nonlocal out
            while want not in out and time.monotonic() < deadline:
                chunk = pty.read(4096, 0.2)
                if chunk is None:
                    break
                out += chunk
            return out

        pty.write(b"echo uid=$(id -u) cols=$(tput cols 2>/dev/null || stty size)\n")
        assert f"uid={os.getuid()}".encode() in drain(b"uid=" + str(os.getuid()).encode())
        # The shell's own line editor is raw mode: not hidden input.
        assert pty.echo_on()
        # A hidden prompt (ECHO off, canonical, as getpass / sudo ask) is.
        pty.write(b"read -s -p pw: x\n")
        deadline2 = time.monotonic() + 5
        while pty.echo_on() and time.monotonic() < deadline2:
            pty.read(4096, 0.1)
        assert not pty.echo_on()
    finally:
        code = pty.terminate()
    assert code is not None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a PTY needs Linux")
def test_a_real_pty_write_gives_up_when_the_session_ends() -> None:
    """M3 on the real PTY: a program that never reads its input fills the
    PTY; the write waits with a timeout and stops once the session is over,
    instead of blocking forever. The program holds the terminal in raw mode,
    as a full-screen program does: a canonical-mode line discipline drops
    what doesn't fit in its line buffer instead of blocking the writer, so
    a plain "sleep" never fills the PTY."""
    pty = sh.PtyBackend(sandboxed=False)
    pty.spawn(80, 24)
    try:
        pty.write(b"stty raw -echo; sleep 30\n")
        time.sleep(0.3)
        over = threading.Event()
        threading.Timer(1.5, over.set).start()
        started = time.monotonic()
        with pytest.raises(OSError):
            pty.write(b"z" * (1024 * 1024), lambda: not over.is_set())
        assert time.monotonic() - started < 10
    finally:
        pty.terminate()
