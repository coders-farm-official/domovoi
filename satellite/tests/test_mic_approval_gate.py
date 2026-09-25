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

And — the second half of this file — what the hardening pass added, because
the worst thing a gate like this can do is not "a new satellite is deaf" but
"a WORKING satellite is deaf":

* nothing in the ``ready`` branch can cost the microphone by raising above
  the call that opens it, and one indigestible frame cannot kill the
  receiver task;
* a mic thread that will not start rolls the whole thing back instead of
  latching the gate shut over an open capture stream;
* a satellite already in service, whose only route into this code is a
  self-upgrade its core commanded, proves itself from the receipts that
  upgrade left behind — with no core in sight — while nothing a PARKED
  device also has counts as proof;
* a core saying ``awaiting_approval`` outlives those receipts, so an
  admin's "reset pairing" reaches the microphone and stays reached, and
  re-provisioning onto a different core does the same;
* the LED ring does not go idle on the ``ready`` frame that opens the mic:
  the wake model is ~10-15 s of ONNX, and the mic thread paints the ring
  when the wake word is genuinely armed.

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
import json
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


def make_sat(
    monkeypatch, *, mic_enabled: bool = True, arms_wake_word: bool = False,
) -> Sat:
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
    sat._voice_gate_closed = False
    sat._ring_handoff_pending = False
    sat._wake_armed = threading.Event()
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
        if not arms_wake_word:
            # The default stops HERE, mid-load, which is the state most of
            # this file is about: the ~10-15 s of ONNX during which the
            # device cannot answer and the ring must not say it can.
            return
        # The rest of `_mic_thread_run`'s arming sequence, copied because a
        # reconnect behaves differently before and after it and a test that
        # cannot say which side it is on cannot pin either.
        sat._wake_armed.set()
        if sat._ring_handoff_pending:
            sat._ring_handoff_pending = False
            sat._leds.resync()

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


class Socket:
    """A WebSocket that yields a scripted list of text frames and closes."""

    def __init__(self, payloads) -> None:
        self._payloads = list(payloads)

    def __aiter__(self):
        async def gen():
            for payload in self._payloads:
                yield json.dumps(payload)
        return gen()


async def _deliver(sat, payloads) -> None:
    """Hand the frames to the REAL `_receiver_loop`.

    Not to `_handle_text_frame` directly: the per-frame guard that keeps one
    indigestible frame from killing the whole receiver task lives in the
    loop, and a harness that skipped it would be testing a session shape
    production does not have."""
    sat.ws = Socket(payloads)
    try:
        await sat._receiver_loop()
    finally:
        sat.ws = None


def parked(code: str = "428913"):
    """One connection attempt against a core that parks the device.

    Exactly what `domovoi/streaming.py` does: the `awaiting_approval` error
    frame, then the socket closes. `_run_session` returning normally is the
    truth of it — the core was reachable, it just refused."""
    async def attempt(sat) -> None:
        await _deliver(sat, [{
            "type": "error",
            "reason": "awaiting_approval",
            "message": "waiting for approval on the dashboard",
            "code": code,
        }])
        sat._on_session_ended()
    return attempt


def accepted():
    """One attempt the core accepts: `ready`, then the link drops."""
    async def attempt(sat) -> None:
        await _deliver(sat, [{
            "type": "ready", "protocol_version": "1", "bot_name": "Domovoi",
        }])
        sat._on_session_ended()
    return attempt


def unreachable():
    """One attempt that never reaches a server at all."""
    def attempt(sat) -> None:
        raise OSError("[Errno 111] Connection refused")
    return attempt


def armed(attempt):
    """`attempt`, but not until the mic thread has finished arming.

    The wake model load is a real thread here as it is on the Pi, so
    "reconnects after the ring handoff" and "reconnects during it" are the
    same scripted list unless a test says which it means. Only useful with
    `make_sat(..., arms_wake_word=True)`; without it the wait would never
    return, so it fails loudly instead of hanging the suite."""
    async def waited(sat):
        assert sat._wake_armed.wait(5.0), (
            "the mic thread never armed — arms_wake_word=True?"
        )
        outcome = attempt(sat)
        if inspect.isawaitable(outcome):
            await outcome
    return waited


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
        outcome = left.pop(0)(sat)
        if inspect.isawaitable(outcome):
            await outcome

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
    # And the core's verdict is written DOWN as a no, not left as an absent
    # file. Absent means "never decided", which is what an upgrade looks
    # like — see the bridge tests further down.
    assert client._read_approval_record() == client._APPROVAL_NO
    assert client._was_ever_approved() is False


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
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")

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
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    assert sat._start_voice_input(fatal=False) is True

    sat._handle_text_frame({
        "type": "error", "reason": "awaiting_approval", "code": "428913",
    })

    assert client._read_approval_record() == client._APPROVAL_NO
    assert client._was_ever_approved() is False, "deaf on the next boot"
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


# ─── the mic opener cannot be skipped by an unrelated failure ─────────────
#
# The worst thing this feature can do is not "a new satellite is deaf" — it
# is "a WORKING satellite is deaf". Four statements used to run ahead of the
# one call that opens the microphone, inside a `ready` branch with no guard
# and a receiver loop with no per-frame guard. A raise in any of them left
# the device reconnecting about once a second forever, approved and deaf,
# with `fatal_error` unset so systemd never restarted it either.


def _ready_branch_raisers():
    """Raises the housekeeping ahead of the mic can really produce.

    `_promote_pending_server` catches OSError and nothing else, so a
    config.toml that is not valid UTF-8 comes straight out of its
    `read_text(encoding="utf-8")`; its function-local `from satellite import
    config_writer, server_identity` raises ImportError against the
    half-mirrored tree a self-upgrade briefly leaves behind — the hazard
    this file already defends against elsewhere.
    """
    return [
        pytest.param(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            id="config.toml is not utf-8",
        ),
        pytest.param(
            ImportError("cannot import name 'config_writer'"),
            id="half-mirrored tree mid-upgrade",
        ),
    ]


@pytest.mark.parametrize("boom", _ready_branch_raisers())
def test_a_raise_ahead_of_the_mic_no_longer_costs_the_microphone(
    monkeypatch, boom,
):
    """The acceptance bar for this whole pass. Five accepted sessions, each
    of which blows up in the housekeeping above the mic — and the microphone
    is open after the first one."""
    rec = make_sat(monkeypatch)

    def explode(*a, **kw):
        raise boom

    monkeypatch.setattr(client, "_promote_pending_server", explode)

    run_sessions(rec, [accepted()] * 5, monkeypatch)

    assert rec.mic_opens == 1, "the mic opens even though the branch raised"
    assert rec.wake_models_loaded == 1
    assert rec.s._voice_input_started is True
    assert client._read_approval_record() == client._APPROVAL_YES


