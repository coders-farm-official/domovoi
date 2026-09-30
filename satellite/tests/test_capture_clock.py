"""A capture says, by its own clock, how far behind the room it is.

The late speculative starts of 2026-09-30 could only be pinned on the
satellite by reasoning backwards from the core's numbers: nothing the
satellite sent said when it had sent it, or with how much of the person's
speech still queued behind it. Now it does, in fields on the messages it
already sends (an older core ignores them):

* `utterance_start`: `backlog_ms`, the mic audio already captured and not
  yet read when the capture opens (how far behind real time it starts),
  and after a wake word `wake_ms`, the wake word to this message;
* `speech_pause`, `speech_resume`, `utterance_end`: `sat_ms`, the time
  since that `utterance_start` on this satellite's monotonic clock, and
  `backlog_ms` again.

A capture that starts behind shows it at once and on every hint sent
from inside the backlog; one that is in step shows 0. Plus the
`[listen] speech_pause_ms` setting that decides when a pause is reported:
default 240 ms, whole frames, a typo or an out-of-range value refused.
"""

from __future__ import annotations

import asyncio
import json
import queue
import tomllib
import types
from collections import deque
from pathlib import Path

import pytest

from satellite import config_writer
from satellite.tests._client_import import import_client
from satellite.tests.test_capture_endpointing import FRAME, SILENCE, FakeVad, make_sat

client = import_client()
MS = client.FRAME_MS
EXAMPLE = Path(client.__file__).resolve().parent / "config.toml.example"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class _Mic(queue.Queue):
    """A mic in real time with a backlog: ``queued`` frames are already
    waiting when the capture opens (captured while the mic thread was busy
    elsewhere); every later read waits for the next ``upcoming`` frame,
    30 ms on the fake clock."""

    def __init__(self, clock: _Clock, queued: list[bytes], upcoming: list[bytes]) -> None:
        super().__init__()
        self.clock = clock
        for f in queued:
            super().put(f)
        self.upcoming = deque(upcoming)

    def get(self, block=True, timeout=None):
        try:
            return super().get(block=False)
        except queue.Empty:
            pass
        if not self.upcoming:
            raise queue.Empty
        self.clock.now += MS / 1000
        return self.upcoming.popleft()


@pytest.fixture(autouse=True)
def _fake_vad(monkeypatch):
    monkeypatch.setattr(client, "webrtcvad", types.SimpleNamespace(Vad=FakeVad))


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    # Only the client's own clock: the event loop keeps the real one.
    monkeypatch.setattr(client, "time", types.SimpleNamespace(monotonic=c.monotonic))
    return c


def _run(sat, loop, trigger: str = "wake_word") -> list[dict]:
    sat._begin_utterance(trigger)
    assert sat._stream_capture([]) is True
    loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not sat.send_q.empty():
        kind, data = sat.send_q.get_nowait()
        if kind == "text":
            out.append(json.loads(data))
    return out


def test_a_capture_in_step_with_the_room_says_so(clock) -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat.raw_q = _Mic(clock, [], [FRAME] * 5 + [SILENCE] * 12)
        sat._woke_at = clock.now - 1.234        # the acknowledgement took 1.234 s
        start, pause, end = _run(sat, loop)
    finally:
        loop.close()
    assert (start["backlog_ms"], start["wake_ms"]) == (0, 1234)
    assert "sat_ms" not in start
    # The pause went out as the 13th frame was read; the end with the 15th.
    assert (pause["sat_ms"], pause["backlog_ms"]) == (13 * MS, 0)
    assert (end["sat_ms"], end["backlog_ms"]) == (15 * MS, 0)


def test_a_capture_that_starts_behind_says_by_how_much(clock) -> None:
    """The wake model's reset used to hold the mic thread ~1.5 s on a Pi
    Zero 2 W: the person's command queued, and a short one's pause went
    out from inside the backlog — `sat_ms` small, `backlog_ms` large."""
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        queued = [FRAME] * 5 + [SILENCE] * 20       # 750 ms said before the capture opened
        sat.raw_q = _Mic(clock, queued, [SILENCE] * 10)
        start, pause, end = _run(sat, loop)
    finally:
        loop.close()
    assert start["backlog_ms"] == 25 * MS
    assert pause["frame"] == 13
    assert (pause["sat_ms"], pause["backlog_ms"]) == (0, (25 - 13) * MS)
    # The end (15 frames) was inside the backlog too.
    assert (end["sat_ms"], end["backlog_ms"]) == (0, (25 - 15) * MS)


def test_a_pause_after_the_backlog_is_back_in_step(clock) -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat.raw_q = _Mic(clock, [FRAME] * 10, [FRAME] * 10 + [SILENCE] * 12)
        start, pause, end = _run(sat, loop)
    finally:
        loop.close()
    assert start["backlog_ms"] == 10 * MS
    # 10 queued + 10 live voiced + 8 silent: 18 frames read in real time.
    assert (pause["frame"], pause["sat_ms"], pause["backlog_ms"]) == (28, 18 * MS, 0)


