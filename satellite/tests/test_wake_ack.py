"""The wake acknowledgement plays BEFORE the satellite listens.

Office satellite, 2026-09-28 (conversation_log #187): the wake greeting
"Back so soon?" played over the command capture, trusting the XVF3800's
echo cancellation to keep it out of the mic. Some of it got through, the
capture ended on the pause after it, and the core answered Domovoi's own
words. Kamron's decision (2026-09-29): acknowledge first, then listen —
per satellite, `[wake] ack_mode`:

* "greeting" (the default): the spoken clip plays to its end (a hard cap
  kills a player that doesn't finish), then everything the mic heard under
  it plus a short tail is dropped, then the capture starts;
* "chime": the same with a 0.24 s two-note chime made on the Pi;
* "none": the LED alone, listening at once.

Pinned here, through the real `_wait_for_wake` → `_stream_capture` path
with a fake wake model, a fake player and a mic that runs in time (frames
arrive one per read; what the mic hears while the player runs is only ever
there to be drained):

* the sound has finished before the first frame of the capture is sent,
  and nothing heard under it — nor the tail after it — is in the capture;
* "none" (and a missing player, and music having been playing for
  "greeting") captures the very next frame;
* the chime still plays after music is stopped; the greeting doesn't;
* a player still going at the cap is killed and listening starts anyway;
* what the capture tells the core: `greeting_played` / `greeting_clip` and
  `ack_before_capture`, on `speech_pause` and `utterance_end`, one-shot;
* the config: `ack_mode` derived from `[greeting] enabled` when absent, a
  typo refused, the dashboard's save path writing it (and refusing a value
  this satellite doesn't know rather than crash-looping on it);
* the chime itself: short, small, silent at the end, scaled by the same
  `[playback] gain` the greetings get.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import queue
import subprocess
import threading
import time
import tomllib
import types
import wave
from collections import deque
from pathlib import Path

import numpy as np
import pytest

from satellite import chime, config_writer
from satellite.tests.test_send_queue_lifecycle import FakeVad, client, make_sat, settle

MS = client.FRAME_MS
CLIP = "greet_funny_a995c16f.mp3"   # "Back so soon?", what #187's Pi played
DEVICE = "plughw:CARD=Array,DEV=0"
EXAMPLE = Path(client.__file__).resolve().parent / "config.toml.example"


def _frame(value: int) -> bytes:
    return np.full(client.FRAME_SAMPLES, value, dtype=np.int16).tobytes()


WAKE = _frame(7000)        # the wake word: the fake model fires on it
GREETING = _frame(3000)    # what the mic hears while the acknowledgement plays
TAIL = _frame(2)           # just after the player exits: under the noise gate
USER = _frame(5000)        # the person's command
SILENCE = bytes(client.FRAME_BYTES)
_NAMES = {WAKE: "wake", GREETING: "greeting", TAIL: "tail", USER: "user", SILENCE: "silence"}


class _Mic(queue.Queue):
    """The mic, in time. Frames in ``upcoming`` are captured one per
    blocking read, as the wake and capture loops read them; ``hear`` puts
    frames the mic took in while the mic thread was busy elsewhere (waiting
    on the player) straight into the queue, where a drain finds them. Past
    the end of ``upcoming`` the room is silent."""

    def __init__(self, upcoming: list[bytes], events: list) -> None:
        super().__init__()
        self.upcoming = deque(upcoming)
        self.events = events

    def get(self, block=True, timeout=None):
        if not block:
            return super().get(block=False)
        try:
            frame = super().get(block=False)
        except queue.Empty:
            frame = self.upcoming.popleft() if self.upcoming else SILENCE
        self.events.append(("read", _NAMES[frame]))
        return frame

    def hear(self, frames: list[bytes]) -> None:
        for f in frames:
            super().put(f)


class _Player:
    """mpg123 / aplay stand-in. While it plays, the mic hears it: each of
    its first ``calls`` waits puts ``frames`` GREETING frames into the mic
    (a wait before the last one times out, as a real one does while the
    clip plays), and the player has then exited — or, ``hangs``, keeps
    going until it is killed. ``backlog`` is the mic queue's size at each
    wait: how much the satellite let pile up. ``status`` is its exit status
    when it ends on its own (a real one exits 1 when, say, the ALSA device
    is busy)."""

    def __init__(self, argv, *, mic: _Mic, events: list, frames: int, hangs: bool,
                 calls: int = 1, status: int = 0) -> None:
        self.argv, self.mic, self.events = list(argv), mic, events
        self.frames, self.hangs, self.calls = frames, hangs, calls
        self.status = status
        self.returncode = None
        self.killed = False
        self.backlog: list[int] = []
        events.append(("spawn", self.argv[0]))

    def wait(self, timeout=None):
        self.backlog.append(self.mic.qsize())
        if self.calls > 0 and not self.killed:
            self.calls -= 1
            self.mic.hear([GREETING] * self.frames)
            if self.calls > 0:
                raise subprocess.TimeoutExpired(self.argv, timeout)
        if self.hangs and not self.killed:
            time.sleep(timeout or 0)
            raise subprocess.TimeoutExpired(self.argv, timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else self.status
            self.events.append(("exited", self.argv[0]))
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.events.append(("killed", self.argv[0]))

    terminate = kill


class _Music:
    """A music mpg123 that is playing until it is stopped."""

    def __init__(self, events: list) -> None:
        self.events = events
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self) -> None:
        self.stopped = True
        self.events.append(("music-stopped", "mpg123"))

    def wait(self, timeout=None):
        return 0

    kill = terminate


class _Oww:
    def reset(self) -> None:
        pass

    def predict(self, chunk):
        return {"hey_jarvis": 1.0 if int(chunk.min()) == 7000 else 0.0}


def _sat(
    monkeypatch,
    mode: str,
    upcoming: list[bytes],
    *,
    frames: int = 40,
    hangs: bool = False,
    music: bool = False,
    gain: float = 1.0,
    features: frozenset[str] = frozenset(),
    missing: bool = False,
    calls: int = 1,
    status: int = 0,
):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))
    loop = asyncio.new_event_loop()
    sat = make_sat(loop=loop)
    sat.send_q = asyncio.Queue()
    events: list = []
    sat.events = events
    sat.raw_q = _Mic(upcoming, events)
    sat.cfg.wake_ack_mode = mode
    sat.cfg.tts_playback_gain = gain
    sat.cfg.music_alsa_device = DEVICE
    sat.cfg.greeting_funny_chance = 0.2
    sat.cfg.noise_gate_recalibrate_interval_sec = 3600.0
    sat._wake_word, sat._wake_threshold = "hey_jarvis", 0.5
    sat._ack_lock = threading.Lock()
    sat._ack_proc = None
    sat._greeting_clip_name = None
    sat._ack_before_capture = False
    sat._music_lock = threading.Lock()
    sat._music_proc = _Music(events) if music else None
    sat._music_url = None
    sat._core_features = features
    clip = client.SOUNDS_CACHE_DIR / "greetings" / CLIP
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"ID3")
    sat.players = []

    def popen(argv, **kwargs):
        if missing:
            raise FileNotFoundError(argv[0])
        player = _Player(
            argv, mic=sat.raw_q, events=events, frames=frames, hangs=hangs, calls=calls,
            status=status,
        )
        sat.players.append(player)
        return player

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    real_emit = client.Satellite._emit_audio.__get__(sat)

    def emit(frame: bytes) -> bool:
        events.append(("sent", _NAMES[frame]))
        return real_emit(frame)

    sat._emit_audio = emit
    return sat, loop


def _wake_then_capture(sat, loop, *, follow_up: bool = False):
    """Wake, open the capture on the core, capture — the mic thread's own
    order. ``follow_up`` runs a second capture after it with no wake (the
    bot asked a question)."""
    try:
        assert sat._wait_for_wake(_Oww()) is True
        sat._begin_utterance("wake_word")
        assert sat._stream_capture([]) is True
        if follow_up:
            sat._begin_utterance("followup")
            assert sat._stream_capture([], pre_speech_timeout_sec=5.0) is True
        loop.run_until_complete(settle())
        items = []
        while not sat.send_q.empty():
            items.append(sat.send_q.get_nowait())
    finally:
        loop.close()
    audio = [_NAMES[d] for kind, d in items if kind == "bytes"]
    controls = [json.loads(d) for kind, d in items if kind == "text"]
    return audio, controls


def _feed(tail: int = client._ACK_TAIL_FRAMES, user: int = 10) -> list[bytes]:
    """The wake word, the room after the acknowledgement, the command, and
    enough silence (make_sat's 0.3 s silence_timeout) to end it."""
    return [WAKE] * 3 + [TAIL] * tail + [USER] * user + [SILENCE] * 12


SILENCE_LIMIT = int(0.3 * 1000 / MS)   # make_sat's silence_timeout, in frames


# ─── the order: acknowledge, drop what the mic heard, then listen ─────────


@pytest.mark.parametrize("mode,player", [("greeting", "mpg123"), ("chime", "aplay")])
def test_the_sound_finishes_before_the_capture_starts(monkeypatch, mode, player):
    sat, loop = _sat(monkeypatch, mode, _feed())
    audio, controls = _wake_then_capture(sat, loop)

    # Nothing the mic heard while it played, and none of the tail after it,
    # is in the capture: it is the command and the silence that ended it.
    assert audio == ["user"] * 10 + ["silence"] * SILENCE_LIMIT
    ev = sat.events
    first_sent = ev.index(("sent", "user"))
    assert ev.index(("spawn", player)) < ev.index(("exited", player)) < first_sent
    # The tail is read (and dropped) after the player exits, before the
    # capture's first frame.
    tail_reads = [i for i, e in enumerate(ev) if e == ("read", "tail")]
    assert len(tail_reads) == client._ACK_TAIL_FRAMES
    assert ev.index(("exited", player)) < tail_reads[0] and tail_reads[-1] < first_sent
    # The core hears of the capture only after all of that.
    assert controls[0]["type"] == "utterance_start"
    assert sat._ack_proc is None


def test_what_was_heard_under_the_acknowledgement_is_drained_not_sent(monkeypatch):
    """A person talking over the greeting: those frames reach the mic
    queue while the player runs, and are dropped — by design."""
    sat, loop = _sat(monkeypatch, "greeting", _feed(), frames=70)
    audio, _controls = _wake_then_capture(sat, loop)
    assert "greeting" not in audio
    assert audio.count("user") == 10
    assert sat.raw_q.qsize() == 0


def test_the_mic_is_drained_while_a_long_greeting_plays(monkeypatch, caplog):
    """The mic queue holds ~2 s; a 3 s greeting (the Edge voices' longer
    lines) must not fill it, or the mic callback drops frames and warns
    that wake-word predict is falling behind — false here. Drained at
    every wait, the backlog never grows past one wait's worth."""
    sat, loop = _sat(monkeypatch, "greeting", _feed(), frames=2, calls=50)
    with caplog.at_level(logging.INFO, logger="satellite"):
        audio, _controls = _wake_then_capture(sat, loop)
    (player,) = sat.players
    assert max(player.backlog) <= 2
    assert "dropped 100 mic frames under it" in caplog.text
    assert audio == ["user"] * 10 + ["silence"] * SILENCE_LIMIT


def test_none_captures_the_very_next_frame(monkeypatch):
    sat, loop = _sat(monkeypatch, "none", _feed())
    audio, controls = _wake_then_capture(sat, loop)

    assert sat.players == []
    # Nothing dropped: the frames right after the wake word open the capture.
    assert audio[: client._ACK_TAIL_FRAMES] == ["tail"] * client._ACK_TAIL_FRAMES
    assert audio.count("user") == 10
    end = controls[-1]
    assert end["greeting_played"] is False
    assert "ack_before_capture" not in end and "greeting_clip" not in end


def test_a_missing_player_listens_at_once(monkeypatch, caplog):
    sat, loop = _sat(monkeypatch, "greeting", _feed(), missing=True)
    with caplog.at_level(logging.WARNING, logger="satellite"):
        audio, controls = _wake_then_capture(sat, loop)
    assert audio[0] == "tail"
    assert controls[-1]["greeting_played"] is False
    assert "ack_before_capture" not in controls[-1]
    assert "could not run mpg123" in caplog.text


# ─── music was playing ────────────────────────────────────────────────────


def test_music_playing_skips_the_greeting(monkeypatch):
    """The existing rule: a greeting on top of just-stopped music is an
    abrupt interruption, so the LED alone says "listening"."""
    sat, loop = _sat(monkeypatch, "greeting", _feed(), music=True)
    audio, controls = _wake_then_capture(sat, loop)

    assert ("music-stopped", "mpg123") in sat.events
    assert sat.players == []
    assert audio[0] == "tail"          # listening at once
    assert controls[-1]["greeting_played"] is False
    assert "ack_before_capture" not in controls[-1]


def test_music_playing_still_gets_the_chime(monkeypatch):
    """The chime is the short "stopped the music to listen" cue, and it is
    over before anyone has started talking — it plays after the music has
    been stopped, never over it."""
    sat, loop = _sat(monkeypatch, "chime", _feed(), music=True)
    audio, controls = _wake_then_capture(sat, loop)

    ev = sat.events
    assert ev.index(("music-stopped", "mpg123")) < ev.index(("spawn", "aplay"))
    assert audio == ["user"] * 10 + ["silence"] * SILENCE_LIMIT
    assert controls[-1]["ack_before_capture"] is True


# ─── the cap ──────────────────────────────────────────────────────────────


def test_a_player_still_going_at_the_cap_is_killed(monkeypatch, caplog):
    monkeypatch.setattr(client, "_ACK_GREETING_CAP_SEC", 0.2)
    sat, loop = _sat(monkeypatch, "greeting", _feed(), hangs=True)
    t0 = time.monotonic()
    with caplog.at_level(logging.INFO, logger="satellite"):
        audio, controls = _wake_then_capture(sat, loop)

    (player,) = sat.players
    assert player.killed
    assert ("killed", "mpg123") in sat.events
    assert time.monotonic() - t0 < 5.0
    # Listening started anyway, clean.
    assert audio == ["user"] * 10 + ["silence"] * SILENCE_LIMIT
    assert controls[-1]["greeting_clip"] == CLIP
    assert controls[-1]["ack_before_capture"] is True
    assert "cut at the 0.2 s cap" in caplog.text


def test_the_ack_timing_is_logged(monkeypatch, caplog):
    sat, loop = _sat(monkeypatch, "greeting", _feed(), frames=25)
    with caplog.at_level(logging.INFO, logger="satellite"):
        _wake_then_capture(sat, loop)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("wake ack:"))
    assert CLIP in line
    assert "dropped 25 mic frames under it + 7 tail" in line
    assert "ms after the wake word" in line


