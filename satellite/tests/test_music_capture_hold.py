"""No music under a capture: a music_start is held until the turn is over.

Dining room, 2026-09-30 ~23:56-00:00Z, music playing: "it seems to be
listening forever. Listening to music while interacting is not going well."
Captures ran to the 30 s `max_record_seconds` cap with their first speech
pause 20-29 s in, and the lyrics were routed and answered. Reproduced on
this client (e19ac3b): nothing kept the satellite's own music player out of
an open capture. A music_start that reached it during a capture, a
follow-up window or the reply that asks the question spawned mpg123 the
moment the speaker was free — and the speaker frees itself in the very
instant the follow-up capture opens — and the capture then heard one long
sentence. The cast fix's retry loop respawned it there too.

Pinned here:

* end to end, through the REAL mic, playback and receiver threads (only the
  room, the sound card, the VAD, the wake model and Popen are stand-ins; a
  fake core answers each capture): a music_start that arrives after a
  question, during it, during a capture, just after the wake, or after a
  wake over an announcement never plays into a capture. It is held until
  the turn is really over — and then plays (a follow-up nobody answered, a
  turn the core ended without a reply), or is dropped by the reply's
  response_start, a music_stop or a wake word, after which the core decides
  the music again. Music playing at the wake is still dead before the
  capture opens, and a refused stream is not retried into a capture;
* the follow-up capture opens only once the question has finished
  playing: the release response_end defers until the speaker drains used
  to fire when the FIRST chunk opened the stream;
* the bound when the satellite itself is the source: a music player found
  running while a capture is open is stopped at the next frame, with an
  error in the log, and the capture ends on its own silence instead of at
  `max_record_seconds`;
* the player is started in a process group of its own and stopped as a
  group (mpg123's `-b` output buffer runs in a forked child).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import subprocess
import threading
import time
import types
from collections import deque

import numpy as np
import pytest

from satellite.tests._client_import import import_client

client = import_client()

# Mic frames and TTS playback run this many times faster than the room.
# Every moment the harness records is read off `time.perf_counter()`:
# `time.monotonic()` ticks every 15.6 ms on Windows, and a player spawned
# just after a capture closed must not read as spawned inside it.
SPEED = 6.0
FRAME_WALL = client.FRAME_MS / 1000 / SPEED
URL = "http://192.168.0.117:8051"
DEVICE = "plughw:CARD=Array,DEV=0"


def _frame(v: int) -> bytes:
    return np.full(client.FRAME_SAMPLES, v, dtype=np.int16).tobytes()


WAKE = _frame(7000)
USER = _frame(5000)
MUSIC = _frame(3000)
SILENCE = bytes(client.FRAME_BYTES)
NAMES = {WAKE: "wake", USER: "user", MUSIC: "music", SILENCE: "silence"}


class FakeVad:
    def __init__(self, *_a) -> None:
        pass

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return any(frame)


class FakeOww:
    def reset(self) -> None:
        pass

    def predict(self, chunk):
        return {"hey_jarvis": 1.0 if int(chunk.min()) == 7000 else 0.0}


class Ring:
    def __init__(self) -> None:
        self.state = "idle"

    def set_state(self, s: str) -> None:
        self.state = s

    def set_state_unless(self, unless: str, s: str) -> None:
        if self.state != unless:
            self.state = s

    def set_state_if(self, expected: str, s: str) -> None:
        if self.state == expected:
            self.state = s

    def resync(self) -> None:
        pass


class FakeMusic:
    """The music mpg123 (its argv has --devbuffer). ``plan``: "plays" until
    terminated, or "refused" (exits rc=1 with mpg123's refusal soon after
    it starts)."""

    def __init__(self, argv, plan: str) -> None:
        self.argv, self.plan = argv, plan
        self.returncode = None
        self.spawned = time.perf_counter()
        self.ended: float | None = None
        self._done = threading.Event()
        self.stderr = self
        self._err = b""
        if plan == "refused":
            self._err = b"[src/resolver.c:timeout_connect():239] error: connection failed: Connection refused\n"
            threading.Timer(0.23 / SPEED, self._exit, args=(1,)).start()

    def _exit(self, rc: int) -> None:
        if not self._done.is_set():
            self.returncode = rc
            self.ended = time.perf_counter()
            self._done.set()

    def read(self) -> bytes:
        self._done.wait()
        return self._err

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return self.returncode

    def terminate(self) -> None:
        self._exit(-15)

    kill = terminate

    def alive(self) -> bool:
        return self.returncode is None

    def audible(self) -> bool:
        return self.plan == "plays" and self.returncode is None


class FakeAckPlayer:
    """The greeting's mpg123 / the chime's aplay: plays ~0.5 s, exits 0."""

    def __init__(self, argv) -> None:
        self.argv = argv
        self.returncode = None
        self._end = time.perf_counter() + 0.5 / SPEED

    def wait(self, timeout=None):
        left = self._end - time.perf_counter()
        if timeout is not None and left > timeout:
            time.sleep(timeout)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        time.sleep(max(0.0, left))
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    terminate = kill


class FakeOut:
    """sounddevice.RawOutputStream: write() takes the audio's duration / SPEED."""

    def __init__(self, samplerate, channels, dtype, device) -> None:
        self.rate = samplerate
        self.latency = 0.1

    def start(self) -> None:
        pass

    def write(self, chunk: bytes) -> None:
        time.sleep(len(chunk) / (self.rate * 2) / SPEED)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeWS:
    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return m