@pytest.mark.parametrize("boom", _ready_branch_raisers())
def test_the_ready_branch_opens_the_mic_even_as_it_unwinds(monkeypatch, boom):
    """Two independent belts, and this is the inner one. The receiver loop
    now guards each frame, so the test above would pass even if the mic call
    were still the last statement of the branch. This one calls
    `_handle_text_frame` directly and lets the exception out: the `finally`
    has to have opened the microphone on the way past."""
    rec = make_sat(monkeypatch)

    def explode(*a, **kw):
        raise boom

    monkeypatch.setattr(client, "_promote_pending_server", explode)

    with pytest.raises(type(boom)):
        rec.s._handle_text_frame({"type": "ready", "bot_name": "Domovoi"})

    assert rec.mic_opens == 1
    assert rec.mic_thread_bodies == 1


def test_the_time_sync_failing_does_not_cost_the_microphone(monkeypatch):
    """`_sync_time_with_server` starts a thread, and `Thread.start` raises
    `RuntimeError: can't start new thread` under thread pressure — an
    ordinary Pi Zero 2 W failure, not a hypothetical."""
    rec = make_sat(monkeypatch)

    def explode() -> None:
        raise RuntimeError("can't start new thread")

    rec.s._sync_time_with_server = explode

    run_sessions(rec, [accepted(), accepted()], monkeypatch)

    assert rec.mic_opens == 1


def test_the_receiver_loop_survives_a_frame_it_cannot_handle(monkeypatch):
    """The other half of the same defect: `_handle_text_frame` was called
    with no guard, so one frame it choked on killed the receiver task,
    `_run_session` returned NORMALLY (backoff reset, `fatal_error` unset)
    and the client reconnected forever doing less each time. The real
    `_receiver_loop` here, against a fake socket."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    handled: list[object] = []

    def handle(payload) -> None:
        handled.append(payload)
        if isinstance(payload, dict) and payload.get("type") == "poison":
            raise ValueError("no idea what this is")

    sat._handle_text_frame = handle
    sat.ws = Socket([
        {"type": "poison"},
        [],                                     # not even a dict
        {"type": "ready"},
    ])
    asyncio.run(sat._receiver_loop())

    assert [p.get("type") for p in handled if isinstance(p, dict)] == [
        "poison", "ready",
    ], "the loop kept reading after the frame it choked on"


# ─── a half-started microphone rolls back instead of wedging ──────────────


class _MicThreadRefuses(threading.Thread):
    def start(self):
        if self.name == "mic":
            raise RuntimeError("can't start new thread")
        return super().start()


def test_a_thread_start_failure_rolls_back_instead_of_wedging(monkeypatch):
    """`Thread.start()` raising left the flag latched True and the capture
    stream open: PortAudio filling `raw_q` with no consumer, no wake word,
    no `fatal_error` for systemd, and every later `ready` told the
    microphone was already up. It must roll all the way back."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    real_thread = threading.Thread

    monkeypatch.setattr(client.threading, "Thread", _MicThreadRefuses)

    assert sat._start_voice_input(fatal=False) is False
    assert sat._voice_input_started is False, "retryable, not wedged"
    assert sat._input_stream is None, "no stream left filling raw_q"
    assert sat._mic_thread is None
    assert rec.mic_stops == 1, "the stream that did open was closed again"
    assert rec.mic_thread_bodies == 0
    # Same outcome as a dead microphone by the other door: exit non-zero so
    # `Restart=on-failure` tries again.
    assert sat.fatal_error is not None
    assert sat.shutdown_event.is_set()

    # And a later attempt really can retry.
    monkeypatch.setattr(client.threading, "Thread", real_thread)
    assert sat._start_voice_input(fatal=False) is True
    assert rec.mic_opens == 2
    assert rec.mic_thread_bodies == 1


def test_a_thread_start_failure_at_boot_still_raises(monkeypatch):
    """Boot keeps today's behaviour — the process dies — but not with an
    orphaned capture stream behind it."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    monkeypatch.setattr(client.threading, "Thread", _MicThreadRefuses)

    with pytest.raises(RuntimeError):
        sat._start_voice_input(fatal=True)
    assert sat._voice_input_started is False
    assert sat._input_stream is None
    assert rec.mic_stops == 1


# ─── the first boot after the upgrade ────────────────────────────────────
#
# Kamron has three satellites in his house. Each one restarts into this code
# with NO approval record, because only this code writes one. If "no record"
# meant "not approved", all three would be deaf until their core answered —
# and if the core is down at that moment (the power cut where the Pis boot
# faster than the Beelink) deaf for the whole outage, with no wake word and
# therefore no way even to play the canned "I can't reach the server" clip.
# So an absent record means UNDECIDED, and the client looks for receipts the
# OLD code left behind.


def _receipt(path, body: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.mark.parametrize("sidecar", [
    "SYNCED_SHA_SIDECAR",
    "SYNCED_MANIFEST_SIDECAR",
    "UPGRADE_PENDING_MARKER",
    "WAKE_SIDECAR",
    "WAKE_THRESHOLD_SIDECAR",
    "VOICE_SIDECAR",
])
def test_an_already_paired_device_proves_itself_with_no_core_in_sight(
    monkeypatch, sidecar,
):
    """Each of these files is a receipt: only code reacting to a frame the
    core sends on an ACCEPTED session ever writes one. A parked device is
    refused on its `hello`, before the socket carries anything it could act
    on, so it can never have any of them."""
    rec = make_sat(monkeypatch)
    _receipt(getattr(client, sidecar))
    assert not client.APPROVED_MARKER.exists(), "the upgrade's starting state"

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1, "the wake word works through the whole outage"
    assert rec.mic_thread_bodies == 1
    assert ("no-server",) in rec.setup_status, "and it knows it is offline"
    # Decided once and written down, so the next boot does not re-derive it.
    assert client._read_approval_record() == client._APPROVAL_YES


def test_nothing_a_parked_device_also_has_counts_as_a_receipt(monkeypatch):
    """The bridge must not hand the microphone back to the device F-V013 is
    about. A parked satellite gets a config.toml, a pairing token, an
    approval code and a full sounds cache — `_sync_sounds` even runs before
    `ready` is handled — so none of them is evidence."""
    rec = make_sat(monkeypatch)
    _receipt(client.CONFIG_PATH, "[satellite]\nroom_id = 'kitchen'\n")
    _receipt(client.PAIRING_TOKEN_SIDECAR, "deadbeef\n")
    _receipt(client.APPROVAL_CODE_SIDECAR, "428913\n")
    _receipt(client.SOUNDS_CACHE_DIR / "greeting.mp3", "not really audio\n")

    assert client._prior_acceptance_evidence() is None
    assert client._was_ever_approved() is False

    run_sessions(rec, [parked(), parked()], monkeypatch)
    assert rec.mic_opens == 0


def test_a_core_saying_no_outlives_the_receipts(monkeypatch):
    """Why the negative verdict is WRITTEN rather than the file deleted. A
    device the core has parked still carries the synced-sha sidecar from the
    core that approved it months ago — if `awaiting_approval` merely deleted
    the record, the bridge above would re-approve it on the very next boot
    and an admin's "reset pairing" would never reach the microphone."""
    rec = make_sat(monkeypatch)
    _receipt(client.SYNCED_SHA_SIDECAR)
    client._write_approval_record(client._APPROVAL_YES)

    # This boot: approved from the record, mic opens, then the core parks it.
    run_sessions(rec, [parked(), parked()], monkeypatch)
    assert rec.mic_opens == 1, "it was approved when it booted"
    assert client._read_approval_record() == client._APPROVAL_NO

    # Next boot, same disk, same receipts.
    again = make_sat(monkeypatch)
    run_sessions(again, [parked()], monkeypatch)
    assert again.mic_opens == 0, "the core's no is the last word"