def test_another_thread_can_cut_the_acknowledgement_short(monkeypatch):
    """A drop-in, chat or wake recording that starts stops the player
    (`_stop_ack`), and the mic thread waiting on it carries on."""
    monkeypatch.setattr(client, "_ACK_GREETING_CAP_SEC", 30.0)
    sat, loop = _sat(monkeypatch, "greeting", _feed(), hangs=True)
    stopper = threading.Timer(0.2, sat._stop_ack)
    stopper.start()
    t0 = time.monotonic()
    try:
        audio, _controls = _wake_then_capture(sat, loop)
    finally:
        stopper.cancel()
    assert time.monotonic() - t0 < 5.0
    assert sat.players[0].killed
    assert audio.count("user") == 10


def test_a_mode_that_starts_before_the_player_is_registered_still_stops_it(monkeypatch):
    """The receiver starting a drop-in (or chat, or a wake recording) calls
    `_stop_ack`, which cannot see a player the mic thread has spawned but
    not yet registered. The wait itself watches for the mode too: the
    player is killed at once, no tail is waited out, and the wake reports
    nothing — its capture never happens (the mic thread's outer loop
    switches to the mode)."""
    monkeypatch.setattr(client, "_ACK_GREETING_CAP_SEC", 30.0)
    sat, loop = _sat(monkeypatch, "greeting", _feed(), hangs=True)
    spawn = client.subprocess.Popen

    def popen_then_dropin(argv, **kwargs):
        player = spawn(argv, **kwargs)
        sat.dropin_active.set()          # lands before `_ack_proc = proc`
        return player

    monkeypatch.setattr(client.subprocess, "Popen", popen_then_dropin)
    t0 = time.monotonic()
    try:
        assert sat._wait_for_wake(_Oww()) is True
    finally:
        loop.close()
    assert time.monotonic() - t0 < 5.0
    assert sat.players[0].killed
    assert ("read", "tail") not in sat.events
    assert (sat._greeting_played_this_turn, sat._greeting_clip_name,
            sat._ack_before_capture) == (False, None, False)
    assert sat._ack_proc is None