class StampedEvent(threading.Event):
    """An Event that remembers when it was set."""

    def __init__(self) -> None:
        super().__init__()
        self.sets: list[float] = []

    def set(self) -> None:
        self.sets.append(time.perf_counter())
        super().set()


class Room:
    """One satellite in a room, its real threads running, a fake core on
    the other end of its socket.

    The microphone hears, frame by frame: what the person says (a script),
    else the satellite's OWN music while one of its music players "plays",
    else silence. The dining room's config as of the incident: silence 1.2 s,
    VAD 2, gate -60 dBFS, a greeting acknowledgement, barge-in off."""

    def __init__(self, monkeypatch, *, music_plans=("plays",), wake_ack_mode="greeting",
                 prebuffer=0.0) -> None:
        self.t0 = time.perf_counter()
        monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
        monkeypatch.setattr(client, "sd", types.SimpleNamespace(
            RawOutputStream=FakeOut, query_devices=lambda *a, **k: {"default_samplerate": 16000},
        ), raising=False)
        self.music: list[FakeMusic] = []
        self.popen_kwargs: list[dict] = []
        plans = list(music_plans)

        def popen(argv, **kw):
            if "--devbuffer" in argv:
                plan = plans.pop(0) if len(plans) > 1 else plans[0]
                p = FakeMusic(argv, plan)
                self.music.append(p)
                self.popen_kwargs.append(kw)
                self.event("SPAWN music player (%s)" % plan)
                return p
            self.event("ack player %s" % argv[0])
            return FakeAckPlayer(argv)

        monkeypatch.setattr(client.subprocess, "Popen", popen)
        # A greeting clip, as the dining room has (ack_mode greeting).
        clip = client.SOUNDS_CACHE_DIR / "greetings" / "greet_aaaa.mp3"
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.write_bytes(b"ID3")

        sat = object.__new__(client.Satellite)
        self.sat = sat
        sat.cfg = types.SimpleNamespace(
            domovoi_url="ws://127.0.0.1:6370", room_id="dining-room",
            vad_aggressiveness=2, silence_timeout=1.2,
            max_record_seconds=30.0, noise_gate_dbfs=-60.0,
            noise_gate_auto_calibrate=False, noise_gate_noisy_capture_dbfs=-3.0,
            noise_gate_recalibrate_interval_sec=3600.0,
            followup_pre_speech_timeout=8.0,
            wake_ack_mode=wake_ack_mode, greeting_funny_chance=0.0,
            tts_playback_gain=1.0, tts_prebuffer_sec=prebuffer,
            music_alsa_device=DEVICE, music_prime_sec=1.0 / SPEED,
            barge_in=False, barge_in_require_wake_word=False,
            vad_aggressiveness_during_tts=3, barge_in_min_speech_ms=300,
            output_device=None,
            device=types.SimpleNamespace(playback_sample_rate=None, supports_full_duplex=True),
            mic_enabled=True,
        )
        for name in ("shutdown_event", "playback_active", "stop_playback",
                     "expect_followup", "wake_recording", "dropin_active", "chat_active",
                     "_network_degraded", "_end_capture", "_response_audio_received",
                     "_wake_armed"):
            setattr(sat, name, threading.Event())
        sat.response_done = StampedEvent()
        sat._playback_idle = StampedEvent()
        sat._playback_idle.set()
        sat.raw_q = queue.Queue()
        sat.playback_q = queue.Queue()
        sat._leds = Ring()
        sat._wake_word, sat._wake_threshold = "hey_jarvis", 0.5
        sat._ring_handoff_pending = False
        sat._ws_disconnected_since = None
        sat._ack_lock = threading.Lock()
        sat._ack_proc = None
        sat._greeting_played_this_turn = False
        sat._greeting_clip_name = None
        sat._ack_before_capture = False
        sat._music_lock = threading.Lock()
        sat._music_proc = None
        sat._music_url = None
        sat._core_features = frozenset({"speech_pause", "end_capture", "music_failed"})
        sat._capture_lock = threading.Lock()
        sat._barge_prefix = []
        sat._post_playback_state = None
        sat._response_starts = 0
        sat._turn_mark = 0
        sat._turn_capturing = False
        sat._prebuffer_active = False
        sat._prebuffer_buffer = []
        sat._prebuffer_target_bytes = 0
        sat._prebuffer_accumulated = 0
        sat.audio_sample_rate = 16000
        sat._offline_drops = 0
        sat._offline_drop_last_log = 0.0
        sat._DROPIN_MAX_PLAYBACK_FRAMES = 10
        sat._load_wake_model = lambda: FakeOww()
        sat._calibrate_mic_gain_initial = lambda: None
        sat._calibrate_noise_gate_initial = lambda: None
        sat._maybe_recalibrate = lambda samples: None
        # The music supervisor's real-time constants, in room time.
        sat.MUSIC_RETRY_BACKOFF_SEC = tuple(x / SPEED for x in client.Satellite.MUSIC_RETRY_BACKOFF_SEC)
        sat.MUSIC_RETRY_WINDOW_SEC = client.Satellite.MUSIC_RETRY_WINDOW_SEC / SPEED
        sat.MUSIC_CONNECT_GRACE_SEC = client.Satellite.MUSIC_CONNECT_GRACE_SEC / SPEED

        # Every capture's window.
        self.captures: list[dict] = []
        real_capture = client.Satellite._capture.__get__(sat)

        def capture(prefix, pre_speech):
            rec = {"utt": sat._utt_seq, "start": time.perf_counter(), "end": None,
                   "followup": pre_speech is not None}
            self.captures.append(rec)
            self.event("CAPTURE OPEN utt=%d%s" % (rec["utt"], " (follow-up)" if rec["followup"] else ""))
            try:
                rec["result"] = real_capture(prefix, pre_speech)
                return rec["result"]
            finally:
                rec["end"] = time.perf_counter()
                self.event("CAPTURE CLOSED utt=%d" % rec["utt"])

        sat._capture = capture
        # Whether a turn was open whenever the music was stopped.
        self.stops: list[bool] = []
        real_stop = client.Satellite._stop_music.__get__(sat)

        def stop_music():
            self.stops.append(getattr(sat, "_turn_open", None))
            real_stop()

        sat._stop_music = stop_music

        self.script: deque = deque()
        self.script_lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        self.ws = FakeWS()
        sat.ws = self.ws
        sat.loop = self.loop
        sat.send_q = asyncio.Queue()
        self.text_out: list[tuple[float, dict]] = []
        self.audio_by_utt: dict[int, list[str]] = {}
        self.cur_utt: int | None = None
        self.on_end: dict[int, object] = {}     # utt -> async reply(room)
        self.on_start: dict[int, object] = {}   # utt -> async action(room)
        self.events: list[tuple[float, str]] = []
        self._threads: list[threading.Thread] = []

    # ── the room ───────────────────────────────────────────────────────
    def event(self, what: str) -> None:
        self.events.append((time.perf_counter(), what))

    def say(self, frames) -> None:
        with self.script_lock:
            self.script.extend(frames)

    def music_alive(self) -> bool:
        return any(p.audible() for p in self.music)

    def _room_frame(self) -> bytes:
        with self.script_lock:
            f = self.script.popleft() if self.script else None
        if f is not None:
            return f
        return MUSIC if self.music_alive() else SILENCE

    def _feeder(self) -> None:
        nxt = time.perf_counter()
        while not self.sat.shutdown_event.is_set():
            self.sat.raw_q.put(self._room_frame())
            nxt += FRAME_WALL
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)

    # ── the core ───────────────────────────────────────────────────────
    async def send(self, msg: dict) -> None:
        self.event("core -> %s%s" % (msg["type"], " expect_followup" if msg.get("expect_followup") else ""))
        await self.ws.q.put(json.dumps(msg))

    async def tts(self, seconds: float) -> None:
        pcm = (np.sin(np.arange(1600) / 5.0) * 4000).astype(np.int16).tobytes()  # 0.1 s
        for _ in range(int(seconds / 0.1)):
            await self.ws.q.put(pcm)
            await asyncio.sleep(0.1 / SPEED / 2)

    async def _core(self) -> None:
        q = self.sat.send_q
        while True:
            kind, data = await q.get()
            if kind == "bytes":
                if self.cur_utt is not None:
                    self.audio_by_utt.setdefault(self.cur_utt, []).append(NAMES.get(data, "?"))
                continue
            msg = json.loads(data)
            self.text_out.append((time.perf_counter(), msg))
            t = msg["type"]
            if t == "utterance_start":
                self.cur_utt = msg["utt"]
                self.event("sat -> utterance_start utt=%d trigger=%s" % (msg["utt"], msg["trigger"]))
                act = self.on_start.get(msg["utt"])
                if act is not None:
                    asyncio.ensure_future(act(self))
            elif t == "utterance_end":
                self.event("sat -> utterance_end utt=%d exit=%s" % (msg["utt"], msg["exit_reason"]))
                self.cur_utt = None
                act = self.on_end.get(msg["utt"])
                if act is not None:
                    asyncio.ensure_future(act(self))

    # ── running it ─────────────────────────────────────────────────────
    def start(self) -> None:
        def run_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()

        lt = threading.Thread(target=run_loop, daemon=True)
        lt.start()
        self._loop_thread = lt
        self._futs = [
            asyncio.run_coroutine_threadsafe(self.sat._receiver_loop(), self.loop),
            asyncio.run_coroutine_threadsafe(self._core(), self.loop),
        ]
        for target, name in ((self._feeder, "feeder"),
                             (self.sat._playback_thread_run, "playback"),
                             (self.sat._mic_thread_run, "mic")):
            t = threading.Thread(target=target, daemon=True, name=name)
            t.start()
            self._threads.append(t)
        assert self.sat._wake_armed.wait(3)

    def core_now(self, coro_fn) -> None:
        asyncio.run_coroutine_threadsafe(coro_fn(self), self.loop).result(5)

    def wait_until(self, cond, timeout: float = 20.0) -> None:
        deadline = time.perf_counter() + timeout
        while not cond():
            if time.perf_counter() > deadline:
                raise AssertionError("timed out\n" + self.timeline())
            time.sleep(0.01)

    def ends(self) -> list[dict]:
        return [m for _, m in self.text_out if m["type"] == "utterance_end"]

    def turn_over(self) -> bool:
        """The mic thread is back listening for the wake word."""
        return (
            not getattr(self.sat, "_turn_open", False)
            and all(c["end"] is not None for c in self.captures)
        )

    def stop(self) -> None:
        self.sat.shutdown_event.set()
        for t in self._threads:
            t.join(5)
        for p in self.music:
            p.terminate()
        for f in self._futs:
            f.cancel()
        time.sleep(0.1)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(2)
        self.loop.close()

    # ── what happened ──────────────────────────────────────────────────
    def music_in_capture(self, rec: dict) -> list[FakeMusic]:
        """Music players that were playing at any moment of the capture."""
        out = []
        for p in self.music:
            end = p.ended if p.ended is not None else float("inf")
            if p.plan == "plays" and p.spawned < rec["end"] and end > rec["start"]:
                out.append(p)
        return out

    def spawned_in(self, rec: dict) -> list[FakeMusic]:
        return [p for p in self.music if rec["start"] <= p.spawned <= rec["end"]]

    def heard(self, utt: int) -> list[str]:
        return self.audio_by_utt.get(utt, [])

    def timeline(self) -> str:
        lines = []
        for t, e in sorted(self.events, key=lambda x: x[0]):
            lines.append("  %6.2fs room time  %s" % ((t - self.t0) * SPEED, e))
        for rec in self.captures:
            audio = self.heard(rec["utt"])
            counts = {k: audio.count(k) for k in ("user", "music", "silence", "wake")}
            lines.append("  capture utt=%d followup=%s frames-sent=%s" % (rec["utt"], rec["followup"], counts))
        return "\n".join(lines)