#: Every shape a record can take that is neither verdict. The first three
#: are the ones the old fail-OPEN rule read as an ACCEPTANCE — they are the
#: A/B for this finding. The last three were already read as undecided, so
#: they pass both ways and are here to pin the whole class, not to bite.
_NOT_A_VERDICT = [
    (b"not-approv", "a `not-approved` torn in half by a power cut mid-write"),
    (b"approve", "an `approved` torn in half"),
    (b"the core accepted a session from this device\n",
     "the sentence 995c3cf wrote, from a build that never reached main"),
    (b"", "a zero-length file, the ext4 shape of an interrupted write"),
    (b"\n\n", "whitespace, with no verdict anywhere in it"),
    (b"\xff\xfe\x00", "not UTF-8 at all"),
]


@pytest.mark.parametrize("body, why", _NOT_A_VERDICT)
def test_a_record_that_is_not_a_verdict_keeps_the_gate_shut(
    monkeypatch, body, why,
):
    """The mic must NOT open. This is the parse failing closed, and it is a
    deliberate reversal: the record used to read "anything that is not
    `not-approved` is approved", so a torn write — `write_text` truncates
    before it writes, and a Pi loses power mid-write far more often than a
    server does — put the device back in the F-V013 state through the
    gate's own memory. The behavioural assertion comes FIRST, so a source
    that lacks the fix fails on the microphone rather than on a name."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_bytes(body)

    run_sessions(rec, [parked(), parked()], monkeypatch)

    assert rec.mic_opens == 0, f"a record that is {why} is not an acceptance"
    assert rec.wake_models_loaded == 0
    # After the run the core's own `awaiting_approval` has replaced the
    # damaged record with a real verdict, which is the point of it — so the
    # parse itself is checked on a fresh copy of the same bytes.
    client.APPROVED_MARKER.write_bytes(body)
    assert client._was_ever_approved() is False


@pytest.mark.parametrize("body, why", _NOT_A_VERDICT)
def test_a_record_that_is_not_a_verdict_parses_as_damaged(body, why):
    """The state named rather than inferred. `damaged` is distinct from
    absent so the log can say which, and so the record gets REPAIRED by the
    receipts bridge rather than merely obeyed."""
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_bytes(body)
    assert client._read_approval_record() == client._APPROVAL_DAMAGED, why


def test_a_damaged_record_still_lets_an_in_service_device_hear(monkeypatch):
    """The other half of the same rule. Failing closed on a torn record
    would make a working room deaf for an outage it cannot even announce,
    so the fall-through is to the RECEIPTS — files only a core-accepted
    session can have produced. Damaged plus a receipt opens; damaged alone
    (above) does not."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_bytes(b"not-approv")
    _receipt(client.SYNCED_SHA_SIDECAR)

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1, "the wake word works through the whole outage"
    assert ("no-server",) in rec.setup_status
    # And the damaged record is REPAIRED, so the next boot reads a verdict.
    assert client._read_approval_record() == client._APPROVAL_YES


# ─── re-provisioning takes the verdict away ──────────────────────────────


def test_re_provisioning_leaves_the_device_unapproved(monkeypatch):
    """A fresh pairing token is a fresh identity: the previous core's verdict
    must not travel with it. Without this, a device re-homed onto a different
    core opens its microphone at boot while that core is parking it — F-V013
    exactly, on the one path the gate did not cover."""
    from satellite import provisioning_mode

    # As it was before: approved by the old core, with a receipt to match.
    client._write_approval_record(client._APPROVAL_YES)
    _receipt(client.SYNCED_SHA_SIDECAR)
    assert client._was_ever_approved() is True

    assert provisioning_mode.APPROVED_MARKER_PATH == client.APPROVED_MARKER
    provisioning_mode._write_not_approved()

    assert client._read_approval_record() == client._APPROVAL_NO
    assert client._was_ever_approved() is False, "the receipt must not revive it"

    rec = make_sat(monkeypatch)
    run_sessions(rec, [parked()], monkeypatch)
    assert rec.mic_opens == 0


def test_apply_provision_resets_the_approval_record():
    """Pinned at the call site, not just on the helper: the reset has to
    happen where the pairing token is rewritten, so every re-provision goes
    through it."""
    from satellite import provisioning_mode

    body = inspect.getsource(provisioning_mode.apply_provision)
    assert "_write_not_approved()" in body
    assert body.index("PAIRING_TOKEN_PATH.write_text") < body.index(
        "_write_not_approved()"
    ), "beside the new token, just after it is written"


# ─── the ring does not promise a wake word that is not armed yet ──────────


def test_the_ring_waits_for_the_wake_word_on_the_first_approval(monkeypatch):
    """The wake model is ~10-15 s of ONNX on a Pi Zero 2 W, and since this
    feature it loads AFTER approval instead of at boot. Resyncing the ring on
    the `ready` frame would take it off the awaiting-approval violet and tell
    the person who just clicked Approve to try the wake word a quarter of a
    minute early — the F-V013 false affordance, pointing the other way. The
    mic thread owns that transition now."""
    rec = make_sat(monkeypatch)

    run_sessions(rec, [accepted()], monkeypatch)

    assert rec.mic_opens == 1
    assert rec.s._leds.resyncs == 0, (
        "the ring stays where it was until the wake word is armed"
    )


