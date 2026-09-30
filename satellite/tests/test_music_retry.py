"""A refused music stream is retried, and one that never plays is let go.

Office satellite, 2026-09-30 00:16:04 — the first cast after the room's MPD
daemon restarted:

    starting music: http://192.168.0.117:8051 (alsa=plughw:CARD=Array,DEV=0)
    mpg123 exited rc=1; stderr tail:
    [src/resolver.c:timeout_connect():239] error: connection failed: Connection refused
    ...

and then nothing: no retry, no music_ready, the ring stayed on the "music"
rainbow over a silent room until the next wake word. MPD opens its stream
only once it actually plays, the core had left it paused, and mpg123 gives
up on a refused port within a few hundred ms. (The core now opens the
stream before it sends music_start — domovoi/tests/test_music_stream_ready
— and this is the other half, which also covers an older core.)

Pinned here, through `_start_music_when_idle` with a fake mpg123 whose
connection is scripted per spawn:

* refused, refused, connected: three spawns of the same command, ONE
  music_ready, once it has primed on the connection that worked;
* refused for the whole retry window: the ring goes from "music" back to
  idle and the core gets `music_failed` (stream_url, reason, attempts) —
  only when its `ready.features` lists it; an older core gets nothing new;
* not fought: a music_stop, a wake (both `_stop_music`), a newer
  music_start for another stream or the same one — each lands in the
  backoff, and the retrying request then spawns nothing, reports nothing,
  and leaves the ring to whoever set it;
* a failure that is not a connection failure (the ALSA device) is given up
  on at once, not retried;
* a stream that played and then dropped on its own turns the ring off and
  is reported; one stopped on purpose is not;
* the defaults: the retry window outlasts the core's 5 s music_ready
  fallback, because an older core's stream opens only then.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import types

import pytest

from satellite.leds import LEDConfig, LEDController
from satellite.tests._client_import import import_client

client = import_client()

URL = "http://192.168.0.117:8051"
OTHER = "http://192.168.0.117:8052"
DEVICE = "plughw:CARD=Array,DEV=0"
REFUSED = (
    b"[src/resolver.c:timeout_connect():239] error: connection failed: Connection refused\n"
    b"[src/resolver.c:open_connection():326] error: Cannot resolve/connect to 192.168.0.117:8051!\n"
    b"[src/httpget.c:http_open():467] error: Unable to establish connection to 192.168.0.117\n"
)
NO_DATA = (
    b"[src/libmpg123/readers.c:stream_parse_headers():1056] error: "
    b"stream_parse_headers(): no data at all from network resource\n"
)
ALSA = b"[src/libout123/modules/alsa.c:open_alsa():204] error: cannot open device plughw:CARD=Array,DEV=0\n"


LIVE: list["FakeMpg123"] = []


@pytest.fixture(autouse=True)
def _no_fake_outlives_its_test():
    yield
    while LIVE:
        LIVE.pop().terminate()


class FakeMpg123:
    """One mpg123 process. ``plan`` is what its connection does:
    ("refused",) / ("no_data",) / ("alsa",) exit rc=1 after ``after`` s with
    mpg123's own stderr; ("plays",) runs until terminated or `drop()`."""

    def __init__(self, argv, plan, after: float) -> None:
        self.argv = argv
        self.plan = plan
        self.returncode: int | None = None
        self._done = threading.Event()
        self.watched = threading.Event()   # the music-watch thread is done with it
        self._err = b""
        self.stderr = self
        LIVE.append(self)
        if plan[0] != "plays":
            self._err = {"refused": REFUSED, "no_data": NO_DATA, "alsa": ALSA}[plan[0]]
            threading.Timer(after, self._exit, args=(1,)).start()

    def _exit(self, rc: int) -> None:
        if not self._done.is_set():
            self.returncode = rc
            self._done.set()

    # stderr pipe: read() returns at EOF, i.e. when the process is gone.
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

    def drop(self, stderr: bytes = b"", rc: int = 0) -> None:
        """The stream went away under a playing mpg123."""
        self._err = stderr
        self._exit(rc)