def play_music(room: Room) -> None:
    """Music already playing in the room before the wake word."""
    room.core_now(lambda r: r.send({"type": "music_start", "stream_url": URL}))
    room.wait_until(lambda: room.music_alive())
    room.wait_until(lambda: any(m["type"] == "music_ready" for _, m in room.text_out), 5)


def reply(*, followup: bool, music_before_end: bool = False, music_after_end: bool = False,
          tts_s: float = 1.0):
    """The core's answer to a capture: response_start, ``tts_s`` of speech,
    response_end; a music_start during the speech or right after it, as the
    core's auto-resume or a dashboard cast would send one."""
    async def run(room: Room) -> None:
        await asyncio.sleep(0.05)                      # STT + route
        await room.send({"type": "response_start", "audio_sample_rate": 16000,
                         "text": "Want me to check online?" if followup else "OK."})
        if music_before_end:
            await room.send({"type": "music_start", "stream_url": URL})
        await room.tts(tts_s)
        await room.send({"type": "response_end", "interrupted": False, "expect_followup": followup})
        if music_after_end:
            await room.send({"type": "music_start", "stream_url": URL})
    return run


async def no_reply(room: Room) -> None:
    """A capture the core ends without a word (it heard nothing): a bare
    response_end."""
    await asyncio.sleep(0.05)
    await room.send({"type": "response_end", "interrupted": False, "expect_followup": False})