def test_a_reconnect_still_resyncs_the_ring_immediately(monkeypatch):
    """Which is what the resync was added for: a satellite that was parked,
    then approved, then reconnects must not go on advertising that it is
    waiting for approval. Once the wake word is ARMED — the mic thread has
    taken the handoff and painted the ring — every later `ready` resyncs on
    the spot, exactly as it did before the gate existed."""
    rec = make_sat(monkeypatch, arms_wake_word=True)
    run_sessions(
        rec, [accepted(), armed(accepted()), armed(accepted())], monkeypatch,
    )
    assert rec.mic_opens == 1
    # 1 from the mic thread's own handoff, then one per later `ready`.
    assert rec.s._leds.resyncs == 3, "every ready but the one that opened it"
    assert rec.s._ring_handoff_pending is False


def test_a_reconnect_inside_the_onnx_load_leaves_the_ring_to_the_mic_thread(
    monkeypatch,
):
    """The same reconnect, a few seconds earlier — and it must do nothing.

    The first `ready` after approval hands the ring to the mic thread and
    deliberately leaves the awaiting-approval violet showing until the wake
    model is loaded. A second `ready` inside that ~10-15 s window (a Wi-Fi
    hiccup, the core restarting, any reconnect at all) used to clear the
    handoff flag and resync on the spot: the ring went to the controller's
    cached `idle` while the device still could not hear, AND the thread's
    own repaint was cancelled because the flag it was handed had been taken
    away, so nothing ever corrected it. That is round 1's finding 4 — click
    Approve, ring goes idle, say the wake word, nothing happens — coming
    back through the fix for it."""
    rec = make_sat(monkeypatch)      # the thread stops mid-load and stays there

    run_sessions(rec, [accepted(), accepted(), accepted()], monkeypatch)

    assert rec.mic_opens == 1
    assert rec.s._leds.resyncs == 0, (
        "the ring belongs to the mic thread until the wake word is armed"
    )
    assert rec.s._ring_handoff_pending is True, "and the handoff is still on"


def test_a_mic_less_build_resyncs_the_ring_exactly_as_before(monkeypatch):
    """The video kiosk never opens a microphone, so nothing would ever
    resync the ring for it later."""
    rec = make_sat(monkeypatch, mic_enabled=False)
    run_sessions(rec, [accepted(), accepted()], monkeypatch)
    assert rec.s._leds.resyncs == 2


def test_an_already_listening_device_resyncs_on_its_first_ready(monkeypatch):
    """A device approved before this boot opens its mic in `run()`, so the
    mic thread paints the ring; the first `ready` is then an ordinary one and
    resyncs immediately, as it always did."""
    rec = make_sat(monkeypatch)
    client._write_approval_record(client._APPROVAL_YES)
    run_sessions(rec, [accepted()], monkeypatch)
    assert rec.mic_opens == 1
    assert rec.s._leds.resyncs == 1


def test_the_mic_thread_arms_the_wake_word_and_then_paints_the_ring():
    """The real `_mic_thread_run`, which is where the transition now lives.
    Only the audio stack and the calibrations are faked; `shutdown_event` is
    pre-set so the wake loop exits at once."""
    sat = object.__new__(client.Satellite)
    sat.shutdown_event = threading.Event()
    sat.shutdown_event.set()
    sat._wake_armed = threading.Event()
    sat._wake_word = "hey_domovoi"
    sat._leds = Leds()
    # The handoff `_note_session_accepted` arms when its own call opened the
    # stack. This test is that path.
    sat._ring_handoff_pending = True
    sat.wake_recording = threading.Event()
    sat.dropin_active = threading.Event()
    sat.chat_active = threading.Event()
    order: list[str] = []

    def load():
        order.append("model")
        return object()

    sat._load_wake_model = load
    sat._calibrate_mic_gain_initial = lambda: order.append("gain")
    sat._calibrate_noise_gate_initial = lambda: order.append("gate")

    client.Satellite._mic_thread_run(sat)

    assert order == ["model", "gain", "gate"]
    assert sat._wake_armed.is_set()
    assert sat._leds.resyncs == 1, "painted only once it can actually answer"


def test_a_wake_model_that_will_not_load_leaves_the_ring_alone(monkeypatch):
    """It sets `fatal_error` and exits for systemd instead, and
    `_setup_status("startup-failed")` writes the ring directly. Repainting
    from the controller's cached state would paper over that."""
    said: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        client, "_setup_status", lambda state, *a: said.append((state, *a)),
    )
    sat = object.__new__(client.Satellite)
    sat.shutdown_event = threading.Event()
    sat._wake_armed = threading.Event()
    sat._wake_word = "hey_domovoi"
    sat._leds = Leds()
    sat._ring_handoff_pending = True
    sat.fatal_error = None
    sat.loop = None
    sat._async_shutdown = None

    def boom():
        raise RuntimeError("no such model")

    sat._load_wake_model = boom
    client.Satellite._mic_thread_run(sat)

    assert sat.fatal_error is not None
    assert sat.shutdown_event.is_set()
    assert said == [("startup-failed",)]
    assert sat._wake_armed.is_set() is False
    assert sat._leds.resyncs == 0