class Ring:
    """LEDController's state semantics, recording every change."""

    def __init__(self) -> None:
        self.state = "idle"
        self.log: list[str] = []

    def _go(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.log.append(state)

    def set_state(self, state: str) -> None:
        self._go(state)

    def set_state_unless(self, unless: str, new: str) -> None:
        if self.state != unless:
            self._go(new)

    def set_state_if(self, expected: str, new: str) -> None:
        if self.state == expected:
            self._go(new)


def make_sat(monkeypatch, plans, *, features=("speech_pause", "end_capture", "music_failed"),
             prime=0.05, backoff=(0.02, 0.04), window=0.6, after=0.01):
    """A Satellite with exactly the state the music path touches, and a
    Popen that hands out one scripted mpg123 per spawn (the last plan
    repeats)."""
    sat = object.__new__(client.Satellite)
    sat.cfg = types.SimpleNamespace(music_prime_sec=prime, music_alsa_device=DEVICE)
    sat.shutdown_event = threading.Event()
    sat._playback_idle = threading.Event()
    sat._playback_idle.set()
    sat._music_lock = threading.Lock()
    sat._music_proc = None
    sat._music_url = None
    sat._leds = Ring()
    sat._core_features = frozenset(features)
    sat.MUSIC_RETRY_BACKOFF_SEC = backoff
    sat.MUSIC_RETRY_WINDOW_SEC = window
    sat.frames = []
    sat._emit_text = lambda payload: sat.frames.append(payload) or True
    sat.procs = []
    plans = list(plans)

    def popen(argv, **kwargs):
        plan = plans.pop(0) if len(plans) > 1 else plans[0]
        proc = FakeMpg123(argv, plan, after)
        sat.procs.append(proc)
        return proc

    monkeypatch.setattr(client.subprocess, "Popen", popen)
    real_watch = client.Satellite._music_watch.__get__(sat)

    def watch(run):
        try:
            real_watch(run)
        finally:
            run.proc.watched.set()

    sat._music_watch = watch
    return sat


def start(sat, url: str = URL) -> threading.Thread:
    """What the receiver does on music_start: the music-defer thread."""
    t = threading.Thread(target=sat._start_music_when_idle, args=(url,), daemon=True)
    t.start()
    return t


def wait_for(cond, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        time.sleep(0.005)


def in_backoff(sat, spawns: int):
    """True once `spawns` processes exist and the last one has exited, i.e.
    the supervisor is sleeping before its next try."""
    return lambda: len(sat.procs) == spawns and sat.procs[-1].poll() is not None


def settle(sat) -> None:
    """Let the watcher of every mpg123 that has exited finish with it."""
    for proc in sat.procs:
        if proc.poll() is not None:
            assert proc.watched.wait(2.0)


# ─── retried until it plays ────────────────────────────────────────────────


def test_a_refused_stream_is_retried_and_music_ready_follows_the_connection_that_worked(
    monkeypatch, caplog,
):
    caplog.set_level(logging.INFO, logger=client.log.name)
    sat = make_sat(monkeypatch, [("refused",), ("refused",), ("plays",)])
    start(sat).join(5)

    assert len(sat.procs) == 3
    assert [p.argv for p in sat.procs] == [sat.procs[0].argv] * 3
    assert sat.procs[0].argv[-1] == URL and DEVICE in sat.procs[0].argv
    assert sat.frames == [{"type": "music_ready"}]
    assert sat._music_proc is sat.procs[2] and sat._music_proc.poll() is None
    assert sat._is_music_playing()
    assert sat._leds.log == ["music"]          # set once, never dropped
    assert "retrying music (attempt 2)" in caplog.text
    assert "retrying music (attempt 3)" in caplog.text


def test_accepted_then_closed_before_a_byte_is_retried_too(monkeypatch):
    """A daemon restored paused accepts and closes (mpg123: no data at all
    from network resource) — the same "not up yet" as a refusal."""
    sat = make_sat(monkeypatch, [("no_data",), ("plays",)])
    start(sat).join(5)
    assert len(sat.procs) == 2
    assert sat.frames == [{"type": "music_ready"}]


def test_with_prime_zero_a_refused_stream_is_still_retried_but_nothing_is_sent(monkeypatch):
    sat = make_sat(monkeypatch, [("refused",), ("plays",)], prime=0.0)
    sat.MUSIC_CONNECT_GRACE_SEC = 0.05
    start(sat).join(5)
    assert len(sat.procs) == 2
    assert sat.frames == []                     # handshake off: no music_ready
    assert sat._is_music_playing()


# ─── given up on ───────────────────────────────────────────────────────────


def test_refused_for_the_whole_window_turns_the_ring_off_and_tells_the_core(
    monkeypatch, caplog,
):
    caplog.set_level(logging.INFO, logger=client.log.name)
    sat = make_sat(monkeypatch, [("refused",)], window=0.3)
    started = time.monotonic()
    start(sat).join(5)
    settle(sat)

    assert time.monotonic() - started < 2.0
    assert len(sat.procs) >= 3                  # it did keep trying
    assert sat._leds.state == "idle" and sat._leds.log == ["music", "idle"]
    assert sat._music_proc is None and sat._music_url is None
    assert not sat._is_music_playing()
    assert sat.frames == [{
        "type": "music_failed",
        "stream_url": URL,
        "reason": "connection failed: Connection refused",
        "attempts": len(sat.procs),
    }]
    assert f"music: giving up on {URL} after {len(sat.procs)} attempt(s)" in caplog.text


def test_an_older_core_hears_nothing_new_when_music_is_given_up_on(monkeypatch, caplog):
    """An older core answers an unknown frame with `error`, which the
    satellite takes as the end of a turn. Its `ready` lists no
    music_failed, so the log line and the ring are all there is."""
    caplog.set_level(logging.INFO, logger=client.log.name)
    sat = make_sat(monkeypatch, [("refused",)], features=("speech_pause", "end_capture"),
                   window=0.2)
    start(sat).join(5)
    settle(sat)
    assert sat.frames == []
    assert sat._leds.state == "idle"
    assert "music: giving up on" in caplog.text


def test_a_failure_that_is_not_the_connection_is_given_up_on_at_once(monkeypatch):
    sat = make_sat(monkeypatch, [("alsa",)])
    start(sat).join(5)
    settle(sat)
    assert len(sat.procs) == 1
    assert sat.frames == [{
        "type": "music_failed",
        "stream_url": URL,
        "reason": "cannot open device plughw:CARD=Array,DEV=0",
        "attempts": 1,
    }]
    assert sat._leds.state == "idle"


def test_a_music_start_after_giving_up_spawns_again(monkeypatch):
    sat = make_sat(monkeypatch, [("refused",), ("plays",)], window=0.0)
    start(sat).join(5)
    settle(sat)
    assert len(sat.procs) == 1
    assert sat.frames == [{
        "type": "music_failed", "stream_url": URL,
        "reason": "connection failed: Connection refused", "attempts": 1,
    }]
    sat.frames.clear()
    start(sat).join(5)                          # the next music_start
    assert sat.frames == [{"type": "music_ready"}]
    assert sat._is_music_playing() and sat._leds.state == "music"


# ─── not fought ────────────────────────────────────────────────────────────


def _slow(monkeypatch, plans, **kw):
    kw.setdefault("backoff", (0.3,))
    kw.setdefault("window", 5.0)
    return make_sat(monkeypatch, plans, **kw)


def test_a_music_stop_in_the_backoff_ends_the_retrying(monkeypatch):
    sat = _slow(monkeypatch, [("refused",), ("plays",)])
    t = start(sat)
    wait_for(in_backoff(sat, 1))
    # The receiver's music_stop branch.
    sat._stop_music()
    sat._leds.set_state_unless("listening", "idle")
    t.join(5)
    time.sleep(0.35)
    settle(sat)
    assert len(sat.procs) == 1                  # no respawn after the stop
    assert sat.frames == []                     # no music_ready, no music_failed
    assert sat._leds.log == ["music", "idle"]


def test_a_wake_in_the_backoff_is_not_fought(monkeypatch):
    """The mic thread on a wake word: ring to "listening", then
    `_stop_music`. The retrying request must not respawn mpg123 over the
    capture, and must not paint the ring back to idle under it."""
    sat = _slow(monkeypatch, [("refused",), ("plays",)])
    t = start(sat)
    wait_for(in_backoff(sat, 1))
    sat._leds.set_state("listening")
    assert sat._is_music_playing() is False     # so the greeting is not skipped
    sat._stop_music()
    t.join(5)
    time.sleep(0.35)
    settle(sat)
    assert len(sat.procs) == 1
    assert sat.frames == []
    assert sat._leds.state == "listening"


def test_a_response_in_the_backoff_is_not_fought(monkeypatch):
    """response_start stops music before the TTS takes the output device;
    a retry spawned after that would fight it for the card."""
    sat = _slow(monkeypatch, [("refused",), ("plays",)])
    t = start(sat)
    wait_for(in_backoff(sat, 1))
    sat._stop_music()                           # response_start's first step
    sat._playback_idle.clear()
    sat._leds.set_state("speaking")
    t.join(5)
    time.sleep(0.35)
    assert len(sat.procs) == 1
    assert sat.frames == [] and sat._leds.state == "speaking"


def test_a_newer_music_start_for_another_stream_takes_over(monkeypatch):
    sat = _slow(monkeypatch, [("refused",), ("plays",)])
    t1 = start(sat, URL)
    wait_for(in_backoff(sat, 1))
    t2 = start(sat, OTHER)
    t2.join(5)
    t1.join(5)
    time.sleep(0.35)
    settle(sat)
    assert [p.argv[-1] for p in sat.procs] == [URL, OTHER]
    assert sat.frames == [{"type": "music_ready"}]
    assert sat._music_url == OTHER and sat._is_music_playing()


def test_a_newer_music_start_for_the_same_stream_takes_over(monkeypatch):
    """The auto-resume after a turn re-sends the same URL. The newer
    request spawns (the old process is dead) and the older one steps aside:
    one process, one music_ready."""
    sat = _slow(monkeypatch, [("refused",), ("plays",)])
    t1 = start(sat)
    wait_for(in_backoff(sat, 1))
    t2 = start(sat)
    t2.join(5)
    t1.join(5)
    time.sleep(0.35)
    settle(sat)
    assert len(sat.procs) == 2
    assert sat.frames == [{"type": "music_ready"}]


def test_a_music_start_for_the_stream_already_playing_spawns_nothing(monkeypatch):
    sat = make_sat(monkeypatch, [("plays",)])
    start(sat).join(5)
    start(sat).join(5)
    assert len(sat.procs) == 1
    # Each request gets its music_ready: the server prepared again.
    assert sat.frames == [{"type": "music_ready"}, {"type": "music_ready"}]


# ─── after it played ───────────────────────────────────────────────────────


def test_a_stream_that_played_then_dropped_turns_the_ring_off_and_is_reported(monkeypatch):
    sat = make_sat(monkeypatch, [("plays",)])
    start(sat).join(5)
    assert sat.frames == [{"type": "music_ready"}]
    sat.procs[0].drop(b"[src/httpget.c:...] error: Connection reset by peer\n", rc=1)
    settle(sat)
    assert len(sat.procs) == 1                  # a late drop is not retried
    assert sat._leds.state == "idle"
    assert sat.frames[-1] == {
        "type": "music_failed",
        "stream_url": URL,
        "reason": "Connection reset by peer",
        "attempts": 1,
    }
    assert not sat._is_music_playing()


def test_a_stream_stopped_on_purpose_is_not_reported(monkeypatch):
    sat = make_sat(monkeypatch, [("plays",)])
    start(sat).join(5)
    sat._stop_music()
    settle(sat)
    assert sat.frames == [{"type": "music_ready"}]
    assert sat._leds.log == ["music"]


# ─── the pieces ────────────────────────────────────────────────────────────


def test_what_counts_as_the_stream_not_being_up_yet():
    owner = REFUSED.decode()
    assert client._music_exit_is_retryable(1, owner)
    assert client._music_exit_is_retryable(1, NO_DATA.decode())
    assert not client._music_exit_is_retryable(1, ALSA.decode())
    assert not client._music_exit_is_retryable(0, owner)     # a clean exit
    assert not client._music_exit_is_retryable(1, "")
    assert client._music_exit_reason(1, owner) == "connection failed: Connection refused"
    assert client._music_exit_reason(1, "") == "mpg123 exited with status 1"


def test_the_retry_window_outlasts_the_core_fallback():
    """An older core leaves a closed stream closed until its music_ready
    fallback resumes MPD (MUSIC_PREPARE_FALLBACK_SEC, 5 s after the
    music_start). The satellite has to still be trying then, with a retry
    soon after."""
    core_fallback_sec = 5.0     # domovoi/config.py music_prepare_fallback_sec

    sat = client.Satellite
    assert sat.MUSIC_RETRY_WINDOW_SEC > core_fallback_sec + 1.0
    assert max(sat.MUSIC_RETRY_BACKOFF_SEC) <= 2.0
    assert sat.MUSIC_RETRY_BACKOFF_SEC[0] <= 0.5


def test_the_ring_is_taken_back_only_from_music():
    leds = LEDController(LEDConfig(enabled=False))
    leds.set_state("music")
    leds.set_state_if("music", "idle")
    assert leds._current_state() == "idle"
    leds.set_state("listening")
    leds.set_state_if("music", "idle")
    assert leds._current_state() == "listening"
