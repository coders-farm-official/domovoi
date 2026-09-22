"""Frames emitted while the core is unreachable are dropped at the source.

The mic thread keeps running the wake → capture path across an outage
(the canned-clip branch only arms after `wifi.degraded_after_disconnect_sec`,
30 s by default, sampled on the 60 s Wi-Fi poll). Every frame it emitted
used to land in the previous session's `send_q` - an unbounded
asyncio.Queue nothing drained - and the next session replaced that queue
wholesale. Up to `max_record_seconds` of PCM per wake was held for the
whole outage and then lost silently.

Pinned here:
  * `_on_session_ended` nulls the queue; the emitters are then no-ops that
    never touch the old queue;
  * a new session gets a fresh queue, and what the emitters send reaches
    the wire;
  * a capture whose closing frame went nowhere reports False, so the wake
    loop does not park waiting for a reply that cannot come;
  * the dropped-capture warning fires once per window, not once per frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import types

import numpy as np
import pytest

from satellite.tests._client_import import import_client

client = import_client()

FRAME = np.full(client.FRAME_SAMPLES, 8000, dtype=np.int16).tobytes()   # ~-12 dBFS
SILENCE = bytes(client.FRAME_BYTES)                                       # -inf dBFS


class Leds:
    def __init__(self) -> None:
        self.states: list[str] = []

    def set_state(self, state: str) -> None:
        self.states.append(state)

    def set_state_unless(self, unless: str, state: str) -> None:
        self.states.append(state)


def make_sat(*, loop=None):
    """A Satellite with exactly the state the session lifecycle touches."""
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(
        domovoi_url="ws://192.168.0.117:6370",
        room_id="kitchen",
        sat_type="voice",
        mic_enabled=True,
        wifi_enabled=False,
        device=types.SimpleNamespace(supports_full_duplex=False),
        # `_stream_capture`
        vad_aggressiveness=2,
        silence_timeout=0.3,
        max_record_seconds=5.0,
        noise_gate_dbfs=-40.0,
        noise_gate_auto_calibrate=False,
    )
    sat.loop = loop
    sat.send_q = None
    sat._offline_drops = 0
    sat._offline_drop_last_log = 0.0
    # Everything `_on_session_ended` touches.
    sat.response_done = threading.Event()
    sat.playback_active = threading.Event()
    sat.stop_playback = threading.Event()
    sat.playback_q = queue.Queue()
    sat.wake_recording = threading.Event()
    sat._wake_rec_params = None
    sat.chat_active = threading.Event()
    sat.dropin_active = threading.Event()
    sat._post_playback_state = None
    sat._ws_disconnected_since = None
    sat._network_degraded = threading.Event()
    sat._leds = Leds()
    # `_stream_capture`
    sat.raw_q = queue.Queue()
    sat.shutdown_event = threading.Event()
    sat._greeting_played_this_turn = False
    return sat


async def settle(ticks: int = 5) -> None:
    """Let call_soon_threadsafe callbacks and task cancellations land."""
    for _ in range(ticks):
        await asyncio.sleep(0)


# ─── the queue is gone once the session is ────────────────────────────────


def test_emitters_are_no_ops_after_the_session_ends_and_leave_the_old_queue_alone():
    async def scenario():
        sat = make_sat(loop=asyncio.get_running_loop())
        old_q: asyncio.Queue = asyncio.Queue()
        sat.send_q = old_q

        assert sat._emit_text({"type": "utterance_start"}) is True
        assert sat._emit_audio(FRAME) is True
        await settle()
        assert old_q.qsize() == 2

        sat._on_session_ended()
        assert sat.send_q is None
        assert sat.response_done.is_set(), "the mic thread is unparked"

        # The whole point: nothing captured now lands anywhere.
        assert sat._emit_text({"type": "utterance_end"}) is False
        for _ in range(50):
            assert sat._emit_audio(FRAME) is False
        await settle()
        assert old_q.qsize() == 2, "the previous session's queue is not touched"

    asyncio.run(scenario())


def test_session_end_is_idempotent_with_the_queue_already_gone():
    """A failed reconnect runs `_on_session_ended` again with no queue."""
    sat = make_sat()
    sat._on_session_ended()
    sat._on_session_ended()
    assert sat.send_q is None


# ─── a new session gets a fresh queue ─────────────────────────────────────


class FakeServer:
    """Accepts the hello, lets the satellite emit one frame, then hangs up."""

    def __init__(self, sat) -> None:
        self.sat = sat
        self.sent: list = []
        self.queue_at_hello = None

    async def send(self, data) -> None:
        if self.queue_at_hello is None:
            self.queue_at_hello = self.sat.send_q
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        # Runs on the receiver task, once the sender task is draining.
        assert self.sat._emit_audio(FRAME) is True
        await settle()
        raise StopAsyncIteration


class FakeConnect:
    def __init__(self, server: FakeServer) -> None:
        self.server = server

    async def __aenter__(self):
        return self.server

    async def __aexit__(self, *a):
        return False


def test_each_session_gets_a_fresh_queue_and_frames_reach_the_wire(monkeypatch):
    for name in ("_effective_wake_word",):
        monkeypatch.setattr(client, name, lambda cfg: "domovoi")
    for name in ("_read_synced_sha", "_effective_pairing_token", "_effective_approval_code"):
        monkeypatch.setattr(client, name, lambda: None)

    async def scenario():
        sat = make_sat(loop=asyncio.get_running_loop())
        sat._async_shutdown = asyncio.Event()
        sat._read_output_volume = lambda: None
        sat._emit_voice_status = lambda: None
        sat._emit_config_status = lambda: None

        async def no_sounds():
            return None

        sat._sync_sounds = no_sounds

        servers: list[FakeServer] = []

        def connect(url, **kw):
            server = FakeServer(sat)
            servers.append(server)
            return FakeConnect(server)

        monkeypatch.setattr(client.websockets, "connect", connect)

        await sat._run_session()
        await settle()
        assert sat.send_q is None, "torn down with the session"

        # Between sessions: dropped, and counted for the warning.
        assert sat._emit_audio(FRAME) is False
        assert sat._offline_drops == 1

        await sat._run_session()
        await settle()
        assert sat.send_q is None

        first, second = servers
        assert first.queue_at_hello is not None
        assert second.queue_at_hello is not None
        assert first.queue_at_hello is not second.queue_at_hello, "a fresh queue per session"
        assert FRAME in first.sent and FRAME in second.sent, "emits reach the wire in both"
        assert first.queue_at_hello.qsize() == 0, "the between-sessions frame never landed"
        assert json.loads(first.sent[0])["type"] == "hello"
        # The outage counter is re-armed by the session coming up, so the
        # first drop of the next outage warns at once.
        assert sat._offline_drops == 0
        assert sat._offline_drop_last_log == 0.0

    asyncio.run(scenario())


# ─── a capture that went nowhere does not wait for a reply ────────────────


class FakeVad:
    def __init__(self, level: int) -> None:
        pass

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return any(frame)


def fill_mic(sat, *, loud: int = 5) -> int:
    """Queue `loud` speech frames then enough silence to end the capture.

    Returns the number of frames the capture will consume: the speech plus
    `silence_limit` silent frames, at which point VAD endpointing breaks.
    """
    silence_limit = int(sat.cfg.silence_timeout * 1000 / client.FRAME_MS)
    for _ in range(loud):
        sat.raw_q.put(FRAME)
    for _ in range(silence_limit + 2):
        sat.raw_q.put(SILENCE)
    return loud + silence_limit


def test_a_capture_with_no_session_reports_false_so_the_caller_does_not_wait(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop=loop)
        consumed = fill_mic(sat)

        assert sat._stream_capture([]) is False
        assert sat._offline_drops == consumed, "every frame was dropped, none queued"
        assert sat._leds.states[-1] == "idle", "not left on 'thinking' for a reply that cannot come"
    finally:
        loop.close()


def test_the_same_capture_with_a_session_up_reports_true_and_ends_the_utterance(monkeypatch):
    """The control: the False above is the outage, not the capture."""
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop=loop)
        sat.send_q = asyncio.Queue()
        consumed = fill_mic(sat)

        assert sat._stream_capture([]) is True
        assert sat._offline_drops == 0
        loop.run_until_complete(settle())
        items = []
        while not sat.send_q.empty():
            items.append(sat.send_q.get_nowait())
        assert len(items) == consumed + 1
        assert all(kind == "bytes" for kind, _ in items[:-1])
        assert json.loads(items[-1][1])["type"] == "utterance_end"
        assert sat._leds.states[-1] == "thinking"
    finally:
        loop.close()


# ─── the warning is per window, not per frame ─────────────────────────────


def test_dropped_capture_warns_once_per_window(monkeypatch, caplog):
    clock = {"now": 1000.0}
    monkeypatch.setattr(client.time, "monotonic", lambda: clock["now"])
    sat = make_sat()

    with caplog.at_level(logging.WARNING, logger="satellite"):
        for _ in range(100):
            sat._emit_audio(FRAME)
            clock["now"] += 0.03            # frame rate: 3 s of capture
        dropped = [r for r in caplog.records if "core unreachable" in r.getMessage()]
        assert len(dropped) == 1, "one line for 100 frames, not 100 lines"
        # Fired on the FIRST drop, so the outage is visible at once.
        assert "(1 frames so far this outage)" in dropped[0].getMessage()

        clock["now"] += 31.0                # the window elapses
        sat._emit_audio(FRAME)
        dropped = [r for r in caplog.records if "core unreachable" in r.getMessage()]
        assert len(dropped) == 2
        assert "101 frames so far this outage" in dropped[1].getMessage()