def test_a_re_parked_device_says_out_loud_that_it_is_still_listening(
    monkeypatch, caplog,
):
    """The gate is a BOOT-time gate, and an admin who presses "Reset
    pairing" on a live room gets a device that goes on answering its wake
    word until it restarts. Nothing it hears can leave (the core closed the
    socket) and tearing capture down under a running wake loop is a hazard
    of its own — so the behaviour stands and the log says so, rather than
    leaving whoever debugs it next to infer it from the source."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    client._write_approval_record(client._APPROVAL_YES)
    assert sat._start_voice_input(fatal=False) is True

    with caplog.at_level("WARNING", logger="satellite"):
        sat._handle_text_frame({
            "type": "error", "reason": "awaiting_approval", "code": "428913",
        })

    said = " ".join(r.getMessage() for r in caplog.records)
    assert "microphone is already open" in said
    assert "deaf from the next restart" in said
    assert rec.mic_stops == 0, "still not torn down mid-boot"


def test_a_device_that_was_never_listening_says_nothing_of_the_kind(
    monkeypatch, caplog,
):
    """The ordinary parked satellite — the F-V013 case — has no open mic to
    warn about, and a warning there would be noise on every retry."""
    rec = make_sat(monkeypatch)
    with caplog.at_level("WARNING", logger="satellite"):
        run_sessions(rec, [parked(), parked()], monkeypatch)
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "microphone is already open" not in said


# ─── round 3: the record is not more reliable than an SD card ─────────────
#
# Three of the findings against the hardening pass were the same mistake
# seen from different sides: the approval record was treated as more
# reliable than a file on a Pi's SD card actually is. A torn write read as
# a YES; a write that could not land silently decided the device's fate for
# good; and one file was the only truth in either direction. What follows
# pins the replacement: fail CLOSED on a record that is damaged, absent or
# uncorrectable, fail OPEN only where a RECEIPT agrees, and make a write
# that cannot land say so.


def _refuse_writes(monkeypatch, *, only_the_marker: bool, exc: OSError):
    """Make writes to the approval record fail, and nothing else.

    `only_the_marker=True` models the file the client can READ and not
    WRITE: provisioning creates it as root (`User=root` in
    domovoi-provisioning.service) and hands it over with a best-effort
    chown, so a failed chown leaves a root-owned 0644 file in a directory
    the satellite user owns. Its `.tmp` sibling is still writable, which is
    exactly why the record is replaced by rename.

    `only_the_marker=False` models the whole config dir refusing — a
    read-only remount after a card error, or a full SD card.
    """
    real = client.Path.write_text
    marker = client.APPROVED_MARKER

    def guarded(self, *a, **kw):
        if self == marker or (
            not only_the_marker
            and self.parent == marker.parent
            and self.name.startswith(marker.name)
        ):
            raise exc
        return real(self, *a, **kw)

    monkeypatch.setattr(client.Path, "write_text", guarded)


def _use_the_real_mic_thread(rec, *, model_raises=None) -> None:
    """Swap the harness's stand-in mic thread for `Satellite._mic_thread_run`
    itself. Only the model load and the two calibrations stay faked."""
    sat = rec.s
    sat._wake_word = "hey_domovoi"
    sat._calibrate_mic_gain_initial = lambda: None
    sat._calibrate_noise_gate_initial = lambda: None

    def load():
        rec.wake_models_loaded += 1
        if model_raises is not None:
            raise model_raises
        return object()

    sat._load_wake_model = load
    sat._mic_thread_run = types.MethodType(client.Satellite._mic_thread_run, sat)


# ─── a wake model that will not load must EXIT, not hang ──────────────────


def test_a_wake_model_that_will_not_load_after_approval_still_exits(monkeypatch):
    """The gate moved the model load from boot into a LIVE session, and the
    failure path only woke the threading side.

    `shutdown_event` is a `threading.Event`; `run()` is parked on the
    asyncio one inside `asyncio.wait({sender, receiver, shutdown-watch})`.
    While the mic thread started at boot that cost nothing — the failure
    happened before `run()` ever entered its session loop, `main()` returned
    non-zero and `Restart=on-failure` brought the satellite back. Now the
    thread can only start from the `ready` frame, so the failure lands
    inside a healthy session that nothing ends: `run()` never returns,
    systemd never restarts, and the room sits ONLINE on the dashboard
    holding the startup-failed colour and never hearing another word. A
    satellite that exits gets restarted; one that hangs needs a power cycle,
    and tomorrow's flash is the first approval of a brand-new unit.

    The behavioural assertion is `run()` RETURNING — against a source
    without the fix this test times out rather than naming a symbol."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    _use_the_real_mic_thread(rec, model_raises=FileNotFoundError("hey_domovoi.onnx"))

    async def live_session() -> None:
        # Accepted, and then STAYS UP — the shape `_run_session` has once a
        # core has said `ready`. Only a shutdown request ends it.
        await _deliver(sat, [{
            "type": "ready", "protocol_version": "1", "bot_name": "Domovoi",
        }])
        await sat._async_shutdown.wait()
        sat._on_session_ended()

    sat._run_session = live_session

    async def go() -> None:
        await asyncio.wait_for(sat.run(), timeout=10.0)

    asyncio.run(go())

    assert rec.mic_opens == 1, "the session was accepted and the mic opened"
    assert rec.wake_models_loaded == 1
    assert sat.fatal_error is not None, "systemd needs a non-zero exit"
    assert ("startup-failed",) in rec.setup_status


def test_the_mic_thread_wakes_the_asyncio_side_as_well_as_the_threading_one(
    monkeypatch,
):
    """The same thing at the source, so the reason is pinned and not just
    the symptom. `_start_voice_input`'s own failure path has called
    `_request_async_shutdown()` since the hardening pass; the mic thread's
    did not, and it is the one that can only fire mid-session."""
    said: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        client, "_setup_status", lambda state, *a: said.append((state, *a)),
    )

    async def go() -> None:
        sat = object.__new__(client.Satellite)
        sat.shutdown_event = threading.Event()
        sat._wake_armed = threading.Event()
        sat._wake_word = "hey_domovoi"
        sat._leds = Leds()
        sat._ring_handoff_pending = True
        sat.fatal_error = None
        sat.loop = asyncio.get_running_loop()
        sat._async_shutdown = asyncio.Event()

        def boom():
            raise RuntimeError("no such model")

        sat._load_wake_model = boom
        t = threading.Thread(target=client.Satellite._mic_thread_run, args=(sat,))
        t.start()
        t.join(5.0)
        assert not t.is_alive()
        # `call_soon_threadsafe` lands on the next turn of the loop.
        await asyncio.wait_for(sat._async_shutdown.wait(), timeout=5.0)
        assert sat.shutdown_event.is_set(), "and the worker threads too"
        assert sat.fatal_error is not None

    asyncio.run(go())


# ─── the ring the boot path painted survives the wake word arming ─────────


class RingLikeTheHardware(Leds):
    """A ring that models the one property this bug turned on.

    `LEDController.resync()` re-renders the controller's CACHED state, while
    `domovoi-status` (the setup indicator) paints `no-server`,
    `no-microphone` and `startup-failed` STRAIGHT to the hardware, where the
    controller has never heard of them. So a `resync()` after one of those
    is not a refresh — it is an overwrite."""

    def __init__(self) -> None:
        super().__init__()
        self.showing = "off"
        self._cached = "idle"

    def set_state(self, state: str) -> None:
        super().set_state(state)
        self._cached = state
        self.showing = state

    def resync(self) -> None:
        super().resync()
        self.showing = self._cached