def test_a_player_that_fails_on_its_own_is_logged_and_listening_goes_on(
    monkeypatch, caplog,
):
    """mpg123 exiting 1 at once (a busy ALSA device) used to look like a
    greeting that "played 20 ms". It says so now, and the wake carries on:
    drained, the tail dropped, the capture opened."""
    sat, loop = _sat(monkeypatch, "greeting", _feed(), frames=0, status=1)
    with caplog.at_level(logging.INFO, logger="satellite"):
        audio, controls = _wake_then_capture(sat, loop)
    assert "wake ack: mpg123 exited with status 1" in caplog.text
    assert DEVICE in caplog.text
    assert audio == ["user"] * 10 + ["silence"] * SILENCE_LIMIT
    assert controls[-1]["ack_before_capture"] is True


def test_a_killed_player_is_not_reported_as_a_failure(monkeypatch, caplog):
    monkeypatch.setattr(client, "_ACK_GREETING_CAP_SEC", 0.2)
    sat, loop = _sat(monkeypatch, "greeting", _feed(), hangs=True)
    with caplog.at_level(logging.INFO, logger="satellite"):
        _wake_then_capture(sat, loop)
    assert "exited with status" not in caplog.text


def test_an_error_in_the_acknowledgement_never_costs_the_capture(monkeypatch, caplog):
    """Whatever breaks inside the acknowledgement (here the greeting bank's
    directory), the wake still gets its capture: an exception left to
    propagate would end the mic thread, and the satellite would stay online
    without ever hearing another word."""
    sat, loop = _sat(monkeypatch, "greeting", _feed())

    def broken_bank():
        raise OSError(5, "Input/output error")

    sat._pick_greeting = broken_bank
    with caplog.at_level(logging.ERROR, logger="satellite"):
        audio, controls = _wake_then_capture(sat, loop)
    assert "wake ack failed; listening without it" in caplog.text
    assert audio[0] == "tail"            # listening at once, as "none" does
    assert audio.count("user") == 10
    assert controls[-1]["greeting_played"] is False
    assert "ack_before_capture" not in controls[-1]


