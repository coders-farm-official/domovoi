"""Approval gates the microphone.

Found on hardware (F-V013, https://trello.com/c/lCvkRb9q) while setting up a
third satellite through the Wi-Fi portal: a device parked on the dashboard
waiting for someone to approve it heard its wake word, played the normal
greeting, opened the microphone and captured the utterance — into a socket
the core had already closed after answering ``awaiting_approval``. Nothing
reached the core. The device still recorded a room while nobody had approved
it, and still told the person standing in front of it that it was working.

The card records the symptom as the canned ``network_issues.mp3``. It was
not: that clip is gated on ``_network_degraded`` AND a live
``_ws_disconnected_since``, and a parked device with healthy Wi-Fi has
neither, so it took the ordinary branch and gave the ordinary greeting. A
false affordance, not a wrong error message.

What is pinned here:

* a device that has never had a session accepted opens no capture stream,
  starts no mic thread — and therefore never loads the wake model, which
  only ever loads inside that thread;
* it is not dark while it waits: every attempt still announces its approval
  code through the setup helper, and nothing on that path claims a network
  problem;
* the ``ready`` frame that accepts a session opens the microphone exactly
  once, and a reconnect — or a storm of them — neither closes it nor opens
  a second one;
* an already-approved device opens its microphone at boot even with no
  server to be seen, because that is exactly when the network-degraded
  canned clip is the only useful thing it can say;
* ``[mic] enabled = false`` — the video-kiosk build — is untouched by all
  of it.

The satellite here is assembled field by field rather than constructed
(`test_send_queue_lifecycle` does the same): `Satellite.__init__` builds an
LED controller and reads a config this test has no use for. `run()`,
`_start_voice_input`, `_note_session_accepted` and `_handle_text_frame` are
the REAL code — the fakes are the audio stack, the threads' bodies and the
server.
"""

from __future__ import annotations

import asyncio
import inspect
import queue
import threading
import time
import types

import pytest

from satellite.tests._client_import import import_client

client = import_client()


class Leds:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.started = False
        self.resyncs = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def resync(self) -> None:
        self.resyncs += 1

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_state_unless(self, unless: str, state: str) -> None:
        self.states.append(state)


class Sat:
    """What the fakes recorded, alongside the satellite they were hung on."""

    def __init__(self, satellite) -> None:
        self.s = satellite
        self.mic_opens = 0
        self.mic_stops = 0
        self.mic_thread_bodies = 0
        self.wake_models_loaded = 0
        self.setup_status: list[tuple[str, ...]] = []
        self.open_delay = 0.0
        self.open_raises: BaseException | None = None


def make_sat(monkeypatch, *, mic_enabled: bool = True) -> Sat:
    sat = object.__new__(client.Satellite)
    rec = Sat(sat)

    sat.cfg = types.SimpleNamespace(
        # Loopback only, per satellite/tests/conftest.py — though nothing
        # here opens a socket: the session is a fake.
        domovoi_url="ws://127.0.0.1:6370",
        room_id="kitchen",
        sat_type="voice",
        mic_enabled=mic_enabled,
        wifi_enabled=False,
        tts_playback_gain=1.0,
        output_device=None,
        music_alsa_device="",
        device=types.SimpleNamespace(
            name="xvf3800",
            description="XVF3800 USB array",
            playback_sample_rate=None,
            provisioned_music_alsa_device="",
            supports_full_duplex=True,
        ),
    )
    sat.shutdown_event = threading.Event()
    sat.loop = None
    sat._async_shutdown = None
    sat.fatal_error = None
    sat._input_stream = None
    sat._mic_thread = None
    sat._playback_thread = None
    sat._wifi_thread = None
    sat._kiosk_thread = None
    sat._voice_input_lock = threading.Lock()
    sat._voice_input_started = False
    sat._network_degraded = threading.Event()
    sat._ws_disconnected_since = None
    sat._upgrade_confirmed = False
    sat.raw_q = queue.Queue()
    sat._leds = Leds()
    # Everything `_on_session_ended` touches — a scripted attempt ends the
    # session the way a dropped socket does.
    sat.send_q = None
    sat.response_done = threading.Event()
    sat.playback_active = threading.Event()
    sat.stop_playback = threading.Event()
    sat.playback_q = queue.Queue()
    sat.wake_recording = threading.Event()
    sat._wake_rec_params = None
    sat.chat_active = threading.Event()
    sat.dropin_active = threading.Event()
    sat._post_playback_state = None

    def start_mic() -> None:
        if rec.open_delay:
            time.sleep(rec.open_delay)
        if rec.open_raises is not None:
            raise rec.open_raises
        rec.mic_opens += 1
        sat._input_stream = object()

    def stop_mic() -> None:
        rec.mic_stops += 1
        sat._input_stream = None

    def mic_thread_run() -> None:
        # Shaped like the real one, which loads the wake model as its very
        # first act and nowhere else — see
        # `test_the_wake_model_has_exactly_one_door_and_it_is_the_mic_thread`.
        rec.mic_thread_bodies += 1
        sat._load_wake_model()

    def load_wake_model():
        rec.wake_models_loaded += 1
        return object()

    sat._start_mic = start_mic
    sat._stop_mic = stop_mic
    sat._mic_thread_run = mic_thread_run
    sat._load_wake_model = load_wake_model
    sat._arm_upgrade_watchdog = lambda: None
    sat._stop_music = lambda: None
    sat._stop_greeting = lambda: None
    sat._sync_time_with_server = lambda: None
    sat._playback_thread_run = lambda: sat.shutdown_event.wait(5.0)

    monkeypatch.setattr(
        client, "_setup_status",
        lambda state, *args: rec.setup_status.append((state, *args)),
    )
    return rec