def test_a_resume_carries_the_clock_and_only_a_wake_says_wake_ms(clock) -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._woke_at = clock.now - 2.0
        sat.raw_q = _Mic(clock, [], [FRAME] * 3 + [SILENCE] * 9 + [FRAME] * 2 + [SILENCE] * 10)
        msgs = _run(sat, loop, trigger="followup")
    finally:
        loop.close()
    start, _p1, resume, _p2, _end = msgs
    assert "wake_ms" not in start
    assert sat._woke_at is None, "a wake time is used once, by the capture after it"
    assert (resume["type"], resume["sat_ms"], resume["backlog_ms"]) == ("speech_resume", 13 * MS, 0)


def test_a_wake_time_is_never_reused_by_a_later_capture(clock) -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop)
        sat._woke_at = clock.now - 1.0
        sat.raw_q = _Mic(clock, [], [FRAME] * 5 + [SILENCE] * 12 + [FRAME] * 5 + [SILENCE] * 12)
        first = _run(sat, loop)[0]
        # A drop-in's command capture, say, whose wake set no time here.
        second = _run(sat, loop)[0]
    finally:
        loop.close()
    assert first["wake_ms"] == 1000
    assert second["type"] == "utterance_start" and "wake_ms" not in second


def test_the_clock_rides_existing_messages_only(clock) -> None:
    """New fields on messages every core already knows — never a new
    message type, which an older core answers with `error`."""
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, features=frozenset())    # an older core
        sat.raw_q = _Mic(clock, [], [FRAME] * 5 + [SILENCE] * 12)
        msgs = _run(sat, loop)
    finally:
        loop.close()
    assert [m["type"] for m in msgs] == ["utterance_start", "utterance_end"]
    assert all(type(v) is int for m in msgs for k, v in m.items() if k in ("sat_ms", "backlog_ms", "wake_ms"))


# ─── [listen] speech_pause_ms ─────────────────────────────────────────────


def _cfg(text: str):
    return client.Config.from_toml(tomllib.loads(text))


def test_the_pause_is_240_ms_unless_the_config_says_otherwise() -> None:
    assert _cfg("").speech_pause_ms == 240 == client.SPEECH_PAUSE_FRAMES * MS
    assert _cfg("[listen]\nspeech_pause_ms = 150\n").speech_pause_ms == 150
    # Whole frames: 160 ms is 5 of them.
    assert _cfg("[listen]\nspeech_pause_ms = 160\n").speech_pause_ms == 150
    assert _cfg("[listen]\nspeech_pause_ms = 90\n").speech_pause_ms == 90
    assert _cfg("[listen]\nspeech_pause_ms = 600\n").speech_pause_ms == 600


@pytest.mark.parametrize("bad", ["30", "700", '"soon"', "true"])
def test_a_pause_setting_out_of_range_fails_loud(bad) -> None:
    with pytest.raises(ValueError, match="speech_pause_ms"):
        _cfg(f"[listen]\nspeech_pause_ms = {bad}\n")


@pytest.mark.parametrize("pause_ms,frames", [(240, 8), (150, 5), (90, 3)])
def test_the_hint_goes_out_on_the_configured_silent_frame(pause_ms, frames) -> None:
    loop = asyncio.new_event_loop()
    try:
        sat = make_sat(loop, silence_timeout=0.8)
        sat.cfg.speech_pause_ms = pause_ms
        for f in [FRAME] * 5 + [SILENCE] * 30:
            sat.raw_q.put(f)
        sat._begin_utterance("wake_word")
        assert sat._stream_capture([]) is True
        loop.run_until_complete(asyncio.sleep(0))
        items = []
        while not sat.send_q.empty():
            items.append(sat.send_q.get_nowait())
    finally:
        loop.close()
    at = next(i for i, (k, d) in enumerate(items) if k == "text" and json.loads(d)["type"] == "speech_pause")
    pause = json.loads(items[at][1])
    # The start, 5 voiced frames and `frames` silent ones, then the hint.
    assert at == 1 + 5 + frames
    assert (pause["frame"], pause["last_voiced_frame"]) == (5 + frames, 4)


def test_a_satellite_config_without_the_setting_keeps_240() -> None:
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace()
    assert sat._speech_pause_frames() == client.SPEECH_PAUSE_FRAMES


def test_the_example_documents_it_and_the_dashboard_can_set_it() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")
    assert _cfg(example).speech_pause_ms == 240
    merged = config_writer.apply_changes(example, {"listen.speech_pause_ms": 150})
    assert _cfg(merged).speech_pause_ms == 150
    assert tomllib.loads(merged)["listen"]["speech_pause_ms"] == 150
    assert merged.count("speech_pause_ms =") == example.count("speech_pause_ms =") == 1
    sat = object.__new__(client.Satellite)
    sat.cfg = _cfg(merged)
    assert sat._config_report()["listen.speech_pause_ms"] == 150