# ─── what the capture tells the core ──────────────────────────────────────


def test_a_greeting_turn_reports_the_clip_and_that_it_came_first(monkeypatch):
    sat, loop = _sat(
        monkeypatch, "greeting", _feed(), features=frozenset({"speech_pause"}),
    )
    _audio, controls = _wake_then_capture(sat, loop)

    start, pause, end = controls
    assert start == {"type": "utterance_start", "trigger": "wake_word", "utt": 1}
    fields = {"greeting_played": True, "greeting_clip": CLIP, "ack_before_capture": True}
    assert {k: pause[k] for k in fields} == fields
    assert pause["type"] == "speech_pause" and pause["last_voiced_frame"] == 9
    assert end == {
        "type": "utterance_end", **fields,
        "utt": 1, "frames": 10 + SILENCE_LIMIT, "last_voiced_frame": 9,
        "exit_reason": "vad_silence_after_speech", "voiced_frames": 10,
        "trailing_silent_frames": SILENCE_LIMIT, "silence_limit_frames": SILENCE_LIMIT,
    }


def test_a_chime_turn_reports_no_greeting(monkeypatch):
    sat, loop = _sat(monkeypatch, "chime", _feed())
    _audio, controls = _wake_then_capture(sat, loop)
    end = controls[-1]
    assert end["greeting_played"] is False and "greeting_clip" not in end
    assert end["ack_before_capture"] is True