def test_the_wake_word_arming_does_not_wipe_the_no_server_colour():
    """The boot this branch exists to protect: an approved satellite comes
    up while the Beelink is down but the LAN is up — a whole-house power cut
    where the Pis boot first, or the manual core restart that is part of the
    deploy flow.

    `run()` says `no-server` once at about t+3 s and then relies on the ring
    holding it ("the ring holds the colour on its own"). Ten seconds later
    the mic thread finished its ONNX load and called `resync()`, which
    painted the cached `idle` — i.e. OFF — over the orange, and nothing ever
    said `no-server` again. The device listened through the whole outage
    looking exactly like a healthy one, on the only surface left when the
    network is what is broken."""
    ring = RingLikeTheHardware()
    sat = object.__new__(client.Satellite)
    sat.shutdown_event = threading.Event()
    sat.shutdown_event.set()
    sat._wake_armed = threading.Event()
    sat._wake_word = "hey_domovoi"
    sat._leds = ring
    sat.wake_recording = threading.Event()
    sat.dropin_active = threading.Event()
    sat.chat_active = threading.Event()
    sat._load_wake_model = lambda: object()
    sat._calibrate_mic_gain_initial = lambda: None
    sat._calibrate_noise_gate_initial = lambda: None
    # This mic thread was started by `run()` at boot, not by a `ready`
    # frame, so nothing handed it the ring.
    sat._ring_handoff_pending = False

    # t+3 s: `run()` gives up on the core and paints the ring directly.
    ring.showing = "no-server"

    client.Satellite._mic_thread_run(sat)

    assert ring.showing == "no-server", (
        "the only surface left during an outage still says there is an outage"
    )
    assert ring.resyncs == 0
    assert sat._wake_armed.is_set(), "and the wake word is armed regardless"


def test_the_approval_handoff_still_paints_the_ring():
    """The other side of the same flag: on the `ready` that OPENS the mic,
    the transition is still the mic thread's to make, so the person who just
    clicked Approve does not watch the ring go idle a quarter of a minute
    before the wake word works."""
    ring = RingLikeTheHardware()
    sat = object.__new__(client.Satellite)
    sat.shutdown_event = threading.Event()
    sat.shutdown_event.set()
    sat._wake_armed = threading.Event()
    sat._wake_word = "hey_domovoi"
    sat._leds = ring
    sat.wake_recording = threading.Event()
    sat.dropin_active = threading.Event()
    sat.chat_active = threading.Event()
    sat._load_wake_model = lambda: object()
    sat._calibrate_mic_gain_initial = lambda: None
    sat._calibrate_noise_gate_initial = lambda: None
    sat._ring_handoff_pending = True
    ring.showing = "awaiting-approval"

    client.Satellite._mic_thread_run(sat)

    assert ring.resyncs == 1
    assert ring.showing == "idle", "off the violet, once it can answer"
    assert sat._ring_handoff_pending is False, "and only once"


def test_a_boot_path_run_never_arms_the_ring_handoff(monkeypatch):
    """Through the real `run()`, because the flag being False is only worth
    something if the boot path really does leave it False."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1
    assert rec.s._ring_handoff_pending is False
    assert rec.s._leds.resyncs == 0, "nothing repaints the ring on this path"
    assert ("no-server",) in rec.setup_status


# ─── a record the client cannot open for writing is not a verdict ─────────


def test_a_record_the_client_cannot_open_is_replaced_by_rename(monkeypatch):
    """Provisioning writes `not-approved` as ROOT and hands it over with a
    best-effort chown. When that chown fails the satellite user can read the
    file and not write it — and `write_text` opens `'w'`, which needs
    permission on the FILE. The record was then frozen at `not-approved` for
    the life of the device: deaf at every boot until its core answered, with
    no wake word and therefore no network_issues.mp3 during an outage, and
    the receipts bridge could not reach it because a decided NO short-
    circuits before the receipts are consulted. The record is written to a
    sibling and renamed now, which needs the DIRECTORY and not the file."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("not-approved\n", encoding="utf-8")
    _refuse_writes(
        monkeypatch, only_the_marker=True,
        exc=PermissionError(13, "Permission denied"),
    )

    run_sessions(rec, [accepted()], monkeypatch)
    assert rec.mic_opens == 1, "the core accepted it, so the mic opened"

    # The next boot, over the same disk, with no core in sight.
    again = make_sat(monkeypatch)
    run_sessions(again, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert again.mic_opens == 1, "the acceptance survived the reboot"
    assert ("no-server",) in again.setup_status
    assert client._read_approval_record() == client._APPROVAL_YES


def test_a_no_nothing_could_have_corrected_does_not_pin_a_room_deaf(monkeypatch):
    """The residue of the same failure, for a device whose whole config dir
    refuses writes. A `not-approved` is evidence of the core's verdict only
    if a later `approved` COULD have replaced it; where nothing can be
    written it is an accident of when the disk broke. So the gate falls
    through to the receipts — and a receipt can only ever speak for a unit
    that was previously in service."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("not-approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1, "the wake word works through the whole outage"
    assert rec.mic_thread_bodies == 1
    assert ("no-server",) in rec.setup_status


def test_the_same_no_keeps_a_device_with_no_receipts_shut(monkeypatch):
    """The half that makes the one above safe. Identical disk, identical
    unwritable dir, no receipt — and the microphone stays shut, because the
    fall-through is to evidence and not to optimism."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("not-approved\n", encoding="utf-8")
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )

    run_sessions(rec, [unreachable(), unreachable(), parked()], monkeypatch)

    assert rec.mic_opens == 0, "a device nobody has approved stays deaf"
    assert rec.wake_models_loaded == 0


# ─── a refusal that cannot be recorded reaches the mic anyway ─────────────


def test_a_reset_pairing_that_cannot_be_recorded_still_reaches_the_mic(
    monkeypatch, caplog,
):
    """"Reset pairing" has to reach the microphone. Where the record can be
    written it reaches it through the record and the next restart; where it
    cannot, there is no restart that would settle it — the record still says
    `approved`, so the device would come back up listening for ever. So it
    reaches the microphone directly instead: capture stops now, and nothing
    reopens it in this process."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )

    with caplog.at_level("ERROR", logger="satellite"):
        run_sessions(rec, [parked()], monkeypatch)

    assert rec.mic_opens == 1, "approved at boot, from a record it can read"
    assert sat._voice_gate_closed is True, "the refusal reached the microphone"
    assert rec.mic_stops >= 1
    assert sat._input_stream is None
    # Nothing opens it again for the rest of this process — not a later
    # `ready`, not a reconnect.
    assert sat._start_voice_input(fatal=False) is False
    assert rec.mic_opens == 1
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "could not be written" in said or "could not record" in said


def test_a_refusal_that_cannot_be_written_does_not_erase_the_record(
    monkeypatch, caplog,
):
    """The fallback used to unlink the record when it could not write
    `not-approved`, calling that "still better than leaving a stale
    approved". With the receipts bridge in the same file it is not: absent
    means UNDECIDED, so the synced-sha sidecar the device has carried since
    its last self-upgrade re-approved it on the very next boot — undoing the
    refusal the write was trying to record. ENOSPC is the shape where that
    mattered: the write fails and an unlink would have SUCCEEDED."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(28, "No space left on device"),
    )

    with caplog.at_level("ERROR", logger="satellite"):
        run_sessions(rec, [parked()], monkeypatch)

    assert client.APPROVED_MARKER.is_file(), "the record was not erased"
    assert rec.s._voice_gate_closed is True, (
        "and the refusal reached the microphone another way"
    )
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "could not write 'not-approved' to the approval record" in said