async def music_start(room: Room) -> None:
    await room.send({"type": "music_start", "stream_url": URL})


def music_start_after(delay_room_s: float):
    async def run(room: Room) -> None:
        await asyncio.sleep(delay_room_s / SPEED)
        await room.send({"type": "music_start", "stream_url": URL})
    return run


# The wake word, a beat, ~0.9 s of command.
WAKE_AND_COMMAND = [WAKE] * 6 + [None] * 30 + [USER] * 30


@pytest.fixture
def room_factory(monkeypatch):
    rooms: list[Room] = []

    def make(**kw) -> Room:
        r = Room(monkeypatch, **kw)
        rooms.append(r)
        return r

    yield make
    for r in rooms:
        if not r.sat.shutdown_event.is_set():
            r.stop()


def _cleanly_ended(room: Room, utt: int) -> None:
    end = next(m for m in room.ends() if m["utt"] == utt)
    assert end["exit_reason"] == "vad_silence_after_speech", room.timeline()
    assert "music" not in room.heard(utt), room.timeline()


# ─── a question, and music_start sent after it or during it ──────────────


def test_a_start_after_a_question_waits_out_the_followup_then_plays(room_factory):
    """Music playing; wake; command; the reply asks a question; a
    music_start right after its response_end. It used to spawn mpg123 into
    the follow-up capture, which then ran 30 s on 945 frames of music."""
    room = room_factory()
    room.start()
    play_music(room)
    room.on_end[1] = reply(followup=True, music_after_end=True)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.captures) == 2 and room.captures[1]["end"] is not None, 30)
    room.wait_until(lambda: len(room.music) == 2, 10)
    room.stop()

    wake, follow = room.captures
    assert follow["followup"]
    _cleanly_ended(room, wake["utt"])
    assert not room.music_in_capture(follow), room.timeline()
    assert "music" not in room.heard(follow["utt"])
    # Nobody answered: the follow-up timed out (no utterance_end) and the
    # held start played once the turn was over.
    assert follow["result"] is False
    assert [e["utt"] for e in room.ends()] == [wake["utt"]]
    assert room.music[1].spawned >= follow["end"]


def test_a_start_during_the_question_waits_out_the_followup_then_plays(room_factory):
    """The music_start came while the question was still playing. It waited
    for the speaker, and the speaker frees itself in the same instant as the
    follow-up capture opens."""
    room = room_factory()
    room.start()
    play_music(room)
    room.on_end[1] = reply(followup=True, music_before_end=True)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.captures) == 2 and room.captures[1]["end"] is not None, 30)
    room.wait_until(lambda: len(room.music) == 2, 10)
    room.stop()

    follow = room.captures[1]
    assert follow["followup"] and follow["result"] is False
    assert not room.music_in_capture(follow), room.timeline()
    assert room.music[1].spawned >= follow["end"]