def test_the_report_is_one_shot(monkeypatch):
    """The follow-up capture after a greeting turn had no acknowledgement
    of its own, and says nothing about one."""
    feed = _feed() + [USER] * 5 + [SILENCE] * 12
    sat, loop = _sat(monkeypatch, "greeting", feed)
    _audio, controls = _wake_then_capture(sat, loop, follow_up=True)

    ends = [c for c in controls if c["type"] == "utterance_end"]
    assert [e.get("ack_before_capture") for e in ends] == [True, None]
    assert [e["greeting_played"] for e in ends] == [True, False]
    assert "greeting_clip" not in ends[1]
    assert (sat._greeting_played_this_turn, sat._greeting_clip_name,
            sat._ack_before_capture) == (False, None, False)


def test_the_core_can_still_end_an_acknowledged_capture_early(monkeypatch):
    """"Pause the music", then the core's end_capture: the capture stops
    there and its utterance_end still carries the acknowledgement."""
    sat, loop = _sat(
        monkeypatch, "greeting", _feed(), features=frozenset({"speech_pause"}),
    )
    emit = sat._emit_audio

    def emit_then_commit(frame: bytes) -> bool:
        ok = emit(frame)
        if sum(1 for e in sat.events if e[0] == "sent") == 14:
            sat._end_capture.set()
        return ok

    sat._emit_audio = emit_then_commit
    audio, controls = _wake_then_capture(sat, loop)
    assert len(audio) == 14
    end = controls[-1]
    assert end["exit_reason"] == "server_endpoint"
    assert (end["greeting_clip"], end["ack_before_capture"]) == (CLIP, True)