# ─── driving `run()` with a scripted server ───────────────────────────────


def parked(code: str = "428913"):
    """One connection attempt against a core that parks the device.

    Exactly what `domovoi/streaming.py` does: the `awaiting_approval` error
    frame, then the socket closes. `_run_session` returning normally is the
    truth of it — the core was reachable, it just refused."""
    def attempt(sat) -> None:
        sat._handle_text_frame({
            "type": "error",
            "reason": "awaiting_approval",
            "message": "waiting for approval on the dashboard",
            "code": code,
        })
        sat._on_session_ended()
    return attempt


def accepted():
    """One attempt the core accepts: `ready`, then the link drops."""
    def attempt(sat) -> None:
        sat._handle_text_frame({
            "type": "ready", "protocol_version": "1", "bot_name": "Domovoi",
        })
        sat._on_session_ended()
    return attempt


def unreachable():
    """One attempt that never reaches a server at all."""
    def attempt(sat) -> None:
        raise OSError("[Errno 111] Connection refused")
    return attempt


def run_sessions(rec: Sat, attempts: list, monkeypatch) -> None:
    """Run the real `run()` over a scripted sequence of connection attempts.

    The reconnect backoff is a real one-second sleep on the `_async_shutdown`
    event; a test paying that per attempt would be mostly sleep, so the wait
    is shortened rather than removed (removing it would spin the loop
    differently from production).
    """
    sat = rec.s
    real_wait_for = asyncio.wait_for

    async def brief_wait(awaitable, timeout=None):
        return await real_wait_for(awaitable, 0.001)

    monkeypatch.setattr(client.asyncio, "wait_for", brief_wait)

    left = list(attempts)

    async def fake_session() -> None:
        if not left:
            sat.shutdown_event.set()
            return
        left.pop(0)(sat)

    sat._run_session = fake_session
    asyncio.run(sat.run())


# ─── a device nobody has approved is deaf ─────────────────────────────────


def test_a_device_that_has_never_been_accepted_opens_no_microphone(monkeypatch):
    rec = make_sat(monkeypatch)

    run_sessions(rec, [parked(), parked(), parked()], monkeypatch)

    # The behaviour first, and deliberately before anything that names the
    # new code: against the unfixed client this line is the A/B, and it
    # should fail saying one microphone was opened where none belongs —
    # not saying a constant is missing.
    assert rec.mic_opens == 0, "no capture stream while it waits for approval"
    assert rec.mic_thread_bodies == 0, "no mic thread, so no wake word"
    assert rec.s._mic_thread is None
    assert rec.s._input_stream is None
    assert rec.s._voice_input_started is False
    assert not client.APPROVED_MARKER.exists(), "nothing to remember yet"