def test_an_answered_question_drops_the_held_start_for_the_core_to_decide(room_factory):
    """Someone answers the question: the answer's reply (its
    response_start) drops the held start, and the core decides the music
    again at the end of that turn — here, a music_start after it."""
    room = room_factory()
    room.start()
    play_music(room)
    room.on_end[1] = reply(followup=True, music_before_end=True)
    room.on_end[2] = reply(followup=False, music_after_end=True)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.captures) == 2, 30)
    room.say([USER] * 25)                              # "yes, please"
    room.wait_until(lambda: len(room.ends()) == 2, 30)
    room.wait_until(lambda: len(room.music) == 2, 10)
    room.stop()

    follow = room.captures[1]
    _cleanly_ended(room, follow["utt"])
    assert len(room.music) == 2                       # the held one never spawned
    assert room.music[1].spawned >= follow["end"]
    assert room.turn_over()


def test_a_refused_stream_is_not_retried_into_the_followup_capture(room_factory):
    """The stream refuses twice before it plays (the cast fix's retries):
    every spawn comes after the follow-up capture, none inside it."""
    room = room_factory(music_plans=("plays", "refused", "refused", "plays"))
    room.start()
    play_music(room)
    room.on_end[1] = reply(followup=True, music_after_end=True)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.captures) == 2 and room.captures[1]["end"] is not None, 30)
    room.wait_until(lambda: len(room.music) == 4 and room.music_alive(), 10)
    room.stop()

    follow = room.captures[1]
    assert not room.spawned_in(follow), room.timeline()
    assert not room.music_in_capture(follow)
    assert all(p.spawned >= follow["end"] for p in room.music[1:])


# ─── music_start during a capture ─────────────────────────────────────────


def test_a_start_during_a_capture_is_held_and_the_reply_drops_it(room_factory):
    """No music; a music_start lands 0.3 s into the wake capture with the
    speaker idle (a dashboard cast). It used to spawn at once and the
    capture ran 30 s. Held now, then dropped by the reply's response_start
    — the core sends a fresh one after the reply when the music is still
    wanted."""
    room = room_factory()
    room.start()
    room.on_start[1] = music_start_after(0.3)
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(room.turn_over, 10)
    time.sleep(0.3)
    room.stop()

    _cleanly_ended(room, 1)
    assert room.music == [], room.timeline()


def test_a_start_held_through_a_turn_with_no_reply_plays_when_it_is_over(room_factory):
    """Held, not dropped: the core ends this capture without a reply (it
    heard nothing), so nothing stops the held start, and it plays once the
    mic thread is back listening for the wake word."""
    room = room_factory()
    room.start()
    room.on_start[1] = music_start_after(0.3)
    room.on_end[1] = no_reply
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(lambda: len(room.music) == 1, 10)
    room.stop()

    _cleanly_ended(room, 1)
    assert room.music[0].spawned >= room.captures[0]["end"]


def test_a_late_resume_just_after_the_wake_stays_out_of_the_capture(room_factory):
    """Music playing at the wake is stopped; a music_start the core had in
    flight lands just after that stop. It used to play over the capture
    (the stop count was taken after the stop)."""
    room = room_factory()
    room.start()
    play_music(room)
    room.on_start[1] = music_start
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(room.turn_over, 10)
    room.stop()

    _cleanly_ended(room, 1)
    assert not room.music_in_capture(room.captures[0]), room.timeline()


def test_a_start_after_a_wake_over_an_announcement_stays_out_of_the_capture(room_factory):
    """An announcement is playing; the wake word is said over it; a
    music_start arrives after the wake. It used to wait for the
    announcement to drain — which happened mid-capture — and spawn there."""
    room = room_factory()

    async def announcement(r: Room) -> None:
        await r.send({"type": "response_start", "audio_sample_rate": 16000, "text": "Timer done."})
        await r.tts(1.5)
        await r.send({"type": "response_end", "interrupted": False, "expect_followup": False})

    room.start()
    room.core_now(announcement)
    room.wait_until(lambda: not room.sat._playback_idle.is_set(), 5)
    room.on_start[1] = music_start
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(room.turn_over, 10)
    room.stop()

    assert not room.music_in_capture(room.captures[0]), room.timeline()
    assert "music" not in room.heard(1)


def test_a_start_waiting_before_the_wake_is_dropped_by_it(room_factory):
    """Unchanged: a music_start already waiting for an announcement to
    drain when the wake word fires is dropped by the wake."""
    room = room_factory()

    async def announcement_with_music(r: Room) -> None:
        await r.send({"type": "response_start", "audio_sample_rate": 16000, "text": "Timer done."})
        await r.send({"type": "music_start", "stream_url": URL})
        await r.tts(6.0)
        await r.send({"type": "response_end", "interrupted": False, "expect_followup": False})

    room.start()
    room.core_now(announcement_with_music)
    room.wait_until(lambda: not room.sat._playback_idle.is_set(), 5)
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(room.turn_over, 10)
    room.stop()

    assert room.music == []
    _cleanly_ended(room, 1)