# ─── the players, their arguments and the chime ───────────────────────────


def test_the_greeting_plays_through_mpg123_with_the_voice_gain(monkeypatch):
    sat, loop = _sat(monkeypatch, "greeting", _feed(), gain=2.0)
    _wake_then_capture(sat, loop)
    (player,) = sat.players
    assert player.argv == [
        "mpg123", "-q", "-f", "65536", "-o", "alsa", "-a", DEVICE,
        str(client.SOUNDS_CACHE_DIR / "greetings" / CLIP),
    ]


def test_the_chime_plays_through_aplay_with_the_gain_in_its_samples(monkeypatch):
    sat, loop = _sat(monkeypatch, "chime", _feed(), gain=2.0)
    _wake_then_capture(sat, loop)
    (player,) = sat.players
    path = client.SOUNDS_CACHE_DIR / client.CHIME_FILE
    assert player.argv == ["aplay", "-q", "-D", DEVICE, str(path)]
    assert path.read_bytes() == chime.render_wav(2.0)


def test_the_chime_is_short_small_and_ends_in_silence():
    data = chime.render_wav(1.0)
    assert len(data) < 20_000
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22_050)
        assert 0.15 <= w.getnframes() / w.getframerate() <= 0.25
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    assert pcm[0] == 0 and pcm[-1] == 0
    # Under full scale at unity gain, with room for a make-up gain: -8 dBFS.
    assert 0.35 < np.abs(pcm).max() / 32767 < 0.45


def test_the_chime_gain_scales_and_clips_like_the_greetings():
    one = chime.render_pcm(1.0).astype(np.int32)
    two = chime.render_pcm(2.0).astype(np.int32)
    assert np.abs(two - 2 * one).max() <= 2
    loud = chime.render_pcm(10.0)
    assert loud.max() == 32767 and loud.min() == -32768   # clipped, not wrapped


def test_ensure_rewrites_only_when_the_chime_changes(tmp_path):
    path = tmp_path / "sounds" / "chime.wav"
    chime.ensure(path, 1.0)
    first = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    time.sleep(0.01)
    chime.ensure(path, 1.0)
    assert path.stat().st_mtime_ns == stamp
    chime.ensure(path, 3.0)
    assert path.read_bytes() == chime.render_wav(3.0) != first
    assert not path.with_name("chime.wav.tmp").exists()


# ─── configuration ────────────────────────────────────────────────────────