def test_it_never_loads_the_wake_model_while_it_waits(monkeypatch):
    """~10-15 s of ONNX on a Pi Zero 2 W, for a device that must not answer."""
    rec = make_sat(monkeypatch)
    run_sessions(rec, [parked(), parked()], monkeypatch)
    assert rec.wake_models_loaded == 0


def test_the_wake_model_has_exactly_one_door_and_it_is_the_mic_thread():
    """What makes the test above a statement about production and not about
    its own fake: gating the mic thread is only enough to keep the model
    unloaded while the thread is the model's only caller."""
    src = inspect.getsource(client)
    callers = [
        line.strip() for line in src.splitlines()
        if "_load_wake_model(" in line and "def _load_wake_model" not in line
    ]
    assert callers == ["oww = self._load_wake_model()"], callers
    assert "self._load_wake_model()" in inspect.getsource(
        client.Satellite._mic_thread_run
    )


def test_the_approval_code_is_still_announced_on_every_attempt(monkeypatch):
    """The gate must not make a waiting device silent as well as deaf: the
    spoken code is the only feedback channel it has left."""
    rec = make_sat(monkeypatch)
    run_sessions(rec, [parked("428913"), parked("428913")], monkeypatch)

    assert rec.setup_status == [
        ("awaiting-approval",), ("say-code", "428913"),
        ("awaiting-approval",), ("say-code", "428913"),
    ]


def test_nothing_on_the_waiting_path_claims_a_network_problem(monkeypatch):
    """The canned network clip is gated on `_network_degraded` plus a live
    disconnect timestamp, and the no-server ring colour on consecutive
    CONNECTION failures. A parked device reaches its core fine, so neither
    may arm — the device is waiting, not broken."""
    rec = make_sat(monkeypatch)
    run_sessions(rec, [parked(), parked(), parked(), parked()], monkeypatch)

    assert not rec.s._network_degraded.is_set()
    assert ("no-server",) not in rec.setup_status
    assert [s for s, *_ in rec.setup_status] == [
        "awaiting-approval", "say-code",
    ] * 4


# ─── approval opens it, once ───────────────────────────────────────────────


def test_an_accepted_session_opens_the_microphone_exactly_once(monkeypatch):
    rec = make_sat(monkeypatch)
    run_sessions(rec, [accepted()], monkeypatch)

    assert rec.mic_opens == 1
    assert rec.mic_thread_bodies == 1
    assert rec.wake_models_loaded == 1, "the wake word arms once it may answer"
    assert client.APPROVED_MARKER.is_file(), "remembered for the next boot"


def test_a_reconnect_neither_closes_the_microphone_nor_opens_a_second(monkeypatch):
    """`ready` arrives once per session, so this runs on every reconnect. An
    approved satellite that briefly loses its server keeps working exactly as
    it does today — including the genuine network-degraded canned clip."""
    rec = make_sat(monkeypatch)
    run_sessions(
        rec, [accepted(), unreachable(), accepted(), accepted()], monkeypatch,
    )

    assert rec.mic_opens == 1, "opened by the first accepted session and left open"
    assert rec.mic_thread_bodies == 1, "one mic thread for the process"
    assert rec.mic_stops == 1, "closed once, by the shutdown at the end"


def test_the_session_ending_does_not_re_arm_the_gate(monkeypatch):
    """A dropped WS unparks the mic thread; it must not un-approve the
    device. If `_on_session_ended` cleared the flag, the next `ready` would
    open a second stream on top of the live one."""
    rec = make_sat(monkeypatch)
    sat = rec.s

    assert sat._start_voice_input(fatal=False) is True
    sat._on_session_ended()
    assert sat._voice_input_started is True
    assert sat._input_stream is not None
    assert sat._start_voice_input(fatal=False) is False
    assert rec.mic_opens == 1


def test_a_reconnect_storm_opens_exactly_one_microphone(monkeypatch):
    """Twelve `ready` frames arriving while the first mic is still opening.
    The loop delivers them one at a time today; the lock is what makes that
    a property of the code rather than of the scheduler."""
    rec = make_sat(monkeypatch)
    rec.open_delay = 0.02
    sat = rec.s

    ready = threading.Barrier(12)
    started: list[bool] = []
    lock = threading.Lock()

    def accept() -> None:
        ready.wait()
        opened = sat._start_voice_input(fatal=False)
        with lock:
            started.append(opened)

    threads = [threading.Thread(target=accept) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10.0)

    assert started.count(True) == 1, "one caller opens it, eleven find it open"
    assert rec.mic_opens == 1
    assert rec.mic_thread_bodies == 1