# ─── music playing at the wake ────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["greeting", "chime", "none"])
def test_music_playing_at_the_wake_is_stopped_before_the_capture_opens(room_factory, mode):
    room = room_factory(wake_ack_mode=mode)
    room.start()
    play_music(room)
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.stop()

    cap = room.captures[0]
    assert room.music[0].ended is not None and room.music[0].ended <= cap["start"]
    _cleanly_ended(room, 1)
    # The turn was marked open before the wake stopped the music: a start
    # checking in between waits rather than spawning (`_music_hold_reason`).
    assert room.stops and all(room.stops)


def test_a_wake_in_a_refused_streams_backoff_spawns_nothing_later(room_factory):
    room = room_factory(music_plans=("refused",))
    room.start()
    room.core_now(music_start)
    room.wait_until(lambda: len(room.music) == 1 and not room.music[0].alive(), 5)
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    time.sleep(1.0)
    room.stop()

    assert not room.spawned_in(room.captures[0])


# ─── the follow-up capture opens once the question has played ────────────


@pytest.mark.parametrize("prebuffer", [0.0, 0.5], ids=["no-prebuffer", "prebuffer-0.5s"])
def test_the_followup_capture_opens_only_once_the_question_has_played(room_factory, prebuffer):
    """A 1.5 s question. The follow-up capture used to open ~2 s early in
    about half the runs: the release response_end defers until the speaker
    drains fired when the reply's FIRST chunk opened the stream."""
    for _ in range(3):
        room = room_factory(prebuffer=prebuffer)
        room.start()
        room.on_end[1] = reply(followup=True, tts_s=1.5)
        room.say(WAKE_AND_COMMAND)
        room.wait_until(lambda: len(room.captures) == 2, 20)
        room.stop()
        drained = room.sat._playback_idle.sets[-1]
        assert room.captures[1]["start"] >= drained, room.timeline()
        assert room.sat.response_done.sets[-1] >= drained


# ─── the bound when the satellite itself is the source ───────────────────


def test_a_player_running_under_a_capture_is_stopped_and_the_capture_ends(
    room_factory, caplog,
):
    """However it got there, the satellite's own music under an open capture
    is stopped at the next frame, loudly, and the capture ends on its own
    silence — not 30 s later at max_record_seconds."""
    caplog.set_level(logging.INFO, logger=client.log.name)
    room = room_factory()

    async def sneak_music_in(r: Room) -> None:
        # Past every guard, straight to the spawn.
        with r.sat._music_lock:
            r.sat._spawn_music_locked(URL, attempt=1)

    room.start()
    room.on_start[1] = sneak_music_in
    room.on_end[1] = reply(followup=False)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.stop()

    end = room.ends()[0]
    assert end["exit_reason"] == "vad_silence_after_speech", room.timeline()
    assert room.heard(1).count("music") <= 3
    assert room.music[0].ended is not None and room.music[0].ended < room.captures[0]["end"]
    assert "own music player is running while the microphone is open" in caplog.text
    assert any(r.levelno == logging.ERROR for r in caplog.records
               if "microphone is open" in r.getMessage())


def test_the_player_gets_a_process_group_of_its_own(room_factory):
    room = room_factory()
    room.start()
    play_music(room)
    room.stop()
    assert room.popen_kwargs[0].get("start_new_session") is True


def test_a_wake_while_the_network_is_down_leaves_no_turn_open(room_factory):
    """The wake word with the socket down plays the canned "network issues"
    clip and goes straight back to listening, inside `_wait_for_wake`: no
    turn is left open behind it, or a music_start after the reconnect would
    be held until the next wake word."""
    room = room_factory()
    room.sat._network_degraded.set()
    room.sat._ws_disconnected_since = time.monotonic()
    played: list = []
    room.sat._play_canned_mp3 = lambda *a: played.append(a)
    room.start()
    room.say([WAKE] * 6)
    room.wait_until(lambda: played, 10)
    time.sleep(0.2)
    turn_open = room.sat._turn_open
    room.stop()
    assert not turn_open
    assert room.captures == []


# ─── the pieces, without threads ──────────────────────────────────────────


def _bare_sat():
    sat = object.__new__(client.Satellite)
    for name in ("chat_active", "dropin_active", "wake_recording", "shutdown_event"):
        setattr(sat, name, threading.Event())
    sat._ack_proc = None
    return sat


def test_what_holds_the_music():
    sat = _bare_sat()
    assert sat._music_hold_reason() is None
    sat._turn_open = True
    assert sat._music_hold_reason() == "a turn is in progress"
    sat._turn_open = False
    sat._capture_utt = 4
    assert sat._music_hold_reason() == "a capture is open"
    sat._capture_utt = None
    sat._ack_proc = object()
    assert sat._music_hold_reason() == "the wake acknowledgement is playing"
    sat._ack_proc = None
    for name, what in (("chat_active", "chat mode"), ("dropin_active", "a drop-in call"),
                       ("wake_recording", "a wake-word recording")):
        getattr(sat, name).set()
        assert sat._music_hold_reason() == what
        getattr(sat, name).clear()
    assert sat._music_hold_reason() is None


def _music_sat(monkeypatch):
    sat = _bare_sat()
    sat.cfg = types.SimpleNamespace(music_prime_sec=0.0, music_alsa_device=DEVICE)
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat._music_lock = threading.Lock()
    sat._music_proc = None
    sat._music_url = None
    sat._leds = Ring()
    sat._core_features = frozenset()
    sat._emit_text = lambda payload: True
    sat.MUSIC_CONNECT_GRACE_SEC = 0.0
    sat.MUSIC_HOLD_POLL_SEC = 0.01
    sat.procs = []

    def popen(argv, **kw):
        p = FakeMusic(argv, "plays")
        sat.procs.append(p)
        return p

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    return sat