def _cfg(body: str):
    return client.Config.from_toml(tomllib.loads(body))


@pytest.mark.parametrize(
    "body,mode",
    [
        # Before [wake] ack_mode existed: [greeting] enabled decides, so a
        # satellite keeps what it did — except that the greeting now plays
        # before the capture rather than over it.
        ("", "greeting"),
        ("[greeting]\nenabled = true\nreply_wait = 2.5\n", "greeting"),
        ("[greeting]\nenabled = false\n", "none"),
        # Once set, ack_mode is the answer, whatever the greeting switch says.
        ('[wake]\nack_mode = "chime"\n[greeting]\nenabled = false\n', "chime"),
        ('[wake]\nack_mode = "greeting"\n[greeting]\nenabled = false\n', "greeting"),
        ('[wake]\nack_mode = "none"\n[greeting]\nenabled = true\n', "none"),
        # Blank is the same as absent.
        ('[wake]\nack_mode = ""\n[greeting]\nenabled = false\n', "none"),
    ],
    ids=["absent", "legacy-on", "legacy-off", "chime", "greeting-wins", "none-wins", "blank"],
)
def test_ack_mode_is_derived_from_the_greeting_switch_until_set(body, mode):
    assert _cfg(body).wake_ack_mode == mode


def test_an_unknown_ack_mode_fails_loud():
    with pytest.raises(ValueError, match=r"\[wake\] ack_mode 'beep'"):
        _cfg('[wake]\nack_mode = "beep"\n')


def test_the_example_config_greets_and_the_writer_can_set_ack_mode():
    """A fresh satellite from the example greets; the dashboard's edit
    lands on the documented (commented) line in [wake], uncommented,
    instead of a second line appended somewhere."""
    example = EXAMPLE.read_text(encoding="utf-8")
    assert _cfg(example).wake_ack_mode == "greeting"
    for mode in ("chime", "none", "greeting"):
        merged = config_writer.apply_changes(example, {"wake.ack_mode": mode})
        assert _cfg(merged).wake_ack_mode == mode
        assert tomllib.loads(merged)["wake"]["ack_mode"] == mode
        assert merged.count("ack_mode =") == example.count("ack_mode =") == 1


def test_the_report_carries_ack_mode_and_not_the_retired_greeting_keys():
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg('[wake]\nack_mode = "chime"\n')
    report = sat._config_report()
    assert report["wake.ack_mode"] == "chime"
    assert "greeting.funny_chance" in report
    assert "greeting.enabled" not in report and "greeting.reply_wait" not in report


def _saving_sat(monkeypatch):
    sat = object.__new__(client.Satellite)
    restarts: list[bool] = []
    sat._restart_service = lambda: restarts.append(True) or True
    client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.CONFIG_PATH.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return sat, restarts


def test_a_dashboard_save_writes_ack_mode_and_restarts(monkeypatch):
    sat, restarts = _saving_sat(monkeypatch)
    original = client.CONFIG_PATH.read_text(encoding="utf-8")
    sat._apply_config_changes({"wake.ack_mode": "chime"})

    saved = client.CONFIG_PATH.read_text(encoding="utf-8")
    assert tomllib.loads(saved)["wake"]["ack_mode"] == "chime"
    assert client.Config.load(client.CONFIG_PATH).wake_ack_mode == "chime"
    assert client.CONFIG_PATH.with_name("config.toml.bak").read_text(encoding="utf-8") == original
    assert restarts == [True]


def test_a_value_this_satellite_does_not_know_is_refused_before_writing(monkeypatch, caplog):
    """A newer server could offer a mode this code lacks. Written, it would
    fail the next start and crash-loop the service; refused, the satellite
    keeps running as it was."""
    sat, restarts = _saving_sat(monkeypatch)
    original = client.CONFIG_PATH.read_text(encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="satellite"):
        sat._apply_config_changes({"wake.ack_mode": "fanfare"})
    assert client.CONFIG_PATH.read_text(encoding="utf-8") == original
    assert not client.CONFIG_PATH.with_name("config.toml.bak").exists()
    assert restarts == []
    assert "doesn't load" in caplog.text
