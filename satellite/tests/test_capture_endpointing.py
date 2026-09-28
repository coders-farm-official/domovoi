"""What a capture tells the core about its own endpoint.

The core transcribes a copy of the capture at the first pause and uses it
only when the satellite's last voiced frame is inside the copy (early
endpointing, design notes 2026-09-28). So `_stream_capture` reports, when
the core asks for it in `ready.features`:

  * `speech_pause` on the 8th silent frame after speech (once per silence
    run), with the frames sent so far and the last voiced one;
  * `speech_resume` when speech comes back after a reported pause;
  * and, always, `utt` / `frames` / `last_voiced_frame` / `exit_reason` in
    `utterance_end` — new fields on an existing message, which an older
    core ignores.

An older core never lists the feature and never gets a hint: it answers an
unknown message type with `error`, and this client takes `error` as the
end of the turn.

For a satellite too old to report any of it, the core works the last
voiced frame out from the silence timeout; the cross-check at the bottom
runs the real capture loop against the core's formula.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import queue
import random
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


class FakeVad:
    def __init__(self, level: int) -> None:
        pass

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return any(frame)


def make_sat(loop, *, silence_timeout: float = 0.3, max_record_seconds: float = 5.0,
             features: frozenset[str] = frozenset({"speech_pause"})):
    """A Satellite with the state `_stream_capture` touches, connected
    (a live send queue) to a core that lists `features`."""
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(
        vad_aggressiveness=2,
        silence_timeout=silence_timeout,
        max_record_seconds=max_record_seconds,
        noise_gate_dbfs=-40.0,
        noise_gate_auto_calibrate=False,
    )
    sat.loop = loop
    sat.send_q = asyncio.Queue()
    sat._offline_drops = 0
    sat._offline_drop_last_log = 0.0
    sat._leds = Leds()
    sat.raw_q = queue.Queue()
    sat.shutdown_event = threading.Event()
    sat._greeting_played_this_turn = False
    sat._core_features = features
    sat._end_capture = threading.Event()
    sat._capture_lock = threading.Lock()
    return sat


def capture(sat, loop, frames: list[bytes], prefix: list[bytes] | None = None) -> list:
    """Run one capture over `frames`; returns what went to the core, text
    frames decoded, audio frames as the string "audio"."""
    for f in frames:
        sat.raw_q.put(f)
    assert sat._stream_capture(prefix or []) is True
    loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        out.append(json.loads(data) if kind == "text" else "audio")
    return out


def texts(out: list) -> list[dict]:
    return [m for m in out if m != "audio"]


@pytest.fixture(autouse=True)
def _fake_vad(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))


def test_a_pause_is_reported_once_with_the_frames_it_is_about() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._begin_utterance("wake_word")
        # 0.3 s timeout = 10 silent frames end it.
        out = capture(sat, loop, [FRAME] * 5 + [SILENCE] * 12)
    finally:
        loop.close()
    start, pause, end = texts(out)
    assert start == {"type": "utterance_start", "trigger": "wake_word", "utt": 1}
    # The hint sits in the stream right after the 8th silent frame, so the
    # core holds exactly `frame` frames when it reads it.
    assert out.index(pause) == 1 + 5 + 8
    assert pause == {
        "type": "speech_pause", "utt": 1, "frame": 13,
        "last_voiced_frame": 4, "greeting_played": False,
    }
    assert end == {
        "type": "utterance_end", "greeting_played": False, "utt": 1,
        "frames": 15, "last_voiced_frame": 4,
        "exit_reason": "vad_silence_after_speech",
    }


def test_speech_after_a_reported_pause_is_reported_too() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._utt_seq = 6
        sat._begin_utterance("followup")
        out = capture(sat, loop, [FRAME] * 3 + [SILENCE] * 9 + [FRAME] * 2 + [SILENCE] * 10)
    finally:
        loop.close()
    kinds = [m["type"] for m in texts(out)]
    assert kinds == ["utterance_start", "speech_pause", "speech_resume", "speech_pause", "utterance_end"]
    _, p1, resume, p2, end = texts(out)
    assert (p1["frame"], p1["last_voiced_frame"]) == (11, 2)
    assert resume == {"type": "speech_resume", "utt": 7, "frame": 13}
    assert (p2["frame"], p2["last_voiced_frame"]) == (22, 13)
    assert (end["utt"], end["frames"], end["last_voiced_frame"]) == (7, 24, 13)


def test_a_silence_run_shorter_than_the_hint_says_nothing() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._begin_utterance("wake_word")
        out = capture(sat, loop, [FRAME] * 3 + [SILENCE] * 7 + [FRAME] * 3 + [SILENCE] * 10)
    finally:
        loop.close()
    assert [m["type"] for m in texts(out)] == [
        "utterance_start", "speech_pause", "utterance_end",
    ]


def test_an_older_core_never_gets_a_hint_but_the_end_still_counts() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, features=frozenset())
        sat._begin_utterance("wake_word")
        out = capture(sat, loop, [FRAME] * 5 + [SILENCE] * 9 + [FRAME] + [SILENCE] * 10)
    finally:
        loop.close()
    start, end = texts(out)
    assert start["type"] == "utterance_start" and end["type"] == "utterance_end"
    # Extra fields on a message the old core already knows: harmless.
    assert (end["frames"], end["last_voiced_frame"]) == (25, 14)


def test_a_barge_in_prefix_counts_as_speech() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._begin_utterance("barge_in")
        out = capture(sat, loop, [SILENCE] * 10, prefix=[FRAME] * 4)
    finally:
        loop.close()
    _, pause, end = texts(out)
    assert (pause["frame"], pause["last_voiced_frame"]) == (12, 3)
    assert (end["frames"], end["last_voiced_frame"]) == (14, 3)


def test_the_capture_number_goes_up_and_the_ready_frame_sets_the_features(monkeypatch) -> None:
    sat = object.__new__(client.Satellite)
    sat.loop = None
    sat.send_q = None
    assert sat._core_features == frozenset(), "nothing new is sent before a ready"
    sat._begin_utterance("wake_word")
    sat._begin_utterance("followup")
    assert sat._utt_seq == 2

    monkeypatch.setattr(client, "_promote_pending_server", lambda path: None)
    sat._upgrade_confirmed = False
    sat._sync_time_with_server = lambda: None
    sat._note_session_accepted = lambda: None
    sat._handle_text_frame({"type": "ready", "features": ["speech_pause", 7, "later"]})
    assert sat._core_features == frozenset({"speech_pause", "later"})
    # An older core's ready: no list, nothing new.
    sat._handle_text_frame({"type": "ready", "protocol_version": "0.1"})
    assert sat._core_features == frozenset()


def test_the_hello_offers_hints_and_a_new_session_forgets_the_features() -> None:
    src = inspect.getsource(client.Satellite._run_session)
    hello = src[src.index('"type": "hello"'):src.index("}))", src.index('"type": "hello"'))]
    assert '"speech_pause": True' in hello
    src = inspect.getsource(client.Satellite._on_session_ended)
    assert "self._core_features = frozenset()" in src


def test_the_cores_formula_for_an_old_satellite_matches_the_real_capture_loop() -> None:
    """For a satellite that doesn't report its last voiced frame the core
    works it out from the silence timeout (`endpointing.
    last_voiced_from_timeout`). Run the real loop over random speech and
    compare with what it reports — frame for frame."""
    from domovoi.endpointing import last_voiced_from_timeout

    rng = random.Random(20260928)
    for timeout in (0.3, 0.8, 1.2):
        for _ in range(40):
            frames = []
            for _ in range(rng.randint(1, 4)):
                frames += [FRAME] * rng.randint(1, 20)
                frames += [SILENCE] * rng.randint(0, int(timeout * 1000 / 30) - 1)
            frames += [SILENCE] * 60
            loop = asyncio.new_event_loop()
            try:
                sat = make_sat(loop, silence_timeout=timeout, max_record_seconds=30.0,
                               features=frozenset())
                end = texts(capture(sat, loop, frames))[-1]
            finally:
                loop.close()
            assert end["exit_reason"] == "vad_silence_after_speech"
            assert last_voiced_from_timeout(end["frames"], timeout, 30.0) == end["last_voiced_frame"]


def test_a_capped_capture_is_one_the_formula_refuses() -> None:
    from domovoi.endpointing import last_voiced_from_timeout

    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, max_record_seconds=5.0, features=frozenset())
        end = texts(capture(sat, loop, [FRAME] * 200))[-1]
    finally:
        loop.close()
    assert end["exit_reason"] == "max_record_seconds"
    assert last_voiced_from_timeout(end["frames"], 0.3, 5.0) is None


# ─── the core ending a capture (early commit) ─────────────────────────────


class HookedQueue(queue.Queue):
    """A mic queue that runs `hook` after the n-th frame is taken — the
    moment an `end_capture` "arrives" in these tests."""

    def __init__(self, at: int, hook) -> None:
        super().__init__()
        self.at, self.hook, self.taken = at, hook, 0

    def get(self, *a, **kw):
        item = super().get(*a, **kw)
        self.taken += 1
        if self.taken == self.at:
            self.hook()
        return item


def _run_with_end_capture(sat, loop, frames, *, at: int, utt=None) -> list:
    """Capture `frames`; after the `at`-th, the receiver handles an
    end_capture for `utt` (default: the capture running)."""
    def arrive() -> None:
        sat._handle_text_frame({"type": "end_capture", "utt": sat._capture_utt if utt is None else utt})

    hooked = HookedQueue(at, arrive)
    for f in frames:
        hooked.put(f)
    sat.raw_q = hooked
    return capture(sat, loop, [])


def test_end_capture_for_the_running_capture_ends_it_at_the_next_frame() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, silence_timeout=1.2)
        sat._begin_utterance("wake_word")
        out = _run_with_end_capture(sat, loop, [FRAME] * 20 + [SILENCE] * 60, at=32)
    finally:
        loop.close()
    end = texts(out)[-1]
    assert end["type"] == "utterance_end"
    assert end["exit_reason"] == "server_endpoint"
    assert end["frames"] == 32, "no frame is sent after the end_capture"
    assert end["last_voiced_frame"] == 19
    assert out.count("audio") == 32
    assert sat._capture_utt is None, "between captures nothing is running"
    assert sat._leds.states[-1] == "thinking"


def test_a_stale_end_capture_does_not_end_the_capture_it_does_not_name() -> None:
    """The follow-up capture starts right after the reply; an end_capture
    meant for the previous one, arriving late, must not cut it off."""
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, silence_timeout=0.3)
        sat._begin_utterance("wake_word")
        sat._begin_utterance("followup")         # this capture is utt 2
        out = _run_with_end_capture(sat, loop, [FRAME] * 20 + [SILENCE] * 12, at=5, utt=1)
    finally:
        loop.close()
    end = texts(out)[-1]
    assert end["exit_reason"] == "vad_silence_after_speech" and end["utt"] == 2


def test_an_end_capture_between_captures_does_not_leak_into_the_next() -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, silence_timeout=0.3)
        sat._begin_utterance("wake_word")
        capture(sat, loop, [FRAME] * 5 + [SILENCE] * 10)
        # Late, for the capture that just ended on its own.
        sat._handle_text_frame({"type": "end_capture", "utt": 1})
        assert not sat._end_capture.is_set()
        sat._begin_utterance("followup")
        end = texts(capture(sat, loop, [FRAME] * 5 + [SILENCE] * 10))[-1]
    finally:
        loop.close()
    assert end["exit_reason"] == "vad_silence_after_speech" and end["utt"] == 2


def test_a_capture_the_core_ended_is_never_reported_noisy() -> None:
    """The core is already answering it; a noisy_capture would make it
    cancel that answer for an apology."""
    def loud_room(loop):
        sat = make_sat(loop, silence_timeout=0.3)
        sat.cfg.noise_gate_auto_calibrate = True
        sat.cfg.noise_gate_noisy_capture_dbfs = -20.0     # FRAME is ~-12 dBFS
        sat._maybe_recalibrate = lambda samples: None
        sat._begin_utterance("wake_word")
        return sat

    loop = asyncio.new_event_loop()
    try:
        # Control: the same loud capture, ended by its own silence, is noisy.
        sat = loud_room(loop)
        out = capture(sat, loop, [FRAME] * 40 + [SILENCE] * 10)
        assert texts(out)[-1] == {"type": "noisy_capture"}
        sat = loud_room(loop)
        out = _run_with_end_capture(sat, loop, [FRAME] * 45, at=40)
    finally:
        loop.close()
    assert texts(out)[-1]["type"] == "utterance_end"
    assert texts(out)[-1]["exit_reason"] == "server_endpoint"


def test_the_hello_declares_capture_control_from_the_rooms_setting(tmp_path) -> None:
    src = inspect.getsource(client.Satellite._run_session)
    hello = src[src.index('"type": "hello"'):src.index("}))", src.index('"type": "hello"'))]
    assert '"capture_control": self.cfg.early_commit' in hello

    example = (client.Path(client.__file__).parent / "config.toml.example").read_text(encoding="utf-8")
    path = tmp_path / "config.toml"
    path.write_text(example, encoding="utf-8")
    cfg = client.Config.load(path)
    assert cfg.early_commit is True
    path.write_text(example.replace("early_commit = true", "early_commit = false"), encoding="utf-8")
    assert client.Config.load(path).early_commit is False
    # Absent (an older config.toml): on.
    path.write_text(example.replace("early_commit = true", ""), encoding="utf-8")
    assert client.Config.load(path).early_commit is True


def test_the_setting_is_reported_and_editable_from_the_dashboard() -> None:
    from domovoi.satellite_config_schema import FIELD_BY_NAME

    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(**{f: None for f in client.Config.__dataclass_fields__})
    sat.cfg.early_commit = False
    assert sat._config_report()["listen.early_commit"] is False
    assert FIELD_BY_NAME["listen.early_commit"].type == "bool"