def _start(sat) -> threading.Thread:
    t = threading.Thread(target=sat._start_music_when_idle, args=(URL,), daemon=True)
    t.start()
    return t


def test_a_held_start_plays_once_the_turn_is_over(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=client.log.name)
    sat = _music_sat(monkeypatch)
    sat._turn_open = True
    t = _start(sat)
    time.sleep(0.15)
    assert sat.procs == [] and t.is_alive()
    sat._turn_open = False
    t.join(3)
    assert len(sat.procs) == 1 and sat._is_music_playing()
    assert "music: holding %s until the turn is over (a turn is in progress)" % URL in caplog.text
    assert "music: the turn is over; starting %s" % URL in caplog.text
    sat._stop_music()


def test_a_held_start_is_dropped_by_a_stop(monkeypatch):
    """A music_stop, a wake word, a barge-in and a reply's response_start
    all stop the music (`_stop_music`); a start held through one of them
    spawns nothing."""
    sat = _music_sat(monkeypatch)
    sat._turn_open = True
    t = _start(sat)
    time.sleep(0.05)
    sat._stop_music()
    sat._turn_open = False
    t.join(3)
    assert not t.is_alive() and sat.procs == []


def test_a_long_turn_does_not_use_up_the_wait_for_the_speaker(monkeypatch):
    """The speaker wait (`MUSIC_SPEAKER_WAIT_SEC`) counts only once no turn
    holds the start: a turn longer than it — a 30 s capture, a long answer
    — is waited out in full."""
    sat = _music_sat(monkeypatch)
    sat.MUSIC_SPEAKER_WAIT_SEC = 0.1
    sat._turn_open = True
    sat._playback_idle.clear()
    t = _start(sat)
    time.sleep(0.3)
    sat._turn_open = False
    time.sleep(0.05)
    sat._playback_idle.set()
    t.join(3)
    assert len(sat.procs) == 1
    sat._stop_music()


def test_a_start_that_cannot_get_the_speaker_still_gives_up(monkeypatch, caplog):
    sat = _music_sat(monkeypatch)
    sat.MUSIC_SPEAKER_WAIT_SEC = 0.1
    sat._playback_idle.clear()
    t = _start(sat)
    t.join(3)
    assert not t.is_alive() and sat.procs == []
    assert "didn't release output stream" in caplog.text


def test_a_turn_that_opens_at_the_last_moment_holds_the_start(monkeypatch):
    """Checked again under the music lock right before the spawn: a turn
    that opened since the first check is waited for."""
    sat = _music_sat(monkeypatch)
    real = client.Satellite._music_hold_reason
    calls = {"n": 0}

    def reason(self):
        calls["n"] += 1
        if calls["n"] == 2:              # the check under the lock
            self._turn_open = True
        return real(self)

    monkeypatch.setattr(client.Satellite, "_music_hold_reason", reason)
    t = _start(sat)
    time.sleep(0.1)
    assert sat.procs == [] and t.is_alive()
    sat._turn_open = False
    t.join(3)
    assert len(sat.procs) == 1
    sat._stop_music()


def test_a_retry_never_respawns_into_a_turn(monkeypatch, caplog):
    """Belt and braces for the cast fix's retries: a refused stream whose
    retry comes due while a turn is open (which every turn's own stop
    prevents) stops instead of respawning."""
    sat = _music_sat(monkeypatch)
    sat.MUSIC_RETRY_BACKOFF_SEC = (0.05,)
    sat.MUSIC_RETRY_WINDOW_SEC = 1.0
    sat.MUSIC_CONNECT_GRACE_SEC = 0.5

    def popen(argv, **kw):
        p = FakeMusic(argv, "refused")
        sat.procs.append(p)
        return p

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    real_delay = client.Satellite._music_retry_delay

    def delay(self, attempts):
        self._turn_open = True           # a turn opened during the backoff
        return real_delay(self, attempts)

    monkeypatch.setattr(client.Satellite, "_music_retry_delay", delay)
    _start(sat).join(5)
    assert len(sat.procs) == 1
    assert "not retrying" in caplog.text


# ─── playback: the deferred release ──────────────────────────────────────


class RecordingOut(FakeOut):
    written: list[tuple[float, int]] = []

    def write(self, chunk: bytes) -> None:
        RecordingOut.written.append((time.perf_counter(), len(chunk)))
        time.sleep(len(chunk) / (self.rate * 2) / 20)


class RefusingOut:
    def __init__(self, *a, **k) -> None:
        raise OSError("device busy")


def _playback_sat(monkeypatch, out_cls):
    monkeypatch.setattr(client, "sd", types.SimpleNamespace(RawOutputStream=out_cls), raising=False)
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(
        device=types.SimpleNamespace(playback_sample_rate=None),
        tts_playback_gain=1.0, output_device=None,
    )
    for name in ("shutdown_event", "playback_active", "stop_playback", "dropin_active"):
        setattr(sat, name, threading.Event())
    sat.response_done = StampedEvent()
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat.playback_q = queue.Queue()
    sat._leds = Ring()
    sat._post_playback_state = None
    return sat