def test_no_stray_temp_file_is_left_behind_when_a_write_fails(monkeypatch):
    """The rename's sibling is cleaned up on the way out, so a device that
    cannot write its record does not accumulate `approved.tmp` either."""
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(28, "No space left on device"),
    )

    assert client._write_approval_record(client._APPROVAL_YES) is False
    leftovers = sorted(p.name for p in client.APPROVED_MARKER.parent.iterdir())
    assert leftovers == [], f"nothing left behind, got {leftovers}"


def test_a_write_that_lands_says_so_and_one_that_does_not_says_so(monkeypatch):
    """`_write_approval_record` returns whether the verdict is on disk, which
    is what lets the two callers do something about a failure instead of
    deciding the device's fate silently."""
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    assert client._write_approval_record(client._APPROVAL_YES) is True
    assert client._read_approval_record() == client._APPROVAL_YES
    assert client._remember_approved() is True
    assert client._forget_approved() is True
    assert client._read_approval_record() == client._APPROVAL_NO

    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )
    assert client._write_approval_record(client._APPROVAL_YES) is False
    assert client._remember_approved() is False
    # ...and the refusal already on disk is left exactly where it is.
    assert client._read_approval_record() == client._APPROVAL_NO
    # And `_forget_approved` says NO even though the file already says the
    # right word, because what the caller reads out of it is "will the next
    # boot be shut", not "does the file spell not-approved". On this disk
    # the answer is no: nothing could ever have corrected that word, so
    # `_approval_decision` stops treating it as a verdict.
    assert client._forget_approved() is False, (
        "the refusal is on disk but nothing can be written there, so it is "
        "not a refusal the next boot will obey"
    )


def test_the_record_is_written_by_rename_not_in_place():
    """Why a torn record is not merely handled but prevented: `write_text`
    truncates and then writes, and the window is exactly when someone
    power-cycles a satellite that "isn't doing anything yet"."""
    src = inspect.getsource(client._write_approval_record)
    assert "os.replace" in src
    assert ".tmp" in src
    # And a failed write no longer unlinks the RECORD (only its own temp
    # sibling), which is what used to hand a refused device back to the
    # receipts bridge on the next boot.
    assert "APPROVED_MARKER.unlink" not in src


# ─── the refusal reaches the mic on the devices it was written for ────────
#
# Round 3 shipped `_close_voice_gate` and a failure table promising that a
# device whose core is parking it stops capturing. On the one row the
# teardown was written for it never ran, because `_forget_approved` reported
# a refusal as recorded when it had merely FOUND one on disk — true about
# the file, false about the outcome. And where it did run it never came
# back, so a genuine re-approval left an online, approved room permanently
# deaf. The two are the same interaction and are pinned together.


def _watch_the_stream(rec, monkeypatch) -> list[tuple[str, bool, bool]]:
    """Sample the capture stream every time the setup indicator is written.

    `run()`'s teardown calls `_stop_mic()` on its way out, so a post-run
    `_input_stream is None` cannot tell a device that was torn down from one
    that was listening the whole time and then shut down normally. These
    samples are taken WHILE the session that provoked them is live."""
    snaps: list[tuple[str, bool, bool]] = []
    recorder = client._setup_status

    def spy(state, *args):
        snaps.append(
            (state, rec.s._input_stream is not None, rec.s._voice_gate_closed)
        )
        return recorder(state, *args)

    monkeypatch.setattr(client, "_setup_status", spy)
    return snaps


def accepted_watching(snaps: list):
    """`accepted()`, sampling the capture stream while the session is up."""
    async def attempt(sat) -> None:
        await _deliver(sat, [{
            "type": "ready", "protocol_version": "1", "bot_name": "Domovoi",
        }])
        snaps.append(sat._input_stream is not None)
        sat._on_session_ended()
    return attempt


def test_a_no_it_could_not_have_corrected_still_reaches_the_microphone(
    monkeypatch, caplog,
):
    """Failure-table row 5, which used to be the worst cell in the branch.

    Record `not-approved`, config dir refusing writes, a receipt on disk: the
    NO is not a verdict — nothing could ever have corrected it — so the
    microphone opens at boot, which is the point of that row. Then its core
    answers `awaiting_approval`. The old `_forget_approved` read the record,
    saw `not-approved` already there and returned True without attempting
    anything, so the caller took the "a restart settles it" branch. The
    restart settled nothing: the next boot read the same word off the same
    unwritable disk, found the same receipt, and opened the microphone
    again. The room answered its wake word through every session its core
    refused, on every boot, for ever — F-V013 reached through the gate's own
    memory, in a cell the branch documented as handled."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("not-approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )
    snaps = _watch_the_stream(rec, monkeypatch)

    with caplog.at_level("ERROR", logger="satellite"):
        run_sessions(rec, [parked(), parked(), parked()], monkeypatch)

    # The behaviour first: the stream is gone before the device is told to
    # show the awaiting-approval colour, on the first refusal and every one
    # after it.
    listening = [live for state, live, _ in snaps if state == "awaiting-approval"]
    assert listening == [False, False, False], (
        "a device its core is refusing does not keep capturing the room"
    )
    assert rec.mic_opens == 1, "row 5 still opens at boot — the NO is no verdict"
    assert rec.mic_stops >= 1
    assert rec.s._voice_gate_closed is True
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "will not answer its wake word" in said

    # And the next boot over the same disk does the same thing rather than
    # inheriting the deafness: it opens (row 5), and is shut again the
    # moment its core answers. Bounded per boot, never permanent.
    again = make_sat(monkeypatch)
    again_snaps = _watch_the_stream(again, monkeypatch)
    run_sessions(again, [parked(), parked()], monkeypatch)
    assert again.mic_opens == 1
    assert [live for s, live, _ in again_snaps if s == "awaiting-approval"] == [
        False, False,
    ]
    assert again.s._voice_gate_closed is True


def test_a_no_it_could_have_corrected_still_leaves_the_mic_alone(monkeypatch):
    """The control, and the branch of this that must NOT change.

    Same shape, writable disk, record `approved`. The refusal lands, so the
    next restart genuinely settles it, and tearing capture down underneath a
    running wake loop buys nothing the closed socket has not already bought.
    The mic stays open for the rest of this boot and says so — round 2's
    finding 7, kept deliberately. The only variable between this test and
    the one above is whether the disk accepts the write."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    snaps = _watch_the_stream(rec, monkeypatch)

    run_sessions(rec, [parked(), parked()], monkeypatch)

    assert [live for s, live, _ in snaps if s == "awaiting-approval"] == [
        True, True,
    ], "the documented, deliberate path is untouched"
    assert rec.s._voice_gate_closed is False
    assert client._read_approval_record() == client._APPROVAL_NO
    # ...and the restart really does settle it, which is what earns it.
    again = make_sat(monkeypatch)
    run_sessions(again, [unreachable(), unreachable(), unreachable()], monkeypatch)
    assert again.mic_opens == 0