# ─── across a reboot ──────────────────────────────────────────────────────


def test_an_approved_device_opens_its_microphone_at_boot(monkeypatch):
    """With no server in sight. This is the case the canned "I can't reach
    the server" clip exists for, and a gate that waited for a `ready` frame
    that cannot come would have taken it away."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("accepted\n", encoding="utf-8")

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1, "the wake word works during an outage"
    assert rec.mic_thread_bodies == 1
    # The outage still reaches the ring — that behaviour is not ours to change.
    assert ("no-server",) in rec.setup_status


def test_the_core_parking_a_device_again_forgets_the_approval(monkeypatch):
    """An admin who resets this room's pairing gets a device that is deaf
    again on its next boot. The mic already open in THIS process is left
    alone: the core has closed the socket, so nothing it hears goes
    anywhere, and tearing capture down under a running wake loop is a
    hazard of its own."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("accepted\n", encoding="utf-8")
    assert sat._start_voice_input(fatal=False) is True

    sat._handle_text_frame({
        "type": "error", "reason": "awaiting_approval", "code": "428913",
    })

    assert not client.APPROVED_MARKER.exists()
    assert rec.mic_stops == 0
    assert sat._input_stream is not None


def test_the_marker_survives_a_read_only_config_dir(monkeypatch):
    """Every other sidecar write on this device is best-effort, and this one
    costs the gate its memory across a reboot — not its microphone now."""
    rec = make_sat(monkeypatch)

    def refuse(*a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(client.Path, "write_text", refuse)
    run_sessions(rec, [accepted()], monkeypatch)

    assert rec.mic_opens == 1
    assert not client.APPROVED_MARKER.exists()


# ─── the mic-less build is untouched ──────────────────────────────────────


def test_a_mic_less_build_behaves_exactly_as_before(monkeypatch, caplog):
    """`[mic] enabled = false` is the video kiosk. It was never listening and
    still is not — same single log line, and not a word about approval."""
    rec = make_sat(monkeypatch, mic_enabled=False)

    with caplog.at_level("INFO", logger="satellite"):
        run_sessions(rec, [accepted(), accepted()], monkeypatch)

    assert rec.mic_opens == 0
    assert rec.mic_thread_bodies == 0
    assert rec.wake_models_loaded == 0
    assert rec.s._mic_thread is None
    said = [r.getMessage() for r in caplog.records]
    assert any("voice input disabled ([mic] enabled=false)" in m for m in said)
    assert not any("held shut until this satellite is approved" in m for m in said)


def test_a_mic_less_build_ignores_an_accepted_session(monkeypatch):
    """Belt and braces: `ready` is the gate's opener, and it must not become
    a way around `mic_enabled`."""
    rec = make_sat(monkeypatch, mic_enabled=False)
    rec.s._start_voice_input(fatal=False)
    rec.s._note_session_accepted()
    assert rec.mic_opens == 0
    assert rec.s._voice_input_started is False


# ─── a microphone that will not open ──────────────────────────────────────


def test_a_dead_microphone_at_boot_still_raises(monkeypatch):
    """Today's behaviour, kept: the process dies and systemd restarts it."""
    rec = make_sat(monkeypatch)
    rec.open_raises = RuntimeError("Error querying device -1")
    with pytest.raises(RuntimeError):
        rec.s._start_voice_input(fatal=True)
    assert rec.s._voice_input_started is False


def test_a_dead_microphone_after_approval_exits_non_zero(monkeypatch):
    """The same outcome by the other door. On the `ready` frame we are inside
    the receiver task, where a raise is swallowed and the client would
    reconnect forever with no microphone; `fatal_error` plus the shutdown
    event is how `main()` already turns a startup failure into a restart."""
    rec = make_sat(monkeypatch)
    rec.open_raises = RuntimeError("Error querying device -1")

    rec.s._note_session_accepted()

    assert rec.s.fatal_error is not None
    assert rec.s.shutdown_event.is_set()
    assert rec.s._voice_input_started is False, "so a later attempt may retry"
    assert rec.mic_thread_bodies == 0