def test_a_reply_ended_before_its_first_chunk_is_released_after_it_plays(monkeypatch):
    """response_end handled before the playback thread took the reply's
    first chunk (a one-sentence reply in one burst, or the prebuffer it
    flushed): the release it deferred until the drain used to fire as that
    first chunk opened the stream — the follow-up capture opened over the
    question."""
    RecordingOut.written = []
    sat = _playback_sat(monkeypatch, RecordingOut)
    chunk = b"\x01\x00" * 1600                      # 0.1 s at 16 kHz
    for _ in range(5):
        sat.playback_q.put((16000, chunk))
    sat._post_playback_state = "listening"          # response_end: deferred
    t = threading.Thread(target=sat._playback_thread_run, daemon=True)
    t.start()
    try:
        assert sat.response_done.wait(3)
    finally:
        sat.shutdown_event.set()
        t.join(3)
    assert sum(n for _, n in RecordingOut.written) == 5 * len(chunk)
    assert sat.response_done.sets[0] >= RecordingOut.written[-1][0]
    assert sat._leds.state == "listening"


def test_a_reply_the_device_refused_is_still_released(monkeypatch):
    """No stream could be opened, so there is no drain to wait for: the turn
    is released once the queue is empty instead of waiting forever."""
    sat = _playback_sat(monkeypatch, RefusingOut)
    for _ in range(3):
        sat.playback_q.put((16000, b"\x01\x00" * 1600))
    sat._post_playback_state = "idle"
    t = threading.Thread(target=sat._playback_thread_run, daemon=True)
    t.start()
    try:
        assert sat.response_done.wait(3)
    finally:
        sat.shutdown_event.set()
        t.join(3)
    assert sat.playback_q.empty()


def test_a_reply_the_device_refused_leaves_the_speaker_free(monkeypatch):
    """The reply's response_start marked the speaker busy; no stream ever
    opened, so nothing marked it free again — and every music_start after
    it waited out its speaker wait and was dropped."""
    sat = _playback_sat(monkeypatch, RefusingOut)
    sat._playback_idle.clear()                          # response_start
    for _ in range(3):
        sat.playback_q.put((16000, b"\x01\x00" * 1600))
    sat._post_playback_state = "idle"                   # response_end: deferred
    t = threading.Thread(target=sat._playback_thread_run, daemon=True)
    t.start()
    try:
        assert sat.response_done.wait(3)
        assert sat._playback_idle.wait(1)
    finally:
        sat.shutdown_event.set()
        t.join(3)


def test_the_music_comes_back_after_a_reply_the_device_refused(room_factory):
    """End to end: music playing; wake; the reply's output stream cannot be
    opened (the device still held — by the old player's buffer, say); the
    core's auto-resume after it plays once the turn is over."""
    room = room_factory()
    room.sat.MUSIC_SPEAKER_WAIT_SEC = client.Satellite.MUSIC_SPEAKER_WAIT_SEC / SPEED
    refuse = {"on": False}
    real_out = client.sd.RawOutputStream

    def out(*a, **k):
        if refuse["on"]:
            raise OSError("Device unavailable")
        return real_out(*a, **k)

    client.sd.RawOutputStream = out
    room.start()
    play_music(room)
    refuse["on"] = True
    room.on_end[1] = reply(followup=False, music_after_end=True)
    room.say(WAKE_AND_COMMAND)
    room.wait_until(lambda: len(room.ends()) == 1, 30)
    room.wait_until(lambda: len(room.music) == 2, 10)
    room.stop()
    assert room.music[1].spawned >= room.captures[0]["end"]


# ─── stopping the player and what it forked ──────────────────────────────


class _Proc:
    def __init__(self, pid=None) -> None:
        if pid is not None:
            self.pid = pid
        self.calls: list[str] = []

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")


def test_the_player_is_stopped_as_a_process_group(monkeypatch):
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(client.os, "killpg", lambda pid, sig: sent.append((pid, sig)), raising=False)
    proc = _Proc(pid=4242)
    client._signal_player(proc)
    assert sent == [(4242, client.signal.SIGTERM)]
    assert proc.calls == []


def test_without_a_group_the_player_alone_is_stopped(monkeypatch):
    def no_group(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr(client.os, "killpg", no_group, raising=False)
    proc = _Proc(pid=4242)
    client._signal_player(proc)
    assert proc.calls == ["terminate"]
    stand_in = _Proc()                                   # no pid at all
    client._signal_player(stand_in, kill=True)
    assert stand_in.calls == ["kill"]


def test_stopping_the_music_signals_the_players_whole_group(monkeypatch):
    """`_stop_music` goes through `_signal_player`, not the process alone."""
    sat = _music_sat(monkeypatch)

    def popen(argv, **kw):
        p = FakeMusic(argv, "plays")
        p.pid = 4321 + len(sat.procs)
        sat.procs.append(p)
        return p

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    sent: list[tuple[int, int]] = []

    def killpg(pid, sig):
        sent.append((pid, sig))
        for p in sat.procs:
            if p.pid == pid:
                p.terminate()

    monkeypatch.setattr(client.os, "killpg", killpg, raising=False)
    sat._start_music(URL)
    assert sat.procs and sat.procs[0].alive()
    sat._stop_music()
    assert sent == [(4321, client.signal.SIGTERM)]
    assert not sat.procs[0].alive()