def test_a_core_that_accepts_it_again_gives_the_microphone_back(monkeypatch):
    """`_close_voice_gate` latched for the life of the process and nothing
    cleared it, so the admin who reset a room's pairing and then approved it
    again got a satellite that was ONLINE, approved on the dashboard, and
    could not hear a wake word — with `fatal_error` None and
    `shutdown_event` unset, so systemd never intervened either. The branch's
    own reason for not latching the sibling path says this must not happen:
    the only frame that reopens the gate is `ready`, and a `ready` IS the
    core accepting the device."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )
    parked_snaps = _watch_the_stream(rec, monkeypatch)
    live: list[bool] = []

    run_sessions(
        rec,
        [parked(), accepted_watching(live), accepted_watching(live)],
        monkeypatch,
    )

    assert live == [True, True], "approved again, and hearing again"
    assert [s for st, s, _ in parked_snaps if st == "awaiting-approval"] == [
        False,
    ], "and it really was torn down first"
    assert rec.s._voice_gate_closed is False
    assert rec.mic_opens == 2, "boot, then the stream the acceptance gave back"
    assert rec.mic_thread_bodies == 1, (
        "one wake loop — a second thread on one raw_q would see half the audio"
    )
    assert rec.s.fatal_error is None
    # The ring follows the microphone rather than staying on whatever the
    # awaiting-approval frame painted, which it did while the latch held.
    assert rec.s._leds.resyncs == 2


def test_the_writability_question_is_asked_by_performing_the_write(monkeypatch):
    """The predictor used to create and delete a ZERO-BYTE `approved.probe`
    and call that an answer about a ~250-byte `approved.tmp` renamed over an
    existing file. A card out of data blocks with inodes still free takes
    the first and refuses the second — the ordinary end of a Pi that has
    logged to a small card for months — so the gate believed a later
    `approved` could have replaced the `not-approved` on disk, obeyed the
    NO, and left the room deaf for the whole of any outage its core could
    not answer. The question is now asked by doing the thing."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("not-approved\n", encoding="utf-8")
    _receipt(client.SYNCED_SHA_SIDECAR)

    real = client.Path.write_text
    written: list[str] = []

    def guarded(self, data="", *a, **kw):
        written.append(self.name)
        if self.name == client.APPROVED_MARKER.name + ".tmp" and data:
            raise OSError(28, "No space left on device")
        return real(self, data, *a, **kw)

    monkeypatch.setattr(client.Path, "write_text", guarded)

    run_sessions(rec, [unreachable(), unreachable(), unreachable()], monkeypatch)

    assert rec.mic_opens == 1, (
        "no verdict can land on this card, so the NO on it is not one"
    )
    assert ("no-server",) in rec.setup_status
    assert not any(name.endswith(".probe") for name in written), (
        f"the record is the only file the gate writes, got {written}"
    )
    assert not hasattr(client, "_approval_record_can_be_rewritten"), (
        "a predictor that does not predict is worse than none"
    )
    assert client._write_approval_record(client._APPROVAL_YES) is False


def test_a_directory_where_the_record_should_be_reaches_the_mic_both_ways(
    monkeypatch,
):
    """The same three bugs on a card with nothing else wrong with it.

    No write is refused here: `~/.domovoi` is ordinary and writable. There
    is simply a DIRECTORY where `approved` should be, so `read_text` cannot
    parse a verdict (DAMAGED) and `os.replace` cannot step over it however
    healthy the disk is. The old probe wrote its zero-byte `approved.probe`
    beside it and answered yes. This device therefore reaches the whole
    interaction — open from a receipt, torn down when its core parks it,
    given back when its core accepts it — without a broken SD card anywhere
    in the story."""
    rec = make_sat(monkeypatch)
    client.APPROVED_MARKER.mkdir(parents=True, exist_ok=True)
    _receipt(client.SYNCED_SHA_SIDECAR)
    parked_snaps = _watch_the_stream(rec, monkeypatch)
    live: list[bool] = []

    run_sessions(
        rec, [accepted_watching(live), parked(), accepted_watching(live)],
        monkeypatch,
    )

    assert live == [True, True]
    assert [s for st, s, _ in parked_snaps if st == "awaiting-approval"] == [
        False,
    ], "the refusal reached the microphone"
    assert client._read_approval_record() == client._APPROVAL_DAMAGED
    assert rec.mic_opens == 2
    assert rec.mic_thread_bodies == 1
    assert rec.s._voice_gate_closed is False
    assert rec.s.fatal_error is None


def test_the_gate_reopens_only_for_the_core_and_not_for_anything_else(
    monkeypatch,
):
    """The latch is still a latch. `_start_voice_input` — the door every
    other caller uses, including a reconnect — refuses while the gate is
    closed; only `_reopen_voice_gate`, which `_note_session_accepted` calls
    on a `ready` frame, opens it."""
    rec = make_sat(monkeypatch)
    sat = rec.s
    client.APPROVED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    client.APPROVED_MARKER.write_text("approved\n", encoding="utf-8")
    _refuse_writes(
        monkeypatch, only_the_marker=False,
        exc=OSError(30, "Read-only file system"),
    )
    sat._start_voice_input(fatal=True)
    assert rec.mic_opens == 1

    sat._close_voice_gate()
    assert sat._voice_gate_closed is True
    assert sat._start_voice_input(fatal=False) is False, "not through that door"
    assert sat._input_stream is None
    assert rec.mic_opens == 1

    sat._reopen_voice_gate()
    assert sat._voice_gate_closed is False
    assert sat._input_stream is not None
    assert rec.mic_opens == 2
    assert rec.mic_thread_bodies == 1
    # Idempotent from both sides: the core repeats itself on every
    # reconnect, and a reopen must not stack streams or threads.
    sat._reopen_voice_gate()
    assert rec.mic_opens == 2
